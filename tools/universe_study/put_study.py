"""PUT-side 20-day study — derive the PUT Pine from data (like CE's H12/H14), NOT by
blind-mirroring the CE script. Markets are asymmetric; every claim re-tested on the
bearish side from scratch.

One streaming pass over candles30 (210 symbols x 20 days) producing:
1. STATE STUDY: per (feature-bin, day) counts with the DOWN-label
   (first-touch -0.5% before +0.5% within 60min) over ALL bars 09:30-14:30 —
   which indicator states precede profitable PUT entries, per-day consistency.
2. CANDIDATES: every bar matching the PUT geometric patterns (fresh VWAP-line
   crossUNDER <=3 bars, or close < VWAP-1sigma), leg = resets when close > vwap,
   with bearish features + down-label + SHORT-side wide-trail return
   (stop +0.5% adverse, arm -1.0%, trail trough+0.8%, ride to 15:10).
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from features import wilder_rsi, wilder_adx, macd_hist
from pass1_baserates import resample, tf_ind

DATA = BASE / "data" / "candles30"
UP, DN, HORIZON = 0.5, 0.5, 60  # DOWN-label: -0.5 first vs +0.5

state = defaultdict(lambda: [0, 0])  # (feat, bin, day) -> [n, wins(down)]


def bump(feat, b, day, lab):
    c = state[(feat, b, day)]
    c[0] += 1
    c[1] += lab


def bin_rsi(v):
    if v is None: return "na"
    if v < 20: return "<20"
    if v < 32: return "20-32"
    if v < 45: return "32-45"
    if v < 55: return "45-55"
    return "55+"


def bin_signed(v, cuts, labels):
    if v is None: return "na"
    for c, lab in zip(cuts, labels):
        if v < c: return lab
    return labels[-1]


def down_label(bars, i, day):
    e = bars[i][4]
    for k in range(i + 1, min(i + 1 + HORIZON, len(bars))):
        if bars[k][0][:10] != day:
            break
        if (bars[k][2] / e - 1) * 100 >= UP:      # adverse first (high)
            return 0
        if (bars[k][3] / e - 1) * 100 <= -DN:     # favorable first (low)
            return 1
    return 0


def short_trail_ret(bars, i, day_last_i, day):
    """Short-side wide trail. Returns PUT-favorable %: positive when stock FELL."""
    e = bars[i][4]
    trough = e
    armed = False
    last = i
    for k in range(i + 1, day_last_i + 1):
        hi_p = (bars[k][2] / e - 1) * 100      # adverse
        trough = min(trough, bars[k][3])
        tp = (trough / e - 1) * 100            # favorable depth (negative)
        if not armed and hi_p >= 0.5:
            return -0.5
        if tp <= -1.0:
            armed = True
        if armed and hi_p >= tp + 0.8:
            return -(tp + 0.8)
        last = k
    return -((bars[last][4] / e - 1) * 100)


def main():
    cands = []
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
        hist1 = macd_hist(closes)
        rb5 = resample(bars, 5)
        f5 = tf_ind(rb5)
        by_day = defaultdict(list)
        for i, b in enumerate(bars):
            by_day[b[0][:10]].append(i)
        days = sorted(by_day)
        if len(days) < 4:
            continue
        dclose = {d: bars[by_day[d][-1]][4] for d in days}
        p5 = 0
        for day in days[2:]:
            idxs = by_day[day]
            pdc = dclose[days[days.index(day) - 1]]
            day_open = bars[idxs[0]][1]
            gap = (day_open / pdc - 1) * 100 if pdc else 0
            last_i = idxs[0]
            for i in idxs:
                if bars[i][0][11:16] <= "15:10":
                    last_i = i
            cum_pv = cum_v = cum_p2v = 0.0
            vwap_hist = []
            vols = []
            bsu = 9999   # bars since crossUNDER
            leg = 0
            for j, i in enumerate(idxs):
                t, o, h, l, c, v = bars[i]
                tp_ = (h + l + c) / 3
                vv = max(v, 1)
                cum_pv += tp_ * vv; cum_v += vv; cum_p2v += tp_ * tp_ * vv
                vwap = cum_pv / cum_v
                var = max(cum_p2v / cum_v - vwap * vwap, 0)
                lower = vwap - math.sqrt(var)
                vwap_hist.append(vwap)
                vols.append(v)
                prev_c = bars[idxs[j - 1]][4] if j > 0 else None
                crossed_under = j > 0 and prev_c is not None and prev_c >= vwap_hist[j - 1] and c < vwap
                bsu = 0 if crossed_under else (bsu + 1 if (c < vwap and bsu < 9999) else (bsu if c < vwap else 9999))
                if c > vwap:
                    leg += 1
                msm = int(t[11:13]) * 60 + int(t[14:16])
                if not (570 <= msm <= 870) or j < 5:
                    continue
                lab = down_label(bars, i, day)
                # ---- STATE STUDY on every bar (morning window only for the table) ----
                if msm < 660:
                    r5avg = sum(vols[-6:-1]) / 5 if j >= 5 else 0
                    c5b = bars[idxs[j - 5]][4]
                    while p5 + 1 < len(rb5) and (rb5[p5 + 1][7], rb5[p5 + 1][6]) <= (day, msm):
                        p5 += 1
                    ok5 = p5 < len(rb5) and rb5[p5][7] <= day and rb5[p5][6] <= msm
                    bump("ALL", "all", day, lab)
                    bump("run5m", bin_signed((c / c5b - 1) * 100, [-0.8, -0.4, -0.15, 0, 0.3], ["<-0.8", "-0.8--0.4", "-0.4--0.15", "-0.15-0", "0-0.3", "0.3+"]), day, lab)
                    bump("rsi1", bin_rsi(rsi1[i]), day, lab)
                    bump("rsi1_falling2", "Y" if (rsi1[i] is not None and rsi1[i-1] is not None and rsi1[i-2] is not None and rsi1[i] < rsi1[i-1] < rsi1[i-2]) else "N", day, lab)
                    bump("adx1", bin_signed(adx1[i], [20, 30, 40], ["<20", "20-30", "30-40", "40+"]), day, lab)
                    bump("adx1_rising2", "Y" if (adx1[i] is not None and adx1[i-1] is not None and adx1[i-2] is not None and adx1[i] > adx1[i-1] > adx1[i-2]) else "N", day, lab)
                    bump("hist1_neg", "Y" if (hist1[i] is not None and hist1[i] < 0) else "N", day, lab)
                    bump("hist1_falling2", "Y" if (hist1[i] is not None and hist1[i-1] is not None and hist1[i-2] is not None and hist1[i] < hist1[i-1] < hist1[i-2]) else "N", day, lab)
                    bump("below_vwap", "Y" if c < vwap else "N", day, lab)
                    bump("vwap_slope3", bin_signed((vwap / vwap_hist[j-3] - 1) * 100 if j >= 3 else None, [-0.08, -0.03, 0], ["<-0.08", "-0.08--0.03", "-0.03-0", "0+"]), day, lab)
                    bump("below_pdc", "Y" if c < pdc else "N", day, lab)
                    bump("gap", bin_signed(gap, [-0.75, -0.3, 0.3], ["<-0.75", "-0.75--0.3", "-0.3-0.3", "0.3+"]), day, lab)
                    bump("vol_ratio", bin_signed(v / r5avg if r5avg > 0 else None, [0.7, 1.5, 3], ["<0.7", "0.7-1.5", "1.5-3", "3+"]), day, lab)
                    bump("rsi5", bin_rsi(f5["rsi"][p5] if ok5 else None), day, lab)
                    bump("hist5_neg", "Y" if (ok5 and f5["hist"][p5] is not None and f5["hist"][p5] < 0) else "N", day, lab)
                # ---- CANDIDATES: bearish geometry ----
                fresh_under = bsu <= 3
                below_lower = c < lower
                if not (fresh_under or below_lower):
                    continue
                r5avg = sum(vols[-6:-1]) / 5 if j >= 5 else 0
                c5b = bars[idxs[j - 5]][4]
                while p5 + 1 < len(rb5) and (rb5[p5 + 1][7], rb5[p5 + 1][6]) <= (day, msm):
                    p5 += 1
                ok5 = p5 < len(rb5) and rb5[p5][7] <= day and rb5[p5][6] <= msm
                cands.append({
                    "sym": f.stem, "day": day, "hhmm": t[11:16], "leg": leg,
                    "pattern": "BREAKDOWN" if fresh_under else "CONT_DOWN",
                    "run5m": (c / c5b - 1) * 100,
                    "rsi": rsi1[i], "adx": adx1[i],
                    "rsi_falling2": 1 if (rsi1[i] is not None and rsi1[i-1] is not None and rsi1[i-2] is not None and rsi1[i] < rsi1[i-1] < rsi1[i-2]) else 0,
                    "adx_rising2": 1 if (adx1[i] is not None and adx1[i-1] is not None and adx1[i-2] is not None and adx1[i] > adx1[i-1] > adx1[i-2]) else 0,
                    "hist_neg": 1 if (hist1[i] is not None and hist1[i] < 0) else 0,
                    "hist_falling2": 1 if (hist1[i] is not None and hist1[i-1] is not None and hist1[i-2] is not None and hist1[i] < hist1[i-1] < hist1[i-2]) else 0,
                    "vwap_slope3": (vwap / vwap_hist[j-3] - 1) * 100 if j >= 3 else None,
                    "below_pdc": 1 if c < pdc else 0,
                    "gap": gap,
                    "vol_ratio": v / r5avg if r5avg > 0 else None,
                    "rsi5": f5["rsi"][p5] if ok5 else None,
                    "hist5_neg": 1 if (ok5 and f5["hist"][p5] is not None and f5["hist"][p5] < 0) else 0,
                    "label": lab,
                    "ret_put": short_trail_ret(bars, i, last_i, day),
                })
        if (fi + 1) % 50 == 0:
            print(f"  {fi+1}/{len(files)}", flush=True)

    json.dump({"|".join(k): v for k, v in state.items()}, open(BASE / "data" / "put_state_counts.json", "w"))
    json.dump(cands, open(BASE / "data" / "put_candidates.json", "w"))
    print(f"state cells: {len(state)} | PUT candidates: {len(cands)}")


if __name__ == "__main__":
    main()
