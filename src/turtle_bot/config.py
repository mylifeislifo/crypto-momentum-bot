"""Turtle backtest configuration: video parameters baked in, doc/ rules enforced.

All price/quantity/balance/return values use ``decimal.Decimal`` only — never
``float`` (trading rules §1.2). Leverage is hard-capped at 2x (§1.1). Parameter
changes must be logged in trading rules §3.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_LEVERAGE: Decimal = Decimal("2.0")  # trading §1.1 hard cap — never exceed


def _to_decimal(v: object) -> Decimal:
    """Convert int/str/float to Decimal via str() to avoid float artifacts (§1.2).

    Raises ValueError (not TypeError) so pydantic wraps it into a ValidationError.
    """
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):  # bool is a subclass of int; reject explicitly
        raise ValueError("bool is not a valid Decimal value")
    if isinstance(v, (int, str, float)):
        return Decimal(str(v))
    raise ValueError(f"Decimal-convertible value required, got {type(v).__name__}")


class TurtleParams(BaseModel):
    """Strategy parameters — defaults are the reference video values verbatim."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    entry_window: int = Field(default=20, ge=5, le=200)  # video: HHLL entry breakout
    exit_window: int = Field(default=11, ge=2, le=200)  # video: HHLL exit breakout
    atr_window: int = Field(default=20, ge=5, le=200)
    atr_stop_multiplier: Decimal = Field(default=Decimal("2.0"))  # video: ATR x2 stop
    trend_sma_window: int = Field(default=200, ge=50, le=500)  # video: 200-day filter
    risk_per_trade: Decimal = Field(default=Decimal("0.02"))  # video: 2% risk rule

    @field_validator("atr_stop_multiplier", "risk_per_trade", mode="before")
    @classmethod
    def _ensure_decimal(cls, v: object) -> Decimal:
        return _to_decimal(v)


class BacktestConfig(BaseModel):
    """Backtest run configuration: capital split, costs, data/results paths."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    capital_allocation: Decimal = Field(default=Decimal("0.5"))  # 50/50 split
    initial_capital: Decimal = Field(default=Decimal("10000"))
    leverage: Decimal = Field(default=Decimal("1.0"))  # M1 = 1x
    taker_fee: Decimal = Field(default=Decimal("0.0004"))  # 0.04% taker
    slippage: Decimal = Field(default=Decimal("0.0005"))  # 0.05% conservative
    interval: str = "1d"
    data_cache_dir: Path = Path("data/turtle_bot_cache")
    results_dir: Path = Path("results/turtle_bot/m1")

    @field_validator(
        "capital_allocation",
        "initial_capital",
        "leverage",
        "taker_fee",
        "slippage",
        mode="before",
    )
    @classmethod
    def _ensure_decimal(cls, v: object) -> Decimal:
        return _to_decimal(v)

    @field_validator("leverage")
    @classmethod
    def _check_leverage_cap(cls, v: Decimal) -> Decimal:
        if v > MAX_LEVERAGE:
            raise ValueError(f"leverage {v} exceeds hard cap {MAX_LEVERAGE} (trading §1.1)")
        return v


PARAMS_M1: TurtleParams = TurtleParams()
CONFIG_M1: BacktestConfig = BacktestConfig()
