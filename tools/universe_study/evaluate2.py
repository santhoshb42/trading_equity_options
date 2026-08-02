"""Stage 2: (a) qualified gates split by entry_type, (b) 2-gate AND/OR combos of the
top qualified single gates, (c) sanity: does the gate keep the big winners (peak>=10%)?"""
import json
from itertools import combinations
from pathlib import Path

OUT = Path("/tmp/claude-0/-root-santhosh-trading/98d4451b-b7bc-4a03-8c57-47e6e8a5a7c0/scratchpad/backtest")
DAYS = ["2026-07-13", "2026-07-14", "2026-07-15"]
rows = json.load(open(OUT / "dataset.json"))
qualified = json.load(open(OUT / "qualified_gates.json"))


def hit(r, feat, op, th):
    v = r.get(feat)
    if v is None:
        return False
    return v > th if op == ">" else v < th


def eval_block(pred, label):
    per_day = []
    tot_save = tot_block = tot_wl = 0
    big_winners_lost = 0
    worst = 1e18
    for day in DAYS:
        rr = [r for r in rows if r["day"] == day]
        blocked = [r for r in rr if pred(r)]
        save = -sum(r["pnl"] for r in blocked)
        wl = sum(1 for r in blocked if r["pnl"] > 0)
        big_winners_lost += sum(1 for r in blocked if r["peak"] >= 10)
        per_day.append(f"{day[8:]}:{save:+.0f}")
        tot_save += save; tot_block += len(blocked); tot_wl += wl
        worst = min(worst, save)
    print(f"{label:64} save3d={tot_save:>8.0f} worst={worst:>7.0f} blocked={tot_block:>4} "
          f"winLost={tot_wl:>3} bigWinLost={big_winners_lost:>2}  {'  '.join(per_day)}")
    return tot_save, worst


print("=== (a) top qualified gates BY ENTRY TYPE ===")
top = qualified[:6]
for g in top:
    for et in ["TREND_CONTINUATION", "VWAP_BREAKOUT"]:
        pred = lambda r, g=g, et=et: r.get("entry_type") == et and hit(r, g["feat"], g["op"], g["th"])
        eval_block(pred, f"{g['feat']}{g['op']}{g['th']} [{et[:10]}]")

print("\n=== (b) 2-gate OR combos (block if EITHER fires) of top-6 qualified ===")
combo_results = []
for g1, g2 in combinations(top, 2):
    if g1["feat"] == g2["feat"]:
        continue
    pred = lambda r, g1=g1, g2=g2: hit(r, g1["feat"], g1["op"], g1["th"]) or hit(r, g2["feat"], g2["op"], g2["th"])
    s, w = eval_block(pred, f"{g1['feat']}{g1['op']}{g1['th']} OR {g2['feat']}{g2['op']}{g2['th']}")
    combo_results.append((s, w, g1, g2))

print("\n=== (c) 2-gate AND combos (block only if BOTH fire — surgical) ===")
for g1, g2 in combinations(top, 2):
    if g1["feat"] == g2["feat"]:
        continue
    pred = lambda r, g1=g1, g2=g2: hit(r, g1["feat"], g1["op"], g1["th"]) and hit(r, g2["feat"], g2["op"], g2["th"])
    eval_block(pred, f"{g1['feat']}{g1['op']}{g1['th']} AND {g2['feat']}{g2['op']}{g2['th']}")
