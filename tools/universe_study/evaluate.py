"""Gate evaluator: for each candidate feature, scan thresholds and measure
(losers cut, winners lost, net PnL saved) PER DAY. A gate only qualifies if it
is net-positive (or at worst neutral) on ALL THREE days — the H1-H3 lesson:
anything that only works on one day's regime is curve-fit noise.

Direction convention: gate BLOCKS entry when feature <op> threshold.
"""
import json
from pathlib import Path

OUT = Path("/tmp/claude-0/-root-santhosh-trading/98d4451b-b7bc-4a03-8c57-47e6e8a5a7c0/scratchpad/backtest")
DAYS = ["2026-07-13", "2026-07-14", "2026-07-15"]

rows = json.load(open(OUT / "dataset.json"))

FEATURES = {
    # feature: (block_direction, candidate thresholds)
    "move_from_open":   (">", [0.8, 1.0, 1.25, 1.5, 2.0]),
    "run_5m":           (">", [0.4, 0.6, 0.8, 1.0]),
    "run_15m":          (">", [0.8, 1.0, 1.25, 1.5]),
    "gap_pct":          (">", [0.5, 0.75, 1.0, 1.5]),
    "dist_day_high":    ("<", [-0.3, -0.2, -0.15, -0.1]),   # block if entry is far BELOW day high (stalled/pulled back)
    "vol_ratio_5":      ("<", [0.5, 0.7, 0.9, 1.1]),        # block if entry-bar volume is fading vs last 5
    "vol_fade":         ("<", [0.2, 0.3, 0.5]),             # block if recent volume collapsed vs open volume
    "green_streak":     (">", [4, 5, 6, 8]),                 # block if too many consecutive green bars (late)
    "entry_bar_red":    (">", [0]),                          # block if signal bar closed red
    "rsi":              (">", [70, 74, 78, 80]),
    "rsi_slope3":       ("<", [-2, -1, 0]),                  # block if RSI falling into entry
    "rsi_from_day_peak":("<", [-8, -5, -3]),                 # block if RSI well off its day peak (momentum already rolled)
    "adx":              (">", [40, 45, 50]),
    "adx_slope3":       ("<", [-1, 0]),
    "hist_slope3":      ("<", [0]),                          # block if MACD hist declining into entry
    "ext_vwap":         (">", [1.0, 1.5, 2.0, 2.5]),
    "ext_upper":        (">", [0.3, 0.5, 0.8]),
    "vwap_slope3":      ("<", [0.0, 0.02, 0.05]),
    "bars_since_open":  ("<", [3, 5, 8]),                    # block first N minutes entirely
}


def apply_gate(rr, feat, op, th):
    blocked, kept = [], []
    for r in rr:
        v = r.get(feat)
        if v is None:
            kept.append(r)  # missing data -> fail open
            continue
        hit = v > th if op == ">" else v < th
        (blocked if hit else kept).append(r)
    return blocked, kept


def day_stats(rr):
    w = [r for r in rr if r["pnl"] > 0]
    return len(rr), len(w), sum(r["pnl"] for r in rr)


results = []
for feat, (op, ths) in FEATURES.items():
    for th in ths:
        per_day = {}
        ok = True
        for day in DAYS:
            rr = [r for r in rows if r["day"] == day]
            blocked, kept = apply_gate(rr, feat, op, th)
            nb, wb, pb = day_stats(blocked)   # what the gate removes
            nk, wk, pk = day_stats(kept)
            per_day[day] = {"blocked_n": nb, "blocked_winners": wb, "blocked_pnl": pb,
                            "kept_n": nk, "kept_pnl": pk}
        # gate saves money when blocked_pnl is NEGATIVE. Consistency: saved >= -500 every day.
        saves = [-per_day[d]["blocked_pnl"] for d in DAYS]
        total_save = sum(saves)
        worst_day = min(saves)
        blocked_total = sum(per_day[d]["blocked_n"] for d in DAYS)
        winners_lost = sum(per_day[d]["blocked_winners"] for d in DAYS)
        if blocked_total == 0:
            continue
        results.append({"feat": feat, "op": op, "th": th, "total_save": total_save,
                        "worst_day_save": worst_day, "blocked_total": blocked_total,
                        "winners_lost": winners_lost, "per_day": per_day})

# Rank: only gates that never lose more than a token amount on any day, by total saved
qualified = [r for r in results if r["worst_day_save"] > -1000]
qualified.sort(key=lambda r: -r["total_save"])

print(f"total rows={len(rows)}  | gates tested={len(results)}  | qualified (no day worse than -1000): {len(qualified)}")
print(f"\n{'gate':38} {'saved3d':>9} {'worstDay':>9} {'blocked':>8} {'winLost':>8}   per-day saved")
for r in qualified[:18]:
    pd = "  ".join(f"{d[8:]}:{-r['per_day'][d]['blocked_pnl']:+.0f}" for d in DAYS)
    print(f"{r['feat']:>18} {r['op']} {r['th']:<8} {r['total_save']:>9.0f} {r['worst_day_save']:>9.0f} "
          f"{r['blocked_total']:>8} {r['winners_lost']:>8}   {pd}")

print("\n--- top gates that FAILED consistency (best single-day but losing another day) ---")
failed = [r for r in results if r["worst_day_save"] <= -1000]
failed.sort(key=lambda r: -r["total_save"])
for r in failed[:8]:
    pd = "  ".join(f"{d[8:]}:{-r['per_day'][d]['blocked_pnl']:+.0f}" for d in DAYS)
    print(f"{r['feat']:>18} {r['op']} {r['th']:<8} {r['total_save']:>9.0f} {r['worst_day_save']:>9.0f} "
          f"{r['blocked_total']:>8} {r['winners_lost']:>8}   {pd}")

json.dump(qualified, open(OUT / "qualified_gates.json", "w"))
