# 📋 **Candle Bot Quick Reference**

## 🚀 One-Minute Setup

```python
from equity.eqcode.candle_bot import CandleBot

# Create bot
bot = CandleBot(broker_api, smart_api, candle_interval="FIVE_MINUTE")

# Scan symbols
signals = bot.scan_symbols([
    ("NSE", "3045", "RELIANCE"),
    ("NFO", "46294", "BANKNIFTY 30Dec 45500CE"),
], min_confidence=0.6)

# Execute
for signal in signals:
    if signal.confidence >= 0.75:
        bot.execute_signal(signal, quantity=1)
```

---

## 📊 **Indicators at a Glance**

| Indicator | Method | What It Does | Usage |
|-----------|--------|--------------|-------|
| **EMA** | `ema(series, 20)` | Trend following | Entry direction |
| **RSI** | `rsi(close, 14)` | Momentum (0-100) | Overbought/oversold |
| **MACD** | `macd(close, 12, 26, 9)` | Trend + momentum | Signal crossovers |
| **ATR** | `atr(df, 14)` | Volatility | Stop loss distance |
| **BB** | `bollinger_bands(close, 20)` | Range breakouts | Entry/exit levels |
| **ADX** | `adx(df, 14)` | Trend strength | Confirm trend exists |
| **Stoch** | `stochastic(df, 14)` | Momentum (0-100) | Overbought/oversold |
| **OBV** | `obv(df)` | Volume trend | Volume confirmation |
| **Keltner** | `keltner_channel(df, 20)` | ATR-based bands | Dynamic support/resistance |
| **SuperTrend** | `supertrend(df, 10, 3.0)` | Trend + stops | Entry/exit signals |

---

## 🎯 **Signal Levels**

```
Confidence < 30%  →  NEUTRAL      (skip)
Confidence 30-60% →  BUY/SELL     (wait for confirmation)
Confidence 60-75% →  BUY/SELL     (valid entry)
Confidence > 75%  →  STRONG_BUY/SELL (execute immediately)
```

---

## 📈 **Complete Example**

```python
from equity.eqcode.candle_bot import CandleBot
import logging

logging.basicConfig(level=logging.INFO)

# 1. Initialize
bot = CandleBot(broker, smart_api, candle_interval="FIVE_MINUTE")

# 2. Single symbol analysis
signal = bot.analyze_symbol("NSE", "3045", "RELIANCE")
print(signal)
# Output: BUY RELIANCE @ ₹2905.0 (Confidence: 72%)

# 3. Detailed breakdown
print(f"  Reasons: {', '.join(signal.reasons)}")
print(f"  Indicators: {signal.indicators_snapshot}")

# 4. Get analysis
analysis = bot.get_symbol_analysis("NSE", "3045", "RELIANCE")
print(analysis['price'])  # OHLCV
print(analysis['indicators'])  # All indicators
print(analysis['trend'])  # Trend direction

# 5. Multi-symbol scan
watchlist = [
    ("NSE", "3045", "RELIANCE"),
    ("NSE", "881", "INFY"),
    ("NSE", "4963", "ICICIBANK"),
]
signals = bot.scan_symbols(watchlist, min_confidence=0.6)

# 6. Portfolio summary
portfolio = bot.get_portfolio_signals(watchlist)
print(f"Buy signals: {len(portfolio['buy_signals'])}")
print(f"Sell signals: {len(portfolio['sell_signals'])}")
print(f"Strong buys: {len(portfolio['strong_buys'])}")
```

---

## 🔄 **Integration Points**

### With Your Webhook
```python
def on_webhook(alert):
    symbol, token, exchange = alert['symbol'], alert['token'], alert['exchange']
    
    # Confirm with candles
    signal = bot.analyze_symbol(exchange, token, symbol)
    if signal.confidence >= 0.7:
        place_order(symbol)
```

### With Your Monitor
```python
def check_exit(position):
    df = bot.get_latest_candles(position.exchange, position.token, 50)
    latest = df.iloc[-1]
    
    # Exit if SuperTrend turns down
    if latest['SuperTrend_Trend'] == -1:
        return True
```

### For Real-time Monitoring
```python
import time
while True:
    signals = bot.scan_symbols(watchlist)
    for signal in signals:
        if signal.confidence >= 0.8:
            bot.execute_signal(signal)
    time.sleep(300)  # Every 5 min
```

---

## 🧪 **Test Before Trading**

```python
# 1. Check single candle fetch
df = bot.get_latest_candles("NSE", "3045", num_candles=50)
print(df[['timestamp', 'close', 'volume']])

# 2. Verify indicators
print(df[['EMA20', 'RSI', 'ADX', 'ATR']])

# 3. Test signal generation
signal = bot.analyze_symbol("NSE", "3045", "RELIANCE")
print(f"Signal: {signal.signal.value}, Confidence: {signal.confidence}")

# 4. Check execution (paper trading first!)
bot.execute_signal(signal, quantity=1)
```

---

## ⚡ **Performance Tips**

1. **Cache hits:** Same request within 5 minutes uses cache (no API call)
2. **Bulk scanning:** Process high-priority symbols every 5 min, low-priority every 15 min
3. **Interval choice:** 5-min candles are best for day trading (balance speed + signal quality)
4. **Rate limits:** Batch up to 50 symbols per API call (already handled by fetcher)

---

## 🛡️ **Error Handling**

```python
try:
    signal = bot.analyze_symbol(exchange, token, symbol)
    if signal:
        bot.execute_signal(signal)
except Exception as e:
    print(f"Error: {e}")
    # Continue with next symbol
```

---

## 📊 **What Each Signal Reason Means**

| Reason | What It Means | Action |
|--------|--------------|--------|
| "EMA20 crossed above EMA50" | Trend turning up | BUY bias |
| "RSI oversold" | Price too low | Reversal expected |
| "Price broke above Bollinger Band" | Strong breakout | Follow breakout |
| "Strong trend (ADX: 35)" | Trend is powerful | Trust the trend |
| "High volume confirmation" | Big buyers/sellers | Signal is real |
| "SuperTrend confirms uptrend" | Multiple indicators agree | High confidence |

---

## 🎯 **Customization**

### Change signal strategy:
Edit `candle_bot.py` → `analyze_symbol()` method

### Change indicators:
Edit `candle_bot.py` → `compute_all_indicators()` config

### Change candle interval:
```python
bot = CandleBot(broker, smart_api, candle_interval="ONE_MINUTE")  # or FIFTEEN_MINUTE, etc.
```

### Change lookback period:
```python
bot = CandleBot(broker, smart_api, lookback_candles=200)  # more candles = more history
```

---

## ✅ **Checklist Before First Trade**

- [ ] Test authentication with Angel One
- [ ] Verify candle data fetching works
- [ ] Check indicator calculations
- [ ] Backtest signal generation
- [ ] Paper trade with 1-unit quantity
- [ ] Monitor first live signals
- [ ] Verify order execution
- [ ] Check exit logic
- [ ] Monitor rate limits (should be <10% utilization)

---

## 📞 **Troubleshooting**

| Problem | Solution |
|---------|----------|
| No candles returned | Check exchange/token validity, market hours |
| Indicator is NaN | Need more historical candles (increase lookback) |
| Signal not generating | Check min_confidence threshold |
| Order not executing | Check order_type (MARKET vs LIMIT), quantity |
| Rate limit hit | Already handled with fallback, check logs |

---

## 🚀 **Next Steps**

1. ✅ Test with single symbol
2. ✅ Expand to watchlist of 5-10 symbols
3. ✅ Monitor for 1-2 days
4. ✅ Adjust confidence threshold if needed
5. ✅ Add more custom indicators if desired
6. ✅ Scale to larger watchlist (50+ symbols)

---

**That's it! Your candle-based trading bot is ready to go.** 🎯
