# PUT Options Bot - Critical Changes Required

## Overview
This document outlines the critical modifications needed to convert the CE (Call) bot to a functional PE (Put) bot. The bots use opposite Greeks logic and profit/loss mechanics.

## 1. Strike Selection ✅ DONE
**File**: `optcode/strike_selector.py`
**Status**: COMPLETED

### Changes Made:
- Updated `select_atm_strike()`: Selects LOWER strikes (below spot) instead of middle
- Selection index: 35% from sorted list (lower third for OTM puts)
- Updated all docstrings to explain PE logic
- Function logic reversed for PE OTM positioning

### Why This Matters:
- **CE**: BUY calls on upside breakout → Select HIGHER strikes (e.g., 2850 when spot 2750)
- **PE**: SELL puts on downside breakout → Select LOWER strikes (e.g., 2750 when spot 2850)
- Lower strike = cheaper premium, more leverage, more risk

---

## 2. Entry Signals ✅ DONE  
**File**: `optcode/entry_filter_engine.py`
**Status**: COMPLETED

### Changes Made:
- **Entry Action**: SELL (not BUY) for PE entries
- **Momentum Check**: RSI < 45 for downtrend (not RSI > 55 for uptrend)
- **Trend Check**: Short MA < Long MA for downtrend (not > for uptrend)
- **Blacklist**: Added 25 worst-performing symbols (0-30% win rate)
  - Always Losers: ETERNAL, JIOFIN, PIDILITIND, ANGELONE
  - Frequent Losers: IRFC, CONCOR, DELHIVERY, WAAREEENER, PFC, GAIL, HUDCO, ADANIGREEN, DIXON, BHARATFORG, TATATECH, COLPAL, MCX, VBL, INFY, HDFCLIFE, LT, SOLARINDS, EXIDEIND, CANBK

### Why This Matters:
- **CE Bot**: Enters on BUY signal → profit if price goes UP
- **PE Bot**: Enters on SELL signal → profit if price goes DOWN
- Entry signal direction is CRITICAL - wrong direction = guaranteed losses

---

## 3. Premium & Profit Logic ⚠️ CRITICAL - NEEDS REVIEW

**File**: `optcode/optmonitor.py`

### Issue:
CE and PE use OPPOSITE premium mechanics:

**CE (Call) Logic:**
```
Entry at ₹100
Price goes UP → Premium UP to ₹120 → PROFIT ₹20
Price goes DOWN → Premium DOWN to ₹80 → LOSS ₹20
HARD_SL at 0.80 * entry = ₹80 (exit when premium drops 20%)
TRIAL_SL trails UP from peak (exit at 5% below peak)
```

**PE (Put) Logic (NEEDS INVERSION):**
```
Entry at ₹100
Underlying goes DOWN → Premium UP to ₹120 → PROFIT ₹20 ✓ CORRECT
Underlying goes UP → Premium DOWN to ₹80 → LOSS ₹20 ✓ CORRECT  
HARD_SL at 1.20 * entry = ₹120 (exit when premium rises 20% - REVERSED!)
TRIAL_SL trails DIFFERENTLY: For puts, premium can increase on losses
```

### Critical Fix Needed:

**Line 1393 - HARD_SL Calculation:**
```python
# CURRENT (CE bot) - WRONG for PE
sl_premium_raw = position.entry_premium * 0.80  # -20%

# NEEDED for PE bot
# For PE, loss means premium INCREASES (underlying rises)
# So HARD_SL should be ABOVE entry premium
sl_premium_raw = position.entry_premium * 1.20  # +20% (REVERSED)
```

**Lines 349-350 - Highest Premium Tracking:**
```python
# CURRENT (CE) - tracks peak premium (profit)
if current_premium > self.highest_premium:
    self.highest_premium = current_premium

# NEEDED for PE - track LOWEST premium (best profit point)
# For PE, profit = entry - current, so lower is better
# But the logic assumes higher = better, so this needs rethinking
```

**Lines 1872-1877 - TRIAL_SL Calculation:**
```python
# CURRENT (CE) - peaks UP from entry
new_trial_sl = position.entry_premium * (1 + peak_gain_milestone / 100)

# NEEDED for PE - TRIAL_SL logic also needs inversion
# For PE: TRIAL_SL should be LOWER as premium increases (loss risk)
new_trial_sl = position.entry_premium * (1 - peak_gain_milestone / 100)
```

---

## 4. Unrealized P&L Calculation  
**File**: `optcode/optmonitor.py` - Lines 368-380

### Current (CE):
```python
premium_difference = self.current_premium - self.entry_premium
if self.action == "BUY":
    self.unrealized_pnl = premium_difference * self.quantity  # Profit if positive
```

### Needed for PE:
```python
premium_difference = self.current_premium - self.entry_premium
# For PE: loss means premium INCREASED (opposite of CE)
# So we need to INVERT: profit = -(current - entry) = entry - current
if self.action == "SELL":
    self.unrealized_pnl = -premium_difference * self.quantity  # INVERTED
```

---

## 5. Greeks Interpretation
**File**: `optcode/optmonitor.py` - Greeks tracking

### CE vs PE Delta Interpretation:
```
CE (Call):
- Delta +0.5 = moderate upside exposure
- Risk: Premium falls if underlying falls (delta goes negative)
- SL protects against unexpected downside

PE (Put):  
- Delta -0.5 = moderate downside exposure  
- Risk: Premium falls if underlying rises (delta becomes less negative)
- SL protects against unexpected upside (premium rises)
```

**Current issue**: The bot tracks delta normally, but SL logic is CE-based.

---

## 6. Position Monitoring & Exit Logic
**File**: `optcode/optmonitor.py` - Lines 1800-1950 (CHECK_TRIAL_SL)

### Current Flow (CE):
1. Position enters at premium ₹100
2. Premium rises to ₹120 → highest_premium = ₹120
3. TRIAL_SL activates when gain > 10%
4. TRIAL_SL trails DOWN: ₹120 * 0.95 = ₹114
5. If premium drops to ₹114 → EXIT

### Needed for PE:
```
Position enters at premium ₹100
Premium falls to ₹80 → PROFIT ₹20 (highest gain = 20%)
When premium = ₹120 (loss situation) → HIGHEST LOSS
TRIAL_SL logic is OPPOSITE
```

---

## Summary of Required Changes

| Component | CE Logic | PE Logic | File | Status |
|-----------|----------|----------|------|--------|
| Strike Selection | Middle/ATM | Lower OTM | strike_selector.py | ✅ DONE |
| Entry Signal | BUY on UP | SELL on DOWN | entry_filter_engine.py | ✅ DONE |
| Momentum Check | RSI > 55 | RSI < 45 | entry_filter_engine.py | ✅ DONE |
| Trend Check | MA up | MA down | entry_filter_engine.py | ✅ DONE |
| Worst Symbols | N/A | Blacklist 25 symbols | entry_filter_engine.py | ✅ DONE |
| HARD_SL Calc | * 0.80 | * 1.20 | optmonitor.py | ⚠️ NEEDS FIX |
| TRIAL_SL Logic | trails UP | trails DOWN | optmonitor.py | ⚠️ NEEDS FIX |
| Unrealized P&L | premium_diff | -premium_diff | optmonitor.py | ⚠️ NEEDS FIX |
| Highest Premium | track peaks | track valleys | optmonitor.py | ⚠️ NEEDS FIX |

---

## Implementation Order

### Phase 1: DONE ✅
1. Strike selection - COMPLETED
2. Entry signals - COMPLETED  
3. Blacklist - COMPLETED

### Phase 2: CRITICAL (Next)
1. **Fix HARD_SL calculation** (Line 1393)
   - Change from `* 0.80` to `* 1.20`
   - Update all SL calculations that reference this
   
2. **Fix TRIAL_SL logic** (Lines 1872-1877)
   - Reverse the peak gain milestone logic
   - Trail DOWN instead of UP

3. **Fix P&L calculations** (Lines 368-380)
   - Invert unrealized_pnl for SELL action
   
4. **Test with sandbox capital** (10% of normal)
   - Monitor first few trades
   - Check exit signals
   - Verify Greeks tracking

---

## Testing Checklist Before Live Trading

- [ ] Strike selection returns PE strikes (lower than underlying)
- [ ] Entry filter rejects CE entries (BUY action)  
- [ ] Entry filter accepts PE entries (SELL action)
- [ ] Blacklist is active (check logs)
- [ ] HARD_SL calculated at HIGHER premium (+20%)
- [ ] TRIAL_SL logic reversed (trails down for losses)
- [ ] P&L calculation shows profit when premium DECREASES
- [ ] Bot exits on upside (premium increase = loss)
- [ ] Greeks tracking matches PE interpretation
- [ ] First 5 trades analyzed for correct direction

---

## Risk Warning

**DO NOT START LIVE TRADING UNTIL ALL PHASES COMPLETE**

- Wrong SL logic = Losses on winning trades
- Wrong P&L = Cannot track positions
- Wrong entry signals = Trading against the trend

Start with 10% capital allocation for first week of testing.
