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

이는 `skills/bot-ops.md`의 "봇 발언 신뢰성 0" 원칙의 트레이딩 도메인 구현이다.

---

## 6. 다중 데이터 소스

가능한 경우 **dual source 검증**을 적용:
- 가격: 바이낸스 + 업비트 등 2개 거래소 비교
- 펀딩비: 거래소 직접 + 분석 서비스 비교
- 이격 발견 시 알림 + 거래 중단

---

## 7. 예외 케이스 누적

- **[2026.04]** 8코인 전략보다 5코인 필터링이 안정적 수익률 → 코인 풀은 정기 검증 후 축소 권장
- **[2026.05.04]** EXPOSURE 0.25 → 0.30 + BE_TRIGGER 0.01 도입. 백테스트에서 샤프 비율 개선 확인 후 반영
- **[2026.05]** Grok 봇이 이월잔금 포함값으로 보고하면서 누적 수익률이 부풀려진 사례 → 리포트 포맷에 "투자 시작일" 명시 + 원금 기준 명확히
