# Entry → TRIAL_SL → Exit Flow Chart

## PHASE 1: ENTRY DECISION FLOW

```
🔔 WEBHOOK RECEIVED
    ↓
┌─────────────────────────────────┐
│ BASIC VALIDATION                │
├─────────────────────────────────┤
│ ✓ Time 9:15-15:30 (Market Hrs)  │
│ ✓ Symbol in Universe (260 F&O)  │
│ ✓ Option Available (Call/Put)   │
│ ✓ DTE >= 2 days                 │
│ ✓ OI > 1000                     │
└─────────────────────────────────┘
    ↓
    │
    ├─ ANY FAIL? ──→ REJECT (Don't enter)
    │
    ↓ ALL PASS
    
┌─────────────────────────────────────────────────────────────┐
│ ENTRY FILTER ENGINE (Comprehensive)                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ 1️⃣  PCR (Put-Call Ratio) - Market Sentiment                │
│     ├─ PCR > 1.2 → BEARISH (more puts, expect down)        │
│     ├─ PCR < 0.8 → BULLISH (more calls, expect up)         │
│     └─ 0.8 - 1.2 → NEUTRAL (balanced)                      │
│                                                              │
│ 2️⃣  OI BUILDUP - Trend Confirmation                        │
│     ├─ OI Rising + Price Rising → Strong Uptrend ✅        │
│     ├─ OI Rising + Price Falling → Strong Downtrend ✅     │
│     └─ OI Falling → Trend Weakening ❌                     │
│                                                              │
│ 3️⃣  TECHNICAL (RSI 15-min)                                 │
│     ├─ RSI > 70 → Overbought (risky entry)                 │
│     ├─ RSI < 30 → Oversold (good entry)                    │
│     └─ 30-70 → Safe zone                                   │
│                                                              │
│ 4️⃣  MACD (Momentum Indicator)                              │
│     ├─ MACD > Signal Line → Bullish momentum ✅            │
│     ├─ MACD < Signal Line → Bearish momentum ❌            │
│     └─ Crossover → Entry signal                            │
│                                                              │
│ 5️⃣  MOVING AVERAGES (MA10 & MA20)                          │
│     ├─ Current Price > MA10 > MA20 → Strong Uptrend ✅     │
│     ├─ Current Price < MA10 < MA20 → Strong Downtrend ✅   │
│     ├─ Price Crossing MA → Entry Signal                    │
│     └─ Slope > 0 → Momentum up                             │
│                                                              │
│ 6️⃣  IV PERCENTILE (Volatility)                             │
│     ├─ IV High (>70%) → Expensive (risky)                  │
│     ├─ IV Low (<30%) → Cheap (good value)                  │
│     └─ IV 30-70% → Safe zone                               │
│                                                              │
│ 7️⃣  GREEKS (Option Greeks Quality)                         │
│     ├─ Delta: 0.3-0.8 (ATM preferred)                      │
│     ├─ Gamma: Not too high (avoid pinch)                   │
│     ├─ Theta: Time decay okay                              │
│     └─ Vega: IV movement risk controlled                   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
    ↓
    │
    ├─ ALL FILTERS PASS? ──→ ✅ ENTRY
    │                           └─ Place order at market
    │
    └─ ANY FAIL? ──→ ❌ SKIP (Wait for next signal)
```

---

## PHASE 2: POST-ENTRY - TRIAL_SL LIFECYCLE

### After Entry (Position Created)

```
Position Opened at ₹X (entry_premium)
    ↓
┌────────────────────────────────────┐
│ INITIAL STATE                      │
├────────────────────────────────────┤
│ • entry_premium = ₹X               │
│ • highest_premium = ₹X (set to current)
│ • current_premium = ₹X             │
│ • trial_sl_enabled = FALSE         │
│ • hard_sl = ₹X * 0.9 (-10%)        │
│ • trial_sl = NULL (not activated)  │
└────────────────────────────────────┘
    ↓
    └─ EVERY 3 SECONDS (Monitoring Loop)
        ↓
```

### TRIAL_SL Activation Phase (PHASE 1)

```
Every 3 seconds:
    ↓
    ├─ Check: peak_gain_percent >= 10%?
    │   (is highest_premium reached 10% above entry_premium?)
    │
    ├─ NO ──→ Wait (position still gaining, not at 10% yet)
    │          └─ Continue monitoring...
    │
    └─ YES ──→ 🚀 ACTIVATE TRIAL_SL
                 └─ Set trial_sl_enabled = TRUE
                 └─ Set trial_sl_price = highest_premium * 0.95
                    └─ (5% buffer below peak)
                 └─ Log activation
                 └─ Save position state
                 └─ Continue to Phase 2...
```

### TRIAL_SL Update Phase (PHASE 2)

```
After TRIAL_SL Activated (trial_sl_enabled = TRUE):
    ↓
    ├─ Check: current_premium > highest_premium?
    │   (Did price go to new high?)
    │
    ├─ NO ──→ TRIAL_SL stays same
    │          └─ Continue monitoring...
    │
    └─ YES ──→ 🔺 UPDATE TRIAL_SL
                 ├─ highest_premium = current_premium
                 ├─ new_trial_sl = highest_premium * 0.95
                 ├─ Is new_trial_sl > old_trial_sl?
                 │   └─ YES: Update SL (only move up, never down)
                 │   └─ NO: Keep old SL
                 ├─ Log update
                 ├─ Save position state
                 └─ Continue monitoring...
                 
    EXAMPLE:
    ────────────────────────────────────
    • Entry: ₹100
    • Peak reaches ₹110 (+10%)
      └─ Activate TRIAL_SL at ₹104.5 (95% of 110)
    • Peak reaches ₹115 (+15%)
      └─ Update TRIAL_SL to ₹109.25 (95% of 115)
    • Peak reaches ₹120 (+20%)
      └─ Update TRIAL_SL to ₹114 (95% of 120)
    • Price drops to ₹114
      └─ TRIAL_SL HIT! Exit position
      └─ P&L = ₹114 - ₹100 = +₹14 (~14% profit)
```

### TRIAL_SL Hit Detection Phase (PHASE 3)

```
Every 3 seconds (continuous check):
    ↓
    ├─ Determine EFFECTIVE_SL:
    │   ├─ If trial_sl_enabled = TRUE
    │   │  └─ effective_sl = trial_sl_price
    │   └─ If trial_sl_enabled = FALSE
    │      └─ effective_sl = hard_sl_price (₹entry * 0.8)
    │
    ├─ Check: current_premium <= effective_sl?
    │   (Did price breach the SL?)
    │
    ├─ NO ──→ Position still safe
    │          └─ Continue monitoring...
    │
    └─ YES ──→ 🎯 SL HIT!
                 ├─ Determine exit type:
                 │  ├─ If trial_sl_enabled = TRUE
                 │  │  └─ "TRIAL_SL_HIT"
                 │  └─ If trial_sl_enabled = FALSE
                 │     └─ "HARD_SL_HIT"
                 ├─ Close position at current_premium
                 ├─ Calculate P&L = exit_price - entry_price
                 ├─ Log exit (reason, price, P&L)
                 ├─ Record in option_pnl_history.json
                 └─ POSITION CLOSED ✅
```

---

## PHASE 3: EXIT DECISION FLOW (Full Decision Tree)

### Entry Point: Every 3-Second Monitoring Cycle

```
MONITORING CYCLE STARTS
    ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 1: REFRESH MARKET DATA                                  │
├──────────────────────────────────────────────────────────────┤
│ • Fetch LTP (current premium) for all positions              │
│ • Fetch underlying candle data (for trend check)             │
│ • Track time in position for each trade                      │
└──────────────────────────────────────────────────────────────┘
    ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 2: CHECK EXITS IN PRIORITY ORDER                        │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│ ⭐ PRIORITY 0: BASIC CHECKS                                  │
│    ↓                                                          │
│    1. EXPIRY CLOSE                                           │
│       Condition: Contract expiring in < 30 min               │
│       Action: FORCE CLOSE (no negotiation)                   │
│       Frequency: Once per day (last 30 min of market)        │
│                                                               │
│    2. PROFIT TARGETS                                         │
│       Condition: Unrealized P&L >= +20%                      │
│       Action: Take profits (exit position)                   │
│       Frequency: Throughout day                              │
│                                                               │
│    3. TRAILING_STOP_LOSS (TRIAL_SL) ⭐                      │
│       Condition: price <= trial_sl_price                     │
│       Action: Exit position (lock gains)                     │
│       Win Rate: 99.4% ✅ (175 trades, ₹662,776 profit)      │
│       Frequency: Continuous                                  │
│                                                               │
└──────────────────────────────────────────────────────────────┘
    ↓
│    ⭐ PRIORITY 1: EARLY REVERSAL DETECTION                   │
│    ↓                                                          │
│    4. GREEKS_DELTA_REVERSAL                                  │
│       Condition: Delta changed > 0.15 (reversal sign)        │
│       Logic:                                                 │
│         IF entry_delta = 0.50 (ATM)                          │
│         AND current_delta = 0.25 (declining)                 │
│         AND delta_change > 0.15                              │
│         THEN exit (price moving against us)                  │
│                                                               │
│       Goal: Catch reversals BEFORE 10% loss occurs           │
│       When: After price has moved 5-10%                      │
│                                                               │
└──────────────────────────────────────────────────────────────┘
    ↓
│    ⭐ PRIORITY 2: MOMENTUM-BASED EXIT (PROBLEMATIC)          │
│    ↓                                                          │
│    5. MOMENTUM_REVERSAL ⚠️                                    │
│       Condition:                                             │
│         IF time_in_position > 10 seconds:                    │
│         AND drawdown from peak > 10%:                        │
│         AND position already losing > -1%:                   │
│         THEN exit (save from worse loss)                     │
│                                                               │
│       Problem Analysis:                                      │
│       • Trades: 293 total                                    │
│       • Win Rate: 5.1% (only 15 winners) 🔴                 │
│       • Loss Rate: 95% (278 losers)                          │
│       • Total Loss: -₹566,168                                │
│       • Avg Loss: -₹1,932 per trade                          │
│                                                               │
│       Why it Fails:                                          │
│       ├─ Exits positions that would recover w/ TRIAL_SL     │
│       ├─ 10% drawdown threshold too aggressive              │
│       └─ Catches winners, not just losers                   │
│                                                               │
└──────────────────────────────────────────────────────────────┘
    ↓
│    ⭐ PRIORITY 2.3: STALE CONSOLIDATION (NEW)                │
│    ↓                                                          │
│    6. STALE_CONSOLIDATION_EXIT ✨ SIMPLIFIED                │
│       Condition:                                             │
│         IF time_held >= 15 minutes:                          │
│         AND trial_sl_enabled = FALSE (peak never hit 10%):  │
│         AND current_pnl >= -1% (near breakeven or profit):   │
│         THEN exit (lock gains before momentum reversal)      │
│                                                               │
│       Why This Works:                                        │
│       • TRIAL_SL activates at peak >= 10% (golden trades)   │
│       • If peak < 10% after 15 min → trend died, exit       │
│       • Lock +6%, +7%, +8% instead of risk -5% loss         │
│                                                               │
│       Pattern Prevented:                                     │
│       Entry → +6.9% peak (consolidates, trend dies)         │
│       Without exit: → drops 12.4% → -5.45% loss ❌          │
│       With exit: → lock +6.9% ✅ (skip the reversal)        │
│                                                               │
└──────────────────────────────────────────────────────────────┘
    ↓
│    ⭐ PRIORITY 2.5: TIME-BASED STALENESS (NEW)               │
│    ↓                                                          │
│    7. STALE_TIMEOUT_EXIT                                     │
│       Condition:                                             │
│         IF time_held >= 20 minutes:                          │
│         AND (                                                 │
│           (abs_price_change < 0.5% AND position_pnl <= 0)  │
│           OR position_pnl < -2%                              │
│         ):                                                    │
│         THEN exit (free capital for fresh trades)            │
│                                                               │
│       Goal: Exit non-trending positions                      │
│            before they waste more capital                    │
│                                                               │
│       Use Case:                                              │
│       ├─ Position entered, no momentum appears               │
│       ├─ Stuck between ±0.5% for 20 minutes                │
│       ├─ Expected to hit -10% MOMENTUM soon                 │
│       └─ Exit early to free capital                         │
│                                                               │
└──────────────────────────────────────────────────────────────┘
    ↓
│    ⭐ PRIORITY 3: HARD PROTECTION                            │
│    ↓                                                          │
│    8. STOP_LOSS (HARD SL)                                    │
│       Condition: price <= entry_premium * 0.90 (-10% loss)  │
│       Action: Emergency exit (catastrophic loss prevention) │
│       Frequency: Rarely (shouldn't reach here)              │
│                                                               │
│    9. SENTIMENT_EXIT ⚠️                                       │
│       Condition: Sector/market sentiment flipped             │
│       Problem: Costing -₹23,000 (negative ROI)             │
│       Recommendation: Disable this check                     │
│                                                               │
└──────────────────────────────────────────────────────────────┘
    ↓
│    ⭐ PRIORITY 4: ADVANCED GREEKS-BASED                      │
│    ↓                                                          │
│    10. GREEKS_GAMMA_EXPLOSION                                │
│        Condition: Gamma > risk threshold                     │
│        Action: Exit (gamma runaway = risk explosion)         │
│                                                               │
│    11. GREEKS_THETA_ACCELERATION                             │
│        Condition: Theta decay accelerating (time against us) │
│        Action: Exit (time eating our profits)                │
│                                                               │
│    12. GREEKS_VEGA_CRUSH                                     │
│        Condition: IV collapsed (premium decayed)             │
│        Action: Exit (vega reversal)                          │
│                                                               │
│    13. GREEKS_HEALTH_SCORE                                   │
│        Condition: Overall Greeks health degraded             │
│        Action: Exit (Greeks warning)                         │
│                                                               │
│    14. IV_CRASH                                              │
│        Condition: IV dropped > 5% suddenly                   │
│        Action: Exit (premium decay too fast)                 │
│                                                               │
│    15. IV_SPIKE                                              │
│        Condition: IV spiked > 10% (panic/crash signal)       │
│        Action: Exit (crisis signal)                          │
│                                                               │
└──────────────────────────────────────────────────────────────┘
    ↓
│    ⭐ PRIORITY 5: ML-GUIDED EXIT                             │
│    ↓                                                          │
│    16. ML_QUALITY_EXIT                                       │
│        Condition: ML model predicts exit                     │
│        Action: Exit based on learned patterns                │
│        Status: Experimental                                  │
│                                                               │
└──────────────────────────────────────────────────────────────┘

```

---

## Exit Decision Summary Table

| Priority | Exit Type | Condition | Win Rate | Status |
|---|---|---|---|---|
| **0** | **TRIAL_SL_HIT** | price ≤ trial_sl_price | **99.4%** ✅ | **PERFECT** |
| **0** | **HARD_SL_HIT** | price ≤ entry*0.90 | N/A | Emergency |
| **0** | **EXPIRY_CLOSE** | DTE < 0.5 days | N/A | Forced |
| **0** | **PROFIT_TARGET** | P&L ≥ +20% | N/A | Auto-lock |
| **1** | **GREEKS_DELTA** | Delta reversal > 0.15 | ? | Experimental |
| **2** | **MOMENTUM_REVERSAL** | drawdown > 10% + losing > -1% | **5.1%** 🔴 | **PROBLEMATIC** |
| **2.3** | **STALE_CONSOLIDATION** | 15min + trial_sl=FALSE (peak<10%) | ? | New, Smart |
| **2.5** | **STALE_TIMEOUT** | 20min + no trend | ? | New, Good |
| **3** | **SENTIMENT** | Sentiment flipped | ~37% | Costing ₹23k 🔴 |
| **4** | **Greeks (Gamma/Theta/Vega)** | Greeks degradation | ? | Advanced |
| **4** | **IV_CRASH** | IV dropped > 5% | ? | Advanced |
| **4** | **IV_SPIKE** | IV spiked > 10% | ? | Advanced |
| **5** | **ML_QUALITY** | ML prediction | ? | Experimental |

---

## Position Lifecycle Example (Complete)

```
TIME    EVENT                                  PREMIUM  PEAK   GAIN%   ACTION
──────────────────────────────────────────────────────────────────────────────

9:30    ENTRY SIGNAL (webhook received)
        Entry filters all pass ✅
        BUY 100 units                          ₹100     ₹100   0%      🟢 ENTRY
        ├─ trial_sl_enabled = FALSE
        ├─ hard_sl_price = ₹90 (-10%)
        └─ Monitoring starts every 3 sec

9:30:30 MONITORING CYCLE 1
        Current: ₹100                          ₹100     ₹100   0%      Continue
        Check: Is peak_gain >= 10%? NO         ✓        ✓              Monitor

9:31    MONITORING CYCLE 10
        Current: ₹105                          ₹105     ₹105   +5%      Continue
        Check: Is peak_gain >= 10%? NO         ✓        ✓              Monitor

9:32    MONITORING CYCLE 20
        Current: ₹112                          ₹112     ₹112   +12%     🔺 ACT
        Check: Is peak_gain >= 10%? YES
        ├─ ACTIVATE TRIAL_SL ✅
        ├─ trial_sl_enabled = TRUE
        ├─ trial_sl_price = 112 * 0.95 = ₹106.4
        └─ Next: Monitor for SL hit or new peak

9:35    MONITORING CYCLE 35
        Current: ₹118                          ₹118     ₹118   +18%     🔺 UPDATE
        Check: new_peak > old_peak? YES
        ├─ highest_premium = ₹118
        ├─ UPDATE TRIAL_SL ✅
        ├─ trial_sl_price = 118 * 0.95 = ₹112.1 (was ₹106.4)
        └─ TRIAL_SL moved up by ₹5.7

9:40    MONITORING CYCLE 50
        Current: ₹120                          ₹120     ₹120   +20%     🔺 UPDATE
        Check: new_peak > old_peak? YES
        ├─ highest_premium = ₹120
        ├─ UPDATE TRIAL_SL ✅
        ├─ trial_sl_price = 120 * 0.95 = ₹114 (was ₹112.1)
        └─ TRIAL_SL moved up by ₹1.9

9:45    MONITORING CYCLE 65
        Current: ₹115                          ₹115     ₹120   +15%     Continue
        Check: price <= trial_sl (₹114)? NO
        └─ Still above SL, continue monitoring

9:47    MONITORING CYCLE 75
        Current: ₹114.2                        ₹114.2   ₹120   +14.2%   Continue
        Check: price <= trial_sl (₹114)? NO    
        └─ Still above SL (barely), continue

9:49    MONITORING CYCLE 85
        Current: ₹113.8                        ₹113.8   ₹120   +13.8%   🎯 EXIT
        Check: price <= trial_sl (₹114)? YES
        ├─ SL HIT! ✅
        ├─ Exit reason: TRIAL_SL_HIT
        ├─ Entry: ₹100
        ├─ Exit: ₹113.8
        ├─ P&L: +₹13.8 (+13.8%)
        ├─ Duration: 19 minutes
        ├─ Record in option_pnl_history.json
        └─ POSITION CLOSED ✅

RESULT: ✅ Successful trade
├─ Entered at ₹100
├─ Exited at ₹113.8
├─ Profit: +₹13.8 (13.8%)
├─ Duration: 19 min
└─ Exit Reason: TRIAL_SL_HIT
```

---

## Decision Quality Assessment

### ✅ WINNERS (Keep Running)
- **TRIAL_SL_HIT**: 99.4% win rate, ₹3,787 avg profit
- **STALE_CONSOLIDATION**: New, locks profitable stale positions
- **STALE_TIMEOUT**: New, frees capital from non-trending trades
- **GREEKS_DELTA**: Catches reversals early (needs tuning)

### 🔴 LOSERS (Needs Fix or Disable)
- **MOMENTUM_REVERSAL**: 5.1% win rate, -₹1,932 avg loss
  - Solution A: Disable entirely (let TRIAL_SL handle)
  - Solution B: Make time-based (only exit after 15+ min)
  - Solution C: Raise threshold from 10% to 15% drawdown

- **SENTIMENT_EXIT**: Costing -₹23k opportunity
  - Solution: Disable this check entirely

---

## Key Insights

1. **TRIAL_SL is your best exit** - 99.4% win rate, keep it running
2. **MOMENTUM_REVERSAL is killing profits** - Fix or disable it
3. **Entry filters are comprehensive** - PCR, RSI, MACD, MA, IV all checked
4. **Time-based exits help** - STALE_CONSOLIDATION and TIMEOUT defensive mechanism
5. **Priority ordering matters** - Greeks first (catches early), momentum second, SL last

