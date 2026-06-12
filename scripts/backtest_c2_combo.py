"""CLI for the C-2 × 1491-exit × systemic-capitulation backtest.

Thin data-loading shell around `bot.research.c2_combo` (all the tested logic
lives there). Three data sources:

  --demo                 synthetic data, no network — proves the pipeline runs
                         and shows the report format. Use this anywhere.

  --data-dir DIR         load per-symbol CSVs `DIR/<SYMBOL>.csv` with the
                         canonical schema (see CANONICAL_SCHEMA below). This is
                         the path for the full 2020-2025 study: pre-download
                         Binance S3 dumps (data.binance.vision) once, merge
                         klines + openInterestHist into this schema, point here.

  --fetch --days N       fetch the last N days via Binance Futures REST
                         (klines + openInterestHist). Only works where Binance
                         is reachable (e.g. the mac-mini research box); it is
                         BLOCKED in the allowlisted CI/web container.

Report prints BOTH exit modes (fixed-hold baseline vs 1491 trailing) so the
"exit alpha" (R5) is the difference between them, plus the independent-episode
count that surfaces the ~5-crisis sample-size reality (signal-validation §2.2).

NOTE: nothing here trades. Live entry requires the trading §1.3 gate
(backtest → walkforward → paper 7d → 10% seed) + explicit user approval.
"""

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.research.c2_combo import (  # noqa: E402
    DEFAULT_BLOCK,
    BOOTSTRAP_RESAMPLES,
    CostParams,
    EntryParams,
    ExitMode,
    ExitParams,
    MarketArrays,
    RegimeParams,
    capitulation_flag,
    normalize_ts_seconds,
    oi_delta_pct,
    run_comparison,
    systemic_capitulation_count,
    taker_ratio,
)

CANONICAL_SCHEMA = "ts,open,high,low,close,oi,taker_buy,taker_sell"
_BINANCE_KLINE = "https://fapi.binance.com/fapi/v1/klines"
_BINANCE_OI_HIST = "https://fapi.binance.com/futures/data/openInterestHist"


# ---------------------------------------------------------------------------
# data sources
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> MarketArrays:
    """Load one symbol's canonical CSV → MarketArrays. ts may be unix seconds,
    unix milliseconds (Binance S3 dumps), or ISO-8601; everything else float.
    Millisecond timestamps are normalised to seconds (normalize_ts_seconds)."""
    cols: dict[str, list[float]] = {k: [] for k in CANONICAL_SCHEMA.split(",")}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = set(cols) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}: missing columns {sorted(missing)} (need {CANONICAL_SCHEMA})")
        for row in reader:
            cols["ts"].append(_parse_ts(row["ts"]))
            for k in ("open", "high", "low", "close", "oi", "taker_buy", "taker_sell"):
                cols[k].append(float(row[k]))
    arrays = {k: np.asarray(v, dtype=float) for k, v in cols.items()}
    arrays["ts"] = normalize_ts_seconds(arrays["ts"])  # ms (S3) → seconds
    try:
        return MarketArrays(**arrays)
    except ValueError as exc:  # surface which file tripped the ts/length invariant
        raise ValueError(f"{path.name}: {exc}") from exc


def _parse_ts(s: str) -> float:
    s = s.strip()
    try:
        return float(s)  # unix seconds OR milliseconds; normalised by the caller
    except ValueError:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def gen_demo(seed: int = 42, n: int = 2304) -> dict[str, MarketArrays]:
    """Synthetic 8-day-ish universe (BTC + 2 alts) with two injected SYSTEMIC
    capitulation events: one that keeps crashing (kills fixed-hold) and one that
    bounces. Deterministic. No network."""
    rng = np.random.default_rng(seed)
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    out: dict[str, MarketArrays] = {}

    for s_i, sym in enumerate(syms):
        price = 100.0 * (1 + s_i)
        oi = 1_000_000.0
        rows = []
        for i in range(n):
            drift = rng.normal(0, 0.0015)
            oi_step = rng.normal(0, 0.001)
            buy, sell = 1.0, 1.0
            # event A @ ~bar 600: systemic capitulation then KEEPS crashing
            if 600 <= i < 660:
                oi_step = -0.02
                drift = -0.01
                buy, sell = 0.7, 1.0
            # event B @ ~bar 1500: systemic capitulation then BOUNCES
            elif 1500 <= i < 1560:
                oi_step = -0.02
                drift = 0.006 if i > 1505 else -0.008
                buy, sell = 0.7, 1.0
            price *= (1 + drift)
            oi *= (1 + oi_step)
            o = price
            h = price * (1 + abs(rng.normal(0, 0.001)))
            lo = price * (1 - abs(rng.normal(0, 0.001)))
            c = price * (1 + rng.normal(0, 0.0008))
            rows.append((t0 + i * 300, o, h, lo, c, oi, buy, sell))
        arr = np.array(rows, dtype=float)
        out[sym] = MarketArrays(
            ts=arr[:, 0], open=arr[:, 1], high=arr[:, 2], low=arr[:, 3],
            close=arr[:, 4], oi=arr[:, 5], taker_buy=arr[:, 6], taker_sell=arr[:, 7],
        )
    return out


async def _fetch_symbol(session, symbol: str, days: int) -> MarketArrays:
    """Pull 5m klines + OI history for one symbol.

    Pagination guard: both Binance endpoints can stall a naive loop. klines
    will re-return the in-progress current bar when startTime catches up to
    now; openInterestHist (`period=5m`) only retains the last ~30 days, so a
    startTime outside that window is silently clamped and the same 500-row
    batch returns forever. We break the moment `cur` fails to advance past
    the previous request's last timestamp — same correctness, no hang.
    """
    import aiohttp  # noqa: F401  (only needed on the fetch path)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    start_ms = now_ms - days * 86_400_000

    klines: list[list] = []
    cur = start_ms
    prev_cur = -1
    while cur < now_ms:
        if cur <= prev_cur:
            break  # pagination not advancing — Binance is re-returning the same batch
        prev_cur = cur
        async with session.get(_BINANCE_KLINE, params={
            "symbol": symbol, "interval": "5m", "startTime": cur, "endTime": now_ms, "limit": 1500,
        }) as r:
            r.raise_for_status()
            batch = await r.json()
        if not batch:
            break
        klines.extend(batch)
        cur = int(batch[-1][0]) + 1

    # openInterestHist: max 500/call, only the last ~30d are retained.
    # Caller asking for >30d still works — older OI is left nan and ffilled in
    # the kline merge below — but we MUST guard the pagination cursor or the
    # API's silent clamping turns the loop infinite.
    oi_map: dict[int, float] = {}
    cur = start_ms
    prev_cur = -1
    while cur < now_ms:
        if cur <= prev_cur:
            break
        prev_cur = cur
        async with session.get(_BINANCE_OI_HIST, params={
            "symbol": symbol, "period": "5m", "startTime": cur, "endTime": now_ms, "limit": 500,
        }) as r:
            r.raise_for_status()
            batch = await r.json()
        if not batch:
            break
        for row in batch:
            oi_map[int(row["timestamp"])] = float(row["sumOpenInterest"])
        next_cur = int(batch[-1]["timestamp"]) + 1
        if next_cur <= cur:
            break  # last timestamp didn't move forward — retention boundary hit
        cur = next_cur

    rows = []
    last_oi = float("nan")
    for k in klines:
        ts_ms = int(k[0])
        last_oi = oi_map.get(ts_ms, last_oi)  # ffill (trading §7.2)
        buy = float(k[9])               # taker buy base volume
        sell = float(k[5]) - buy        # total - buy
        rows.append((ts_ms / 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                     last_oi, buy, max(sell, 0.0)))
    arr = np.array(rows, dtype=float)
    return MarketArrays(
        ts=arr[:, 0], open=arr[:, 1], high=arr[:, 2], low=arr[:, 3],
        close=arr[:, 4], oi=arr[:, 5], taker_buy=arr[:, 6], taker_sell=arr[:, 7],
    )


def fetch_rest(symbols: list[str], days: int) -> dict[str, MarketArrays]:
    import aiohttp

    async def _run():
        async with aiohttp.ClientSession() as session:
            return {s: await _fetch_symbol(session, s, days) for s in symbols}

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def build_systemic_count(universe: dict[str, MarketArrays], traded_ts: np.ndarray) -> np.ndarray:
    ep = EntryParams()
    flags, tss = {}, {}
    for sym, m in universe.items():
        cap = capitulation_flag(oi_delta_pct(m.oi), taker_ratio(m.taker_buy, m.taker_sell), ep)
        flags[sym] = cap
        tss[sym] = m.ts
    return systemic_capitulation_count(flags, tss, traded_ts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", action="store_true", help="synthetic data, no network")
    src.add_argument("--data-dir", type=Path, help=f"dir of <SYMBOL>.csv ({CANONICAL_SCHEMA})")
    src.add_argument("--fetch", action="store_true", help="fetch via Binance REST (needs network)")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT", help="universe for N2 breadth")
    ap.add_argument("--traded", default="BTCUSDT", help="symbol to trade (must be in --symbols)")
    ap.add_argument("--days", type=int, default=30, help="lookback for --fetch")
    ap.add_argument("--regime", default="n2", choices=["off", "n1", "n2", "both", "any"])
    ap.add_argument("--min-systemic", type=int, default=3)
    ap.add_argument("--block", type=int, default=DEFAULT_BLOCK)
    ap.add_argument("--resamples", type=int, default=BOOTSTRAP_RESAMPLES)
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.demo:
        universe = gen_demo()
        symbols = list(universe)
        args.traded = "BTCUSDT"
    elif args.data_dir:
        universe = {s: load_csv(args.data_dir / f"{s}.csv") for s in symbols}
    else:
        print(f"Fetching {args.days}d for {symbols} from Binance REST ...")
        try:
            universe = fetch_rest(symbols, args.days)
        except Exception as exc:  # network/allowlist failure surfaces clearly
            print(f"FETCH FAILED ({exc}).\nBinance is unreachable here (allowlist). "
                  f"Use --demo, or --data-dir with pre-downloaded S3 CSVs.", file=sys.stderr)
            return 2

    if args.traded not in universe:
        print(f"--traded {args.traded} not in universe {list(universe)}", file=sys.stderr)
        return 2

    m = universe[args.traded]
    systemic = build_systemic_count(universe, m.ts)
    regime_p = RegimeParams(require=args.regime, min_systemic_symbols=args.min_systemic)

    results = run_comparison(
        m, entry_p=EntryParams(), exit_p=ExitParams(), regime_p=regime_p,
        cost=CostParams(), systemic_count=systemic,
    )

    fixed = results[ExitMode.FIXED_HOLD.value]
    trail = results[ExitMode.TRAILING_1491.value]
    # run_comparison scores both modes on one identical entry set, so the counts
    # must match; if they ever diverge the isolation is broken (R5).
    assert fixed.n_trades == trail.n_trades, "exit-alpha entry sets diverged"

    print("\n" + "=" * 78)
    print(f"C-2 × 1491-exit × regime[{args.regime}]   traded={args.traded}   "
          f"universe={len(universe)}   bars={len(m.ts)}")
    print("=" * 78)
    print("  " + fixed.summary())
    print("  " + trail.summary())
    print("-" * 78)
    exit_alpha = float(trail.mean_return) - float(fixed.mean_return)
    print(f"  EXIT ALPHA (R5): trailing − fixed = {exit_alpha:+.4%} per trade "
          f"(identical {fixed.n_trades}-entry set, exit rule only)")
    print(f"  Independent crisis episodes: {trail.n_independent_episodes} "
          f"(signal-validation §2.2 — a handful of events, not {trail.n_trades} IID samples)")
    if trail.n_independent_episodes < 8:
        print("  ⚠ episodes < 8 → significance likely UNREACHABLE; treat as "
              "'insufficient data', not proof either way (§2.2/§2.3).")
    print("  ⚠ Not validated alpha. trading §1.3 gate + user approval before any live use.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
