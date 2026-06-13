"""Martingale (DCA) bot configuration — Bitget screenshot params baked in,
doc/ rules enforced.

All price/quantity/balance/return values use ``decimal.Decimal`` only — never
``float`` (trading §1.2). Leverage is hard-capped at 2x (§1.1); spot martingale
runs 1x. Parameter changes vs the screenshot must be logged in trading §3.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class MartingaleParams(BaseModel):
    """Strategy parameters — defaults are the Bitget "Aggressive" preset verbatim.

    Ladder semantics (3Commas/Bitget DCA convention):
      - Safety order i (i = 1..N) triggers when price has dropped a cumulative
        ``sum_{k=1..i} price_drop_step * step_scale^(k-1)`` below the base price.
        With step_scale = 1.0 this is a flat 1%, 2%, 3%, 4%, 5% ladder.
      - Safety order i size (in quote/USDT) = ``safety_order_size * volume_scale^(i-1)``.
        With volume_scale = 2.5 the sizes are 1x, 2.5x, 6.25x, 15.625x, 39.0625x.
      - Take-profit triggers at ``avg_entry * (1 + tp_target)`` over ALL filled legs,
        then the whole position is sold and a new cycle starts (immediate trigger).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # --- Bitget screenshot parameters (BGB/USDT · Normal · Aggressive) ---
    price_drop_step: Decimal = Field(default=Decimal("0.01"))   # 1% drop per step
    tp_target: Decimal = Field(default=Decimal("0.01"))         # 1% single-cycle TP
    max_safety_orders: int = Field(default=5, ge=0, le=20)      # screenshot: 5
    volume_scale: Decimal = Field(default=Decimal("2.5"))       # 2.50x (martingale mult)
    step_scale: Decimal = Field(default=Decimal("1.0"))         # 1.00x (price step mult)
    immediate_trigger: bool = True                              # "Immediate trigger"

    # --- order sizing (quote/USDT). Bitget shows these on the funding screen, not
    #     this card; kept configurable. base = first (immediate) buy. ---
    base_order_size: Decimal = Field(default=Decimal("100"))
    safety_order_size: Decimal = Field(default=Decimal("100"))  # SO #1 size; scales by volume_scale

    # --- winner-asymmetry risk overlay (trading §8 R5). OFF by default to match
    #     vanilla Bitget martingale (which has NO stop loss). When > 0, the whole
    #     cycle is cut at this fractional loss below the LAST filled leg's price —
    #     imposing "cut losers short" on a strategy that otherwise bag-holds. ---
    hard_stop_pct: Decimal = Field(default=Decimal("0"))        # 0 = disabled (Bitget default)

    @field_validator(
        "price_drop_step",
        "tp_target",
        "volume_scale",
        "step_scale",
        "base_order_size",
        "safety_order_size",
        "hard_stop_pct",
        mode="before",
    )
    @classmethod
    def _ensure_decimal(cls, v: object) -> Decimal:
        return _to_decimal(v)

    @field_validator("price_drop_step", "tp_target")
    @classmethod
    def _step_in_unit_interval(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v < Decimal("1")):
            raise ValueError("price_drop_step / tp_target must be in (0, 1)")
        return v

    @field_validator("volume_scale")
    @classmethod
    def _volume_scale_ge_one(cls, v: Decimal) -> Decimal:
        # < 1 would mean each safety order is SMALLER — no longer a martingale.
        if v < Decimal("1"):
            raise ValueError("volume_scale must be >= 1 (martingale averages up in size)")
        return v

    @field_validator("step_scale")
    @classmethod
    def _step_scale_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("step_scale must be > 0")
        return v

    @field_validator("base_order_size", "safety_order_size")
    @classmethod
    def _size_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("order sizes must be > 0")
        return v

    @field_validator("hard_stop_pct")
    @classmethod
    def _hard_stop_non_negative(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") <= v < Decimal("1")):
            raise ValueError("hard_stop_pct must be in [0, 1) (0 disables)")
        return v

    @model_validator(mode="after")
    def _deepest_leg_stays_positive(self) -> "MartingaleParams":
        # With step_scale > 1 and many safety orders the cumulative deviation can
        # exceed 100% → a negative ladder price, which is nonsense. Reject at config
        # time rather than letting build_grid blow up mid-backtest.
        cum = Decimal("0")
        step = self.price_drop_step
        for _ in range(self.max_safety_orders):
            cum += step
            step *= self.step_scale
        if cum >= Decimal("1"):
            raise ValueError(
                f"cumulative price deviation {cum} >= 100% at the deepest safety order "
                "→ negative price. Reduce max_safety_orders / step_scale / price_drop_step."
            )
        return self


class BacktestConfig(BaseModel):
    """Backtest run configuration: symbol, capital, costs, data/results paths."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    symbol: str = "BGBUSDT"                                     # screenshot: BGB/USDT
    initial_capital: Decimal = Field(default=Decimal("10000"))
    leverage: Decimal = Field(default=Decimal("1.0"))          # spot = 1x
    taker_fee: Decimal = Field(default=Decimal("0.001"))       # Bitget spot taker 0.1%
    slippage: Decimal = Field(default=Decimal("0.0005"))       # 0.05% conservative
    interval: str = "5m"
    data_cache_dir: Path = Path("data/martingale_bot_cache")
    results_dir: Path = Path("results/martingale_bot/m1")

    @field_validator(
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
        if v <= Decimal("0"):
            raise ValueError("leverage must be > 0")
        return v

    @field_validator("initial_capital")
    @classmethod
    def _capital_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("initial_capital must be > 0")
        return v

    @field_validator("taker_fee", "slippage")
    @classmethod
    def _cost_non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError("taker_fee / slippage must be >= 0")
        return v


def max_cycle_cost(params: MartingaleParams) -> Decimal:
    """Total quote (USDT) committed if the full ladder fills: base + every safety order.

    This is the martingale capital trap made explicit. With the screenshot defaults
    (base 100, SO 100, volume_scale 2.5, 5 safety orders) one cycle can demand
    ~6,544 USDT — 65x the base order — to hold a position that is now ~5% underwater.
    """
    total = params.base_order_size
    for i in range(params.max_safety_orders):
        total += params.safety_order_size * (params.volume_scale ** i)
    return total


PARAMS_AGGRESSIVE: MartingaleParams = MartingaleParams()
CONFIG_AGGRESSIVE: BacktestConfig = BacktestConfig()
