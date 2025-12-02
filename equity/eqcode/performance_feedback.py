"""
Performance Feedback Loop Module (Week 3 P3.2)
Tracks performance metrics and adapts trading parameters based on results
Learns from historical performance to improve future trades
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import deque

# Safe imports with fallbacks
try:
    from eqcode.bot_logging import log_event
except ImportError:
    def log_event(event_type: str, message: str, **kwargs):
        print(f"[{event_type}] {message}")


class PerformanceFeedback:
    """
    Tracks trading performance and adapts parameters based on results
    Provides feedback loop for continuous improvement
    """
    
    def __init__(self, lookback_window: int = 20):
        """
        Initialize performance feedback system
        
        Args:
            lookback_window: Number of recent trades to analyze
        """
        self.lookback_window = lookback_window
        
        # Trade tracking
        self.trades: deque = deque(maxlen=lookback_window * 2)  # Keep extra for history
        
        # Performance metrics
        self.win_count = 0
        self.loss_count = 0
        self.total_profit = 0.0
        self.total_loss = 0.0
        
        # Time-based metrics
        self.hourly_performance: Dict[int, Dict] = {}  # Hour -> {wins, losses, profit}
        self.daily_performance: Dict[str, Dict] = {}   # Date -> {wins, losses, profit}
        
        # Signal quality metrics
        self.signal_source_stats: Dict[str, Dict] = {}  # Source -> {wins, losses, accuracy}
        self.symbol_stats: Dict[str, Dict] = {}         # Symbol -> {wins, losses, accuracy}
        
        # Adaptive parameters
        self.confidence_threshold = 90.0
        self.rsi_threshold_low = 30
        self.rsi_threshold_high = 70
        self.position_size_base = 100
        
        # Adaptation history
        self.parameter_changes: List[Dict] = []
        
        log_event('FEEDBACK_INIT', f'PerformanceFeedback initialized (window: {lookback_window})')
    
    def record_trade(self, symbol: str, entry_price: float, exit_price: float,
                    entry_time: datetime, exit_time: datetime,
                    profit_percent: float, signal_source: Optional[str] = None) -> None:
        """
        Record a completed trade for analysis
        
        Args:
            symbol: Stock symbol
            entry_price: Entry price
            exit_price: Exit price
            entry_time: Entry time
            exit_time: Exit time
            profit_percent: Profit/loss percentage
            signal_source: Source of signal (e.g., 'TRADINGVIEW', 'RSI', etc.)
        """
        is_winning = profit_percent > 0.0
        profit_amount = abs(profit_percent)
        
        trade = {
            'symbol': symbol,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'entry_time': entry_time,
            'exit_time': exit_time,
            'profit_percent': profit_percent,
            'is_winning': is_winning,
            'signal_source': signal_source or 'UNKNOWN',
            'hour': entry_time.hour,
            'date': entry_time.strftime('%Y-%m-%d')
        }
        
        self.trades.append(trade)
        
        # Update metrics
        if is_winning:
            self.win_count += 1
            self.total_profit += profit_amount
        else:
            self.loss_count += 1
            self.total_loss += profit_amount
        
        # Update time-based metrics
        hour = entry_time.hour
        if hour not in self.hourly_performance:
            self.hourly_performance[hour] = {'wins': 0, 'losses': 0, 'profit': 0.0}
        
        if is_winning:
            self.hourly_performance[hour]['wins'] += 1
            self.hourly_performance[hour]['profit'] += profit_percent
        else:
            self.hourly_performance[hour]['losses'] += 1
            self.hourly_performance[hour]['profit'] -= profit_percent
        
        # Update daily metrics
        date_key = trade['date']
        if date_key not in self.daily_performance:
            self.daily_performance[date_key] = {'wins': 0, 'losses': 0, 'profit': 0.0}
        
        if is_winning:
            self.daily_performance[date_key]['wins'] += 1
            self.daily_performance[date_key]['profit'] += profit_percent
        else:
            self.daily_performance[date_key]['losses'] += 1
            self.daily_performance[date_key]['profit'] -= profit_percent
        
        # Update signal source stats
        source = trade['signal_source']
        if source not in self.signal_source_stats:
            self.signal_source_stats[source] = {'wins': 0, 'losses': 0, 'profit': 0.0}
        
        if is_winning:
            self.signal_source_stats[source]['wins'] += 1
            self.signal_source_stats[source]['profit'] += profit_percent
        else:
            self.signal_source_stats[source]['losses'] += 1
            self.signal_source_stats[source]['profit'] -= profit_percent
        
        # Update symbol stats
        if symbol not in self.symbol_stats:
            self.symbol_stats[symbol] = {'wins': 0, 'losses': 0, 'profit': 0.0}
        
        if is_winning:
            self.symbol_stats[symbol]['wins'] += 1
            self.symbol_stats[symbol]['profit'] += profit_percent
        else:
            self.symbol_stats[symbol]['losses'] += 1
            self.symbol_stats[symbol]['profit'] -= profit_percent
        
        log_event('FEEDBACK_TRADE_RECORDED', f'{symbol}: {profit_percent:+.2f}%', 
                 source=source, is_winning=is_winning)
    
    def get_win_rate(self, recent_only: bool = True) -> float:
        """
        Get win rate percentage
        
        Args:
            recent_only: Use only recent trades (lookback_window)
            
        Returns:
            Win rate percentage (0-100), or 50.0 if insufficient data
        """
        if recent_only:
            trades_to_check = list(self.trades)[-self.lookback_window:]
        else:
            trades_to_check = list(self.trades)
        
        # 🔧 FIX GAP-009: If not enough data, return 50% (neutral) instead of 0% (bad)
        if len(trades_to_check) < 3:
            return 50.0  # Neutral position when no data
        
        wins = sum(1 for t in trades_to_check if t['is_winning'])
        return (wins / len(trades_to_check)) * 100
    
    def get_profit_factor(self) -> float:
        """
        Calculate profit factor (gross profit / gross loss)
        
        Returns:
            Profit factor (>1.0 is profitable)
        """
        if self.total_loss == 0:
            return float('inf') if self.total_profit > 0 else 0.0
        
        return self.total_profit / self.total_loss
    
    def get_best_hour(self) -> Optional[int]:
        """
        Get hour with best win rate
        
        Returns:
            Hour (0-23) with best performance, or None
        """
        if not self.hourly_performance:
            return None
        
        best_hour = None
        best_rate = -1
        
        for hour, stats in self.hourly_performance.items():
            total = stats['wins'] + stats['losses']
            if total > 0:
                win_rate = stats['wins'] / total
                if win_rate > best_rate:
                    best_rate = win_rate
                    best_hour = hour
        
        return best_hour
    
    def get_best_symbol(self) -> Optional[str]:
        """
        Get symbol with best win rate
        
        Returns:
            Symbol with best performance, or None
        """
        if not self.symbol_stats:
            return None
        
        best_symbol = None
        best_rate = -1
        
        for symbol, stats in self.symbol_stats.items():
            total = stats['wins'] + stats['losses']
            if total > 2:  # Need at least 3 trades
                win_rate = stats['wins'] / total
                if win_rate > best_rate:
                    best_rate = win_rate
                    best_symbol = symbol
        
        return best_symbol
    
    def get_worst_symbol(self) -> Optional[str]:
        """
        Get symbol with worst win rate
        
        Returns:
            Symbol with worst performance, or None
        """
        if not self.symbol_stats:
            return None
        
        worst_symbol = None
        worst_rate = 2.0
        
        for symbol, stats in self.symbol_stats.items():
            total = stats['wins'] + stats['losses']
            if total > 2:  # Need at least 3 trades
                win_rate = stats['wins'] / total
                if win_rate < worst_rate:
                    worst_rate = win_rate
                    worst_symbol = symbol
        
        return worst_symbol
    
    def get_best_signal_source(self) -> Optional[str]:
        """
        Get signal source with best accuracy
        
        Returns:
            Signal source with best win rate, or None
        """
        if not self.signal_source_stats:
            return None
        
        best_source = None
        best_rate = -1
        
        for source, stats in self.signal_source_stats.items():
            total = stats['wins'] + stats['losses']
            if total > 2:
                win_rate = stats['wins'] / total
                if win_rate > best_rate:
                    best_rate = win_rate
                    best_source = source
        
        return best_source
    
    def adapt_confidence_threshold(self) -> float:
        """
        Adapt confidence threshold based on performance
        
        Returns:
            New confidence threshold
        """
        win_rate = self.get_win_rate(recent_only=True)
        
        # If not enough data, maintain current threshold
        # (win_rate will be 50.0 indicating neutral/no data)
        if win_rate == 50.0 and len(list(self.trades)[-self.lookback_window:]) < 3:
            return self.confidence_threshold
        
        # If win rate is high, can lower threshold slightly (more trades)
        # If win rate is low, should raise threshold (quality filter)
        
        if win_rate >= 80:
            # Very high win rate - can be more aggressive
            new_threshold = max(80.0, self.confidence_threshold - 2)
            change = "LOWER (more aggressive)"
        elif win_rate >= 75:
            # Good win rate - maintain
            new_threshold = 90.0
            change = "MAINTAIN"
        elif win_rate >= 70:
            # Acceptable but could be better - raise slightly
            new_threshold = min(95.0, self.confidence_threshold + 2)
            change = "RAISE (quality filter)"
        else:
            # Poor win rate - raise significantly
            new_threshold = min(98.0, self.confidence_threshold + 5)
            change = "RAISE (strict filter)"
        
        if new_threshold != self.confidence_threshold:
            log_event('FEEDBACK_ADAPT_THRESHOLD', 
                     f'Confidence threshold adjusted: {self.confidence_threshold:.1f} → {new_threshold:.1f}',
                     reason=change, win_rate=f'{win_rate:.1f}%')
            
            self.parameter_changes.append({
                'timestamp': datetime.now(),
                'parameter': 'confidence_threshold',
                'old_value': self.confidence_threshold,
                'new_value': new_threshold,
                'reason': f'Win rate {win_rate:.1f}%'
            })
            
            self.confidence_threshold = new_threshold
        
        return new_threshold
    
    def adapt_position_size(self) -> float:
        """
        Adapt position size based on performance and volatility
        
        Returns:
            New position size multiplier
        """
        win_rate = self.get_win_rate(recent_only=True)
        profit_factor = self.get_profit_factor()
        
        # If not enough data, don't adapt
        if win_rate == 50.0 and len(list(self.trades)[-self.lookback_window:]) < 3:
            return self.position_size_base
        
        # Base size on confidence
        if win_rate >= 80 and profit_factor > 1.5:
            # High confidence and profitability - increase size
            multiplier = 1.2
            reason = "High performance"
        elif win_rate >= 75 and profit_factor > 1.0:
            # Good performance - normal size
            multiplier = 1.0
            reason = "Stable performance"
        elif win_rate >= 70:
            # Acceptable - slight reduction
            multiplier = 0.85
            reason = "Below target performance"
        else:
            # Poor performance - reduce size
            multiplier = 0.7
            reason = "Poor performance"
        
        new_size = self.position_size_base * multiplier
        
        log_event('FEEDBACK_ADAPT_SIZE', 
                 f'Position size adjusted: {self.position_size_base:.0f} → {new_size:.0f}',
                 reason=reason, multiplier=f'{multiplier:.2f}x')
        
        return new_size
    
    def get_performance_summary(self) -> Dict:
        """
        Get comprehensive performance summary
        
        Returns:
            Dictionary with performance metrics
        """
        recent_trades = list(self.trades)[-self.lookback_window:]
        
        # 🔧 FIX GAP-009: Handle None win rate values
        overall_wr = self.get_win_rate(recent_only=False)
        recent_wr = self.get_win_rate(recent_only=True)
        overall_wr_str = f'{overall_wr:.1f}%' if overall_wr is not None else 'N/A'
        recent_wr_str = f'{recent_wr:.1f}%' if recent_wr is not None else 'N/A'
        
        return {
            'total_trades': len(list(self.trades)),
            'recent_trades': len(recent_trades),
            'win_count': self.win_count,
            'loss_count': self.loss_count,
            'win_rate': overall_wr_str,
            'recent_win_rate': recent_wr_str,
            'profit_factor': f'{self.get_profit_factor():.2f}',
            'total_profit': f'{self.total_profit:.2f}%',
            'total_loss': f'{self.total_loss:.2f}%',
            'net_profit': f'{self.total_profit - self.total_loss:.2f}%',
            'best_hour': self.get_best_hour(),
            'best_symbol': self.get_best_symbol(),
            'worst_symbol': self.get_worst_symbol(),
            'best_signal_source': self.get_best_signal_source(),
            'current_confidence_threshold': f'{self.confidence_threshold:.1f}%',
            'parameter_changes': len(self.parameter_changes)
        }
    
    def reset_statistics(self) -> None:
        """Reset all statistics"""
        self.trades.clear()
        self.win_count = 0
        self.loss_count = 0
        self.total_profit = 0.0
        self.total_loss = 0.0
        self.hourly_performance.clear()
        self.daily_performance.clear()
        self.signal_source_stats.clear()
        self.symbol_stats.clear()
        self.parameter_changes.clear()
        log_event('FEEDBACK_RESET', 'Performance feedback statistics reset')


# Global instance
performance_feedback = PerformanceFeedback()


# Convenience wrapper functions
def record_trade(symbol: str, entry_price: float, exit_price: float,
                entry_time: datetime, exit_time: datetime,
                profit_percent: float, signal_source: Optional[str] = None) -> None:
    """Record a completed trade"""
    try:
        performance_feedback.record_trade(symbol, entry_price, exit_price,
                                        entry_time, exit_time, profit_percent, signal_source)
    except Exception as e:
        log_event('FEEDBACK_ERROR', f'Error recording trade: {str(e)}')


def get_win_rate(recent_only: bool = True) -> float:
    """Get win rate"""
    try:
        return performance_feedback.get_win_rate(recent_only)
    except Exception as e:
        log_event('FEEDBACK_ERROR', f'Error getting win rate: {str(e)}')
        return 0.0


def get_profit_factor() -> float:
    """Get profit factor"""
    try:
        return performance_feedback.get_profit_factor()
    except Exception as e:
        log_event('FEEDBACK_ERROR', f'Error getting profit factor: {str(e)}')
        return 0.0


def adapt_parameters() -> Dict:
    """Adapt trading parameters based on performance"""
    try:
        threshold = performance_feedback.adapt_confidence_threshold()
        size = performance_feedback.adapt_position_size()
        return {'threshold': threshold, 'position_size': size}
    except Exception as e:
        log_event('FEEDBACK_ERROR', f'Error adapting parameters: {str(e)}')
        return {}


def get_performance_summary() -> Dict:
    """Get performance summary"""
    try:
        return performance_feedback.get_performance_summary()
    except Exception as e:
        log_event('FEEDBACK_ERROR', f'Error getting summary: {str(e)}')
        return {}


def get_best_symbol() -> Optional[str]:
    """Get best performing symbol"""
    try:
        return performance_feedback.get_best_symbol()
    except Exception as e:
        log_event('FEEDBACK_ERROR', f'Error getting best symbol: {str(e)}')
        return None


def get_worst_symbol() -> Optional[str]:
    """Get worst performing symbol"""
    try:
        return performance_feedback.get_worst_symbol()
    except Exception as e:
        log_event('FEEDBACK_ERROR', f'Error getting worst symbol: {str(e)}')
        return None
