"""Tests for the martingale backtest engine: cycles, stuck bags, hard stop, log."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from martingale_bot.config import BacktestConfig, MartingaleParams
from martingale_bot.engine import PriceBar, run_backtest

_T0 = datetime(2026, 6, 13, tzinfo=timezone.utc)


def _bars(rows) -> list[PriceBar]:
    """rows: list of (high, low, close) — ts auto-incremented by 5m."""
    out = []
    for i, (hi, lo, cl) in enumerate(rows):
        out.append(PriceBar(_T0 + timedelta(minutes=5 * i),
                            Decimal(str(hi)), Decimal(str(lo)), Decimal(str(cl))))
    return out


def _params(**kw) -> MartingaleParams:
    base = dict(base_order_size=Decimal("100"), safety_order_size=Decimal("100"))
    base.update(kw)
    return MartingaleParams(**base)


def _frictionless(capital="100000") -> BacktestConfig:
    return BacktestConfig(initial_capital=Decimal(capital),
                          taker_fee=Decimal("0"), slippage=Decimal("0"))


class TestCleanTakeProfit:
    def setup_method(self):
        # bar0 base@100; bar1 rallies to TP(101); bar2 opens a flat new cycle
        bars = _bars([(100, 100, 100), (101, 100, 101), (101, 101, 101)])
        self.res = run_backtest(bars, _params(), _frictionless())

    def test_one_tp_cycle(self):
        assert self.res.n_tp_cycles == 1

    def test_tp_cycle_pnl_positive(self):
        tp = [c for c in self.res.cycles if c.exit_reason == "tp"][0]
        assert tp.realized_pnl == Decimal("1")   # 1 base * (101 - 100)
        assert tp.legs_filled == 1

    def test_not_stuck_when_final_cycle_flat(self):
        assert self.res.ended_stuck is False

    def test_total_pnl(self):
        assert self.res.total_pnl == Decimal("1")


class TestSafetyOrderThenRecover:
    def setup_method(self):
        # bar0 base@100; bar1 dips to 99 (SO1 fills) no TP; bar2 rallies to TP
        bars = _bars([(100, 100, 100), (99.5, 99, 99.2), (101, 100, 101)])
        self.res = run_backtest(bars, _params(), _frictionless())

    def test_safety_order_used(self):
        assert self.res.max_safety_orders_used >= 1

    def test_cycle_filled_two_legs(self):
        tp = [c for c in self.res.cycles if c.exit_reason == "tp"][0]
        assert tp.legs_filled == 2

    def test_avg_entry_below_base(self):
        tp = [c for c in self.res.cycles if c.exit_reason == "tp"][0]
        assert tp.avg_entry < Decimal("100")

    def test_tp_pnl_positive(self):
        tp = [c for c in self.res.cycles if c.exit_reason == "tp"][0]
        assert tp.realized_pnl > 0


class TestStuckBag:
    """The martingale fat tail: price falls through the whole ladder, never recovers."""

    def setup_method(self):
        # descend through every safety price (99..95) then sit at 90
        bars = _bars([
            (100, 100, 100),
            (100, 99, 99),
            (99, 98, 98),
            (98, 97, 97),
            (97, 96, 96),
            (96, 95, 95),
            (95, 90, 90),
            (90, 90, 90),
        ])
        self.res = run_backtest(bars, _params(), _frictionless())

    def test_all_five_safety_orders_used(self):
        assert self.res.max_safety_orders_used == 5

    def test_ended_stuck(self):
        assert self.res.ended_stuck is True

    def test_total_pnl_negative(self):
        assert self.res.total_pnl < 0

    def test_drawdown_recorded(self):
        assert self.res.max_drawdown_pct > 0


class TestCapitalExhaustion:
    """Too little capital → cannot fill the deep legs → flagged, not silently skipped."""

    def setup_method(self):
        # capital 500: affords base(100)+SO1(100)+SO2(250)=450, not SO3(625)
        bars = _bars([
            (100, 100, 100),
            (100, 99, 99),
            (99, 98, 98),
            (98, 97, 97),
            (97, 90, 90),
            (90, 90, 90),
        ])
        cfg = BacktestConfig(initial_capital=Decimal("500"),
                             taker_fee=Decimal("0"), slippage=Decimal("0"))
        self.res = run_backtest(bars, _params(), cfg)

    def test_stuck_with_exhausted_reason(self):
        assert self.res.ended_stuck is True
        last = self.res.cycles[-1]
        assert last.exit_reason == "capital_exhausted_stuck"

    def test_did_not_fill_beyond_affordable(self):
        # base + SO1 + SO2 = 3 legs; SO3 unaffordable
        last = self.res.cycles[-1]
        assert last.legs_filled == 3


class TestHardStopOverlay:
    """Winner-asymmetry overlay (trading §8 R5): cut the bag instead of holding it."""

    def setup_method(self):
        bars = _bars([
            (100, 100, 100),   # base @100
            (99, 99, 99),      # SO1 fills @99, last_fill=99, stop=99*0.95=94.05
            (95, 94, 94),      # low 94 <= 94.05 → hard stop
        ])
        params = _params(max_safety_orders=1, hard_stop_pct=Decimal("0.05"))
        self.res = run_backtest(bars, params, _frictionless())

    def test_exit_reason_hard_stop(self):
        reasons = [c.exit_reason for c in self.res.cycles]
        assert "hard_stop" in reasons

    def test_hard_stop_realizes_loss(self):
        hs = [c for c in self.res.cycles if c.exit_reason == "hard_stop"][0]
        assert hs.realized_pnl < 0

    def test_not_stuck_after_hard_stop(self):
        # the stop cut the bag; a fresh flat cycle may reopen but isn't underwater
        assert self.res.ended_stuck is False


class TestJsonLinesLog:
    """audit-log §2.1 schema + bot-ops §2.2: re-parse the log, don't trust the summary."""

    def test_log_parses_and_pnl_reconciles(self, tmp_path):
        bars = _bars([(100, 100, 100), (101, 100, 101), (101, 101, 101)])
        log = tmp_path / "trades.jsonl"
        res = run_backtest(bars, _params(), _frictionless(), log_path=log)

        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert records, "log must not be empty"

        # schema (audit-log §2.1)
        for r in records:
            assert set(r) >= {"ts", "source", "event", "level", "payload"}
            assert r["source"] == "martingale_bot"
            assert set(r["payload"]) >= {"symbol", "side", "qty", "price", "pnl"}

        # direct re-parse: summed cycle_closed pnl must equal the closed-cycle total
        closed_pnl = sum(
            Decimal(r["payload"]["pnl"]) for r in records if r["event"] == "cycle_closed"
        )
        # only the flat reopened cycle is unclosed (mtm 0) → total == closed
        assert closed_pnl == res.total_pnl

    def test_buy_events_are_buys(self, tmp_path):
        bars = _bars([(100, 100, 100), (101, 100, 101), (101, 101, 101)])
        log = tmp_path / "trades.jsonl"
        run_backtest(bars, _params(), _frictionless(), log_path=log)
        records = [json.loads(line) for line in log.read_text().splitlines()]
        fills = [r for r in records if r["event"] == "order_filled"]
        assert fills and all(r["payload"]["side"] == "BUY" for r in fills)


class TestGuards:
    def test_too_few_bars_rejected(self):
        with pytest.raises(ValueError, match="at least 2 bars"):
            run_backtest(_bars([(100, 100, 100)]), _params(), _frictionless())
