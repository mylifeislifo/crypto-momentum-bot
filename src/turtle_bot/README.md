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

## 데이터 소스 (M1 2단계)

- 거래소: **비트겟 USDT-M 무기한 선물** (실거래 예정 거래소와 일치). 공개
  klines라 API 키 불필요.
- 엔드포인트: Bitget v2 `GET /api/v2/mix/market/history-candles`
  (`productType=usdt-futures`, `granularity=1D`), `endTime` 기준 역방향
  페이지네이션.
- 코드: `data/fetcher.py`(aiohttp + tenacity 재시도) → Polars DF(OHLCV
  `Decimal` 보존·ts UTC) → `data/cache.py` parquet 캐시(`data/turtle_bot_cache/`).
- ⚠️ **미검증**: 작성 시점 `api.bitget.com`이 네트워크 차단 상태라 라이브
  호출은 한 번도 안 됨. 파싱·페이지네이션은 오프라인(`aioresponses`)으로
  테스트 통과. **정책 허용 후 1회 실호출로 candle 배열 필드 순서·granularity
  표기 검증 필수** (`bot-ops §2.2` 신뢰성 0).

## 마일스톤

| | 범위 | 상태 |
|---|------|------|
| **M1** | 단일 백테스트 (점 추정만, 양수 확인) | 🔧 스켈레톤 |
| M2 | walkforward (T180/V90/S30) + 부트스트랩 CI | 대기 |
| M3 | 시간 분해 (연도별 alpha 감쇄 점검) | 대기 |
| M4 | paper 게이트 (`.env` 시크릿) | 대기 |

> **시리즈 메타 교훈**: 단일 백테스트 양수 ≠ alpha. 전체 평균만 보고 운용 결정
> 금지(연도별 감쇄가 평균에 가려짐). M1 결과 보고 후 M2 stop/continue 결정.

## 엔진 설계 결정 (M1 3단계 — 사용자 승인)

1. **룩어헤드 방지**: 모든 지표(20일·11일 돌파 채널, ATR(20), 200일 SMA)에
   `shift(1)` 적용. 시그널은 **t-1 봉 마감** 기준으로 확정하고, 체결은
   **다음 봉(t) 시가**에서 한다. 같은 봉의 종가·고저로 진입 판단하지 않는다
   (미래 정보 누수 차단).

2. **방향 게이트 (200일 SMA)**:
   - 종가가 200SMA **위** → **롱만** 진입. 롱 청산 = 11일 채널 **하단 이탈**.
   - 종가가 200SMA **아래** → **숏만** 진입. 숏 청산 = 11일 채널 **상단 돌파**.
   - 추세 반대 방향 신규 진입은 차단.

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
