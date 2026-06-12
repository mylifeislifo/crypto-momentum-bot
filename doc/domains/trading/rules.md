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
- **[2026.05.24] (발견 — 검증 미완, 실거래 미반영)** "ETH 펀딩비 비정상 고점 → BTC ~12h 후 하락" lead-lag 신호 후보. 보고된 OOS 상관 −0.335(1년). 부호는 경제적으로 일관(과열 레버리지 → 평균회귀). **단 다음 caveat로 검증 미완 처리**: (a) −0.335는 정상 IC(0.02~0.05)보다 과대 → 아티팩트 의심 (b) 12h forward-return 겹침 표본 + 펀딩 고지속성으로 유효 N·유의성 과대평가 가능(Bonferroni는 자기상관 미보정) (c) 60⊂180⊂365 중첩창은 독립 3회가 아닌 사실상 1회 (d) BTC-ETH 95% 상관 하에서 BTC 자체 펀딩 대비 증분 우위 미입증 → 인과 미확정. 모멘텀 봇 진입차단 stop 필터로는 부적합(극단 펀딩 시점 ≠ 모멘텀 진입 시점). 검증 방법론·후속 점검 항목은 `skills/signal-validation.md` §2 적용. §1.3 게이트 통과 전 실거래 반영 금지.
- **[2026.06.11] (검증 사이클 #2 — 알파 후보 4종 전부 기각/미검증, 실거래 미반영)** 청산-구조(exit-structure) 알파 탐색. 후보·결과·재사용 자산을 다음과 같이 기록한다. 발견은 모두 `skills/signal-validation.md §2` 방법론으로 검증했으며, **4종 모두 §1.3 게이트(backtest→walkforward→paper 7일→시드 10%) 미통과 → 실거래 반영 금지**.
  - **C-2** (OI >0.5% 급감 + Taker buy/sell <0.90 → BTC 롱 24h): OOS(2024-11~2025-08) 41건 평균 +0.69%, 블록 부트스트랩 CI[−0.38,+1.57] → **0 포함, 기각**. 결과가 단 2건의 대형 손실(−8.36%, −10.55%)에 좌우 — 두 건 모두 BTC 지속 하락 중 신호 발화(반등 없이 추가 하락). 소표본 × fat-tail 취약(`signal-validation §2.1`·§2.2 유효 N 연계).
  - **C-2.1** (C-2 + 7일 −8% 추세 필터): OOS(2024-06~10) **기각** — 해당 횡보 구간에서 신호 자체가 무력. C-2는 위기-알파(레짐 의존)임이 확인됨.
  - **1491 스타일 청산** (SL −2%, MAX 보유 5일; OKX 트레이더 거래이력 기반): 거래당 +1.20%, Profit Factor 2.91, 연환산 +43%(레버리지 1x). **단 CI[−0.16,+2.29] → 0 포함 + 표본 25건 유의 미달 기각**. IS(약세장)에서 −0.04% → 약세장 취약. point estimate는 매력적이나 통계적 미확정.
  - **청산 구조 분석 (사이클 핵심 발견 — 단 정보적 알파 아닌 행동적 구조)**: 동일 진입 + 고정 24h 청산 시 +0.15% vs 실제 트레이더 청산 규율 적용 시 +1.63% → **알파의 ~90%가 진입이 아니라 청산(exit)에 있음**. 1491은 이익보유 178h vs 손실보유 47h(3.8배 비대칭, winner를 길게), Splendid는 이익 9h vs 손실 30h(역패턴 — 위험 구조). 비밀정보 불필요·규율만 필요 → 기계화 난이도 있으나 가능.
  - **재사용 검증 파이프라인 자산**: Binance S3 공개 OI/Taker 데이터(2020-09~현재, 5분봉, 무료) · OKX 트레이더 거래이력 API(uniqueCode→public-subpositions-history) · 블록 부트스트랩 CI(block=6~8, nb=10000, seed=42).
  - **다음 사이클 후보**: (A) 청산 강도 필터(상위 1~2% 극단 이벤트만) (B) 멀티-심볼 동시 청산(시스템 언와인드 신호) (C) 숏 전략(1491 숏비중 63% — 동일 구조 분석). 방법론 교훈은 `skills/signal-validation.md §3` [2026.06.11] 교차참조.
- **[2026.06.11 조합분석] (알파 조합 가능성 분석 — 정리, 실거래 미반영)** 누적 알파 원자를 조합 관점에서 정리. 원자: 라이브 confluence(L1 롱·L2 숏)·청산기계(L3: SL·BE_TRIGGER·trailing) / 연구(R1 ETH펀딩·R2 C-2·R3 C-2.1·R4 1491청산·R5 청산구조) / 후보(N1 청산강도·N2 멀티심볼·N3 숏).
  - **🔴 핵심 충돌**: R2(C-2: OI **급감**+테이커 **매도**→롱)가 라이브 L1 MICRO 게이트(OI **상승**+매수벽→롱, `src/bot/strategy/confluence.py:99-103`)와 **입력 부호 정반대**. 같은 두 입력(OI 방향·플로우)을 모멘텀 지속(L1) vs 자본항복 반등(R2)으로 반대 해석 → 라이브 진입과 겹치지 않는 **직교 신호**이나, 어느 레짐인지 가르는 분류기 부재. C-2.1(−8% 추세필터)이 이를 시도했으나 위기 케이스까지 잘라내 실패.
  - **직교(곱하면 강화) ✅**: 청산(L3/R4/R5)은 모든 진입과 직교 — R5("알파 90%가 청산")가 참이면 최고 레버리지는 진입 교체가 아니라 **기존 진입 × 청산 최적화**. C-2 진입(R2) × 1491 청산(R4)을 C-2 n=41에서 동시 검증 가능(R4의 n=25보다 큰 표본).
  - **중복(더하면 자기기만) 🔴**: 센티먼트(공포)·음펀딩·테이커매도(R2)·ETH펀딩(R1)은 단일 "레버리지 스트레스 요인"의 collinear 프록시 가능성 → 증분 IC 미검증(`signal-validation §2.5`). 라이브 confidence 1/5 단순평균(`confluence.py:184-197`)도 미검증 조합. 단 펀딩은 양/음 양꼬리 평균회귀로 일관(R1↔L1 모순 아님) → 단일 "펀딩 극단 요인"으로 묶는 게 타당.
  - **레짐 게이트**: N1(청산강도 상위1~2%)+N2(멀티심볼 동시청산)=C-2.1이 놓친 "바닥 항복 vs 가속 폭락" 판별자. 후보 3개가 단일 가설로 수렴.
  - **권장 통합 검증**: "C-2 진입 × 1491 청산(SL−2%/MAX5일, **C-2 손실 보기 전 사전 고정**) × N1+N2 레짐게이트"를 C-2 n=41에서 블록부트스트랩 CI로 1회 검증(R2·R4·R5·N1·N2 동시 타격). 방법론 갭은 `skills/signal-validation.md §3` [2026.06.11 조합분석] 교차참조. **§1.3 게이트 통과 전 실거래 금지.**
- **[2026.06.12] (라이브 L3 청산 구현 — winner-asymmetry, paper 단계 / 신규 알파 아님)** §3 표가 "BE_TRIGGER 0.01 도입"이라 기재했으나 **실제 라이브 코드엔 breakeven이 없었음**(`research/c2_combo.py` 백테스트 하네스에만 존재) → 문서↔코드 드리프트. `risk/trail.py`에 breakeven 바닥(LONG)·천장(SHORT)을 ATR 트레일에 통합 구현해 해소: 진입가 대비 +`breakeven_trigger_pct`(=0.01) 유리하게 가면 스톱이 진입가 아래(LONG)/위(SHORT)로 다시 못 내려감 → winner가 손실로 round-trip 불가 = R5("알파 ~90%가 청산")의 **exit-side 적용**(진입 신호 무변경). config `risk.breakeven_trigger_pct`/`breakeven_offset_pct`(0=비활성), ATR 트레일이 BE 레벨 위로 오르면 트레일 인계. 이는 정보적 알파가 아니라 **행동적 청산 규율**(`signal-validation §3` [2026.06.11] 구분). **현재 `mode: paper`/`dry_run: true` → §1.3 게이트(backtest→walkforward→paper 7일→시드 10%) 전 실거래 영향 없음.** 시간스톱("시간 타이트닝")은 여전히 미구현 — 다음 차례. `tests/unit/test_trail.py` BE 6종 추가(전체 156 통과).
