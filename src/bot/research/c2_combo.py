"""Combined-hypothesis backtest: C-2 entry × 1491 exit × systemic-capitulation gate.

Implements the recommended integrated test from
`doc/domains/trading/rules.md §8` [2026.06.11 조합분석] under the validation
methodology in `doc/skills/signal-validation.md §2`.

The hypothesis (each piece maps to a recorded finding):
  • ENTRY   R2/C-2  — OI drops >0.5% AND taker buy/sell < 0.90 → go LONG (fade
                      capitulation). On its own this was REJECTED (2 of 41 OOS
                      trades were -8%/-10% in sustained crashes, no stop).
  • EXIT    R4/1491 — SL -2%, max hold 5 days, breakeven after +1%. The losers
                      that killed C-2 get truncated at -2%; winners ride to 5d.
                      Tests R5 ("alpha ~90% is in the exit") on C-2's n=41.
  • REGIME  N1+N2   — only enter when capitulation is SYSTEMIC (many symbols at
                      once) and/or in the top intensity quantile. This is the
                      discriminator C-2.1's blunt -8% trend filter lacked.

LOOK-AHEAD SAFETY (signal-validation §2.4) — enforced and unit-tested:
  - entry executes at the NEXT bar's open; the signal at bar T uses only data
    confirmed at the close of T.
  - the regime intensity threshold is a TRAILING quantile over strictly-past
    bars; never the full-sample distribution.
  - exit simulation walks forward using only each bar's own OHLC.

NUMERIC POLICY (trading §1.2 / §7.1):
  - prices flow as float in the numpy hot path (§7.1 carve-out);
  - each trade's entry/exit price and return are recorded as Decimal(str(x))
    (§1.2 — the money math);
  - the block bootstrap operates on the float view of those returns (§7.1 —
    stats hot path), and the final reported figures are Decimal.

This module depends only on numpy + the stdlib so its logic is cheap to
unit-test without market data or Polars.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional

import numpy as np

# --- block bootstrap reproducibility (audit-log / signal-validation §2.2) -----
BOOTSTRAP_SEED = 42
BOOTSTRAP_RESAMPLES = 10_000
DEFAULT_BLOCK = 7  # block 6~8 per validation pipeline asset (trading §8 [06.11])


class ExitMode(str, Enum):
    FIXED_HOLD = "fixed_hold"        # baseline: hold N bars, no stop (C-2 original)
    TRAILING_1491 = "trailing_1491"  # SL -2% + breakeven + max-hold (the test)


# ============================================================================
# Parameter blocks (frozen — locked BEFORE evaluating C-2, §2.4 researcher-DoF)
# ============================================================================

@dataclass(frozen=True)
class EntryParams:
    """C-2 (R2). OI drop and taker imbalance thresholds."""
    oi_drop_pct: float = -0.005    # OI 급감 ≤ -0.5%
    taker_ratio_max: float = 0.90  # taker buy/sell < 0.90 (sellers dominate)


@dataclass(frozen=True)
class ExitParams:
    """1491 (R4). Values locked from the OKX trader BEFORE looking at C-2's
    loss sizes — applying them to C-2 is therefore a quasi-out-of-sample test
    of the exit rule (signal-validation §2.4 ordering guard)."""
    stop_loss_pct: float = 0.02          # SL -2%
    breakeven_trigger_pct: float = 0.01  # after +1% move stop to entry (live BE_TRIGGER, trading §3)
    max_hold_bars: int = 5 * 24 * 12     # 5 days of 5m bars = 1440
    fixed_hold_bars: int = 24 * 12        # baseline comparison: fixed 24h = 288 bars


@dataclass(frozen=True)
class RegimeParams:
    """N1 (intensity) + N2 (systemic breadth)."""
    intensity_quantile: float = 0.02   # N1: OI-drop in the most-extreme 2% (lower tail)
    intensity_window: int = 30 * 24 * 12  # trailing 30d window for the quantile
    intensity_warmup: int = 7 * 24 * 12   # need ≥7d of past bars before the gate is valid
    min_systemic_symbols: int = 3      # N2: ≥3 symbols capitulating in the same bar
    require: str = "any"               # "n1" | "n2" | "both" | "any" | "off"


@dataclass(frozen=True)
class CostParams:
    """Round-trip friction subtracted from every trade (Binance USDⓈ-M futures)."""
    taker_fee_pct: float = 0.0004  # per side
    slippage_pct: float = 0.0002   # per side

    @property
    def round_trip(self) -> float:
        return 2 * (self.taker_fee_pct + self.slippage_pct)


@dataclass(frozen=True)
class MarketArrays:
    """One symbol's aligned 5m series. All float; ts is unix-seconds (UTC)."""
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    oi: np.ndarray
    taker_buy: np.ndarray
    taker_sell: np.ndarray

    def __post_init__(self) -> None:
        n = len(self.ts)
        for name in ("open", "high", "low", "close", "oi", "taker_buy", "taker_sell"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"array length mismatch: {name}")


# ============================================================================
# Derived signals (vectorised, look-ahead safe)
# ============================================================================

def oi_delta_pct(oi: np.ndarray) -> np.ndarray:
    """(oi[t] - oi[t-1]) / oi[t-1]; oi_delta[0] = nan (no prior bar)."""
    prev = np.empty_like(oi, dtype=float)
    prev[0] = np.nan
    prev[1:] = oi[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        d = (oi - prev) / prev
    return d


def taker_ratio(buy: np.ndarray, sell: np.ndarray) -> np.ndarray:
    """taker buy/sell volume ratio; nan where sell == 0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        r = buy / np.where(sell == 0, np.nan, sell)
    return r


def trailing_quantile(
    x: np.ndarray, q: float, window: int, warmup: int,
    where: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Quantile of x over the STRICTLY-PAST window [t-window, t-1].

    Returns nan until `warmup` non-nan past observations exist. Because the
    window ends at t-1 (current bar excluded), the result at t is usable to
    gate an entry at t with no look-ahead (signal-validation §2.4).

    `where`: optional boolean mask the same length as x. If provided, the
    quantile is computed only at indices where the mask is True; everywhere
    else the result stays nan. This is the fast path for backtests where the
    quantile is only consulted at sparse candidate bars — O(n_cand · w log w)
    instead of O(n · w log w) for multi-year inputs. Correctness is identical
    because non-candidate bars never read the threshold.
    """
    n = len(x)
    out = np.full(n, np.nan, dtype=float)
    indices: np.ndarray | range
    if where is None:
        indices = range(n)
    else:
        if len(where) != n:
            raise ValueError("where mask length must equal x length")
        indices = np.where(where)[0]
    for t in indices:
        lo = max(0, t - window)
        past = x[lo:t]  # excludes t
        past = past[~np.isnan(past)]
        if len(past) >= warmup:
            out[t] = float(np.quantile(past, q))
    return out


def capitulation_flag(
    oi_d: np.ndarray, t_ratio: np.ndarray, p: EntryParams
) -> np.ndarray:
    """Boolean C-2 raw condition per bar (entry candidate before regime gate)."""
    cond = (oi_d <= p.oi_drop_pct) & (t_ratio < p.taker_ratio_max)
    return np.nan_to_num(cond, nan=0.0).astype(bool)


def systemic_capitulation_count(
    flags_by_symbol: dict[str, np.ndarray],
    ts_by_symbol: dict[str, np.ndarray],
    target_ts: np.ndarray,
) -> np.ndarray:
    """For each target_ts bar, how many symbols are capitulating in that bar.

    N2 breadth. Aligns each symbol's capitulation flag onto target_ts by exact
    timestamp match (missing bars count as not-capitulating).
    """
    count = np.zeros(len(target_ts), dtype=int)
    target_index = {int(t): i for i, t in enumerate(target_ts)}
    for sym, flags in flags_by_symbol.items():
        sym_ts = ts_by_symbol[sym]
        for ti, f in zip(sym_ts, flags):
            if f:
                idx = target_index.get(int(ti))
                if idx is not None:
                    count[idx] += 1
    return count


# ============================================================================
# Trade simulation
# ============================================================================

@dataclass(frozen=True)
class Trade:
    entry_ts: int
    exit_ts: int
    entry_price: Decimal
    exit_price: Decimal
    return_pct: Decimal   # net of round-trip cost
    bars_held: int
    exit_reason: str


def _simulate_one(
    m: MarketArrays,
    entry_idx: int,
    exit_p: ExitParams,
    cost: CostParams,
    mode: ExitMode,
) -> Trade:
    """Simulate a single LONG trade entered at the OPEN of `entry_idx`.

    Walks forward bar by bar using only each bar's own OHLC. Returns the
    realised Trade. Exit price is Decimal(str(...)) (§1.2)."""
    n = len(m.ts)
    entry_price = float(m.open[entry_idx])
    rt = cost.round_trip

    if mode is ExitMode.FIXED_HOLD:
        last = min(entry_idx + exit_p.fixed_hold_bars, n - 1)
        exit_price = float(m.close[last])
        reason = "fixed_hold"
        gross = (exit_price - entry_price) / entry_price
        return _mk_trade(m, entry_idx, last, entry_price, exit_price, gross - rt, reason)

    # TRAILING_1491: SL -2% with breakeven-after-+1%, capped at max_hold.
    init_stop = entry_price * (1.0 - exit_p.stop_loss_pct)
    be_level = entry_price * (1.0 + exit_p.breakeven_trigger_pct)
    be_armed = False
    horizon = min(entry_idx + exit_p.max_hold_bars, n - 1)

    for j in range(entry_idx, horizon + 1):
        # arm breakeven once the bar's high reaches +1%
        if not be_armed and float(m.high[j]) >= be_level:
            be_armed = True
        stop = entry_price if be_armed else init_stop

        # gap-through: a later bar opening BELOW the stop fills at the open
        # (open == stop is not a gap → handled by the intrabar branch at `stop`)
        if j > entry_idx and float(m.open[j]) < stop:
            exit_price = float(m.open[j])
            reason = "gap_stop" if not be_armed else "gap_breakeven"
            gross = (exit_price - entry_price) / entry_price
            return _mk_trade(m, entry_idx, j, entry_price, exit_price, gross - rt, reason)

        # intrabar stop hit
        if float(m.low[j]) <= stop:
            exit_price = stop
            reason = "stop_loss" if not be_armed else "breakeven"
            gross = (exit_price - entry_price) / entry_price
            return _mk_trade(m, entry_idx, j, entry_price, exit_price, gross - rt, reason)

    # never stopped → exit at the close of the max-hold bar
    exit_price = float(m.close[horizon])
    gross = (exit_price - entry_price) / entry_price
    return _mk_trade(m, entry_idx, horizon, entry_price, exit_price, gross - rt, "max_hold")


def _mk_trade(
    m: MarketArrays,
    entry_idx: int,
    exit_idx: int,
    entry_price: float,
    exit_price: float,
    net_return: float,
    reason: str,
) -> Trade:
    return Trade(
        entry_ts=int(m.ts[entry_idx]),
        exit_ts=int(m.ts[exit_idx]),
        entry_price=Decimal(str(entry_price)),
        exit_price=Decimal(str(exit_price)),
        return_pct=Decimal(str(net_return)),
        bars_held=exit_idx - entry_idx,
        exit_reason=reason,
    )


# ============================================================================
# Entry scan + full backtest
# ============================================================================

def _regime_ok(
    rp: RegimeParams,
    n1_pass: bool,
    n2_pass: bool,
) -> bool:
    if rp.require == "off":
        return True
    if rp.require == "n1":
        return n1_pass
    if rp.require == "n2":
        return n2_pass
    if rp.require == "both":
        return n1_pass and n2_pass
    return n1_pass or n2_pass  # "any"


def scan_and_simulate(
    m: MarketArrays,
    mode: ExitMode,
    *,
    entry_p: EntryParams = EntryParams(),
    exit_p: ExitParams = ExitParams(),
    regime_p: RegimeParams = RegimeParams(),
    cost: CostParams = CostParams(),
    systemic_count: Optional[np.ndarray] = None,
) -> list[Trade]:
    """Scan for C-2 signals, apply the regime gate, simulate one position at a
    time (no overlap), and return realised trades.

    A signal at bar T enters at the OPEN of bar T+1 (look-ahead safe). While a
    position is open, new signals are ignored.
    """
    n = len(m.ts)
    oi_d = oi_delta_pct(m.oi)
    t_ratio = taker_ratio(m.taker_buy, m.taker_sell)
    cap = capitulation_flag(oi_d, t_ratio, entry_p)

    # N1: trailing lower-tail quantile of OI delta (more negative = more extreme).
    # Only computed at candidate bars (where cap is True) — the threshold is
    # never read elsewhere, so this is identical in result but O(n_cand) instead
    # of O(n). Critical for multi-year backtests; see trailing_quantile docstring.
    intensity_threshold = trailing_quantile(
        oi_d, regime_p.intensity_quantile, regime_p.intensity_window,
        regime_p.intensity_warmup, where=cap,
    )

    if systemic_count is None:
        systemic_count = np.zeros(n, dtype=int)

    trades: list[Trade] = []
    i = 0
    while i < n - 1:  # need an i+1 bar to enter on
        if cap[i]:
            n1_pass = (not math.isnan(intensity_threshold[i])) and (oi_d[i] <= intensity_threshold[i])
            n2_pass = systemic_count[i] >= regime_p.min_systemic_symbols
            if _regime_ok(regime_p, n1_pass, n2_pass):
                trade = _simulate_one(m, i + 1, exit_p, cost, mode)
                trades.append(trade)
                # resume scanning after the trade closes (no overlapping positions)
                exit_i = _index_of_ts(m.ts, trade.exit_ts)
                i = max(i + 1, exit_i) + 1
                continue
        i += 1
    return trades


def _index_of_ts(ts: np.ndarray, target: int) -> int:
    hit = np.where(ts == target)[0]
    return int(hit[0]) if len(hit) else len(ts) - 1


# ============================================================================
# Statistics (signal-validation §2.2 / §2.3)
# ============================================================================

def block_bootstrap_ci(
    returns: np.ndarray,
    *,
    block: int = DEFAULT_BLOCK,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Moving-block bootstrap CI for the MEAN per-trade return.

    Resamples consecutive blocks of trades to preserve the autocorrelation /
    episode-clustering that makes naive IID inference overstate significance
    (signal-validation §2.2). Returns (point_mean, ci_low, ci_high).
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    point = float(np.mean(r))
    if n == 1:
        return (point, point, point)

    b = max(1, min(block, n))
    n_blocks = math.ceil(n / b)
    max_start = n - b
    rng = np.random.default_rng(seed)

    means = np.empty(resamples, dtype=float)
    for k in range(resamples):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        sample = np.concatenate([r[s:s + b] for s in starts])[:n]
        means[k] = sample.mean()

    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return (point, lo, hi)


def count_independent_episodes(entry_ts: np.ndarray, gap_days: float = 7.0) -> int:
    """Cluster trades whose entries are within `gap_days` of each other into one
    'episode'. Surfaces the true number of INDEPENDENT events behind a trade
    count (signal-validation §2.2/§2.3 — many 5m trades can be ~5 real crises)."""
    if len(entry_ts) == 0:
        return 0
    ts = np.sort(np.asarray(entry_ts, dtype=float))
    gap = gap_days * 86400.0
    return 1 + int(np.sum(np.diff(ts) > gap))


def profit_factor(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype=float)
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else float("nan")
    return float(gains / losses)


# ============================================================================
# Result container + top-level runner
# ============================================================================

@dataclass
class BacktestResult:
    mode: str
    n_trades: int
    n_independent_episodes: int
    mean_return: Decimal
    ci_low: Decimal
    ci_high: Decimal
    profit_factor: float
    win_rate: float
    worst_trade: Decimal
    significant: bool       # CI strictly above 0
    trades: list[Trade] = field(default_factory=list)

    def summary(self) -> str:
        verdict = "SIGNIFICANT (CI>0)" if self.significant else "NOT significant (CI includes 0)"
        return (
            f"[{self.mode}] n_trades={self.n_trades}  "
            f"independent_episodes={self.n_independent_episodes}  "
            f"mean={self.mean_return:.4%}  CI95=[{self.ci_low:.4%}, {self.ci_high:.4%}]  "
            f"PF={self.profit_factor:.2f}  win={self.win_rate:.1%}  "
            f"worst={self.worst_trade:.2%}  → {verdict}"
        )


def summarise(trades: list[Trade], mode: ExitMode) -> BacktestResult:
    rets = np.array([float(t.return_pct) for t in trades], dtype=float)
    point, lo, hi = block_bootstrap_ci(rets)
    n = len(trades)
    win_rate = float(np.mean(rets > 0)) if n else float("nan")
    worst = float(rets.min()) if n else float("nan")
    episodes = count_independent_episodes(np.array([t.entry_ts for t in trades]))
    return BacktestResult(
        mode=mode.value,
        n_trades=n,
        n_independent_episodes=episodes,
        mean_return=Decimal(str(point)),
        ci_low=Decimal(str(lo)),
        ci_high=Decimal(str(hi)),
        profit_factor=profit_factor(rets),
        win_rate=win_rate,
        worst_trade=Decimal(str(worst)),
        significant=(not math.isnan(lo)) and lo > 0.0,
        trades=trades,
    )


def run_comparison(
    m: MarketArrays,
    *,
    entry_p: EntryParams = EntryParams(),
    exit_p: ExitParams = ExitParams(),
    regime_p: RegimeParams = RegimeParams(),
    cost: CostParams = CostParams(),
    systemic_count: Optional[np.ndarray] = None,
) -> dict[str, BacktestResult]:
    """Run BOTH exit modes on the SAME entries → isolates the 'exit alpha' (R5).

    The only difference between the two results is the exit rule, so
    (trailing_1491.mean - fixed_hold.mean) is the marginal contribution of the
    1491 exit discipline on identical C-2 entries.
    """
    out: dict[str, BacktestResult] = {}
    for mode in (ExitMode.FIXED_HOLD, ExitMode.TRAILING_1491):
        trades = scan_and_simulate(
            m, mode, entry_p=entry_p, exit_p=exit_p,
            regime_p=regime_p, cost=cost, systemic_count=systemic_count,
        )
        out[mode.value] = summarise(trades, mode)
    return out
