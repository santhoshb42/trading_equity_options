#!/usr/bin/env python3
"""
Slippage analysis (PAPER) — quantifies the entry/exit slippage the bots WOULD incur in LIVE.

Reads closed positions from each bot's logs/<date>/positions.jsonl. Every closed record now
carries:
  - entry_context.entry_slippage : {ideal_ltp, real_bid, real_ask, spread_pct, fill, slippage_pct,
                                     applied, spread_is_synthetic, order_type}
  - exit_slippage                : {intended_exit, real_bid, fill, slippage_pct, applied,
                                     spread_is_synthetic, reason}

Usage:
  python3 analyze_slippage.py                 # last 4 trading days, all bots
  python3 analyze_slippage.py 2026-06-25      # a single date
"""
import json, os, sys
from datetime import datetime, timedelta
from collections import defaultdict

ROOT = "/root/santhosh/trading"
BOTS = [("CE_OPTIONS/ITM","CE-ITM"),("CE_OPTIONS/OTM","CE-OTM"),
        ("PUT_OPTIONS/ITM","PUT-ITM"),("PUT_OPTIONS/OTM","PUT-OTM")]

def dates_arg():
    if len(sys.argv) > 1:
        return sys.argv[1:]
    return [(datetime.today()-timedelta(d)).strftime("%Y-%m-%d") for d in range(0,5)]

def load(path, date):
    f = f"{ROOT}/{path}/logs/{date}/positions.jsonl"
    out = []
    if not os.path.exists(f): return out
    for line in open(f):
        try:
            d = json.loads(line)
            if d.get("action") != "closed": continue
            p = d.get("position", {})
            ent = (p.get("entry_context") or {}).get("entry_slippage") or {}
            ext = p.get("exit_slippage") or {}
            out.append({
                "sym": p.get("underlying",""),
                "pnl_pct": float(p.get("pnl_percent") or 0),
                "entry": ent, "exit": ext,
            })
        except Exception:
            pass
    return out

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

DATES = dates_arg()
print(f"Dates: {min(DATES)} … {max(DATES)}\n")
print(f"{'Bot':9} {'trades':>6} {'w/real':>6} {'avgSpread%':>10} "
      f"{'entrySlip%':>10} {'exitSlip%':>10} {'roundTrip%':>10} {'applied':>8}")
print("-"*82)

grand = defaultdict(list)
for path,label in BOTS:
    rows=[]
    for dt in DATES: rows += load(path, dt)
    spreads, eslip, xslip, applied_e, applied_x, real = [],[],[],0,0,0
    for r in rows:
        e, x = r["entry"], r["exit"]
        syn = e.get("spread_is_synthetic", True) or x.get("spread_is_synthetic", True)
        sp = fnum(e.get("spread_pct"))
        if sp is not None and not e.get("spread_is_synthetic", True):
            spreads.append(sp); real += 1
        es = fnum(e.get("slippage_pct"));  xs = fnum(x.get("slippage_pct"))
        if es is not None: eslip.append(es)
        if xs is not None: xslip.append(xs)
        if e.get("applied"): applied_e += 1
        if x.get("applied"): applied_x += 1
    n=len(rows)
    avg=lambda L: sum(L)/len(L) if L else 0.0
    rt = avg(eslip)+avg(xslip)
    print(f"{label:9} {n:6} {real:6} {avg(spreads):10.2f} "
          f"{avg(eslip):10.2f} {avg(xslip):10.2f} {rt:10.2f} {applied_e}/{applied_x:>3}")
    grand[label] = rows

print("-"*82)
print("\nNotes:")
print("  • entrySlip% = (ask-ltp)/ltp — what a BUY overpays vs the LTP old-PAPER booked.")
print("  • exitSlip%  = (bid-trigger)/trigger — what a SELL gives up vs the trigger old-PAPER booked.")
print("  • roundTrip% ≈ the per-trade PnL the old PAPER OVERSTATED (now baked into PnL when applied).")
print("  • w/real = trades with a REAL (non-synthetic) spread quote. Synthetic (ltp±2%) are excluded")
print("    from the spread average — only count real-depth quotes as honest slippage measurements.")
print("  • applied = entries/exits where the model actually changed the booked fill.")

# Spread distribution (real quotes only) — to choose an enforce threshold later
all_sp=[]
for label,rows in grand.items():
    for r in rows:
        sp=fnum(r["entry"].get("spread_pct"))
        if sp is not None and not r["entry"].get("spread_is_synthetic", True):
            all_sp.append(sp)
if all_sp:
    all_sp.sort()
    def pct(p): return all_sp[min(len(all_sp)-1, int(len(all_sp)*p))]
    print(f"\nReal-spread distribution (n={len(all_sp)}):  "
          f"p50={pct(.5):.2f}%  p75={pct(.75):.2f}%  p90={pct(.9):.2f}%  "
          f"p95={pct(.95):.2f}%  max={all_sp[-1]:.2f}%")
    print("  → use this to set OPTIONS_MAX_ENTRY_SPREAD_PCT before flipping ENFORCE=true.")
else:
    print("\n(no real-depth spread quotes yet — run a session after restart to collect data)")
