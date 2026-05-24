"""Tests for ConfluenceStrategy.

Covers all 8 gate combinations (2^3) for both LONG and SHORT:
  macro × micro × cvd ∈ {True, False}^3

Also tests:
- SHORT macro requires BOTH extreme_greed AND high funding (not just one)
- Confidence is in [0, 1]
- Stop prices are directionally correct
- No signal when data is missing
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot.config.schema import AppConfig, RiskCfg, StrategyCfg
from bot.core.enums import Interval, SentimentLabel, Side
from bot.core.types import Bar, OBLevel, OBSnapshot, OIFunding, SentimentReading
from bot.strategy.base import StrategyContext
from bot.strategy.confluence import ConfluenceStrategy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)
_ENTRY = Decimal("50000")


def _cfg() -> tuple[StrategyCfg, RiskCfg]:
    return StrategyCfg(), RiskCfg()


def _engine() -> ConfluenceStrategy:
    s, r = _cfg()
    return ConfluenceStrategy(s, r)


def _ob(imbalance: float) -> OBSnapshot:
    mid = _ENTRY
    bid = OBLevel(price=mid - Decimal("1"), qty=Decimal("1"))
    ask = OBLevel(price=mid + Decimal("1"), qty=Decimal("1"))
    return OBSnapshot(
        ts=_TS,
        bids=(bid,),
        asks=(ask,),
        imbalance_raw=imbalance,
        imbalance=imbalance,
        mid_price=mid,
        spread=Decimal("2"),
    )


def _oi(delta_pct: float, funding: float) -> OIFunding:
    return OIFunding(
        ts=_TS,
        open_interest=Decimal("10000"),
        oi_delta_pct=delta_pct,
        funding_rate=funding,
        next_funding_ts=_TS,
    )


def _sent(label: SentimentLabel, fg: int = 50) -> SentimentReading:
    return SentimentReading(
        ts=_TS,
        fear_greed_index=fg,
        sentiment_label=label,
        long_ratio=0.5,
        short_ratio=0.5,
    )


def _bar(cvd_delta: float, n: int = 1) -> list[Bar]:
    bars = []
    for i in range(n):
        bars.append(Bar(
            ts=_TS,
            interval=Interval.M5,
            open=_ENTRY,
            high=_ENTRY + Decimal("100"),
            low=_ENTRY - Decimal("100"),
            close=_ENTRY,
            volume=Decimal("10"),
            buy_volume=Decimal("6"),
            sell_volume=Decimal("4"),
            cvd_delta=cvd_delta,
            cvd_cumulative=cvd_delta * (i + 1),
            vwap=_ENTRY,
            trade_count=100,
        ))
    return bars


def _ctx(
    imbalance: float,
    oi_delta: float,
    funding: float,
    sentiment: SentimentLabel,
    cvd_delta: float,
    n_bars: int = 3,
) -> StrategyContext:
    ctx = StrategyContext()
    ctx.latest_ob = _ob(imbalance)
    ctx.latest_oi = _oi(oi_delta, funding)
    ctx.latest_sentiment = _sent(sentiment)
    for bar in _bar(cvd_delta, n_bars):
        ctx.ingest_bar(bar)
    return ctx


# ---------------------------------------------------------------------------
# LONG — all 8 gate combinations
# ---------------------------------------------------------------------------

class TestLongGates:
    """For LONG: macro=fear+, micro=OI↑+bid, cvd=positive."""

    MACRO_OK = (SentimentLabel.FEAR, 0.0)           # fear, neutral funding
    MACRO_NO = (SentimentLabel.GREED, 0.0005)       # greed, positive funding
    MICRO_OK = (0.004, 0.40)                         # oi_delta, imbalance
    MICRO_NO = (0.001, 0.10)                         # below both thresholds
    CVD_OK = 5.0                                     # positive CVD
    CVD_NO = -5.0                                    # negative CVD

    def _make(self, macro: bool, micro: bool, cvd: bool) -> StrategyContext:
        sent, funding = self.MACRO_OK if macro else self.MACRO_NO
        oi_delta, imbalance = self.MICRO_OK if micro else self.MICRO_NO
        cvd_val = self.CVD_OK if cvd else self.CVD_NO
        return _ctx(imbalance, oi_delta, funding, sent, cvd_val)

    def test_all_pass(self):
        signal = _engine().evaluate(self._make(True, True, True))
        assert signal is not None
        assert signal.side == Side.LONG

    @pytest.mark.parametrize("macro,micro,cvd", [
        (False, True, True),
        (True, False, True),
        (True, True, False),
        (False, False, True),
        (False, True, False),
        (True, False, False),
        (False, False, False),
    ])
    def test_any_gate_missing_blocks_signal(self, macro, micro, cvd):
        signal = _engine().evaluate(self._make(macro, micro, cvd))
        # Either no signal at all, or a SHORT (never LONG when macro/micro/cvd fail)
        assert signal is None or signal.side != Side.LONG


class TestLongMacroVariants:
    """Macro gate passes with fear OR negative funding (OR condition)."""

    def test_negative_funding_alone_satisfies_macro(self):
        # greed sentiment but very negative funding → macro still passes
        ctx = _ctx(imbalance=0.40, oi_delta=0.004, funding=-0.0005,
                   sentiment=SentimentLabel.GREED, cvd_delta=5.0)
        signal = _engine().evaluate(ctx)
        assert signal is not None
        assert signal.side == Side.LONG

    def test_extreme_fear_alone_satisfies_macro(self):
        ctx = _ctx(imbalance=0.40, oi_delta=0.004, funding=0.0005,
                   sentiment=SentimentLabel.EXTREME_FEAR, cvd_delta=5.0)
        signal = _engine().evaluate(ctx)
        assert signal is not None
        assert signal.side == Side.LONG

    def test_neutral_sentiment_neutral_funding_blocks_macro(self):
        ctx = _ctx(imbalance=0.40, oi_delta=0.004, funding=0.00005,
                   sentiment=SentimentLabel.NEUTRAL, cvd_delta=5.0)
        signal = _engine().evaluate(ctx)
        # macro fails → no LONG
        assert signal is None or signal.side != Side.LONG


# ---------------------------------------------------------------------------
# SHORT — all 8 gate combinations
# ---------------------------------------------------------------------------

class TestShortGates:
    """For SHORT: macro=extreme_greed+high_funding, micro=OI↓+ask, cvd=negative."""

    MACRO_OK = (SentimentLabel.EXTREME_GREED, 0.0002)  # extreme greed + high funding
    MACRO_NO = (SentimentLabel.GREED, 0.00005)          # greed but funding too low
    MICRO_OK = (-0.004, -0.40)                           # oi_delta, imbalance
    MICRO_NO = (-0.001, -0.10)                           # above both thresholds (insufficient)
    CVD_OK = -5.0                                        # negative CVD
    CVD_NO = 5.0                                         # positive CVD

    def _make(self, macro: bool, micro: bool, cvd: bool) -> StrategyContext:
        sent, funding = self.MACRO_OK if macro else self.MACRO_NO
        oi_delta, imbalance = self.MICRO_OK if micro else self.MICRO_NO
        cvd_val = self.CVD_OK if cvd else self.CVD_NO
        return _ctx(imbalance, oi_delta, funding, sent, cvd_val)

    def test_all_pass(self):
        signal = _engine().evaluate(self._make(True, True, True))
        assert signal is not None
        assert signal.side == Side.SHORT

    @pytest.mark.parametrize("macro,micro,cvd", [
        (False, True, True),
        (True, False, True),
        (True, True, False),
        (False, False, True),
        (False, True, False),
        (True, False, False),
        (False, False, False),
    ])
    def test_any_gate_missing_blocks_short(self, macro, micro, cvd):
        signal = _engine().evaluate(self._make(macro, micro, cvd))
        assert signal is None or signal.side != Side.SHORT


class TestShortMacroStrictness:
    """SHORT macro requires BOTH extreme_greed AND high funding (AND condition)."""

    def test_extreme_greed_alone_is_not_enough(self):
        # extreme greed but funding below threshold
        ctx = _ctx(imbalance=-0.40, oi_delta=-0.004, funding=0.00005,
                   sentiment=SentimentLabel.EXTREME_GREED, cvd_delta=-5.0)
        signal = _engine().evaluate(ctx)
        assert signal is None or signal.side != Side.SHORT

    def test_high_funding_alone_is_not_enough(self):
        # high funding but not extreme greed
        ctx = _ctx(imbalance=-0.40, oi_delta=-0.004, funding=0.0002,
                   sentiment=SentimentLabel.GREED, cvd_delta=-5.0)
        signal = _engine().evaluate(ctx)
        assert signal is None or signal.side != Side.SHORT


# ---------------------------------------------------------------------------
# Stop price direction
# ---------------------------------------------------------------------------

class TestStopPrices:
    def test_long_stop_below_entry(self):
        ctx = _ctx(imbalance=0.40, oi_delta=0.004, funding=-0.0002,
                   sentiment=SentimentLabel.EXTREME_FEAR, cvd_delta=5.0)
        signal = _engine().evaluate(ctx)
        assert signal is not None and signal.side == Side.LONG
        assert signal.stop_price < signal.entry_price_est

    def test_short_stop_above_entry(self):
        ctx = _ctx(imbalance=-0.40, oi_delta=-0.004, funding=0.0002,
                   sentiment=SentimentLabel.EXTREME_GREED, cvd_delta=-5.0)
        signal = _engine().evaluate(ctx)
        assert signal is not None and signal.side == Side.SHORT
        assert signal.stop_price > signal.entry_price_est

    def test_long_stop_distance_matches_config(self):
        s_cfg, r_cfg = _cfg()
        engine = ConfluenceStrategy(s_cfg, r_cfg)
        ctx = _ctx(imbalance=0.40, oi_delta=0.004, funding=-0.0002,
                   sentiment=SentimentLabel.EXTREME_FEAR, cvd_delta=5.0)
        signal = engine.evaluate(ctx)
        assert signal is not None
        expected_stop = signal.entry_price_est * (Decimal("1") + Decimal(str(r_cfg.long_sl_pct)))
        assert abs(signal.stop_price - expected_stop) < Decimal("0.1")

    def test_short_stop_distance_matches_config(self):
        s_cfg, r_cfg = _cfg()
        engine = ConfluenceStrategy(s_cfg, r_cfg)
        ctx = _ctx(imbalance=-0.40, oi_delta=-0.004, funding=0.0002,
                   sentiment=SentimentLabel.EXTREME_GREED, cvd_delta=-5.0)
        signal = engine.evaluate(ctx)
        assert signal is not None
        expected_stop = signal.entry_price_est * (Decimal("1") + Decimal(str(abs(r_cfg.short_sl_pct))))
        assert abs(signal.stop_price - expected_stop) < Decimal("0.1")


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_confidence_in_range(self):
        ctx = _ctx(imbalance=0.40, oi_delta=0.004, funding=-0.0002,
                   sentiment=SentimentLabel.EXTREME_FEAR, cvd_delta=5.0)
        signal = _engine().evaluate(ctx)
        assert signal is not None
        assert 0.0 <= signal.confidence <= 1.0

    def test_stronger_signal_higher_confidence(self):
        ctx_weak = _ctx(imbalance=0.31, oi_delta=0.0031, funding=-0.00011,
                        sentiment=SentimentLabel.FEAR, cvd_delta=0.1)
        ctx_strong = _ctx(imbalance=0.80, oi_delta=0.020, funding=-0.0010,
                          sentiment=SentimentLabel.EXTREME_FEAR, cvd_delta=50.0)
        sig_weak = _engine().evaluate(ctx_weak)
        sig_strong = _engine().evaluate(ctx_strong)
        assert sig_weak is not None and sig_strong is not None
        assert sig_strong.confidence > sig_weak.confidence


# ---------------------------------------------------------------------------
# Missing data guard
# ---------------------------------------------------------------------------

class TestMissingData:
    def test_no_signal_without_ob(self):
        ctx = StrategyContext()
        ctx.latest_oi = _oi(0.004, -0.0002)
        ctx.latest_sentiment = _sent(SentimentLabel.EXTREME_FEAR)
        for bar in _bar(5.0, 3):
            ctx.ingest_bar(bar)
        assert _engine().evaluate(ctx) is None

    def test_no_signal_without_oi(self):
        ctx = StrategyContext()
        ctx.latest_ob = _ob(0.40)
        ctx.latest_sentiment = _sent(SentimentLabel.EXTREME_FEAR)
        for bar in _bar(5.0, 3):
            ctx.ingest_bar(bar)
        assert _engine().evaluate(ctx) is None

    def test_no_signal_without_bars(self):
        ctx = StrategyContext()
        ctx.latest_ob = _ob(0.40)
        ctx.latest_oi = _oi(0.004, -0.0002)
        ctx.latest_sentiment = _sent(SentimentLabel.EXTREME_FEAR)
        # only 2 bars, cvd_lookback_bars=3
        for bar in _bar(5.0, 2):
            ctx.ingest_bar(bar)
        assert _engine().evaluate(ctx) is None

    def test_no_signal_without_sentiment(self):
        ctx = StrategyContext()
        ctx.latest_ob = _ob(0.40)
        ctx.latest_oi = _oi(0.004, -0.0002)
        for bar in _bar(5.0, 3):
            ctx.ingest_bar(bar)
        assert _engine().evaluate(ctx) is None
