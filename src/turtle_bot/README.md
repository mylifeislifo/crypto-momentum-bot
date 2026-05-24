# turtle_bot

터틀 트레이딩 돌파 시스템을 영상 룰 그대로 코드화한 백테스트 봇. 기존
`src/bot/`(Confluence 전략)과 **코드 구조 완전 별도**, 같은 레포에 공존.

대상: **BTCUSDT + ETHUSDT 일봉 USDT-M 무기한 선물**, 양방향(롱/숏), 자본 50/50 분배.

## 영상 룰 (파라미터 박힘 — `config.py`)

| 항목 | 값 | 출처 |
|------|-----|------|
| 진입 채널 (HHLL) | 20일 | 영상 |
| 청산 채널 (HHLL) | 11일 | 영상 |
| 손절 | ATR(20) × 2 | 영상 |
| 추세 필터 | 200일 SMA | 영상 |
| 리스크 / 트레이드 | 2% | 영상 |
| 진입 방향 | 200SMA 위 = 롱 중심 / 아래 = 숏 중심 | 영상 |

거래 비용 가정(보수적): taker 0.04% × 2(in/out) + 슬리피지 0.05%.

## doc/ 룰 매핑 (협상 불가)

| doc 룰 | 적용 |
|--------|------|
| `trading §1.2` Decimal 강제 | 가격·수량·잔고·수익률 전부 `Decimal`. `float` 금지. `config.py` `_to_decimal` 변환 |
| `trading §1.1` 레버리지 2x 캡 | `MAX_LEVERAGE = Decimal("2.0")`, `BacktestConfig.leverage` validator 차단. M1 = 1x |
| `trading §1.3` + `progressive-gate §2` paper 게이트 | backtest → walkforward+CI → paper 7일 → 시드 10%. **M1은 backtest 점 추정만** |
| `trading §3` 파라미터 변경 이력 | 영상값 변경 시 trading rules §3 표에 기록 |
| `audit-log §2.1` JSON Lines | 결과·거래 로그 `ts/source/event/level/payload`. payload는 `trading §5` `{symbol,side,qty,price,pnl}` |
| `bot-ops §2.2` 봇 발언 신뢰성 0 | 자기 자신 포함. 결과 직접 파싱으로 검증 |

## 마일스톤

| | 범위 | 상태 |
|---|------|------|
| **M1** | 단일 백테스트 (점 추정만, 양수 확인) | 🔧 스켈레톤 |
| M2 | walkforward (T180/V90/S30) + 부트스트랩 CI | 대기 |
| M3 | 시간 분해 (연도별 alpha 감쇄 점검) | 대기 |
| M4 | paper 게이트 (`.env` 시크릿) | 대기 |

> **시리즈 메타 교훈**: 단일 백테스트 양수 ≠ alpha. 전체 평균만 보고 운용 결정
> 금지(연도별 감쇄가 평균에 가려짐). M1 결과 보고 후 M2 stop/continue 결정.

## 검증 명령 (예시)

```bash
# 설정 로드 확인
python3 -c "from turtle_bot.config import PARAMS_M1, CONFIG_M1; print(PARAMS_M1, CONFIG_M1)"

# 단위 테스트
pytest tests/unit/turtle_bot/ -v

# (M1 결과 산출 후) JSON Lines 거래 로그 직접 파싱 — bot-ops §2.2 신뢰성 0
jq -s 'map(select(.event=="order_filled")) | map(.payload.pnl|tonumber) | add' \
  results/turtle_bot/m1/trades.jsonl
```
