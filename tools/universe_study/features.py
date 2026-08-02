"""Rebuild Pine-equivalent 1-min indicator state at each traded signal's entry bar,
plus candidate exhaustion features Pine COULD compute but currently doesn't.
All features use only data available AT OR BEFORE the entry bar (no lookahead)."""
import json
import math
from pathlib import Path

OUT = Path("/tmp/claude-0/-root-santhosh-trading/98d4451b-b7bc-4a03-8c57-47e6e8a5a7c0/scratchpad/backtest")


def wilder_rsi(closes, period=14):
    rsi = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    ag, al = gains / period, losses / period
    rsi[period] = 100 - 100 / (1 + (ag / al if al else float('inf')))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        rsi[i] = 100 - 100 / (1 + (ag / al if al else float('inf')))
    return rsi


def wilder_adx(highs, lows, closes, period=14):
    n = len(closes)
    adx = [None] * n
    if n < period * 2 + 1:
        return adx
    trs, pdms, ndms = [], [], []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdms.append(up if (up > dn and up > 0) else 0.0)
        ndms.append(dn if (dn > up and dn > 0) else 0.0)
        trs.append(tr)
    str_, spdm, sndm = sum(trs[:period]), sum(pdms[:period]), sum(ndms[:period])
    dxs = []
    for i in range(period, len(trs)):
        str_ = str_ - str_ / period + trs[i]
        spdm = spdm - spdm / period + pdms[i]
        sndm = sndm - sndm / period + ndms[i]
        pdi = 100 * spdm / str_ if str_ else 0
        ndi = 100 * sndm / str_ if str_ else 0
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0
        dxs.append(dx)
        bar = i + 1  # dx for bar index i+1 in original arrays
        if len(dxs) == period:
            adx[bar] = sum(dxs) / period
        elif len(dxs) > period:
            adx[bar] = (adx[bar - 1] * (period - 1) + dx) / period
    return adx


def ema_series(vals, period):
    out = [None] * len(vals)
    k = 2 / (period + 1)
    e = None
    for i, v in enumerate(vals):
        e = v if e is None else v * k + e * (1 - k)
        if i >= period - 1:
            out[i] = e
    return out


def macd_hist(closes, fast=12, slow=26, sig=9):
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    macd = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)]
    valid = [m for m in macd if m is not None]
    hist = [None] * len(closes)
    if not valid:
        return hist
    k = 2 / (sig + 1)
    sigv = None
    for i, m in enumerate(macd):
        if m is None:
            continue
        sigv = m if sigv is None else m * k + sigv * (1 - k)
        hist[i] = m - sigv
    return hist


def compute_day_features(bars_all, day, pdc):
    """bars_all: full multi-day 1-min bars. Returns per-bar feature dicts for `day`'s session."""
    # continuous series for RSI/ADX/MACD (Pine behavior: indicators roll over days)
    closes = [b["c"] for b in bars_all]
    highs = [b["h"] for b in bars_all]
    lows = [b["l"] for b in bars_all]
    rsi = wilder_rsi(closes)
    adx = wilder_adx(highs, lows, closes)
    hist = macd_hist(closes)

    day_idx = [i for i, b in enumerate(bars_all) if b["t"][:10] == day]
    if not day_idx:
        return {}
    feats = {}
    d0 = day_idx[0]
    day_open = bars_all[d0]["o"]
    cum_pv = cum_v = 0.0
    cum_p2v = 0.0
    vwap_hist = []
    day_high = -1e18
    vols = []
    green = 0
    rsi_day_max = -1e18
    for j, i in enumerate(day_idx):
        b = bars_all[i]
        tp = (b["h"] + b["l"] + b["c"]) / 3
        v = max(b["v"], 1)
        cum_pv += tp * v; cum_v += v; cum_p2v += tp * tp * v
        vwap = cum_pv / cum_v
        var = max(cum_p2v / cum_v - vwap * vwap, 0)
        stdev = math.sqrt(var)
        upper = vwap + 2 * stdev
        vwap_hist.append(vwap)
        day_high = max(day_high, b["h"])
        vols.append(b["v"])
        green = green + 1 if b["c"] > b["o"] else 0
        if rsi[i] is not None:
            rsi_day_max = max(rsi_day_max, rsi[i])
        hhmm = b["t"][11:16]
        c5 = bars_all[day_idx[j - 5]]["c"] if j >= 5 else None
        c15 = bars_all[day_idx[j - 15]]["c"] if j >= 15 else None
        feats[hhmm] = {
            "bars_since_open": j,
            "gap_pct": (day_open / pdc - 1) * 100 if pdc else None,
            "move_from_open": (b["c"] / day_open - 1) * 100,
            "run_5m": (b["c"] / c5 - 1) * 100 if c5 else None,
            "run_15m": (b["c"] / c15 - 1) * 100 if c15 else None,
            "dist_day_high": (b["c"] / day_high - 1) * 100,
            "vol_ratio_5": (b["v"] / (sum(vols[-6:-1]) / 5)) if j >= 5 and sum(vols[-6:-1]) else None,
            "vol_fade": (sum(vols[-3:]) / 3) / (sum(vols[:3]) / 3) if j >= 5 and sum(vols[:3]) else None,
            "green_streak": green,
            "entry_bar_red": 1 if b["c"] < b["o"] else 0,
            "rsi": rsi[i],
            "rsi_slope3": (rsi[i] - rsi[day_idx[j - 3]]) if (j >= 3 and rsi[i] is not None and rsi[day_idx[j - 3]] is not None) else None,
            "rsi_from_day_peak": (rsi[i] - rsi_day_max) if (rsi[i] is not None and rsi_day_max > -1e17) else None,
            "adx": adx[i],
            "adx_slope3": (adx[i] - adx[day_idx[j - 3]]) if (j >= 3 and adx[i] is not None and adx[day_idx[j - 3]] is not None) else None,
            "macd_hist": hist[i],
            "hist_slope3": (hist[i] - hist[day_idx[j - 3]]) if (j >= 3 and hist[i] is not None and hist[day_idx[j - 3]] is not None) else None,
            "ext_vwap": (b["c"] / vwap - 1) * 100,
            "ext_upper": (b["c"] / upper - 1) * 100 if upper else None,
            "vwap_slope3": (vwap - vwap_hist[j - 3]) / vwap_hist[j - 3] * 100 if j >= 3 else None,
            "close": b["c"],
        }
    return feats


def main():
    signals = json.load(open(OUT / "signals.json"))
    candles = json.load(open(OUT / "candles.json"))
    out_rows = []
    missing = 0
    for s in signals:
        bars = candles.get(s["sym"]) or []
        if not bars:
            missing += 1
            continue
        feats = compute_day_features(bars, s["day"], s.get("pdc") or 0)
        # entry decision uses the CLOSED bar before entry: alert fires on bar close,
        # entry lands ~2-7s into the NEXT minute. hhmm of entry == the bar being formed;
        # the signal bar is the previous minute.
        hh, mm = s["hhmm"].split(":")
        prev = f"{hh}:{int(mm)-1:02d}" if int(mm) > 0 else f"{int(hh)-1}:59"
        f = feats.get(prev) or feats.get(s["hhmm"])
        if not f:
            missing += 1
            continue
        out_rows.append({**s, **f})
    json.dump(out_rows, open(OUT / "dataset.json", "w"))
    print(f"dataset rows={len(out_rows)} (missing candles/bars for {missing})")


if __name__ == "__main__":
    main()
