#!/usr/bin/env python3
"""Join TradingView alert payloads (alerts.jsonl) to booked trade outcomes
(option_pnl_history.json) so any alert field can be scored against real P&L.

  python3 tools/alert_trade_join.py [YYYY-MM-DD ...]     # default: today

Matches on underlying + nearest alert within ALERT_WINDOW_S before entry.
Prints per-field breakdowns; writes the joined rows to /tmp/an/joined.json.
"""
import json,glob,os,sys,collections,statistics as st,datetime
ALERT_WINDOW_S=180
BOTS=[('CE-ITM','CE_OPTIONS/ITM'),('CE-OTM','CE_OPTIONS/OTM'),
      ('PE-ITM','PUT_OPTIONS/ITM'),('PE-OTM','PUT_OPTIONS/OTM')]
days=sys.argv[1:] or [datetime.date.today().isoformat()]

alerts=collections.defaultdict(list)
for name,bd in BOTS:
    for d in days:
        f=f"{bd}/logs/{d}/alerts.jsonl"
        if not os.path.exists(f): continue
        for l in open(f):
            try: r=json.loads(l)
            except: continue
            a=r.get("alert") or {}
            if not a.get("symbol"): continue
            try: ts=datetime.datetime.fromisoformat(r["timestamp"])
            except: continue
            alerts[(name,a["symbol"])].append((ts,a))
for v in alerts.values(): v.sort(key=lambda x:x[0])

rows=[]
for name,bd in BOTS:
    for r in json.load(open(f"{bd}/data/option_pnl_history.json")):
        t=r.get("entry_time") or ""
        if t[:10] not in days or r.get("gross_pnl") is None: continue
        try: et=datetime.datetime.fromisoformat(t)
        except: continue
        best=None
        for ts,a in alerts.get((name,r.get("underlying")),[]):
            dt=(et-ts).total_seconds()
            if 0<=dt<=ALERT_WINDOW_S and (best is None or dt<best[0]): best=(dt,a)
        if not best: continue
        a=best[1]
        def f(k):
            try: return float(a.get(k))
            except: return None
        rows.append(dict(bot=name, sym=r["symbol"], und=r.get("underlying"), day=t[:10], tm=t[11:16],
            side=r.get("contract_type"), gross=r.get("gross_pnl") or 0.0, net=r.get("pnl") or 0.0,
            chg=r.get("charges") or 0.0, peak=r.get("peak_pct"),
            reason=(r.get("exit_reason") or "").split("(")[0].strip(),
            entry_type=a.get("entry_type"), trigger=a.get("trigger"),
            label=a.get("tv_setup_label"), bars_since=f("bars_since_cross"),
            vwap_z=f("vwap_z"), vwap_slope_pct=f("vwap_slope_pct"), vwap_dist_pct=f("vwap_dist_pct"),
            day_change_pct=f("day_change_pct"), ao_ratio=f("ao_ratio"), ao_force=f("ao_force"),
            adx=f("adx"), adx_slope=f("adx_slope"), rsi=f("rsi_value"), rsi_slope=f("rsi_slope"),
            macd_slope=f("macd_slope"), vol_ratio=f("vol_ratio"), atr_pct=f("atr_pct")))
os.makedirs("/tmp/an",exist_ok=True)
json.dump(rows,open("/tmp/an/joined.json","w"))
print(f"days: {days}")
print(f"joined trades: {len(rows)}   gross Rs {sum(r['gross'] for r in rows):+,.0f}   net Rs {sum(r['net'] for r in rows):+,.0f}\n")
if not rows: sys.exit()

def grp(title,keyfn,minn=4):
    a=collections.defaultdict(list)
    for r in rows:
        k=keyfn(r)
        if k is not None: a[k].append(r)
    if not a: return
    print(f"  --- {title} ---")
    print(f"    {'bucket':<26} {'n':>4} {'gross/tr':>10} {'net/tr':>9} {'win%':>6} {'med peak':>9}")
    for k,v in sorted(a.items(), key=lambda x:-(sum(y['gross'] for y in x[1])/len(x[1]))):
        if len(v)<minn: continue
        w=sum(1 for x in v if x["gross"]>0)
        pk=[x["peak"] for x in v if x["peak"] is not None]
        print(f"    {str(k):<26} {len(v):>4} {sum(x['gross'] for x in v)/len(v):>+10,.0f} "
              f"{sum(x['net'] for x in v)/len(v):>+9,.0f} {w/len(v)*100:>5.0f}% {st.median(pk) if pk else 0:>8.2f}%")
    print()
grp("ENTRY TYPE + side", lambda r: f"{r['entry_type']} {r['side']}")
grp("TRIGGER (zero-cross vs pullback)", lambda r: f"{r['trigger']} {r['side']}")
grp("tv_setup_label", lambda r: r["label"])
def band(v,edges,lbls):
    if v is None: return None
    for (lo,hi),l in zip(edges,lbls):
        if lo<=v<hi: return l
    return None
grp("VWAP z at entry", lambda r: band(r["vwap_z"],[(-99,-2),(-2,-1),(-1,0),(0,1),(1,2),(2,99)],
      ["z <= -2","-2..-1","-1..0","0..+1","+1..+2","z >= +2"]))
grp("VWAP slope % (60 bars)", lambda r: band(r["vwap_slope_pct"],[(-99,-0.15),(-0.15,-0.05),(-0.05,0.05),(0.05,0.15),(0.15,99)],
      ["<= -0.15%","-0.15..-0.05","-0.05..+0.05 flat","+0.05..+0.15",">= +0.15%"]))
grp("AO force (cross-force multiple)", lambda r: band(abs(r["ao_force"]) if r["ao_force"] is not None else None,
      [(0,1),(1,1.5),(1.5,3),(3,999)],["|force| <1","1-1.5","1.5-3",">3"]))
grp("bars since cross", lambda r: None if r["bars_since"] is None else ("0 (at cross)" if r["bars_since"]==0 else ("1-2" if r["bars_since"]<=2 else "3+")))
grp("day change % at entry", lambda r: band(r["day_change_pct"],[(-99,-1),(-1,-0.3),(-0.3,0.3),(0.3,1),(1,99)],
      ["<= -1%","-1..-0.3","-0.3..+0.3","+0.3..+1",">= +1%"]))
