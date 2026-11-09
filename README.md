# korakuen-alert

Japanican의 **Otaru Asarigawa Onsen Korakuen**에서 노천탕 객실(이름에 "Open-air Bath"가 들어간 객실)이
예약 가능해지면 디스코드로 알림을 보냅니다. GitHub Actions가 10분마다 확인합니다.

## 알림이 오는 경우
- 노천탕 객실이 새로 나타났을 때 (`@everyone` 멘션 + 예약 링크)
- 나왔던 객실이 다시 매진됐을 때 (멘션 없음)
- 3회 연속 조회에 실패했을 때 (사이트 구조 변경이나 차단 의심) / 다시 정상으로 돌아왔을 때

## 날짜 바꾸기·추가하기
`config.json`의 `searches`를 수정해서 커밋하면 됩니다. 여러 날짜를 넣을 수 있고, 지난 날짜는 자동으로 건너뜁니다.

```json
"searches": [
  { "check_in": "2026-12-26", "check_out": "2026-12-27", "adults": 2, "rooms": 1 },
  { "check_in": "2026-12-27", "check_out": "2026-12-28", "adults": 2, "rooms": 1 }
]
```

## 테스트·수동 실행
- GitHub → Actions → korakuen-watch → **Run workflow** → "테스트 알림" 체크 → 현재 상태가 디스코드로 옵니다.
- 로컬: `python watch.py --dry-run` (알림 없이 출력), `python watch.py --dry-run --check-in 2026-12-02 --check-out 2026-12-03`

## 끄기
Actions → korakuen-watch → `...` → **Disable workflow**

## 참고
- Japanican(Agoda 기반)의 비공식 내부 API를 씁니다. 사이트가 바뀌면 동작하지 않을 수 있고, 그때는 오류 알림이 옵니다.
- 가격은 API가 USD로만 돌려줘서 USD로 표시됩니다.
- `state.json`은 직전에 확인한 상태를 저장하는 파일입니다 (중복 알림 방지용, 자동 커밋됨).
