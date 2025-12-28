# Options Trading Bot - Architecture Documentation

## System Overview

The Options Trading Bot is a standalone system independent from the Equity bot that monitors, enters, and exits options positions based on TradingView webhook signals and advanced Greeks monitoring. It's designed for paper trading with full risk management and position tracking.

### Key Characteristics
- **Exchange**: NSE (NFO - National Futures and Options)
- **Mode**: Paper Trading (simulated, no real capital)
- **Capital Per Trade**: ₹30,000
- **Max Concurrent Positions**: 30 slots
- **Max Daily Trades**: 30 total trades/day
- **Product Type**: INTRADAY (day trading, positions closed EOD)

---

## System Architecture

### 1. Core Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    TradingView Webhook Router                   │
│              (Entry Signal Distribution)                        │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ├─────────────────────────────────────────┐
                     │                                         │
        ┌────────────▼──────────┐            ┌───────────────▼──────┐
        │  Options Bot Entry    │            │  Equity Bot Entry    │
        │   (Independent)       │            │   (Independent)      │
        └────────────┬──────────┘            └──────────────────────┘
                     │
        ┌────────────▼────────────────────────────────────────────┐
        │        Options Position Monitor (optmonitor.py)         │
        │                                                         │
        │  ✓ Tracks open CE/PE positions                         │
        │  ✓ Updates LTP from broker (5-sec intervals)          │
        │  ✓ Calculates P&L in real-time                        │
        │  ✓ Monitors Greeks (Delta, Gamma, Theta, Vega)        │
        │  ✓ Fetches IV from option chain                       │
        │  ✓ Detects fake moves & unusual activity             │
        │  ✓ Smart exit logic based on Greeks                   │
        └────────────┬────────────────────────────────────────────┘
                     │
        ┌────────────▼────────────────────────────────────────────┐
        │         Exit Decision Engine (Multiple Monitors)        │
        │                                                         │
        │  1. Greeks-Based Exit (NEW v2.0 Framework)             │
        │     - Delta Reversal (with 2-cycle confirmation)       │
        │     - Gamma Explosion (dual-factor: 1.5x OR 0.04 cap)  │
        │     - Theta Acceleration (context-aware)               │
        │     - Vega Crush (dynamic IV regime thresholds)        │
        │                                                         │
        │  2. P&L-Based Exit                                     │
        │     - Profit Target (₹2,000 default)                   │
        │     - Stop Loss (₹500 default)                         │
        │     - Max Duration (5 minutes per position)            │
        │                                                         │
        │  3. Sentiment-Based Exit                               │
        │     - PCR Fade Detection (PE conviction weakening)     │
        │     - OI Buildup Fade (buildup conviction weakening)   │
        │     - Fake Move Detection (sustained price action)     │
        │                                                         │
        │  4. Risk Management                                    │
        │     - Decay Monitoring (premium decay tracking)        │
        │     - Portfolio Greeks (net delta/gamma/theta)         │
        │     - Capital Allocation (30K per trade)               │
        └────────────┬────────────────────────────────────────────┘
                     │
        ┌────────────▼────────────────────────────────────────────┐
        │        Order Execution (angelone.py)                   │
        │                                                         │
        │  ✓ MARKET orders (immediate execution)                |
        │  ✓ Intraday only (products close EOD)                 │
        │  ✓ Rate limiting (2 sec per request, 5 batch)         │
        │  ✓ Error handling & retry logic                       │
        └────────────┬────────────────────────────────────────────┘
                     │
        ┌────────────▼────────────────────────────────────────────┐
        │         Data & State Persistence                       │
        │                                                         │
        │  - positions.json (open positions state)               │
        │  - session.json (login tokens)                         │
        │  - option_chain_cache.json (Greeks/IV cache)           │
        │  - instrument.json (NFO contracts list)                │
        └────────────────────────────────────────────────────────┘
```

### 2. Class Hierarchy

#### OptionPosition (Core State)
```python
class OptionPosition:
    # Identity
    symbol: str              # "BANKNIFTY24DEC1000CE"
    underlying: str          # "BANKNIFTY"
    side: str               # "CE" or "PE"
    strike_price: float     # 1000.0
    expiry: str             # "2024-12-31"
    
    # Entry State
    entry_price: float      # Entry premium paid
    entry_premium: float    # Same as entry_price
    entry_greeks: Dict      # Delta/Gamma/Theta/Vega at entry
    entry_iv: float         # IV at entry
    entry_delta: float      # For tracking reversal
    
    # Current State
    current_premium: float  # LTP (updated every cycle)
    current_greeks: Dict    # Current Greeks from option chain
    current_iv: float       # Current IV
    
    # Greek History (for trend detection)
    greeks_history: List    # [{'timestamp': dt, 'delta': x, ...}]
    delta_trend_samples: List  # For reversal confirmation
    
    # P&L Tracking
    quantity: int           # Lot size
    unrealized_pnl: float   # Premium diff × quantity
    realized_pnl: float     # Closed positions only
    
    # Risk State
    entry_datetime: datetime
    max_duration: int       # 5 minutes default
    profit_target: float    # ₹2,000
    stop_loss: float        # ₹500
    
    # Sentiment State
    entry_pcr: float        # Put-Call Ratio at entry
    entry_oi_buildup: float # OI change % at entry
```

#### OptionPositionMonitor (Master Controller)
```python
class OptionPositionMonitor:
    positions: Dict[str, OptionPosition]  # All open positions
    symbol_pool: ActiveSymbolPool         # Bulk fetch manager
    broker: AngelOneOptionsBroker         # Broker interface
    
    # Core Methods
    add_position()           # Entry from webhook
    refresh_position_ltps()  # Update all LTPs (5-sec)
    
    # Exit Monitors
    check_greeks_*()        # Greeks-based exits
    check_pnl_exit()        # P&L-based exits
    check_sentiment_exit()  # Sentiment-based exits
    
    # State Management
    get_position_summary()  # Portfolio overview
    to_json() / from_json() # Persistence
```

#### ActiveSymbolPool (API Optimization)
```python
class ActiveSymbolPool:
    """
    Manages active position symbols to reduce API calls.
    Only fetches LTP for currently open positions.
    Prevents wasted bulk_fetch calls on closed positions.
    """
    active_symbols: Set[str]
    add_position(symbol)
    remove_position(symbol)
    get_active_symbols() -> List[str]
```

#### LTPBucketManager (Rate Limiting)
```python
class LTPBucketManager:
    """
    Divides positions into buckets for staggered LTP updates.
    
    Example: 30 positions → 6 buckets of 5 each
    - Cycle 1: Check bucket 1 (5 API calls)
    - Cycle 2: Check bucket 2 (5 API calls)
    - ...
    - Cycle 6: Check bucket 6 (5 API calls)
    - Cycle 7: Back to bucket 1
    
    Effect: Spreads 30 API calls over 6 cycles = 5/cycle max
    Prevents rate limit violations and API saturation.
    """
    buckets: List[List[str]]
    current_bucket_index: int
    get_current_bucket() -> List[str]
```

---

## Data Flow: Position Lifecycle

### Phase 1: ENTRY

```
TradingView Webhook
├─ Symbol: BANKNIFTY
├─ Signal: BUY/SELL CE/PE
├─ Strike: 1000
├─ Expiry: 2024-12-31
└─ Premium: ₹45

↓ (webhook_router.py)

Entry Validation
├─ Capital available?     (>= ₹30,000)
├─ Slots available?       (< 30)
├─ Max daily trades?      (< 30)
└─ Instrument exists?     (Check instrument.json)

↓ (optmonitor.py::add_position)

Fetch Option Data
├─ Get LTP from broker
├─ Fetch Option Chain
├─ Extract Greeks (Delta, Gamma, Theta, Vega)
├─ Calculate IV
├─ Get PCR (Put-Call Ratio)
└─ Record OI buildup

↓

Create OptionPosition
├─ Store entry_price, entry_greeks, entry_iv
├─ Calculate unrealized_pnl
├─ Set profit_target, stop_loss
├─ Initialize greeks_history
└─ Add to positions dict

↓

Execute MARKET Order
├─ Order type: MARKET
├─ Product: INTRADAY
├─ Quantity: Determined by capital (30K / premium)
└─ Exchange: NFO

↓ Status: OPEN POSITION ✓
```

### Phase 2: MONITORING (5-second loop)

```
Main Loop (main.py)
↓ Every 5 seconds

Step 1: Refresh LTPs
├─ Get active_symbols from symbol_pool
├─ Bulk fetch LTPs: broker.get_ltp_bulk(symbols)
├─ Update position.current_premium for each position
└─ Rate limited by LTPBucketManager

Step 2: Fetch Option Chain
├─ For each symbol:
│  ├─ Get option_chain from broker
│  ├─ Find matching strike row (strike_price × side)
│  ├─ Extract Greeks (delta, gamma, theta, vega)
│  ├─ Calculate IV from row
│  └─ Store in position.current_greeks
└─ Cache option_chain to avoid repeated fetches

Step 3: Update Greeks History
├─ Capture current_greeks every 30 seconds
├─ Add to greeks_history: [{timestamp, delta, gamma, theta, vega, iv}, ...]
├─ Keep last 20 samples for trend detection
└─ Enable multi-sample confirmation logic

Step 4: Update P&L
├─ unrealized_pnl = (current_premium - entry_premium) × quantity
├─ Logging (log_pnl event)
└─ Display in UI

Step 5: Update Sentiment Data
├─ Fetch PCR (Put-Call Ratio) for underlying
├─ Get OI buildup % for underlying
├─ Store for sentiment exit checking
└─ Used for conviction analysis
```

### Phase 3: EXIT DECISION

```
Check Exit Conditions (IN PRIORITY ORDER)
│
├─ 1. GREEKS-BASED EXITS (New v2.0 Framework)
│  │
│  ├─ Delta Reversal
│  │  ├─ Check: delta_change < -0.05 per cycle
│  │  ├─ Confirmation: 2 consecutive cycles OR 3-sample rolling avg
│  │  └─ Signal: Momentum loss, exit before further downside
│  │
│  ├─ Gamma Explosion
│  │  ├─ Check: gamma > entry_gamma × 1.5 OR gamma > 0.04 absolute
│  │  ├─ Logic: Dual-factor protection (relative + absolute)
│  │  └─ Signal: Binary risk near expiry, protect position
│  │
│  ├─ Theta Acceleration
│  │  ├─ Check: |theta| > |entry_theta| × 3.0
│  │  ├─ Confirmation: P&L <= 0 OR delta is weakening
│  │  └─ Signal: Time decay is winning, don't fight it
│  │
│  └─ Vega Crush
│     ├─ Low IV Regime (<50%): threshold = 1.0%
│     ├─ High IV Regime (>=50%): threshold = 3.0%
│     ├─ Check: vega_change > threshold
│     └─ Signal: IV shock indicates reversal
│
├─ 2. P&L-BASED EXITS
│  │
│  ├─ Profit Target
│  │  ├─ Check: unrealized_pnl >= profit_target (₹2,000)
│  │  └─ Signal: Lock in gains
│  │
│  ├─ Stop Loss
│  │  ├─ Check: unrealized_pnl <= -stop_loss (₹-500)
│  │  └─ Signal: Protect capital
│  │
│  └─ Max Duration
│     ├─ Check: current_time - entry_time >= 5 minutes
│     └─ Signal: Close aging positions, reduce overnight risk
│
├─ 3. SENTIMENT-BASED EXITS
│  │
│  ├─ PCR Fade (for PE positions)
│  │  ├─ Check: entry_pcr > 1.2 BUT current_pcr < 0.9
│  │  ├─ % Change: entry_pcr dropped 40%+
│  │  └─ Signal: PE conviction weakening, reversal likely
│  │
│  ├─ OI Buildup Fade
│  │  ├─ Check: entry_oi_buildup was high, now declined
│  │  ├─ % Change: dropped 40%+
│  │  └─ Signal: Conviction fade, reduce exposure
│  │
│  └─ Fake Move Detection
│     ├─ Check: Momentum filter detects sustained price action
│     └─ Signal: Whipsaw avoided, true direction confirmed
│
└─ 4. RISK MANAGEMENT
   │
   ├─ Portfolio Greeks
   │  ├─ Total Delta: sum(delta × qty for all positions)
   │  ├─ Total Gamma: sum(gamma × qty for all positions)
   │  ├─ Total Theta: sum(theta × qty for all positions)
   │  └─ Used to avoid net directional bias
   │
   └─ Decay Monitoring
      ├─ Track premium decay vs expected theta decay
      ├─ Detect unusual decay patterns
      └─ Flag positions decaying faster than expected
```

### Phase 4: EXIT EXECUTION

```
close_position(symbol, current_premium, exit_reason)
│
├─ Step 1: Record Exit State
│  ├─ Capture current_greeks as exit_greeks
│  ├─ Store exit_reason
│  └─ Calculate final P&L
│
├─ Step 2: Execute EXIT Order
│  ├─ Order type: MARKET (opposite side of entry)
│  ├─ Product: INTRADAY
│  ├─ Exchange: NFO
│  └─ Quantity: Same as entry quantity
│
├─ Step 3: Calculate PnL
│  ├─ realized_pnl = (exit_price - entry_price) × quantity
│  ├─ Account for brokerage, STT, transaction charges
│  └─ Store for analytics
│
├─ Step 4: Persistence
│  ├─ Store closed position in trade history
│  ├─ Update performance tracker
│  ├─ Log to trade_logger for ML learning
│  └─ Remove from positions dict
│
└─ Status: CLOSED ✓
   └─ Available for next entry
```

---

## Integration Points

### 1. TradingView Webhook Integration
- **Route**: `/webhook/options` (webhook_router.py)
- **Format**: JSON with symbol, side, strike, expiry
- **Flow**: Webhook → Entry Validation → Add Position

### 2. Broker Integration (AngelOne SmartAPI)
- **Authentication**: API Key, Client Code, Password, TOTP
- **Methods**:
  - `place_order()` - MARKET orders for entry/exit
  - `get_ltp_bulk()` - Bulk LTP fetch (5 symbols per call)
  - `fetch_option_chain()` - Greeks and IV extraction
  - `get_PCR()` - Put-Call ratio for sentiment
  
### 3. Logging & Analytics
- **optlogging.py**: Position tracking, P&L logging
- **trade_logger.py**: Entry/exit details for ML
- **performance_tracker.py**: Portfolio performance
- **Logging Events**:
  - Entry: `log_event("OPTION_ENTRY", ...)`
  - Exit: `log_event("OPTION_EXIT", ...)`
  - Greeks: `log_event("GREEKS_SIGNAL", ...)`

### 4. Fake Move & Decay Detection
- **fake_move_detector.py**: Premium movement pattern analysis
- **decay_monitor.py**: Theta decay tracking
- **Used for**: Signal validation, whipsaw avoidance

---

## Configuration

### optconfig.py - Master Configuration

```python
# Capital Management
MAX_CAPITAL = 900,000         # Total options capital
CAP_PER_TRADE = 30,000        # Per position
MAX_SLOTS = 30                # Concurrent positions
MAX_TRADES_PER_DAY = 30       # Daily limit

# P&L Targets
PROFIT_TARGET = 2000          # Exit when +₹2000
STOP_LOSS = 500               # Exit when -₹500
MAX_DURATION_MINUTES = 5      # 5-minute max hold

# Greeks Monitoring (NEW v2.0)
# Delta Reversal
DELTA_REVERSAL_THRESHOLD = -0.05              # Per cycle
DELTA_REVERSAL_CONFIRM_CYCLES = 2             # Confirmation cycles
ENABLE_DELTA_ROLLING_AVG = true               # Use 3-sample avg

# Gamma Explosion
GAMMA_MULTIPLIER_THRESHOLD = 1.5              # 1.5x from entry
GAMMA_ABSOLUTE_CAP = 0.04                     # Absolute limit

# Theta Acceleration
THETA_MULTIPLIER_THRESHOLD = 3.0              # 3x from entry
ENABLE_THETA_PNL_CHECK = true                 # Check P&L condition
ENABLE_THETA_DELTA_CHECK = true               # Check delta weakening

# Vega Crush (Dynamic)
VEGA_CRUSH_FIXED_THRESHOLD = 2.0              # Fallback
ENABLE_VEGA_DYNAMIC_THRESHOLD = true          # Dynamic IV regime
VEGA_LOW_IV_THRESHOLD = 1.0                   # Low IV regime
VEGA_HIGH_IV_THRESHOLD = 3.0                  # High IV regime
VEGA_IV_REGIME_BOUNDARY = 50.0                # IV % threshold

# Sentiment
EXIT_PCR_FADE_THRESHOLD = 40                  # % drop threshold
EXIT_OI_FADE_THRESHOLD = 40                   # % drop threshold
```

---

## Performance Metrics

### Position-Level Metrics
- **Entry Greeks**: Delta, Gamma, Theta, Vega (stored for ML)
- **Exit Greeks**: Same, stored at exit (for learning)
- **Greeks Delta**: Entry → Exit change tracking
- **Premium Tracking**: Entry → Current → Exit
- **Duration**: Time from entry to exit (seconds)
- **IV Tracking**: Entry IV vs Exit IV vs Current IV

### Portfolio-Level Metrics
- **Total Open P&L**: Sum of all unrealized P&L
- **Total Realized P&L**: Sum of closed position P&L
- **Net Delta**: Portfolio-level directional exposure
- **Net Gamma**: Portfolio-level convexity risk
- **Net Theta**: Portfolio-level time decay benefit
- **Capital Utilization**: Used capital / max capital
- **Win Rate**: Closed profitable / total closed
- **Avg Win Size**: Average profit per winner
- **Avg Loss Size**: Average loss per loser

### Risk Metrics
- **Max Drawdown**: Peak unrealized loss from peak
- **Sharpe Ratio**: Risk-adjusted returns
- **Decay Efficiency**: Actual theta decay vs expected
- **Sentiment Accuracy**: PCR/OI fade exit success rate
- **Greeks Signal Accuracy**: Smart exit success rate

---

## Error Handling & Recovery

### API Errors
- **Rate Limiting**: Bucket manager prevents overload
- **Connection Loss**: Retry with exponential backoff
- **Data Inconsistency**: Fall back to last known good state
- **Option Chain Fetch**: Use cached data if broker down

### Position State Errors
- **Missing Position**: Ignore stale position references
- **Invalid Greeks**: Use fallback defaults
- **LTP Fetch Failure**: Mark position for retry next cycle
- **Order Execution Failure**: Log error, keep position open, retry

### Recovery Mechanisms
- **Persistence**: positions.json saved every cycle
- **Startup Recovery**: Load positions from file on restart
- **Stale Position Cleanup**: Auto-close positions > EOD
- **Health Checks**: Broker connectivity checks every 30s

---

## Key Design Decisions

### 1. Why Bucketed LTP Updates?
- **Problem**: 30 positions × 5 sec cycle = 150 API calls/min
- **Solution**: Divide into 6 buckets = 5 calls/cycle max
- **Benefit**: Fits within rate limits, real-time monitoring

### 2. Why Greeks-Based Exits (v2.0)?
- **Problem**: Traditional P&L exits miss reversal signals
- **Solution**: Use Greeks (delta, gamma, theta, vega) for exits
- **Benefit**: 
  - Delta reversal = early momentum loss detection
  - Gamma explosion = binary risk protection
  - Theta context = avoid killing winners
  - Vega crush = IV shock response

### 3. Why Sentiment + Greeks Combination?
- **Problem**: Greeks alone don't capture market sentiment
- **Solution**: Combine with PCR fade & OI buildup signals
- **Benefit**: Multi-factor confirmation reduces whipsaws

### 4. Why Intraday Only?
- **Problem**: Options decay rapidly, overnight risk high
- **Solution**: Close all positions by EOD (5-minute max hold)
- **Benefit**: Simplified risk, no overnight Greeks surprises

---

## Next Steps for Users

1. **Configure `optconfig.py`**
   - Set capital limits
   - Tune Greeks thresholds
   - Adjust P&L targets

2. **Test with Paper Trading**
   - Validate entry/exit signals
   - Monitor Greeks tracking
   - Observe fake move detection

3. **Monitor Performance**
   - Check win rate
   - Track Greeks accuracy
   - Review sentiment signals

4. **Iterate & Optimize**
   - Adjust Greeks thresholds based on results
   - Fine-tune sentiment sensitivity
   - Optimize capital allocation

