# Options Bot - Monitoring Algorithm

## Overview
The monitoring system runs every 3 seconds, checking all open positions and deciding which ones to exit based on multiple criteria with strict priority ordering.

---

## Main Monitoring Loop Flow

```
Every 3 seconds:
  1. Refresh LTP (current prices) for all positions
  2. Refresh underlying candle data (for fake move detection)
  3. Run exit checks in PRIORITY ORDER
  4. Track which positions were closed to avoid duplicate exits
  5. Log results and update live tracking
```

---

## Exit Mechanisms (In Priority Order)

### TIER 0: Basic Checks (Always run first)
1. **EXPIRY_CLOSE** - Position expired, must close
2. **PROFIT_TARGETS** - Hit predefined profit target → auto close
3. **TRAILING_STOP_LOSS (TRIAL_SL)** - Trailing SL triggered → exit
   - Performance: 175 trades, **99.4% win rate**, +₹662,776 total
   - Status: ✅ **PERFECT** - This is your best exit

---

### TIER 1: Early Reversal Detection (PRIORITY 1)
**4. GREEKS_DELTA_REVERSAL** - Delta movement indicates reversal starting
   - Catches reversals BEFORE 10% momentum loss occurs
   - Works on: Delta changes, not premium absolute price
   - Goal: Exit early before MOMENTUM_REVERSAL can hit

---

### TIER 2: Drawdown-Based Exit (PRIORITY 2)
**5. MOMENTUM_REVERSAL** - 10%+ drawdown from peak premium
   - Current Status: 293 trades, **5.1% win rate**, -₹566,168 total 🔴 **DISASTER**
   - Logic:
     ```
     IF time_in_position > 10 seconds:
       AND drawdown from peak > 10%:
       AND position already losing > -1%:
         THEN exit (save from -20% SL)
     ```
   - Problem: 95% of exits are losers
   - Only exits losing positions (smart filter), but exits too many

---

### TIER 2.3: Profitable Stale Consolidation (PRIORITY 2.3)
**6. STALE_CONSOLIDATION_EXIT** - Exit stale profitable positions
   - Condition:
     ```
     IF time_held >= 15 minutes:
       AND peak_profit >= +5%:
       AND current_pnl >= 0%:
         THEN exit (lock gains before reversal)
     ```
   - Status: NEW feature, not yet in trade data (< 1 execution)

---

### TIER 2.5: Non-Trending Position Exit (PRIORITY 2.5)
**7. STALE_TIMEOUT_EXIT** - Exit positions with no momentum
   - Condition:
     ```
     IF time_held >= 20 minutes:
       AND (
         (abs_price_change < 0.5% AND position_pnl <= 0)
         OR position_pnl < -2%
       ):
         THEN exit (free capital from stalled trades)
     ```
   - Status: NEW feature, 1 execution, -₹1,962 loss

---

### TIER 3: Hard Protection (PRIORITY 3)
8. **STOP_LOSS** - Hard SL hit (loss > -20% or -₹X)
   - Emergency exit when position gets too bad
   - Should rarely trigger if other exits work

9. **SENTIMENT_EXIT** - Sector/market sentiment has flipped
   - Exits on negative sentiment signals
   - Performance: Costing -₹23k (negative impact)
   - Status: ⚠️ Consider disabling

---

### TIER 4: Greeks-Based Advanced Exits (PRIORITY 4+)
10. **GREEKS_GAMMA_EXPLOSION** - Gamma runaway (risk explodes)
11. **GREEKS_THETA_ACCELERATION** - Theta decay accelerating (time works against us)
12. **GREEKS_VEGA_CRUSH** - IV crash (premium decay)
13. **GREEKS_HEALTH_SCORE** - Overall Greeks health degradation
14. **IV_CRASH** - IV dropped > threshold (premium decays fast)
15. **IV_SPIKE** - IV spiked > threshold (panic/crash signal)

---

### TIER 5: ML-Guided Exit (PRIORITY 5)
16. **ML_QUALITY_EXIT** - ML model suggests exit based on learned patterns
    - Uses historical trade data to predict good exits
    - Status: NEW experimental feature

---

## Duplicate Exit Prevention

Each exit runs, but results are filtered to prevent double-closing:

```python
# Example: After MOMENTUM_REVERSAL runs, filter to exclude positions
# that were already closed by Greeks_Delta or TRIAL_SL
momentum_closes = [p for p in momentum_closes 
                   if p['symbol'] not in greeks_closed_symbols 
                   and p['symbol'] not in trailing_closed_symbols]
```

---

## Key Performance Data (Last ~10 Days)

| Exit Mechanism | Trades | Win Rate | Total P&L | Avg per Trade |
|---|---|---|---|---|
| **TRIAL_SL_HIT** | 175 | 99.4% ✅ | +₹662,776 | +₹3,787 |
| **MOMENTUM_REVERSAL** | 293 | 5.1% 🔴 | -₹566,168 | -₹1,932 |
| **EOD_SQUAREOFF** | 2 | 100% ✅ | +₹7,280 | +₹3,640 |
| **STALE_TIMEOUT** | 1 | 0% | -₹1,962 | -₹1,962 |
| **SENTIMENT_EXIT** | - | - | -₹23,000 | - |
| **GREEKS_DELTA** | - | - | - | - |

---

## Current Problems Identified

### 🔴 CRITICAL: MOMENTUM_REVERSAL
- Loses ₹1,932 per trade on average
- 95% of exits are losers (only 5% are winners)
- Total loss: -₹566,168 across 293 trades
- **Root cause**: Exiting positions that would recover with TRIAL_SL

### 🟡 MEDIUM: SENTIMENT_EXIT
- Costing -₹23,000 in opportunity (sector filtering too strict)
- Win rate: 36.8% with sector checks vs 40.3% without
- **Root cause**: Sector checks don't improve profitability

### 🟢 EXCELLENT: TRIAL_SL_HIT
- 99.4% win rate, all profitable
- Average win: ₹3,787 per trade
- **Keep as-is, this is your best exit**

---

## Recommendations for Review

1. **MOMENTUM_REVERSAL** - Needs redesign:
   - Option A: Disable entirely (let TRIAL_SL handle it)
   - Option B: Make time-based: Only exit if `time_held > 15 minutes` (not drawdown-based)
   - Option C: Raise threshold from 10% to 15% drawdown (be less aggressive)

2. **STALE_CONSOLIDATION/TIMEOUT** - New exits are good:
   - Currently active but not yet showing in trades
   - Will help lock profits and free capital
   - Keep running

3. **SENTIMENT_EXIT** - Consider disabling:
   - Data shows negative impact (-₹23k)
   - Greeks filters already catching reversals better
   - Disable to improve win rate from 40% → 41%+

4. **GREEKS_DELTA_REVERSAL** - Enhancement needed:
   - Should run BEFORE momentum to catch early reversals
   - Currently working but need tuning

---

## Algorithm Summary

**Your monitoring logic is:**
1. ✅ Well-structured with clear priority ordering
2. ✅ Designed to catch reversals early (Greeks first, then momentum)
3. ⚠️ But has 1 major profit-killer: MOMENTUM_REVERSAL (losing -₹566k)
4. ⚠️ And 1 minor drag: SENTIMENT_EXIT (costing -₹23k)
5. ✅ New time-based exits (stale checks) are good defensive additions
6. ✅ TRIAL_SL is working perfectly (keep it)
