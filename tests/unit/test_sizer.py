from decimal import Decimal

from bot.risk.sizer import initial_stop, position_qty


def test_basic_risk_sizing():
    qty = position_qty(
        equity=Decimal("10000000"),
        risk_per_trade=0.015,
        entry_price=Decimal("100000"),
        atr_value=Decimal("2500"),
        atr_stop_mult=2.0,
        max_concentration=0.25,
        min_notional=Decimal("5000"),
        lot_size=Decimal("0.0001"),
    )
    # risk-based: 150_000 / 5_000 = 30; concentration cap: 2_500_000/100_000 = 25
    assert qty == Decimal("25.0000")


def test_zero_atr_returns_zero():
    qty = position_qty(
        equity=Decimal("10000000"),
        risk_per_trade=0.015,
        entry_price=Decimal("100000"),
        atr_value=Decimal("0"),
        atr_stop_mult=2.0,
        max_concentration=0.25,
        min_notional=Decimal("5000"),
        lot_size=Decimal("0.0001"),
    )
    assert qty == Decimal(0)


def test_min_notional_rejection():
    qty = position_qty(
        equity=Decimal("10000"),  # tiny equity
        risk_per_trade=0.015,
        entry_price=Decimal("100000"),
        atr_value=Decimal("2500"),
        atr_stop_mult=2.0,
        max_concentration=0.25,
        min_notional=Decimal("5000"),
        lot_size=Decimal("0.001"),
    )
    assert qty == Decimal(0)


def test_initial_stop_below_entry():
    s = initial_stop(Decimal("100000"), Decimal("2500"), 2.0)
    assert s == Decimal("95000")
