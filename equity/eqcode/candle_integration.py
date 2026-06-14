"""
Candle Integration Module - Equity Trading Bot

Integrates CandleBot with webhook (entry) and monitor (exit) systems.
Provides drop-in functions to enhance trading decisions with technical analysis.

Three Integration Points:
1. Entry Confirmation: Validate BUY signals with candle analysis
2. Exit Detection: Smart exits based on technical indicators
3. Dynamic Stop Loss: ATR-based stop loss instead of fixed %
"""

import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import logging

# Import candle system
from .candle_fetcher import CandleFetcher
from .indicators import IndicatorEngine
from .candle_bot import CandleBot, Signal

# Create logger
logger = logging.getLogger(__name__)


# =============================================================================
# PART 1: ENTRY CONFIRMATION
# =============================================================================

class EntryConfirmationEngine:
    """
    Validates BUY signals with candle analysis before order placement.
    Reduces fake-out trades from TradingView alerts.
    """
    
    def __init__(self, broker_api, smart_api, min_confidence: float = 0.75):
        """
        Initialize entry confirmation engine
        
        Args:
            broker_api: AngelOneBroker instance
            smart_api: SmartAPI instance
            min_confidence: Minimum signal confidence (0.0 to 1.0)
        """
        self.candle_fetcher = CandleFetcher(broker_api)
        self.candle_bot = CandleBot(broker_api, smart_api)
        self.min_confidence = min_confidence
        self.last_check = {}
        self.cache_ttl = 30  # seconds
    
    def confirm_buy_signal(self, symbol: str, exchange: str, token: str) -> Tuple[bool, str, float]:
        """
        Confirm BUY signal with candle analysis
        
        Args:
            symbol: Trading symbol (e.g., "RELIANCE")
            exchange: Exchange code (NSE for equity)
            token: Angel One token
            
        Returns:
            Tuple of (confirmed: bool, reason: str, confidence: float)
            
        Example:
            confirmed, reason, confidence = engine.confirm_buy_signal("RELIANCE", "NSE", "3045")
            if confirmed:
                place_order(symbol, quantity)
            else:
                log_event(f"Entry rejected: {reason}")
        """
        try:
            # Get latest candle analysis
            signal = self.candle_bot.analyze_symbol(exchange, token, symbol)
            
            if not signal:
                return False, "Failed to analyze candles", 0.0
            
            confidence = signal.confidence
            reason = signal.reason
            
            # Check if confidence meets threshold
            if confidence >= self.min_confidence:
                logger.info(f"✅ Entry confirmed for {symbol} (confidence: {confidence:.0%})")
                return True, f"Candle confirmation passed ({confidence:.0%})", confidence
            else:
                logger.warning(f"❌ Entry rejected for {symbol} (confidence: {confidence:.0%})")
                return False, f"Low candle confidence ({confidence:.0%})", confidence
        
        except Exception as e:
            logger.error(f"Error confirming {symbol}: {str(e)}")
            return False, f"Confirmation check failed: {str(e)}", 0.0


# =============================================================================
# PART 2: EXIT DETECTION
# =============================================================================

class SmartExitEngine:
    """
    Detects exit signals using technical indicators.
    Replaces hardcoded profit/loss exits with intelligent decisions.
    
    Exit Signals:
    1. SuperTrend reversal (primary trend changed)
    2. ADX weakening (trend losing strength)
    3. Bollinger Band rejection (price rejected upper band)
    4. RSI extremes (overbought/oversold)
    5. MACD divergence (momentum loss)
    """
    
    def __init__(self, broker_api):
        """
        Initialize smart exit engine
        
        Args:
            broker_api: AngelOneBroker instance
        """
        self.candle_fetcher = CandleFetcher(broker_api)
        self.indicators = IndicatorEngine()
        self.exit_cache = {}
    
    def should_exit_position(self, symbol: str, exchange: str, token: str,
                           entry_price: float, current_price: float) -> Tuple[bool, str, float]:
        """
        Determine if position should be exited based on technical analysis
        
        Args:
            symbol: Trading symbol
            exchange: Exchange code
            token: Angel One token
            entry_price: Entry price of the position
            current_price: Current LTP
            
        Returns:
            Tuple of (should_exit: bool, reason: str, exit_signal_strength: 0.0-1.0)
            
        Example:
            should_exit, reason, strength = engine.should_exit_position("RELIANCE", "NSE", "3045", 2800, 2850)
            if should_exit:
                sell_order(symbol)
                log_event(f"Exit: {reason} (strength: {strength:.0%})")
        """
        try:
            # Fetch latest candles (100 candles = ~1.67 hours in 1-min timeframe)
            candles = self.candle_fetcher.fetch_latest_candles(
                exchange=exchange, 
                token=token, 
                symbol=symbol,
                interval="ONE_MINUTE",
                limit=100
            )
            
            if not candles or len(candles) < 20:
                return False, "Insufficient candle data", 0.0
            
            # Compute all indicators
            df = self.indicators.compute_all_indicators(candles)
            
            exit_signals = []
            exit_strength = 0.0
            
            # Check Signal 1: SuperTrend reversal (PRIMARY)
            if 'supertrend_signal' in df.iloc[-1]:
                current_signal = df.iloc[-1]['supertrend_signal']
                if current_signal == 'SELL':
                    exit_signals.append("SuperTrend reversal")
                    exit_strength += 0.3  # 30% weight
            
            # Check Signal 2: ADX weakening (MOMENTUM LOSS)
            if 'adx' in df.iloc[-1]:
                adx = df.iloc[-1]['adx']
                if adx < 20:
                    exit_signals.append(f"ADX weak ({adx:.1f})")
                    exit_strength += 0.2  # 20% weight
            
            # Check Signal 3: Bollinger Band rejection (OVERBOUGHT)
            if 'bb_upper' in df.iloc[-1]:
                bb_upper = df.iloc[-1]['bb_upper']
                bb_lower = df.iloc[-1]['bb_lower']
                bb_width = bb_upper - bb_lower
                
                # Price near upper band = potential reversal
                distance_to_upper = (bb_upper - current_price) / bb_width if bb_width > 0 else 0
                if distance_to_upper < 0.1 and distance_to_upper >= 0:  # Within 10% of upper band
                    exit_signals.append("At Bollinger upper band")
                    exit_strength += 0.15  # 15% weight
            
            # Check Signal 4: RSI extremes (OVERBOUGHT)
            if 'rsi' in df.iloc[-1]:
                rsi = df.iloc[-1]['rsi']
                if rsi > 75:
                    exit_signals.append(f"RSI overbought ({rsi:.1f})")
                    exit_strength += 0.2  # 20% weight
            
            # Check Signal 5: MACD divergence (MOMENTUM LOSS)
            if 'macd_histogram' in df.iloc[-1]:
                macd_hist = df.iloc[-1]['macd_histogram']
                prev_macd_hist = df.iloc[-2]['macd_histogram'] if len(df) > 1 else macd_hist
                
                if macd_hist < prev_macd_hist and macd_hist < 0:
                    exit_signals.append("MACD histogram negative")
                    exit_strength += 0.15  # 15% weight
            
            # Normalize strength to 0-1
            exit_strength = min(exit_strength, 1.0)
            
            # Decision: Exit if multiple signals (strength > 0.5) or SuperTrend reversal
            should_exit = (exit_strength > 0.5) or "SuperTrend reversal" in exit_signals
            
            if should_exit:
                reason = " + ".join(exit_signals) if exit_signals else "Unknown signal"
                logger.info(f"🔴 Exit signal for {symbol}: {reason} (strength: {exit_strength:.0%})")
                return True, reason, exit_strength
            else:
                logger.debug(f"✅ Hold {symbol} (strength: {exit_strength:.0%})")
                return False, "No exit signal", exit_strength
        
        except Exception as e:
            logger.error(f"Error checking exit for {symbol}: {str(e)}")
            return False, f"Exit check failed: {str(e)}", 0.0


# =============================================================================
# PART 3: DYNAMIC STOP LOSS
# =============================================================================

class DynamicStopLossEngine:
    """
    Calculates dynamic stop losses based on volatility (ATR) instead of fixed %.
    Adapts to market conditions automatically.
    
    Logic:
    - Low volatility: Tight SL (1x ATR)
    - Medium volatility: Medium SL (2x ATR)
    - High volatility: Loose SL (3x ATR)
    """
    
    def __init__(self, broker_api):
        """
        Initialize dynamic SL engine
        
        Args:
            broker_api: AngelOneBroker instance
        """
        self.candle_fetcher = CandleFetcher(broker_api)
        self.indicators = IndicatorEngine()
    
    def calculate_stop_loss(self, symbol: str, exchange: str, token: str,
                          entry_price: float, multiplier: float = 2.0) -> Tuple[float, str]:
        """
        Calculate dynamic stop loss based on ATR
        
        Args:
            symbol: Trading symbol
            exchange: Exchange code
            token: Angel One token
            entry_price: Entry price
            multiplier: ATR multiplier (1=tight, 2=medium, 3=loose)
            
        Returns:
            Tuple of (stop_loss_price: float, reason: str)
            
        Example:
            sl_price, reason = engine.calculate_stop_loss("RELIANCE", "NSE", "3045", 2800, multiplier=2.0)
            # sl_price = 2800 - (2 * ATR14)
            place_stop_loss_order(symbol, sl_price)
        """
        try:
            # Fetch latest candles
            candles = self.candle_fetcher.fetch_latest_candles(
                exchange=exchange,
                token=token,
                symbol=symbol,
                interval="ONE_MINUTE",
                limit=50
            )
            
            if not candles or len(candles) < 14:
                # Fallback to fixed 2% if insufficient data
                fallback_sl = entry_price * 0.98
                logger.warning(f"Insufficient candle data for {symbol}, using fallback SL (2%)")
                return fallback_sl, "Fallback: 2% fixed SL"
            
            # Calculate ATR
            df = self.indicators.compute_all_indicators(candles)
            atr = df.iloc[-1].get('atr', None)
            
            if atr is None or atr == 0:
                fallback_sl = entry_price * 0.98
                return fallback_sl, "Fallback: 2% fixed SL"
            
            # Calculate dynamic SL
            sl_price = entry_price - (multiplier * atr)
            
            # Ensure SL is below entry
            if sl_price >= entry_price:
                sl_price = entry_price * 0.98
                reason = "SL adjusted to 2% (ATR calculation issue)"
            else:
                sl_percent = ((entry_price - sl_price) / entry_price) * 100
                atr_percent = (atr / entry_price) * 100
                
                if multiplier == 1.0:
                    volatility = "LOW"
                elif multiplier == 3.0:
                    volatility = "HIGH"
                else:
                    volatility = "MEDIUM"
                
                reason = f"ATR {volatility} ({atr_percent:.2f}%), SL {sl_percent:.2f}%"
            
            logger.info(f"SL for {symbol}: {sl_price:.2f} ({reason})")
            return sl_price, reason
        
        except Exception as e:
            logger.error(f"Error calculating SL for {symbol}: {str(e)}")
            # Fallback to 2%
            fallback_sl = entry_price * 0.98
            return fallback_sl, "Error in ATR calculation, using 2% SL"
    
    def is_stop_loss_hit(self, current_price: float, stop_loss_price: float) -> bool:
        """Check if stop loss has been hit"""
        return current_price <= stop_loss_price
    
    def adjust_stop_loss(self, symbol: str, exchange: str, token: str,
                        entry_price: float, current_price: float,
                        trailing_multiplier: float = 1.5) -> Tuple[float, bool]:
        """
        Adjust stop loss upward as price moves up (trailing stop loss)
        
        Args:
            symbol: Trading symbol
            exchange: Exchange code
            token: Angel One token
            entry_price: Entry price
            current_price: Current price
            trailing_multiplier: ATR multiplier for trailing (lower = tighter)
            
        Returns:
            Tuple of (new_stop_loss: float, was_adjusted: bool)
        """
        try:
            # Only adjust if price is up
            if current_price <= entry_price:
                return entry_price * 0.98, False
            
            # Calculate new SL using trailing multiplier
            new_sl, _ = self.calculate_stop_loss(
                symbol, exchange, token, current_price,
                multiplier=trailing_multiplier
            )
            
            return new_sl, True
        
        except Exception as e:
            logger.error(f"Error adjusting SL for {symbol}: {str(e)}")
            return entry_price * 0.98, False


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def validate_before_buy(alert: Dict[str, Any], broker_api, smart_api) -> Tuple[bool, str, float]:
    """
    Quick validation before placing BUY order
    
    Args:
        alert: Webhook alert dictionary with keys: symbol, exchange, token
        broker_api: AngelOneBroker instance
        smart_api: SmartAPI instance
        
    Returns:
        Tuple of (is_valid, reason, confidence)
    """
    try:
        engine = EntryConfirmationEngine(broker_api, smart_api, min_confidence=0.75)
        return engine.confirm_buy_signal(
            symbol=alert.get("symbol"),
            exchange=alert.get("exchange", "NSE"),
            token=alert.get("token")
        )
    except Exception as e:
        return False, str(e), 0.0


def check_exit_logic(symbol: str, exchange: str, token: str,
                    entry_price: float, current_price: float,
                    broker_api) -> Tuple[bool, str]:
    """
    Quick exit check during monitoring
    
    Args:
        symbol: Trading symbol
        exchange: Exchange code
        token: Angel One token
        entry_price: Entry price
        current_price: Current LTP
        broker_api: AngelOneBroker instance
        
    Returns:
        Tuple of (should_exit, reason)
    """
    try:
        engine = SmartExitEngine(broker_api)
        should_exit, reason, _ = engine.should_exit_position(
            symbol, exchange, token, entry_price, current_price
        )
        return should_exit, reason
    except Exception as e:
        return False, str(e)


def get_smart_stop_loss(symbol: str, exchange: str, token: str,
                       entry_price: float, broker_api) -> float:
    """
    Quick dynamic SL calculation
    
    Args:
        symbol: Trading symbol
        exchange: Exchange code
        token: Angel One token
        entry_price: Entry price
        broker_api: AngelOneBroker instance
        
    Returns:
        Stop loss price
    """
    try:
        engine = DynamicStopLossEngine(broker_api)
        sl_price, _ = engine.calculate_stop_loss(
            symbol, exchange, token, entry_price, multiplier=2.0
        )
        return sl_price
    except Exception as e:
        logger.error(f"Error calculating SL: {str(e)}")
        return entry_price * 0.98
