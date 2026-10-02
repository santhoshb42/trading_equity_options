#!/usr/bin/env python3
"""Daily verdict on the side-mute rule, logged so the record accumulates.

THE RULE (user, 2026-10-02): both sides trade freely until 10:00. After 10:00, a trade is
muted if NIFTY is beyond the band AGAINST its own side AT THE MOMENT OF ENTRY — re-checked
continuously, not once at 10:00. A single 10:00 snapshot misses days that only develop
direction later (2026-10-01: NIFTY was -0.17% at 10:00, fell to -1.65% by 14:00, and CE lost
Rs22,822 after 10:00 while PE made Rs92,102).

MEASURED OVER 51 SESSIONS (2026-07-22..10-01) before any forward test:
  +/-0.5%: Rs117,517 -> Rs287,653 (+170,136), better 16 days / worse 8, worst day improves
           -79,675 -> -62,682, still +72,121 after removing its best THREE days.
  +/-0.3%: +70,754 but acts on 42 of 51 days and is -34,209 without its best three — too tight.
  FLAT-MUTE ("stop both sides after 10:00 when |NIFTY| < band", user 2026-10-02): REJECTED on
  measurement, kept here only as a forward-test control. 51 sessions = +Rs108,931 but it acts on
  43 of 51 days, is -Rs27,115 without its best THREE days, does not change the positive-day count
  (24) or the worst day (-79,675), and post-fix (09-23..10-01) it is -Rs79,834, better 1 / worse 4.
  The flat after-10:00 book is the friction floor (-Rs23/tr over 4,674 trades), not a loser, and
  since the fixes it is the PROFITABLE cell (CE +Rs51/tr, PE +Rs312/tr) while CE-against-trend is
  -Rs378/tr. Muting flat drags the directional rule from +59,073 to -20,761.

  THE WHOLE RESULT IS THE CE HALF: muting CE on 18 strong down-days = +Rs171,853 over 634
  trades; muting PE fired on only 6 up-days for -Rs1,717. July-September drifted down, so the
  PE half is effectively untested and must NOT be shipped on this evidence.

WHY THIS TOOL EXISTS: the bots changed a lot between 09-23 and 09-30 (phantom-stop fix,
fabricated prices deleted, one-sided chain pricing, PE premium side). A rule measured across
that boundary is measured on two different systems. This logs one row per (day, band, side)
so the post-fix record stands on its own and is never re-argued from memory.

SHADOW MODE IS LIVE from 2026-10-02 (CE bots only): the bot stamps its OWN verdict on every
entry as entry_context.ce_mute_would_skip / ce_mute_reason and takes the trade anyway. Read
that with --logged to score the rule on the bot's ledger instead of this tool's offline
reconstruction; the two should agree, and --logged is the one that counts.

Usage:
  python3 tools/side_mute_eval.py                    # today
  python3 tools/side_mute_eval.py --logged <day>     # score the BOT's own stamp
  python3 tools/side_mute_eval.py --since 2026-09-29
  python3 tools/side_mute_eval.py --report           # the accumulated record
"""
import bisect
import collections
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = [("CE_OPTIONS/ITM", "CE"), ("CE_OPTIONS/OTM", "CE"),
        ("PUT_OPTIONS/ITM", "PE"), ("PUT_OPTIONS/OTM", "PE")]
REGIME = ROOT / "tools" / "market_regime_history.jsonl"
LOG = ROOT / "tools" / "side_mute_log.jsonl"
BANDS = [0.3, 0.5, 0.7]
GATE_FROM_MIN = 10 * 60          # the rule only applies after 10:00
# "flat" is the rejected control: mute BOTH sides when the index has no direction at all.
VARIANTS = ("CE", "PE", "both", "flat")


def nifty_series():
    """day_net_pct per minute, per day, from the daemon's own log."""
    by_day = collections.defaultdict(list)
    if not REGIME.exists():
        return by_day
    for line in open(REGIME):
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("last_candle_ts") or ""
        if len(t) < 16 or r.get("day_net_pct") is None:
            continue
        by_day[t[:10]].append((int(t[11:13]) * 60 + int(t[14:16]), float(r["day_net_pct"])))
    for d in by_day:
        by_day[d].sort()
    return by_day


def at(series, day, minute):
    """NIFTY's day move as it stood at that minute — never later (no look-ahead)."""
    arr = series.get(day) or []
    if not arr:
        return None
    i = bisect.bisect_right([m for m, _ in arr], minute) - 1
    return arr[i][1] if i >= 0 else None


def trades(day):
    out = []
    for base, side in BOTS:
        path = ROOT / base / "data" / "option_pnl_history.json"
        try:
            rows = json.load(open(path))
        except Exception:
            continue
        for r in rows:
            if not isinstance(r, dict) or str(r.get("entry_time") or "")[:10] != day:
                continue
            et = str(r["entry_time"])
            out.append(dict(side=side, pnl=float(r.get("pnl") or 0),
                            minute=int(et[11:13]) * 60 + int(et[14:16]),
                            sym=r.get("symbol")))
    return out


def evaluate(day, series):
    rows = trades(day)
    if not rows:
        return []
    book = sum(t["pnl"] for t in rows)
    out = []
    for band in BANDS:
        for which in VARIANTS:
            muted = []
            for t in rows:
                if t["minute"] < GATE_FROM_MIN:
                    continue
                nf = at(series, day, t["minute"])
                if nf is None:
                    continue
                if which == "flat":
                    if abs(nf) < band:
                        muted.append(t)
                    continue
                wrong = (t["side"] == "CE" and nf < -band) or (t["side"] == "PE" and nf > band)
                if wrong and which in (t["side"], "both"):
                    muted.append(t)
            out.append(dict(day=day, band=band, mutes=which, n_trades=len(rows),
                            n_muted=len(muted),
                            muted_pnl=round(sum(t["pnl"] for t in muted), 2),
                            book_actual=round(book, 2),
                            book_with_rule=round(book - sum(t["pnl"] for t in muted), 2),
                            delta=round(-sum(t["pnl"] for t in muted), 2),
                            evaluated_at=datetime.datetime.now().isoformat(timespec="seconds")))
    return out


def logged(days):
    """Score the rule on the bot's own stamp (shadow mode), not on our reconstruction."""
    print("\nBOT'S OWN SHADOW VERDICT (entry_context.ce_mute_would_skip, CE bots only)")
    print("  the rule is ENFORCED only when ce_mute_mode says ENFORCE; otherwise these traded.\n")
    grand_skip = grand_kept = 0.0
    for day in days:
        rows = []
        for base, side in BOTS:
            if side != "CE":
                continue
            try:
                recs = json.load(open(ROOT / base / "data" / "option_pnl_history.json"))
            except Exception:
                continue
            for r in recs:
                if not isinstance(r, dict) or str(r.get("entry_time") or "")[:10] != day:
                    continue
                ec = r.get("entry_context") or {}
                if "ce_mute_would_skip" not in ec:
                    continue
                rows.append((bool(ec["ce_mute_would_skip"]), float(r.get("pnl") or 0),
                             ec.get("ce_mute_mode"), ec.get("ce_mute_reason")))
        if not rows:
            print(f"  {day}: no stamped CE trades (bot not restarted yet, or no trades)")
            continue
        sk = [r for r in rows if r[0]]
        kp = [r for r in rows if not r[0]]
        grand_skip += sum(r[1] for r in sk)
        grand_kept += sum(r[1] for r in kp)
        mode = rows[0][2]
        print(f"  {day}  mode={mode}  stamped {len(rows)} CE trades")
        print(f"     would-skip : n={len(sk):>3}  Rs{sum(r[1] for r in sk):>9,.0f}"
              f"  ({sum(r[1] for r in sk)/max(len(sk),1):>+7,.0f}/tr)  <- the rule's claim")
        print(f"     kept       : n={len(kp):>3}  Rs{sum(r[1] for r in kp):>9,.0f}"
              f"  ({sum(r[1] for r in kp)/max(len(kp),1):>+7,.0f}/tr)")
    print(f"\n  TOTAL would-skip Rs{grand_skip:,.0f} | kept Rs{grand_kept:,.0f}")
    print(f"  Rule ships only if would-skip stays clearly NEGATIVE across sessions.\n")


def report():
    if not LOG.exists():
        print("no log yet")
        return
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    latest = {}
    for r in rows:                      # newest evaluation per (day, band, mutes)
        latest[(r["day"], r["band"], r["mutes"])] = r
    rows = list(latest.values())
    days = sorted({r["day"] for r in rows})
    print(f"\nSIDE-MUTE RECORD — {len(days)} session(s): {days[0]} .. {days[-1]}")
    print("Ship only what is positive overall AND on most of the days it acts.\n")
    print(f"  {'band':>6} {'mutes':>6} {'acted':>6} {'muted':>6} {'delta':>12} {'better':>7} {'worse':>6}")
    for band in BANDS:
        for which in VARIANTS:
            sel = [r for r in rows if r["band"] == band and r["mutes"] == which]
            if not sel:
                continue
            acted = [r for r in sel if r["n_muted"]]
            print(f"  {band:>6} {which:>6} {len(acted):>6} {sum(r['n_muted'] for r in sel):>6} "
                  f"{sum(r['delta'] for r in sel):>+12,.0f} "
                  f"{sum(1 for r in acted if r['delta'] > 0):>7} {sum(1 for r in acted if r['delta'] < 0):>6}")
    print("\n  per day, the candidate rule (band 0.5, CE only):")
    for d in days:
        r = next((x for x in rows if x["day"] == d and x["band"] == 0.5 and x["mutes"] == "CE"), None)
        if r:
            print(f"    {d}  book {r['book_actual']:>10,.0f} -> {r['book_with_rule']:>10,.0f} "
                  f"({r['delta']:>+9,.0f})  muted {r['n_muted']:>3}")
    print("\n  delta = what the rule would have added. Muting is priced by removing those trades;")
    print("  capital is not reallocated, which the bots would not do either.\n")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--report" in sys.argv:
        report()
        return
    if "--logged" in sys.argv:
        logged(args or [datetime.date.today().isoformat()])
        return
    if "--since" in sys.argv:
        start = datetime.date.fromisoformat(args[0])
        today = datetime.date.today()
        days = [(start + datetime.timedelta(d)).isoformat() for d in range((today - start).days + 1)]
    else:
        days = args or [datetime.date.today().isoformat()]
    series = nifty_series()
    out = []
    for day in days:
        rows = evaluate(day, series)
        if not rows:
            print(f"  {day}: no trades — skipped")
            continue
        ce = next(r for r in rows if r["band"] == 0.5 and r["mutes"] == "CE")
        print(f"  {day}: book Rs{ce['book_actual']:,.0f} | band 0.5 CE-mute {ce['delta']:+,.0f} "
              f"({ce['n_muted']} trades)")
        out += rows
    if out:
        with open(LOG, "a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        print(f"\nlogged {len(out)} rows -> {LOG}")
    report()


if __name__ == "__main__":
    main()
