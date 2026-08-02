"""PASS 1 — unconditional indicator base-rate study over ~21 trading days.

For EVERY 1-min bar (09:30-14:30, no VWAP-geometry precondition) of every F&O
underlying: label = first-touch +0.5% before -0.5% within 60 min (calibrated to
real trade outcomes); compute Pine-computable indicator states on 1m/5m/15m;
accumulate (feature, bin, 30-min window, day) -> (n, wins) counts.

Streaming: one symbol in memory at a time (2GB VPS shared with live bots).
Skips the first 2 sessions of each symbol's series for indicator warmup.
Output: data/pass1_counts.json
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from features import wilder_rsi, wilder_adx, macd_hist, ema_series

DATA = BASE / "data" / "candles30"
UP, DN, HORIZON = 0.5, 0.5, 60


def resample(bars, n):
    out = []
    cur = None
    ck = None
    for t, o, h, l, c, v in bars:
        day = t[:10]
        msm = int(t[11:13]) * 60 + int(t[14:16])
        key = (day, (msm - 555) // n)
        if key != ck:
            if cur:
                out.append(cur)
            cur = [t, o, h, l, c, v, msm + n, day]
            ck = key
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[5] += v
    if cur:
        out.append(cur)
    return out


def tf_ind(rb):
    closes = [b[4] for b in rb]
    highs = [b[2] for b in rb]
    lows = [b[3] for b in rb]
    return {
        "rsi": wilder_rsi(closes),
        "adx": wilder_adx(highs, lows, closes),
        "hist": macd_hist(closes),
        "ema20": ema_series(closes, 20),
        "closes": closes,
    }


def bin_rsi(v):
    if v is None: return "na"
    if v < 40: return "<40"
    if v < 50: return "40-50"
    if v < 60: return "50-60"
    if v < 70: return "60-70"
    if v < 80: return "70-80"
    return "80+"


def bin_adx(v):
    if v is None: return "na"
    if v < 20: return "<20"
    if v < 30: return "20-30"
    if v < 40: return "30-40"
    return "40+"


def bin_signed(v, cuts, labels):
    if v is None: return "na"
    for c, lab in zip(cuts, labels):
        if v < c: return lab
    return labels[-1]


def wbucket(msm):
    slot = (msm - 570) // 30
    start = 570 + slot * 30
    return f"{start//60:02d}:{start%60:02d}"


def main():
    counts = defaultdict(lambda: [0, 0])  # (feat,bin,window,day) -> [n,wins]
    files = sorted(DATA.glob("*.json"))
    print(f"symbols: {len(files)}")
    for fi, f in enumerate(files):
        bars = json.load(open(f))
        if len(bars) < 2000:
            continue
        closes = [b[4] for b in bars]
        highs = [b[2] for b in bars]
        lows = [b[3] for b in bars]
        rsi1 = wilder_rsi(closes)
        adx1 = wilder_adx(highs, lows, closes)
        hist1 = macd_hist(closes)
        rb5, rb15 = resample(bars, 5), resample(bars, 15)
        f5, f15 = tf_ind(rb5), tf_ind(rb15)
        # last-closed-bar pointers built incrementally
        by_day = defaultdict(list)
        for i, b in enumerate(bars):
            by_day[b[0][:10]].append(i)
        days = sorted(by_day)
        if len(days) < 4:
            continue
        analysis_days = days[2:]  # warmup skip
        daily_close = {d: bars[by_day[d][-1]][4] for d in days}
        p5 = p15 = 0
        for d_i, day in enumerate(analysis_days):
            idxs = by_day[day]
            prev_day = days[days.index(day) - 1]
            pdc = daily_close[prev_day]
            day_open = bars[idxs[0]][1]
            gap = (day_open / pdc - 1) * 100 if pdc else None
            cum_pv = cum_v = cum_p2v = 0.0
            vwap_hist = []
            vols = []
            for j, i in enumerate(idxs):
                t, o, h, l, c, v = bars[i]
                tp = (h + l + c) / 3
                vv = max(v, 1)
                cum_pv += tp * vv; cum_v += vv; cum_p2v += tp * tp * vv
                vwap = cum_pv / cum_v
                vwap_hist.append(vwap)
                vols.append(v)
                msm = int(t[11:13]) * 60 + int(t[14:16])
                if msm < 570 or msm > 870 or j < 5:
                    continue
                # label (forward, same symbol arrays)
                e = c
                lab = 0
                for k in range(i + 1, min(i + 1 + HORIZON, len(bars))):
                    if bars[k][0][:10] != day:
                        break
                    if (bars[k][3] / e - 1) * 100 <= -DN:
                        lab = 0
                        break
                    if (bars[k][2] / e - 1) * 100 >= UP:
                        lab = 1
                        break
                # advance TF pointers to last CLOSED bar
                while p5 + 1 < len(rb5) and (rb5[p5 + 1][7], rb5[p5 + 1][6]) <= (day, msm):
                    p5 += 1
                while p15 + 1 < len(rb15) and (rb15[p15 + 1][7], rb15[p15 + 1][6]) <= (day, msm):
                    p15 += 1
                ok5 = rb5[p5][7] <= day and rb5[p5][6] <= msm if p5 < len(rb5) else False
                ok15 = rb15[p15][7] <= day and rb15[p15][6] <= msm if p15 < len(rb15) else False
                r5avg = sum(vols[-6:-1]) / 5 if j >= 5 else 0
                c5b = bars[idxs[j - 5]][4]
                w = wbucket(msm)
                feats = {
                    "rsi1": bin_rsi(rsi1[i]),
                    "rsi1_rising2": "Y" if (rsi1[i] and rsi1[i-1] and rsi1[i-2] and rsi1[i] > rsi1[i-1] > rsi1[i-2]) else "N",
                    "adx1": bin_adx(adx1[i]),
                    "adx1_rising2": "Y" if (adx1[i] and adx1[i-1] and adx1[i-2] and adx1[i] > adx1[i-1] > adx1[i-2]) else "N",
                    "hist1": "pos" if (hist1[i] is not None and hist1[i] > 0) else ("neg" if hist1[i] is not None else "na"),
                    "hist1_rising2": "Y" if (hist1[i] is not None and hist1[i-1] is not None and hist1[i-2] is not None and hist1[i] > hist1[i-1] > hist1[i-2]) else "N",
                    "above_vwap": "Y" if c > vwap else "N",
                    "ext_vwap": bin_signed((c / vwap - 1) * 100, [-0.5, 0, 0.3, 0.8, 1.5], ["<-0.5", "-0.5-0", "0-0.3", "0.3-0.8", "0.8-1.5", "1.5+"]),
                    "vwap_slope3": bin_signed((vwap / vwap_hist[j-3] - 1) * 100 if j >= 3 else None, [0, 0.03, 0.08], ["<0", "0-0.03", "0.03-0.08", "0.08+"]),
                    "vol_ratio": bin_signed(v / r5avg if r5avg > 0 else None, [0.7, 1.5, 3], ["<0.7", "0.7-1.5", "1.5-3", "3+"]),
                    "run5m": bin_signed((c / c5b - 1) * 100, [-0.3, 0, 0.15, 0.4, 0.8], ["<-0.3", "-0.3-0", "0-0.15", "0.15-0.4", "0.4-0.8", "0.8+"]),
                    "mfo": bin_signed((c / day_open - 1) * 100, [-0.5, 0, 0.75, 1.5], ["<-0.5", "-0.5-0", "0-0.75", "0.75-1.5", "1.5+"]),
                    "gap": bin_signed(gap, [-0.3, 0.3, 0.75], ["<-0.3", "-0.3-0.3", "0.3-0.75", "0.75+"]),
                    "above_pdc": "Y" if c > pdc else "N",
                    "rsi5": bin_rsi(f5["rsi"][p5] if ok5 else None),
                    "adx5": bin_adx(f5["adx"][p5] if ok5 else None),
                    "hist5": "pos" if (ok5 and f5["hist"][p5] is not None and f5["hist"][p5] > 0) else ("neg" if (ok5 and f5["hist"][p5] is not None) else "na"),
                    "ema20_5m": "above" if (ok5 and f5["ema20"][p5] is not None and f5["closes"][p5] > f5["ema20"][p5]) else ("below" if (ok5 and f5["ema20"][p5] is not None) else "na"),
                    "rsi15": bin_rsi(f15["rsi"][p15] if ok15 else None),
                    "hist15": "pos" if (ok15 and f15["hist"][p15] is not None and f15["hist"][p15] > 0) else ("neg" if (ok15 and f15["hist"][p15] is not None) else "na"),
                    "ema20_15m": "above" if (ok15 and f15["ema20"][p15] is not None and f15["closes"][p15] > f15["ema20"][p15]) else ("below" if (ok15 and f15["ema20"][p15] is not None) else "na"),
                    "ALL": "all",  # base rate
                }
                for fk, fb in feats.items():
                    cell = counts[(fk, fb, w, day)]
                    cell[0] += 1
                    cell[1] += lab
        if (fi + 1) % 25 == 0:
            print(f"  {fi+1}/{len(files)} symbols done", flush=True)
    out = {"|".join(k): v for k, v in counts.items()}
    json.dump(out, open(BASE / "data" / "pass1_counts.json", "w"))
    print(f"cells: {len(out)} -> data/pass1_counts.json")


if __name__ == "__main__":
    main()
