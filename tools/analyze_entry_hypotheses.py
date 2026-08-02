"""
Entry-hypothesis tracker — tools/analyze_entry_hypotheses.py

Tests the two candidate entry refinements from the 2026-07-13 session analysis on every trade,
per day and pooled, so a full week of evidence can be reviewed before changing the Pine:

  H1 (continuation time-box): TREND_CONTINUATION entries are only profitable in the opening
      drive (09:30-09:44); they bleed after ~10:15.
  H2 (time-conditional VWAP-vs-PDC gate): from 09:45 onward, entries where the session VWAP is
      still BELOW the previous-day close lose; before 09:45 that same condition is FINE (VWAP
      hasn't had time to climb — early below-PDC entries are the strong opening reversals).

Data sources (no bot interference, read-only):
  {CE,PUT}_OPTIONS/{OTM,ITM}/logs/<date>/alerts.jsonl   — entry telemetry (rsi/adx/macd/vwap/pdc)
  {CE,PUT}_OPTIONS/{OTM,ITM}/data/option_pnl_history.json — closed trades with net pnl (charges-adj)

Usage:
  python3 tools/analyze_entry_hypotheses.py                 # current week (Mon..today)
  python3 tools/analyze_entry_hypotheses.py 2026-07-13      # single day
  python3 tools/analyze_entry_hypotheses.py 2026-07-13 2026-07-17   # explicit range
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

TRADING_DIR = Path(__file__).parent.parent
BOTS = [
    ("CE-OTM", TRADING_DIR / "CE_OPTIONS" / "OTM"),
    ("CE-ITM", TRADING_DIR / "CE_OPTIONS" / "ITM"),
    ("PE-OTM", TRADING_DIR / "PUT_OPTIONS" / "OTM"),
    ("PE-ITM", TRADING_DIR / "PUT_OPTIONS" / "ITM"),
]


def date_range():
    args = sys.argv[1:]
    if len(args) == 2:
        d0, d1 = date.fromisoformat(args[0]), date.fromisoformat(args[1])
    elif len(args) == 1:
        d0 = d1 = date.fromisoformat(args[0])
    else:
        today = date.today()
        d0, d1 = today - timedelta(days=today.weekday()), today  # Monday..today
    days = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def load_alerts(bot_dir: Path, day: str):
    """Received alerts for one day keyed by (underlying) -> chronological list."""
    out = defaultdict(list)
    f = bot_dir / "logs" / day / "alerts.jsonl"
    if not f.exists():
        return out
    for line in open(f, errors="ignore"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("status") != "received":
            continue
        a = o.get("alert", {})
        sym = a.get("symbol")
        if not sym:
            continue
        try:
            out[sym].append({
                "ts": o.get("timestamp", ""),
                "entry_type": a.get("entry_type", "?"),
                "price": float(a.get("price", 0) or 0),
                "vwap": float(a.get("vwap_value", 0) or 0),
                "vwap_upper": float(a.get("vwap_upper", 0) or 0) if a.get("vwap_upper") else None,
                "pdc": float(a.get("prev_day_close", 0) or 0),
                "rsi": float(a.get("rsi_value", 0) or 0),
                "adx": float(a.get("adx_value", 0) or 0) if a.get("adx_value") else None,
            })
        except (TypeError, ValueError):
            continue
    return out


def load_trades(bot_dir: Path, days):
    f = bot_dir / "data" / "option_pnl_history.json"
    if not f.exists():
        return []
    trades = json.load(open(f))
    dayset = set(days)
    return [t for t in trades
            if (t.get("closed_at", "") or t.get("exit_time", ""))[:10] in dayset]


def join(days):
    rows = []
    for bot, bot_dir in BOTS:
        alerts_by_day = {d: load_alerts(bot_dir, d) for d in days}
        for t in load_trades(bot_dir, days):
            entry_time = t.get("entry_time", "")
            day = entry_time[:10]
            und = t.get("underlying", "")
            cands = alerts_by_day.get(day, {}).get(und, [])
            best = None
            for a in cands:
                if a["ts"] <= entry_time:
                    if best is None or a["ts"] > best["ts"]:
                        best = a
            entry_prem = t.get("entry_premium", 0)
            highest = t.get("highest_premium", 0)
            row = {
                "bot": bot, "day": day, "underlying": und,
                "hhmm": entry_time[11:16],
                "pnl": t.get("pnl", 0),
                "is_pe": bot.startswith("PE"),
                "peak_pct": ((highest - entry_prem) / entry_prem * 100) if entry_prem else 0.0,
            }
            if best:
                row["entry_type"] = best["entry_type"]
                if best["pdc"] > 0:
                    row["vpdc"] = (best["vwap"] - best["pdc"]) / best["pdc"] * 100
                    # H3: price-vs-PDC margin at the exact entry bar. Small margin (price just
                    # crossed PDC) + already-elevated ADX = PDC was almost certainly the LAST gate
                    # to clear on this entry, meaning the move was already mature when it fired.
                    row["pdc_margin_pct"] = (best["price"] - best["pdc"]) / best["pdc"] * 100
                row["adx_at_entry"] = best["adx"]
                row["rsi_at_entry"] = best["rsi"]
            else:
                row["entry_type"] = "UNMATCHED"
            rows.append(row)
    return rows


def tbucket(hhmm):
    if hhmm < "09:45":
        return "09:30-09:44"
    if hhmm < "10:15":
        return "09:45-10:14"
    if hhmm < "11:05":
        return "10:15-11:00"
    return "13:30-14:30"


def table(title, groups):
    print(f"\n--- {title} ---")
    print(f"{'group':34} {'n':>4} {'WR%':>5} {'netPnL':>9} {'avg':>7}")
    for label, rr in groups:
        if not rr:
            continue
        w = sum(1 for r in rr if r["pnl"] > 0)
        pnl = sum(r["pnl"] for r in rr)
        print(f"{label:34} {len(rr):>4} {100*w/len(rr):>5.0f} {pnl:>9.0f} {pnl/len(rr):>7.0f}")


def report(rows, label):
    rows = [r for r in rows if r["entry_type"] != "UNMATCHED"]
    if not rows:
        print(f"\n===== {label}: no matched trades =====")
        return
    print(f"\n{'='*72}\n===== {label}  (n={len(rows)}, net ₹{sum(r['pnl'] for r in rows):.0f}) =====\n{'='*72}")

    ce = [r for r in rows if not r["is_pe"]]
    pe = [r for r in rows if r["is_pe"]]
    if pe:
        table("CE vs PE", [("CE", ce), ("PE", pe)])

    # H1: entry_type x time (CE)
    g = defaultdict(list)
    for r in ce:
        g[(r["entry_type"], tbucket(r["hhmm"]))].append(r)
    table("H1: entry_type x time (CE)", [(f"{et} @ {b}", rr) for (et, b), rr in sorted(g.items())])

    # H1 verdict cells
    cont = [r for r in ce if r["entry_type"] == "TREND_CONTINUATION"]
    early = [r for r in cont if r["hhmm"] < "09:45"]
    late = [r for r in cont if r["hhmm"] >= "10:15"]
    table("H1 verdict: continuation early vs late",
          [("cont 09:30-09:44 (keep?)", early), ("cont 10:15+ (cut?)", late)])

    # H2: vwap-vs-pdc x time (CE; PE would need inverted sign — reported separately)
    have = [r for r in ce if "vpdc" in r]
    g2 = defaultdict(list)
    for r in have:
        side = "above" if r["vpdc"] >= 0 else "below"
        period = "early(<09:45)" if r["hhmm"] < "09:45" else "later(>=09:45)"
        g2[(period, side)].append(r)
    table("H2: VWAP-vs-PDC x time (CE)", [(f"{p} vwap {s} PDC", rr) for (p, s), rr in sorted(g2.items())])

    # H2 verdict: the proposed gate = drop later+below
    gate_drop = g2.get(("later(>=09:45)", "below"), [])
    kept = [r for r in have if r not in gate_drop]
    table("H2 verdict: proposed gate effect",
          [("dropped by gate (later+below)", gate_drop), ("kept by gate", kept)])

    if pe:
        # PE mirror: for puts, VWAP BELOW pdc = symbol weak = good for PE
        haveP = [r for r in pe if "vpdc" in r]
        gP = defaultdict(list)
        for r in haveP:
            side = "below(good for PE?)" if r["vpdc"] < 0 else "above(bad for PE?)"
            gP[side].append(r)
        table("H2-PE mirror: VWAP-vs-PDC (sign inverted)", sorted(gP.items()))

    # H3: PDC-margin-at-entry as an ADX-maturity proxy (TREND_CONTINUATION only — pattern 1
    # already requires a fresh cross, so this doesn't apply the same way there). Hypothesis
    # (07-13, n=71): when price has JUST crossed PDC (small margin) at the exact entry bar, ADX
    # is usually already elevated -> PDC was the LAST gate to clear -> move is already mature.
    cont3 = [r for r in cont if "pdc_margin_pct" in r]
    if cont3:
        close_pdc = [r for r in cont3 if abs(r["pdc_margin_pct"]) < 0.15]
        far_pdc = [r for r in cont3 if abs(r["pdc_margin_pct"]) >= 0.15]
        table("H3 verdict: continuation PDC-margin at entry (maturity proxy)",
              [("just-crossed PDC (<0.15%, likely late/mature)", close_pdc),
               ("comfortably above PDC (>=0.15%, likely fresher)", far_pdc)])
        for label, rr in [("just-crossed", close_pdc), ("comfortable", far_pdc)]:
            adxs = [r["adx_at_entry"] for r in rr if r.get("adx_at_entry")]
            peaks = [r["peak_pct"] for r in rr]
            if rr:
                print(f"    {label:14} avgADXatEntry={sum(adxs)/len(adxs):.1f}" if adxs else f"    {label:14} avgADXatEntry=n/a",
                      f" avgPeak%={sum(peaks)/len(peaks):.1f}")


def main():
    days = date_range()
    print(f"Days analyzed: {', '.join(days)}")
    all_rows = join(days)
    for d in days:
        day_rows = [r for r in all_rows if r["day"] == d]
        if day_rows:
            report(day_rows, f"DAY {d}")
    if len(days) > 1:
        report(all_rows, f"POOLED {days[0]}..{days[-1]}")


if __name__ == "__main__":
    main()
