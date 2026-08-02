"""Universe-wide signal scan — precision rules:
- Features computed at bar CLOSE only (no lookahead); label from FORWARD bars only.
- PDC computed from the candle series itself (prior day's last close), not alert payloads.
- Trading windows: 09:30-11:00 + 13:30-14:30 IST, signals fire on bars 09:30..10:59 & 13:30..14:29.
- Per-leg latch replicated (one candidate per leg; leg resets when close < vwap line).
- Label: first-touch +0.5% before -0.5% within 60 min (calibrated: 75% agreement w/ real trades,
  proxy WR 38.2% vs real 39.6% on the 341 traded signals).
Output: candidates.json — one row per candidate bar with all features + label + leg-first flags.
"""
import json
import math
from pathlib import Path

OUT = Path("/tmp/claude-0/-root-santhosh-trading/98d4451b-b7bc-4a03-8c57-47e6e8a5a7c0/scratchpad/backtest")
DAYS = ["2026-07-13", "2026-07-14", "2026-07-15"]
UP_TH, DN_TH, HORIZON = 0.5, 0.5, 60

import sys
sys.path.insert(0, str(OUT))
from features import wilder_rsi, wilder_adx, macd_hist


def label_first_touch(bars, i):
    e = bars[i]["c"]
    for b in bars[i + 1:i + 1 + HORIZON]:
        hit_up = (b["h"] / e - 1) * 100 >= UP_TH
        hit_dn = (b["l"] / e - 1) * 100 <= -DN_TH
        if hit_dn:  # conservative: down-touch wins ties
            return 0
        if hit_up:
            return 1
    return 0  # timeout = loser (STALE-like)


def mfe_mae(bars, i, horizon=30):
    e = bars[i]["c"]
    hi = lo = 0.0
    for b in bars[i + 1:i + 1 + horizon]:
        hi = max(hi, (b["h"] / e - 1) * 100)
        lo = min(lo, (b["l"] / e - 1) * 100)
    return hi, lo


def in_window(hhmm):
    return ("09:30" <= hhmm <= "10:59") or ("13:30" <= hhmm <= "14:29")


def scan_symbol(sym, bars_all):
    rows = []
    closes = [b["c"] for b in bars_all]
    highs = [b["h"] for b in bars_all]
    lows = [b["l"] for b in bars_all]
    rsi = wilder_rsi(closes)
    adx = wilder_adx(highs, lows, closes)
    hist = macd_hist(closes)
    by_day = {}
    for i, b in enumerate(bars_all):
        by_day.setdefault(b["t"][:10], []).append(i)
    day_list = sorted(by_day)
    daily = DAILY.get(sym) or []
    daily_closes = {d["t"][:10]: d["c"] for d in daily}
    daily_days = sorted(daily_closes)
    for day in DAYS:
        if day not in by_day:
            continue
        idxs = by_day[day]
        prior = [d for d in daily_days if d < day]
        pdc = daily_closes[prior[-1]] if prior else None
        if not pdc:
            continue
        day_open = bars_all[idxs[0]]["o"]
        gap_pct = (day_open / pdc - 1) * 100
        cum_pv = cum_v = cum_p2v = 0.0
        vwap_hist = []
        vols = []
        # leg_id increments whenever price is below vwap (leg broken); rule evaluation
        # applies the Pine latch: FIRST bar in a leg that passes the rule = the entry.
        bars_since_cross = 9999
        leg_id = 0
        for j, i in enumerate(idxs):
            b = bars_all[i]
            tp = (b["h"] + b["l"] + b["c"]) / 3
            v = max(b["v"], 1)
            cum_pv += tp * v; cum_v += v; cum_p2v += tp * tp * v
            vwap = cum_pv / cum_v
            var = max(cum_p2v / cum_v - vwap * vwap, 0)
            upper = vwap + 1.0 * math.sqrt(var)  # mult=1.0 (script default)
            vwap_hist.append(vwap)
            vols.append(b["v"])
            # fresh-cross state
            prev_c = bars_all[idxs[j - 1]]["c"] if j > 0 else None
            crossed = prev_c is not None and prev_c <= vwap_hist[j - 1] if j > 0 else False
            crossed = crossed and b["c"] > vwap
            if crossed:
                bars_since_cross = 0
            elif b["c"] > vwap:
                bars_since_cross = bars_since_cross + 1 if bars_since_cross < 9999 else 9999
            else:
                bars_since_cross = 9999
            if b["c"] < vwap:
                leg_id += 1
            hhmm = b["t"][11:16]
            if not in_window(hhmm) or j < 5:
                continue
            fresh = bars_since_cross <= 3
            above_upper = b["c"] > upper
            if not (fresh or above_upper):
                continue
            if j + 1 >= len(idxs):
                continue
            # features (all at bar close, no lookahead)
            r5avg = sum(vols[-6:-1]) / 5 if j >= 5 else 0
            c5 = bars_all[idxs[j - 5]]["c"] if j >= 5 else None
            row = {
                "sym": sym, "day": day, "hhmm": hhmm, "leg": leg_id,
                "pattern": "BREAKOUT" if fresh else "CONT",
                "bars_since_open": j,
                "gap_pct": gap_pct,
                "move_from_open": (b["c"] / day_open - 1) * 100,
                "run_5m": (b["c"] / c5 - 1) * 100 if c5 else None,
                "vol_ratio": (b["v"] / r5avg) if r5avg > 0 else None,
                "rsi": rsi[i],
                "rsi_rising2": 1 if (rsi[i] is not None and rsi[i - 1] is not None and rsi[i - 2] is not None and rsi[i] > rsi[i - 1] > rsi[i - 2]) else 0,
                "rsi_not_rolling3": 1 if (rsi[i] is not None and rsi[i - 3] is not None and rsi[i] > rsi[i - 3]) else 0,
                "adx": adx[i],
                "adx_rising2": 1 if (adx[i] is not None and adx[i - 1] is not None and adx[i - 2] is not None and adx[i] > adx[i - 1] > adx[i - 2]) else 0,
                "adx_falling3": 1 if (adx[i] is not None and adx[i - 3] is not None and adx[i] < adx[i - 3]) else 0,
                "hist": hist[i],
                "hist_rising2": 1 if (hist[i] is not None and hist[i - 1] is not None and hist[i - 2] is not None and hist[i] > hist[i - 1] > hist[i - 2]) else 0,
                "hist_positive": 1 if (hist[i] is not None and hist[i] > 0) else 0,
                "vwap_slope_pos": 1 if (j >= 3 and vwap > vwap_hist[j - 3]) else 0,
                "vwap_slope_pct3": (vwap / vwap_hist[j - 3] - 1) * 100 if j >= 3 else None,
                "ext_vwap": (b["c"] / vwap - 1) * 100,
                "ext_upper": (b["c"] / upper - 1) * 100,
                "above_pdc": 1 if b["c"] > pdc else 0,
                "pdc_margin": (b["c"] / pdc - 1) * 100,
            }
            row["label"] = label_first_touch(bars_all, i)
            row["mfe30"], row["mae30"] = mfe_mae(bars_all, i)
            rows.append(row)
    return rows


DAILY = {}

def main():
    global DAILY
    DAILY = json.load(open(OUT / "daily.json"))
    candles = json.load(open(OUT / "candles.json"))
    universe = json.load(open(OUT / "universe.json"))
    all_rows = []
    have = 0
    for sym in universe:
        bars = candles.get(sym) or []
        if len(bars) < 400:
            continue
        have += 1
        all_rows.extend(scan_symbol(sym, bars))
    json.dump(all_rows, open(OUT / "candidates.json", "w"))
    from collections import Counter
    c = Counter(r["day"] for r in all_rows)
    w = Counter(r["day"] for r in all_rows if r["label"] == 1)
    print(f"symbols with data: {have} | candidate signals: {len(all_rows)}")
    for d in DAYS:
        print(f"  {d}: candidates={c[d]:4}  baselineWR={100*w[d]/c[d]:.0f}%" if c[d] else f"  {d}: none")


if __name__ == "__main__":
    main()
