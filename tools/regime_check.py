#!/usr/bin/env python3
"""What did each market regime pay us?

From 2026-09-25 every entry stamps the NIFTY regime snapshot into entry_context (regime_trend,
regime_session_net_pct, regime_net_15m_pct, regime_net_5m_pct, ...). This reads those stamps off
the bots' own closed-trade records - no reconstruction, no look-ahead.

Two axes, because they answer different questions:
  LEVEL  - where the market IS (session/day net). "Is this a down day?"
  CHANGE - where it is GOING (last 15 min, last 5 min). "Is the regime flipping right now?"
           2026-09-25 is the case in point: BAD/NEUTRAL and drifting down until 13:30, GOOD by
           14:15, and the 14:00 hour alone made the day (+Rs66,469 after 10:00 vs -Rs8,460 before).

Usage:  python3 tools/regime_check.py                 # today
        python3 tools/regime_check.py 2026-09-25
        python3 tools/regime_check.py --since 2026-09-25
        python3 tools/regime_check.py --since 2026-09-11 --history   # pre-stamp days, joined
                                                                      # from market_regime_history
"""
import bisect
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
MIN_CELL = 50   # below this a cell is noise; printed but flagged


def minute(ts):
    return int(ts[11:13]) * 60 + int(ts[14:16])


def load_history():
    """Minute-indexed regime snapshots, for days before the stamp existed."""
    by_day = defaultdict(list)
    path = ROOT / "tools" / "market_regime_history.jsonl"
    if not path.exists():
        return by_day
    for line in open(path):
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("last_candle_ts") or ""
        if len(t) >= 16:
            by_day[t[:10]].append((minute(t), r))
    for d in by_day:
        by_day[d].sort(key=lambda x: x[0])
    return by_day


def load(d_from, d_to, use_history):
    hist = load_history() if use_history else {}
    rows = []
    for base, bot, side in BOTS:
        for r in json.load(open(ROOT / base / "data" / "option_pnl_history.json")):
            if not isinstance(r, dict):
                continue
            et = str(r.get("entry_time") or "")
            if not (d_from <= et[:10] <= d_to):
                continue
            ec = r.get("entry_context") or {}
            row = dict(day=et[:10], bot=bot, side=side, m=minute(et),
                       pnl=float(r.get("pnl") or 0),
                       typ=ec.get("entry_type") or "?",
                       trend=ec.get("regime_trend"),
                       sess=ec.get("regime_session_net_pct"),
                       n15=ec.get("regime_net_15m_pct"),
                       n5=ec.get("regime_net_5m_pct"))
            # "stamped" means the trade itself carried the regime, not that we joined it later.
            row["src"] = "stamp" if row["trend"] is not None else "none"
            if row["trend"] is None and hist.get(row["day"]):
                arr = hist[row["day"]]
                i = bisect.bisect_right([a for a, _ in arr], row["m"]) - 1
                if i >= 0:
                    s = arr[i][1]
                    row.update(trend=s.get("market_trend"), sess=s.get("session_net_pct"),
                               n15=s.get("net_move_pct"), n5=s.get("short_window_net_move_pct"),
                               src="history")
            rows.append(row)
    return rows


def line(label, rs):
    n = len(rs)
    if not n:
        return f"    {label:22} n=   0"
    t = sum(r["pnl"] for r in rs)
    w = sum(1 for r in rs if r["pnl"] > 0)
    flag = "  <- thin" if n < MIN_CELL else ""
    return f"    {label:22} n={n:>4}  win {100 * w / n:>3.0f}%  Rs{t:>10,.0f}  Rs/tr {t / n:>7,.0f}{flag}"


def bucket(rs, key, edges, fmt="{:+.2f}"):
    out = []
    for lo, hi in zip(edges, edges[1:]):
        g = [r for r in rs if r[key] is not None and lo <= float(r[key]) < hi]
        out.append((f"{fmt.format(lo)} to {fmt.format(hi)}%", g))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_history = "--history" in sys.argv
    if "--since" in sys.argv:
        d_from, d_to = args[0], date.today().isoformat()
    else:
        d_from = d_to = args[0] if args else date.today().isoformat()
    rows = load(d_from, d_to, use_history)
    span = d_from if d_from == d_to else f"{d_from} .. {d_to}"
    stamped = sum(1 for r in rows if r["src"] == "stamp")
    print(f"\n=== REGIME CHECK  {span}  ({len(rows)} trades, {stamped} carry the entry stamp) ===")
    if not rows:
        print("  no trades in range")
        return
    if stamped == 0 and not use_history:
        print("  none carry the stamp yet - the bots pick it up at the next restart."
              "  Re-run with --history to join the daemon log instead.")
        return

    for side in ("CE", "PE"):
        rs = [r for r in rows if r["side"] == side]
        if not rs:
            continue
        print(f"\n  {side} — LEVEL: where NIFTY was (session net at entry)")
        for label, g in bucket(rs, "sess", [-9, -0.4, -0.15, 0.15, 0.4, 9]):
            print(line(label, g))
        print(f"  {side} — CHANGE: NIFTY's last 15 min at entry (a flip in progress)")
        for label, g in bucket(rs, "n15", [-9, -0.15, -0.05, 0.05, 0.15, 9]):
            print(line(label, g))
        print(f"  {side} — CHANGE: NIFTY's last 5 min at entry (the fastest read)")
        for label, g in bucket(rs, "n5", [-9, -0.1, -0.03, 0.03, 0.1, 9]):
            print(line(label, g))
        print(f"  {side} — daemon label")
        for lab in ("GOOD", "NEUTRAL", "BAD"):
            print(line(lab, [r for r in rs if r["trend"] == lab]))

    print("\n  per day, split by whether NIFTY was rising or falling at entry:")
    for d in sorted({r["day"] for r in rows}):
        g = [r for r in rows if r["day"] == d]
        up = [r for r in g if r["n15"] is not None and float(r["n15"]) > 0.05]
        dn = [r for r in g if r["n15"] is not None and float(r["n15"]) < -0.05]
        fl = [r for r in g if r["n15"] is not None and -0.05 <= float(r["n15"]) <= 0.05]
        print(f"    {d}  rising Rs{sum(r['pnl'] for r in up):>9,.0f} ({len(up):>3})   "
              f"flat Rs{sum(r['pnl'] for r in fl):>9,.0f} ({len(fl):>3})   "
              f"falling Rs{sum(r['pnl'] for r in dn):>9,.0f} ({len(dn):>3})")
    print()


if __name__ == "__main__":
    main()
