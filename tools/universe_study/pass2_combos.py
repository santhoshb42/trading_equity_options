"""PASS 2 — exact conjunction search over the pass-1 stable conditions.

10 binary conditions per bar -> 1024 masks; accumulate (mask, window, day) counts.
Any AND-rule = subset sum over masks. Morning windows only (pass 1: midday dead,
consistent with 7-day study). Same streaming/label discipline as pass 1.
Output: data/pass2_masks.json
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from features import wilder_rsi, wilder_adx, macd_hist, ema_series
from pass1_baserates import resample, tf_ind, wbucket

DATA = BASE / "data" / "candles30"
UP, DN, HORIZON = 0.5, 0.5, 60

COND_NAMES = [
    "slope>=0.08",   # 0
    "run5m>=0.4",    # 1
    "adx1>=30",      # 2
    "adx1>=40",      # 3
    "rsi1>=68",      # 4
    "vol>=1.5",      # 5
    "hist5_pos",     # 6
    "rsi5>=65",      # 7
    "ext>=0.3",      # 8
    "above_pdc",     # 9
]


def main():
    counts = defaultdict(lambda: [0, 0])  # (mask, window, day) -> [n, wins]
    files = sorted(DATA.glob("*.json"))
    for fi, f in enumerate(files):
        bars = json.load(open(f))
        if len(bars) < 2000:
            continue
        closes = [b[4] for b in bars]
        highs = [b[2] for b in bars]
        lows = [b[3] for b in bars]
        rsi1 = wilder_rsi(closes)
        adx1 = wilder_adx(highs, lows, closes)
        rb5 = resample(bars, 5)
        f5 = tf_ind(rb5)
        by_day = defaultdict(list)
        for i, b in enumerate(bars):
            by_day[b[0][:10]].append(i)
        days = sorted(by_day)
        if len(days) < 4:
            continue
        daily_close = {d: bars[by_day[d][-1]][4] for d in days}
        p5 = 0
        for day in days[2:]:
            idxs = by_day[day]
            pdc = daily_close[days[days.index(day) - 1]]
            cum_pv = cum_v = 0.0
            vwap_hist = []
            vols = []
            for j, i in enumerate(idxs):
                t, o, h, l, c, v = bars[i]
                tp = (h + l + c) / 3
                vv = max(v, 1)
                cum_pv += tp * vv; cum_v += vv
                vwap = cum_pv / cum_v
                vwap_hist.append(vwap)
                vols.append(v)
                msm = int(t[11:13]) * 60 + int(t[14:16])
                if not (570 <= msm < 660) or j < 5:   # morning window only 09:30-11:00
                    continue
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
                while p5 + 1 < len(rb5) and (rb5[p5 + 1][7], rb5[p5 + 1][6]) <= (day, msm):
                    p5 += 1
                ok5 = p5 < len(rb5) and rb5[p5][7] <= day and rb5[p5][6] <= msm
                r5avg = sum(vols[-6:-1]) / 5 if j >= 5 else 0
                c5b = bars[idxs[j - 5]][4]
                slope = (vwap / vwap_hist[j - 3] - 1) * 100 if j >= 3 else -1
                conds = [
                    slope >= 0.08,
                    (c / c5b - 1) * 100 >= 0.4,
                    (adx1[i] or 0) >= 30,
                    (adx1[i] or 0) >= 40,
                    (rsi1[i] or 0) >= 68,
                    (v / r5avg if r5avg > 0 else 0) >= 1.5,
                    ok5 and f5["hist"][p5] is not None and f5["hist"][p5] > 0,
                    ok5 and (f5["rsi"][p5] or 0) >= 65,
                    (c / vwap - 1) * 100 >= 0.3,
                    c > pdc,
                ]
                mask = 0
                for bi, cv in enumerate(conds):
                    if cv:
                        mask |= (1 << bi)
                cell = counts[(mask, wbucket(msm), day)]
                cell[0] += 1
                cell[1] += lab
        if (fi + 1) % 50 == 0:
            print(f"  {fi+1}/{len(files)}", flush=True)
    out = {f"{m}|{w}|{d}": v for (m, w, d), v in counts.items()}
    json.dump({"cond_names": COND_NAMES, "cells": out}, open(BASE / "data" / "pass2_masks.json", "w"))
    print(f"cells: {len(out)}")


if __name__ == "__main__":
    main()
