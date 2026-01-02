# Trading System Architecture - Complete Guide

**Status**: Most Stable | **Version**: 2.0 | **Last Updated**: January 2, 2026

---

## Table of Contents

1. [System Overview](#system-overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Core Components](#core-components)
4. [Data Flow](#data-flow)
5. [Module Organization](#module-organization)
6. [Key Design Patterns](#key-design-patterns)
7. [Configuration Management](#configuration-management)
8. [Error Handling & Recovery](#error-handling--recovery)
9. [Performance Characteristics](#performance-characteristics)

---

## System Overview

The trading system is a **dual-bot architecture** consisting of:

1. **Equity Trading Bot** - NSE Equity segment trading
2. **Options Trading Bot** - NSE Derivatives (F&O) segment trading

Both bots share a **unified webhook router** but operate as **completely independent systems** with:
- Separate entry/exit logic
- Separate position management
- Separate capital allocation
- Separate ML models
- Separate logging and analytics

### Key Characteristics

| Aspect | Equity | Options |
|--------|--------|---------|
| **Exchange** | NSE (Equity) | NSE (F&O/Derivatives) |
| **Instruments** | Stocks (INFY, TCS, etc) | Options (CE/PE contracts) |
| **Capital** | ₹300,000 | ₹900,000 |
| **Max Positions** | 10 concurrent | 30 concurrent |
| **Position Duration** | Intraday (swing) | Intraday (5-min max) |
| **Trading Mode** | Paper Trading | Paper Trading |

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    WEBHOOK ROUTER (Port 8080)                      │
│         TradingView Alert → Unified Webhook Endpoint               │
└────────────────┬────────────────────────────┬──────────────────────┘
                 │                            │
        ┌────────▼────────┐         ┌────────▼─────────┐
        │  EQUITY BOT     │         │  OPTIONS BOT     │
        │  (Independent)  │         │  (Independent)   │
        └────────┬────────┘         └────────┬─────────┘
                 │                           │
        ┌────────▼────────────┐    ┌────────▼──────────────┐
        │  Equity Monitor     │    │ Options Monitor      │
        │  - Position tracker │    │ - Position tracker   │
        │  - P&L calculation  │    │ - Greeks monitoring  │
        │  - Exit logic       │    │ - IV-aware exits     │
        └────────┬────────────┘    └────────┬──────────────┘
                 │                           │
        ┌────────▼────────────┐    ┌────────▼──────────────┐
        │  Broker API         │    │ Broker API           │
        │  (AngelOne)         │    │ (AngelOne)           │
        └─────────────────────┘    └──────────────────────┘
                 │                           │
        ┌────────▼────────────────────────────────────────┐
        │  Shared Services                                │
        │  - ML Learning Engine                           │
        │  - Rate Limiting & Queue Management             │
        │  - Logging & Analytics                          │
        │  - Instrument Management                        │
        └─────────────────────────────────────────────────┘
```

---

## Core Components

### 1. Webhook Router (`webhook_router.py`)

**Purpose**: Single entry point for all TradingView alerts

**Responsibilities**:
- Parse incoming JSON alerts from TradingView
- Route to Equity or Options bot based on signal
- Validate alert structure
- Handle multiple webhook sources

**Key Features**:
```python
# Alert format:
{
    "symbol": "INFY",                # Stock or underlying
    "action": "BUY" | "SELL",        # Direction
    "price": 1234.50,                # Entry price
    "strike": 1000,                  # For options only
    "expiry": "2026-01-31",          # For options only
    "type": "CE" | "PE",             # For options only
    "confidence": 95.0,              # Signal strength
    ...
}
```

---

### 2. Equity Trading Bot (`equity/main.py`)

**Purpose**: Manage equity positions in NSE segment

**Core Classes**:
- `EquityTradingBot` - Main orchestrator
- `EquityPositionManager` - Position tracking
- `EquityPnLCalculator` - P&L management
- `EquityExitEngine` - Exit decision logic

**Position Lifecycle**:
```
ENTRY: Webhook → Validation → Broker Order → Position Created
       ↓
MONITOR: 5-sec cycle → LTP update → P&L calc → Exit check
       ↓
EXIT: P&L target | Stop loss | Time-based | Signal-based
       ↓
CLOSED: Log to ML → Update performance tracker → Free slot
```

**Capital Allocation**:
- Total: ₹300,000
- Per trade: ₹30,000 (10 slots max)
- Risk per trade: 1% (₹3,000 loss max)

---

### 3. Options Trading Bot (`options/main.py`)

**Purpose**: Manage derivatives positions in NSE F&O segment

**Core Classes**:
- `OptionsTradingBot` - Main orchestrator
- `OptionPositionMonitor` - Position tracking
- `OptionPosition` - Individual position state
- `ActiveSymbolPool` - Bulk data fetching
- `LTPBucketManager` - API call optimization
- `ExitDecisionEngine` - Multi-factor exit logic

**Position State**:
```python
class OptionPosition:
    # Identity
    symbol: str              # "BANKNIFTY27JAN261000CE"
    underlying: str          # "BANKNIFTY"
    strike_price: float      # 1000.0
    expiry: str             # "2026-01-27"
    
    # Entry
    entry_price: float      # Premium paid
    entry_greeks: Dict      # Delta/Gamma/Theta/Vega
    entry_iv: float         # IV at entry
    
    # Current
    current_premium: float  # LTP
    current_greeks: Dict    # Greeks from option chain
    
    # Tracking
    quantity: int           # Lot size
    unrealized_pnl: float   # Mark-to-market
    greeks_history: List    # Time series for trend detection
```

**Position Lifecycle**:
```
ENTRY: Webhook → Option chain fetch → Greeks check → Capital check
       → Broker MARKET order → Position created
       ↓
MONITOR: 5-sec cycle (bucketed LTP) → Greeks update → IV tracking
       ↓
EXIT: Greeks-based | P&L-based | Sentiment-based | Duration-based
       ↓
CLOSED: Log trade → Update ML → Free slot
```

**Capital Allocation**:
- Total: ₹900,000
- Per trade: ₹30,000 (30 slots max)
- Risk per trade: 1% (₹9,000 loss max)

---

### 4. Position Monitor (`optmonitor.py`)

**Purpose**: Real-time position monitoring and Greeks tracking

**Core Responsibilities**:
- Fetch current LTP for all positions
- Extract Greeks from option chain
- Calculate unrealized P&L
- Detect fake moves and unusual activity
- Track IV changes and volatility regime

**LTP Bucket Optimization**:
```
Problem: 30 positions × get_market_data = 30 API calls/cycle
Solution: Divide into buckets (5 per bucket) = 6 buckets

Cycle Pattern:
├─ Cycle 1: Check bucket 1 (symbols 1-5)     → 5 API calls
├─ Cycle 2: Check bucket 2 (symbols 6-10)    → 5 API calls
├─ Cycle 3: Check bucket 3 (symbols 11-15)   → 5 API calls
├─ Cycle 4: Check bucket 4 (symbols 16-20)   → 5 API calls
├─ Cycle 5: Check bucket 5 (symbols 21-25)   → 5 API calls
├─ Cycle 6: Check bucket 6 (symbols 26-30)   → 5 API calls
└─ Cycle 7: Back to bucket 1 (rotate)        → 5 API calls

Result: 30 positions fully updated every 6 cycles = better data freshness!
```

---

### 5. Exit Decision Engine

**Purpose**: Multi-factor exit logic combining Greeks, P&L, and sentiment

**Four Exit Monitors**:

#### 5.1 Greeks-Based Exit (Primary)
```
Strategy: Use Greeks as leading indicators of position reversal

A. Delta Reversal (Early exit signal)
   ├─ Monitor delta change per cycle
   ├─ If delta becomes less favorable for 2 consecutive cycles
   └─ Trigger exit → Reduce losses early

B. Gamma Explosion (Convexity risk)
   ├─ Monitor gamma absolute value
   ├─ If gamma > 0.04 (absolute cap) OR 1.5x from entry
   └─ Trigger exit → Avoid binary risk

C. Theta Acceleration (Decay risk)
   ├─ Monitor theta change vs entry
   ├─ If theta > 3x from entry AND (low P&L or weak delta)
   └─ Trigger exit → Avoid premium bleed

D. Vega Crush (IV shock)
   ├─ Monitor IV regime (low < 50, high ≥ 50)
   ├─ Dynamic thresholds: Low IV = 1.0x, High IV = 3.0x
   └─ Trigger exit → Avoid IV-driven losses
```

#### 5.2 P&L-Based Exit (Secondary)
```
Simple but effective:
├─ Profit Target: ₹2,000 → Exit with profit
├─ Stop Loss: ₹500 → Cut losses
└─ Max Duration: 5 minutes → Time decay protection
```

#### 5.3 Sentiment-Based Exit (Confirmation)
```
Market sentiment shifts:
├─ PCR Fade: Put-Call Ratio drops 40% → PE conviction weakening
├─ OI Fade: OI buildup reverses 40% → Buildup conviction weakening
└─ Fake Move: Price reversal sustained → Avoid chasing
```

#### 5.4 Risk Management
```
Portfolio-level controls:
├─ Net Delta monitoring (avoid directional bias)
├─ Portfolio Gamma check (convexity limits)
├─ Margin utilization (capital preservation)
└─ Max daily loss (drawdown control)
```

---

## Data Flow

### Entry Flow (Webhook → Position)

```
1. TradingView Alert Received
   └─ JSON: {symbol, action, price, ...}

2. Webhook Router (webhook_router.py)
   ├─ Parse alert
   ├─ Validate schema
   └─ Route to Options/Equity bot

3. Options Bot Entry Handler
   ├─ Check capital availability
   ├─ Check max slots (< 30)
   ├─ Check max daily trades (< 30)
   └─ Validate instrument exists

4. ML Signal Filtering (opt_ml_integration.py)
   ├─ Greeks quality check
   ├─ PoP (Probability of Profit) calculation
   ├─ Volatility regime validation
   └─ Quality score → Accept/Reject

5. Broker Order Execution
   ├─ Fetch current option chain
   ├─ Extract Greeks, IV
   ├─ Place MARKET order
   └─ Confirm execution

6. Position Created
   ├─ Store in positions.json
   ├─ Initialize greeks_history
   ├─ Set profit_target, stop_loss
   └─ Add to monitoring queue
```

### Monitoring Flow (5-second cycle)

```
Every 5 seconds:

1. Select next LTP bucket
   └─ Get 5-6 symbols for current cycle

2. Fetch Bulk Market Data
   └─ get_market_data() → LTP prices

3. For each position in bucket:
   ├─ Fetch option chain
   ├─ Extract Greeks (delta, gamma, theta, vega)
   ├─ Calculate IV from option chain
   ├─ Calculate unrealized P&L
   ├─ Update greeks_history
   └─ Check for fake moves

4. Check Exit Conditions
   ├─ Greeks triggers?
   ├─ P&L targets?
   ├─ Duration exceeded?
   ├─ Sentiment reversal?
   └─ Risk breaches?

5. Execute Exits (if triggered)
   ├─ Place exit order
   ├─ Record P&L
   ├─ Log to ML engine
   └─ Free position slot
```

### Exit Flow (Decision → Closed)

```
1. Exit Decision Made
   ├─ Exit reason: Greeks | P&L | Duration | Sentiment

2. Generate Exit Order
   ├─ Calculate quantity to square off
   ├─ Place MARKET order
   └─ Record exit price

3. Position Closed
   ├─ Realized P&L = (Exit - Entry) × Quantity
   ├─ Exit timestamp recorded
   └─ Status: CLOSED

4. Learning Update
   ├─ Record to ML engine
   ├─ Update symbol performance tracker
   ├─ Calculate feature importance
   └─ Improve future alert scoring

5. Analytics & Logging
   ├─ Log to events.jsonl
   ├─ Update positions.json
   ├─ Calculate daily stats
   └─ Alert monitoring system
```

---

## Module Organization

```
/root/santhosh/trading/
├── options/                          # Options trading bot
│   ├── main.py                      # Entry point
│   ├── optcode/                     # Core modules
│   │   ├── optmonitor.py            # Position monitoring
│   │   ├── optconfig.py             # Configuration
│   │   ├── angelone_options.py      # Broker API wrapper
│   │   ├── options_rate_limiter.py  # Rate limiting
│   │   ├── opt_ml_integration.py    # ML integration
│   │   ├── opt_ml_signal_filter.py  # Signal quality filter
│   │   ├── opt_hybrid_learning_engine.py  # ML training engine
│   │   ├── technical_analyzer.py    # Technical indicators
│   │   ├── entry_filter_engine.py   # Entry validation
│   │   ├── fake_move_detector.py    # Anomaly detection
│   │   └── trade_logger.py          # Trading analytics
│   ├── data/                        # Data persistence
│   │   ├── positions.json           # Open positions
│   │   ├── option_pnl_history.json # P&L tracking
│   │   └── learning/               # ML data
│   ├── logs/                        # Daily logs
│   │   ├── YYYY-MM-DD/
│   │   │   ├── app.log
│   │   │   ├── optbot.log
│   │   │   ├── events.jsonl
│   │   │   ├── alerts.jsonl
│   │   │   └── positions.jsonl
│   └── deployment/                 # Systemd services
│
├── equity/                          # Equity trading bot
│   ├── main.py                      # Entry point
│   ├── eqcode/                      # Core modules
│   │   ├── api.py                   # Position management
│   │   ├── hybrid_learning_engine.py # ML training
│   │   └── ...
│   └── data/                        # Data persistence
│
├── webhook_router.py                # Unified webhook endpoint
├── broker_health_monitor.py         # System health check
├── alert_system.py                  # Alert aggregation
├── ARCHITECTURE.md                  # This file
├── ML.md                           # ML design guide
└── RATE_LIMIT.md                   # Rate limiting guide
```

---

## Key Design Patterns

### 1. Independent Bot Pattern

**Problem**: Mixed equity and options trading in single bot causes:
- Logic conflicts (equity vs options exits)
- Capital conflicts
- API rate limit conflicts

**Solution**: Separate bots with shared webhook

```python
# webhook_router.py routes to appropriate bot
if signal.is_equity_symbol():
    equity_bot.handle_entry(signal)
elif signal.is_options_signal():
    options_bot.handle_entry(signal)
```

**Benefits**:
- ✅ Clear separation of concerns
- ✅ Independent optimization
- ✅ Easier debugging
- ✅ Shared ML models work across both

---

### 2. Token Bucket Rate Limiting

**Problem**: AngelOne API has strict limits:
- 8 requests/second
- 180 requests/minute
- Violations = account suspension

**Solution**: Token bucket with request queue

```python
# Token bucket with 8 tokens per second refill
bucket = TokenBucket(capacity=8, refill_rate=8.0)

# On every API call
if bucket.consume(1):
    # Call API
    result = api.call()
else:
    # Queue for retry
    request_queue.add_request(api.call, args, kwargs)
```

**Benefits**:
- ✅ Guaranteed rate limit compliance
- ✅ Automatic queuing during peaks
- ✅ Exponential backoff for retries
- ✅ Thread-safe across bot

---

### 3. Bucketed LTP Updates

**Problem**: 30 positions × get_market_data = 30 API calls per 5-sec cycle = 360 calls/minute
- Exceeds rate limits
- Expensive computationally
- Stale data for later positions

**Solution**: Rotate through buckets

```
Positions: P1, P2, ..., P30
Buckets (size=5): [P1-P5], [P6-P10], [P11-P15], [P16-P20], [P21-P25], [P26-P30]

Cycle 1: Update bucket 1 (5 API calls)
Cycle 2: Update bucket 2 (5 API calls)
...
Cycle 6: Update bucket 6 (5 API calls)
Cycle 7: Update bucket 1 (rotate)

Result: 30 API calls/6 cycles = 5 API calls per cycle ✅
```

---

### 4. Greeks History Tracking

**Problem**: Greeks change constantly, need trend detection for exit signals

**Solution**: Maintain time series of Greeks samples

```python
position.greeks_history = [
    {
        'timestamp': 2026-01-02 10:00:00,
        'delta': 0.45,
        'gamma': 0.02,
        'theta': -0.15,
        'vega': 0.08
    },
    {
        'timestamp': 2026-01-02 10:00:05,
        'delta': 0.42,        # Delta decreasing = reversal?
        'gamma': 0.022,       # Gamma increasing = risk?
        'theta': -0.18,       # Theta accelerating = decay?
        'vega': 0.075
    },
    ...
]

# Check for delta reversal trend
delta_trend = [s['delta'] for s in position.greeks_history[-2:]]
if is_unfavorable_trend(delta_trend):
    exit_with_reason("Delta reversal detected")
```

---

### 5. Position Persistence

**Problem**: Bot crash = lose all position state

**Solution**: Persist to JSON files

```python
# positions.json - Open positions
{
    "INFY27JAN261640CE": {
        "symbol": "INFY27JAN261640CE",
        "entry_price": 40.25,
        "quantity": 745,
        "entry_datetime": "2026-01-02T09:53:39",
        ...
    }
}

# On bot restart
positions = load_from_json()
for symbol, pos in positions.items():
    resume_monitoring(pos)
```

---

## Configuration Management

All configuration centralized in `optconfig.py`:

```python
class OptionsCapitalConfig:
    MAX_CAPITAL = 900_000           # Total capital
    CAP_PER_TRADE = 30_000          # Per position
    MAX_SLOTS = 30                  # Concurrent positions
    MAX_TRADES_PER_DAY = 30

class OptionsTradingConfig:
    PROFIT_TARGET = 2000            # Exit with ₹2000 profit
    STOP_LOSS = 500                 # Exit with ₹500 loss
    MAX_DURATION_MINUTES = 5        # 5-min max hold
    
class GreeksConfig:
    # Delta Reversal
    DELTA_REVERSAL_THRESHOLD = -0.05
    DELTA_REVERSAL_CONFIRM_CYCLES = 2
    
    # Gamma Explosion
    GAMMA_MULTIPLIER_THRESHOLD = 1.5
    GAMMA_ABSOLUTE_CAP = 0.04
    
    # Theta Acceleration
    THETA_MULTIPLIER_THRESHOLD = 3.0
    
    # Vega Crush (Dynamic)
    VEGA_LOW_IV_THRESHOLD = 1.0
    VEGA_HIGH_IV_THRESHOLD = 3.0
    VEGA_IV_REGIME_BOUNDARY = 50.0
    
class MLConfig:
    ENABLE_ML_FILTERING = True
    GREEK_QUALITY_THRESHOLD = 0.7
    POP_THRESHOLD = 40.0            # 40% minimum
    MIN_IV_PERCENTILE = 20.0
```

---

## Error Handling & Recovery

### Error Categories

| Error Type | Detection | Recovery |
|-----------|-----------|----------|
| **API Timeout** | Watchdog timer | Retry with backoff |
| **Rate Limited** | HTTP 429 | Queue for retry |
| **Invalid Token** | Login failure | Re-authenticate |
| **Position Lock** | Double booking | Acquire exclusive lock |
| **Stale Position** | Zombie detection | Close manually |
| **Capital Exceeded** | Validation fail | Reject new entry |

### Recovery Mechanisms

```python
# 1. Automatic Retry
def api_call_with_retry(func, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func()
        except RateLimitError:
            wait_time = 2 ** attempt  # Exponential backoff
            time.sleep(wait_time)
    raise Exception("Max retries exceeded")

# 2. Request Queuing
def handle_rate_limit():
    request_queue.add_request(
        request_type="order_placement",
        callback=api.place_order,
        args=(symbol, quantity)
    )
    # Process queue when rate limit clears

# 3. Position Lock Recovery
def refresh_position_ltps():
    with position_lock:  # Exclusive access
        for position in positions:
            update_ltp(position)
            
# 4. EOD Cleanup
def eod_cleanup():
    # Close stale positions
    for position in positions:
        if position.age > MAX_AGE:
            close_position(position)
    
    # Sync state to JSON
    persist_positions()
```

---

## Performance Characteristics

### API Call Budget

**Per 5-second cycle**:
- LTP updates: 5 calls (1 bucket of 5 positions)
- Option chains: 5 calls (extracted along with LTP)
- Total: ~10 API calls/cycle = 2 calls/second

**Per minute** (12 cycles):
- ~120 API calls = 2 calls/second avg

**Rate limit**: 8 calls/second max
- **Usage**: 25% of limit ✅

---

### Monitoring Cycle

```
Cycle Time: 5 seconds

Activities per cycle:
├─ LTP bucket fetch: 1-2 sec
├─ Option chains: 2-3 sec
├─ Greeks calculations: 0.5 sec
├─ P&L updates: 0.2 sec
└─ Exit checks: 0.3 sec

Total: ~4-5 seconds ✅ (finishes before next cycle)
```

---

### Memory Usage

Typical for 30 concurrent positions:
- Position objects: ~15 MB
- Greeks history (100 samples each): ~5 MB
- Caches (option chains, LTP): ~10 MB
- **Total**: ~30 MB ✅

---

## Next Steps

1. **Configure `optconfig.py`** with your capital/risk limits
2. **Test entry filters** in paper mode
3. **Monitor Greeks tracking** for accuracy
4. **Tune exit thresholds** based on backtesting
5. **Review ML improvements** daily
6. **Scale gradually** to live trading if comfortable

---

**Questions?** See ML.md for learning details or RATE_LIMIT.md for rate limiting architecture.
