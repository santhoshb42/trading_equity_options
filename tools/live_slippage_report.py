#!/usr/bin/env python3
"""What the LIVE pilot actually paid, read from the PERMANENT trade record.

THE QUESTION THIS EXISTS TO ANSWER. PAPER books its own fills; LIVE books the broker's. On
2026-07-03, the only previous real session, PAPER had reported +Rs51,109 and +Rs47,730 on the
two preceding days and the first real half-day lost Rs9,836 over 54 trades (-Rs182/trade). We
never knew why, because nothing recorded what a real fill cost. Now it does.

READS option_pnl_history.json, NOT the logs. The logs carry more detail but are pruned by cron
(now 45 days, was 7); the trade record is permanent. Fields used:
  entry_context.entry_slippage : ideal_ltp, fill, slippage_pct, real_bid/ask, live_order_price,
                                 live_confirm_ms, live_order_id, live_quantity, mode
  exit_slippage                : live_decided_exit, live_fill, live_slippage_pct, live_adverse,
                                 live_rupees, live_bid/ask_at_decision, live_broker_managed

Usage:  python3 tools/live_slippage_report.py              # every LIVE trade on record
        python3 tools/live_slippage_report.py 2026-10-06   # one session
"""
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = [("CE_OPTIONS/ITM", "CE-ITM"), ("CE_OPTIONS/OTM", "CE-OTM"),
        ("PUT_OPTIONS/ITM", "PE-ITM"), ("PUT_OPTIONS/OTM", "PE-OTM")]


def load(day=None):
    out = []
    for base, name in BOTS:
        try:
            rows = json.load(open(ROOT / base / "data" / "option_pnl_history.json"))
        except Exception:
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            t = str(r.get("entry_time") or "")
            if day and t[:10] != day:
                continue
            ec = r.get("entry_context") or {}
            en = ec.get("entry_slippage") or {}
            ex = r.get("exit_slippage") or {}
            # LIVE only: PAPER rows model their own fills and would drown the real ones.
            if str(en.get("mode") or ex.get("mode") or "").upper() != "LIVE":
                continue
            out.append(dict(bot=name, sym=r.get("symbol"), t=t, en=en, ex=ex,
                            qty=float(r.get("quantity") or 0),
                            pnl=float(r.get("pnl") or 0),
                            reason=str(r.get("exit_reason") or "")[:24]))
    return out


def dist(vals, unit="%"):
    if not vals:
        return "no data"
    v = sorted(vals)
    return (f"n={len(v):<4} median {st.median(v):+7.2f}{unit}  "
            f"p10 {v[int(len(v) * .1)]:+7.2f}{unit}  p90 {v[int(len(v) * .9)]:+7.2f}{unit}  "
            f"worst {v[0]:+7.2f}{unit}")


def main():
    day = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    rows = load(day)
    print(__doc__.split("Usage:")[0].rstrip())
    if not rows:
        print("\n  NO LIVE TRADES ON RECORD YET.")
        print("  (this reads only rows whose slippage meta says mode=LIVE)\n")
        return
    print(f"\n{'=' * 78}\n  {len(rows)} LIVE trades" + (f" on {day}" if day else "") + f"\n{'=' * 78}")

    # ---- entry ----
    e_slip = [r["en"]["slippage_pct"] for r in rows if r["en"].get("slippage_pct") is not None]
    e_rs = []
    for r in rows:
        en = r["en"]
        if en.get("fill") and en.get("ideal_ltp") and r["qty"]:
            e_rs.append((en["fill"] - en["ideal_ltp"]) * r["qty"])
    print("\nENTRY — real fill vs the LTP we decided on")
    print(f"  {dist(e_slip)}")
    if e_rs:
        print(f"  cost in rupees: total Rs{sum(e_rs):,.0f}  median Rs{st.median(e_rs):,.0f}/trade")
    cms = [r["en"]["live_confirm_ms"] for r in rows if r["en"].get("live_confirm_ms")]
    if cms:
        print(f"  order -> fill latency: median {st.median(cms):,.0f}ms  worst {max(cms):,.0f}ms")

    # ---- exit ----
    x_slip = [r["ex"]["live_slippage_pct"] for r in rows if r["ex"].get("live_slippage_pct") is not None]
    x_rs = [r["ex"]["live_rupees"] for r in rows if r["ex"].get("live_rupees") is not None]
    print("\nEXIT — real fill vs the price the exit decision was made on")
    print(f"  {dist(x_slip)}")
    if x_rs:
        print(f"  cost in rupees: total Rs{sum(x_rs):,.0f}  median Rs{st.median(x_rs):,.0f}/trade")
    adv = [r for r in rows if r["ex"].get("live_adverse")]
    if x_slip:
        print(f"  adverse on {len(adv)} of {len(x_slip)} exits ({100 * len(adv) / len(x_slip):.0f}%)")

    # THE ONE THAT MATTERS: a broker STOPLOSS_MARKET fills wherever the book is when it triggers.
    bm = [r["ex"]["live_slippage_pct"] for r in rows
          if r["ex"].get("live_broker_managed") and r["ex"].get("live_slippage_pct") is not None]
    mn = [r["ex"]["live_slippage_pct"] for r in rows
          if not r["ex"].get("live_broker_managed") and r["ex"].get("live_slippage_pct") is not None]
    print(f"\n  broker-fired SL : {dist(bm)}")
    print(f"  our own exit    : {dist(mn)}")
    print("  (the SL is a MARKET order on trigger - this split is where a stop really costs)")

    # ---- round trip ----
    print("\nROUND TRIP — what a real trade costs before the strategy earns anything")
    rt = []
    for r in rows:
        en, ex = r["en"], r["ex"]
        a = (en["fill"] - en["ideal_ltp"]) * r["qty"] if en.get("fill") and en.get("ideal_ltp") and r["qty"] else None
        b = ex.get("live_rupees")
        if a is not None and b is not None:
            rt.append(a - b)          # entry paid above + exit received below
    if rt:
        print(f"  n={len(rt)}  total Rs{sum(rt):,.0f}  median Rs{st.median(rt):,.0f}/trade")
        print(f"  BENCHMARK 2026-07-03: -Rs182/trade real vs the +Rs241/trade PAPER claimed.")
    else:
        print("  not computable yet (need both ends on the same trade)")

    # ---- cuts that explain it ----
    print("\nBY BOT")
    for _, name in BOTS:
        g = [r for r in rows if r["bot"] == name]
        if not g:
            continue
        s = [r["ex"]["live_slippage_pct"] for r in g if r["ex"].get("live_slippage_pct") is not None]
        print(f"  {name:<8} trades {len(g):>3}  pnl Rs{sum(x['pnl'] for x in g):>9,.0f}  exit slip {dist(s)}")

    print("\nBY EXIT REASON")
    by = defaultdict(list)
    for r in rows:
        if r["ex"].get("live_slippage_pct") is not None:
            by[r["reason"].split(" ")[0]].append(r["ex"]["live_slippage_pct"])
    for k, v in sorted(by.items(), key=lambda kv: st.median(kv[1])):
        print(f"  {k:<24} {dist(v)}")

    print("\nBY HOUR (is the opening burst worse?)")
    byh = defaultdict(list)
    for r in rows:
        if r["ex"].get("live_slippage_pct") is not None and len(r["t"]) > 13:
            byh[r["t"][11:13]].append(r["ex"]["live_slippage_pct"])
    for h in sorted(byh):
        print(f"  {h}:00  {dist(byh[h])}")

    print("\nMARKET IMPACT — does a bigger order fill worse?")
    bys = defaultdict(list)
    for r in rows:
        if r["ex"].get("live_slippage_pct") is not None and r["qty"]:
            bys[r["qty"]].append(r["ex"]["live_slippage_pct"])
    if len(bys) > 1:
        for q in sorted(bys):
            print(f"  qty {int(q):>5}  {dist(bys[q])}")
    else:
        print("  only one order size on record - raise OPTIONS_MAX_LOTS_PER_TRADE to compare")
    print()


if __name__ == "__main__":
    main()
