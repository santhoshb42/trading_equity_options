"""H13.5 / H11 — nightly universe validation (run after market close, Mon-Fri).

Each run:
1. Refreshes the 30-day 1-min candle store for all 210 F&O underlyings (one call
   per symbol) + NIFTY.
2. Re-runs the 3-profile trail simulation of the H12 best entry rule
   (run5m>=0.4 & ADX1>=40 & RSI1>=68 & 5m-hist>0 & above PDC, 09:30-11:00).
3. Joins per-day results with NIFTY session metrics (eff/net from 09:30).
4. Checks the four standing claims on the LATEST day and appends a verdict line
   to data/validation_log.jsonl:
   (i)   day-quality: HEALTHY NIFTY session <-> positive sim day?
   (ii)  loose exit >= tight exit on the day?
   (iii) early-cut asymmetry (losers vs winners hitting -6% premium in 10min,
         from the CE bots' real premium snapshots)?
   (iv)  the rule's day WR vs the day's base rate.
LIVE gate (H11): 10 days of surviving evidence before LIVE re-entry discussion.
"""
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from features import wilder_rsi, wilder_adx
from pass1_baserates import resample, tf_ind

DATA = BASE / "data"
PROFILES = {"tight": (0.5, 0.5, 0.3, 60), "mid": (0.5, 0.8, 0.6, 90), "loose": (0.5, 1.0, 0.8, 150)}


def refresh_candles():
    env = dict(os.environ, UNIVERSE_REFRESH="1")
    subprocess.run([sys.executable, str(BASE / "fetch30.py")], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1200)


def fetch_nifty():
    os.environ.setdefault("BOT_MODE", "OTM")
    os.chdir("/root/santhosh/trading/CE_OPTIONS")
    sys.path.insert(0, "/root/santhosh/trading/CE_OPTIONS")
    from dotenv import load_dotenv
    load_dotenv("tools/.env")
    from optcode.angelone_options import AngelOneOptionsBroker
    import time as _t
    b = AngelOneOptionsBroker()
    b.authenticate()
    c = None
    for a in range(4):
        try:
            c = b.get_historical_data("Nifty 50", "ONE_MINUTE", days_back=30, force_refresh=True)
        except Exception:
            c = None
        if c:
            break
        _t.sleep(3 + a * 3)
    if c:
        json.dump([[x["timestamp"][:16], x["open"], x["high"], x["low"], x["close"]] for x in c],
                  open(DATA / "nifty_1min.json", "w"))
    return bool(c)


def nifty_day_metrics():
    nb = json.load(open(DATA / "nifty_1min.json"))
    by_day = defaultdict(list)
    for t, o, h, l, c in nb:
        by_day[t[:10]].append((t[11:16], o, c))
    out = {}
    for day, rows in by_day.items():
        rows = [r for r in rows if "09:30" <= r[0] < "11:00"]
        if len(rows) < 5:
            continue
        op = rows[0][1]
        cl = rows[-1][2]
        path = sum(abs(c - o) for _, o, c in rows)
        net = (cl / op - 1) * 100
        eff = abs(cl - op) / path * 100 if path else 0
        out[day] = {"eff": round(eff, 2), "net": round(net, 3),
                    "healthy": eff >= 8.0 and net > 0.05}
    return out


def run_sim():
    res = {p: defaultdict(lambda: [0, 0, 0.0]) for p in PROFILES}
    for f in sorted((DATA / "candles30").glob("*.json")):
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
        dc = {d: bars[by_day[d][-1]][4] for d in days}
        p5 = 0
        for day in days[2:]:
            idxs = by_day[day]
            pdc = dc[days[days.index(day) - 1]]
            taken = -1
            for j, i in enumerate(idxs):
                t, o, h, l, c, v = bars[i]
                msm = int(t[11:13]) * 60 + int(t[14:16])
                if not (570 <= msm < 660) or j < 5 or i <= taken:
                    continue
                c5b = bars[idxs[j - 5]][4]
                if (c / c5b - 1) * 100 < 0.4 or (adx1[i] or 0) < 40 or (rsi1[i] or 0) < 68 or c <= pdc:
                    continue
                while p5 + 1 < len(rb5) and (rb5[p5 + 1][7], rb5[p5 + 1][6]) <= (day, msm):
                    p5 += 1
                ok5 = p5 < len(rb5) and rb5[p5][7] <= day and rb5[p5][6] <= msm
                if not (ok5 and f5["hist"][p5] is not None and f5["hist"][p5] > 0):
                    continue
                for pname, (stop, arm, gap, cap) in PROFILES.items():
                    e = c
                    peak = c
                    armed = False
                    ret = None
                    last = i
                    for k in range(i + 1, min(i + cap + 1, len(bars))):
                        if bars[k][0][:10] != day:
                            break
                        lo_p = (bars[k][3] / e - 1) * 100
                        peak = max(peak, bars[k][2])
                        pp = (peak / e - 1) * 100
                        if not armed and lo_p <= -stop:
                            ret = -stop
                            break
                        if pp >= arm:
                            armed = True
                        if armed and lo_p <= pp - gap:
                            ret = pp - gap
                            break
                        last = k
                    if ret is None:
                        ret = (bars[last][4] / e - 1) * 100
                    cell = res[pname][day]
                    cell[0] += 1
                    cell[1] += 1 if ret > 0 else 0
                    cell[2] += ret
                taken = idxs[min(j + 15, len(idxs) - 1)]
    out = {p: {d: v for d, v in rd.items()} for p, rd in res.items()}
    json.dump(out, open(DATA / "pass3_daily.json", "w"))
    return out


def early_cut_asymmetry(day):
    """Losers-vs-winners hitting -6% premium within 10min, from real CE bot data for `day`."""
    paths = defaultdict(list)
    for d in ["OTM", "ITM"]:
        f = Path(f"/root/santhosh/trading/CE_OPTIONS/{d}/data/trial_sl_premium_snapshots.jsonl")
        if not f.exists():
            continue
        for line in open(f, errors="ignore"):
            if '"phase":"open"' not in line or f'"{day}' not in line[:60]:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("timestamp", "")[:10] == day:
                paths[(d, o["symbol"])].append((o["timestamp"], o.get("current_premium")))
    W, L = [], []
    for d in ["OTM", "ITM"]:
        hf = Path(f"/root/santhosh/trading/CE_OPTIONS/{d}/data/option_pnl_history.json")
        if not hf.exists():
            continue
        for t in json.load(open(hf)):
            if (t.get("closed_at", "") or t.get("exit_time", ""))[:10] != day:
                continue
            pp = paths.get((d, t.get("symbol") or t.get("option_symbol")))
            if not pp or not t.get("entry_premium") or not t.get("entry_time"):
                continue
            et = datetime.fromisoformat(t["entry_time"])
            cutoff = et + timedelta(minutes=10)
            win10 = [p for ts, p in pp if p is not None and et <= datetime.fromisoformat(ts) <= cutoff]
            if len(win10) < 5:
                continue
            dd = (min(win10) / t["entry_premium"] - 1) * 100
            (W if t.get("pnl", 0) > 0 else L).append(dd)
    lh = 100 * sum(1 for x in L if x <= -6) / len(L) if L else None
    wh = 100 * sum(1 for x in W if x <= -6) / len(W) if W else None
    return {"n_win": len(W), "n_loss": len(L), "losers_hit_pct": lh, "winners_hit_pct": wh}


def live_window_pnl(day):
    """H21 continuous-window test — live CE PnL for `day` bucketed by entry window."""
    buckets = {"morning_0930_1100": [0, 0.0], "midday_1100_1330": [0, 0.0],
               "afternoon_1330_1430": [0, 0.0], "other": [0, 0.0]}
    for d in ["OTM", "ITM"]:
        hf = Path(f"/root/santhosh/trading/CE_OPTIONS/{d}/data/option_pnl_history.json")
        if not hf.exists():
            continue
        for t in json.load(open(hf)):
            if (t.get("closed_at", "") or t.get("exit_time", ""))[:10] != day:
                continue
            hhmm = t.get("entry_time", "")[11:16]
            if "09:30" <= hhmm < "11:01":
                k = "morning_0930_1100"
            elif "11:01" <= hhmm < "13:30":
                k = "midday_1100_1330"
            elif "13:30" <= hhmm <= "14:35":
                k = "afternoon_1330_1430"
            else:
                k = "other"
            buckets[k][0] += 1
            buckets[k][1] += t.get("pnl", 0) or 0
    return {k: {"n": v[0], "pnl": round(v[1])} for k, v in buckets.items() if v[0] or k != "other"}


def main():
    print(f"[{datetime.now():%H:%M:%S}] refreshing candles (210 symbols)...", flush=True)
    refresh_candles()
    print(f"[{datetime.now():%H:%M:%S}] fetching NIFTY...", flush=True)
    fetch_nifty()
    print(f"[{datetime.now():%H:%M:%S}] running 3-profile sim...", flush=True)
    sim = run_sim()
    nd = nifty_day_metrics()

    latest = max(sim["loose"].keys())
    nmet = nd.get(latest, {})
    tight = sim["tight"].get(latest, [0, 0, 0.0])
    loose = sim["loose"].get(latest, [0, 0, 0.0])
    ec = early_cut_asymmetry(latest)

    # H16c: evaluate the index preset candidates (NIFTY/BANKNIFTY) on the latest day —
    # builds the out-of-sample record required before index alerts are ever enabled.
    index_presets = None
    try:
        r = subprocess.run([sys.executable, str(BASE / "index_preset_eval.py")],
                           capture_output=True, text=True, timeout=300)
        for line in reversed(r.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                index_presets = json.loads(line)
                break
    except Exception as exc:
        index_presets = {"error": str(exc)}

    verdict = {
        "day": latest,
        "run_at": datetime.now().isoformat(),
        "index_presets": index_presets,
        "nifty": nmet,
        "sim_n": loose[0],
        "sim_wr_pct": round(100 * loose[1] / loose[0], 1) if loose[0] else None,
        "sim_ret_tight": round(tight[2], 2),
        "sim_ret_loose": round(loose[2], 2),
        "claim_day_quality": (nmet.get("healthy") == (loose[2] > 0)) if nmet else None,
        "claim_loose_ge_tight": loose[2] >= tight[2],
        "early_cut": ec,
        "live_windows": live_window_pnl(latest),
    }
    with open(DATA / "validation_log.jsonl", "a") as fh:
        fh.write(json.dumps(verdict) + "\n")
    print(json.dumps(verdict, indent=2))
    print(f"[{datetime.now():%H:%M:%S}] appended to data/validation_log.jsonl")


if __name__ == "__main__":
    main()
