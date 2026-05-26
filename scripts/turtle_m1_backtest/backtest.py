"""Turtle M1 backtest — video rules verbatim + doc rule enforcement.

Rules:
- Entry: close > rolling-20 high (long) or close < rolling-20 low (short), using
  shift(1) windows so the t-decision uses [t-20, t-1] only (no look-ahead).
- 200-SMA direction filter: long only when close > 200SMA, short only when below.
  Conflicting side at trade time: no entry, no flip.
- Position sizing: 2% per-asset-pool risk where stop_distance = ATR(20)*2 (Wilder).
- Exits: HHLL(11) channel break (opposite extreme of [t-11, t-1]) at next-bar open,
  OR ATR-based hard stop hit intraday (uses bar.high/low to detect).
- Fees: taker 0.04% per side, slippage 0.05% per side, applied to fill price.
- Capital: 50/50 split between BTC and ETH; each pool runs independently with
  its own equity, compounding from realized PnL.
- Leverage: 1.0 (M1 default; doc trading §1.1 cap is 2.0).
- All monetary computations in Decimal at PnL/equity layer (doc trading §1.2).
- Trade log emitted as JSON Lines (doc audit-log §2.1).
"""
from __future__ import annotations
import json
from decimal import Decimal, getcontext
from pathlib import Path
from datetime import timezone

import pandas as pd

getcontext().prec = 40

# ---- params (video) ----
ENTRY_WINDOW = 20
EXIT_WINDOW = 11
ATR_WINDOW = 20
ATR_STOP_MULT = Decimal("2")
SMA_WINDOW = 200
RISK_PER_TRADE = Decimal("0.02")          # 2% of pool equity (video)
TAKER_FEE = Decimal("0.0004")             # 0.04% per side
SLIPPAGE = Decimal("0.0005")              # 0.05% per side

# ---- backtest config ----
INITIAL_CAPITAL = Decimal("10000")
POOL_ALLOC = Decimal("0.5")               # 50/50 BTC vs ETH
LEVERAGE = Decimal("1")                   # M1: 1x


def _D(x: float | str | int) -> Decimal:
    return Decimal(str(x))


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder's smoothing = EMA with alpha = 1/n; equivalent to (prev*(n-1)+tr)/n
    atr = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return atr


def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    # shift(1) windows — t-decision sees [t-N, t-1]
    df["entry_high"] = df["high"].shift(1).rolling(ENTRY_WINDOW).max()
    df["entry_low"] = df["low"].shift(1).rolling(ENTRY_WINDOW).min()
    df["exit_high"] = df["high"].shift(1).rolling(EXIT_WINDOW).max()
    df["exit_low"] = df["low"].shift(1).rolling(EXIT_WINDOW).min()
    df["atr"] = wilder_atr(df["high"], df["low"], df["close"], ATR_WINDOW).shift(1)
    df["sma200"] = df["close"].shift(1).rolling(SMA_WINDOW).mean()
    return df


def run_backtest_clean(df: pd.DataFrame, symbol: str, initial_pool: Decimal,
                       trade_sink: list[dict]) -> dict:
    """Clean implementation with pending-action queue."""
    df = compute_signals(df).reset_index(drop=True)
    equity = initial_pool
    position = 0
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    qty: Decimal | None = None
    entry_time = None
    pending_exit = False
    pending_entry: int = 0  # 1 long, -1 short, 0 none

    equity_curve = []
    yearly_pnl: dict[int, Decimal] = {}

    for i in range(len(df)):
        row = df.iloc[i]
        bar_time = row["open_time"]
        year = pd.Timestamp(bar_time).year

        # ---- 1. process queued actions at this bar's OPEN ----
        if position != 0 and pending_exit:
            exit_px_raw = _D(row["open"])
            if position == 1:
                fill = exit_px_raw * (Decimal("1") - SLIPPAGE)
                gross = (fill - entry_price) * qty
            else:
                fill = exit_px_raw * (Decimal("1") + SLIPPAGE)
                gross = (entry_price - fill) * qty
            entry_notional = entry_price * qty
            exit_notional = fill * qty
            fees = (entry_notional + exit_notional) * TAKER_FEE
            pnl = gross - fees
            equity += pnl
            yearly_pnl[year] = yearly_pnl.get(year, Decimal("0")) + pnl
            trade_sink.append({
                "ts_entry": entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time),
                "ts_exit": bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time),
                "symbol": symbol,
                "side": "long" if position == 1 else "short",
                "qty": str(qty),
                "entry_price": str(entry_price),
                "exit_price": str(fill),
                "stop_price": str(stop_price),
                "exit_reason": "signal",
                "gross_pnl": str(gross),
                "fees": str(fees),
                "pnl": str(pnl),
                "equity_after": str(equity),
            })
            position = 0
            entry_price = stop_price = qty = None
            entry_time = None
            pending_exit = False

        if position == 0 and pending_entry != 0:
            atr = _D(row["atr"])
            if pd.isna(row["atr"]):
                pending_entry = 0
            else:
                open_px = _D(row["open"])
                if pending_entry == 1:
                    fill = open_px * (Decimal("1") + SLIPPAGE)
                    stop = fill - atr * ATR_STOP_MULT
                    risk_per_unit = fill - stop
                else:
                    fill = open_px * (Decimal("1") - SLIPPAGE)
                    stop = fill + atr * ATR_STOP_MULT
                    risk_per_unit = stop - fill
                if risk_per_unit > 0:
                    risk_capital = equity * RISK_PER_TRADE
                    size = risk_capital / risk_per_unit
                    notional = size * fill
                    max_notional = equity * LEVERAGE
                    if notional > max_notional:
                        size = max_notional / fill
                    if size > 0:
                        position = pending_entry
                        entry_price = fill
                        stop_price = stop
                        qty = size
                        entry_time = bar_time
                pending_entry = 0

        # ---- 2. intraday stop check ----
        if position != 0:
            assert entry_price is not None and stop_price is not None and qty is not None
            high = _D(row["high"]); low = _D(row["low"])
            stop_hit = False
            if position == 1 and low <= stop_price:
                stop_hit = True
                fill = stop_price
            elif position == -1 and high >= stop_price:
                stop_hit = True
                fill = stop_price

            if stop_hit:
                if position == 1:
                    gross = (fill - entry_price) * qty
                else:
                    gross = (entry_price - fill) * qty
                entry_notional = entry_price * qty
                exit_notional = fill * qty
                fees = (entry_notional + exit_notional) * TAKER_FEE
                pnl = gross - fees
                equity += pnl
                yearly_pnl[year] = yearly_pnl.get(year, Decimal("0")) + pnl
                trade_sink.append({
                    "ts_entry": entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time),
                    "ts_exit": bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time),
                    "symbol": symbol,
                    "side": "long" if position == 1 else "short",
                    "qty": str(qty),
                    "entry_price": str(entry_price),
                    "exit_price": str(fill),
                    "stop_price": str(stop_price),
                    "exit_reason": "stop",
                    "gross_pnl": str(gross),
                    "fees": str(fees),
                    "pnl": str(pnl),
                    "equity_after": str(equity),
                })
                position = 0
                entry_price = stop_price = qty = None
                entry_time = None
                pending_exit = False

        # ---- 3. evaluate signals at this bar's CLOSE for NEXT bar ----
        if pd.isna(row["entry_high"]) or pd.isna(row["sma200"]):
            equity_curve.append((bar_time, float(equity), position))
            continue

        close = _D(row["close"])
        eh = _D(row["entry_high"]); el = _D(row["entry_low"])
        xh = _D(row["exit_high"]); xl = _D(row["exit_low"])
        sma = _D(row["sma200"])

        if position == 1:
            if close < xl:
                pending_exit = True
        elif position == -1:
            if close > xh:
                pending_exit = True
        else:
            if close > eh and close > sma:
                pending_entry = 1
            elif close < el and close < sma:
                pending_entry = -1

        equity_curve.append((bar_time, float(equity), position))

    n_trades = sum(1 for t in trade_sink if t["symbol"] == symbol)
    wins = sum(1 for t in trade_sink if t["symbol"] == symbol and Decimal(t["pnl"]) > 0)
    pnl_total = sum((Decimal(t["pnl"]) for t in trade_sink if t["symbol"] == symbol), Decimal("0"))

    return {
        "symbol": symbol,
        "initial_pool": str(initial_pool),
        "final_equity": str(equity),
        "total_pnl": str(pnl_total),
        "pct_return": float(pnl_total / initial_pool * 100),
        "trades": n_trades,
        "wins": wins,
        "losses": n_trades - wins,
        "win_rate": (wins / n_trades * 100) if n_trades else 0.0,
        "yearly_pnl": {str(y): str(v) for y, v in sorted(yearly_pnl.items())},
        "equity_curve": equity_curve,
    }


def main():
    data_dir = Path("/home/claude/turtle_redo/data")
    out_dir = Path("/home/claude/turtle_redo/results")
    out_dir.mkdir(parents=True, exist_ok=True)

    trades: list[dict] = []
    summaries: dict[str, dict] = {}

    pool_btc = INITIAL_CAPITAL * POOL_ALLOC
    pool_eth = INITIAL_CAPITAL * POOL_ALLOC

    for symbol, pool in [("BTCUSDT", pool_btc), ("ETHUSDT", pool_eth)]:
        df = pd.read_csv(data_dir / f"{symbol}_1d.csv", parse_dates=["open_time"])
        s = run_backtest_clean(df, symbol, pool, trades)
        s.pop("equity_curve", None)
        summaries[symbol] = s
        print(f"\n=== {symbol} ===", flush=True)
        for k, v in s.items():
            if k == "yearly_pnl":
                continue
            print(f"  {k}: {v}", flush=True)
        print(f"  yearly_pnl:", flush=True)
        for y, v in s["yearly_pnl"].items():
            print(f"    {y}: ${float(v):,.2f}", flush=True)

    trades_file = out_dir / "trades.jsonl"
    with trades_file.open("w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")

    total_initial = INITIAL_CAPITAL
    total_final = Decimal(summaries["BTCUSDT"]["final_equity"]) + Decimal(summaries["ETHUSDT"]["final_equity"])
    total_pnl = total_final - total_initial
    summary_payload = {
        "ts": pd.Timestamp.now(tz=timezone.utc).isoformat(),
        "config": {
            "entry_window": ENTRY_WINDOW, "exit_window": EXIT_WINDOW,
            "atr_window": ATR_WINDOW, "atr_stop_mult": str(ATR_STOP_MULT),
            "sma_window": SMA_WINDOW, "risk_per_trade": str(RISK_PER_TRADE),
            "taker_fee": str(TAKER_FEE), "slippage": str(SLIPPAGE),
            "initial_capital": str(INITIAL_CAPITAL), "pool_alloc": str(POOL_ALLOC),
            "leverage": str(LEVERAGE),
        },
        "per_symbol": summaries,
        "portfolio": {
            "initial_capital": str(total_initial),
            "final_equity": str(total_final),
            "total_pnl": str(total_pnl),
            "pct_return": float(total_pnl / total_initial * 100),
            "n_trades": len(trades),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary_payload, indent=2))
    print(f"\n=== PORTFOLIO ===", flush=True)
    print(f"  initial: ${float(total_initial):,.2f}", flush=True)
    print(f"  final:   ${float(total_final):,.2f}", flush=True)
    print(f"  pnl:     ${float(total_pnl):,.2f} ({float(total_pnl/total_initial*100):.2f}%)", flush=True)
    print(f"  trades:  {len(trades)}", flush=True)

    sum_pnl = sum(Decimal(t["pnl"]) for t in trades)
    expected_final = total_initial + sum_pnl
    print(f"\n[cross-check] sum(trade_pnl) + initial = {expected_final}", flush=True)
    print(f"[cross-check] reported final          = {total_final}", flush=True)
    print(f"[cross-check] match: {expected_final == total_final}", flush=True)


if __name__ == "__main__":
    main()
