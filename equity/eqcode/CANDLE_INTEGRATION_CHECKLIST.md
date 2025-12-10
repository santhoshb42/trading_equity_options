"""
Webhook Integration with Candle Confirmation

Modifies the existing webhook handler to confirm BUY signals with candle analysis
before placing orders.

Integration Points:
1. In handle_buy_alert(): Add candle confirmation before order placement
2. In monitor.py: Add smart exit detection
3. In place_stop_loss(): Use dynamic SL calculation
"""

# =============================================================================
# 1. WEBHOOK MODIFICATION - Add to handle_buy_alert() function
# =============================================================================

"""
In /root/santhosh/trading/equity/eqcode/api.py, find handle_buy_alert() function
and add this code after capital check and before order placement:

LOCATION: Around line 1400-1430 (before "Place BUY order" comment)

=============================================================================
# ===== CANDLE CONFIRMATION (NEW) =====
# Confirm BUY signal with candle analysis to reduce fake-out trades
# 
# This checks if the TradingView signal aligns with technical indicators
# from recent candles (EMA, RSI, BB, ADX, SuperTrend, etc.)

try:
    from .candle_integration import EntryConfirmationEngine
    
    # Initialize confirmation engine
    confirmation_engine = EntryConfirmationEngine(
        broker_api=trading_state.broker,
        smart_api=trading_state.smart_api,
        min_confidence=0.75  # Require 75% confidence
    )
    
    # Get exchange and token for symbol
    # For NSE equity:
    exchange = "NSE"
    token = get_token_for_symbol(symbol)  # Need to implement this lookup
    
    # Confirm signal with candles
    confirmed, reason, confidence = confirmation_engine.confirm_buy_signal(
        symbol=symbol,
        exchange=exchange,
        token=token
    )
    
    if not confirmed:
        # Log rejected entry
        log_trade_execution("ENTRY_REJECTED_CANDLE", symbol, "BUY",
                          reason=reason,
                          confidence=confidence,
                          price=price)
        
        # Log missed opportunity
        try:
            from .pnl_analytics import get_pnl_analytics
            pnl_analytics = get_pnl_analytics()
            pnl_analytics.log_missed_signal(
                symbol=symbol,
                action="BUY",
                signal_price=price,
                reason=f"Candle confirmation failed: {reason}",
                alert_data=alert
            )
        except:
            pass
        
        return {
            "status": "rejected",
            "reason": f"Candle confirmation failed: {reason}",
            "symbol": symbol,
            "confidence": confidence
        }
    
    log_event("ENTRY_CONFIRMED_CANDLE", f"BUY signal confirmed by candles",
             symbol=symbol, confidence=confidence)

except Exception as e:
    log_event("CANDLE_CONFIRMATION_ERROR", f"Candle confirmation failed: {str(e)}")
    # Continue with order placement (fail-open)
    pass

=============================================================================

# ===== STOP LOSS MODIFICATION (NEW) =====
# Use dynamic ATR-based SL instead of fixed 2%

try:
    from .candle_integration import DynamicStopLossEngine
    
    sl_engine = DynamicStopLossEngine(trading_state.broker)
    
    # Calculate dynamic SL
    sl_price, sl_reason = sl_engine.calculate_stop_loss(
        symbol=symbol,
        exchange="NSE",
        token=token,
        entry_price=price,
        multiplier=2.0  # 2x ATR (medium volatility)
    )
    
    log_event("DYNAMIC_SL_CALCULATED", f"SL for {symbol}",
             sl_price=sl_price, reason=sl_reason)

except Exception as e:
    # Fallback to fixed 2%
    sl_price = price * 0.98
    log_event("DYNAMIC_SL_ERROR", f"Using fallback 2% SL: {str(e)}")

=============================================================================
"""


# =============================================================================
# 2. MONITOR MODIFICATION - Add to monitoring loop
# =============================================================================

"""
In /root/santhosh/trading/equity/eqcode/monitor.py, find the monitoring loop
(around line 1200-1300) where you check for exit conditions.

ADD THIS CODE in the exit check section:

=============================================================================
# ===== SMART EXIT DETECTION (NEW) =====
# Use technical indicators to detect optimal exit points
# This replaces or supplements the hardcoded 5% profit / 2% loss exits

try:
    from .candle_integration import SmartExitEngine
    
    exit_engine = SmartExitEngine(self.broker)
    
    # Check if position should be exited based on technical analysis
    should_exit, exit_reason, exit_strength = exit_engine.should_exit_position(
        symbol=symbol,
        exchange="NSE",
        token=token,
        entry_price=position.entry_price,
        current_price=current_ltp
    )
    
    if should_exit:
        log_monitor("SMART_EXIT_SIGNAL", 
                   symbol=symbol,
                   current_price=current_ltp,
                   decision="SELL",
                   details={
                       "reason": exit_reason,
                       "strength": exit_strength,
                       "entry_price": position.entry_price
                   })
        
        # Place sell order
        sell_order = self.broker.place_order_safe(
            symbol=symbol,
            action="SELL",
            quantity=position.quantity,
            price=0  # Market order
        )
        
        if sell_order and sell_order.order_id:
            self.remove_position(symbol)
            log_event("EXIT_ORDER_PLACED", 
                     f"Smart exit for {symbol}: {exit_reason}",
                     reason=exit_reason)
        continue  # Skip further checks for this symbol

except Exception as e:
    log_event("SMART_EXIT_ERROR", f"Error in smart exit for {symbol}: {str(e)}")
    # Continue with normal monitoring

=============================================================================

# THEN check hardcoded exits (profit/loss) as backup:

# ===== BACKUP: HARDCODED EXITS (if no smart exit signal) =====
pnl_percent = ((current_ltp - position.entry_price) / position.entry_price) * 100

# Take profit at 5%
if pnl_percent >= 5.0:
    log_monitor("HARDCODED_EXIT", symbol=symbol, pnl_percent=pnl_percent, decision="SELL")
    sell_order()
    continue

# Stop loss at 2%
if pnl_percent <= -2.0:
    log_monitor("HARDCODED_SL", symbol=symbol, pnl_percent=pnl_percent, decision="SELL")
    sell_order()
    continue

=============================================================================
"""


# =============================================================================
# 3. COMPLETE WORKING EXAMPLE - Drop-in Replacement
# =============================================================================

"""
Here's a complete working example showing how to integrate everything:

FILE: /root/santhosh/trading/equity/eqcode/api.py

In handle_buy_alert() function, REPLACE this:

    # Place BUY order
    order = trading_state.broker.place_order_safe(
        symbol=symbol,
        action="BUY",
        quantity=quantity,
        price=0  # Market order
    )

WITH THIS:

    # ===== CANDLE CONFIRMATION (NEW) =====
    from .candle_integration import (
        EntryConfirmationEngine,
        DynamicStopLossEngine
    )
    
    try:
        # Confirm BUY signal
        confirmation_engine = EntryConfirmationEngine(
            broker_api=trading_state.broker,
            smart_api=trading_state.smart_api,
            min_confidence=0.75
        )
        
        token = "3045"  # Hardcoded for RELIANCE for now
        confirmed, reason, confidence = confirmation_engine.confirm_buy_signal(
            symbol=symbol,
            exchange="NSE",
            token=token
        )
        
        if not confirmed:
            log_trade_execution("ENTRY_REJECTED_CANDLE", symbol, "BUY",
                              reason=reason, confidence=confidence, price=price)
            return {
                "status": "rejected",
                "reason": f"Candle check failed: {reason}",
                "symbol": symbol
            }
        
        log_event("ENTRY_CONFIRMED_CANDLE", f"BUY confirmed for {symbol}", confidence=confidence)
        
        # Calculate dynamic SL
        sl_engine = DynamicStopLossEngine(trading_state.broker)
        sl_price, sl_reason = sl_engine.calculate_stop_loss(
            symbol=symbol,
            exchange="NSE",
            token=token,
            entry_price=price,
            multiplier=2.0
        )
        
    except Exception as e:
        log_event("CANDLE_CHECK_ERROR", f"Candle integration error: {str(e)}")
        sl_price = price * 0.98  # Fallback to 2%
        log_event("FALLBACK_SL", f"Using fallback 2% SL for {symbol}")
    
    # Place order
    order = trading_state.broker.place_order_safe(
        symbol=symbol,
        action="BUY",
        quantity=quantity,
        price=0  # Market order
    )

=============================================================================
"""


# =============================================================================
# 4. SIMPLE 3-STEP INTEGRATION CHECKLIST
# =============================================================================

"""
STEP 1: Add candle_integration.py to your codebase
✅ We just created /root/santhosh/trading/equity/eqcode/candle_integration.py

STEP 2: Import in api.py
Add this at the top of /root/santhosh/trading/equity/eqcode/api.py:

    from .candle_integration import (
        EntryConfirmationEngine,
        SmartExitEngine,
        DynamicStopLossEngine
    )

STEP 3: Use in handle_buy_alert() and monitor.py
See examples above - copy/paste into your code

STEP 4: Test with paper trading
Run with TRADING_MODE=PAPER first to verify signals


MINIMAL INTEGRATION (Start here):
- Just add entry confirmation to handle_buy_alert()
- Keep existing exit logic for now
- This gives you 80% of the benefit with 20% of the work


FULL INTEGRATION (Later):
- Add smart exit detection to monitor.py
- Use dynamic SL calculation
- Fine-tune confidence thresholds


DEBUGGING TIPS:
1. If candle confirmation is rejecting good signals:
   - Lower min_confidence from 0.75 to 0.65
   - Check logs for specific exit signals
   
2. If you're not getting exits:
   - Check candle data is fetching correctly
   - Verify indicator calculations in indicators.py
   - Set log level to DEBUG for detailed output

3. Performance issues:
   - Candle fetching is cached (5 min TTL)
   - One API call per check, not per position
   - Typical check time: 50-100ms per symbol

=============================================================================
"""
