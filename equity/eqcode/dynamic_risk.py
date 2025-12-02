"""
Dynamic Risk Management Module
Calculates volatility-based stop-loss and position sizing

Features:
1. ATR (Average True Range) calculation for volatility
2. Dynamic stop-loss placement based on ATR
3. Dynamic position sizing based on volatility
4. Dynamic target calculation for consistent risk/reward
"""

import logging
import math

try:
    from .bot_logging import log_event
except Exception:
    def log_event(*args, **kwargs):
        pass

logger = logging.getLogger(__name__)


class DynamicRiskManager:
    """Manages dynamic risk parameters based on market volatility"""
    
    def __init__(self, 
                 atr_period=14,
                 high_volatility_threshold=3.0,
                 low_volatility_threshold=1.0,
                 risk_reward_ratio=1.5):
        """
        Initialize risk manager
        
        Args:
            atr_period: Period for ATR calculation (default 14)
            high_volatility_threshold: ATR % above which is considered high (default 3%)
            low_volatility_threshold: ATR % below which is considered low (default 1%)
            risk_reward_ratio: Target risk/reward ratio (default 1.5)
        """
        self.atr_period = atr_period
        self.high_volatility_threshold = high_volatility_threshold
        self.low_volatility_threshold = low_volatility_threshold
        self.risk_reward_ratio = risk_reward_ratio
        
        # Price history for ATR calculation
        self.price_history = {}  # symbol -> list of (high, low, close)
        
        logger.info(f"DynamicRiskManager initialized: atr_period={atr_period}, "
                   f"high_vol_threshold={high_volatility_threshold}%, "
                   f"low_vol_threshold={low_volatility_threshold}%")
    
    def calculate_atr(self, symbol, current_high, current_low, current_close):
        """
        Calculate ATR for a symbol
        
        Args:
            symbol: str - Trading symbol
            current_high: float - Current bar high
            current_low: float - Current bar low
            current_close: float - Previous bar close (for TR calc)
            
        Returns:
            float - ATR percentage of current price
        """
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        
        history = self.price_history[symbol]
        
        # Calculate True Range for current bar
        tr = max(
            current_high - current_low,
            abs(current_high - current_close) if history else 0,
            abs(current_low - current_close) if history else 0
        )
        
        history.append((current_high, current_low, current_close, tr))
        
        # Keep only last atr_period bars
        if len(history) > self.atr_period:
            history.pop(0)
        
        # Calculate ATR
        if len(history) < self.atr_period:
            # Not enough data yet, use simple average of TR
            atr = sum(bar[3] for bar in history) / len(history) if history else 0
        else:
            # SMA of TR
            atr = sum(bar[3] for bar in history[-self.atr_period:]) / self.atr_period
        
        atr_percent = (atr / current_high) * 100 if current_high > 0 else 0
        return atr_percent
    
    def get_volatility_regime(self, atr_percent):
        """
        Classify volatility regime
        
        Args:
            atr_percent: float - ATR as percentage
            
        Returns:
            str - 'HIGH', 'MEDIUM', or 'LOW'
        """
        if atr_percent >= self.high_volatility_threshold:
            return 'HIGH'
        elif atr_percent <= self.low_volatility_threshold:
            return 'LOW'
        else:
            return 'MEDIUM'
    
    def calculate_dynamic_stop_loss(self, entry_price, atr_percent, action='BUY'):
        """
        Calculate stop-loss price based on ATR
        
        Args:
            entry_price: float - Entry price
            atr_percent: float - ATR as percentage
            action: str - 'BUY' or 'SELL'
            
        Returns:
            float - Stop-loss price
        """
        # Calculate SL based on ATR multiplier
        if atr_percent >= self.high_volatility_threshold:
            # High volatility: wider stop (ATR × 1.5)
            sl_distance_percent = atr_percent * 1.5
        elif atr_percent <= self.low_volatility_threshold:
            # Low volatility: tighter stop (ATR × 2.5)
            sl_distance_percent = atr_percent * 2.5
        else:
            # Medium volatility: standard stop (ATR × 2.0)
            sl_distance_percent = atr_percent * 2.0
        
        if action.upper() == 'BUY':
            # For BUY: SL is below entry
            sl_price = entry_price * (1 - sl_distance_percent / 100)
        else:
            # For SELL: SL is above entry
            sl_price = entry_price * (1 + sl_distance_percent / 100)
        
        return sl_price
    
    def calculate_target_price(self, entry_price, sl_price, action='BUY'):
        """
        Calculate target price for consistent risk/reward
        
        Args:
            entry_price: float - Entry price
            sl_price: float - Stop-loss price
            action: str - 'BUY' or 'SELL'
            
        Returns:
            float - Target price
        """
        risk_amount = abs(entry_price - sl_price)
        target_amount = risk_amount * self.risk_reward_ratio
        
        if action.upper() == 'BUY':
            # For BUY: target is above entry
            target_price = entry_price + target_amount
        else:
            # For SELL: target is below entry
            target_price = entry_price - target_amount
        
        return target_price
    
    def get_position_size_multiplier(self, atr_percent):
        """
        Get position size multiplier based on volatility
        
        Args:
            atr_percent: float - ATR as percentage
            
        Returns:
            float - Position size multiplier (0.7 to 1.2)
        """
        if atr_percent >= self.high_volatility_threshold:
            # High volatility: reduce position size to 70%
            multiplier = 0.7
        elif atr_percent <= self.low_volatility_threshold:
            # Low volatility: increase position size to 120%
            multiplier = 1.2
        else:
            # Medium volatility: full size 100%
            multiplier = 1.0
        
        return multiplier
    
    def get_risk_parameters(self, symbol, current_high, current_low, current_close, entry_price, action='BUY'):
        """
        Get complete risk parameters for a trade
        
        Args:
            symbol: str - Trading symbol
            current_high: float - Current bar high
            current_low: float - Current bar low
            current_close: float - Current bar close
            entry_price: float - Trade entry price
            action: str - 'BUY' or 'SELL'
            
        Returns:
            dict - Complete risk parameters
        """
        atr_percent = self.calculate_atr(symbol, current_high, current_low, current_close)
        volatility = self.get_volatility_regime(atr_percent)
        sl_price = self.calculate_dynamic_stop_loss(entry_price, atr_percent, action)
        target_price = self.calculate_target_price(entry_price, sl_price, action)
        position_multiplier = self.get_position_size_multiplier(atr_percent)
        
        risk_amount = abs(entry_price - sl_price)
        reward_amount = abs(target_price - entry_price)
        risk_reward_ratio = reward_amount / risk_amount if risk_amount > 0 else 0
        
        return {
            'symbol': symbol,
            'atr_percent': round(atr_percent, 2),
            'volatility_regime': volatility,
            'entry_price': round(entry_price, 2),
            'sl_price': round(sl_price, 2),
            'target_price': round(target_price, 2),
            'risk_amount': round(risk_amount, 2),
            'reward_amount': round(reward_amount, 2),
            'risk_reward_ratio': round(risk_reward_ratio, 2),
            'position_size_multiplier': round(position_multiplier, 2),
            'action': action
        }
    
    def clear_history(self, symbol=None):
        """Clear price history for a symbol or all symbols"""
        if symbol:
            if symbol in self.price_history:
                del self.price_history[symbol]
        else:
            self.price_history.clear()


# Global instance
risk_manager = DynamicRiskManager()


def calculate_dynamic_stop_loss(entry_price, atr_percent, action='BUY'):
    """
    Calculate dynamic stop-loss price
    
    Args:
        entry_price: float - Entry price
        atr_percent: float - ATR as percentage
        action: str - 'BUY' or 'SELL'
        
    Returns:
        float - Stop-loss price
    """
    return risk_manager.calculate_dynamic_stop_loss(entry_price, atr_percent, action)


def calculate_atr(symbol, current_high, current_low, current_close):
    """Calculate ATR for symbol"""
    return risk_manager.calculate_atr(symbol, current_high, current_low, current_close)


def get_risk_parameters(symbol, current_high, current_low, current_close, entry_price, action='BUY'):
    """Get complete risk parameters"""
    return risk_manager.get_risk_parameters(symbol, current_high, current_low, current_close, entry_price, action)


def get_position_size_multiplier(atr_percent):
    """Get position size multiplier based on ATR"""
    return risk_manager.get_position_size_multiplier(atr_percent)


def get_volatility_regime(atr_percent):
    """Get volatility regime classification"""
    return risk_manager.get_volatility_regime(atr_percent)


def clear_price_history(symbol=None):
    """Clear price history"""
    risk_manager.clear_history(symbol)
