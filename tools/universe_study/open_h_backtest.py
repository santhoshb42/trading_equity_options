"""Decide the open H's (H1, H2, H3, H5, H6, H7b) on 20 days × 210 symbols instead of
waiting for more live days.

Population: bars matching the v27-era entry logic (what H1-H6 are hypotheses ABOUT):
  BREAKOUT: fresh VWAP-line cross (<=3 bars) + rsi_rising2 + hist_rising2 + vwap_slope>0 + >PDC
  CONT:     close > VWAP+1sigma + ADX>=20 + adx_rising2 + rsi_not_rolling3 + >PDC
First passing bar per leg (Pine latch). Label: first-touch +0.5%/-0.5%/60min (calibrated).
Window: 09:30-14:30 (morning + afternoon so H6 is answerable).

H7b: per-minute NIFTY daemon state (15-min trend + 5-min recovery, exact daemon thresholds)
joined to each signal minute -> would H7b have blocked it, and was blocking right?
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from features import wilder_rsi, wilder_adx, macd_hist
from pass1_baserates import resample, tf_ind

DATA = BASE / "data" / "candles30"
UP, DN, HORIZON = 0.5, 0.5, 60

# ---- NIFTY per-minute H7b state (exact daemon logic) ----
nifty = json.load(open(BASE / "data" / "nifty_1min.json"))
nifty_by_day = defaultdict(list)
for t, o, h, l, c in nifty:
    nifty_by_day[t[:10]].append((t[11:16], o, c))
h7b_state = {}
for day, rows in nifty_by_day.items():
    closes = []
    opens = []
    for hhmm, o, c in rows:
        closes.append(c)
        opens.append(o)
        i = len(closes)
        if i < 15:
            continue
        w_o = opens[i - 15]
        w_c = closes[-1]
        path = sum(abs(closes[k] - opens[k]) for k in range(i - 15, i))
        eff = abs(w_c - w_o) / path * 100 if path else 0
        net = (w_c - w_o) / w_o * 100
        trend = "GOOD" if (eff >= 12 and net > 0) else ("BAD" if (eff >= 12 and net < 0) else "NEUTRAL")
        short_net = (closes[-1] - opens[i - 5]) / opens[i - 5] * 100 if i >= 5 else None
        recovering = short_net is not None and short_net > 0.02
        advice = "BLOCK" if (trend == "BAD" and not recovering) else "ALLOW"
        h7b_state[(day, hhmm)] = advice

# ---- accumulators ----
acc = defaultdict(lambda: [0, 0])  # (H, cell, day) -> [n, wins]


def bump(h, cell, day, lab):
    c = acc[(h, cell, day)]
    c[0] += 1
    c[1] += lab


def main():
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
        daily_close = {d: bars[by_day[d][-1]][4] for d in days}
        p5 = 0
        for day in days[2:]:
            idxs = by_day[day]
            pdc = daily_close[days[days.index(day) - 1]]
            cum_pv = cum_v = cum_p2v = 0.0
            vwap_hist = []
            bars_since_cross = 9999
            latched = False
            import math
            for j, i in enumerate(idxs):
                t, o, h, l, c, v = bars[i]
                tp = (h + l + c) / 3
                vv = max(v, 1)
                cum_pv += tp * vv; cum_v += vv; cum_p2v += tp * tp * vv
                vwap = cum_pv / cum_v
                var = max(cum_p2v / cum_v - vwap * vwap, 0)
                upper = vwap + math.sqrt(var)
                vwap_hist.append(vwap)
                prev_c = bars[idxs[j - 1]][4] if j > 0 else None
                crossed = j > 0 and prev_c is not None and prev_c <= vwap_hist[j - 1] and c > vwap
                if crossed:
                    bars_since_cross = 0
                elif c > vwap:
                    bars_since_cross = bars_since_cross + 1 if bars_since_cross < 9999 else 9999
                else:
                    bars_since_cross = 9999
                if c < vwap:
                    latched = False   # leg reset
                msm = int(t[11:13]) * 60 + int(t[14:16])
                if not (570 <= msm <= 870) or j < 5 or latched:
                    continue
                fresh = bars_since_cross <= 3
                above_upper = c > upper
                if not (fresh or above_upper):
                    continue
                # v27-style conditions
                r_ok2 = rsi1[i] is not None and rsi1[i - 1] is not None and rsi1[i - 2] is not None and rsi1[i] > rsi1[i - 1] > rsi1[i - 2]
                h_ok2 = hist1[i] is not None and hist1[i - 1] is not None and hist1[i - 2] is not None and hist1[i] > hist1[i - 1] > hist1[i - 2]
                slope_pos = j >= 3 and vwap > vwap_hist[j - 3]
                a_ok = (adx1[i] or 0) >= 20
                a_r2 = adx1[i] is not None and adx1[i - 1] is not None and adx1[i - 2] is not None and adx1[i] > adx1[i - 1] > adx1[i - 2]
                r_nr3 = rsi1[i] is not None and rsi1[i - 3] is not None and rsi1[i] > rsi1[i - 3]
                above_pdc = c > pdc
                is_break = fresh and r_ok2 and h_ok2 and slope_pos and above_pdc
                is_cont = above_upper and a_ok and a_r2 and r_nr3 and above_pdc
                # H2/H3 need the below-PDC and margin variants too — evaluate them on the
                # same geometry WITHOUT the above_pdc gate so 'below' cells exist:
                is_break_nopdc = fresh and r_ok2 and h_ok2 and slope_pos
                is_cont_nopdc = above_upper and a_ok and a_r2 and r_nr3
                if not (is_break_nopdc or is_cont_nopdc):
                    continue
                latched = True
                # label
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
                hhmm = t[11:16]
                pattern = "CONT" if is_cont_nopdc and not is_break_nopdc else ("BREAK" if is_break_nopdc and not is_cont_nopdc else "BOTH")
                tb = ("0930-0944" if hhmm < "09:45" else
                      "0945-1014" if hhmm < "10:15" else
                      "1015-1100" if hhmm <= "11:00" else
                      "midday" if hhmm < "13:30" else "1330-1430")

                # H1: continuation early vs late (v27 conditions incl PDC)
                if is_cont:
                    bump("H1", tb, day, lab)
                # H2: vwap-vs-pdc side x early/later (all v27-passing signals, no PDC gate)
                vwap_pdc = (vwap / pdc - 1) * 100
                period = "early" if hhmm < "09:45" else "later"
                bump("H2", f"{period}|vwap{'>=' if vwap_pdc >= 0 else '<'}PDC", day, lab)
                # H3: continuation pdc-margin (needs above_pdc true, v27 world)
                if is_cont:
                    margin = (c / pdc - 1) * 100
                    bump("H3", "just(<0.15)" if margin < 0.15 else "comfort(>=0.15)", day, lab)
                # H5: RSI zones on all signals (1-min RSI; NOTE bot gate uses 1h — proxy only)
                r = rsi1[i] or 0
                zone = "<50" if r < 50 else ("50-68" if r < 68 else ("68-82" if r <= 82 else ">82"))
                bump("H5", zone, day, lab)
                # rsi5 zone too
                while p5 + 1 < len(rb5) and (rb5[p5 + 1][7], rb5[p5 + 1][6]) <= (day, msm):
                    p5 += 1
                ok5 = p5 < len(rb5) and rb5[p5][7] <= day and rb5[p5][6] <= msm
                if ok5 and f5["rsi"][p5] is not None:
                    r5 = f5["rsi"][p5]
                    z5 = "<50" if r5 < 50 else ("50-68" if r5 < 68 else ("68-82" if r5 <= 82 else ">82"))
                    bump("H5_rsi5", z5, day, lab)
                # H6: window value (v27-passing incl PDC, pattern-split)
                if is_break or is_cont:
                    wname = "morning" if hhmm <= "11:00" else ("midday" if hhmm < "13:30" else "afternoon")
                    bump("H6", f"{wname}|{pattern}", day, lab)
                # H7b: would the daemon have blocked this minute?
                adv = h7b_state.get((day, hhmm), "ALLOW")
                bump("H7b", adv, day, lab)
        if (fi + 1) % 50 == 0:
            print(f"  {fi+1}/{len(files)}", flush=True)

    out = {"|".join(k): v for k, v in acc.items()}
    json.dump(out, open(BASE / "data" / "open_h_counts.json", "w"))
    print(f"cells: {len(out)}")


if __name__ == "__main__":
    main()
