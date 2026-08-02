# Sniper VWAP Strategy — CE (`Sniper_VWAP`) & PUT (`Sniper_VWAP_PUT`)

> Context doc for review. Two TradingView Pine v6 indicators that fire webhook alerts into a
> pair of options-trading bots (CE bot / PUT bot). This describes what they do, why, the exact
> entry logic, the bot contract, and the open questions we want a second opinion on.

---

## 1. Big picture

- **Universe:** Indian equity/index options, scanned on the **1-minute** chart. One indicator instance
  runs per underlying symbol; each fires a webhook alert to the bot when its entry condition is met.
- **CE side (`Sniper_VWAP`)** = bullish. Buys **CALL** options on an upward VWAP-**line** breakout.
- **PUT side (`Sniper_VWAP_PUT`)** = bearish mirror. Buys **PUT** options on a VWAP-line breakdown.
- **Division of labour:** the Pine scripts own **ENTRIES only**. All EXITS (TRIAL_SL / HARD_SL /
  EOD square-off) are owned by the bot. This is deliberate — strategy logic lives in Pine, risk
  management lives in the bot, so we can tune exits without re-pasting scripts into TradingView.
- **Timeframe guard:** both scripts no-op unless `timeframe.multiplier == 1` and minutes
  (`isOneMin`), and paint a red background otherwise as a "use the 1-min chart" reminder.
- **Session window (v26 CE / v10 PUT):** both fire only in TWO trading sessions — **09:30–11:00** and
  **13:30–14:30** IST — not one continuous span. No entries at all outside these two windows (the
  late-morning/midday chop and the last 30 minutes are fully excluded). The start was 09:45 for one
  version (v19-v25 CE, v8-v9 PUT) but reverted to 09:30: that shift was based on a single day's data,
  and real momentum in 09:30-09:44 was being missed since. The bot's own `ENTRY_FILTER_MARKET_OPEN`
  is now 930 for CE too (PUT's was never actually overridden to 945 — only CE's `.env` was — so this
  revert also fixes a pre-existing Pine/bot mismatch on the PUT side).
  ```pine
  minutesSinceMidnight = hourIST * 60 + minuteIST
  inWindow1 = minutesSinceMidnight >= 570 and minutesSinceMidnight <= 660   // 09:30-11:00
  inWindow2 = minutesSinceMidnight >= 810 and minutesSinceMidnight <= 870   // 13:30-14:30
  inTradeWindow = inWindow1 or inWindow2
  ```

---

## 2. Shared building blocks

### 2.1 Manual session VWAP (resets daily)
Both compute a **session-anchored** VWAP by hand (not `ta.vwap`) so it resets cleanly at the IST day
boundary and so we can derive the standard-deviation bands from the same running sums:

```pine
sumSrcVol     += hlc3 * volume
sumVol        += volume
sumSrcSrcVol  += hlc3 * hlc3 * volume         // for variance

vwapVal   = sumSrcVol / sumVol
variance  = sumSrcSrcVol / sumVol - vwapVal^2
stdevVal  = sqrt(max(variance, 0))
vwapUpper = vwapVal + mult * stdevVal          // CE pattern 2 (continuation) entry level
vwapLower = vwapVal - mult * stdevVal          // PUT pattern 2 (continuation) entry level, v11+
```

- `mult` default **1.0** (≈ ±1σ band).
- All running sums (and `entryOpen`, `barsSinceBreak`, `dayOpen`) reset on `ta.change(time("D"))`.

### 2.2 Repaint-safe previous-day close
```pine
prevDayClose = request.security(tickerid, "D", close[1], gaps_off, lookahead_on)
```
`close[1]` + `lookahead_on` is the standard repaint-safe idiom for "yesterday's confirmed close."

### 2.3 The FRESH-BREAK gate (the fix under review)
**Both scripts share this pattern** — the reason this doc exists.

**Problem:** the raw breakout condition (`close > vwapUpper` for CE, `close < vwapVal` for PUT) is a
**STATE**, not an event. It stays true for the *entire* leg the price spends above/below the band.
The other filters (RSI rising/falling, MACD falling) can align **many bars later**, so the alert
would fire deep into — or bouncing off — an already-exhausted move (observed live on BOSCH: entry
fired 5–6+ candles after the actual band cross, during exhaustion).

**Fix:** gate the entry to fire only within `maxBarsAfterBreak` (default **3**) bars of the *genuine*
cross, using `ta.crossover` / `ta.crossunder` to detect the event:

```pine
// CE:  crossedUpVwap   = ta.crossover(close, vwapUpper)
// PUT: crossedDownVwap = ta.crossunder(close, vwapVal)
if crossed
    barsSinceBreak := 0
else if stillBrokenOut          // close > vwapUpper (CE) / close < vwapVal (PUT)
    barsSinceBreak := barsSinceBreak + 1
else
    barsSinceBreak := 9999       // price returned to the other side → window invalid
freshBreak = barsSinceBreak <= maxBarsAfterBreak
```

`barsSinceBreak` is a `var int` seeded at `9999`, reset to `9999` on new day and whenever price
crosses back, so a stale/prior-day cross can never open a valid window.

---

## 3. `Sniper_VWAP` (CE / bullish) — v30

**v20 ran a dual-level A/B (VWAP-line vs upper-band entry) for one session.** Result: the upper-band
cross fires late — into an already-exhausted move — noisy and low quality vs the earlier line cross.
**v21 removed the upper-band entry entirely.** `vwapUpper` is now a reference plot only (mirrors the
PUT script's `vwapLower` treatment), no longer part of entry logic. **v22 added a VWAP-slope gate**
— the biggest remaining gap at the time: nothing checked whether VWAP itself was trending.

**v23 adds a SECOND, independent entry pattern: TREND CONTINUATION.** v21's removal of the
upper-band entry was based on an average — "upper-band crosses fire late" — but average isn't
always. Some upper-band situations are the START of a strong continuation leg, not the tail of an
exhausted one. The distinguishing signal is trend strength (ADX), not price level. v23 reintroduces
an upper-band entry, but now gated on ADX rather than the old ungated `close > vwapUpper` v20 had.
It is a fully separate condition block with its own `entry_type` (`TREND_CONTINUATION` vs
`VWAP_BREAKOUT`) so the two patterns' win rates can be measured independently in the trade log.
**`enableContinuationEntry` defaulted to `false` in v23-v26, flipped to `true` in v27** (decision
2026-07-12): continuation goes live Monday alongside pattern 1. The per-pattern `entry_type` tag
keeps their win rates independently measurable despite launching together. NOTE: TradingView alerts
snapshot input values at CREATION time — the default only matters for alerts (re)created from a
chart running v27+; pre-existing alerts keep whatever value they were created with.

**v24 is presentation-only, for backtesting: both `ENTRY-B` and `ENTRY-C` chart labels now carry a
hover TOOLTIP** with the actual gate values at that bar (bars-since-cross, RSI, MACD hist, VWAP
slope for pattern 1; VWAP upper, ADX, RSI-vs-lookback for pattern 2) — replay/backtest can verify
WHY a signal fired without cross-referencing `alerts.jsonl`. No entry logic changed.
`max_labels_count=500` is already TradingView's indicator ceiling (cannot be raised further); one
label per entry/exit comfortably fits a full session on any single symbol.

**v29 adds three "H9" exhaustion gates to BOTH entry patterns** (v28 was a title-only bump). Derived
from a 3-day candle-level backtest (2026-07-13 trend day / 07-14 chop day / 07-15 gap-up day, 341
deduped traded signals, real broker 1-min candles, no lookahead) with a hard consistency rule: a
gate qualifies only if net-positive on ALL three days. The package would have cut the 3-day net
from −₹128,687 to −₹47,557 while keeping 8 of the top-10 winners:
1. **VOL_FADE** (`volume >= 0.7 × sma(volume,5)[1]`) — the workhorse (−₹57K of losers). Losers are
   exhaustion entries: the alert fires on the last gasp after participation has left. Known cost:
   rare quiet-bar-then-explode winners are lost at any threshold.
2. **ADX_EXHAUST** (block if `ADX > 40 and ADX < ADX[3]`) — high-and-falling trend (−₹26K, near-zero
   winner cost). Note this also gives pattern 1 (breakout) an ADX check for the first time.
3. **BIG_GAP** (block if day open gapped > +0.75% over prev close) — the move already happened in
   the gap (−₹17K; fires zero times on normal-open days). Directly targets the 07-15 failure mode:
   141 opening-cohort entries on a broad gap-up morning, most dead on arrival.
All three are independent `input.bool` toggles, default ON. Payloads now carry `vol_ratio` and
`gap_pct` (and breakout now sends `adx_value` too) so live gate behavior is verifiable from
`alerts.jsonl`. Rejected alternatives (failed cross-day consistency; do NOT add): distance-from-
day-high, RSI-off-day-peak, volume-vs-open fade, blanket VWAP-slope-strength.

**v30 adds four "H12" strong-momentum gates from the 30-DAY universe study** (210 F&O stocks, 20
trading days 06-17..07-15, ~1.9M bar-observations, all 1,024 AND-combos tested with per-day
consistency required). Both patterns, all toggles default ON:
1. **THRUST** — 5-min move ≥ 0.4% (`close/close[5]`). The 0-0.15% "drift" state lost to the
   universe base rate on 20 of 20 days — the single most reliable filter found.
2. **ADX_STRONG** — ADX ≥ 40 (beat base 18/20 days). With v29's ADX_EXHAUST the net requirement
   is ADX ≥ 40 and not rolling over; subsumes continuation's old `adxMinLevel=20` when ON.
3. **RSI_STRONG** — RSI ≥ 68 (the 70-80 band beat base 17/20 days — high RSI is GOOD at the
   60-min horizon; the bot's >82 ceiling still applies downstream, effective band ≈ 68-82).
4. **HIST_5M** — last CLOSED 5-min MACD histogram > 0 (`request.security` "5", non-repainting
   `[1]`+`lookahead_on` idiom) — multi-timeframe confirmation.
(The study rule's 5th condition, price > prev-day close, was already present since v22/v23.)
Payloads add `run_5m` + `macd_hist_5m`. Honest framing: these gates lift study WR ~31%→~46-48%
and collapse junk volume on chop days (sim: 12 signals on 07-14 vs the 96 actually taken) — a
risk-reducer, NOT a profit engine; the same study proved best-possible 1-min entries are ~breakeven
before costs. Profit levers live bot-side: session-health sizing (H13.1), 10-min early-loser cut
(H13.2), wide progressive TRIAL_SL (H13.3). Nightly `universe-validation.timer` re-validates the
conditions on each new trading day. Expect visibly FEWER signals, especially on chop days.

### 3a. Entry pattern 1 — Fresh VWAP-line cross (v18-v22, unchanged in v23-v24)

**Entry fires when ALL are true (on bar close):**

| # | Condition | Code |
|---|-----------|------|
| 1 | **Fresh** cross above the VWAP **line** (≤ `maxBarsAfterBreak` bars since the crossover) | `freshLineBreak` |
| 2 | RSI(14) rising over the last 2 bars | `ta.rising(rsi, 2)` |
| 3 | **MACD histogram rising** for the last `macdHistBars` bars (default 2) | see below |
| 4 | Price above the previous-day close (bullish-on-day) | `close > prevDayClose` |
| 5 | **VWAP line itself rising** over the last 3 bars (v22) | `vwapSlopeOK` |
| 6 | Inside one of the two trading sessions: 09:30–11:00 or 13:30–14:30 IST (v26) | `inTradeWindow` |
| 7 | 1-minute chart | `isOneMin` |

```pine
histRisingOK = ta.rising(macdHist, macdHistBars)
vwapSlopeOK  = vwapVal > vwapVal[3]
conditionMet = freshLineBreak and rsiRisingOK and histRisingOK
             and priceAbovePrevClose and vwapSlopeOK and inTradeWindow and isOneMin
entrySignal  = conditionMet and not entryOpen
```

- **VWAP-slope gate (v22):** `close > vwapVal` only checks that price is above VWAP, not that VWAP
  itself is trending. A fresh cross above a FLAT VWAP usually means the session is chopping — the
  breakout is more likely a fakeout that doesn't carry the next 4-5 candles. Requiring VWAP to have
  actually risen over the last 3 bars filters that out with one relative comparison, no new indicator.
- **Histogram gate — v21 FIXED the v20 bug:** v20 required `|hist|` to be *shrinking* unconditionally,
  which wrongly rejected a positive-and-EXPANDING histogram (strong building bullish momentum — exactly
  what a breakout should look like). The correct directional rule is: negative histogram must shrink
  toward zero (sellers fading); positive histogram must expand away from zero (buyers building). Both
  cases are algebraically the SAME test — **hist rising bar-over-bar** (less-negative = rising;
  more-positive = rising) — so the gate simplifies to `ta.rising(macdHist, N)`.
- **`entryOpen` latch:** fires once per breakout **leg** — releases when price falls back below the
  VWAP line, so a NEW fresh cross later in the day can fire again (unchanged from v19).
- **Exit:** `enableVwapExit = false` by default. The optional dip-exit and the old EOD square-off are
  both disabled — the bot owns all exits.
- **Alert payload** (`action:BUY`, hard-coded `confidence=90`/`score=90` — bot rejects below 90):
  `symbol, action, confidence, score, entry_type=VWAP_BREAKOUT, price, vwap_value, vwap_upper
  (reference), rsi_value, macd_line, macd_signal, macd_hist, prev_day_close, tv_setup_label`.

### 3b. Entry pattern 2 — Trend continuation above VWAP upper (v23; ON by default since v27)

| # | Condition | Code |
|---|-----------|------|
| 1 | Master toggle | `enableContinuationEntry` (default **true** since v27) |
| 2 | Close above the VWAP **upper band** (the level v21 excluded from pattern 1) | `close > vwapUpper` |
| 3 | ADX ≥ `adxMinLevel` (default 20) — actually trending, not choppy | `adxTrendingOK` |
| 4 | ADX rising over `adxRisingBars` (default 2) — trend still building, not fading | `adxRisingOK` |
| 5 | RSI not rolling over: `rsi > rsi[rsiContinuationLookback]` (default 3 bars back) | `rsiNotRollingOver` |
| 6 | Price above the previous-day close | `priceAbovePrevClose` |
| 7 | Inside window, 1-minute chart | `inTradeWindow and isOneMin` |

```pine
[diPlus, diMinus, adxVal] = ta.dmi(adxLen, adxLen)
adxTrendingOK = adxVal >= adxMinLevel
adxRisingOK   = ta.rising(adxVal, adxRisingBars)
rsiNotRollingOver = rsi > rsi[rsiContinuationLookback]

conditionMet_Continuation = enableContinuationEntry and close > vwapUpper and adxTrendingOK
                           and adxRisingOK and rsiNotRollingOver and priceAbovePrevClose
                           and inTradeWindow and isOneMin
entrySignal_Continuation  = conditionMet_Continuation and not entryOpen
```

- **Worked example (GAIL, the chart that motivated this):** at the point the move was already fading,
  ADX had dropped to ~11 — well under the 20 minimum — so `adxTrendingOK` alone would have blocked a
  continuation entry into that exhaustion tail, matching the intent.
- **Shared latch:** `entrySignal = entrySignal_Breakout or entrySignal_Continuation`, and both feed
  the SAME `entryOpen` latch / re-arm-below-VWAP-line logic as pattern 1. This means: if a fresh-cross
  entry (3a) fires first, a continuation entry (3b) can't also fire on the same leg, and vice versa —
  the two patterns are mutually exclusive per leg, not stacked. A rare edge case: a bar that gaps
  straight through both the line (within its freshness window) and the upper band with all filters
  true on both sides could in theory emit BOTH alerts on the same bar; harmless in practice since the
  bot only opens one position per symbol and treats the second as a no-op.
- **Alert payload adds** `entry_type=TREND_CONTINUATION` and `adx_value` alongside the same fields as
  pattern 1.

---

## 4. `Sniper_VWAP_PUT` (PUT / bearish) — v12

**v12 (2026-07-15) overturns the v11 blind-mirror with data (H15 study: 20 days × 210 symbols,
400K bearish candidates).** v11's core conditions (below PDC, falling RSI, falling MACD) scored
AT/BELOW the 29.2% down-move base rate — they are now LEGACY toggles, default OFF. The new
default rule on both patterns: **FLUSH** (5-min move ≤ −0.4%, beat base 19/20 days) + **GAP_DOWN**
(open ≤ −0.3% vs PDC) + **ADX rising**. Window changed to continuous 09:30-14:30 (the one real
selloff, 07-08, fired 11:46-13:57 — the old windows would have missed it). PUT profit is
EVENT-driven: ~all 20-day edge sat in that one selloff day (+₹689-746K sim) with steady bleed
between events; a **bot-side NIFTY bearish-session arm (session_eff≥10 AND session_net≤−0.4,
from the H13.1 daemon fields) is MANDATORY before alerts are enabled** — it kept ₹716K of the
crash while cutting between-event bleed to −₹29K. Validated on n=1 event; PUT stays paused until
≥5 bearish-healthy days accumulate in the nightly snapshots (H15 gate).

### 4-legacy. v11 description (superseded)

**v11 adds a SECOND, independent entry pattern: TREND CONTINUATION** — the bearish mirror of CE's
v23 pattern. Before v11, PUT was NOT a full inverse of CE: CE had two entry patterns since v23
(fresh-cross breakout + ADX-gated trend continuation), PUT only ever had the fresh-cross mirror.
`enableContinuationEntry` defaults to **false** (unlike CE's v27, which defaults true) — PUT has
far less trade history than CE had when its continuation went live by default.

### 4a. Entry pattern 1 — Fresh VWAP-line breakdown (v7-v10, unchanged in v11)

**Entry fires when ALL are true (on bar close):**

| # | Condition | Code |
|---|-----------|------|
| 1 | Close below the VWAP **line** | `close < vwapVal` |
| 2 | **Fresh** breakdown (≤ `maxBarsAfterBreak` bars since cross) | `freshBreakdown` |
| 3 | RSI(14) falling over the last 2 bars | `ta.falling(rsi, 2)` |
| 4 | MACD **line** below zero **and falling fast** | see below |
| 5 | Price below the previous-day close | `close < prevDayClose` |
| 6 | Price below the **day open** | `close < dayOpen` |
| 7 | Inside window + 1-min | `inTradeWindow and isOneMin` |

```pine
[macdLine, macdSignal, macdHist] = ta.macd(close, 12, 26, 9)
macdLineDrop2 = macdLine[2] - macdLine
macdFallingOK = macdLine < 0 and ta.falling(macdLine, 2)
              and macdLineDrop2 >= close * macdFallMinPct / 100   // "fast" = steep 2-bar drop

conditionMet_Breakdown = close < vwapVal and freshBreakdown and rsiFallingOK and macdFallingOK
             and priceBelowPrevClose and priceBelowDayOpen and inTradeWindow and isOneMin
entrySignal_Breakdown  = conditionMet_Breakdown and not entryOpen
```

- **Why the extra MACD "fast" clause (PUT pattern 1 only, not CE):** v5 required only
  `line<0 and falling`, but "falling" = *any* 2-bar decline, so a flat MACD drifting a hair below
  zero in dead chop still qualified (AUROBINDO 11:00–12:30, ADX ~16 → chop losses). v6 added: the
  2-bar line drop must exceed `macdFallMinPct` (default 0.02) **% of price** — scaling by price
  keeps the threshold comparable across a mixed-price universe.
- **`entryOpen` re-arm:** PUT re-arms when `close > vwapVal` (price recovers above the line), so it
  can fire **once per below-VWAP leg** — same cadence as CE (which re-arms per leg since v19).
  Shared with pattern 2 below (one latch for both).
- **Alert payload:** `action:SELL, original_action:BUY_PUT, option_side:PE, confidence=90, score=90,
  entry_type=VWAP_BREAKDOWN, price, vwap_value, rsi_value, macd_hist, prev_day_close, day_open,
  tv_setup_label`. The PUT bot has **no `is_exit` close handler** (unlike CE), so we deliberately
  send no exit alert (neither pattern sends one).

### 4b. Entry pattern 2 — Trend continuation below VWAP lower (v11, NEW, off by default)

| # | Condition | Code |
|---|-----------|------|
| 1 | Master toggle | `enableContinuationEntry` (default **false**) |
| 2 | Close below the VWAP **lower band** (the level pattern 1 does NOT require) | `close < vwapLower` |
| 3 | ADX ≥ `adxMinLevel` (default 20) — actually trending, not choppy | `adxTrendingOK` |
| 4 | ADX rising over `adxRisingBars` (default 2) — trend still building | `adxRisingOK` |
| 5 | RSI not recovering: `rsi < rsi[rsiContinuationLookback]` (still weak) | `rsiNotRecovering` |
| 6 | MACD line below zero and falling fast (**reuses pattern 1's `macdFallingOK`**) | see 4a |
| 7 | Price below the previous-day close | `priceBelowPrevClose` |
| 8 | Inside window, 1-minute chart | `inTradeWindow and isOneMin` |

```pine
[diPlus, diMinus, adxVal] = ta.dmi(adxLen, adxLen)
adxTrendingOK = adxVal >= adxMinLevel
adxRisingOK   = ta.rising(adxVal, adxRisingBars)
rsiNotRecovering = rsi < rsi[rsiContinuationLookback]

conditionMet_Continuation = enableContinuationEntry and close < vwapLower and adxTrendingOK
                           and adxRisingOK and rsiNotRecovering and macdFallingOK
                           and priceBelowPrevClose and inTradeWindow and isOneMin
entrySignal_Continuation  = conditionMet_Continuation and not entryOpen
```

- **Deliberate asymmetry vs CE's continuation:** CE's pattern 2 (v23) has NO MACD gate at all.
  PUT's pattern 2 reuses `macdFallingOK` (pattern 1's proven anti-chop gate) as an EXTRA
  confirmation — added on request for a stricter bearish check, since PUT has far less trade
  history than CE to lean on. Not a mirroring bug; a deliberate strictness difference.
- **No day-open check in pattern 2** (unlike pattern 1) — mirrors CE's continuation, which also
  has no day-open equivalent.
- **Shared latch:** `entrySignal = entrySignal_Breakdown or entrySignal_Continuation`, and both
  feed the same `entryOpen` re-arm (releases when `close > vwapVal`). Same mutual-exclusion and
  same rare same-bar-double-fire edge case as CE (see section 3b) — harmless, bot dedups.
- **Alert payload adds** `entry_type=TREND_CONTINUATION` and `adx_value`; bot-side, this entry_type
  is already recognized by `OptionsSignalValidator._min_quality_for_entry_type` (maps to
  `MIN_CONFIDENCE_TREND_CONTINUATION`, default 75) — the payload's confidence=90/score=90 clears
  it with room to spare.

---

## 5. Bot contract (how alerts become trades)

The webhook JSON is validated by two gates in the bot before an order is placed:

1. **Signal validator** — symbol/action/confidence/score. CE needs `action=BUY`; PUT needs
   `action=SELL` + `original_action=BUY_PUT` + `option_side=PE`. Both need `confidence=90` & `score=90`
   (hence the hard-coded 90s in the payload).
2. **Comprehensive entry filter** — 8 validators (premium, PCR/market-structure, RSI momentum, MA
   trend, IV, market-hours, expiry, setup-quality) with a data-aware pass threshold
   (`effective_required = min(required, evaluable)`, coverage floor 4). This is bot-side and can reject
   an otherwise-valid Pine alert (e.g. thin option premium, bad PCR).

So a Pine entry is **necessary but not sufficient** — the bot may still decline. Exits are 100% bot-owned
(TRIAL_SL progressive trail, HARD_SL, 10-min STALE_CONSOLIDATION for unarmed trades, EOD square-off).

---

## 6. Known asymmetries CE vs PUT (deliberate, flagged for review)

| Aspect | CE (`Sniper_VWAP` v30) | PUT (`Sniper_VWAP_PUT` v11) |
|---|---|---|
| Breakout/breakdown reference | **VWAP line** (pattern 1) + **VWAP upper band** (pattern 2, on by default since v27) | **VWAP line** (pattern 1) + **VWAP lower band** (pattern 2, off by default) |
| MACD gate | histogram **rising** N bars (directional) — pattern 1 only, NOT in pattern 2 | line<0 **and** falling fast (% of price) — pattern 1, ALSO reused in pattern 2 |
| Momentum gate | RSI rising 2 bars (pattern 1); RSI-not-rolling-over (pattern 2) | RSI falling 2 bars (pattern 1); RSI-not-recovering (pattern 2) |
| Trend-strength gate | ADX ≥20 + rising (pattern 2 only) | ADX ≥20 + rising (pattern 2 only) — same |
| Day-open filter | none | pattern 1 only; pattern 2 has none (mirrors CE) |
| Continuation default | **ON** since v27 | **OFF** — far less trade history than CE had |
| Re-arm cadence | once per VWAP-line leg (shared by both patterns) | once per below-VWAP leg (shared by both patterns) |
| Fresh-break gate | ✅ pattern 1 (`crossover`); pattern 2 has no freshness window (ADX substitutes) | ✅ pattern 1 (`crossunder`); pattern 2 has no freshness window (ADX substitutes) |

**Resolved in v21 (CE) / v11 (PUT):** the line-vs-band asymmetry (former open question #2) and the
missing-continuation-pattern asymmetry — PUT is now structurally a full mirror of CE (two patterns
each, same trend-strength mechanism). Remaining DELIBERATE asymmetries: PUT's pattern-1 MACD gate
checks the *line* (below zero + falling fast, % of price) where CE's checks the *histogram* (rising,
directional); PUT's pattern 2 additionally reuses the MACD gate as an extra confirmation CE's
pattern 2 doesn't have; PUT has a day-open filter on pattern 1, CE doesn't; PUT's continuation
defaults OFF where CE's defaults ON, purely because of the trade-history gap between the two sides.

---

## 7. Open questions for review

1. **Fresh-break window (`maxBarsAfterBreak=3`)** — is 3 bars right? Too tight risks missing a valid
   entry where the confirming filter (RSI/MACD) lands on bar 4–5; too loose reintroduces the stale-fire
   problem. Is there a cleaner formulation than a bar-count window (e.g. require the cross AND the
   filters on the *same* bar, or a volatility-scaled window)?
2. ~~CE vs PUT band asymmetry~~ — PARTIALLY RE-OPENED in v23: v21 dropped the upper-band entry after a
   1-session A/B showed it fires late/noisy; v23 reintroduces it as a SEPARATE, ADX-gated pattern
   (off by default). Once enabled and tested, does the ADX gate actually separate "continuation" from
   "exhaustion," or does it just shift the same problem to a different threshold?
3. **CE has no MACD-line / no day-open gate** — CE's new histogram-rising gate is a different
   momentum check than PUT's line-below-zero-and-falling-fast gate. Should CE get PUT's exact gate (or
   vice versa) for symmetry, or are they legitimately different because CE enters AT the cross (needs
   forward-looking momentum) while PUT's gate was built to reject flat chop specifically?
4. ~~`entryOpen` cadence asymmetry~~ — RESOLVED in v19: both now re-arm per leg ("tune with filters,
   don't restrict cadence"). Residual question: is per-leg re-entry on the SAME symbol a chop trap on
   range days, or do the fresh-break + filter gates keep it clean?
5. **Repaint / look-ahead correctness** — the manual VWAP, `request.security` prev-day close, and the
   `barsSinceBreak` state machine: any repaint or same-bar look-ahead hazard on `freq_once_per_bar_close`
   alerts?
6. ~~First-bars degenerate cross~~ — LARGELY MITIGATED: the window now opens 09:45, guaranteeing 30 bars
   of VWAP. A cross registered 09:43–09:44 can still seed a fresh window that fires at 09:45 with a
   slightly thin band — acceptable? (A `barsSinceDay >= N` guard would close it fully.)
7. **Continuation pattern (v23) has no freshness window** — pattern 1 requires the cross within
   `maxBarsAfterBreak` bars; pattern 2 has no analogous bound on HOW LONG price has been above
   `vwapUpper` — only that ADX is currently ≥20 and rising. Could this still admit a late entry if ADX
   happens to tick up again mid-move (a secondary acceleration) long after the original break? Worth
   checking against real continuation legs before enabling.
8. **`ta.dmi(adxLen, adxLen)` uses one input for both DI length and ADX smoothing** — this matches
   TradingView's standard ADX default (14/14), so it's conventional, not a bug — flagging only because
   it means `adxLen` controls two different things at once; a future tune of one shouldn't assume it's
   independent of the other.
9. **Double-fire edge case (v23)** — a bar that gaps through both the VWAP-line freshness window and
   above `vwapUpper` with all filters on both patterns true could emit BOTH `VWAP_BREAKOUT` and
   `TREND_CONTINUATION` alerts on the same bar for the same symbol. The bot's own dedup (one position
   per symbol) makes this harmless, but it would show as two alerts in `alerts.jsonl` for one trade —
   worth knowing when reading the log, not worth a code guard.

---

## 8. Files

- `pinescripts/Sniper_VWAP` — CE, v30
- `pinescripts/Sniper_VWAP_PUT` — PUT, v12
- (Sibling, not covered here: `pinescripts/Sniper_CE_DEEP` — a separate DEEP-burst CE strategy.)
