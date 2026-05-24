from decimal import Decimal

import pytest

from bot.core.types import OBLevel
from bot.data.spoof_filter import compute_imbalance, filter_spoofs


def lvl(price: float, qty: float) -> OBLevel:
    return OBLevel(price=Decimal(str(price)), qty=Decimal(str(qty)))


class TestFilterSpoofs:
    def test_passes_normal_levels(self):
        levels = [lvl(100, 1.0), lvl(99, 1.1), lvl(98, 0.9), lvl(97, 1.0), lvl(96, 1.2)]
        result = filter_spoofs(levels, size_multiplier=3.0)
        assert len(result) == 5

    def test_removes_obvious_spoof(self):
        # Level at index 2 is 10× its neighbors → should be removed
        levels = [lvl(100, 1.0), lvl(99, 1.0), lvl(98, 30.0), lvl(97, 1.0), lvl(96, 1.0)]
        result = filter_spoofs(levels, size_multiplier=3.0)
        prices = [float(l.price) for l in result]
        assert 98.0 not in prices

    def test_keeps_legitimately_large_level(self):
        # Large but not disproportionate to neighbors
        levels = [lvl(100, 5.0), lvl(99, 5.5), lvl(98, 6.0), lvl(97, 5.2), lvl(96, 5.8)]
        result = filter_spoofs(levels, size_multiplier=3.0)
        assert len(result) == 5

    def test_too_few_levels_returns_unchanged(self):
        levels = [lvl(100, 10.0), lvl(99, 1.0)]
        result = filter_spoofs(levels, size_multiplier=3.0, neighbor_window=2)
        assert result == levels

    def test_empty_input(self):
        assert filter_spoofs([]) == []


class TestComputeImbalance:
    def test_balanced_book(self):
        bids = [lvl(99, 1.0)]
        asks = [lvl(101, 1.0)]
        assert compute_imbalance(bids, asks) == pytest.approx(0.0)

    def test_bid_heavy(self):
        bids = [lvl(99, 3.0)]
        asks = [lvl(101, 1.0)]
        result = compute_imbalance(bids, asks)
        assert result == pytest.approx(0.5)  # (3-1)/(3+1)

    def test_ask_heavy(self):
        bids = [lvl(99, 1.0)]
        asks = [lvl(101, 3.0)]
        result = compute_imbalance(bids, asks)
        assert result == pytest.approx(-0.5)

    def test_empty_both_sides(self):
        assert compute_imbalance([], []) == 0.0

    def test_range_bounds(self):
        bids = [lvl(99, 100.0)]
        asks = []
        result = compute_imbalance(bids, asks)
        assert result == pytest.approx(1.0)

        result2 = compute_imbalance([], [lvl(101, 100.0)])
        assert result2 == pytest.approx(-1.0)
