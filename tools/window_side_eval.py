#!/usr/bin/env python3
"""
Window x side scorecard — recomputes the exact cells marked as the 2026-08-19 baseline
so later sessions are a like-for-like comparison, not a fresh analysis.

Everything is measured AFTER the two rules that are live/planned, so the numbers mean
the same thing every day:
  R1  no re-entry on an underlying that already closed at a loss that day
  R2  AO_MOMCONT excluded

Usage:
  python3 tools/window_side_eval.py                    # today
  python3 tools/window_side_eval.py 2026-08-20
  python3 tools/window_side_eval.py --since 2026-08-17
  python3 tools/window_side_eval.py --report           # accumulated record vs baseline
"""
import json, sys, datetime, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = [("CE_OPTIONS", "ITM", "CE"), ("CE_OPTIONS", "OTM", "CE"),
        ("PUT_OPTIONS", "ITM", "PE"), ("PUT_OPTIONS", "OTM", "PE")]
LOG = ROOT / "tools" / "window_side_log.jsonl"
WINDOWS = [("09:15-10:15", "09:15", "10:15"), ("10:15-11:00", "10:15", "11:00"),
           ("11:00-12:00", "11:00", "12:00"), ("12:00-13:00", "12:00", "13:00"),
           ("13:00-15:30", "13:00", "15:30")]

# Baseline marked 2026-08-19 from 08-17..08-19 (after R1+R2). net_per_trade and the
# per-day sign pattern are what we are testing for repeatability.
BASELINE = {
    ("CE", "09:15-10:15"): (-187, "--+", 106), ("PE", "09:15-10:15"): (+380, "-++", 24),
    ("CE", "10:15-11:00"): (+26,  "+-+", 50),  ("PE", "10:15-11:00"): (+78,  "--+", 41),
    ("CE", "11:00-12:00"): (+299, "+++", 66),  ("PE", "11:00-12:00"): (+28,  "-+-", 70),
    ("CE", "12:00-13:00"): (+300, "-++", 44),  ("PE", "12:00-13:00"): (-66,  "---", 89),
    ("CE", "13:00-15:30"): (-160, "+--", 63),  ("PE", "13:00-15:30"): (-49,  "++-", 84),
}
CLAIMS = [
    ("CE 11:00-12:00 stays POSITIVE",      lambda c: c.get(("CE","11:00-12:00"),{}).get("net",0) > 0),
    ("PE 12:00-13:00 stays NEGATIVE",      lambda c: c.get(("PE","12:00-13:00"),{}).get("net",0) < 0),
    ("CE 12:00-13:00 stays POSITIVE",      lambda c: c.get(("CE","12:00-13:00"),{}).get("net",0) > 0),
    ("PE 09:15-10:15 stays POSITIVE",      lambda c: c.get(("PE","09:15-10:15"),{}).get("net",0) > 0),
    ("CE 13:00-15:30 stays NEGATIVE",      lambda c: c.get(("CE","13:00-15:30"),{}).get("net",0) < 0),
    ("PE 13:00-15:30 stays NEGATIVE",      lambda c: c.get(("PE","13:00-15:30"),{}).get("net",0) < 0),
    ("CE beats PE in 11:00-13:00",         lambda c: sum(c.get(("CE",w),{}).get("net",0) for w in ("11:00-12:00","12:00-13:00"))
                                                   > sum(c.get(("PE",w),{}).get("net",0) for w in ("11:00-12:00","12:00-13:00"))),
    ("CE win% > PE win% overall",          lambda c: c.get(("CE","ALL"),{}).get("win",0) > c.get(("PE","ALL"),{}).get("win",0)),
]


def parse(s):
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def collect(day):
    rows = []
    for family, mode, side in BOTS:
        hist = ROOT / family / mode / "data" / "option_pnl_history.json"
        if not hist.exists():
            continue
        for r in json.load(open(hist)):
            et = r.get("entry_time") or ""
            if et[:10] != day or r.get("gross_pnl") is None:
                continue
            rows.append({
                "side": side, "t": et[11:16], "e": parse(et), "c": parse(r.get("closed_at") or ""),
                "u": r.get("underlying"), "n": r.get("pnl") or 0.0,
                "g": r.get("gross_pnl") or 0.0, "ch": r.get("charges") or 0.0,
                "ety": (r.get("entry_context") or {}).get("entry_type") or "NONE",
            })
    # R1: drop any entry made after that underlying already closed a loss today
    for x in rows:
        x["prior"] = sum(1 for y in rows
                         if y["u"] == x["u"] and y["c"] and x["e"] and y["c"] <= x["e"] and y["n"] < 0)
    return rows, [x for x in rows if x["prior"] == 0 and x["ety"] != "AO_MOMCONT"]


def cells(filtered):
    out = {}
    for side in ("CE", "PE"):
        for label, a, b in WINDOWS:
            v = [x for x in filtered if x["side"] == side and a <= x["t"] < b]
            if v:
                out[(side, label)] = {
                    "n": len(v), "net": round(sum(x["n"] for x in v), 2),
                    "net_per_trade": round(sum(x["n"] for x in v) / len(v), 2),
                    "win": round(sum(1 for x in v if x["n"] > 0) / len(v) * 100, 1),
                    "gross": round(sum(x["g"] for x in v), 2),
                    "charges": round(sum(x["ch"] for x in v), 2),
                }
        v = [x for x in filtered if x["side"] == side]
        if v:
            out[(side, "ALL")] = {
                "n": len(v), "net": round(sum(x["n"] for x in v), 2),
                "net_per_trade": round(sum(x["n"] for x in v) / len(v), 2),
                "win": round(sum(1 for x in v if x["n"] > 0) / len(v) * 100, 1),
                "gross": round(sum(x["g"] for x in v), 2),
                "charges": round(sum(x["ch"] for x in v), 2),
            }
    return out


def evaluate(day, quiet=False):
    raw, filt = collect(day)
    if not raw:
        print(f"\n{day}: no trades")
        return None
    c = cells(filt)
    if not quiet:
        print(f"\n{'='*104}\n{day}   raw {len(raw)} trades Rs {sum(x['n'] for x in raw):+,.0f}   "
              f"after R1+R2 {len(filt)} trades Rs {sum(x['n'] for x in filt):+,.0f}\n{'='*104}")
        print(f"  {'window':<14}{'CE n':>7}{'CE win':>8}{'CE/tr':>9}{'CE net':>11}{'  vs base':>11}"
              f"  |{'PE n':>7}{'PE win':>8}{'PE/tr':>9}{'PE net':>11}{'  vs base':>11}")
        for label, a, b in WINDOWS:
            line = f"  {label:<14}"
            for side in ("CE", "PE"):
                d = c.get((side, label))
                if d:
                    bp = BASELINE.get((side, label), (None, "", 0))[0]
                    tag = "" if bp is None else ("same dir" if (d["net_per_trade"] > 0) == (bp > 0) else "FLIPPED")
                    line += f"{d['n']:>7}{d['win']:>7.0f}%{d['net_per_trade']:>+9,.0f}{d['net']:>+11,.0f}{tag:>11}"
                else:
                    line += f"{'-':>7}{'-':>8}{'-':>9}{'-':>11}{'-':>11}"
                if side == "CE":
                    line += "  |"
            print(line)
        print(f"\n  CLAIMS MARKED ON 2026-08-19 — did each hold on {day}?")
        for name, fn in CLAIMS:
            try:
                ok = fn(c)
            except Exception:
                ok = None
            print(f"    [{'HELD' if ok else 'FAILED' if ok is False else ' ?  '}]  {name}")
    return {"day": day, "n_raw": len(raw), "n_filtered": len(filt),
            "net_raw": round(sum(x["n"] for x in raw), 2),
            "net_filtered": round(sum(x["n"] for x in filt), 2),
            "cells": {f"{s}|{w}": v for (s, w), v in c.items()},
            "claims": {name: bool(fn(c)) for name, fn in CLAIMS},
            "evaluated_at": datetime.datetime.now().isoformat()}


def report():
    if not LOG.exists():
        print("no log yet")
        return
    rows = {}
    for line in LOG.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["day"]] = r          # newest wins
    days = sorted(rows)
    print(f"\nWINDOW x SIDE RECORD — {len(days)} session(s): {', '.join(days)}\n")
    print(f"  {'cell':<20}{'baseline/tr':>12}" + "".join(f"{d[5:]:>12}" for d in days) + f"{'verdict':>12}")
    for side in ("CE", "PE"):
        for label, a, b in WINDOWS:
            bp = BASELINE.get((side, label), (None,))[0]
            vals = [rows[d]["cells"].get(f"{side}|{label}", {}).get("net_per_trade") for d in days]
            got = [v for v in vals if v is not None]
            if not got:
                continue
            agree = sum(1 for v in got if bp is not None and (v > 0) == (bp > 0))
            print(f"  {side+' '+label:<20}{bp:>+12,.0f}"
                  + "".join(f"{v:>+12,.0f}" if v is not None else f"{'-':>12}" for v in vals)
                  + f"{f'{agree}/{len(got)} same dir':>12}")
    print(f"\n  CLAIM SCORECARD")
    for name, _ in CLAIMS:
        res = [rows[d]["claims"].get(name) for d in days]
        held = sum(1 for r in res if r)
        print(f"    {held}/{len(res)}  {name:<38} " + " ".join("Y" if r else "n" for r in res))
    print(f"\n  book: " + "  ".join(f"{d[5:]} raw {rows[d]['net_raw']:+,.0f} / filtered {rows[d]['net_filtered']:+,.0f}"
                                    for d in days))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--report" in sys.argv:
        report(); return
    if "--since" in sys.argv:
        start = datetime.date.fromisoformat(args[0]); today = datetime.date.today()
        days = [(start + datetime.timedelta(d)).isoformat() for d in range((today - start).days + 1)]
    else:
        days = args or [datetime.date.today().isoformat()]
    out = [r for r in (evaluate(d) for d in days) if r]
    if out:
        with open(LOG, "a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        print(f"\nlogged {len(out)} day(s) -> {LOG}")
        report()


if __name__ == "__main__":
    main()
