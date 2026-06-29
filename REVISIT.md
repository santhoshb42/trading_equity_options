# REVISIT — Pending Observations / Deferred Decisions

Findings collected here to revisit after 3-4 more days of live trading data.

---

## [2026-06-16] CE Signal Type Performance — MOMENTUM_ACCELERATION Filter

**Data basis:** 1 day (2026-06-16 paper trading), 49 OTM-CE + 44 ITM-CE trades. Source: `option_pnl_history.json` → `entry_context.entry_type`.

### Core finding

| Signal | Bot | Trades | WR | Net PnL | STALE_EARLY | avg macd_hist at entry |
|--------|-----|--------|----|---------|-------------|------------------------|
| DEEP_MACD_REVERSAL | OTM | 18 | 61% | +₹5,838 | 4 (0% WR) | 0.93 |
| DEEP_MACD_REVERSAL | ITM | 18 | 56% | +₹5,322 | 4 (0% WR) | 1.09 |
| MOMENTUM_ACCELERATION | OTM | 25 | 36% | **−₹4,164** | **12 (0% WR)** | **0.62** |
| MOMENTUM_ACCELERATION | ITM | 22 | 45% | **−₹1,606** | **9 (0% WR)** | **0.32** |

- **DEEP_MACD_REVERSAL is net positive** on both bots. STALE_EARLY losses are 4 each and contained.
- **MOMENTUM_ACCELERATION is net negative** on both. The 12+9 STALE_EARLY losses (trades that never moved from entry, peak=0%) wipe out the TRIAL_SL wins.
- STALE_EARLY MA trades have low macd_hist (avg 0.62 OTM, 0.32 ITM) vs DEEP's avg 0.93/1.09. The histogram hasn't built conviction before the signal fires.
- RSI at entry for MA STALE_EARLY: avg 68 — already stretched. DEEP trades entering on reversal have lower RSI (avg 55–63).

### What was already done for this
- v7.5 added `histContMCFloor=0.80` gate for MOMENTUM_CONTINUATION → WR 24%→44%.
- MOMENTUM_ACCELERATION has **no equivalent floor gate**.

### Proposed action (do NOT implement until 3-4 days of data confirms pattern)
Add `macdHist >= 0.65` gate for MOMENTUM_ACCELERATION in `Sniper_CE` (similar to what `histContMCFloor` does for Continuation). This would cut the low-conviction MA entries (macd_hist 0.32–0.62) without touching the strong ones.

**Revisit by: 2026-06-20** — run same `option_pnl_history.json` analysis across 4+ days. If MA STALE_EARLY rate stays > 40% of MA trades, add the gate. If it drops to ~20%, today was noise.

### How to query this analysis
```bash
python3 -c "
import json
from collections import defaultdict
d = json.load(open('CE_OPTIONS/OTM/data/option_pnl_history.json'))
today = [t for t in d if '2026-06-XX' in t.get('entry_time','')]  # change date
by_type = defaultdict(list)
for t in today:
    et = t.get('entry_context', {}).get('entry_type', 'UNKNOWN')
    by_type[et].append(t)
for et, trades in sorted(by_type.items()):
    wins = [t for t in trades if t['pnl'] > 0]
    stale_early = [t for t in trades if 'early' in t.get('exit_reason','')]
    print(f'{et}: {len(trades)} trades | WR {len(wins)/len(trades)*100:.0f}% | STALE_EARLY {len(stale_early)}')
"
```

---

## [2026-06-15] Trading Time Cutoff Analysis

**Observation:** Trading until 3:00 PM is hurting total PnL. Historical analysis across 4,534 trades (2025-12-01 → 2026-06-15, 116 days) shows two clear problems:

### Problem 1 — Late-day degradation (user's original concern)
Stopping new entries at **14:30** is optimal vs. trading until 15:00:
- Stop at 14:30 → ₹1,77,941 total PnL
- Stop at 15:00 → ₹1,38,464 total PnL
- **Delta: +₹39,477** by stopping 30 min earlier

The 14:45 slot alone is the worst of the day: 35.4% win rate, -₹515 avg/trade, -₹33,479 total (65 trades).

### Problem 2 — Early morning chop window (bigger issue)
The 09:45–10:15 window is responsible for **-₹3,60,000+** across ~2,800 trades (44–50% win rate).
By contrast, 09:30 is the single best slot: 66.1% win rate, +₹1,64,533 (168 trades).

### Entry slot summary
| Slot | Trades | PnL | Win% | Verdict |
|------|--------|-----|------|---------|
| 09:30 | 168 | +₹1,64,533 | 66.1% | Best slot |
| 09:45–10:15 | 2,814 | -₹3,60,815 | ~45% | Worst window |
| 10:30–10:45 | 475 | +₹2,01,217 | ~51% | Good recovery |
| 11:15–11:30 | 140 | +₹81,884 | ~61% | Solid |
| 14:30 | 81 | -₹5,998 | 44.4% | Marginal |
| 14:45 | 65 | -₹33,479 | 35.4% | Clear loser |
| 15:00 | 34 | -₹1,790 | 41.2% | Marginal |

### Proposed changes (deferred — needs more live data)
1. Add entry blackout: **09:45–10:15** (block new entries in this window)
2. Stop new entries at **14:30** instead of 15:00

**Revisit by: 2026-06-22** — re-run this analysis with updated trade data before implementing.

---

## [2026-06-13] Pine Script Audit — Issues for Discussion

Audited: 2026-06-13
Scripts: Sniper_CE, Sniper_PUT.pine, Index_NIFTY_MACD_Combo.pine, equity/docs/TRADINGVIEW_PINE_SCRIPT.pine

Legend: [BUG] = correctness issue affecting live signals | [ASYMMETRY] = inconsistency between scripts | [QUALITY] = signal dilution / calibration

---

### B1 — Sniper_CE: `macdReversalTrigger` missing `inTradeWindow`
- **File:** `pinescripts/Sniper_CE`, line ~190
- **Issue:** The primary MACD_REVERSAL signal has no time-window guard. The other two signals (DEEP, MOMENTUM) both check `inTradeWindow` (09:30–15:30), but the main trigger is missing it.
- **Effect:** MACD_REVERSAL alerts can fire pre-market and post-market. Bot rejects them, but wasteful.
- **Fix:** Add `and inTradeWindow` to `macdReversalTrigger` condition.
- **Status:** OPEN

---

### B2 — Index_NIFTY_MACD_Combo.pine: `callHistCandle2` double-counted in score
- **File:** `pinescripts/Index_NIFTY_MACD_Combo.pine`, lines 117–118 (and 129–130 for put)
- **Issue:** Two consecutive lines add score for the same `callHistCandle2` condition (16 pts + 18 pts = 34 pts). Same bug in `putScore`. The input `callHistCandle3Factor = 1.3` is defined but never used — candle-3 logic was never implemented; candle-2 line was copy-pasted instead.
- **Effect:** Histogram confirmation candle gets disproportionate weight. Relative weighting is wrong.
- **Fix:** Remove the duplicate line; implement candle-3 check separately using `callHistCandle3Factor`, or remove that input.
- **Status:** OPEN

---

### B3 — Index_NIFTY_MACD_Combo.pine: `putRsiCeiling` is dead code
- **File:** `pinescripts/Index_NIFTY_MACD_Combo.pine`, line ~22
- **Issue:** `putRsiCeiling = input.float(48.0, ...)` is defined and visible in TradingView settings but never referenced in any trigger or scoring logic.
- **Effect:** User adjusts this thinking it controls RSI gate — it has zero effect.
- **Fix:** Wire it into `putRsiLead` as an upper bound, or remove the input.
- **Status:** OPEN

---

### B4 — Equity Script: `pdh` uses `lookahead_on` (backtest repainting)
- **File:** `equity/docs/TRADINGVIEW_PINE_SCRIPT.pine`, line ~37
- **Issue:** `pdc` correctly uses `barmerge.lookahead_off` but `pdh` omits it, defaulting to `lookahead_on`. In live trading this is harmless. In historical replay it leaks future bar's high into current bar.
- **Effect:** Historical backtests on this script are unreliable for PDH-gated entries.
- **Fix:** Add `barmerge.lookahead_off` to the `pdh` security call.
- **Status:** OPEN (low priority — backtests only)

---

### B5 — Sniper_CE + Sniper_PUT: DEEP/MOMENTUM alerts fire intrabar
- **File:** `pinescripts/Sniper_CE` lines ~231–236; `pinescripts/Sniper_PUT.pine` lines ~234–239
- **Issue:** `macdReversalTrigger` fires `alert.freq_once_per_bar_close` (waits for confirmed bar close). But `deepReversalTrigger` and `momentumAccelTrigger` (both scripts) fire `alert.freq_once_per_bar` — fires on the FIRST tick the condition becomes true, mid-bar.
- **Effect:** An intrabar alert fires when conditions look right at 10:31:23, but the bar closes differently at 10:32:00. Bot processes a signal based on unstable intrabar data.
- **Fix:** Change DEEP and MOMENTUM alerts to `alert.freq_once_per_bar_close`.
- **Status:** OPEN — this may explain some STALE_EARLY failures (signal valid mid-bar, invalidated at close)

---

### A1 — CE vs PUT: Different reference level for day-direction gate
- **Sniper_CE** line ~178: `priceAbovePrevClose = close > prevDayClose` — compares to YESTERDAY's close
- **Sniper_PUT** line ~194: `priceBelowDayOpen = close < dayOpen` — compares to TODAY's open
- **Issue:** CE uses a cross-day reference (easier to clear on any green day). PUT uses same-day reference (tighter). Inconsistent conviction thresholds.
- **Status:** OPEN — discuss which reference is preferred

---

### A2 — Market trend thresholds differ across all 3 scripts
| Script | GOOD | BAD |
|--------|------|-----|
| Sniper_CE | ≥ 0.5% | ≤ −0.4% |
| Sniper_PUT | ≥ 0.4% | ≤ −0.5% |
| Index_NIFTY_MACD_Combo | ≥ 0.35% | ≤ −0.35% |

- **Issue:** On a day NIFTY is up 0.42%, CE sends `NEUTRAL`, PUT sends `GOOD`, Index sends `GOOD`. CE bot is structurally more restrictive than PE bot on same-market conditions.
- **Fix:** Agree on a single threshold set and apply consistently across all 3 scripts.
- **Status:** OPEN

---

### Q1 — Sniper_PUT: `continuationSequenceReady` is too permissive
- **File:** `pinescripts/Sniper_PUT.pine`, lines ~91–94
- **Issue:** Continuation model only requires `macdHist < -histFloor` + `volume > volMa * 0.9` (below-average volume accepted). No histogram shift requirement. `sequenceReady = burstSequenceReady OR continuationSequenceReady` — either path fires.
- **Effect:** In any sustained bearish trend, almost every bar satisfies the continuation model. Signal count inflates.
- **Fix:** Raise continuation volume to 1.0× minimum, or remove continuation path.
- **Status:** OPEN

---

### Q2 — CE + PUT: `priceBurstCandle2` rarely filters on 1-min charts
- **File:** `pinescripts/Sniper_CE` line ~92; `pinescripts/Sniper_PUT.pine` line ~84
- **Issue:** `open > close[1]` (CE) / `open <= close[1]` (PUT) — on liquid 1-min instruments every bar opens within a tick of prior bar's close. This condition is almost always true and provides no discrimination.
- **Effect:** Dead gate — passes on nearly every candle.
- **Potential fix:** Replace with `open > ema9` (CE) or `open < ema9` (PUT).
- **Status:** OPEN

---

### Q3 — Equity script: `fVolume` accepts 5% above last bar as valid
- **File:** `equity/docs/TRADINGVIEW_PINE_SCRIPT.pine`, lines ~52–55
- **Issue:** `fVolume = v1 or v2 or v3` where v3 = `volume > volume[1] * 1.05`. Minor uptick in volume satisfies v3 alone, bypassing the stronger v1 and v2 checks.
- **Effect:** Volume gate is effectively off on low-volume mornings.
- **Fix:** Change to `v1 or (v2 and v3)`.
- **Status:** OPEN

---

### Q4 — Equity script: `confidence` is always 95.0
- **File:** `equity/docs/TRADINGVIEW_PINE_SCRIPT.pine`, lines ~84–93
- **Issue:** `igniteEvent` requires all 6 gates simultaneously → score always 75 (max) → `confidence` always 95.0. The field carries zero information.
- **Fix:** Use partial-score confidence, or remove the field and document equity signals as binary.
- **Status:** OPEN (low priority)

---

### Q5 — Sniper_CE: Confidence formula denominators inconsistent
- **File:** `pinescripts/Sniper_CE`, lines ~205–207
- **Issue:** All three signals use same 6 scoring components (max 100) but normalize with different divisors: 85, 75, 80. A score of 80/100 gives DEEP confidence = 107% (clamped 95%), MOMENTUM = 100% (clamped 90%). Not on comparable scales.
- **Effect:** Signal types appear to have different confidence levels even when conditions are equally strong. If bot filters by confidence threshold, some types are structurally favored.
- **Fix:** Use the same denominator across all three.
- **Status:** OPEN

---

## Status Tracker

| ID | Script | Type | Priority | Status | Revisit By |
|----|--------|------|----------|--------|------------|
| CE-MA | Sniper_CE | Signal Quality | **High** | OPEN | 2026-06-20 |
| TC-1 | Bot config | Time cutoff | **High** | OPEN | 2026-06-22 |
| TC-2 | Bot config | Morning blackout | **High** | OPEN | 2026-06-22 |
| B1 | Sniper_CE | Bug | Medium | OPEN | — |
| B2 | Index Combo | Bug | High | OPEN | — |
| B3 | Index Combo | Bug | Low | OPEN | — |
| B4 | Equity | Bug | Low (backtests only) | OPEN | — |
| B5 | CE + PUT | Bug | Medium | OPEN | — |
| A1 | CE vs PUT | Asymmetry | Medium | OPEN | — |
| A2 | All 3 | Asymmetry | Medium | OPEN | — |
| Q1 | PUT | Quality | Medium | OPEN | — |
| Q2 | CE + PUT | Quality | Low | OPEN | — |
| Q3 | Equity | Quality | Medium | OPEN | — |
| Q4 | Equity | Quality | Low | OPEN | — |
| Q5 | Sniper_CE | Quality | Low | OPEN | — |
