#!/usr/bin/env python3
"""Would a "price still moving my way at the moment of entry" gate have helped?

The bots never look at the underlying again after the alert: underlying_entry_price is the
alert price copied verbatim (optmonitor.py), so this question cannot be answered from the
trade records alone. This script downloads ONE_MINUTE candles for every underlying traded on
a given day and reconstructs, for each fill, where the underlying was at that moment.

The gate being tested (bot-side, one extra LTP call right before the order):
    CE  -> take the trade only if spot at entry is ABOVE the alert price
    PE  -> take the trade only if spot at entry is BELOW the alert price

A 1-minute candle cannot give the price at a specific second, so each fill is judged by the
candle CONTAINING it, and the verdict is only stated when the whole minute is on one side:
    low  >= alert price  -> the price was above the alert for the entire minute  (certain)
    high <= alert price  -> below for the entire minute                          (certain)
    otherwise            -> AMBIGUOUS, the minute straddles the alert price
Ambiguous fills are reported separately and never counted as a decision, so the numbers are a
floor, not an estimate. Using the candle close instead would be look-ahead (the close happens
after the fill), which is why it is not used.

Usage:  python3 tools/entry_direction_check.py 2026-09-23
        python3 tools/entry_direction_check.py 2026-09-23 --tol 0.05   # dead-band, percent
Run it AFTER market close: it competes with the live bots for the shared candle budget.
"""
import bisect
import datetime as dt
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = [
    ("CE_OPTIONS", "ITM", "CE-ITM", "CE"),
    ("CE_OPTIONS", "OTM", "CE-OTM", "CE"),
    ("PUT_OPTIONS", "ITM", "PE-ITM", "PE"),
    ("PUT_OPTIONS", "OTM", "PE-OTM", "PE"),
]


def ts(s):
    return dt.datetime.fromisoformat(str(s)[:26])


def load_trades(day):
    """Closed trades for the day, each joined to the alert that produced it."""
    trades = []
    for pkg, mode, bot, side in BOTS:
        base = ROOT / pkg / mode
        alerts = defaultdict(list)
        for f in glob.glob(str(base / "logs" / day / "alerts.jsonl")):
            for line in open(f):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("status") != "received":
                    continue
                sym = (r.get("alert") or {}).get("symbol")
                if sym:
                    alerts[sym].append(ts(r["timestamp"]))
        for sym in alerts:
            alerts[sym].sort()
        for r in json.load(open(base / "data" / "option_pnl_history.json")):
            if not isinstance(r, dict) or str(r.get("entry_time", ""))[:10] != day:
                continue
            und = r.get("underlying") or ""
            entry = ts(r["entry_time"])
            cand = alerts.get(und) or []
            i = bisect.bisect_left(cand, entry)
            alert_t = cand[i - 1] if i > 0 and (entry - cand[i - 1]).total_seconds() < 180 else None
            trades.append(dict(
                bot=bot, side=side, und=und, entry=entry, alert_t=alert_t,
                alert_px=float(r.get("underlying_alert_price") or 0),
                pnl=float(r.get("pnl") or 0),
                typ=(r.get("entry_context") or {}).get("entry_type") or "?",
                mins=entry.hour * 60 + entry.minute,
            ))
    return trades


def fetch_candles(unds, day):
    """ONE_MINUTE candles per underlying, keyed by minute."""
    sys.path.insert(0, str(ROOT / "CE_OPTIONS"))
    from optcode.angelone_options import AngelOneOptionsBroker
    api = AngelOneOptionsBroker()
    if not api.authenticate():
        print("!! broker authentication failed"); sys.exit(1)
    back = (dt.date.today() - dt.date.fromisoformat(day)).days + 1
    out, failed = {}, []
    for n, u in enumerate(sorted(unds), 1):
        try:
            rows = api.get_historical_data(u, interval="ONE_MINUTE", days_back=back,
                                           exchange="NSE", force_refresh=True)
        except Exception as exc:
            rows, _ = None, print(f"  ! {u}: {exc}")
        if not rows:
            failed.append(u); continue
        by_min = {}
        for c in rows:
            t = ts(c["timestamp"])
            if t.strftime("%Y-%m-%d") == day:
                by_min[t.hour * 60 + t.minute] = c
        out[u] = by_min
        if n % 25 == 0:
            print(f"  ...{n}/{len(unds)} underlyings")
    if failed:
        print(f"  no candles for {len(failed)}: {', '.join(failed[:12])}{' ...' if len(failed) > 12 else ''}")
    return out


def verdict(tr, candles, tol_pct):
    """MY_WAY / AGAINST / AMBIGUOUS / NO_DATA for one fill."""
    by_min = candles.get(tr["und"])
    if not by_min or tr["alert_px"] <= 0:
        return "NO_DATA"
    c = by_min.get(tr["mins"])
    if not c:
        return "NO_DATA"
    tol = tr["alert_px"] * tol_pct / 100.0
    up, down = tr["alert_px"] + tol, tr["alert_px"] - tol
    if tr["side"] == "CE":
        if c["low"] >= up:
            return "MY_WAY"
        if c["high"] <= up:
            return "AGAINST"
    else:
        if c["high"] <= down:
            return "MY_WAY"
        if c["low"] >= down:
            return "AGAINST"
    return "AMBIGUOUS"


def minute_direction(tr, candles):
    """SECONDARY, WEAKER view: did the entry minute close the trade's way vs the alert price?

    This peeks at the minute's CLOSE, which happens AFTER the fill (up to 60s of look-ahead), so
    it CANNOT be used to claim what a gate would have saved. It is here only to show whether the
    underlying was drifting with or against the trade around the fill, on the fills that the
    strict bracket above cannot decide.
    """
    by_min = candles.get(tr["und"])
    if not by_min or tr["alert_px"] <= 0:
        return "NO_DATA"
    c = by_min.get(tr["mins"])
    if not c:
        return "NO_DATA"
    d = c["close"] - tr["alert_px"]
    if d == 0:
        return "FLAT"
    if tr["side"] == "CE":
        return "MY_WAY" if d > 0 else "AGAINST"
    return "MY_WAY" if d < 0 else "AGAINST"


def money(rs):
    n = len(rs)
    if not n:
        return "n=   0"
    t = sum(r["pnl"] for r in rs)
    w = sum(1 for r in rs if r["pnl"] > 0)
    return f"n={n:>4}  win {100 * w / n:>3.0f}%  Rs{t:>10,.0f}  Rs/tr {t / n:>7,.0f}"


def logged_mode(day):
    """The real answer, once the bots have been logging it (--logged).

    From 2026-09-24 every entry records spot_at_entry / spot_moving_my_way in entry_context,
    measured by an LTP call that runs in parallel with order placement. No candles, no
    reconstruction, no look-ahead: this is the price at the moment of the fill.
    """
    rows = []
    for pkg, mode, bot, side in BOTS:
        path = ROOT / pkg / mode / "data" / "option_pnl_history.json"
        for r in json.load(open(path)):
            if not isinstance(r, dict) or str(r.get("entry_time", ""))[:10] != day:
                continue
            ec = r.get("entry_context") or {}
            rows.append(dict(bot=bot, side=side, und=r.get("underlying") or "",
                             entry=ts(r["entry_time"]), pnl=float(r.get("pnl") or 0),
                             typ=ec.get("entry_type") or "?",
                             my_way=ec.get("spot_moving_my_way"),
                             drift=ec.get("spot_vs_alert_pct"),
                             mins=ts(r["entry_time"]).hour * 60 + ts(r["entry_time"]).minute))
    have = [r for r in rows if r["my_way"] is not None]
    print(f"=== LOGGED ENTRY DIRECTION  {day}  ({len(rows)} trades, {len(have)} with spot logged) ===")
    if not have:
        print("  no trades carry spot_at_entry yet - the bots need a restart to pick up the probe")
        return
    my, against = [r for r in have if r["my_way"]], [r for r in have if not r["my_way"]]
    print(f"  spot moving MY WAY at entry   {money(my)}")
    print(f"  spot moving AGAINST at entry  {money(against)}")
    loss = [r for r in against if r["pnl"] <= 0]
    win = [r for r in against if r["pnl"] > 0]
    tot = sum(r["pnl"] for r in rows)
    print(f"\n  blocking the wrong-way fills: losses avoided Rs{-sum(r['pnl'] for r in loss):,.0f} ({len(loss)}), "
          f"winners given up Rs{sum(r['pnl'] for r in win):,.0f} ({len(win)})")
    print(f"  day Rs{tot:,.0f} -> Rs{tot - sum(r['pnl'] for r in against):,.0f} "
          f"({-sum(r['pnl'] for r in against):+,.0f})")
    for side in ("CE", "PE"):
        for lbl, f in (("all day", lambda r: True), ("09:15-10:00", lambda r: 555 <= r["mins"] < 600)):
            rs = [r for r in have if r["side"] == side and f(r)]
            if rs:
                print(f"  {side} {lbl:12} against: {money([r for r in rs if not r['my_way']])}")
    print("\n  worst wrong-way fills:")
    for r in sorted(against, key=lambda x: x["pnl"])[:10]:
        print(f"    {r['entry'].strftime('%H:%M:%S')} {r['bot']:7} {r['und']:14} {r['typ']:12} "
              f"drift {float(r['drift']):+6.3f}%  Rs{r['pnl']:>9,.0f}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = args[0] if args else dt.date.today().isoformat()
    if "--logged" in sys.argv:
        return logged_mode(day)
    tol = 0.0
    if "--tol" in sys.argv:
        tol = float(sys.argv[sys.argv.index("--tol") + 1])

    trades = load_trades(day)
    print(f"=== ENTRY DIRECTION CHECK  {day}  ({len(trades)} closed trades, dead-band {tol}%) ===")
    if not trades:
        print("no trades"); return
    unds = {t["und"] for t in trades}
    print(f"downloading ONE_MINUTE candles for {len(unds)} underlyings...")
    candles = fetch_candles(unds, day)

    for t in trades:
        t["v"] = verdict(t, candles, tol)
    by = defaultdict(list)
    for t in trades:
        by[t["v"]].append(t)

    print("\n-- the gate's decision on every fill --")
    for v in ("MY_WAY", "AGAINST", "AMBIGUOUS", "NO_DATA"):
        print(f"  {v:10} {money(by[v])}")

    kept = by["MY_WAY"] + by["AMBIGUOUS"] + by["NO_DATA"]   # only certain-AGAINST is blocked
    blocked = by["AGAINST"]
    bl_loss = [r for r in blocked if r["pnl"] <= 0]
    bl_win = [r for r in blocked if r["pnl"] > 0]
    tot = sum(r["pnl"] for r in trades)
    print(f"\n-- blocking only the certain wrong-way fills --")
    print(f"  losses avoided   Rs{-sum(r['pnl'] for r in bl_loss):>10,.0f}  ({len(bl_loss)} trades)")
    print(f"  winners given up Rs{sum(r['pnl'] for r in bl_win):>10,.0f}  ({len(bl_win)} trades)")
    print(f"  day  Rs{tot:,.0f}  ->  Rs{sum(r['pnl'] for r in kept):,.0f}"
          f"   ({sum(r['pnl'] for r in kept) - tot:+,.0f})")

    print("\n-- by bot --")
    for _, _, bot, _ in BOTS:
        rs = [t for t in trades if t["bot"] == bot]
        if not rs:
            continue
        k = [t for t in rs if t["v"] != "AGAINST"]
        print(f"  {bot}: day Rs{sum(r['pnl'] for r in rs):>9,.0f} -> Rs{sum(r['pnl'] for r in k):>9,.0f}"
              f"  | blocked {money([t for t in rs if t['v'] == 'AGAINST'])}")

    print("\n-- by side, and inside the morning window --")
    for side in ("CE", "PE"):
        for lbl, f in (("all day", lambda r: True), ("09:15-10:00", lambda r: 555 <= r["mins"] < 600)):
            rs = [t for t in trades if t["side"] == side and f(t)]
            if not rs:
                continue
            k = [t for t in rs if t["v"] != "AGAINST"]
            print(f"  {side} {lbl:12} Rs{sum(r['pnl'] for r in rs):>9,.0f} -> Rs{sum(r['pnl'] for r in k):>9,.0f}"
                  f"  | blocked {money([t for t in rs if t['v'] == 'AGAINST'])}")

    print("\n-- SECONDARY VIEW (contains look-ahead, not a savings estimate):")
    print("   where the entry minute CLOSED relative to the alert price --")
    for t in trades:
        t["d"] = minute_direction(t, candles)
    for v in ("MY_WAY", "AGAINST", "FLAT", "NO_DATA"):
        rs = [t for t in trades if t["d"] == v]
        if rs:
            print(f"  minute closed {v:9} {money(rs)}")
    for side in ("CE", "PE"):
        rs = [t for t in trades if t["side"] == side and t["d"] == "AGAINST"]
        if rs:
            print(f"    of those, {side} against: {money(rs)}")

    print("\n-- worst fills the gate would have blocked --")
    for t in sorted(blocked, key=lambda r: r["pnl"])[:10]:
        print(f"  {t['entry'].strftime('%H:%M:%S')} {t['bot']:7} {t['und']:14} {t['typ']:12} "
              f"alert {t['alert_px']:>9,.1f}  Rs{t['pnl']:>9,.0f}")
    print("\n-- biggest winners it would have cost --")
    for t in sorted(bl_win, key=lambda r: -r["pnl"])[:5]:
        print(f"  {t['entry'].strftime('%H:%M:%S')} {t['bot']:7} {t['und']:14} {t['typ']:12} "
              f"alert {t['alert_px']:>9,.1f}  Rs{t['pnl']:>9,.0f}")


if __name__ == "__main__":
    main()
