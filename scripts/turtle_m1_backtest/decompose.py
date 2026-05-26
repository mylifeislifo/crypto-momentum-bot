"""Time + structure decomposition of M1 trades."""
from __future__ import annotations
import json
from decimal import Decimal
from pathlib import Path
from collections import defaultdict


def main():
    path = Path("/home/claude/turtle_redo/results/trades.jsonl")
    trades = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    print(f"Total trades: {len(trades)}\n")

    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)

    for sym, ts in by_sym.items():
        wins = [Decimal(t["pnl"]) for t in ts if Decimal(t["pnl"]) > 0]
        losses = [Decimal(t["pnl"]) for t in ts if Decimal(t["pnl"]) <= 0]
        avg_win = sum(wins) / len(wins) if wins else Decimal("0")
        avg_loss = sum(losses) / len(losses) if losses else Decimal("0")
        max_win = max(wins) if wins else Decimal("0")
        max_loss = min(losses) if losses else Decimal("0")
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else None
        print(f"=== {sym} ({len(ts)} trades) ===")
        print(f"  wins: {len(wins)} / losses: {len(losses)}  win_rate: {len(wins)/len(ts)*100:.1f}%")
        print(f"  avg_win:  ${float(avg_win):,.2f}")
        print(f"  avg_loss: ${float(avg_loss):,.2f}")
        print(f"  reward/risk ratio (|avg_win|/|avg_loss|): {float(rr):.2f}" if rr else "  rr: n/a")
        print(f"  max_win:  ${float(max_win):,.2f}")
        print(f"  max_loss: ${float(max_loss):,.2f}")
        print()

    yearly = defaultdict(lambda: defaultdict(lambda: {"pnl": Decimal("0"), "n": 0, "w": 0}))
    for t in trades:
        year = t["ts_exit"][:4]
        sym = t["symbol"]
        yearly[year][sym]["pnl"] += Decimal(t["pnl"])
        yearly[year][sym]["n"] += 1
        if Decimal(t["pnl"]) > 0:
            yearly[year][sym]["w"] += 1

    print("=== Year × Symbol PnL ===")
    print(f"{'year':6} {'BTC pnl':>14} {'BTC n':>6} {'BTC wr':>7}   {'ETH pnl':>14} {'ETH n':>6} {'ETH wr':>7}   {'TOTAL':>14}")
    cumulative = Decimal("0")
    for year in sorted(yearly.keys()):
        b = yearly[year]["BTCUSDT"]
        e = yearly[year]["ETHUSDT"]
        total = b["pnl"] + e["pnl"]
        cumulative += total
        b_wr = f"{b['w']/b['n']*100:.0f}%" if b['n'] else "-"
        e_wr = f"{e['w']/e['n']*100:.0f}%" if e['n'] else "-"
        print(f"{year:6} {float(b['pnl']):14,.0f} {b['n']:6} {b_wr:>7}   {float(e['pnl']):14,.0f} {e['n']:6} {e_wr:>7}   {float(total):14,.0f}  cum={float(cumulative):,.0f}")

    print("\n=== Sensitivity: exclude one year at a time ===")
    total_pnl = sum(Decimal(t["pnl"]) for t in trades)
    print(f"All-in total pnl: ${float(total_pnl):,.2f}  ({float(total_pnl/Decimal('10000')*100):.1f}%)")
    for year in sorted(yearly.keys()):
        year_total = yearly[year]["BTCUSDT"]["pnl"] + yearly[year]["ETHUSDT"]["pnl"]
        without = total_pnl - year_total
        print(f"  ex-{year}: ${float(without):,.2f} ({float(without/Decimal('10000')*100):.1f}%)  [drops ${float(year_total):,.0f}]")

    print("\n=== Worst losing streak (consecutive losses by exit time) ===")
    for sym, ts in by_sym.items():
        ts_sorted = sorted(ts, key=lambda x: x["ts_exit"])
        max_streak = 0
        cur = 0
        for t in ts_sorted:
            if Decimal(t["pnl"]) <= 0:
                cur += 1
                max_streak = max(max_streak, cur)
            else:
                cur = 0
        print(f"  {sym}: max consecutive losses = {max_streak}")

    print("\n=== Exit reasons ===")
    for sym, ts in by_sym.items():
        stops = sum(1 for t in ts if t["exit_reason"] == "stop")
        signals = sum(1 for t in ts if t["exit_reason"] == "signal")
        stop_pnl = sum(Decimal(t["pnl"]) for t in ts if t["exit_reason"] == "stop")
        sig_pnl = sum(Decimal(t["pnl"]) for t in ts if t["exit_reason"] == "signal")
        print(f"  {sym}: stop={stops} (pnl ${float(stop_pnl):,.0f})  signal={signals} (pnl ${float(sig_pnl):,.0f})")

    print("\n=== Long vs Short ===")
    for sym, ts in by_sym.items():
        longs = [t for t in ts if t["side"] == "long"]
        shorts = [t for t in ts if t["side"] == "short"]
        long_pnl = sum(Decimal(t["pnl"]) for t in longs)
        short_pnl = sum(Decimal(t["pnl"]) for t in shorts)
        long_wins = sum(1 for t in longs if Decimal(t["pnl"]) > 0)
        short_wins = sum(1 for t in shorts if Decimal(t["pnl"]) > 0)
        print(f"  {sym} long:  {len(longs):3} trades, ${float(long_pnl):>10,.0f}  wr={long_wins/len(longs)*100:.0f}%" if longs else f"  {sym} long: 0")
        print(f"  {sym} short: {len(shorts):3} trades, ${float(short_pnl):>10,.0f}  wr={short_wins/len(shorts)*100:.0f}%" if shorts else f"  {sym} short: 0")


if __name__ == "__main__":
    main()
