# 트레이딩 도메인 규칙 (rules.md)

이 도메인의 모든 작업은 다음 규칙을 **무조건** 따른다. 어떤 전략·요청·테스트도 이 규칙을 우회할 수 없다.

---

## 1. 하드 안전장치 (위반 시 즉시 예외)

### 1.1 레버리지 한계
모든 전략 모듈에서 **최대 레버리지는 2x를 절대 초과 금지**. 우회 코드는 코드 레벨에서 차단:

```python
from decimal import Decimal

MAX_LEVERAGE = Decimal("2.0")
assert leverage <= MAX_LEVERAGE, f"Leverage {leverage} exceeds hard cap {MAX_LEVERAGE}"
```

### 1.2 수치 타입 강제
가격·수량·잔고·수익률 계산 시 `float` 사용 **전면 금지**. `decimal.Decimal`만 허용:

```python
from decimal import Decimal

qty = Decimal("0.001")          # OK
price = Decimal(str(api_price))  # API float을 안전 변환
qty = 0.001                      # 금지 (부동소수점 오차)
```

거래소 API에서 float을 받더라도 즉시 `Decimal(str(value))`로 변환.

### 1.3 paper / testnet 선검증 강제
신규 로직·새 봇·파라미터 변경은 **반드시** 다음 게이트를 통과해야 실거래 진입:

| 단계 | 조건 |
|------|------|
| 1. 백테스트 | Sharpe > 1, MDD < 30% |
| 2. Walkforward | 동일 기간 분할 검증 통과 |
| 3. Paper / Testnet | 최소 7일 실시간 가동 + 손익 일관성 |
| 4. 실거래 진입 | **시드의 10%부터 시작** |

CLI에서 실거래 가동 시 `--i-understand-real-money` 같은 명시적 플래그를 요구.

> 본 게이트는 `skills/progressive-gate.md §2.1·§2.2`(Sandbox→Small→Full 일반 원칙) + §2.4(사용자 명시 승인)의 트레이딩 도메인 구체화이다. 위 수치(paper 7일·시드 10% 등)가 일반 원칙보다 우선한다.

---

## 2. 상태 리포트 표준 포맷

사용자가 **"상태 알려줘"**라고 할 때 다음 필드를 **모두** 포함한다:

| 필드 | 단위 | 비고 |
|------|------|------|
| 봇 식별자 | 문자열 | 예: `v2`, `bot1_alpha`, `bot2_swing` |
| 가동 모드 | `paper` / `live` / `testnet` | **필수** |
| 투자 원금 | KRW 또는 USD | 시작 시점 잔고 |
| 현재 가치 | 동일 통화 | 미실현 손익 포함 |
| 누적 수익률 | % | `(현재가치 - 원금) / 원금 * 100` |
| 월 예상 수익률 | % | 가동 기간 기반 **복리** 환산 |
| 보유 포지션 | 코인 + 수량 + 진입가 | 있을 경우만 |
| 투자 시작일 | YYYY-MM-DD | 검증·복리 계산용 |

**Telegram 메시지에도 동일 포맷 적용**. 누적 수익률과 투자 시작일은 메시지 푸터에 고정 표시.

---

## 3. 파라미터 표준값 (변경 이력 누적)

| 파라미터 | 현재값 | 변경 이력 |
|----------|--------|-----------|
| `EXPOSURE` | 0.30 | 2026.05.04 이전 0.25 → 0.30 상향 |
| `BE_TRIGGER` | 0.01 | 2026.05.04 도입. 1% 수익 도달 시 SL을 브레이크이븐으로 |
| 시간 타이트닝 | 도입됨 | 2026.05.04 도입 |

손절·익절·노출도 변경은 **항상 backtest + walkforward 재검증 후** 반영. 변경 시 위 표에 이력 추가.

---

## 4. 거래소 API 표준

- 모든 외부 호출은 `try-except` **캡슐화 필수**
- 에러 로그는 `logger.error` 사용 (`print` 금지)
  - **Timeout 에러** 발생 시 로그에 시도했던 주문 파라미터(`symbol`, `qty`, `side`) **반드시 포함**
- 재시도는 `tenacity` 라이브러리의 `@retry` 데코레이터 **일관 사용**
  - 지수 백오프 + 최대 재시도 횟수 + 재시도 대상 예외 명시
- API 키는 **반드시 환경변수**에서 로드 (`security/rules.md` 참조)

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(ConnectionError),
)
def place_order(symbol: str, qty: Decimal, side: str):
    try:
        return exchange.create_order(...)
    except Exception as e:
        logger.error(
            "order failed",
            extra={"symbol": symbol, "qty": str(qty), "side": side, "error": str(e)},
        )
        raise
```

---

## 5. 거래 결과 검증 (월 1회 또는 봇 의심 시)

봇 자체 보고를 신뢰하지 않고 다음을 직접 수행:

1. 거래 로그(JSON Lines 권장) 전수 파싱
2. 거래별 손익 합산 → 누적 수익률 재계산
3. 봇의 텔레그램 보고값과 대조
4. **불일치 시 단일 거래 단위로 역추적**

이는 `skills/bot-ops.md`의 "봇 발언 신뢰성 0" 원칙(SSOT: bot-ops §2.2)의 트레이딩 도메인 구현이다. 거래 로그의 JSON Lines 양식·필수 필드·직접 파싱 절차는 `skills/audit-log.md §2.1·§2.3` SSOT를 따른다.

---

## 6. 다중 데이터 소스

가능한 경우 **dual source 검증**을 적용:
- 가격: 바이낸스 + 업비트 등 2개 거래소 비교
- 펀딩비: 거래소 직접 + 분석 서비스 비교
- 이격 발견 시 알림 + 거래 중단

---

## 7. 퀀트 실행 표준 (Quant Execution Standards)

본 섹션은 백테스트·데이터 처리·시그널 생성 시 적용되는 실행 환경·데이터 표준이다. **§1 하드 안전장치는 본 섹션보다 항상 우선**한다.

### 7.1 연산 환경 (Apple Silicon · ARM64)

- **벡터화 우선**: `multiprocessing`/`joblib`의 워커 분기보다 Numpy·Pandas·**Polars 벡터 연산**을 1순위로 작성. 제너레이터 루프는 Polars로 대체 가능하면 대체.
- **메모리 할당 최소화**: 데이터프레임 복사를 줄이기 위해 `inplace=True`를 적극 활용. 32GB RAM 환경에서 인메모리 연산이 끊기지 않도록 설계.
- **Numba · 구형 JIT 회피**: ARM64에서 호환성 이슈가 잦은 C-extension 의존 라이브러리 대신 **Polars**를 우선 사용.
- **워커 수 동적 제한**: M시리즈의 효율·성능 코어 혼합을 고려, 워커 수는 `CPU 코어 × 0.8`로 제한.
- **§1.2 Decimal 호환 (주의)**: Polars `pl.Decimal(precision, scale)` dtype을 **우선 사용**. 단, Polars의 Decimal은 일부 aggregation·join·수치 함수에서 Float64로 강제 cast되는 케이스가 있어 **연산 결과가 항상 Decimal로 유지된다고 가정 금지**. **최종 출력·DB 저장·주문 직전 단계**에서 `Decimal(str(x))`로 명시 재변환·검증 필수. `float` 직접 노출 금지.

```python
import os
import polars as pl

WORKER_RATIO = 0.8
MAX_WORKERS = max(1, int(os.cpu_count() * WORKER_RATIO))

df = df.with_columns(pl.col("price").cast(pl.Decimal(precision=18, scale=8)))
```

### 7.2 매크로 · 교차 자산 데이터

- **Lead-Lag 입력 파라미터화**: 단일 티커 데이터만 보지 말고, BTC·국채 금리·DXY 등 타 자산과의 선행-지연 상관관계 계산이 가능한 입력 파라미터를 전략 클래스 시그니처에 포함.
- **유동성 지표 병합**: SOFR 금리, 연준 Net Liquidity 등 매크로 지표를 전처리 단계에서 데이터프레임에 `merge`하는 함수 구조를 기본 포함.
- **결측치 처리 일관성**: 주식·크립토·매크로 시계열의 휴장일 차이로 발생하는 NaN은 **`ffill()`로 일관 처리**. 다른 방식(`bfill`·보간) 사용 시 사유 주석 필수.

### 7.3 미국 시장 세션 처리

- **타임존 저장·표시 분리**: 모든 Timestamp는 **내부 UTC 저장**, 조건문·로그 출력은 **`pytz.timezone('US/Eastern')` (EST/EDT) 변환** 후 사용.
- **세션 분리 플래그**: 로직 내에 정규장(RTH: 09:30~16:00 EST) ↔ 시간외장(ETH: Pre-market & After-hours) 구분 플래그 필수 구현.
- **오프닝 진입 차단**: 명시적 override 없는 한, **개장 직후 15분(09:30~09:45 EST) 신규 진입 차단** (§1 하드 안전장치에 준함).
- **ETH 기본 차단**: 시간외장(ETH) 진입은 기본 차단. 활성화 시 전략 클래스 인자로 `allow_eth=True` 명시 요구.

```python
import pytz
from datetime import time

EST = pytz.timezone("US/Eastern")
RTH_OPEN = time(9, 30)
RTH_BLOCK_UNTIL = time(9, 45)
RTH_CLOSE = time(16, 0)

def is_blocked_entry(ts_utc) -> bool:
    ts_est = ts_utc.astimezone(EST).time()
    return RTH_OPEN <= ts_est < RTH_BLOCK_UNTIL  # 09:30~09:45 차단
```

---

## 8. 예외 케이스 누적

- **[2026.04]** 8코인 전략보다 5코인 필터링이 안정적 수익률 → 코인 풀은 정기 검증 후 축소 권장
- **[2026.05.04]** EXPOSURE 0.25 → 0.30 + BE_TRIGGER 0.01 도입. 백테스트에서 샤프 비율 개선 확인 후 반영
- **[2026.05]** Grok 봇이 이월잔금 포함값으로 보고하면서 누적 수익률이 부풀려진 사례 → 리포트 포맷에 "투자 시작일" 명시 + 원금 기준 명확히
- **[2026.05.23]** §7 신설 (구 §8) — 구 `skill_apple_silicon_backtest` / `skill_macro_liquidity_indicators` / `skill_nasdaq_session_handling` 3개 흡수 후 삭제. 구 `skill_meta_generator` / `skill_harness_scaffolder`는 `skill-define.md`가 흡수 → 삭제.
- **[2026.05.23 v2]** 메타 리뷰 반영 — (a) 구 §7 (예약) 삭제로 §8→§7, §9→§8 재번호. (b) §7.1 Polars Decimal 호환 단정을 "Float 강제 cast 가능성" 명시로 약화 — 최종 출력·주문 직전 단계에서 `Decimal(str(x))` 재변환 검증 명문화. (c) §5에 bot-ops §2.2가 "신뢰성 0" SSOT임을 명시.
- **[2026.05.23 v3]** §1.3 게이트가 `skills/progressive-gate.md`(신설) SSOT의 트레이딩 도메인 구체화임을 명문화. §5 거래 로그 직접 파싱·JSON Lines 양식이 `skills/audit-log.md`(신설) SSOT를 따름을 인용.
