"""PnL report generator.

Reads two sources:
  1. paper_state.json        → current balance + open positions
  2. logs/btcbot.log         → trade history (structured JSON log lines)

Usage:
  python scripts/pnl.py
  python scripts/pnl.py --state paper_state.json --log logs/btcbot.log --days 7
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_DEFAULT_STATE = Path("paper_state.json")
_DEFAULT_LOG   = Path("logs/btcbot.log")
_INITIAL_BALANCE = Decimal("10000")

# structlog event names we care about
_EV_OPENED = "order_manager.position_opened"
_EV_CLOSED = "paper.position_closed"
_EV_CB     = "order_manager.circuit_breaker"


def _parse_log(log_path: Path, since: datetime) -> list[dict]:
    """Return all structured JSON log lines emitted after `since`."""
    records = []
    if not log_path.exists():
        return records
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = rec.get("timestamp") or rec.get("ts")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if ts >= since:
                rec["_ts"] = ts
                records.append(rec)
    return records


def _match_trades(records: list[dict]) -> list[dict]:
    """Pair open/close events into completed trade dicts."""
    open_map: dict[str, dict] = {}
    trades: list[dict] = []

    for rec in records:
        ev = rec.get("event")
        if ev == _EV_OPENED:
            pid = rec.get("position_id", "")
            open_map[pid] = {
                "position_id": pid,
                "side": rec.get("side", "?"),
                "qty": rec.get("qty", "0"),
                "entry": rec.get("entry", "0"),
                "sl": rec.get("sl", "0"),
                "opened_at": rec["_ts"],
            }
        elif ev == _EV_CLOSED:
            pid = rec.get("position_id", "")
            entry = open_map.pop(pid, None)
            pnl_raw = rec.get("pnl", "0")
            trade = {
                "position_id": pid,
                "side": entry["side"] if entry else "?",
                "qty": entry["qty"] if entry else "?",
                "entry": entry["entry"] if entry else "?",
                "sl": entry["sl"] if entry else "?",
                "opened_at": entry["opened_at"] if entry else rec["_ts"],
                "closed_at": rec["_ts"],
                "pnl": Decimal(str(pnl_raw)),
            }
            trades.append(trade)

    return trades


def _load_state(state_path: Path) -> tuple[Decimal, list[dict]]:
    if not state_path.exists():
        return _INITIAL_BALANCE, []
    data = json.loads(state_path.read_text())
    balance = Decimal(data.get("balance", str(_INITIAL_BALANCE)))
    positions = []
    for pid, p in data.get("positions", {}).items():
        positions.append({"position_id": pid, **p})
    return balance, positions


def _bar(pnl: Decimal, width: int = 20) -> str:
    if pnl == 0:
        return " " * width
    max_val = Decimal("200")
    filled = int(abs(pnl) / max_val * width)
    filled = min(filled, width)
    char = "█" if pnl > 0 else "░"
    return char * filled


def _fmt_pnl(pnl: Decimal) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}${pnl:,.2f}"


def main(state_path: Path, log_path: Path, days: int, initial: Decimal) -> None:
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)

    balance, open_positions = _load_state(state_path)
    records = _parse_log(log_path, since=since)
    trades = _match_trades(records)

    total_pnl = balance - initial
    total_pct  = (total_pnl / initial) * 100

    today_start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_trades = [t for t in trades if t["closed_at"] >= today_start]
    today_pnl = sum(t["pnl"] for t in today_trades) if today_trades else Decimal("0")
    today_pct  = (today_pnl / initial) * 100

    period_pnl = sum(t["pnl"] for t in trades) if trades else Decimal("0")
    period_pct  = (period_pnl / initial) * 100

    longs  = sum(1 for t in trades if t["side"] == "LONG")
    shorts = sum(1 for t in trades if t["side"] == "SHORT")
    wins   = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] <= 0)
    win_rate = (wins / len(trades) * 100) if trades else 0.0

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║            BTC Bot  PnL Report                   ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  초기 자본:   ${initial:>12,.2f}                      ║")
    print(f"║  현재 잔고:   ${balance:>12,.2f}                      ║")
    pnl_str = f"{'+' if total_pnl >= 0 else ''}${total_pnl:,.2f} ({'+' if total_pnl >= 0 else ''}{total_pct:.2f}%)"
    print(f"║  누적 PnL:    {pnl_str:<35} ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  오늘 PnL:    {f'{chr(43) if today_pnl >= 0 else chr(45)}${abs(today_pnl):,.2f} ({chr(43) if today_pnl >= 0 else chr(45)}{abs(today_pct):.2f}%)':<35} ║")
    print(f"║  {days}일 PnL:   {f'{chr(43) if period_pnl >= 0 else chr(45)}${abs(period_pnl):,.2f} ({chr(43) if period_pnl >= 0 else chr(45)}{abs(period_pct):.2f}%)':<35} ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  총 거래:     {len(trades)}건  (LONG {longs} / SHORT {shorts}){' ' * max(0, 14 - len(str(len(trades))))} ║")
    print(f"║  승률:        {win_rate:.1f}%  (이익 {wins} / 손실 {losses}){' ' * max(0, 14 - len(str(wins)))} ║")
    print("╠══════════════════════════════════════════════════╣")

    if open_positions:
        print(f"║  미결 포지션: {len(open_positions)}건                              ║")
        for p in open_positions:
            side = p.get("side", "?")
            entry = p.get("entry_price", "?")
            sl = p.get("sl_price", "?")
            print(f"║    [{side}] 진입 ${entry}  SL ${sl}{'':>5} ║")
        print("╠══════════════════════════════════════════════════╣")

    if trades:
        print(f"║  최근 {min(10, len(trades))}건 거래                                   ║")
        print("║  ─────────────────────────────────────────────── ║")
        for t in trades[-10:]:
            ts_str = t["closed_at"].strftime("%m/%d %H:%M")
            side_ch = "▲" if t["side"] == "LONG" else "▼"
            pnl = t["pnl"]
            bar = _bar(pnl, width=12)
            pnl_str = f"{'+' if pnl >= 0 else ''}${pnl:,.2f}"
            print(f"║  {ts_str} {side_ch} {t['entry']:>10}  {pnl_str:>9}  {bar:<12} ║")
    else:
        print("║  (로그에서 체결된 거래를 찾지 못했습니다)        ║")

    print("╚══════════════════════════════════════════════════╝")
    print()

    if not log_path.exists():
        print(f"  ⚠  로그 파일 없음: {log_path}")
        print(f"     btcbot 실행 후 거래 내역이 쌓이면 표시됩니다.")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BTC Bot PnL Report")
    parser.add_argument("--state",   default=str(_DEFAULT_STATE), help="paper_state.json 경로")
    parser.add_argument("--log",     default=str(_DEFAULT_LOG),   help="btcbot.log 경로")
    parser.add_argument("--days",    type=int, default=30,         help="조회 기간 (일, 기본 30)")
    parser.add_argument("--initial", type=str, default="10000",    help="초기 자본 (기본 10000)")
    args = parser.parse_args()

    main(
        state_path=Path(args.state),
        log_path=Path(args.log),
        days=args.days,
        initial=Decimal(args.initial),
    )
