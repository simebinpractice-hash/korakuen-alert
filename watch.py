"""Japanican 료칸 노천탕 객실 빈방 감시 → Discord 알림.

사용법:
  python watch.py                 # config.json 기준으로 확인, 변화가 있으면 알림
  python watch.py --dry-run       # 알림을 보내지 않고 콘솔에만 출력
  python watch.py --test          # 현재 상태를 테스트 알림으로 한 번 보냄
  python watch.py --dry-run --check-in 2026-12-02 --check-out 2026-12-03
                                  # 임의 날짜를 한 번만 조회 (state 저장 안 함)

환경변수 DISCORD_WEBHOOK_URL 에 디스코드 웹후크 주소가 있어야 알림이 갑니다.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
API_URL = "https://www.japanican.com/api/cronos/property/BelowFoldParams/GetSecondaryData"
JST = timezone(timedelta(hours=9))
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class MonitorError(Exception):
    """응답이 예상한 형태가 아님 (차단, 사이트 구조 변경 등)."""


def search_key(s):
    return f"{s['check_in']}_{s['check_out']}_a{s['adults']}_r{s.get('rooms', 1)}"


def booking_link(cfg, s):
    q = {
        "locale": "en-us",
        "currency": "JPY",
        "checkIn": s["check_in"],
        "checkOut": s["check_out"],
        "rooms": s.get("rooms", 1),
        "adults": s["adults"],
        "childs": 0,
    }
    return f"{cfg['page_url']}?{urllib.parse.urlencode(q)}"


def fetch_rooms(cfg, s):
    """해당 날짜에 예약 가능한 객실 목록(masterRooms)을 돌려준다."""
    nights = (
        datetime.fromisoformat(s["check_out"]) - datetime.fromisoformat(s["check_in"])
    ).days
    q = {
        "locale": "en-us",
        "currency": "JPY",
        "checkIn": s["check_in"],
        "checkOut": s["check_out"],
        "rooms": s.get("rooms", 1),
        "adults": s["adults"],
        "childs": 0,
        "los": nights,
        "hotel_id": cfg["hotel_id"],
        "all": "false",
        "isHostPropertiesEnabled": "true",
    }
    req = urllib.request.Request(
        f"{API_URL}?{urllib.parse.urlencode(q)}",
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": cfg["page_url"],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except Exception as e:  # 네트워크 오류, 4xx/5xx
        raise MonitorError(f"요청 실패: {e}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise MonitorError(f"JSON이 아닌 응답 (차단 페이지일 수 있음): {body[:120]!r}") from e

    grid = data.get("roomGridData")
    if not isinstance(grid, dict) or grid.get("isDataReady") is not True:
        raise MonitorError("roomGridData가 없거나 준비되지 않음 (사이트 구조 변경 가능성)")
    if data.get("hotelId") != cfg["hotel_id"]:
        raise MonitorError(f"다른 호텔 응답: hotelId={data.get('hotelId')}")
    rooms = grid.get("masterRooms")
    if not isinstance(rooms, list):
        raise MonitorError("masterRooms 형식이 예상과 다름")
    return rooms


def match_rooms(cfg, rooms):
    keyword = cfg["room_keyword"].lower()
    found = {}
    for m in rooms:
        name = (m.get("name") or "").strip()
        if keyword not in name.lower():
            continue
        urgency = m.get("urgencyMessage") or {}
        found[name] = {
            "price_usd": m.get("cheapestPrice"),
            "urgency": urgency.get("title", "") if urgency.get("isAvailable") else "",
        }
    return found


# ---------------------------------------------------------------- Discord

def send_discord(payload, dry_run):
    if dry_run:
        print("[dry-run] Discord 메시지:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise SystemExit("DISCORD_WEBHOOK_URL 환경변수가 비어 있습니다.")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        # 기본 Python UA는 디스코드(Cloudflare)에서 403으로 막히므로 직접 지정
        headers={"Content-Type": "application/json", "User-Agent": "korakuen-alert/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status >= 300:
            raise SystemExit(f"Discord 전송 실패: HTTP {resp.status}")


def make_payload(cfg, content, embed=None, ping=False):
    text = f"{cfg['mention']} {content}".strip() if ping and cfg.get("mention") else content
    payload = {"content": text, "allowed_mentions": {"parse": ["everyone"] if ping else []}}
    if embed:
        payload["embeds"] = [embed]
    return payload


def room_lines(found, new_names=()):
    lines = []
    for name, info in found.items():
        mark = "🆕 " if name in new_names else ""
        price = f"${info['price_usd']:,.2f}" if isinstance(info["price_usd"], (int, float)) else "가격 정보 없음"
        extra = f" · {info['urgency']}" if info["urgency"] else ""
        lines.append(f"{mark}**{name}**\n　{price} (USD, 최저가){extra}")
    return "\n".join(lines)


def date_label(s):
    return f"{s['check_in']} → {s['check_out']} · 성인 {s['adults']}명 · 객실 {s.get('rooms', 1)}개"


def available_payload(cfg, s, found, new_names):
    embed = {
        "title": f"♨️ {cfg['hotel_name']} — 노천탕 객실 예약 가능!",
        "url": booking_link(cfg, s),
        "description": f"{date_label(s)}\n\n{room_lines(found, new_names)}\n\n[👉 지금 예약 페이지 열기]({booking_link(cfg, s)})",
        "color": 0x2ECC71,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return make_payload(cfg, "노천탕 객실이 나왔어요!", embed, ping=True)


def sold_out_payload(cfg, s):
    embed = {
        "title": f"{cfg['hotel_name']} — 노천탕 객실 다시 매진",
        "description": date_label(s),
        "color": 0x95A5A6,
    }
    return make_payload(cfg, "노천탕 객실이 다시 매진됐어요.", embed)


def error_payload(cfg, s, err, count):
    embed = {
        "title": "⚠️ 빈방 감시가 실패하고 있어요",
        "description": f"{date_label(s)}\n연속 {count}회 실패\n```{str(err)[:500]}```\n사이트 구조가 바뀌었거나 차단됐을 수 있습니다.",
        "color": 0xE67E22,
    }
    return make_payload(cfg, "감시 오류", embed)


def recovered_payload(cfg, s):
    return make_payload(cfg, f"✅ 빈방 감시가 다시 정상 작동합니다. ({date_label(s)})")


def test_payload(cfg, results):
    parts = []
    for s, found, err in results:
        if err:
            status = f"❌ 조회 실패: {err}"
        elif found:
            status = room_lines(found)
        else:
            status = "노천탕 객실 없음 (매진)"
        parts.append(f"**{date_label(s)}**\n{status}\n[예약 페이지]({booking_link(cfg, s)})")
    embed = {
        "title": "🔔 테스트 알림 — 빈방 감시 작동 중",
        "description": "\n\n".join(parts) or "감시할 날짜가 없습니다.",
        "color": 0x3498DB,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return make_payload(cfg, "테스트 알림입니다.", embed)


# ---------------------------------------------------------------- main

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows 콘솔(cp949)에서 이모지 출력 오류 방지
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="알림 대신 콘솔 출력")
    ap.add_argument("--test", action="store_true", help="현재 상태로 테스트 알림 전송")
    ap.add_argument("--check-in")
    ap.add_argument("--check-out")
    args = ap.parse_args()
    test_mode = args.test or os.environ.get("TEST_NOTIFY", "").lower() == "true"

    cfg = load_json(CONFIG_PATH, None)
    if args.check_in and args.check_out:
        adults = cfg["searches"][0]["adults"] if cfg["searches"] else 2
        searches = [{"check_in": args.check_in, "check_out": args.check_out, "adults": adults, "rooms": 1}]
        persist = False
    else:
        today = datetime.now(JST).date().isoformat()
        searches = [s for s in cfg["searches"] if s["check_in"] >= today]  # 지난 날짜는 건너뜀
        persist = True

    state = load_json(STATE_PATH, {})
    results = []

    for s in searches:
        key = search_key(s)
        st = state.setdefault(key, {"available": [], "errors": 0, "error_alerted": False})
        try:
            found = match_rooms(cfg, fetch_rooms(cfg, s))
            err = None
        except MonitorError as e:
            found, err = {}, e
        results.append((s, found, err))
        print(f"[{key}] " + (f"오류: {err}" if err else f"노천탕 객실 {len(found)}개 {list(found)}"))

        if test_mode:
            continue

        if err:
            st["errors"] += 1
            if st["errors"] >= cfg["error_alert_after"] and not st["error_alerted"]:
                send_discord(error_payload(cfg, s, err, st["errors"]), args.dry_run)
                st["error_alerted"] = True
            continue

        if st["error_alerted"]:
            send_discord(recovered_payload(cfg, s), args.dry_run)
        st["errors"], st["error_alerted"] = 0, False

        prev = set(st["available"])
        new_names = [n for n in found if n not in prev]
        if new_names:
            send_discord(available_payload(cfg, s, found, new_names), args.dry_run)
        elif prev and not found and cfg.get("notify_sold_out_again"):
            send_discord(sold_out_payload(cfg, s), args.dry_run)
        st["available"] = sorted(found)

    if test_mode:
        send_discord(test_payload(cfg, results), args.dry_run)
    elif persist and not args.dry_run:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
