# for_claude — Crypto momentum/trend trading bot

수학적/기계적 거래로 단기 고수익을 노리는 크립토 트레이딩 봇.
1단계는 **Upbit KRW 현물**(거래량 상위 10개, 5분봉 모멘텀 + 추세추종),
2단계는 **Binance Futures**로 확장 가능한 구조입니다.

## 빠른 시작

```bash
pip install -e .[dev]                          # 패키지 + dev 도구 설치
cp .env.example .env                           # API 키는 실거래 단계에서만 채움
python -m bot.cli --help

# 1) 데이터 수집 (KRW 상위 10개, 180일)
python -m bot.cli fetch --days 180

# 2) 백테스트
python -m bot.cli backtest

# 3) 페이퍼 트레이딩 (라이브 호가 + 가상 체결)
python -m bot.cli paper

# 4) 실거래 (DRY RUN). 실주문은 config/live.yaml의 dry_run: false + 플래그
python -m bot.cli live --i-understand-real-money
```

## 운영 단계 게이트

- **백테스트 → 페이퍼**: 2년 백테스트에서 Sharpe > 1, MDD < 30%
- **페이퍼 → 실거래**: 30일 / 20+ trades, Sharpe ≥ 1.0, MDD ≤ 25%, PF ≥ 1.3,
  무사고 7일 연속, 백테스트 vs 페이퍼 PnL 차이 < 30%
- **실거래 증액**: 시드 10%로 4주 안정 → 단계적 100%

## 구조

```
src/bot/
  core/        # types, enums, clock, logging
  config/      # pydantic schema + YAML loader
  gateway/     # ExchangeGateway ABC + backtest/paper/upbit/binance_futures
  data/        # fetcher (200-limit pagination), cache (parquet), universe, indicators
  strategy/    # MomentumTrendStrategy, RegimeFilter (BTC 1h)
  risk/        # ATR sizer, chandelier trail/initial/time stops, daily/weekly guard, kill switch
  portfolio/   # positions/cash SoT, allocator (signal -> order, concentration rules)
  execution/   # OrderRouter (retry/backoff), slippage models
  backtest/    # event-driven runner, metrics, walk-forward
  live/        # 5min scheduler + paper/live runner
  cli.py       # typer entrypoint
```

거래소 종속 코드는 `gateway/`에만 모여 있어 Binance Futures 추가는 `gateway/binance_futures.py`만 구현하면 됩니다.

## 안전장치

- **dry_run** 플래그: 실거래 모드라도 주문이 거래소로 가지 않음
- **일일/주간 손실 한도**: 한도 초과 시 그날(혹은 그주) 신규 진입 차단
- **MDD 킬스위치**: peak 대비 −25% 도달 시 전 포지션 청산 + 봇 정지(수동 재시작)
- **잔고 reconciliation**: 실주문 직전 거래소 잔고 재조회

## 테스트

```bash
pytest -q
```
