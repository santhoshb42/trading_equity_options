#!/usr/bin/env python3
"""
AO_MOMCONT decision tracker — OPEN ITEM marked 2026-08-22, decide after 2 more sessions
(08-24 and 08-25). Re-scores the same questions each day so the verdict is like-for-like.

Baseline at marking (110 trades, 08-18..08-21):
    net -Rs21,022 | 43% win | -Rs191/trade | gross/charge -1.03x | positive on 1 of 4 days
    PE-ITM -Rs534 (flat) vs PE-OTM -Rs20,488 (the entire loss)
    09:30-09:50 window = -Rs17,515 at -3.88x = 83% of the whole loss

Usage:
  python3 tools/momcont_eval.py                 # today
  python3 tools/momcont_eval.py 2026-08-24
  python3 tools/momcont_eval.py --since 2026-08-18
  python3 tools/momcont_eval.py --report        # accumulated record + verdict
"""
import json, sys, datetime, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = [("CE_OPTIONS", "ITM"), ("CE_OPTIONS", "OTM"), ("PUT_OPTIONS", "ITM"), ("PUT_OPTIONS", "OTM")]
LOG = ROOT / "tools" / "momcont_log.jsonl"
WINDOWS = [("09:30-09:50", "09:30", "09:50"), ("09:50-10:30", "09:50", "10:30"),
           ("10:30-11:30", "10:30", "11:30"), ("11:30-13:00", "11:30", "13:00"),
           ("13:00-close", "13:00", "23:59")]

BASELINE = {"net": -21022, "win": 43.0, "per_trade": -191, "g_over_c": -1.03,
            "itm_net": -534, "otm_net": -20488, "open_window_net": -17515}

# The questions that decide it. MOMCONT survives only if these start coming out YES.
CLAIMS = [
    ("net positive on the day",            lambda s: s["net"] > 0),
    ("gross exceeds charges (>1.0x)",      lambda s: s["g_over_c"] > 1.0),
    ("gross positive before charges",      lambda s: s["gross"] > 0),
    ("PE-ITM leg positive",                lambda s: s["itm_net"] > 0),
    ("09:30-09:50 window not the worst",   lambda s: s["open_is_worst"] is False),
    ("win rate above 50%",                 lambda s: s["win"] > 50.0),
]


def parse(x):
    try:
        return datetime.datetime.fromisoformat(x)
    except Exception:
        return None


def collect(day):
    rows = []
    for family, mode in BOTS:
        hist = ROOT / family / mode / "data" / "option_pnl_history.json"
        if not hist.exists():
            continue
        for r in json.load(open(hist)):
            et = r.get("entry_time") or ""
            if et[:10] != day or r.get("gross_pnl") is None:
                continue
            if ((r.get("entry_context") or {}).get("entry_type") or "") != "AO_MOMCONT":
                continue
            rows.append({
                "bot": f"{'CE' if family.startswith('CE') else 'PE'}-{mode}",
                "t": et[11:16], "u": r.get("underlying"),
                "g": r.get("gross_pnl") or 0.0, "ch": r.get("charges") or 0.0,
                "n": r.get("pnl") or 0.0, "peak": r.get("peak_pct"),
                "ex": (r.get("exit_reason") or "?").split(" (")[0],
            })
    return rows


def summarise(day, rows):
    g = sum(x["g"] for x in rows); ch = sum(x["ch"] for x in rows)
    per_win = {}
    for label, a, b in WINDOWS:
        v = [x for x in rows if a <= x["t"] < b]
        if v:
            per_win[label] = round(sum(x["n"] for x in v), 2)
    worst = min(per_win, key=per_win.get) if per_win else None
    itm = [x for x in rows if x["bot"] == "PE-ITM"]
    otm = [x for x in rows if x["bot"] == "PE-OTM"]
    return {
        "day": day, "n": len(rows), "gross": round(g, 2), "charges": round(ch, 2),
        "net": round(sum(x["n"] for x in rows), 2),
        "per_trade": round(sum(x["n"] for x in rows) / len(rows), 2),
        "win": round(sum(1 for x in rows if x["n"] > 0) / len(rows) * 100, 1),
        "g_over_c": round(g / ch, 2) if ch else 0.0,
        "itm_net": round(sum(x["n"] for x in itm), 2) if itm else 0.0,
        "otm_net": round(sum(x["n"] for x in otm), 2) if otm else 0.0,
        "windows": per_win, "worst_window": worst,
        "open_is_worst": worst == "09:30-09:50",
        "exits": dict(collections.Counter(x["ex"] for x in rows)),
        "evaluated_at": datetime.datetime.now().isoformat(),
    }


def evaluate(day, quiet=False):
    rows = collect(day)
    if not rows:
        if not quiet:
            print(f"\n{day}: no AO_MOMCONT trades")
        return None
    s = summarise(day, rows)
    s["claims"] = {name: bool(fn(s)) for name, fn in CLAIMS}
    if not quiet:
        print(f"\n{'='*84}\n{day}   {s['n']} MOMCONT trades   net Rs {s['net']:+,.0f}   "
              f"win {s['win']:.0f}%   gross/charge {s['g_over_c']:.2f}x\n{'='*84}")
        print(f"  gross Rs {s['gross']:+,.0f}   charges Rs {s['charges']:,.0f}   "
              f"per trade Rs {s['per_trade']:+,.0f}")
        print(f"  PE-ITM Rs {s['itm_net']:+,.0f}   PE-OTM Rs {s['otm_net']:+,.0f}")
        print(f"\n  by window:")
        for label, a, b in WINDOWS:
            if label in s["windows"]:
                mark = "  <-- worst" if label == s["worst_window"] else ""
                print(f"    {label:<14} Rs {s['windows'][label]:>+9,.0f}{mark}")
        print(f"\n  decision questions:")
        for name, _ in CLAIMS:
            print(f"    [{'YES' if s['claims'][name] else ' no'}]  {name}")
    return s


def report():
    if not LOG.exists():
        print("no log yet"); return
    rows = {}
    for line in LOG.read_text().splitlines():
        if line.strip():
            r = json.loads(line); rows[r["day"]] = r
    days = sorted(rows)
    print(f"\n{'='*92}\nAO_MOMCONT — OPEN ITEM marked 2026-08-22, {len(days)} session(s) logged\n{'='*92}")
    print(f"\n  {'day':<13}{'n':>5}{'win%':>7}{'gross':>10}{'charges':>9}{'NET':>10}{'g/c':>7}"
          f"{'ITM':>9}{'OTM':>9}")
    tn = tg = tc = ti = to = nn = 0
    for d in days:
        r = rows[d]
        print(f"  {d:<13}{r['n']:>5}{r['win']:>6.0f}%{r['gross']:>+10,.0f}{r['charges']:>9,.0f}"
              f"{r['net']:>+10,.0f}{r['g_over_c']:>7.2f}{r['itm_net']:>+9,.0f}{r['otm_net']:>+9,.0f}")
        tn += r["net"]; tg += r["gross"]; tc += r["charges"]
        ti += r["itm_net"]; to += r["otm_net"]; nn += r["n"]
    print(f"  {'-'*78}")
    print(f"  {'TOTAL':<13}{nn:>5}{'':>7}{tg:>+10,.0f}{tc:>9,.0f}{tn:>+10,.0f}"
          f"{(tg/tc if tc else 0):>7.2f}{ti:>+9,.0f}{to:>+9,.0f}")
    print(f"\n  BASELINE at marking (08-18..08-21): net Rs {BASELINE['net']:+,} | "
          f"g/c {BASELINE['g_over_c']}x | ITM Rs {BASELINE['itm_net']:+,} | OTM Rs {BASELINE['otm_net']:+,}")
    print(f"\n  DECISION SCORECARD (needs to start reading YES to survive)")
    for name, _ in CLAIMS:
        res = [rows[d]["claims"].get(name) for d in days]
        print(f"    {sum(1 for x in res if x)}/{len(res)}  {name:<34} "
              + " ".join("Y" if x else "n" for x in res))
    new = [d for d in days if d >= "2026-08-24"]
    if len(new) >= 2:
        nt = sum(rows[d]["net"] for d in new)
        ng = sum(rows[d]["gross"] for d in new)
        print(f"\n  >>> VERDICT WINDOW COMPLETE ({', '.join(new)}): net Rs {nt:+,.0f}, gross Rs {ng:+,.0f}")
        print(f"  >>> {'KEEP — it turned' if nt > 0 and ng > 0 else 'TURN IT OFF — usePeMomCont=false in TradingView'}")
    else:
        need = 2 - len(new)
        print(f"\n  >>> {need} more session(s) needed before deciding (08-24, 08-25)")


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
