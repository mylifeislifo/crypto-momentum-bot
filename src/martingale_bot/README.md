# martingale_bot

비트겟(Bitget) **Spot Martingale(DCA) 봇**을 스크린샷 파라미터 그대로 코드화한 백테스트
봇. 기존 `src/bot/`(Confluence 모멘텀)·`src/turtle_bot/`(터틀)과 **코드 구조 완전
별도**, 같은 레포에 공존.

대상: **BGB/USDT 현물(spot)**, 롱 전용, 레버리지 1x. 한 사이클 = 기준주문(즉시) →
하락 시 안전주문 누적매수 → 평단 +1% 도달 시 전량 익절 → 다음 사이클.

## 스크린샷 파라미터 (박힘 — `config.py` `PARAMS_AGGRESSIVE`)

| 항목 | 값 | 의미 |
|------|-----|------|
| Price drop steps | 1% | 안전주문 간 가격 하락 간격 |
| Single-cycle TP target | 1% | 평단 대비 익절 목표 |
| Max safety orders | 5 | 안전주문 최대 개수 |
| Starting condition | Immediate trigger | 기준주문 즉시 진입 |
| Safety order params | 2.50x \| 1.00x | **volume scale**(수량 배수) \| **step scale**(간격 배수) |

→ 가격 사다리: −1%, −2%, −3%, −4%, −5% (step scale 1.0 = 등간격).
→ 수량 사다리: 1x, 2.5x, 6.25x, 15.625x, 39.0625x (volume scale 2.5).
→ **풀 래더 자본 요구량 ≈ base의 65배** (`max_cycle_cost()`로 산출. base 100 → 6,543.75 USDT).

## ⚠️ 노하우 충돌 — 반드시 읽을 것

이 레포가 검증 사이클로 찾아낸 핵심 노하우는 **winner-asymmetry**다
(`doc/domains/trading/rules.md §8` R5 / `doc/skills/signal-validation.md §3` [2026.06.11]):

> **"알파의 ~90%는 진입이 아니라 청산에 있다. 손실은 짧게 끊고(cut losers short),
> 이익은 길게 가져간다(let winners run)."**

**마티게일은 이 원칙의 정반대다.** 손실 포지션에 **물타기로 더 키우고**(let losers
run), 이익은 **+1%에서 잘라버린다**(cut winners short). 라이브 봇(`src/bot/`)의 L3
청산(breakeven·시간스톱·트레일)과 **철학이 충돌**한다. 즉:

- **수익 곡선**: 잦은 소액 익절(+1%) → 겉보기 승률·APY 높음(스크린샷도 +0.85% APY).
- **위험 구조**: 드물지만 치명적인 **fat-tail 1건**이 누적 수익을 통째로 날림. 이는
  C-2 검증(`trading §8` [2026.06.11])에서 "결과가 단 2건의 −8%대 손실에 좌우"로 이미
  관측된 패턴이다. 마티게일은 그 fat-tail을 **구조적으로 내장**한다.
- 스크린샷 "Max drawdown 1.01%"는 **사다리가 끝까지 안 깨진 기간만** 본 수치다.
  −5% 아래로 추세 하락하면 사다리 소진 후 **물린 가방(stuck bag)**이 된다 →
  `engine.py`의 `ended_stuck` 플래그·`capital_exhausted_stuck` 사유로 정량 노출.

그래서 본 봇은 **충실 복제 + 우리 하드룰 + 선택적 winner-asymmetry 오버레이**로 만든다:

- `hard_stop_pct` (기본 0 = 비활성, 바닐라 비트겟과 동일). >0이면 마지막 체결 레그
  대비 그 비율만큼 더 떨어지면 **사이클 전체를 손절** → 마티게일에 "손실 짧게"를 강제.
  실운용 시 활성화 **권장**(레포 노하우 적용 지점).

## doc/ 룰 매핑 (협상 불가)

| doc 룰 | 적용 |
|--------|------|
| `trading §1.2` Decimal 강제 | 가격·수량·잔고·수익률 전부 `Decimal`. `float` 금지. `config.py` `_to_decimal` |
| `trading §1.1` 레버리지 2x 캡 | `MAX_LEVERAGE=Decimal("2.0")`, `BacktestConfig.leverage` validator. 현물 = 1x |
| `trading §1.3` + `progressive-gate §2` paper 게이트 | backtest → walkforward → paper 7일 → 시드 10%. **현재 M1 = 백테스트 점추정만** |
| `trading §3` 파라미터 변경 이력 | 스크린샷값 변경 시 trading §3 표에 기록 |
| `trading §5` + `audit-log §2.1` JSON Lines | 거래 로그 `ts/source/event/level/payload`, payload `{symbol,side,qty,price,pnl}` |
| `signal-validation §2.1·§2.2` fat-tail | 단일 백테스트 양수 ≠ alpha. 점추정 금지, 블록부트스트랩 CI로 판정(M2) |
| `bot-ops §2.2` 봇 발언 신뢰성 0 | 자기 자신 포함. `engine`이 뱉은 요약 불신 → JSON Lines 직접 재파싱으로 검증 |

## 마일스톤

| | 범위 | 상태 |
|---|------|------|
| **M1** | 단일 백테스트(점추정) + 사이클/물림/하드스톱/로그 | ✅ 구현 (`config·grid·engine` + 테스트 62종) |
| M2 | walkforward + 블록부트스트랩 CI + **위기 레짐 분할**(fat-tail 노출) | 대기 |
| M3 | paper 게이트(`.env` 시크릿, 비트겟 현물 API read-only 먼저) | 대기 |
| M4 | 실거래 — **시드 10% + 사용자 명시 승인**(`progressive-gate §2.4`) | 대기 |

> **메타 교훈**(`signal-validation §2.1`): 마티게일의 단일 백테스트 +수익은 거의 항상
> 양수로 나온다(자주 이기니까). 운용 결정은 **fat-tail 손실 1건의 크기·빈도**로 해야지
> 평균·APY로 하면 안 된다. M1 결과 보고 후 M2 stop/continue 결정.

## 검증 명령

```bash
# 설정 로드 + 자본 요구량 확인
python3 -c "from martingale_bot import PARAMS_AGGRESSIVE, max_cycle_cost; print('풀래더 자본:', max_cycle_cost(PARAMS_AGGRESSIVE))"

# 단위 테스트
pytest tests/unit/martingale_bot/ -v

# 합성 시계열 백테스트 (스크립트)
python3 scripts/backtest_martingale.py

# JSON Lines 거래 로그 직접 파싱 — bot-ops §2.2 신뢰성 0 (요약 불신)
jq -s 'map(select(.event=="cycle_closed")) | map(.payload.pnl|tonumber) | add' \
  results/martingale_bot/m1/trades.jsonl
```
