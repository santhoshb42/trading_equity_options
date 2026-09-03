#!/usr/bin/env python3
"""Shadow-test the upper-wick entry theory. LOGS ONLY -- changes no bot behaviour.

THEORY (2026-09-03): CE entries taken when the last COMPLETED 1-min bar before entry had a
large upper wick are entries into a fade -- price was already rejected off its highs. Measured
over 787 CE trades / 9 sessions:

    upper wick <= 10%                 235 tr   +Rs 47/trade   44% win
    upper wick  > 10%                 552 tr   -Rs127/trade
    wick <=10% AND first 120 min      133 tr   +Rs116/trade   49% win
    all CE trades                     787 tr   -Rs 75/trade   39% win

Held on 7 of 9 days (median edge +Rs148/tr) but FAILED on the two most recent sessions
(09-02, 09-03), which is exactly why this runs in shadow instead of as a live filter.

Also measured and REJECTED: no single candle parameter separates winners from losers.
All 47 scored AUC 0.44-0.56 once look-ahead was removed. An earlier pass showed AUC 0.705
for rsi_slope/ret_1m -- that was leakage from reading the entry-MINUTE bar, which does not
close until after the entry. This script only ever reads bars that closed BEFORE entry.

USAGE
    python3 tools/wick_shadow_eval.py              # log today, then print the running verdict
    python3 tools/wick_shadow_eval.py 2026-09-01   # log a specific day
    python3 tools/wick_shadow_eval.py --report     # verdict only, no fetching

Writes tools/wick_shadow_log.jsonl (one row per CE trade, all 47 features) and caches raw
candles under tools/wick_shadow_candles/<date>.json so the log can be rebuilt without
re-hitting the broker.
"""
import json, os, sys, time, math, collections, datetime
import statistics as st
from pathlib import Path

ROOT = Path("/root/santhosh/trading")
LOG = ROOT / "tools" / "wick_shadow_log.jsonl"
CANDLE_DIR = ROOT / "tools" / "wick_shadow_candles"
BOTS = [("CE-ITM", ROOT / "CE_OPTIONS/ITM"), ("CE-OTM", ROOT / "CE_OPTIONS/OTM")]


# ---------------------------------------------------------------- trades
def load_trades(day):
    out = []
    for bot, bd in BOTS:
        p = bd / "data/option_pnl_history.json"
        if not p.exists():
            continue
        try:
            recs = json.load(open(p))
        except Exception:
            continue
        for r in recs:
            if r.get("gross_pnl") is None:
                continue
            if not (r.get("entry_time") or "").startswith(day):
                continue
            ep = r.get("entry_premium")
            out.append(dict(
                key=f"{bot}|{r.get('symbol')}|{r.get('entry_time')}",
                bot=bot, day=day, tm=(r.get("entry_time") or "")[11:16],
                und=r.get("underlying"), sym=r.get("symbol"),
                entry_type=(r.get("entry_context") or {}).get("entry_type") or "-",
                prem=ep, qty=r.get("quantity"),
                net=r.get("pnl") or 0.0, gross=r.get("gross_pnl") or 0.0,
                charges=r.get("charges") or 0.0,
                peak_pct=(((r.get("highest_premium") or ep) - ep) / ep * 100) if ep else None,
                exit_reason=(r.get("exit_reason") or "").split("(")[0].strip(),
            ))
    return out


# ---------------------------------------------------------------- candles
def fetch_candles(unds, day):
    """One 1-min fetch per underlying. Run after the close; throttled either way."""
    CANDLE_DIR.mkdir(parents=True, exist_ok=True)
    path = CANDLE_DIR / f"{day}.json"
    store = json.load(open(path)) if path.exists() else {}
    todo = [u for u in unds if u and u not in store]
    if not todo:
        return store
    os.environ.setdefault("BOT_MODE", "OTM")
    sys.path.insert(0, str(ROOT / "CE_OPTIONS"))
    os.chdir(ROOT / "CE_OPTIONS")
    from dotenv import load_dotenv
    load_dotenv("tools/.env")
    from optcode.angelone_options import AngelOneOptionsBroker
    b = AngelOneOptionsBroker()
    for a in range(6):
        try:
            if b.authenticate(is_retry=a > 0):
                break
        except Exception:
            pass
        time.sleep(min(2 ** a, 32))
    for i, u in enumerate(todo):
        got = None
        for att in range(3):
            try:
                got = b.get_historical_data(u, "ONE_MINUTE", days_back=3, force_refresh=True)
            except Exception:
                got = None
            if got:
                break
            time.sleep(1.5 * (att + 1))
        if got:
            store[u] = [[str(c["timestamp"])[:16].replace(" ", "T"),
                         c["open"], c["high"], c["low"], c["close"], c["volume"]] for c in got]
        time.sleep(0.7)
        if (i + 1) % 25 == 0:
            json.dump(store, open(path, "w"))
    json.dump(store, open(path, "w"))
    return store


# ---------------------------------------------------------------- features
def ema(v, n):
    if not v:
        return None
    k = 2 / (n + 1); e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def rsi(cl, n=14):
    if len(cl) < n + 1:
        return None
    g = []; l = []
    for i in range(1, len(cl)):
        d = cl[i] - cl[i - 1]; g.append(max(d, 0)); l.append(max(-d, 0))
    ag = sum(g[:n]) / n; al = sum(l[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n; al = (al * (n - 1) + l[i]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def sma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None


def features(bars, day, tm):
    """All features come from bars that CLOSED STRICTLY BEFORE the entry minute."""
    key = f"{day}T{tm}"
    idx = None
    for i, b in enumerate(bars):
        if b[0] < key:
            idx = i
        else:
            break
    if idx is None or idx < 3:
        return None
    o, h, l, c, v = bars[idx][1:6]
    cl = [b[4] for b in bars[:idx + 1]]; hi = [b[2] for b in bars[:idx + 1]]
    lo = [b[3] for b in bars[:idx + 1]]; vo = [b[5] for b in bars[:idx + 1]]
    op = [b[1] for b in bars[:idx + 1]]
    day_h = max(hi); day_l = min(lo); day_o = op[0]; rng = day_h - day_l

    def ret(n):
        base = cl[max(0, idx - n)]
        return (c - base) / base * 100 if base else None

    tp = [(hi[i] + lo[i] + cl[i]) / 3 for i in range(len(cl))]
    cumv = sum(vo); vwap = (sum(tp[i] * vo[i] for i in range(len(cl))) / cumv) if cumv else None
    trs = [max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])) for i in range(1, len(cl))]
    atr = sma(trs, 14)
    rets = [(cl[i] - cl[i - 1]) / cl[i - 1] * 100 for i in range(max(1, idx - 30), idx + 1) if cl[i - 1]]
    up = 0
    for i in range(idx, 0, -1):
        if cl[i] > cl[i - 1]:
            up += 1
        else:
            break
    aos = []
    for i in range(len(cl)):
        if i >= 34:
            hl = [(hi[j] + lo[j]) / 2 for j in range(i - 33, i + 1)]
            aos.append(sum(hl[-5:]) / 5 - sum(hl) / 34)
    e9 = ema(cl[-60:], 9); e21 = ema(cl[-60:], 21)
    m12 = ema(cl, 12); m26 = ema(cl, 26)
    s20 = sma(cl, 20); sd20 = st.pstdev(cl[-20:]) if len(cl) >= 20 else None
    hh = max(hi[-14:]) if len(hi) >= 14 else None
    ll = min(lo[-14:]) if len(lo) >= 14 else None
    ma20 = sma(tp, 20)
    md = (sum(abs(x - ma20) for x in tp[-20:]) / 20) if ma20 else None
    v20 = sma(vo, 20)
    return dict(
        bar_time=bars[idx][0], minute_of_session=idx,
        upper_wick_pct=((h - max(o, c)) / (h - l) * 100) if h > l else None,
        lower_wick_pct=((min(o, c) - l) / (h - l) * 100) if h > l else None,
        body_pct=(abs(c - o) / (h - l) * 100) if h > l else None,
        close_pos_in_bar=((c - l) / (h - l) * 100) if h > l else None,
        bar_range_pct=(h - l) / c * 100,
        bar_dir=1 if c > o else (-1 if c < o else 0),
        pos_in_day_range=((c - day_l) / rng * 100) if rng else None,
        dist_day_high_pct=(day_h - c) / c * 100,
        dist_day_open_pct=(c - day_o) / day_o * 100,
        ret_1m=ret(1), ret_3m=ret(3), ret_5m=ret(5), ret_10m=ret(10),
        ret_15m=ret(15), ret_30m=ret(30),
        consec_up=up,
        rvol_10=st.pstdev(rets[-10:]) if len(rets) >= 10 else None,
        rvol_30=st.pstdev(rets) if len(rets) > 3 else None,
        atr_pct=(atr / c * 100) if atr else None,
        range_exp=((h - l) / (sum(hi[i] - lo[i] for i in range(max(0, idx - 14), idx)) / 14)) if idx >= 14 else None,
        vol_surge=(v / v20) if v20 else None,
        vol_trend=(sma(vo, 5) / v20) if (v20 and sma(vo, 5)) else None,
        rsi14=rsi(cl[-100:]),
        rsi_slope=(rsi(cl[-100:]) - rsi(cl[-103:-3])) if len(cl) > 103 else None,
        ao=aos[-1] if aos else None,
        ao_delta=(aos[-1] - aos[-2]) if len(aos) > 1 else None,
        ema_spread_pct=((e9 - e21) / c * 100) if (e9 and e21) else None,
        macd_pct=((m12 - m26) / c * 100) if (m12 and m26) else None,
        vwap_dist_pct=((c - vwap) / vwap * 100) if vwap else None,
        above_vwap=1 if (vwap and c > vwap) else 0,
        bb_pos=((c - s20) / (2 * sd20)) if (s20 and sd20) else None,
        stoch=((c - ll) / (hh - ll) * 100) if (hh and ll and hh > ll) else None,
        cci=((tp[-1] - ma20) / (0.015 * md)) if (ma20 and md) else None,
        sma20_dist_pct=((c - s20) / s20 * 100) if s20 else None,
        is_day_high=1 if c >= day_h - 1e-9 else 0,
        bars_since_day_high=idx - max(range(idx + 1), key=lambda i: hi[i]),
    )


# ---------------------------------------------------------------- log
def existing_keys():
    if not LOG.exists():
        return set()
    ks = set()
    for line in open(LOG, errors="ignore"):
        try:
            ks.add(json.loads(line).get("key"))
        except Exception:
            continue
    return ks


def log_day(day):
    trades = load_trades(day)
    if not trades:
        print(f"  {day}: no closed CE trades"); return 0
    have = existing_keys()
    todo = [t for t in trades if t["key"] not in have]
    if not todo:
        print(f"  {day}: all {len(trades)} trades already logged"); return 0
    store = fetch_candles(sorted({t["und"] for t in todo if t.get("und")}), day)
    n = miss = 0
    with open(LOG, "a") as fh:
        for t in todo:
            bars = store.get(t.get("und") or "")
            f = features(sorted(bars, key=lambda z: z[0]), day, t["tm"]) if bars else None
            if not f:
                miss += 1
                continue
            row = dict(t); row.update(f)
            row["logged_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            fh.write(json.dumps(row) + "\n"); n += 1
    print(f"  {day}: logged {n} trades ({miss} skipped, no candles)")
    return n


# ---------------------------------------------------------------- verdict
def report():
    if not LOG.exists():
        print("  no log yet"); return
    rows = []
    for line in open(LOG, errors="ignore"):
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows = [r for r in rows if r.get("upper_wick_pct") is not None]
    if not rows:
        print("  no usable rows yet"); return
    days = sorted({r["day"] for r in rows})
    print(f"\n  === UPPER-WICK SHADOW VERDICT ===")
    print(f"  {len(rows)} CE trades over {len(days)} sessions ({days[0]} .. {days[-1]})\n")

    def blk(sel, label):
        if not sel:
            return
        n = len(sel); net = sum(x["net"] for x in sel)
        w = sum(1 for x in sel if x["net"] > 0)
        print(f"  {label:38} n={n:4}  net {net:>+9,.0f}  ({net/n:>+6,.0f}/tr)  win {w/n*100:3.0f}%")

    blk(rows, "ALL CE trades")
    for thr in (10, 15, 20):
        blk([r for r in rows if r["upper_wick_pct"] <= thr], f"upper wick <= {thr}%  (KEEP)")
        blk([r for r in rows if r["upper_wick_pct"] > thr], f"upper wick  > {thr}%  (would cut)")
    blk([r for r in rows if r["upper_wick_pct"] <= 10 and (r.get("minute_of_session") or 0) <= 120],
        "wick <=10% AND first 120 min")

    print("\n  per-day (keep wick<=10 vs cut):")
    ok = tot = 0; edges = []
    for d in days:
        a = [r for r in rows if r["day"] == d and r["upper_wick_pct"] <= 10]
        b = [r for r in rows if r["day"] == d and r["upper_wick_pct"] > 10]
        if len(a) < 5 or len(b) < 5:
            print(f"     {d}  (too few trades on one side)"); continue
        ma = sum(x["net"] for x in a) / len(a); mb = sum(x["net"] for x in b) / len(b)
        tot += 1; ok += 1 if ma > mb else 0; edges.append(ma - mb)
        print(f"     {d}  keep {ma:>+8,.0f}/tr (n{len(a):>3})   cut {mb:>+8,.0f}/tr (n{len(b):>3})   "
              f"edge {ma-mb:>+8,.0f}  {'OK' if ma > mb else 'x'}")
    if tot:
        print(f"     -> theory held on {ok}/{tot} days, median edge {st.median(edges):+,.0f}/tr")
        print(f"     -> BASELINE to beat: 7/9 days, median +148/tr (2026-08-24..09-03, pre-shadow)")

    print("\n  by strategy (wick<=10 vs >10):")
    for et in sorted({r.get("entry_type") for r in rows}):
        a = [r for r in rows if r.get("entry_type") == et and r["upper_wick_pct"] <= 10]
        b = [r for r in rows if r.get("entry_type") == et and r["upper_wick_pct"] > 10]
        if len(a) < 5 or len(b) < 5:
            continue
        print(f"     {et:14} keep n={len(a):4} {sum(x['net'] for x in a)/len(a):>+7,.0f}/tr"
              f"   cut n={len(b):4} {sum(x['net'] for x in b)/len(b):>+7,.0f}/tr")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--report" in args:
        report(); sys.exit(0)
    days = [a for a in args if a.startswith("20")] or [datetime.date.today().isoformat()]
    for d in days:
        log_day(d)
    report()
