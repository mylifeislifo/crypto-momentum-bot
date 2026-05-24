"""Confluence signal generator.

Three independent gate layers must ALL pass simultaneously for a signal:

  MACRO GATE  — sentiment extreme + funding rate bias
  MICRO GATE  — OI delta + orderbook imbalance (large directional fill)
  CVD GATE    — cumulative volume delta trend (last N bars)

LONG and SHORT have separate (asymmetric) gate thresholds.
This module only evaluates technical conditions; position-bias
enforcement (80/20 long rule, max 1 short/day) lives in risk/guard.py.
"""

import logging
from decimal import Decimal
from typing import Optional

from ..config.schema import RiskCfg, StrategyCfg
from ..core.clock import utc_now
from ..core.enums import SentimentLabel, Side
from ..core.types import Signal
from .base import StrategyContext

logger = logging.getLogger(__name__)

_LONG_SENTIMENT = {SentimentLabel.EXTREME_FEAR, SentimentLabel.FEAR}


class ConfluenceStrategy:
    def __init__(self, strategy_cfg: StrategyCfg, risk_cfg: RiskCfg) -> None:
        self._s = strategy_cfg
        self._r = risk_cfg

    def evaluate(self, ctx: StrategyContext) -> Optional[Signal]:
        """Evaluate all gates. Returns Signal if LONG or SHORT confluence fires,
        None otherwise. Tries LONG first (consistent with long-bias doctrine)."""
        ob = ctx.latest_ob
        oi = ctx.latest_oi
        sent = ctx.latest_sentiment
        bars = ctx.last_n_bars(self._s.cvd_lookback_bars)

        if ob is None or oi is None or sent is None:
            return None
        if len(bars) < self._s.cvd_lookback_bars:
            return None

        cvd_sum = sum(b.cvd_delta for b in bars)

        long_sig = self._eval_long(ob.imbalance, oi.oi_delta_pct, oi.funding_rate,
                                   sent.sentiment_label, cvd_sum, ob.mid_price)
        if long_sig:
            logger.info(
                "confluence.signal",
                side="LONG",
                imbalance=round(ob.imbalance, 4),
                oi_delta=round(oi.oi_delta_pct * 100, 4),
                funding=oi.funding_rate,
                fear_greed=sent.fear_greed_index,
                cvd_sum=round(cvd_sum, 4),
                confidence=round(long_sig.confidence, 3),
            )
            return long_sig

        short_sig = self._eval_short(ob.imbalance, oi.oi_delta_pct, oi.funding_rate,
                                     sent.sentiment_label, cvd_sum, ob.mid_price)
        if short_sig:
            logger.info(
                "confluence.signal",
                side="SHORT",
                imbalance=round(ob.imbalance, 4),
                oi_delta=round(oi.oi_delta_pct * 100, 4),
                funding=oi.funding_rate,
                fear_greed=sent.fear_greed_index,
                cvd_sum=round(cvd_sum, 4),
                confidence=round(short_sig.confidence, 3),
            )
            return short_sig

        return None

    # ------------------------------------------------------------------
    # LONG gate
    # ------------------------------------------------------------------

    def _eval_long(
        self,
        imbalance: float,
        oi_delta_pct: float,
        funding_rate: float,
        sentiment: SentimentLabel,
        cvd_sum: float,
        mid_price: Decimal,
    ) -> Optional[Signal]:
        # MACRO: fear zone OR negative funding (market paying shorts = bullish)
        macro = (
            sentiment in _LONG_SENTIMENT
            or funding_rate < self._s.funding_long_bias_threshold
        )
        # MICRO: OI rising + bid wall (genuine buy pressure, not spoof-filtered)
        micro = (
            oi_delta_pct >= self._s.oi_delta_long_threshold_pct
            and imbalance >= self._s.ob_imbalance_long_threshold
        )
        # CVD: net buy flow over last N bars
        cvd = cvd_sum > 0

        if not (macro and micro and cvd):
            return None

        entry = mid_price
        stop = entry * (Decimal("1") + Decimal(str(self._r.long_sl_pct)))
        confidence = self._confidence_long(sentiment, funding_rate, oi_delta_pct, imbalance, cvd_sum)

        return Signal(
            ts=utc_now(),
            side=Side.LONG,
            entry_price_est=entry,
            stop_price=stop.quantize(Decimal("0.01")),
            confidence=confidence,
            macro_gate=macro,
            micro_gate=micro,
            cvd_gate=cvd,
            fear_greed=0,       # filled below
            funding_rate=funding_rate,
            oi_delta_pct=oi_delta_pct,
            imbalance=imbalance,
            cvd_delta_sum=cvd_sum,
        )

    # ------------------------------------------------------------------
    # SHORT gate  (deliberately stricter than LONG)
    # ------------------------------------------------------------------

    def _eval_short(
        self,
        imbalance: float,
        oi_delta_pct: float,
        funding_rate: float,
        sentiment: SentimentLabel,
        cvd_sum: float,
        mid_price: Decimal,
    ) -> Optional[Signal]:
        # MACRO: BOTH extreme greed AND high positive funding (longs overheated)
        macro = (
            sentiment == SentimentLabel.EXTREME_GREED
            and funding_rate > self._s.funding_short_trigger
        )
        # MICRO: OI declining + ask wall (shorts opening / longs liquidating)
        micro = (
            oi_delta_pct <= self._s.oi_delta_short_threshold_pct
            and imbalance <= self._s.ob_imbalance_short_threshold
        )
        # CVD: net sell flow over last N bars
        cvd = cvd_sum < 0

        if not (macro and micro and cvd):
            return None

        entry = mid_price
        # stop is ABOVE entry for shorts
        stop = entry * (Decimal("1") + Decimal(str(abs(self._r.short_sl_pct))))
        confidence = self._confidence_short(funding_rate, oi_delta_pct, imbalance, cvd_sum)

        return Signal(
            ts=utc_now(),
            side=Side.SHORT,
            entry_price_est=entry,
            stop_price=stop.quantize(Decimal("0.01")),
            confidence=confidence,
            macro_gate=macro,
            micro_gate=micro,
            cvd_gate=cvd,
            fear_greed=0,
            funding_rate=funding_rate,
            oi_delta_pct=oi_delta_pct,
            imbalance=imbalance,
            cvd_delta_sum=cvd_sum,
        )

    # ------------------------------------------------------------------
    # Confidence helpers  (0.0–1.0; how far each metric exceeds its threshold)
    # ------------------------------------------------------------------

    def _confidence_long(
        self,
        sentiment: SentimentLabel,
        funding_rate: float,
        oi_delta_pct: float,
        imbalance: float,
        cvd_sum: float,
    ) -> float:
        sentiment_score = self._sentiment_score_long(sentiment)
        funding_score = min(1.0, max(0.0, -funding_rate / (abs(self._s.funding_long_bias_threshold) + 1e-9)))
        oi_score = min(1.0, max(0.0, oi_delta_pct / (self._s.oi_delta_long_threshold_pct + 1e-9) - 1.0))
        ob_score = min(1.0, max(0.0, imbalance / (self._s.ob_imbalance_long_threshold + 1e-9) - 1.0))
        cvd_score = min(1.0, max(0.0, abs(cvd_sum) / 10.0))  # normalised to ~10 BTC CVD
        return round((sentiment_score + funding_score + oi_score + ob_score + cvd_score) / 5.0, 4)

    def _confidence_short(
        self,
        funding_rate: float,
        oi_delta_pct: float,
        imbalance: float,
        cvd_sum: float,
    ) -> float:
        funding_score = min(1.0, max(0.0, funding_rate / (self._s.funding_short_trigger + 1e-9) - 1.0))
        oi_score = min(1.0, max(0.0, abs(oi_delta_pct) / (abs(self._s.oi_delta_short_threshold_pct) + 1e-9) - 1.0))
        ob_score = min(1.0, max(0.0, abs(imbalance) / (abs(self._s.ob_imbalance_short_threshold) + 1e-9) - 1.0))
        cvd_score = min(1.0, max(0.0, abs(cvd_sum) / 10.0))
        return round((funding_score + oi_score + ob_score + cvd_score) / 4.0, 4)

    @staticmethod
    def _sentiment_score_long(sentiment: SentimentLabel) -> float:
        scores = {
            SentimentLabel.EXTREME_FEAR: 1.0,
            SentimentLabel.FEAR: 0.6,
            SentimentLabel.NEUTRAL: 0.0,
            SentimentLabel.GREED: 0.0,
            SentimentLabel.EXTREME_GREED: 0.0,
        }
        return scores[sentiment]
