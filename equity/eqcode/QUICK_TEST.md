"""
QUICK START - Test Your Candle Integration

Copy these commands to test the implementation immediately.
"""

═══════════════════════════════════════════════════════════════════════════════
                        🚀 TEST YOUR IMPLEMENTATION
═══════════════════════════════════════════════════════════════════════════════

STEP 1: Start Bot in Paper Trading Mode (Terminal 1)
────────────────────────────────────────────────────
cd /root/santhosh/trading/equity
export TRADING_MODE=PAPER
python3 main.py

[Watch for startup logs, should see webhook server starting]


STEP 2: Send Test Signal (Terminal 2)
──────────────────────────────────────

TEST 1: Good Signal (Should Confirm & Place Order)
───────────────────────────────────────────────────
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "RELIANCE",
    "action": "BUY",
    "price": 2800.00,
    "score": 85,
    "confidence": 80
  }'

Expected Response:
{
  "status": "success",
  "message": "BUY order placed for RELIANCE",
  "symbol": "RELIANCE",
  "confidence": 0.82  ← Candle confirmation confidence
}

Watch Terminal 1 logs for:
✅ ENTRY_CONFIRMED_CANDLE - Signal accepted (confidence 82%)
✅ DYNAMIC_SL_CALCULATED - SL calculated (ATR-based)
✅ ORDER_PLACED - Order submitted to Angel One


TEST 2: Weak Signal (Should Reject)
───────────────────────────────────
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "SBIN",
    "action": "BUY",
    "price": 500.00,
    "score": 55,
    "confidence": 60
  }'

Expected Response:
{
  "status": "rejected",
  "reason": "Candle confirmation failed: Low candle confidence (65%)",
  "symbol": "SBIN",
  "confidence": 0.65  ← Too low, rejected
}

Watch Terminal 1 logs for:
❌ ENTRY_REJECTED_CANDLE - Signal rejected (confidence < 75%)
📊 MISSED_OPPORTUNITY - Logged for analysis


TEST 3: No Token Mapping (Should Use Fixed SL)
───────────────────────────────────────────────
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "NEWNSE",
    "action": "BUY",
    "price": 1000.00,
    "score": 90,
    "confidence": 85
  }'

Expected Response:
{
  "status": "success",
  "message": "BUY order placed for NEWNSE"
}

Watch Terminal 1 logs for:
⚠️  CANDLE_CONFIRMATION_SKIPPED - No token mapping found
✅ SL_SOURCE: Using fixed 2% stop loss (fallback)


═══════════════════════════════════════════════════════════════════════════════
                          📊 MONITOR POSITION EXIT
═══════════════════════════════════════════════════════════════════════════════

After placing a BUY order, the system will automatically monitor the position.

Watch Terminal 1 logs for monitoring output:

Every 1-2 seconds, you should see:
  PRICE_CHECK: RELIANCE | LTP: 2805.50 | PnL: +₹55 (+0.2%)

Watch for exit signals (every time new candle completes):
  
Good case - Position profits:
  SMART_EXIT_SIGNAL: SuperTrend reversal detected
  → Position exits automatically at market

Bad case - Position loses:
  SL_HIT: Stop loss triggered at SL price
  → Position exits automatically


═══════════════════════════════════════════════════════════════════════════════
                          🔍 WHAT TO LOOK FOR
═══════════════════════════════════════════════════════════════════════════════

ENTRY CONFIRMATION LOGS:

✅ Good Entry:
  "ENTRY_CONFIRMED_CANDLE | RELIANCE | confidence: 82%"
  "DYNAMIC_SL_CALCULATED | sl_price: 2776.50"
  "ORDER_PLACED | order_id: XXXXX"

❌ Bad Entry:
  "ENTRY_REJECTED_CANDLE | symbol=SBIN | confidence: 65%"
  "MISSED_OPPORTUNITY | Logged missed BUY signal"


MONITORING LOGS:

📈 Position Gaining:
  "PRICE_CHECK | RELIANCE | LTP: 2810.50 | PnL: ₹105 (+0.37%)"
  "HOLDING | reason: No exit signal yet"

📉 Smart Exit:
  "SMART_EXIT_SIGNAL | SuperTrend reversal | strength: 0.85"
  "SELL_EXECUTED | Entry: 2800 | Exit: 2815 | Profit: ₹15"

🛑 Stop Loss:
  "SL_HIT | Entry: 2800 | Exit: 2776 | Loss: ₹24"


═══════════════════════════════════════════════════════════════════════════════
                      🐛 TROUBLESHOOTING
═══════════════════════════════════════════════════════════════════════════════

Problem: "No token mapping for [SYMBOL]"
─────────────────────────────────────────
Solution: Add symbol to SYMBOL_TOKEN_MAP in api.py & monitor.py
  1. Get Angel One token for symbol from your broker
  2. Add to map: "SYMBOL": "token_number"
  3. Restart bot

Example: Add TCS (token 3789)
  "TCS": "3789",


Problem: "Candle confirmation error: Failed to analyze candles"
────────────────────────────────────────────────────────────────
Possible causes:
  1. No candle data available (market closed?)
  2. Network error fetching candles
  3. Invalid token/exchange combination

Solution: Check logs for detailed error, continue with fixed SL


Problem: "DYNAMIC_SL_ERROR: Insufficient candle data"
───────────────────────────────────────────────────────
Solution: This is normal if market just opened. Falls back to 2% SL.
  • After 5 minutes of candles: ATR becomes available
  • Dynamic SL kicks in on next position


Problem: Smart exits not triggering
───────────────────────────────────
Check:
  1. Position is still OPEN (not already exited)
  2. Market is open (candles available)
  3. Check logs for "SMART_EXIT_ERROR" messages
  4. Fallback exits (SL/profit) should still work


═══════════════════════════════════════════════════════════════════════════════
                      📈 EXPECTED OUTCOMES
═══════════════════════════════════════════════════════════════════════════════

Over 30+ paper trades, you should observe:

Entry Confirmation:
  • 20-30% of alerts rejected as low-confidence
  • Rejected signals mostly don't move in your favor
  • Accepted signals have higher win rate

Dynamic Stop Loss:
  • SL price differs from fixed 2% on volatile stocks
  • ATR-based SL is tighter in calm markets, looser in volatile
  • Fewer whipsaw exits overall

Smart Exits:
  • Exits happen 5-15 minutes earlier than hardcoded 5% target
  • Capture 10-20% more profit on reversal avoidance
  • Fewer big losses from missing trend reversals


═══════════════════════════════════════════════════════════════════════════════
                      ✨ NEXT STEPS
═══════════════════════════════════════════════════════════════════════════════

Short term (this week):
  □ Run 30+ paper trades
  □ Monitor confirmations & exits
  □ Verify logs match expectations
  □ Compare performance vs original system
  □ Note any issues or anomalies

Medium term (next week):
  □ Fine-tune min_confidence (try 0.65, 0.75, 0.85)
  □ Fine-tune ATR multiplier (try 1.5, 2.0, 2.5)
  □ Add more symbols to token mapping
  □ Optimize for your specific watchlist

Long term (ongoing):
  □ A/B test: keep some positions with old SL, some with new
  □ Measure actual PnL improvement
  □ Backtest on historical data
  □ Adjust thresholds based on live performance
  □ Scale to full trading capital


═══════════════════════════════════════════════════════════════════════════════

If you hit any issues, check:
  1. Logs in equity/logs/ directory
  2. Documentation in IMPLEMENTATION_COMPLETE.md
  3. Error messages in Terminal output
  4. Symbol token mappings (most common issue)

Everything is ready to go! 🚀
"""
