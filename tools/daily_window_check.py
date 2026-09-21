#!/usr/bin/env python3
"""Daily follow-up for the 2026-09-21 morning-window findings.

Reads each bot's own closed-trade record (option_pnl_history.json) - no simulation, no candle
reconstruction - and reports the four things being tracked:

  1. The 09:15-10:00 window per bot, and whether the day stayed inside it or leaked after it.
  2. CE RSI_BURN entered 09:40-09:59 - the confirmed dead cell. Is it still losing?
  3. AO_MOMCONT - being re-judged now that the 2026-09-18 rate-limit/monitor fixes are live.
  4. Exit slippage by exit type - does the stop-vs-timer gap persist after those fixes?

Usage:  python3 tools/daily_window_check.py              # today
        python3 tools/daily_window_check.py 2026-09-22   # a given day
        python3 tools/daily_window_check.py --since 2026-09-21   # running tally
"""
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = [
    ("CE_OPTIONS/ITM", "CE-ITM", "CE"),
    ("CE_OPTIONS/OTM", "CE-OTM", "CE"),
    ("PUT_OPTIONS/ITM", "PE-ITM", "PE"),
    ("PUT_OPTIONS/OTM", "PE-OTM", "PE"),
]
WIN_START, WIN_END = 9 * 60 + 15, 10 * 60      # 09:15 <= entry < 10:00
DEAD_FROM = 9 * 60 + 40                        # CE RSI_BURN from 09:40 is the dead cell
# Fixed on 2026-09-18 (rate-limit labels) and 2026-09-21 (deadline sleep). AO_MOMCONT is
# compared before/after this date because its weakness may have been monitor starvation.
FIX_DATE = "2026-09-21"


def minute(ts):
    return int(ts[11:13]) * 60 + int(ts[14:16])


def load(day_from, day_to):
    rows = []
    for base, bot, side in BOTS:
        path = ROOT / base / "data" / "option_pnl_history.json"
        try:
            data = json.load(open(path))
        except Exception as exc:
            print(f"  ! cannot read {path}: {exc}")
            continue
        for r in data:
            if not isinstance(r, dict):
                continue
            et = str(r.get("entry_time") or "")
            if not (day_from <= et[:10] <= day_to):
                continue
            ec = r.get("entry_context") or {}
            slip = (ec.get("exit_slippage") or r.get("exit_slippage") or {}).get("slippage_pct")
            rows.append(dict(
                day=et[:10], m=minute(et), bot=bot, side=side,
                typ=ec.get("entry_type") or "?",
                rsn=str(r.get("exit_reason") or "").split("(")[0].strip(),
                pnl=float(r.get("pnl") or 0.0),
                slip=float(slip) if slip is not None else None,
            ))
    return rows


def line(label, rs):
    if not rs:
        return f"  {label:34} n=   0"
    n = len(rs)
    tot = sum(r["pnl"] for r in rs)
    w = sum(1 for r in rs if r["pnl"] > 0)
    return f"  {label:34} n={n:>4}  win {w / n * 100:>3.0f}%  Rs{tot:>10.0f}  Rs/tr {tot / n:>6.0f}"


def main():
    args = sys.argv[1:]
    if args and args[0] == "--since":
        d_from, d_to = args[1], date.today().isoformat()
    else:
        d_from = d_to = args[0] if args else date.today().isoformat()
    rows = load(d_from, d_to)
    span = d_from if d_from == d_to else f"{d_from} .. {d_to}"
    print(f"\n=== DAILY WINDOW CHECK  {span}  ({len(rows)} closed trades) ===")
    if not rows:
        print("  no closed trades in range")
        return

    win = [r for r in rows if WIN_START <= r["m"] < WIN_END]
    after = [r for r in rows if r["m"] >= WIN_END]
    print("\n1. THE WINDOW - 09:15 to 10:00 vs everything after")
    print(line("inside 09:15-10:00", win))
    print(line("after 10:00", after))
    print(line("whole day", rows))
    print("   per bot, inside the window:")
    for _, bot, _ in BOTS:
        print("  " + line(bot, [r for r in win if r["bot"] == bot]))
    if d_from != d_to:
        per = defaultdict(float)
        for r in win:
            per[r["day"]] += r["pnl"]
        pos = sum(1 for v in per.values() if v > 0)
        print(f"   window positive on {pos}/{len(per)} days   worst day Rs{min(per.values()):.0f}")

    dead = [r for r in rows if r["side"] == "CE" and r["typ"] == "RSI_BURN"
            and DEAD_FROM <= r["m"] < WIN_END]
    print("\n2. DEAD CELL - CE RSI_BURN entered 09:40-09:59  (baseline -Rs573/tr, 30% win)")
    print(line("CE RSI_BURN 09:40-59", dead))

    mom = [r for r in rows if r["typ"] == "AO_MOMCONT"]
    print(f"\n3. AO_MOMCONT  (baseline Rs48/tr in window; re-judging from {FIX_DATE})")
    print(line("AO_MOMCONT all day", mom))
    print(line("AO_MOMCONT inside window", [r for r in mom if WIN_START <= r["m"] < WIN_END]))

    print("\n4. EXIT SLIPPAGE by exit type  (baseline: TRIAL -0.85%, HARD -0.67%, timer -0.30%)")
    by = defaultdict(list)
    for r in rows:
        if r["slip"] is not None and -40 < r["slip"] < 10:
            by[r["rsn"]].append(r["slip"])
    for k, v in sorted(by.items(), key=lambda kv: sorted(kv[1])[len(kv[1]) // 2]):
        v = sorted(v)
        print(f"  {k:24} n={len(v):>4}  median {v[len(v) // 2]:>+6.2f}%   worst {v[0]:>+7.2f}%")
    print()


if __name__ == "__main__":
    main()
