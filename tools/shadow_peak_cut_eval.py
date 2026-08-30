#!/usr/bin/env python3
"""
Shadow evaluation of early peak-cut rules — measures, without changing bot behaviour,
what "exit if peak < THRESH by M minutes" would have done on real closed trades.

Reads only existing telemetry:
  <BOT>/data/trial_sl_premium_snapshots.jsonl   (recorded premium ticks)
  <BOT>/data/option_pnl_history.json            (closed trades, booked P&L)

Both sides of the ledger are counted: losses avoided AND recoveries forfeited.
The cut is priced at the last recorded tick at or before minute M; charges are
paid either way. Trades that already closed before minute M are excluded.

Appends one row per (day, rule) to tools/shadow_peak_cut_log.jsonl so evidence
accumulates across sessions — the point is the multi-day record, not any one day.

Usage:
  python3 tools/shadow_peak_cut_eval.py                 # today
  python3 tools/shadow_peak_cut_eval.py 2026-08-17 2026-08-18
  python3 tools/shadow_peak_cut_eval.py --since 2026-08-17
  python3 tools/shadow_peak_cut_eval.py --report        # summarise the whole log
"""
import json, sys, subprocess, datetime, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = [("CE_OPTIONS", "ITM"), ("CE_OPTIONS", "OTM"), ("PUT_OPTIONS", "ITM"), ("PUT_OPTIONS", "OTM")]
LOG = ROOT / "tools" / "shadow_peak_cut_log.jsonl"
RULES = [(2, 0.0), (2, 1.0), (2, 2.0), (3, 1.0), (3, 2.0), (5, 2.0), (5, 3.0)]
MARKS = sorted({m for m, _ in RULES})


def parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def load_ticks(family, mode, day):
    """Premium ticks for `day`, keyed by option symbol. grep-prefiltered — the raw file is ~100MB."""
    path = ROOT / family / mode / "data" / "trial_sl_premium_snapshots.jsonl"
    out = collections.defaultdict(list)
    if not path.exists():
        return out
    try:
        res = subprocess.run(["grep", "-a", f'"timestamp":"{day}', str(path)],
                             capture_output=True, text=True, timeout=180)
        lines = res.stdout.splitlines()
    except Exception:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("current_premium") is None:
            continue
        ts = parse_ts(r.get("timestamp") or "")
        if ts:
            out[r.get("symbol")].append((ts, r["current_premium"]))
    for k in out:
        out[k].sort()
    return out


def collect(day):
    trades = []
    for family, mode in BOTS:
        ticks = load_ticks(family, mode, day)
        hist = ROOT / family / mode / "data" / "option_pnl_history.json"
        if not hist.exists():
            continue
        for r in json.load(open(hist)):
            if (r.get("entry_time") or "")[:10] != day or r.get("gross_pnl") is None:
                continue
            e = parse_ts(r.get("entry_time")); c = parse_ts(r.get("closed_at") or "")
            ep = r.get("entry_premium"); q = r.get("quantity")
            if not e or not ep or not q:
                continue
            ser = [(t, p) for t, p in ticks.get(r.get("symbol"), [])
                   if e <= t <= (c or e + datetime.timedelta(hours=6))]
            if not ser:
                continue
            peak, price, run = {}, {}, ep
            for t, p in ser:
                mins = (t - e).total_seconds() / 60.0
                run = max(run, p)
                for M in MARKS:
                    if mins <= M:
                        peak[M] = (run / ep - 1) * 100
                        price[M] = p
            trades.append({
                "bot": f"{family}:{mode}", "underlying": r.get("underlying"),
                "peak": peak, "price": price, "entry_premium": ep, "qty": q,
                "charges": r.get("charges") or 92.0, "pnl": r.get("pnl") or 0.0,
                "held_min": ((c - e).total_seconds() / 60.0 if c else 999.0),
            })
    return trades


def evaluate(day, trades, quiet=False):
    rows = []
    book = sum(t["pnl"] for t in trades)
    if not quiet:
        print(f"\n{day}   {len(trades)} closed trades with tick data   booked Rs {book:+,.0f}")
        print(f"  {'rule':<18}{'cut':>5}{'recovered':>11}{'forfeited':>12}{'saved':>11}{'NET':>11}")
    for M, TH in RULES:
        hit = [t for t in trades if M in t["peak"] and t["peak"][M] <= TH and t["held_min"] > M]
        if not hit:
            continue
        deltas = [((t["price"][M] - t["entry_premium"]) * t["qty"] - t["charges"]) - t["pnl"]
                  for t in hit]
        row = {
            "day": day, "rule": f"peak<={TH:g}%@{M}min", "minutes": M, "threshold": TH,
            "n_trades_day": len(trades), "n_cut": len(hit),
            "n_recovered": sum(1 for t in hit if t["pnl"] > 0),
            "forfeited": round(sum(d for d in deltas if d < 0), 2),
            "saved": round(sum(d for d in deltas if d > 0), 2),
            "net": round(sum(deltas), 2), "book_actual": round(book, 2),
            "evaluated_at": datetime.datetime.now().isoformat(),
        }
        rows.append(row)
        if not quiet:
            print(f"  {row['rule']:<18}{row['n_cut']:>5}{row['n_recovered']:>11}"
                  f"{row['forfeited']:>+12,.0f}{row['saved']:>+11,.0f}{row['net']:>+11,.0f}")
    return rows


def report():
    if not LOG.exists():
        print("no log yet"); return
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    latest = {}
    for r in rows:                       # keep the newest evaluation per (day, rule)
        latest[(r["day"], r["rule"])] = r
    rows = list(latest.values())
    days = sorted({r["day"] for r in rows})
    rules = sorted({r["rule"] for r in rows}, key=lambda s: (int(s.split("@")[1][:-3]), s))
    print(f"\nSHADOW PEAK-CUT RECORD — {len(days)} session(s): {', '.join(days)}")
    print("A rule is only worth shipping if it is positive on most days AND positive overall.\n")
    print(f"  {'rule':<18}" + "".join(f"{d[5:]:>11}" for d in days) + f"{'TOTAL':>12}{'+days':>8}")
    for rule in rules:
        per = {d: next((r["net"] for r in rows if r["day"] == d and r["rule"] == rule), None)
               for d in days}
        vals = [v for v in per.values() if v is not None]
        pos = sum(1 for v in vals if v > 0)
        print(f"  {rule:<18}" + "".join(f"{per[d]:>+11,.0f}" if per[d] is not None else f"{'-':>11}"
                                        for d in days)
              + f"{sum(vals):>+12,.0f}{f'{pos}/{len(vals)}':>8}")
    print("\n  net = losses avoided minus recoveries forfeited, charges paid either way.")
    print("  positive means the cut would have helped that day.")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--report" in sys.argv:
        report(); return
    if "--since" in sys.argv:
        start = datetime.date.fromisoformat(args[0])
        today = datetime.date.today()
        days = [(start + datetime.timedelta(d)).isoformat()
                for d in range((today - start).days + 1)]
    else:
        days = args or [datetime.date.today().isoformat()]
    out = []
    for day in days:
        trades = collect(day)
        if not trades:
            print(f"\n{day}   no closed trades with tick data — skipped")
            continue
        out += evaluate(day, trades)
    if out:
        with open(LOG, "a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        print(f"\nlogged {len(out)} rows -> {LOG}")
    report()


if __name__ == "__main__":
    main()
