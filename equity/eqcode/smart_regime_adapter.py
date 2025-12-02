"""
Smart Regime Adapter Integration Module
Integrates with HybridLearningEngine to detect and adapt to market regimes
Lightweight, non-intrusive, pure ML approach (no explicit signals)
"""

from collections import deque
import numpy as np


class SmartRegimeAdapter:
    """
    Lightweight regime detector that learns from win rate trends
    Adapts symbol selection without needing explicit market data
    
    Pure ML approach: Only uses outcomes from trades
    """
    
    def __init__(self, window=3):
        """
        Args:
            window: Number of days to look back for regime detection (3 days recommended)
        """
        self.window = window
        self.recent_win_rates = deque(maxlen=window)
        self.regime = 'neutral'
        
        # Symbol categorization
        self.aggressive = {'AXIS', 'HDFC', 'INFY', 'TCS', 'LT'}
        self.defensive = {'SBI', 'BHARTI', 'WIPRO', 'BAJAJ', 'MARUTI'}
    
    def update(self, today_win_rate):
        """
        Update regime based on today's win rate
        
        Args:
            today_win_rate: Win rate percentage (0-100)
        
        Returns:
            str: Detected regime ('trending_up', 'ranging', 'trending_down', 'volatile', 'neutral')
        """
        self.recent_win_rates.append(today_win_rate)
        
        # Need minimum data points
        if len(self.recent_win_rates) < 2:
            return 'neutral'
        
        recent = list(self.recent_win_rates)
        avg = np.mean(recent)
        trend = recent[-1] - recent[0]
        volatility = np.std(recent)
        
        # TUNED thresholds (optimized from stress test)
        if avg > 55 and trend > -2:
            self.regime = 'trending_up'
        elif avg > 48 and trend > -8 and volatility < 6:
            self.regime = 'ranging'
        elif avg < 48 or trend < -8:
            self.regime = 'trending_down'
        elif volatility > 6:
            self.regime = 'volatile'
        else:
            self.regime = 'neutral'
        
        return self.regime
    
    def get_symbol_boost(self, symbol):
        """
        Get score boost for a symbol based on current regime
        
        Args:
            symbol: Trading symbol
        
        Returns:
            float: Score adjustment (-0.10 to +0.10)
        """
        if self.regime in ['trending_up', 'ranging']:
            return 0.10 if symbol in self.aggressive else -0.05
        elif self.regime == 'trending_down':
            return 0.10 if symbol in self.defensive else -0.05
        elif self.regime == 'volatile':
            return 0.05 if symbol in self.defensive else -0.02
        else:
            return 0.0
    
    def get_status(self):
        """Get human-readable status"""
        return {
            'regime': self.regime,
            'win_rate_history': list(self.recent_win_rates),
            'avg_win_rate': np.mean(self.recent_win_rates) if self.recent_win_rates else 0,
            'active_symbols': list(self.aggressive if self.regime in ['trending_up', 'ranging'] else self.defensive),
        }


def integrate_regime_adapter(alerts, regime_adapter):
    """
    Apply regime-based scoring adjustment to alerts
    
    This is a pure ML approach - no external signals leak into trading
    Only learning from outcomes to adapt symbol selection
    
    Args:
        alerts: List of alert dicts with 'symbol' key
        regime_adapter: SmartRegimeAdapter instance
    
    Returns:
        List of alerts with 'regime_boost' feature added
    """
    for alert in alerts:
        symbol = alert.get('symbol', 'UNKNOWN')
        boost = regime_adapter.get_symbol_boost(symbol)
        alert['features']['regime_adapted'] = boost
    
    return alerts


# Example integration in main.py:
"""
from eqcode.smart_regime_adapter import SmartRegimeAdapter, integrate_regime_adapter

# In bot init:
self.regime_adapter = SmartRegimeAdapter(window=3)

# In daily alert processing:
alerts = get_alerts()
alerts = integrate_regime_adapter(alerts, self.regime_adapter)
selection = self.learning_engine.rank_and_select(alerts)

# After EOD update:
self.learning_engine.eod_learning_update()
self.regime_adapter.update(daily_win_rate)  # Update with today's results

# For monitoring:
status = self.regime_adapter.get_status()
print(f"Market regime: {status['regime']}")
print(f"Win rate avg: {status['avg_win_rate']:.1f}%")
"""
