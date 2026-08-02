"""H17 confirmation study on ~90 days: keep close>VWAP_UPPER as the base geometry,
grid the LOOSENING variants (ADX threshold / upper-cross freshness / thrust / RSI floor /
pattern extras) and judge by RUPEE outcomes with the real exit stack, friction included.
Exit policies compared per variant: (a) wide trail, early-cut ALWAYS (current prod),
(b) early-cut OFF, (c) early-cut only when NIFTY session WEAK (H17 regime-aware candidate).

Precision: continuous multi-day indicators; per-variant Pine latch (leg = close<VWAP resets;
first bar in leg passing THAT variant fires); sims cached per (bar) so shared entries compute
once; friction Rs88 + 1.44% notional; L=20; morning window 09:30-11:00; ride to 15:10.
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from features import wilder_rsi, wilder_adx

DATA = BASE / "data" / "candles90"
NOTIONAL = 22079.0
L = 20.0
FRICTION = 88.0 + 0.0144 * NOTIONAL

# ---- NIFTY session-health per minute over 90d ----
nifty = json.load(open(BASE / "data" / "nifty90.json"))
health = {}
_byday = defaultdict(list)
for t, o, h, l, c, v in nifty:
    _byday[t[:10]].append((t[11:16], o, c))
for day, rows in _byday.items():
    s_open = None
    s_path = 0.0
    for hhmm, o, c in rows:
        if hhmm < "09:30":
            continue
        if s_open is None:
            s_open = o
        s_path += abs(c - o)
        snet = (c - s_open) / s_open * 100
        seff = abs(c - s_open) / s_path * 100 if s_path else 0
        health[(day, hhmm)] = "HEALTHY" if (seff >= 8 and snet > 0.05) else "WEAK"


def ema_raw(vals, p):
    k = 2 / (p + 1)
    e = None
    out = []
    for x in vals:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


# ---- entry variants: (name, fn(feat) -> bool) ----
def V(name, fn):
    return (name, fn)

VARIANTS = [
    V("V0 prod (adx40,th.4,rsi68,+extras)",
      lambda f: f["adx"] >= 40 and f["run5m"] >= 0.4 and f["rsi"] >= 68 and f["extras"]),
    V("V1 adx30",
      lambda f: f["adx"] >= 30 and f["run5m"] >= 0.4 and f["rsi"] >= 68 and f["extras"]),
    V("V2 adx25",
      lambda f: f["adx"] >= 25 and f["run5m"] >= 0.4 and f["rsi"] >= 68 and f["extras"]),
    V("V3 fresh10&adx25",
      lambda f: f["fresh_up"] <= 10 and f["adx"] >= 25 and f["run5m"] >= 0.4 and f["rsi"] >= 68 and f["extras"]),
    V("V4 adx40 OR fresh10&adx25",
      lambda f: (f["adx"] >= 40 or (f["fresh_up"] <= 10 and f["adx"] >= 25)) and f["run5m"] >= 0.4 and f["rsi"] >= 68 and f["extras"]),
    V("V5 V4 + th.3",
      lambda f: (f["adx"] >= 40 or (f["fresh_up"] <= 10 and f["adx"] >= 25)) and f["run5m"] >= 0.3 and f["rsi"] >= 68 and f["extras"]),
    V("V6 V4 + th.3 rsi65",
      lambda f: (f["adx"] >= 40 or (f["fresh_up"] <= 10 and f["adx"] >= 25)) and f["run5m"] >= 0.3 and f["rsi"] >= 65 and f["extras"]),
    V("V7 V4 no-adx-rising",
      lambda f: (f["adx"] >= 40 or (f["fresh_up"] <= 10 and f["adx"] >= 25)) and f["run5m"] >= 0.4 and f["rsi"] >= 68 and f["extras_noar"]),
    V("V8 prod minus hist5",
      lambda f: f["adx"] >= 40 and f["run5m"] >= 0.4 and f["rsi"] >= 68 and f["extras_noh5"]),
    V("V9 loose (adx30,th.3,rsi65,no-extras)",
      lambda f: f["adx"] >= 30 and f["run5m"] >= 0.3 and f["rsi"] >= 65 and f["core_only"]),
]
NV = len(VARIANTS)


def main():
    # results[variant][exit_policy][day] = [n, wins, pnl]
    res = [defaultdict(lambda: defaultdict(lambda: [0, 0, 0.0])) for _ in range(NV)]
    files = sorted(DATA.glob("*.json"))
    print(f"symbols: {len(files)}")
    for fi, fpath in enumerate(files):
        bars = json.load(open(fpath))
        if len(bars) < 3000:
            continue
        closes = [b[4] for b in bars]
        highs = [b[2] for b in bars]
        lows = [b[3] for b in bars]
        rsi = wilder_rsi(closes)
        adx = wilder_adx(highs, lows, closes)
        rb5 = []
        ck = None
        for b in bars:
            day = b[0][:10]
            msm = int(b[0][11:13]) * 60 + int(b[0][14:16])
            key = (day, (msm - 555) // 5)
            if key != ck:
                rb5.append([day, msm + 5, b[4]])
                ck = key
            else:
                rb5[-1][2] = b[4]
        c5 = [x[2] for x in rb5]
        ef5, es5 = ema_raw(c5, 12), ema_raw(c5, 26)
        m5 = [a - bb for a, bb in zip(ef5, es5)]
        s5 = ema_raw(m5, 9)
        h5 = [m - s for m, s in zip(m5, s5)]

        by_day = defaultdict(list)
        for i, b in enumerate(bars):
            by_day[b[0][:10]].append(i)
        days = sorted(by_day)
        if len(days) < 6:
            continue
        dclose = {d: bars[by_day[d][-1]][4] for d in days}
        p5 = 0
        sim_cache = {}

        def sims(i, day_last_i, day):
            if i in sim_cache:
                return sim_cache[i]
            e = bars[i][4]
            out = {}
            for ec_mode in ("on", "off"):
                peak = 0.0
                armed = False
                ret = None
                for k in range(i + 1, day_last_i + 1):
                    up = (bars[k][2] / e - 1) * 100 * L
                    dn = (bars[k][3] / e - 1) * 100 * L
                    mins = k - i
                    peak = max(peak, up)
                    if not armed and ec_mode == "on" and mins <= 10 and dn <= -6:
                        ret = -6.0
                        break
                    if not armed and dn <= -10:
                        ret = -10.0
                        break
                    if peak >= 5:
                        armed = True
                    if armed:
                        g = 4 if peak < 12 else (6 if peak < 25 else (9 if peak < 60 else 12))
                        if dn <= peak - g:
                            ret = peak - g
                            break
                if ret is None:
                    ret = (bars[day_last_i][4] / e - 1) * 100 * L
                out[ec_mode] = ret
            sim_cache[i] = out
            return out

        for day in days[3:]:
            idxs = by_day[day]
            pdc = dclose[days[days.index(day) - 1]]
            day_open = bars[idxs[0]][1]
            gap = (day_open / pdc - 1) * 100 if pdc else 99
            day_last_i = idxs[0]
            for i in idxs:
                if bars[i][0][11:16] <= "15:10":
                    day_last_i = i
            cum = cumv = cum2 = 0.0
            vh = []
            vols = []
            up_age = 9999
            fired = [False] * NV
            for j, i in enumerate(idxs):
                t, o, h, l, c, v = bars[i]
                tp = (h + l + c) / 3
                vv = max(v, 1)
                cum += tp * vv
                cumv += vv
                cum2 += tp * tp * vv
                vwap = cum / cumv
                upper = vwap + math.sqrt(max(cum2 / cumv - vwap * vwap, 0))
                prev_above = vh and bars[idxs[j - 1]][4] > (vh[-1][1] if False else vh[-1])  # placeholder
                vh.append(vwap)
                vols.append(v)
                # upper-cross age
                if c > upper:
                    prev_c = bars[idxs[j - 1]][4] if j > 0 else None
                    # crossed this bar? compare prev close vs prev upper approx: track via age
                    up_age = 0 if up_age == 9999 else up_age + 1
                else:
                    up_age = 9999
                if c < vwap:
                    fired = [False] * NV  # leg reset (Pine latch resets on close<vwap)
                hhmm = t[11:16]
                if not ("09:30" <= hhmm <= "11:00") or j < 5:
                    continue
                if c <= upper:
                    continue  # base geometry: above upper band (user-confirmed)
                msm = int(hhmm[:2]) * 60 + int(hhmm[3:])
                while p5 + 1 < len(rb5) and (rb5[p5 + 1][0], rb5[p5 + 1][1]) <= (day, msm):
                    p5 += 1
                h5ok = p5 < len(h5) and h5[p5] is not None and h5[p5] > 0
                r5avg = sum(vols[-6:-1]) / 5 if j >= 5 else 0
                volok = r5avg > 0 and v / r5avg >= 0.7
                not_exh = not (adx[i] is not None and adx[i - 3] is not None and adx[i] > 40 and adx[i] < adx[i - 3])
                gapok = gap <= 0.75
                a_r2 = adx[i] is not None and adx[i - 1] is not None and adx[i - 2] is not None and adx[i] > adx[i - 1] > adx[i - 2]
                r_nr3 = rsi[i] is not None and rsi[i - 3] is not None and rsi[i] > rsi[i - 3]
                apdc = c > pdc
                feat = {
                    "adx": adx[i] or 0, "rsi": rsi[i] or 0,
                    "run5m": (c / bars[idxs[j - 5]][4] - 1) * 100,
                    "fresh_up": up_age,
                    "extras": h5ok and volok and not_exh and gapok and a_r2 and r_nr3 and apdc,
                    "extras_noar": h5ok and volok and not_exh and gapok and r_nr3 and apdc,
                    "extras_noh5": volok and not_exh and gapok and a_r2 and r_nr3 and apdc,
                    "core_only": volok and gapok and apdc,
                }
                hstate = health.get((day, hhmm), "WEAK")
                for vi, (vname, vfn) in enumerate(VARIANTS):
                    if fired[vi]:
                        continue
                    if not vfn(feat):
                        continue
                    fired[vi] = True
                    s = sims(i, day_last_i, day)
                    for pol, ret in (("ec_always", s["on"]), ("ec_off", s["off"]),
                                     ("ec_weakonly", s["on"] if hstate == "WEAK" else s["off"])):
                        cell = res[vi][pol][day]
                        net = ret / 100 * NOTIONAL - FRICTION
                        cell[0] += 1
                        cell[1] += 1 if net > 0 else 0
                        cell[2] += net
        if (fi + 1) % 40 == 0:
            print(f"  {fi+1}/{len(files)}", flush=True)

    out = []
    for vi, (vname, _) in enumerate(VARIANTS):
        for pol in ("ec_always", "ec_off", "ec_weakonly"):
            per = res[vi][pol]
            days_all = sorted(per)
            n = sum(v[0] for v in per.values())
            if not n:
                continue
            w = sum(v[1] for v in per.values())
            pnl = sum(v[2] for v in per.values())
            posd = sum(1 for v in per.values() if v[2] > 0)
            # month split
            months = defaultdict(float)
            for d, v in per.items():
                months[d[:7]] += v[2]
            out.append({"variant": vname, "policy": pol, "n": n, "n_day": n / max(1, len(days_all)),
                        "wr": 100 * w / n, "pnl": pnl, "pnl_trade": pnl / n,
                        "posDays": f"{posd}/{len(days_all)}",
                        "months": {k: round(vv) for k, vv in sorted(months.items())}})
    json.dump(out, open(BASE / "data" / "loosen_results.json", "w"), indent=1)
    out.sort(key=lambda r: -r["pnl"])
    print(f"\n{'variant':42}{'policy':12}{'n':>6}{'/day':>6}{'WR':>5}{'total₹':>10}{'₹/tr':>7}{'posD':>8}  months")
    for r in out:
        print(f"{r['variant']:42}{r['policy']:12}{r['n']:>6}{r['n_day']:>6.1f}{r['wr']:>4.0f}%"
              f"{r['pnl']:>10.0f}{r['pnl_trade']:>7.0f}{r['posDays']:>8}  {r['months']}")


if __name__ == "__main__":
    main()
