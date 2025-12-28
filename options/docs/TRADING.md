# Options Trading Bot - Complete Trading Guide

## Trading Process Overview

This guide details the complete lifecycle of an options trade: how entries are triggered, how positions are monitored in real-time, how LTP and Greeks drive decision-making, and how exits are executed.

---

## Part 1: ENTRY PROCESS

### 1.1 Entry Signal Reception

**Source**: TradingView Pine Script → Webhook Signal

```json
{
  "symbol": "BANKNIFTY",
  "action": "BUY",
  "side": "CE",
  "strike": "1000",
  "expiry": "2024-12-31",
  "premium": "45.50",
  "time": "09:15:23"
}
```

**Route**: `POST /webhook/options` → `webhook_router.py`

### 1.2 Pre-Entry Validation

**File**: `optmonitor.py::add_position()`

```python
def add_position(self, signal):
    # VALIDATION 1: Capital Available?
    available_capital = MAX_CAPITAL - current_capital_used
    if available_capital < CAP_PER_TRADE:
        REJECT: "Insufficient capital"
        return
    
    # VALIDATION 2: Slots Available?
    if len(self.positions) >= MAX_SLOTS:
        REJECT: "Max concurrent positions reached"
        return
    
    # VALIDATION 3: Daily Trade Limit?
    if today_trade_count >= MAX_TRADES_PER_DAY:
        REJECT: "Max trades per day exceeded"
        return
    
    # VALIDATION 4: Instrument Valid?
    if symbol not in instrument_file:
        REJECT: "Instrument not found in NFO"
        return
    
    # VALIDATION 5: Strike Exists?
    option_chain = broker.fetch_option_chain(underlying, expiry)
    if strike not in option_chain:
        REJECT: "Strike not available"
        return
    
    # ✓ ALL VALIDATIONS PASSED - PROCEED WITH ENTRY
```

### 1.3 Fetch Pre-Entry Data

**Fetch Greeks, IV, and Sentiment**

```python
# STEP 1: Get current LTP
current_ltp = broker.get_market_data(symbol)
# Result: BANKNIFTY24DEC1000CE = ₹45.75

# STEP 2: Fetch Option Chain
option_chain = broker.fetch_option_chain("BANKNIFTY", "2024-12-31")
# Result: List of all strikes for this underlying + expiry

# STEP 3: Extract Greeks for our strike
strike_row = option_chain[strike_price][side]  # "CE" or "PE"
entry_greeks = {
    'delta':  strike_row['delta'],      # e.g., 0.65 (bullish)
    'gamma':  strike_row['gamma'],      # e.g., 0.02 (low)
    'theta':  strike_row['theta'],      # e.g., -0.05 (daily decay)
    'vega':   strike_row['vega']        # e.g., 0.10 (IV sensitive)
}
# Entry Greeks → stored for comparison later

# STEP 4: Extract IV (Implied Volatility)
entry_iv = strike_row['iv']  # e.g., 22.5% (average market volatility)

# STEP 5: Fetch Sentiment Data
entry_pcr = broker.get_put_call_ratio("BANKNIFTY")  # e.g., 1.25 (bearish)
entry_oi_buildup = calculate_oi_change_pct("BANKNIFTY")  # e.g., 8.5%

# STEP 6: Determine Quantity
# Capital per trade ÷ premium per contract
quantity = CAP_PER_TRADE / current_ltp
# e.g., 30,000 ÷ 45.75 = 655 contracts (approximately)
```

**Data Summary at Entry**
```
Symbol:          BANKNIFTY24DEC1000CE
Entry Premium:   ₹45.75
Entry Delta:     0.65  (bullish, 65% probability)
Entry Gamma:     0.02  (low gamma = stable delta)
Entry Theta:     -0.05 (decay: ₹0.05/day)
Entry Vega:      0.10  (IV changes impact ₹0.10 per contract)
Entry IV:        22.5% (relatively calm market)
Entry PCR:       1.25  (put-heavy, indicates caution)
Entry OI %:      8.5%  (moderate buildup)
Quantity:        655 contracts
Capital Used:    ₹30,000
Max Hold:        5 minutes
Profit Target:   ₹2,000
Stop Loss:       ₹500
```

### 1.4 Execute Entry Order

```python
order = {
    'exchange': 'NFO',                      # National Futures & Options
    'symbol': 'BANKNIFTY24DEC1000CE',      # Full contract name
    'side': 'BUY',                          # Entry action
    'order_type': 'MARKET',                 # Immediate execution
    'quantity': 655,                        # As calculated
    'product': 'INTRADAY',                  # Day trading (auto-close EOD)
    'duration': 'DAY'                       # Valid for 1 day only
}

response = broker.place_order(order)
# Result: Order ID 12345, filled at ₹45.75, 655 qty
```

**Entry Complete** ✓
- Position added to `self.positions[symbol]`
- Entry state stored (premium, greeks, time, sentiment)
- Greeks history initialized
- Position monitoring begins

---

## Part 2: MONITORING PROCESS (5-Second Loop)

### 2.1 Refresh LTP (Step 1 - Every 5 seconds)

**File**: `optmonitor.py::refresh_position_ltps()`

**Purpose**: Get current market price for all open positions

```
Timeline:
09:15:30 → ENTRY at ₹45.75
09:15:35 → Check LTP (Cycle 1)
09:15:40 → Check LTP (Cycle 2)
09:15:45 → Check LTP (Cycle 3)
...
```

**How LTP Fetching Works** (Rate-Limited)

```python
# ALL POSITIONS: 30 active symbols
all_symbols = symbol_pool.get_active_symbols()
# ['BANKNIFTY24DEC1000CE', 'BANKNIFTY24DEC1100CE', ...]

# BUCKET MANAGEMENT: Divide into 6 buckets of 5 each
# Cycle 1: Fetch bucket 1 (5 symbols) → 5 API calls
# Cycle 2: Fetch bucket 2 (5 symbols) → 5 API calls
# ...
# Cycle 6: Fetch bucket 6 (5 symbols) → 5 API calls
# Cycle 7: Back to bucket 1

current_bucket = bucket_manager.get_current_bucket()
# Returns: ['BANKNIFTY24DEC1000CE', 'BANKNIFTY24DEC1050CE', ...]

# BULK FETCH: Get LTP for all symbols in current bucket
ltps = broker.get_ltp_bulk(current_bucket, exchange="NFO")
# Result: {
#   'BANKNIFTY24DEC1000CE': 47.25,    # Up from 45.75
#   'BANKNIFTY24DEC1050CE': 23.50,
#   ...
# }

# UPDATE POSITIONS: Store fetched LTPs
for symbol, ltp in ltps.items():
    if symbol in self.positions:
        position = self.positions[symbol]
        position.current_premium = ltp
```

**Rate Limiting Benefit**
- 30 positions in 6 cycles = 5 API calls/cycle maximum
- Prevents rate limit violations
- Keeps monitoring real-time despite API constraints

### 2.2 Fetch Option Chain (Step 2 - Less Frequent)

**File**: `optmonitor.py::refresh_position_ltps()` → `fetch_option_chain()`

**Purpose**: Get Greeks (Delta, Gamma, Theta, Vega) and IV

```python
# FETCH OPTION CHAIN (every position refresh, cached for 30 seconds)
option_chain = broker.fetch_option_chain(
    underlying="BANKNIFTY",
    expiry="2024-12-31"
)

# FIND MATCHING STRIKE
strike_row = option_chain[strike_price][side]  # "CE" or "PE"
# Result: {
#   'strike': 1000,
#   'side': 'CE',
#   'ltp': 47.25,
#   'bid': 47.00,
#   'ask': 47.50,
#   'delta': 0.68,      # UPDATED (was 0.65)
#   'gamma': 0.021,     # UPDATED (was 0.02)
#   'theta': -0.052,    # UPDATED (was -0.05)
#   'vega': 0.098,      # UPDATED (was 0.10)
#   'iv': 23.1,         # UPDATED (was 22.5)
#   'oi': 156000,
#   'volume': 2345
# }

# EXTRACT CURRENT GREEKS
current_greeks = {
    'delta': 0.68,
    'gamma': 0.021,
    'theta': -0.052,
    'vega': 0.098
}
current_iv = 23.1

# UPDATE POSITION
position.current_greeks = current_greeks
position.current_iv = current_iv
position.update_market_data(current_premium=47.25, greeks=current_greeks, iv=23.1)
```

**What The Greeks Tell Us**

| Greek | Change | Meaning | Action |
|-------|--------|---------|--------|
| **Delta** | 0.65→0.68 | Position is MORE bullish | Monitor for reversal |
| **Gamma** | 0.020→0.021 | Convexity still stable | No imminent binary event |
| **Theta** | -0.05→-0.052 | Time decay accelerating | Position losing ₹0.052/day |
| **Vega** | 0.10→0.098 | IV sensitivity dropping | Less vulnerable to IV shocks |
| **IV** | 22.5%→23.1% | Market volatility up 0.6% | Premium inflation |

### 2.3 Capture Greeks History (Step 3 - Every 30 seconds)

**File**: `optmonitor.py::update_greeks_history()`

**Purpose**: Track Greek changes over time for trend detection

```python
# EVERY 30 SECONDS, CAPTURE GREEKS SNAPSHOT
greeks_history.append({
    'timestamp': 2024-12-31 09:15:30,
    'delta': 0.68,
    'gamma': 0.021,
    'theta': -0.052,
    'vega': 0.098,
    'iv': 23.1,
    'premium': 47.25
})

# AFTER 3 SAMPLES (90 seconds), START TREND DETECTION
# Sample 1 (t=0s):   delta=0.65
# Sample 2 (t=30s):  delta=0.665
# Sample 3 (t=60s):  delta=0.68
# Trend: INCREASING (positive), no reversal

# SAMPLE 4 (t=90s):  delta=0.675 (DECLINING!)
# Trend: REVERSAL DETECTED? Not yet - only 1 down sample
# Need 2 consecutive downs OR rolling average confirmation

# SAMPLE 5 (t=120s): delta=0.668 (DECLINING AGAIN!)
# Trend: 2 CONSECUTIVE DOWNS = Delta Reversal Confirmed
# ACTION: Check other Greeks for confirmation before exit
```

**Greek History Use Cases**
1. **Delta Reversal**: Is momentum slowing?
2. **Gamma Explosion**: Is gamma spiking near expiry?
3. **Theta Acceleration**: Is time decay accelerating?
4. **Vega Crush**: Is IV collapsing suddenly?

### 2.4 Update P&L (Step 4 - Every 5 seconds)

```python
# UNREALIZED P&L CALCULATION
unrealized_pnl = (current_premium - entry_premium) × quantity
# e.g., (47.25 - 45.75) × 655 = ₹983.25 profit

# DISPLAY
Position: BANKNIFTY24DEC1000CE
Entry:    ₹45.75
Current:  ₹47.25  (↑ ₹1.50)
P&L:      ₹983.25 (↑ 3.28%)
Time:     45 seconds
```

### 2.5 Update Sentiment Data (Step 5 - Every monitoring cycle)

```python
# GET CURRENT SENTIMENT
current_pcr = broker.get_put_call_ratio("BANKNIFTY")
# Entry: 1.25
# Current: 1.15 (dropping - less fear)
# Change: -8% (conviction weakening)

current_oi_buildup = calculate_oi_change_pct("BANKNIFTY")
# Entry: 8.5%
# Current: 5.2% (OI buildup fading)
# Change: -38.8% (significant fade)

# SENTIMENT ANALYSIS
pcr_fade = entry_pcr > current_pcr * (100% + threshold)
oi_fade = entry_oi_buildup > current_oi_buildup * (100% + threshold)

# Used later in exit logic
```

---

## Part 3: EXIT DECISION FRAMEWORK

### 3.1 Greeks-Based Smart Exits (v2.0 Framework)

**File**: `optmonitor.py::check_greeks_*()`

#### EXIT 1: Delta Reversal

**Signal**: Momentum loss, price action stopping

```python
def check_greeks_delta_reversal():
    """
    Exit if delta declining rapidly - indicates reversal/momentum loss.
    
    IMPROVED (v2.0): Requires confirmation via:
    - 2 consecutive cycles of delta decline, OR
    - Rolling average of last 3 samples showing declining trend
    
    This reduces false positives during volatile periods.
    """
    
    # Example History:
    # T=0s:   delta=0.68
    # T=30s:  delta=0.675 (change=-0.005)
    # T=60s:  delta=0.668 (change=-0.007)  ← 1st decline
    # T=90s:  delta=0.662 (change=-0.006)  ← 2nd decline CONFIRMED
    
    is_confirmed, delta_change = position.get_delta_trend_confirmed()
    
    if is_confirmed and delta_change < -0.05:  # Threshold: -0.05/cycle
        SIGNAL: "Delta reversal detected - momentum loss"
        ACTION: EXIT (sell to close)
        REASON: "greeks_delta_reversal"
```

**Real Example**
```
Time    Delta    Change    Confirmation    Action
------- -------- --------- --------------- --------
09:15:30 0.68    -         Start           HOLD
09:16:00 0.675   -0.005    1st decline     HOLD
09:16:30 0.668   -0.007    2nd decline ✓   EXIT
         
Exit at ₹46.80 (change from ₹45.75)
P&L: (46.80 - 45.75) × 655 = ₹688.25 profit
```

#### EXIT 2: Gamma Explosion

**Signal**: Binary risk near expiry

```python
def check_greeks_gamma_explosion():
    """
    Exit if gamma spiked - indicates high binary risk near expiry.
    
    DUAL-FACTOR CHECK:
    1. Relative: current_gamma > entry_gamma × 1.5
    2. Absolute: current_gamma > 0.04 (safety cap)
    
    Either condition triggers exit (mutual OR logic).
    """
    
    # Example 1: Relative trigger
    # Entry gamma:   0.020
    # Current gamma: 0.035 (approaching expiry)
    # Ratio: 0.035 / 0.020 = 1.75x (> 1.5x threshold) ✓
    # SIGNAL: Exit - gamma explosion
    
    # Example 2: Absolute trigger
    # Entry gamma:   0.010 (far expiry)
    # Current gamma: 0.044 (now closer to expiry)
    # Ratio: 0.044 / 0.010 = 4.4x (but not 1.5x)
    # Absolute: 0.044 > 0.04 cap ✓
    # SIGNAL: Exit - absolute gamma exceeded
    
    is_dangerous, current_gamma, reason = position.get_gamma_status()
    
    if is_dangerous:
        SIGNAL: f"Gamma explosion - {reason}"
        ACTION: EXIT (sell to close)
        REASON: "greeks_gamma_explosion"
```

**Why Exit on Gamma Explosion?**

Gamma is the acceleration of delta. High gamma means:
- Delta changes FAST
- Small price moves = big delta jumps
- Near expiry = binary outcomes (0.0 or 1.0 delta)
- Risk of gap moves and sudden reversals
- Protection strategy: Exit before binary event

#### EXIT 3: Theta Acceleration

**Signal**: Time decay is winning

```python
def check_greeks_theta_acceleration():
    """
    Exit if theta accelerating rapidly - time decay eating position.
    
    CONTEXT-AWARE (v2.0):
    - Only triggers if |current_theta| > |entry_theta| × 3, AND
    - (P&L <= 0 OR Delta is weakening)
    
    Avoids killing WINNING trades from theta noise.
    """
    
    # Example 1: Losing position + theta acceleration
    # Entry theta:   -0.05 (₹0.05/day decay)
    # Current theta: -0.18 (3.6x worse)
    # P&L: -₹400 (LOSING)
    # SIGNAL: ✓ Exit - theta is killing us
    
    # Example 2: Winning position + theta acceleration
    # Entry theta:   -0.05
    # Current theta: -0.20 (4x worse)
    # P&L: +₹2500 (WINNING)
    # Delta weakening? NO - still bullish
    # SIGNAL: ✗ HOLD - don't fight the decay, we're winning
    
    is_dangerous, theta_reason = position.get_theta_status()
    
    if is_dangerous:
        SIGNAL: f"Theta acceleration - {theta_reason}"
        ACTION: EXIT (sell to close)
        REASON: "greeks_theta_acceleration"
```

**Why Context-Aware?**

Theta is ALWAYS negative (options lose value). But:
- **If position is WINNING**: Theta decay helps us → HOLD
- **If position is LOSING**: Theta decay hurts us → EXIT
- Only exit when theta acceleration + losing conditions

#### EXIT 4: Vega Crush (Dynamic IV Regime)

**Signal**: Volatility shock detected

```python
def check_greeks_vega_crush():
    """
    Exit if vega changed sharply - IV shock indicates reversal.
    
    DYNAMIC THRESHOLDS by IV REGIME:
    - Low IV regime (<50%): threshold = 1.0%
    - High IV regime (>=50%): threshold = 3.0%
    
    Prevents false exits in naturally volatile markets.
    """
    
    # Example 1: IV Crush in low volatility
    # Current IV: 18% (low regime)
    # Entry IV:  20%
    # Change:   -2% (> 1% threshold for low regime) ✓
    # SIGNAL: IV crush detected - volatility collapse
    # ACTION: EXIT
    
    # Example 2: IV stays stable in high volatility
    # Current IV: 52% (high regime)
    # Entry IV:  51%
    # Change:   +1% (< 3% threshold for high regime) ✗
    # SIGNAL: No vega crush - normal volatility
    # ACTION: HOLD
    
    is_dangerous, vega_reason = position.get_vega_status()
    
    if is_dangerous:
        SIGNAL: f"Vega crush - {vega_reason}"
        ACTION: EXIT (sell to close)
        REASON: "greeks_vega_crush"
```

### 3.2 P&L-Based Exits

```python
def check_pnl_exit():
    """
    Traditional P&L-based exits: target & stop loss
    """
    
    for symbol, position in self.positions.items():
        # EXIT 1: PROFIT TARGET
        if position.unrealized_pnl >= PROFIT_TARGET:  # ₹2,000
            SIGNAL: "Profit target reached"
            ACTION: EXIT
            REASON: "profit_target"
            # Example: Entry ₹45.75 → Current ₹47.50
            # P&L: (47.50 - 45.75) × 655 = ₹1,143.75 → Likely 2K+ with volume
        
        # EXIT 2: STOP LOSS
        elif position.unrealized_pnl <= -STOP_LOSS:  # -₹500
            SIGNAL: "Stop loss hit"
            ACTION: EXIT
            REASON: "stop_loss"
            # Example: Entry ₹45.75 → Current ₹44.00
            # P&L: (44.00 - 45.75) × 655 = -₹1,143.75 → Exceeds stop
        
        # EXIT 3: MAX DURATION
        elif time.time() - position.entry_time >= 300:  # 5 minutes
            SIGNAL: "Max duration exceeded"
            ACTION: EXIT
            REASON: "max_duration"
            # Automatic exit after 5 minutes to reduce overnight risk
```

### 3.3 Sentiment-Based Exits

```python
def check_sentiment_exit():
    """
    Market sentiment-based exits: conviction signals
    """
    
    # EXIT 1: PCR FADE (for PE positions)
    if position.side == "PE":
        pcr_change = (entry_pcr - current_pcr) / entry_pcr * 100
        
        if pcr_change > 40:  # Entry PCR dropped 40%+
            SIGNAL: "PE conviction weakening - PCR fade"
            ACTION: EXIT
            REASON: "sentiment_pcr_fade"
            # Example:
            # Entry PCR: 1.25 (bearish - people buying puts)
            # Current PCR: 0.75 (bullish - people selling puts)
            # Change: -40% (conviction collapsed)
    
    # EXIT 2: OI BUILDUP FADE
    oi_change = (entry_oi_buildup - current_oi_buildup) / entry_oi_buildup * 100
    
    if oi_change > 40:  # OI buildup dropped 40%+
        SIGNAL: "OI buildup fading - conviction weak"
        ACTION: EXIT
        REASON: "sentiment_oi_fade"
        # Example:
        # Entry OI: +8.5% (strong bullish setup)
        # Current OI: +5.0% (buildup slowing)
        # Change: -41% (conviction fading)
```

### 3.4 Risk Management - Portfolio-Level Greeks

```python
def check_portfolio_greeks():
    """
    Monitor total portfolio exposure (all positions combined)
    """
    
    portfolio_delta = sum(p.current_greeks['delta'] * p.quantity 
                         for p in self.positions.values())
    
    portfolio_gamma = sum(p.current_greeks['gamma'] * p.quantity 
                         for p in self.positions.values())
    
    portfolio_theta = sum(p.current_greeks['theta'] * p.quantity 
                         for p in self.positions.values())
    
    # Example Portfolio State:
    # Position 1: delta=0.70 × 500 = +350
    # Position 2: delta=0.45 × 600 = +270
    # Position 3: delta=0.80 × 400 = +320
    # ─────────────────────────────────
    # Portfolio Delta: +940 (highly bullish)
    
    if portfolio_delta > 900:  # Threshold: max net delta
        ALERT: "Portfolio too bullish - reduce exposure"
        ACTION: Close weaker positions to balance
    
    if portfolio_gamma > 100:  # Threshold: convexity risk
        ALERT: "Portfolio gamma too high - binary risk"
        ACTION: Reduce positions approaching expiry
    
    if portfolio_theta > -5:  # Theta should be negative
        ALERT: "Portfolio theta insufficient - not time decay focused"
        ACTION: Add more decay-benefiting positions
```

---

## Part 4: EXIT EXECUTION

### 4.1 Close Position Order

```python
def close_position(symbol, current_premium, exit_reason):
    """
    Execute exit order and finalize position
    """
    
    # STEP 1: Prepare exit order (opposite side of entry)
    exit_order = {
        'exchange': 'NFO',
        'symbol': symbol,
        'side': 'SELL',                    # Opposite of BUY
        'order_type': 'MARKET',            # Market execution
        'quantity': position.quantity,     # Same as entry
        'product': 'INTRADAY',
        'duration': 'DAY'
    }
    
    # STEP 2: Execute order
    response = broker.place_order(exit_order)
    # Result: Order filled at ₹47.20 (near current LTP)
    
    # STEP 3: Calculate final P&L
    exit_price = response['filled_price']
    realized_pnl = (exit_price - position.entry_premium) × position.quantity
    # (47.20 - 45.75) × 655 = ₹947.75 profit
    
    # STEP 4: Account for charges
    brokerage = 15  # ₹15 per order
    stt = exit_price * position.quantity * 0.005  # 0.5% on exit
    transaction_charges = exit_price * position.quantity * 0.00005
    gst = (brokerage + transaction_charges) * 0.18
    
    total_charges = brokerage + stt + transaction_charges + gst
    net_pnl = realized_pnl - total_charges
    # net_pnl ≈ ₹900 (after charges)
    
    # STEP 5: Update position state
    position.exit_price = exit_price
    position.exit_reason = exit_reason
    position.exit_datetime = now()
    position.realized_pnl = net_pnl
    position.exit_greeks = current_greeks  # Store exit Greeks
    
    # STEP 6: Persist to disk
    save_closed_position_to_history(position)
    
    # STEP 7: Remove from active positions
    del self.positions[symbol]
    symbol_pool.remove_position(symbol)
    
    # STEP 8: Log for analytics
    log_event("OPTION_EXIT", {
        'symbol': symbol,
        'entry_price': 45.75,
        'exit_price': 47.20,
        'quantity': 655,
        'entry_time': entry_time,
        'exit_time': exit_time,
        'duration_seconds': 225,  # 3m 45s
        'exit_reason': exit_reason,
        'entry_greeks': position.entry_greeks,
        'exit_greeks': position.exit_greeks,
        'realized_pnl': net_pnl,
        'entry_iv': position.entry_iv,
        'exit_iv': position.current_iv
    })
    
    return {
        'symbol': symbol,
        'pnl': net_pnl,
        'exit_reason': exit_reason,
        'duration': (exit_time - entry_time).total_seconds()
    }
```

### 4.2 Position Closure Summary

```
═══════════════════════════════════════════════════════════════
                    POSITION CLOSED
═══════════════════════════════════════════════════════════════

Symbol:           BANKNIFTY24DEC1000CE
Entry Time:       09:15:30
Exit Time:        09:19:15
Duration:         3m 45s

Entry Price:      ₹45.75
Exit Price:       ₹47.20
Change:           +₹1.45 (+3.17%)

Quantity:         655 contracts
Gross P&L:        ₹947.75

Charges:
  Brokerage:      ₹15.00
  STT (0.5%):     ₹154.58
  Tx Charges:     ₹15.46
  GST (18%):      ₹25.56
  ─────────────
  Total:          ₹210.60

Net P&L:          ₹737.15 ✓ PROFIT
Win/Loss:         WIN

Entry Greeks:     Delta=0.65, Gamma=0.020, Theta=-0.05, Vega=0.10
Exit Greeks:      Delta=0.68, Gamma=0.021, Theta=-0.052, Vega=0.098
Entry IV:         22.5%
Exit IV:          23.1%

Exit Reason:      profit_target (P&L reached ₹2,000+ threshold)

═══════════════════════════════════════════════════════════════
```

---

## Part 5: HOW LTP & GREEKS DRIVE DECISIONS

### 5.1 LTP (Last Traded Price) Flow

```
LTP Fetched (Every 5 Seconds)
    ↓
Update Position.current_premium
    ↓
Calculate Unrealized P&L = (current_premium - entry_premium) × qty
    ↓
┌─────────────────────────────────────────────────────────────┐
│                   DECISION TREE                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  IF unrealized_pnl >= profit_target (₹2,000)              │
│     → EXIT (profit_target)                                 │
│                                                             │
│  ELSE IF unrealized_pnl <= -stop_loss (₹-500)             │
│     → EXIT (stop_loss)                                     │
│                                                             │
│  ELSE IF time_held >= max_duration (5 minutes)            │
│     → EXIT (max_duration)                                  │
│                                                             │
│  ELSE → Continue to Greeks monitoring                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Greeks (Delta, Gamma, Theta, Vega) Flow

```
Option Chain Fetched (Every ~30 seconds)
    ↓
Extract Strike Row (strike_price × side [CE/PE])
    ↓
┌─────────────────────────────────┬────────────────────────────┐
│  Greek 1: DELTA                 │  Greek 2: GAMMA            │
├─────────────────────────────────┼────────────────────────────┤
│ What: Directional momentum      │ What: Momentum acceleration│
│ Range: -1.0 to +1.0            │ Range: 0 to 0.5 (typically)
│                                 │                            │
│ Delta > 0: Bullish             │ Gamma > 0: Always (long)   │
│ Delta < 0: Bearish             │ Gamma ↑: More acceleration │
│ Delta ≈ 0: Neutral             │                            │
│                                 │                            │
│ Decline Check:                  │ Explosion Check:           │
│ - History: last 3 samples       │ - Current > Entry × 1.5 OR │
│ - If 2 consecutive declines     │ - Current > 0.04 absolute  │
│ - AND delta_change < -0.05      │ - → EXIT                   │
│ - → EXIT                        │                            │
│                                 │                            │
│ Signal: Momentum loss           │ Signal: Binary risk        │
│ Action: Cut losses, exit        │ Action: Protect, exit      │
└─────────────────────────────────┴────────────────────────────┘

┌─────────────────────────────────┬────────────────────────────┐
│  Greek 3: THETA                 │  Greek 4: VEGA             │
├─────────────────────────────────┼────────────────────────────┤
│ What: Time decay (negative)     │ What: IV sensitivity       │
│ Range: -0.3 to 0 (always ≤ 0)  │ Range: 0 to 0.3            │
│                                 │                            │
│ Theta < 0: Position loses value │ Vega > 0: IV up = value up│
│ |Theta| ↑: Faster decay         │ Vega ↓: IV down = value dn
│                                 │                            │
│ Acceleration Check:             │ Crush Check (Dynamic):     │
│ - |Current| > |Entry| × 3 AND   │ - Low IV (<50%):           │
│ - (P&L <= 0 OR delta weakening) │   threshold = 1.0%         │
│ - → EXIT                        │ - High IV (≥50%):          │
│                                 │   threshold = 3.0%         │
│                                 │ - IV change > threshold    │
│                                 │   → EXIT                   │
│                                 │                            │
│ Signal: Time against us         │ Signal: Volatility shock   │
│ Action: Don't fight decay,exit  │ Action: Reversal likely,ex │
└─────────────────────────────────┴────────────────────────────┘
```

### 5.3 Real-Time Decision Example

```
TIME: 09:15:30 (Entry)
────────────────────────────────
Premium: ₹45.75
Entry Greeks: Delta=0.65, Gamma=0.020, Theta=-0.05, Vega=0.10
Entry IV: 22.5%
Status: POSITION OPEN


TIME: 09:15:35
────────────────────────────────
LTP Update:
  Premium: ₹46.25
  Change: +₹0.50 (+1.09%)
  P&L: +₹327.50

Greeks Check (5-sec interval - skip, wait for 30-sec refresh)
  
Decision: HOLD (insufficient data yet)


TIME: 09:16:05 (30-sec interval)
────────────────────────────────
LTP Update:
  Premium: ₹46.50
  Change: +₹0.75 (+1.64%)
  P&L: +₹491.25

Greeks Update (Fetched from option chain):
  Current Greeks: Delta=0.66, Gamma=0.021, Theta=-0.051, Vega=0.099
  Current IV: 23.0%

Greeks History:
  [1] T=0s:   Delta=0.65, Gamma=0.020, Theta=-0.050, Vega=0.100
  [2] T=30s:  Delta=0.66, Gamma=0.021, Theta=-0.051, Vega=0.099

Exit Check - P&L Targets:
  ✗ P&L (₹491) < Profit Target (₹2,000)
  ✗ P&L (₹491) > Stop Loss (-₹500)
  ✗ Duration (35s) < Max (300s)

Exit Check - Greeks:
  ✗ Delta Reversal: Delta INCREASING (0.65→0.66), no reversal yet
  ✗ Gamma Explosion: Gamma stable (0.020→0.021 = 1.05x), not 1.5x+
  ✗ Theta Acceleration: |Theta| stable (-0.050→-0.051), not 3x+
  ✗ Vega Crush: IV increased (+0.5%), no crush

Exit Check - Sentiment:
  ✗ PCR Fade: Need more samples
  ✗ OI Fade: Need more samples

Decision: HOLD - All checks green, continue monitoring


TIME: 09:16:35
────────────────────────────────
LTP Update:
  Premium: ₹46.20
  Change: +₹0.45 (+0.98%)
  P&L: +₹295.25  ← DECLINING!

Greeks Update:
  Current Greeks: Delta=0.64, Gamma=0.022, Theta=-0.053, Vega=0.098
  Current IV: 23.5%

Greeks History:
  [1] T=0s:   Delta=0.65
  [2] T=30s:  Delta=0.66
  [3] T=60s:  Delta=0.64  ← DECLINE DETECTED!

Exit Check - Greeks:
  ? Delta Reversal: 
    - Sample 1→2: +0.01 (increase)
    - Sample 2→3: -0.02 (DECLINE 1st)
    - Need 1 more consecutive decline to confirm
    - Status: NOT YET CONFIRMED

  ✗ Gamma Explosion: Gamma=0.022, Entry=0.020
    - Ratio: 0.022/0.020 = 1.1x (< 1.5x threshold)
    - Absolute: 0.022 < 0.04 cap
    - Status: SAFE

  ✓ Theta Acceleration: |Theta| increasing
    - Entry: -0.050
    - Current: -0.053
    - Ratio: 0.053/0.050 = 1.06x (< 3.0x threshold)
    - Status: NOT YET - need 3x multiplier

Decision: HOLD - Delta decline detected but not confirmed yet


TIME: 09:17:05
────────────────────────────────
LTP Update:
  Premium: ₹45.95
  Change: +₹0.20 (+0.44%)
  P&L: +₹131.00  ← P&L HALVING!

Greeks Update:
  Current Greeks: Delta=0.63, Gamma=0.024, Theta=-0.055, Vega=0.096
  Current IV: 24.0%

Greeks History:
  [1] T=0s:   Delta=0.65
  [2] T=30s:  Delta=0.66
  [3] T=60s:  Delta=0.64
  [4] T=90s:  Delta=0.63  ← DECLINE DETECTED 2nd TIME!

Exit Check - Greeks:
  ✓✓ Delta Reversal CONFIRMED:
    - 2 consecutive declines: 0.66→0.64→0.63
    - Change: -0.02 total (< -0.05 threshold per sample, but trend clear)
    - SIGNAL: Delta reversal = momentum loss
    - RECOMMENDATION: EXIT before further downside

  ✓ Gamma Explosion: Gamma=0.024
    - Ratio: 0.024/0.020 = 1.2x (still < 1.5x)
    - Status: APPROACHING but not critical yet

  ✓ Theta Acceleration: 
    - |Theta|: 0.055 vs entry 0.050
    - Ratio: 1.1x (< 3x threshold)
    - P&L: +₹131 (WINNING - condition NOT MET)
    - Status: NOT TRIGGERED (position winning)

Decision: EXIT ✓
Reason: Delta Reversal Confirmed (momentum loss detected)
Action: Place SELL order at market

Exit Order Executed:
  Exit Price: ₹45.80
  P&L: (45.80 - 45.75) × 655 = ₹32.75
  Plus earlier premium: already captured
  Final: Approximately +₹300 NET profit
```

---

## Part 6: Complete Process Checklist

### Entry Checklist
- [ ] Signal received from TradingView webhook
- [ ] Capital validated (>= ₹30,000 available)
- [ ] Slots validated (< 30 open)
- [ ] Daily trades validated (< 30 total)
- [ ] Instrument valid (exists in NFO)
- [ ] Strike available in option chain
- [ ] LTP fetched
- [ ] Greeks extracted and stored
- [ ] IV captured
- [ ] Sentiment data (PCR, OI) captured
- [ ] Order placed and filled
- [ ] Position added to tracking
- [ ] State saved to positions.json

### Monitoring Checklist (Every 5 seconds)
- [ ] LTP fetched and updated
- [ ] Unrealized P&L calculated
- [ ] Greeks history captured (every 30s)
- [ ] Sentiment data fetched
- [ ] P&L targets checked
- [ ] Greeks exits evaluated
- [ ] Sentiment exits evaluated
- [ ] Portfolio Greeks calculated
- [ ] State logged to file

### Exit Checklist
- [ ] Exit reason identified
- [ ] Current premium confirmed
- [ ] Exit Greeks captured
- [ ] Exit order placed
- [ ] Order filled and confirmed
- [ ] P&L calculated (with charges)
- [ ] Exit logged with full details
- [ ] Position removed from tracking
- [ ] History saved for analytics
- [ ] Capital freed for next trade

---

## Performance Summary

### Expected Metrics (Paper Trading)
- **Win Rate**: 55-65% (depends on Greeks signal accuracy)
- **Avg Win**: ₹1,500-2,000 (hitting profit targets)
- **Avg Loss**: ₹400-600 (hitting stops)
- **Profit Factor**: 1.5-2.0x (wins/losses ratio)
- **Daily Average**: ₹5,000-8,000 (10 trades @ 60% win rate)

### Key Success Factors
1. **Timely Greeks Monitoring**: Capture signals before move exhaustion
2. **Multi-Factor Exits**: Combine Greeks + P&L + Sentiment for confidence
3. **Position Sizing**: ₹30K/trade prevents oversized losses
4. **Quick Exits**: Don't hold winners too long, don't fight losers
5. **Sentiment Validation**: PCR/OI fades = reversal signals

