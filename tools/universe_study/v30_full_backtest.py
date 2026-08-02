"""HIGH-STAKES v30 FULL-STACK BACKTEST — 20 trading days x 210 F&O symbols.

Replicates tomorrow's production stack exactly:
  ENTRY (Pine v30): v27 patterns (fresh-cross breakout / upper-band continuation)
    + H9 gates (VOL_FADE>=0.7, ADX_EXHAUST, BIG_GAP<=0.75%)
    + H12 gates (run5m>=0.4%, ADX>=40, RSI>=68, last-closed 5m MACD hist>0)
    + per-leg latch (leg resets when close < VWAP), evaluated per-config on its window.
  BOT FLAGS:
    H7b   — skip signal if NIFTY 15-min BAD & not recovering (exact daemon thresholds)
    H13.1 — session-health sizing: HEALTHY (eff>=8 & net>0.05 since 09:30) -> full notional,
            else half (proposed cap spread)
    H13.2 — early-loser cut: first 10 bars stop at -0.3% underlying (~ -6% premium @ L=20)
  EXIT: wide trail per H13.3 (stop -0.5%, arm +1.0%, trail peak-0.8%), ride to 15:10 max.
  PnL (stated assumptions, grounded in real trades): notional Rs22,079/trade (real avg),
    leverage L=20 (premium% ~= 20 x underlying%), friction per round trip = Rs88 charges
    + 1.44% of notional spread cost (real medians).

NO LOOKAHEAD: all entry features at bar close; label/exits strictly from forward bars.
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
NOTIONAL = 22079.0
LEV = 20.0
CHARGES = 88.0
SPREAD_PCT = 1.44  # % of notional, round trip

# ---- NIFTY per-minute states: H7b advice + session health ----
nifty = json.load(open(BASE / "data" / "nifty_1min.json"))
nby = defaultdict(list)
for t, o, h, l, c in nifty:
    nby[t[:10]].append((t[11:16], o, c))
h7b = {}
health = {}
for day, rows in nby.items():
    closes, opens = [], []
    s_path = 0.0
    s_open = None
    for hhmm, o, c in rows:
        closes.append(c)
        opens.append(o)
        i = len(closes)
        if i >= 15:
            w_o, w_c = opens[i - 15], closes[-1]
            path = sum(abs(closes[k] - opens[k]) for k in range(i - 15, i))
            eff = abs(w_c - w_o) / path * 100 if path else 0
            net = (w_c - w_o) / w_o * 100
            trend = "GOOD" if (eff >= 12 and net > 0) else ("BAD" if (eff >= 12 and net < 0) else "NEUTRAL")
            sn = (closes[-1] - opens[i - 5]) / opens[i - 5] * 100 if i >= 5 else None
            h7b[(day, hhmm)] = "BLOCK" if (trend == "BAD" and not (sn is not None and sn > 0.02)) else "ALLOW"
        if hhmm >= "09:30":
            if s_open is None:
                s_open = o
            s_path += abs(c - o)
            snet = (c - s_open) / s_open * 100
            seff = abs(c - s_open) / s_path * 100 if s_path else 0
            health[(day, hhmm)] = "HEALTHY" if (seff >= 8 and snet > 0.05) else "WEAK"


def trail_ret(bars, i, day_last_i, early_cut):
    """Wide trail from bar i close; returns underlying-% return."""
    e = bars[i][4]
    peak = e
    armed = False
    last = i
    for k in range(i + 1, day_last_i + 1):
        lo_p = (bars[k][3] / e - 1) * 100
        peak = max(peak, bars[k][2])
        pp = (peak / e - 1) * 100
        stop = -0.3 if (early_cut and (k - i) <= 10 and not armed) else -0.5
        if not armed and lo_p <= stop:
            return stop
        if pp >= 1.0:
            armed = True
        if armed and lo_p <= pp - 0.8:
            return pp - 0.8
        last = k
    return (bars[last][4] / e - 1) * 100


def main():
    signals = []  # all gate-passing bars, latch applied per-config later
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
            gap = (day_open / pdc - 1) * 100 if pdc else 99
            # index of last bar at/before 15:10 for exits
            last_i = idxs[0]
            for i in idxs:
                if bars[i][0][11:16] <= "15:10":
                    last_i = i
            cum_pv = cum_v = cum_p2v = 0.0
            vwap_hist = []
            vols = []
            bsc = 9999
            leg = 0
            for j, i in enumerate(idxs):
                t, o, h, l, c, v = bars[i]
                tp = (h + l + c) / 3
                vv = max(v, 1)
                cum_pv += tp * vv; cum_v += vv; cum_p2v += tp * tp * vv
                vwap = cum_pv / cum_v
                var = max(cum_p2v / cum_v - vwap * vwap, 0)
                upper = vwap + math.sqrt(var)
                vwap_hist.append(vwap)
                vols.append(v)
                prev_c = bars[idxs[j - 1]][4] if j > 0 else None
                crossed = j > 0 and prev_c is not None and prev_c <= vwap_hist[j - 1] and c > vwap
                bsc = 0 if crossed else (bsc + 1 if (c > vwap and bsc < 9999) else (bsc if c > vwap else 9999))
                if c < vwap:
                    leg += 1
                hhmm = t[11:16]
                msm = int(t[11:13]) * 60 + int(t[14:16])
                if not (570 <= msm <= 910) or j < 5:
                    continue
                fresh = bsc <= 3
                above_upper = c > upper
                if not (fresh or above_upper):
                    continue
                # v27 patterns (with PDC)
                r2 = rsi1[i] is not None and rsi1[i-1] is not None and rsi1[i-2] is not None and rsi1[i] > rsi1[i-1] > rsi1[i-2]
                h2 = hist1[i] is not None and hist1[i-1] is not None and hist1[i-2] is not None and hist1[i] > hist1[i-1] > hist1[i-2]
                sp = j >= 3 and vwap > vwap_hist[j-3]
                a20 = (adx1[i] or 0) >= 20
                ar2 = adx1[i] is not None and adx1[i-1] is not None and adx1[i-2] is not None and adx1[i] > adx1[i-1] > adx1[i-2]
                rnr = rsi1[i] is not None and rsi1[i-3] is not None and rsi1[i] > rsi1[i-3]
                apdc = c > pdc
                is_b = fresh and r2 and h2 and sp and apdc
                is_c = above_upper and a20 and ar2 and rnr and apdc
                if not (is_b or is_c):
                    continue
                # H9 gates
                v5 = sum(vols[-6:-1]) / 5 if j >= 5 else 0
                if v5 <= 0 or v / v5 < 0.7:
                    continue
                if adx1[i] is not None and adx1[i-3] is not None and adx1[i] > 40 and adx1[i] < adx1[i-3]:
                    continue
                if gap > 0.75:
                    continue
                # H12 gates
                c5b = bars[idxs[j-5]][4]
                if (c / c5b - 1) * 100 < 0.4:
                    continue
                if (adx1[i] or 0) < 40:
                    continue
                if (rsi1[i] or 0) < 68:
                    continue
                while p5 + 1 < len(rb5) and (rb5[p5+1][7], rb5[p5+1][6]) <= (day, msm):
                    p5 += 1
                ok5 = p5 < len(rb5) and rb5[p5][7] <= day and rb5[p5][6] <= msm
                if not (ok5 and f5["hist"][p5] is not None and f5["hist"][p5] > 0):
                    continue
                signals.append({
                    "day": day, "hhmm": hhmm, "sym": f.stem, "leg": leg,
                    "ret": trail_ret(bars, i, last_i, False),
                    "ret_ec": trail_ret(bars, i, last_i, True),
                    "h7b": h7b.get((day, hhmm), "ALLOW"),
                    "health": health.get((day, hhmm), "WEAK"),
                })
        if (fi + 1) % 50 == 0:
            print(f"  {fi+1}/{len(files)}", flush=True)
    json.dump(signals, open(BASE / "data" / "v30_signals.json", "w"))
    print(f"gate-passing signal-bars: {len(signals)}")


if __name__ == "__main__":
    main()
