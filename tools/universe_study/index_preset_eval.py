"""H16c nightly companion — evaluate the two index preset candidates on the LATEST day.

Presets (from the 2026-07-16 20-day joint entry x exit grid, H16c in memory):
  NIFTY:     entry thrust>=0.05 adx>=25 rsi>=55 | exit hard -15%, no early-cut, arm 12% | L=100
  BANKNIFTY: entry thrust>=0.08 adx>=25 rsi>=55 | exit hard -20%, early-cut -9%, arm 12% | L=38
These are VALIDATION CANDIDATES, not live — index alerts don't exist in TV yet. This script
lets H11's nightly log build the out-of-sample record required before enabling them.

Prints one JSON object to stdout (parsed by nightly_validation.py).
"""
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import wilder_rsi, wilder_adx

NOTIONAL = 22079.0
FRICTION = 88.0 + 0.005 * NOTIONAL

PRESETS = {
    "NIFTY": {"alias": "Nifty 50", "entry": (0.05, 25, 55), "L": 100,
              "exit": {"hard": 15, "ecut": None, "arm": 12}},
    "BANKNIFTY": {"alias": "Nifty Bank", "entry": (0.08, 25, 55), "L": 38,
                  "exit": {"hard": 20, "ecut": 9, "arm": 12}},
}


def ema_raw(vals, p):
    k = 2 / (p + 1)
    e = None
    out = []
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def eval_symbol(raw, entry, L, exit_p):
    bars = [{"t": b["timestamp"][:16], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
            for b in raw]
    closes = [b["c"] for b in bars]
    rsi = wilder_rsi(closes)
    adx = wilder_adx([b["h"] for b in bars], [b["l"] for b in bars], closes)
    ef, es = ema_raw(closes, 12), ema_raw(closes, 26)
    macd = [a - b for a, b in zip(ef, es)]
    sig = ema_raw(macd, 9)
    hist = [m - s for m, s in zip(macd, sig)]
    rb5 = []
    ck = None
    for b in bars:
        day = b["t"][:10]
        msm = int(b["t"][11:13]) * 60 + int(b["t"][14:16])
        key = (day, (msm - 555) // 5)
        if key != ck:
            rb5.append({"day": day, "end": msm + 5, "c": b["c"]})
            ck = key
        else:
            rb5[-1]["c"] = b["c"]
    c5 = [x["c"] for x in rb5]
    ef5, es5 = ema_raw(c5, 12), ema_raw(c5, 26)
    m5 = [a - b for a, b in zip(ef5, es5)]
    s5 = ema_raw(m5, 9)
    h5 = [m - s for m, s in zip(m5, s5)]

    by_day = {}
    for i, b in enumerate(bars):
        by_day.setdefault(b["t"][:10], []).append(i)
    days = sorted(by_day)
    latest = days[-1]
    if len(days) < 3:
        return {"day": latest, "n": 0, "net": 0.0, "note": "insufficient history"}
    idxs = by_day[latest]
    pdc = bars[by_day[days[-2]][-1]]["c"]
    gap = (bars[idxs[0]]["o"] / pdc - 1) * 100
    last_i = idxs[0]
    for i in idxs:
        if bars[i]["t"][11:16] <= "15:10":
            last_i = i
    th, ax, rs = entry
    cum = cum2 = 0.0
    n = 0
    vh = []
    bsc = 9999
    p5 = 0
    latch = False
    fired = []
    for j, i in enumerate(idxs):
        b = bars[i]
        tp = (b["h"] + b["l"] + b["c"]) / 3
        cum += tp
        cum2 += tp * tp
        n += 1
        vwap = cum / n
        var = max(cum2 / n - vwap * vwap, 0)
        upper = vwap + math.sqrt(var)
        prev_c = bars[idxs[j - 1]]["c"] if j > 0 else None
        crossed = j > 0 and prev_c is not None and vh and prev_c <= vh[-1] and b["c"] > vwap
        bsc = 0 if crossed else (bsc + 1 if (b["c"] > vwap and bsc < 9999) else (bsc if b["c"] > vwap else 9999))
        if b["c"] < vwap:
            latch = False
        vh.append(vwap)
        hhmm = b["t"][11:16]
        if not ("09:30" <= hhmm <= "11:00") or j < 5 or latch:
            continue
        fresh = bsc <= 3
        above = b["c"] > upper
        if not (fresh or above):
            continue
        r_r2 = rsi[i] is not None and rsi[i - 1] is not None and rsi[i - 2] is not None and rsi[i] > rsi[i - 1] > rsi[i - 2]
        h_r2 = hist[i] > hist[i - 1] > hist[i - 2]
        sp = j >= 3 and vwap > vh[j - 3]
        a_r2 = adx[i] is not None and adx[i - 1] is not None and adx[i - 2] is not None and adx[i] > adx[i - 1] > adx[i - 2]
        r_nr3 = rsi[i] is not None and rsi[i - 3] is not None and rsi[i] > rsi[i - 3]
        apdc = b["c"] > pdc
        if not ((fresh and r_r2 and h_r2 and sp and apdc) or (above and (adx[i] or 0) >= 20 and a_r2 and r_nr3 and apdc)):
            continue
        if adx[i] is not None and adx[i - 3] is not None and adx[i] > 40 and adx[i] < adx[i - 3]:
            continue
        if gap > 0.75:
            continue
        msm = int(hhmm[:2]) * 60 + int(hhmm[3:])
        while p5 + 1 < len(rb5) and (rb5[p5 + 1]["day"], rb5[p5 + 1]["end"]) <= (latest, msm):
            p5 += 1
        if not (p5 < len(h5) and h5[p5] is not None and h5[p5] > 0):
            continue
        run5m = (b["c"] / bars[idxs[j - 5]]["c"] - 1) * 100
        if run5m < th or (adx[i] or 0) < ax or (rsi[i] or 0) < rs:
            continue
        latch = True
        # exit sim in premium space
        e = b["c"]
        peak = 0.0
        armed = False
        ret = None
        for k in range(i + 1, last_i + 1):
            upp = (bars[k]["h"] / e - 1) * 100 * L
            dnp = (bars[k]["l"] / e - 1) * 100 * L
            mins = k - i
            peak = max(peak, upp)
            if not armed and exit_p["ecut"] and mins <= 10 and dnp <= -exit_p["ecut"]:
                ret = -exit_p["ecut"]
                break
            if not armed and dnp <= -exit_p["hard"]:
                ret = -exit_p["hard"]
                break
            if peak >= exit_p["arm"]:
                armed = True
            if armed:
                g = 4 if peak < 12 else (6 if peak < 25 else (9 if peak < 60 else 12))
                if dnp <= peak - g:
                    ret = peak - g
                    break
        if ret is None:
            ret = (bars[last_i]["c"] / e - 1) * 100 * L
        fired.append({"hhmm": hhmm, "ret_prem_pct": round(ret, 2),
                      "net_rs": round(ret / 100 * NOTIONAL - FRICTION, 0)})
    return {"day": latest, "n": len(fired),
            "net": round(sum(f["net_rs"] for f in fired), 0), "trades": fired}


def main():
    os.environ.setdefault("BOT_MODE", "OTM")
    os.chdir("/root/santhosh/trading/CE_OPTIONS")
    sys.path.insert(0, "/root/santhosh/trading/CE_OPTIONS")
    from dotenv import load_dotenv
    load_dotenv("tools/.env")
    from optcode.angelone_options import AngelOneOptionsBroker
    b = AngelOneOptionsBroker()
    b.authenticate()
    out = {}
    for sym, cfg in PRESETS.items():
        raw = None
        for a in range(4):
            try:
                raw = b.get_historical_data(cfg["alias"], "ONE_MINUTE", days_back=5, force_refresh=True)
            except Exception:
                raw = None
            if raw:
                break
            time.sleep(3 + a * 2)
        out[sym] = eval_symbol(raw, cfg["entry"], cfg["L"], cfg["exit"]) if raw else {"error": "fetch failed"}
        time.sleep(1)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
