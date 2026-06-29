#!/usr/bin/env python3
"""
Alert→order latency analysis. Measures the pipeline delay (received → position_created)
from each bot's logs/<date>/alerts.jsonl, which carry a timestamp per status.

This is the metric we're driving under 3s (was ~20s median pre-fix). Run before/after a
session to see the impact of the per-endpoint rate-limiter fix.

Usage:
  python3 analyze_latency.py                # last 4 trading days
  python3 analyze_latency.py 2026-06-26     # one date (e.g. tomorrow, post-fix)
"""
import json, os, sys
from datetime import datetime, timedelta
from collections import defaultdict

ROOT="/root/santhosh/trading"
BOTS=[("CE_OPTIONS/ITM","CE-ITM"),("CE_OPTIONS/OTM","CE-OTM"),
      ("PUT_OPTIONS/ITM","PUT-ITM"),("PUT_OPTIONS/OTM","PUT-OTM")]
STAGES=['received','queued','bot_processing_started','signal_validated','daily_limit_passed','position_created']

def ts(s):
    try: return datetime.fromisoformat(s)
    except: return None
def akey(d):
    a=d.get('alert',{}); return f"{a.get('symbol')}|{a.get('price')}|{d.get('timestamp','')[:16]}"
def pct(L,q):
    if not L: return 0.0
    L=sorted(L); return L[min(len(L)-1,int(len(L)*q))]

dates = sys.argv[1:] if len(sys.argv)>1 else [(datetime.today()-timedelta(d)).strftime("%Y-%m-%d") for d in range(0,5)]

e2e_all=[]; stage=defaultdict(list); per_bot=defaultdict(list)
for path,label in BOTS:
    for dt in dates:
        f=f"{ROOT}/{path}/logs/{dt}/alerts.jsonl"
        if not os.path.exists(f): continue
        g=defaultdict(dict)
        for line in open(f):
            try: d=json.loads(line)
            except: continue
            t=ts(d.get('timestamp')); s=d.get('status')
            if t and s: g[akey(d)][s]=t
        for k,v in g.items():
            if 'received' in v and 'position_created' in v:
                e=(v['position_created']-v['received']).total_seconds()
                if 0<=e<300: e2e_all.append(e); per_bot[label].append(e)
            prev=None
            for s in STAGES:
                if s in v:
                    if prev and (v[s]-v[prev]).total_seconds()>=0:
                        stage[f"{prev}→{s}"].append((v[s]-v[prev]).total_seconds())
                    prev=s

print(f"Dates: {min(dates)} … {max(dates)}   |   entries measured: {len(e2e_all)}\n")
if not e2e_all:
    print("No completed entries in range."); sys.exit(0)
print("END-TO-END alert→order latency (s):")
print(f"  median={pct(e2e_all,.5):.2f}  p75={pct(e2e_all,.75):.2f}  p90={pct(e2e_all,.9):.2f}  p95={pct(e2e_all,.95):.2f}  max={max(e2e_all):.2f}")
print(f"  TARGET: median < 3s\n")
print("Per bot (median / p90):")
for label in ['CE-ITM','CE-OTM','PUT-ITM','PUT-OTM']:
    L=per_bot.get(label,[])
    if L: print(f"  {label:8} n={len(L):4}  median={pct(L,.5):5.1f}s  p90={pct(L,.9):5.1f}s")
print("\nPer-stage median / p90 (alert-status timestamps):")
for st in ['received→queued','queued→bot_processing_started','bot_processing_started→signal_validated','signal_validated→daily_limit_passed','daily_limit_passed→position_created']:
    L=stage.get(st,[])
    if L: print(f"  {st:45} {pct(L,.5):6.2f} / {pct(L,.9):6.2f}")

# ── ENTRY_TIMING sub-stage breakdown (from events.jsonl) — pinpoints WHAT to cut ──
sub=defaultdict(list)
for path,label in BOTS:
    for dt in dates:
        f=f"{ROOT}/{path}/logs/{dt}/events.jsonl"
        if not os.path.exists(f): continue
        for line in open(f):
            try: d=json.loads(line)
            except: continue
            if d.get('event_type')!='ENTRY_TIMING': continue
            ctx=d.get('context') or {}
            for k in ('chain_filter_ms','select_liquidity_ms','pricing_order_ms','total_ms','rate_limit_wait_ms'):
                v=ctx.get(k, d.get(k))
                if v is not None:
                    try: sub[k].append(float(v))
                    except (TypeError,ValueError): pass
if sub.get('total_ms'):
    print("\nENTRY_TIMING sub-stage breakdown (ms) — median / p90  [n={}]:".format(len(sub['total_ms'])))
    for k in ('chain_filter_ms','select_liquidity_ms','pricing_order_ms','rate_limit_wait_ms','total_ms'):
        L=sub.get(k,[])
        if L: print(f"  {k:22} {pct(L,.5):8.0f} / {pct(L,.9):8.0f}")
    print("  (chain+filter = spot+chain+entry-filter ; sel+liq = ATM select+market-data+OI ; pricing+order = place)")
else:
    print("\n(no ENTRY_TIMING events yet — appears after the next entries post-restart)")
