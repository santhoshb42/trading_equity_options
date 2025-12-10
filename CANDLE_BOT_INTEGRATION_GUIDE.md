# 🔥 **Complete Integration Guide — Candle Data + Indicators + Bot**

## 📋 What You Just Got

Three production-ready modules:

1. **`candle_fetcher.py`** - Fetch candles from Angel One (equity + options)
2. **`indicators.py`** - Compute 15+ technical indicators
3. **`candle_bot.py`** - Complete trading bot with signal generation + execution

---

## 🚀 **Quick Start**

### Step 1: Import the modules

```python
from equity.eqcode.candle_fetcher import CandleFetcher
from equity.eqcode.indicators import IndicatorEngine
from equity.eqcode.candle_bot import CandleBot, Signal
```

### Step 2: Initialize the bot

```python
# After authenticating with Angel One
from smartapi import SmartConnect

smart = SmartConnect(api_key="xxx")
smart.generateSession(client_id="xxx", password="xxx", totp="123456")

# Create bot
bot = CandleBot(
    broker_api=your_broker_instance,
    smart_api=smart,
    candle_interval="FIVE_MINUTE",
    lookback_candles=100
)
```

### Step 3: Scan and execute

```python
# Scan symbols
watchlist = [
    ("NSE", "3045", "RELIANCE"),
    ("NSE", "881", "INFY"),
    ("NFO", "46294", "BANKNIFTY 30-Dec 45500CE"),
]

signals = bot.scan_symbols(watchlist, min_confidence=0.6)

# Execute
for signal in signals:
    if signal.confidence > 0.75:
        bot.execute_signal(signal, quantity=1)
```

---

## 📊 **Module Breakdown**

### **1. CandleFetcher** — Get raw OHLCV data

```python
fetcher = CandleFetcher(smart_api, cache_ttl_seconds=300)

# Fetch candles
df = fetcher.fetch_candles(
    exchange="NSE",
    token="3045",
    interval="FIVE_MINUTE",
    from_date="2024-02-10 09:15",
    to_date="2024-02-10 15:30"
)

# Returns DataFrame:
#   timestamp  open  high   low  close  volume
# 0 2024-02-10 2900.0 2910.0 2895.0 2905.0 100000
# 1 2024-02-10 2905.0 2920.0 2900.0 2915.0 150000
```

**Methods:**

| Method | Purpose |
|--------|---------|
| `fetch_candles()` | Fetch candles for date range |
| `fetch_latest_candles()` | Quick fetch of recent N candles |
| `fetch_candles_bulk()` | Fetch for multiple symbols |
| `clear_cache()` | Clear cached data |
| `get_cache_stats()` | Cache performance metrics |

**Supports:**
- ✅ Equity (NSE)
- ✅ Options (NFO)
- ✅ Futures (NFO)
- ✅ Commodities (MCX)
- ✅ Currency (CDS)

---

### **2. IndicatorEngine** — Compute 15+ indicators

```python
from equity.eqcode.indicators import IndicatorEngine

# Get candles first
df = fetcher.fetch_latest_candles("NSE", "3045", "FIVE_MINUTE", 100)

# Compute individual indicators
df['EMA20'] = IndicatorEngine.ema(df['close'], 20)
df['RSI'] = IndicatorEngine.rsi(df['close'], 14)
df['ATR'] = IndicatorEngine.atr(df, 14)

# Or compute all at once
df = IndicatorEngine.compute_all_indicators(df)
```

**Available Indicators:**

#### Trend (Directional)
- **EMA** - Exponential Moving Average
- **SMA** - Simple Moving Average
- **WMA** - Weighted Moving Average

#### Momentum
- **RSI** - Relative Strength Index (0-100, <30 oversold, >70 overbought)
- **MACD** - Moving Average Convergence Divergence
- **Stochastic** - Stochastic Oscillator (0-100)

#### Volatility
- **ATR** - Average True Range (absolute volatility)
- **Bollinger Bands** - BB upper/middle/lower (3-band envelope)
- **Keltner Channel** - ATR-based bands

#### Trend Direction
- **ADX** - Average Directional Index (trend strength, 0-100, >25 is strong)
- **+DI / -DI** - Directional indicators
- **SuperTrend** - Trend follower + support/resistance

#### Volume
- **OBV** - On Balance Volume (cumulative)
- **ADL** - Accumulation/Distribution Line
- **CMF** - Chaikin Money Flow

---

### **3. CandleBot** — Complete trading system

```python
bot = CandleBot(broker, smart_api)

# Analyze a single symbol
signal = bot.analyze_symbol("NSE", "3045", "RELIANCE")
print(signal)
# Output: BUY RELIANCE @ ₹2905.0 (Confidence: 75%)

# Scan multiple symbols
signals = bot.scan_symbols([
    ("NSE", "3045", "RELIANCE"),
    ("NSE", "881", "INFY"),
], min_confidence=0.6)

# Execute high-confidence signals
for s in signals:
    if s.confidence >= 0.75:
        bot.execute_signal(s, quantity=1)

# Get detailed analysis
analysis = bot.get_symbol_analysis("NSE", "3045", "RELIANCE")

# Get portfolio signals
portfolio = bot.get_portfolio_signals(watchlist, min_confidence=0.6)
```

**Signal Levels:**
- `STRONG_BUY` - Confidence ≥ 75%, multiple confirmations
- `BUY` - Confidence 50-75%, entry valid
- `NEUTRAL` - No clear direction
- `SELL` - Exit signal
- `STRONG_SELL` - High-confidence exit

---

## 🧠 **How Signal Generation Works**

The `analyze_symbol()` method uses a **multi-factor strategy**:

### 1. **Trend** (EMA Crossover)
- BUY: EMA20 crosses above EMA50
- SELL: EMA20 crosses below EMA50
- Adds 20% confidence

### 2. **Momentum** (RSI)
- BUY: RSI < 30 (oversold)
- SELL: RSI > 70 (overbought)
- Adds 15% confidence

### 3. **Volatility Breakout** (Bollinger Bands)
- BUY: Price breaks above BB upper band
- SELL: Price breaks below BB lower band
- Adds 15% confidence

### 4. **Trend Strength** (ADX)
- ADX > 25: Strong trend (adds 10% confidence)
- ADX < 20: Weak trend (neutral signal)

### 5. **Volume Confirmation**
- Volume > 1.5x average: Adds 10% confidence

### 6. **Trend Confirmation** (SuperTrend)
- SuperTrend uptrend + BUY signal: Adds 15%
- SuperTrend downtrend + SELL signal: Adds 15%

**Total possible confidence: 100%**

---

## 📈 **Example: Real-time Monitoring**

```python
import time
from datetime import datetime

bot = CandleBot(broker, smart_api, candle_interval="ONE_MINUTE")

watchlist = [
    ("NSE", "3045", "RELIANCE"),
    ("NSE", "881", "INFY"),
    ("NFO", "46294", "BANKNIFTY 30Dec 45500CE"),
]

while True:
    # Scan every minute
    signals = bot.scan_symbols(watchlist, min_confidence=0.65)
    
    for signal in signals:
        print(f"{datetime.now()} → {signal}")
        
        if signal.confidence >= 0.8:
            # High confidence: execute immediately
            bot.execute_signal(signal, quantity=1)
        elif signal.confidence >= 0.6:
            # Medium confidence: log and wait for confirmation
            print(f"  Signal logged: {signal.reasons}")
    
    time.sleep(60)  # Check every minute
```

---

## 🔄 **Integration with Existing System**

### With your current `monitor.py`:

```python
# In monitor.py

from equity.eqcode.candle_bot import CandleBot

class PositionMonitor:
    def __init__(self, ...):
        # ... existing code ...
        
        # Add candle bot for entry confirmation
        self.candle_bot = CandleBot(
            self.broker,
            self.smart_api,
            candle_interval="FIVE_MINUTE"
        )
    
    def on_webhook(self, alert_data):
        """New webhook handler with candle confirmation"""
        
        symbol = alert_data['symbol']
        token = alert_data['token']
        exchange = alert_data['exchange']
        
        # Get candle signal
        candle_signal = self.candle_bot.analyze_symbol(
            exchange, token, symbol
        )
        
        # Only enter if candles confirm
        if candle_signal and candle_signal.confidence >= 0.7:
            self.place_order(symbol, quantity=1)
        else:
            print(f"Skipped {symbol}: Candle signal not confirmed")
```

### For exit management:

```python
def check_exit(self, position):
    """Check exit conditions with candle analysis"""
    
    # Get latest candles
    df = self.candle_bot.get_latest_candles(
        position.exchange,
        position.token,
        num_candles=50
    )
    
    if df is None:
        return False
    
    latest = df.iloc[-1]
    
    # Exit if price breaks SuperTrend (trend break)
    if position.side == "LONG" and latest['SuperTrend_Trend'] == -1:
        return True, "SuperTrend turned down"
    
    # Exit if ADX drops (trend weakening)
    if latest['ADX'] < 20:
        return True, "Trend weakening (ADX < 20)"
    
    # Exit if RSI reversal
    if position.side == "LONG" and latest['RSI'] > 70:
        return True, "RSI overbought"
    
    return False, ""
```

---

## ⚡ **Performance Tips**

### 1. **Caching**
The fetcher caches candles for 5 minutes by default:

```python
# Cache hit (same request within 5 min)
df1 = fetcher.fetch_latest_candles("NSE", "3045", "FIVE_MINUTE", 100)
df2 = fetcher.fetch_latest_candles("NSE", "3045", "FIVE_MINUTE", 100)
# df2 served from cache, no API call

# Clear cache if needed
fetcher.clear_cache()
```

### 2. **Bulk Scanning**
For scanning multiple symbols:

```python
# Sequential (slow, respects rate limits)
signals = bot.scan_symbols(watchlist_100_symbols)

# Better: Batch by confidence
# Scan high-priority symbols every 5 min
# Scan low-priority symbols every 15 min
```

### 3. **Interval Selection**
- **1-min candles:** Scalping, high-frequency
- **5-min candles:** Day trading (best balance)
- **15-min candles:** Intraday swings
- **1-hour candles:** Position trades
- **Daily candles:** Trend analysis

---

## 🛡️ **Error Handling**

The bot includes proper error handling:

```python
# Handles gracefully
signals = bot.scan_symbols(watchlist, min_confidence=0.6)

for signal in signals:
    try:
        bot.execute_signal(signal)
    except Exception as e:
        print(f"Error executing {signal}: {e}")
        # Continue with next signal
```

---

## 📊 **Monitoring & Debugging**

```python
# Get detailed analysis for a symbol
analysis = bot.get_symbol_analysis("NSE", "3045", "RELIANCE")
print(analysis)
# Output:
# {
#   'symbol': 'RELIANCE',
#   'price': {'open': 2900, 'high': 2920, ..., 'volume': 150000},
#   'indicators': {'EMA20': 2905, 'RSI': 65, 'ADX': 28, ...},
#   'support_resistance': {'bb_upper': 2925, 'bb_middle': 2905, ...},
#   'trend': {'ema_trend': 'UP', 'supertrend_trend': 'UP', ...}
# }

# Get cache performance
stats = bot.candle_fetcher.get_cache_stats()
print(f"Cached symbols: {stats['cached_keys']}")
print(f"Cache size: {stats['cache_size_mb']:.2f} MB")
```

---

## 🎯 **Complete End-to-End Example**

```python
from equity.eqcode.candle_bot import CandleBot
from smartapi import SmartConnect
import logging

logging.basicConfig(level=logging.INFO)

# 1. Authenticate with Angel One
smart = SmartConnect(api_key="YOUR_API_KEY")
smart.generateSession(client_id="YOUR_CLIENT_ID", password="YOUR_PASSWORD", totp="123456")

# 2. Create bot
bot = CandleBot(
    broker_api=your_broker_instance,
    smart_api=smart,
    candle_interval="FIVE_MINUTE",
    lookback_candles=100
)

# 3. Define watchlist (equity + options)
watchlist = [
    ("NSE", "3045", "RELIANCE"),
    ("NSE", "881", "INFY"),
    ("NSE", "4963", "ICICIBANK"),
    ("NFO", "46294", "BANKNIFTY 30-Dec 45500CE"),
    ("NFO", "46295", "BANKNIFTY 30-Dec 45600CE"),
]

# 4. Scan for signals
print("🔍 Scanning for trading signals...")
signals = bot.scan_symbols(watchlist, min_confidence=0.6)

print(f"\n📈 Found {len(signals)} signals:\n")
for signal in signals:
    print(f"  {signal}")
    print(f"    Reasons: {', '.join(signal.reasons)}")

# 5. Execute high-confidence signals
print("\n📋 Executing signals...")
for signal in signals:
    if signal.confidence >= 0.75:
        print(f"\n  Executing: {signal}")
        result = bot.execute_signal(signal, quantity=1)
        if result:
            print(f"    ✅ Order placed: {result}")

# 6. Get portfolio signals
portfolio = bot.get_portfolio_signals(watchlist, min_confidence=0.6)
print(f"\n📊 Portfolio Summary:")
print(f"  Symbols scanned: {portfolio['total_scanned']}")
print(f"  Buy signals: {len(portfolio['buy_signals'])}")
print(f"  Strong buys: {len(portfolio['strong_buys'])}")
print(f"  Sell signals: {len(portfolio['sell_signals'])}")
print(f"  Strong sells: {len(portfolio['strong_sells'])}")
```

---

## 🔧 **Customizing the Strategy**

To modify the signal generation logic, edit `candle_bot.py` method `analyze_symbol()`:

```python
def analyze_symbol(self, exchange, token, symbol):
    df = self.get_latest_candles(exchange, token)
    latest = df.iloc[-1]
    
    signal = Signal.NEUTRAL
    confidence = 0.0
    reasons = []
    
    # ADD YOUR OWN LOGIC HERE
    # Example: Only buy when RSI < 30 AND price above EMA50
    if latest['RSI'] < 30 and latest['close'] > latest['EMA50']:
        signal = Signal.BUY
        confidence = 0.8
        reasons.append("RSI < 30 and price > EMA50")
    
    # ... rest of method ...
```

---

## ✅ **Ready for Production**

✅ Works for equity + options + futures  
✅ 15+ indicators included  
✅ Proper error handling and retries  
✅ Caching for performance  
✅ Rate limit friendly  
✅ Integration with existing broker APIs  

**You can start using it immediately!**
