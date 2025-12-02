"""
Market Regime Detection Module
Analyzes Nifty50 trend and volatility to classify market conditions
Enables dynamic trading behavior based on market regime
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Safe imports with fallbacks
try:
    from eqcode.bot_logging import log_event
except ImportError:
    def log_event(event_type: str, message: str, **kwargs):
        print(f"[{event_type}] {message}")


class RegimeDetector:
    """
    Detects market regime based on trend and volatility
    5 Regimes: TRENDING_BULLISH, TRENDING_BEARISH, CHOPPY_LOW_VOL, CHOPPY_HIGH_VOL, CRISIS
    """
    
    # Regime classifications
    REGIMES = {
        'TRENDING_BULLISH': 'trending_bullish',
        'TRENDING_BEARISH': 'trending_bearish',
        'CHOPPY_LOW_VOL': 'choppy_low_vol',
        'CHOPPY_HIGH_VOL': 'choppy_high_vol',
        'CRISIS': 'crisis'
    }
    
    # Position size multipliers by regime
    # NOTE: Currently all set to 1.0 (full capital allocation) as per current trading strategy
    SIZE_MULTIPLIERS = {
        'trending_bullish': 1.0,      # Full size - best conditions
        'trending_bearish': 1.0,      # Full size - good for shorts
        'choppy_low_vol': 1.0,        # Full size - full capital allocation enabled
        'choppy_high_vol': 1.0,       # Full size - full capital allocation enabled
        'crisis': 0.0                 # No trading - crisis mode
    }
    
    def __init__(self, sma_short: int = 20, sma_long: int = 50, atr_period: int = 14):
        """
        Initialize regime detector
        
        Args:
            sma_short: Short-term SMA period (default 20)
            sma_long: Long-term SMA period (default 50)
            atr_period: ATR period for volatility calculation (default 14)
        """
        self.sma_short = sma_short
        self.sma_long = sma_long
        self.atr_period = atr_period
        
        # Price history for calculations
        self.price_history: List[float] = []
        self.high_history: List[float] = []
        self.low_history: List[float] = []
        self.atr_history: List[float] = []
        
        # Current regime
        self.current_regime = 'choppy_low_vol'
        self.regime_change_time = datetime.now()
        
        # Statistics
        self.regime_changes = 0
        self.bullish_count = 0
        self.bearish_count = 0
        self.choppy_count = 0
        self.crisis_count = 0
        
        log_event('REGIME_INIT', f'RegimeDetector initialized (SMA: {sma_short}/{sma_long}, ATR: {atr_period})')
    
    def add_price_data(self, close: float, high: float, low: float) -> None:
        """
        Add price data point for regime calculation
        
        Args:
            close: Close price
            high: High price
            low: Low price
        """
        self.price_history.append(close)
        self.high_history.append(high)
        self.low_history.append(low)
        
        # Keep only enough data for calculations
        max_length = max(self.sma_long, self.atr_period) + 5
        if len(self.price_history) > max_length:
            self.price_history.pop(0)
            self.high_history.pop(0)
            self.low_history.pop(0)
            if len(self.atr_history) > max_length:
                self.atr_history.pop(0)
    
    def calculate_sma(self, period: int) -> Optional[float]:
        """
        Calculate Simple Moving Average
        
        Args:
            period: SMA period
            
        Returns:
            SMA value or None if not enough data
        """
        if len(self.price_history) < period:
            return None
        
        recent = self.price_history[-period:]
        return sum(recent) / len(recent)
    
    def calculate_atr(self) -> Optional[float]:
        """
        Calculate Average True Range (ATR)
        
        Returns:
            ATR value or None if not enough data
        """
        if len(self.price_history) < self.atr_period + 1:
            return None
        
        # Calculate True Range for each period
        true_ranges = []
        for i in range(1, len(self.price_history)):
            high_low = self.high_history[i] - self.low_history[i]
            high_close = abs(self.high_history[i] - self.price_history[i-1])
            low_close = abs(self.low_history[i] - self.price_history[i-1])
            
            tr = max(high_low, high_close, low_close)
            true_ranges.append(tr)
        
        # ATR is simple average of last N true ranges
        if len(true_ranges) < self.atr_period:
            return None
        
        atr = sum(true_ranges[-self.atr_period:]) / self.atr_period
        self.atr_history.append(atr)
        return atr
    
    def get_atr_percent(self, close: float, atr: float) -> Optional[float]:
        """
        Calculate ATR as percentage of close price
        
        Args:
            close: Close price
            atr: ATR value
            
        Returns:
            ATR as percentage
        """
        if close == 0:
            return None
        return (atr / close) * 100
    
    def detect_trend(self) -> Optional[str]:
        """
        Detect trend direction using SMA crossover
        
        Returns:
            'BULLISH', 'BEARISH', or None if insufficient data
        """
        sma_short = self.calculate_sma(self.sma_short)
        sma_long = self.calculate_sma(self.sma_long)
        
        if sma_short is None or sma_long is None:
            return None
        
        current_price = self.price_history[-1]
        
        # BULLISH: SMA20 > SMA50 and price above both
        if sma_short > sma_long and current_price > sma_short:
            return 'BULLISH'
        
        # BEARISH: SMA20 < SMA50 and price below both
        if sma_short < sma_long and current_price < sma_short:
            return 'BEARISH'
        
        # CHOPPY: SMAs converged or price between them
        return None
    
    def classify_regime(self) -> str:
        """
        Classify market regime based on trend and volatility
        
        Returns:
            Regime classification (TRENDING_BULLISH, TRENDING_BEARISH, CHOPPY_LOW_VOL, CHOPPY_HIGH_VOL, CRISIS)
        """
        # Get current data
        atr = self.calculate_atr()
        if not self.price_history:
            return self.REGIMES['CHOPPY_LOW_VOL']
        
        current_price = self.price_history[-1]
        trend = self.detect_trend()
        
        # Insufficient data
        if atr is None:
            return self.REGIMES['CHOPPY_LOW_VOL']
        
        atr_percent = self.get_atr_percent(current_price, atr)
        if atr_percent is None:
            return self.REGIMES['CHOPPY_LOW_VOL']
        
        # Determine regime based on ATR and trend
        
        # CRISIS: Extreme volatility (ATR > 5%)
        if atr_percent > 5.0:
            regime = self.REGIMES['CRISIS']
            if regime != self.current_regime:
                log_event('REGIME_CRISIS', f'CRISIS mode activated (ATR: {atr_percent:.2f}%)')
                self.regime_changes += 1
                self.crisis_count += 1
            return regime
        
        # TRENDING_BULLISH: Strong uptrend (Bullish + ATR < 3%)
        if trend == 'BULLISH' and atr_percent < 3.0:
            regime = self.REGIMES['TRENDING_BULLISH']
            if regime != self.current_regime:
                log_event('REGIME_BULLISH', f'TRENDING_BULLISH (ATR: {atr_percent:.2f}%)')
                self.regime_changes += 1
                self.bullish_count += 1
            return regime
        
        # TRENDING_BEARISH: Strong downtrend (Bearish + ATR < 3%)
        if trend == 'BEARISH' and atr_percent < 3.0:
            regime = self.REGIMES['TRENDING_BEARISH']
            if regime != self.current_regime:
                log_event('REGIME_BEARISH', f'TRENDING_BEARISH (ATR: {atr_percent:.2f}%)')
                self.regime_changes += 1
                self.bearish_count += 1
            return regime
        
        # CHOPPY_HIGH_VOL: High volatility but no trend (ATR 2.5-5%)
        if 2.5 <= atr_percent < 5.0:
            regime = self.REGIMES['CHOPPY_HIGH_VOL']
            if regime != self.current_regime:
                log_event('REGIME_CHOPPY_HIGH', f'CHOPPY_HIGH_VOL (ATR: {atr_percent:.2f}%)')
                self.regime_changes += 1
                self.choppy_count += 1
            return regime
        
        # CHOPPY_LOW_VOL: Low volatility, choppy (ATR < 1%)
        if atr_percent < 1.0:
            regime = self.REGIMES['CHOPPY_LOW_VOL']
            if regime != self.current_regime:
                log_event('REGIME_CHOPPY_LOW', f'CHOPPY_LOW_VOL (ATR: {atr_percent:.2f}%)')
                self.regime_changes += 1
                self.choppy_count += 1
            return regime
        
        # Default: CHOPPY_LOW_VOL (medium volatility 1-2.5%)
        regime = self.REGIMES['CHOPPY_LOW_VOL']
        if regime != self.current_regime:
            log_event('REGIME_CHOPPY_LOW', f'CHOPPY_LOW_VOL (ATR: {atr_percent:.2f}%)')
            self.regime_changes += 1
            self.choppy_count += 1
        return regime
    
    def update_regime(self) -> str:
        """
        Update and return current market regime
        
        Returns:
            Current regime classification
        """
        new_regime = self.classify_regime()
        
        # Track regime changes
        if new_regime != self.current_regime:
            self.current_regime = new_regime
            self.regime_change_time = datetime.now()
        
        return self.current_regime
    
    def get_position_size_multiplier(self) -> float:
        """
        Get position size multiplier based on current regime
        
        Returns:
            Position size multiplier (0.0 to 1.0)
        """
        return self.SIZE_MULTIPLIERS.get(self.current_regime, 0.5)
    
    def should_trade(self) -> bool:
        """
        Determine if trading should be allowed based on regime
        
        Returns:
            True if trading allowed, False if in CRISIS mode
        """
        return self.current_regime != self.REGIMES['CRISIS']
    
    def get_regime_info(self) -> Dict:
        """
        Get comprehensive regime information
        
        Returns:
            Dictionary with regime details
        """
        atr = None
        atr_percent = None
        sma_short = self.calculate_sma(self.sma_short)
        sma_long = self.calculate_sma(self.sma_long)
        
        if self.price_history:
            atr = self.calculate_atr()
            current_price = self.price_history[-1]
            if atr:
                atr_percent = self.get_atr_percent(current_price, atr)
        
        return {
            'regime': self.current_regime,
            'size_multiplier': self.get_position_size_multiplier(),
            'can_trade': self.should_trade(),
            'sma_short': sma_short,
            'sma_long': sma_long,
            'atr': atr,
            'atr_percent': atr_percent,
            'trend': self.detect_trend(),
            'regime_changes': self.regime_changes,
            'change_time': self.regime_change_time.isoformat() if self.regime_change_time else None
        }
    
    def get_filter_statistics(self) -> Dict:
        """
        Get regime filter statistics
        
        Returns:
            Dictionary with regime statistics
        """
        total = self.bullish_count + self.bearish_count + self.choppy_count + self.crisis_count
        
        return {
            'total_regime_changes': self.regime_changes,
            'bullish_count': self.bullish_count,
            'bearish_count': self.bearish_count,
            'choppy_count': self.choppy_count,
            'crisis_count': self.crisis_count,
            'current_regime': self.current_regime,
            'size_multiplier': self.get_position_size_multiplier(),
            'trading_active': self.should_trade()
        }
    
    def reset_filter_stats(self) -> None:
        """Reset all statistics"""
        self.regime_changes = 0
        self.bullish_count = 0
        self.bearish_count = 0
        self.choppy_count = 0
        self.crisis_count = 0
        log_event('REGIME_RESET', 'Filter statistics reset')


# Global instance
regime_detector = RegimeDetector()


# Convenience wrapper functions
def detect_regime(close: float, high: float, low: float) -> str:
    """
    Detect market regime
    
    Args:
        close: Close price
        high: High price
        low: Low price
        
    Returns:
        Regime classification
    """
    try:
        regime_detector.add_price_data(close, high, low)
        return regime_detector.update_regime()
    except Exception as e:
        log_event('REGIME_ERROR', f'Error detecting regime: {str(e)}')
        return regime_detector.REGIMES['CHOPPY_LOW_VOL']


def get_regime_multiplier() -> float:
    """
    Get position size multiplier for current regime
    
    Returns:
        Position size multiplier
    
    NOTE: Regime-based sizing temporarily disabled to use full capital allocation.
          Uncomment line below to re-enable regime-based position sizing.
    """
    try:
        # DISABLED: Regime-based position sizing was causing 50% position size reduction
        # Temporarily use full capital allocation (1.0x multiplier)
        # To re-enable regime-based sizing, uncomment the line below:
        # return regime_detector.get_position_size_multiplier()
        return 1.0  # Use full capital allocation
    except Exception as e:
        log_event('REGIME_ERROR', f'Error getting multiplier: {str(e)}')
        return 1.0  # Return 1.0 instead of 0.5


def can_trade_in_regime() -> bool:
    """
    Check if trading allowed in current regime
    
    Returns:
        True if trading allowed
    """
    try:
        return regime_detector.should_trade()
    except Exception as e:
        log_event('REGIME_ERROR', f'Error checking if can trade: {str(e)}')
        return True


def get_regime_info() -> Dict:
    """
    Get comprehensive regime information
    
    Returns:
        Dictionary with regime details
    """
    try:
        return regime_detector.get_regime_info()
    except Exception as e:
        log_event('REGIME_ERROR', f'Error getting regime info: {str(e)}')
        return {
            'regime': 'unknown',
            'size_multiplier': 0.5,
            'can_trade': True,
            'error': str(e)
        }
