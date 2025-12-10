"""
IMPLEMENTATION GUIDE - Actually Wire Candle Integration into Your Trading Flow

This file shows the EXACT code changes needed in api.py and monitor.py
"""

# =============================================================================
# STEP 1: MODIFY handle_buy_alert() in api.py
# =============================================================================

"""
LOCATION IN FILE: /root/santhosh/trading/equity/eqcode/api.py
FUNCTION: handle_buy_alert()

Find the section around line 1400-1430 that looks like:

    # Pre-allocate capital before order placement
    trading_state.allocate_capital(symbol, required_capital)
    
    order = trading_state.broker.place_order_safe(
        symbol=symbol,
        action="BUY",
        quantity=quantity,
        price=0  # Market order
    )

REPLACE WITH:

"""

# ===== EXACT CODE TO INSERT =====

def handle_buy_alert_WITH_CANDLE_CONFIRMATION(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Modified handle_buy_alert with candle confirmation
    """
    symbol = alert["symbol"]
    price = alert["price"]
    
    try:
        from .bot_logging import log_trade_execution
        
        log_trade_execution("ALERT_RECEIVED", symbol, "BUY", 
                          price=price, 
                          confidence=alert.get("confidence"),
                          score=alert.get("score"))
        
        # [EXISTING CHECKS OMITTED FOR BREVITY - Capital, slots, position, etc.]
        # ... keep all existing validation code ...
        
        # Calculate quantity
        quantity = 1  # or calculate based on capital
        required_capital = quantity * price
        
        # ===== NEW: CANDLE CONFIRMATION =====
        from .candle_integration import EntryConfirmationEngine, DynamicStopLossEngine
        
        try:
            confirmation_engine = EntryConfirmationEngine(
                broker_api=trading_state.broker,
                smart_api=trading_state.smart_api,
                min_confidence=0.75  # Adjust this threshold
            )
            
            # Get token for symbol (IMPORTANT: This is hardcoded example)
            # In production, use a symbol->token mapping
            SYMBOL_TOKEN_MAP = {
                "RELIANCE": "3045",
                "SBIN": "4119",
                "INFY": "4963",
                "TCS": "3789",
                "HDFC": "1333",
                # Add more as needed
            }
            
            token = SYMBOL_TOKEN_MAP.get(symbol)
            if not token:
                log_event("TOKEN_LOOKUP_FAILED", f"No token found for {symbol}",
                         symbol=symbol)
                token = None
            
            # Confirm with candles
            if token:
                confirmed, reason, confidence = confirmation_engine.confirm_buy_signal(
                    symbol=symbol,
                    exchange="NSE",
                    token=token
                )
                
                if not confirmed:
                    log_trade_execution("ENTRY_REJECTED_CANDLE", symbol, "BUY",
                                      reason=reason,
                                      confidence=confidence,
                                      price=price)
                    
                    # Release capital and log missed opportunity
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
                
                log_event("ENTRY_CONFIRMED_CANDLE", 
                         f"BUY signal confirmed by candles",
                         symbol=symbol, confidence=confidence)
            else:
                log_event("CANDLE_CONFIRMATION_SKIPPED", 
                         f"No token mapping for {symbol}, skipping candle check")
            
            # ===== NEW: DYNAMIC STOP LOSS =====
            sl_engine = DynamicStopLossEngine(trading_state.broker)
            
            try:
                sl_price, sl_reason = sl_engine.calculate_stop_loss(
                    symbol=symbol,
                    exchange="NSE",
                    token=token,
                    entry_price=price,
                    multiplier=2.0  # 2x ATR
                )
                log_event("DYNAMIC_SL_CALCULATED", 
                         f"SL calculated for {symbol}",
                         sl_price=sl_price, reason=sl_reason)
            except Exception as e:
                sl_price = price * 0.98
                log_event("DYNAMIC_SL_ERROR",
                         f"Fallback to 2% SL: {str(e)}")
        
        except Exception as e:
            log_event("CANDLE_INTEGRATION_ERROR",
                     f"Candle check error for {symbol}: {str(e)}")
            # Continue with order placement (fail-open)
            sl_price = price * 0.98
        
        # ===== PLACE ORDER (existing code) =====
        trading_state.allocate_capital(symbol, required_capital)
        
        order = trading_state.broker.place_order_safe(
            symbol=symbol,
            action="BUY",
            quantity=quantity,
            price=0  # Market order
        )
        
        if not order or not order.order_id:
            trading_state.release_capital(symbol, required_capital)
            log_trade_execution("ORDER_PLACEMENT_FAILED", symbol, "BUY",
                              reason="Failed to place BUY order")
            return {
                "status": "failed",
                "reason": "Failed to place BUY order",
                "symbol": symbol
            }
        
        # ===== PLACE STOP LOSS (with dynamic SL) =====
        try:
            sl_order = trading_state.broker.place_stop_loss_order(
                symbol=symbol,
                quantity=quantity,
                stop_loss_price=sl_price  # Use calculated SL
            )
            
            if sl_order:
                log_event("STOP_LOSS_PLACED",
                         f"SL placed at {sl_price:.2f}",
                         symbol=symbol)
            else:
                log_event("STOP_LOSS_PLACEMENT_FAILED",
                         f"Failed to place SL for {symbol}")
        except Exception as e:
            log_event("STOP_LOSS_ERROR",
                     f"Error placing SL: {str(e)}")
        
        # ===== LOG EXECUTION =====
        log_trade_execution("EXECUTION_COMPLETE", symbol, "BUY",
                          order_id=order.order_id,
                          entry_price=price,
                          quantity=quantity,
                          sl_price=sl_price,
                          candle_confirmed=True)
        
        return {
            "status": "success",
            "message": f"BUY order placed for {symbol}",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "order_id": order.order_id,
            "sl_price": sl_price
        }
    
    except Exception as e:
        log_error("HANDLE_BUY_ALERT", "Unexpected error in BUY handler", e)
        return {
            "status": "error",
            "message": str(e),
            "symbol": symbol
        }


# =============================================================================
# STEP 2: MODIFY monitor.py - Add smart exit detection
# =============================================================================

"""
LOCATION IN FILE: /root/santhosh/trading/equity/eqcode/monitor.py
FUNCTION: _check_exit_conditions() or equivalent monitoring loop

Find the section that checks for profit/loss exits, around line 1200-1300:

    # Check for exit conditions
    pnl_percent = ((current_ltp - position['entry_price']) / position['entry_price']) * 100
    
    if pnl_percent >= 5.0:
        # Take profit
        self.place_sell_order(symbol)
    elif pnl_percent <= -2.0:
        # Stop loss
        self.place_sell_order(symbol)

ADD THIS BEFORE THE HARDCODED CHECKS:

"""

def _check_exit_conditions_WITH_SMART_EXIT(self, symbol, position, current_ltp):
    """
    Check if position should exit (with smart exit detection)
    """
    
    # ===== NEW: SMART EXIT DETECTION =====
    from .candle_integration import SmartExitEngine
    
    try:
        exit_engine = SmartExitEngine(self.broker)
        
        # Get token for symbol
        SYMBOL_TOKEN_MAP = {
            "RELIANCE": "3045",
            "SBIN": "4119",
            "INFY": "4963",
            "TCS": "3789",
            "HDFC": "1333",
        }
        token = SYMBOL_TOKEN_MAP.get(symbol)
        
        if token:
            should_exit, exit_reason, exit_strength = exit_engine.should_exit_position(
                symbol=symbol,
                exchange="NSE",
                token=token,
                entry_price=position['entry_price'],
                current_price=current_ltp
            )
            
            if should_exit:
                log_monitor("SMART_EXIT_DETECTED",
                           symbol=symbol,
                           current_price=current_ltp,
                           decision="SELL",
                           details={
                               "reason": exit_reason,
                               "strength": exit_strength,
                               "entry_price": position['entry_price']
                           })
                
                # Place sell order
                sell_order = self.broker.place_order_safe(
                    symbol=symbol,
                    action="SELL",
                    quantity=position['quantity'],
                    price=0
                )
                
                if sell_order and sell_order.order_id:
                    log_event("POSITION_CLOSED_SMART_EXIT",
                             f"Smart exit executed: {exit_reason}",
                             symbol=symbol,
                             exit_reason=exit_reason)
                    return True  # Position exited
    
    except Exception as e:
        log_event("SMART_EXIT_ERROR",
                 f"Error in smart exit check for {symbol}: {str(e)}")
        # Fall through to hardcoded checks
    
    # ===== FALLBACK: HARDCODED EXITS (if no smart exit signal) =====
    pnl_percent = ((current_ltp - position['entry_price']) / position['entry_price']) * 100
    
    # Take profit at 5%
    if pnl_percent >= 5.0:
        log_monitor("TAKE_PROFIT_TRIGGERED",
                   symbol=symbol,
                   pnl_percent=pnl_percent,
                   decision="SELL")
        
        sell_order = self.broker.place_order_safe(
            symbol=symbol,
            action="SELL",
            quantity=position['quantity'],
            price=0
        )
        
        if sell_order and sell_order.order_id:
            log_event("POSITION_CLOSED_TAKE_PROFIT",
                     f"Take profit at {pnl_percent:.2f}%",
                     symbol=symbol)
            return True  # Position exited
    
    # Stop loss at 2%
    if pnl_percent <= -2.0:
        log_monitor("STOP_LOSS_TRIGGERED",
                   symbol=symbol,
                   pnl_percent=pnl_percent,
                   decision="SELL")
        
        sell_order = self.broker.place_order_safe(
            symbol=symbol,
            action="SELL",
            quantity=position['quantity'],
            price=0
        )
        
        if sell_order and sell_order.order_id:
            log_event("POSITION_CLOSED_STOP_LOSS",
                     f"Stop loss at {pnl_percent:.2f}%",
                     symbol=symbol)
            return True  # Position exited
    
    # No exit condition met
    return False


# =============================================================================
# STEP 3: CREATE SYMBOL->TOKEN MAPPING
# =============================================================================

"""
For the candle integration to work, you need a mapping of symbols to tokens.

Option A: Hardcoded mapping (simple)
"""

SYMBOL_TOKEN_MAP = {
    # NSE Equity - Common symbols
    "RELIANCE": "3045",
    "SBIN": "4119",
    "INFY": "4963",
    "TCS": "3789",
    "HDFC": "1333",
    "ICICIBANK": "5920",
    "WIPRO": "7229",
    "AXIS": "3456",
    "BAJAJFINSV": "5087",
    "JSWSTEEL": "5980",
    "MARUTI": "7718",
    "M&M": "7701",
    "BAJAJ-AUTO": "5040",
    "HCLTECH": "5010",
    "ITC": "4419",
    "BHARTIARTL": "4957",
    "ADANIPORTS": "12344",
    "NTPC": "3054",
    "POWERGRID": "4117",
    "LT": "4693",
}

"""
Option B: Load from config file (better)

Create /root/santhosh/trading/equity/eqcode/symbol_tokens.json:

{
    "RELIANCE": "3045",
    "SBIN": "4119",
    ...
}

Then load:

import json
from pathlib import Path

def load_symbol_token_map():
    file = Path(__file__).parent / "symbol_tokens.json"
    with open(file) as f:
        return json.load(f)

SYMBOL_TOKEN_MAP = load_symbol_token_map()
"""

# =============================================================================
# STEP 4: TEST WITH PAPER TRADING FIRST
# =============================================================================

"""
Before running with real capital:

1. Set environment variable:
   export TRADING_MODE=PAPER

2. Run the bot as usual

3. Watch logs for:
   ✅ "ENTRY_CONFIRMED_CANDLE" - Signal confirmed by candles
   ❌ "ENTRY_REJECTED_CANDLE" - Signal rejected by candles
   🔴 "SMART_EXIT_DETECTED" - Exit signal detected
   📊 "DYNAMIC_SL_CALCULATED" - SL calculated

4. Verify:
   - Candle fetching is working (check logs)
   - Indicators are calculating (check logs)
   - Exits are detected (watch for smart exit logs)

5. Adjust thresholds if needed:
   - Entry confirmation: min_confidence=0.75
   - Exit detection: Look at individual signal weights
   - Dynamic SL: multiplier=2.0 (try 1.5 or 2.5)
"""

# =============================================================================
# STEP 5: PRODUCTION ROLLOUT PLAN
# =============================================================================

"""
Phase 1: Entry Confirmation Only (Week 1)
- Add candle confirmation to handle_buy_alert()
- Keep existing exit logic
- Monitor rejection rate (should be 20-30%)

Phase 2: Smart Exits (Week 2)
- Add smart exit detection to monitor
- Run alongside hardcoded exits
- Log both for comparison

Phase 3: Full Integration (Week 3+)
- Remove hardcoded exits if smart exits outperform
- Fine-tune thresholds based on actual performance
- Monitor PnL improvement

Phase 4: Dynamic SL (Ongoing)
- Start with 2x ATR (medium)
- Adjust to 1.5x or 2.5x based on results
- Track win rate and average profit
"""
