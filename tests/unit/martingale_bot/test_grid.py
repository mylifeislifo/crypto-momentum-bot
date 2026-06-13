"""Tests for the pure martingale ladder math."""

from decimal import Decimal

import pytest

from martingale_bot.config import MartingaleParams
from martingale_bot.grid import average_entry, build_grid, tp_price


def _params(**kw) -> MartingaleParams:
    base = dict(base_order_size=Decimal("100"), safety_order_size=Decimal("100"))
    base.update(kw)
    return MartingaleParams(**base)


class TestBuildGridScreenshot:
    """Screenshot defaults at base_price 100 → a clean 1/2/3/4/5% ladder."""

    def setup_method(self):
        self.grid = build_grid(Decimal("100"), _params())

    def test_has_base_plus_five_safety(self):
        assert len(self.grid.legs) == 6
        assert self.grid.legs[0].is_base
        assert all(not leg.is_base for leg in self.grid.safety_legs)

    def test_base_leg(self):
        base = self.grid.legs[0]
        assert base.price == Decimal("100")
        assert base.quote_size == Decimal("100")
        assert base.base_qty == Decimal("1")
        assert base.deviation_pct == Decimal("0")

    def test_safety_prices_are_flat_1pct_ladder(self):
        prices = [leg.price for leg in self.grid.safety_legs]
        assert prices == [Decimal("99"), Decimal("98"), Decimal("97"),
                          Decimal("96"), Decimal("95")]

    def test_safety_deviations_cumulative(self):
        devs = [leg.deviation_pct for leg in self.grid.safety_legs]
        assert devs == [Decimal("0.01"), Decimal("0.02"), Decimal("0.03"),
                        Decimal("0.04"), Decimal("0.05")]

    def test_safety_sizes_scale_by_2_5(self):
        sizes = [leg.quote_size for leg in self.grid.safety_legs]
        assert sizes == [Decimal("100"), Decimal("250"), Decimal("625"),
                         Decimal("1562.5"), Decimal("3906.25")]

    def test_total_quote_matches_max_cycle_cost(self):
        assert self.grid.total_quote == Decimal("6543.75")


class TestBuildGridStepScale:
    """step_scale > 1 → geometric (widening) deviations."""

    def test_geometric_steps(self):
        grid = build_grid(
            Decimal("100"),
            _params(price_drop_step=Decimal("0.01"), step_scale=Decimal("2"),
                    max_safety_orders=3),
        )
        devs = [leg.deviation_pct for leg in grid.safety_legs]
        # steps: 0.01, 0.02, 0.04 → cumulative 0.01, 0.03, 0.07
        assert devs == [Decimal("0.01"), Decimal("0.03"), Decimal("0.07")]


class TestBuildGridGuards:
    def test_zero_safety_orders(self):
        grid = build_grid(Decimal("100"), _params(max_safety_orders=0))
        assert len(grid.legs) == 1
        assert grid.total_quote == Decimal("100")

    def test_non_positive_base_price_rejected(self):
        with pytest.raises(ValueError, match="base_price"):
            build_grid(Decimal("0"), _params())


class TestAverageEntry:
    def test_base_only_equals_base_price(self):
        grid = build_grid(Decimal("100"), _params(max_safety_orders=0))
        assert average_entry(grid.legs) == Decimal("100")

    def test_quote_weighted_not_price_weighted(self):
        # equal quote (100 each) at prices 100 and 50 → qty 1 and 2 → 200/3, not 75
        grid = build_grid(
            Decimal("100"),
            _params(price_drop_step=Decimal("0.5"), max_safety_orders=1,
                    volume_scale=Decimal("1")),
        )
        assert average_entry(grid.legs) == Decimal("200") / Decimal("3")

    def test_avg_sits_below_base_when_averaging_down(self):
        grid = build_grid(Decimal("100"), _params())
        avg = average_entry(grid.legs)
        # most size is in the deepest legs → blended entry well below 100
        assert Decimal("95") < avg < Decimal("100")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            average_entry([])


class TestTpPrice:
    def test_tp_is_one_percent_above_avg(self):
        assert tp_price(Decimal("100"), Decimal("0.01")) == Decimal("101")
