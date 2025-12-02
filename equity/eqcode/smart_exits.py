"""
Intelligent Exit Strategy Module (Week 2 P2.2)
Implements multiple exit methods to maximize profit capture and protect capital
6 Exit Rules: Time-based, Profit-locking, Resistance, Crash Protection, Trend Reversal, Low-conviction
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


class ExitManager:
    """
    Manages intelligent exits for open positions
    Implements 6 different exit strategies to maximize profit and minimize loss
    """
    
    # Exit types
    EXIT_TYPES = {
        'TIME_BASED': 'time_based',
        'PROFIT_LOCK': 'profit_lock',
        'RESISTANCE': 'resistance',
        'CRASH_PROTECTION': 'crash_protection',
        'TREND_REVERSAL': 'trend_reversal',
        'LOW_CONVICTION': 'low_conviction'
    }
    
    def __init__(self, 
                 early_exit_time_min: int = 150,  # 2:30 PM (IST)
                 final_exit_time_min: int = 195,   # 3:15 PM (IST)
                 profit_lock_levels: Optional[List[float]] = None,
                 crash_threshold: float = 1.5,     # Nifty % drop threshold
                 rsi_extremes: Tuple[int, int] = (30, 70)):
        """
        Initialize exit manager
        
        Args:
            early_exit_time_min: Partial exit time in minutes from market open (default 2:30 PM = 150 min)
            final_exit_time_min: Full exit time in minutes from market open (default 3:15 PM = 195 min)
            profit_lock_levels: Profit levels for partial exit [1%, 2%, 3%] (default)
            crash_threshold: Nifty drop % that triggers crash protection (default 1.5%)
            rsi_extremes: RSI overbought/oversold levels (default 30/70)
        """
        self.early_exit_time_min = early_exit_time_min
        self.final_exit_time_min = final_exit_time_min
        self.crash_threshold = crash_threshold
        self.rsi_oversold, self.rsi_overbought = rsi_extremes
        
        # Default profit locking levels: +1%, +2%, +3%
        self.profit_lock_levels = profit_lock_levels or [1.0, 2.0, 3.0]
        
        # Position tracking
        self.open_positions: Dict[str, Dict] = {}
        self.exit_signals: List[Dict] = []
        
        # Statistics
        self.total_exits = 0
        self.time_based_exits = 0
        self.profit_lock_exits = 0
        self.resistance_exits = 0
        self.crash_exits = 0
        self.trend_exits = 0
        self.low_conviction_exits = 0
        
        log_event('EXIT_INIT', f'ExitManager initialized (early: {early_exit_time_min}min, final: {final_exit_time_min}min)')
    
    def add_position(self, symbol: str, entry_price: float, entry_time: datetime, 
                    position_size: int, target_price: Optional[float] = None,
                    stop_loss: Optional[float] = None) -> None:
        """
        Track an open position
        
        Args:
            symbol: Stock symbol
            entry_price: Entry price
            entry_time: Entry time
            position_size: Position size (quantity)
            target_price: Target profit level (optional)
            stop_loss: Stop loss level (optional)
        """
        self.open_positions[symbol] = {
            'entry_price': entry_price,
            'entry_time': entry_time,
            'position_size': position_size,
            'target_price': target_price,
            'stop_loss': stop_loss,
            'current_price': entry_price,
            'last_update': entry_time,
            'profit_lock_level': 0,  # Track which profit lock level we've hit
            'crash_marked': False,    # Track if crash protection already triggered
        }
        log_event('POSITION_ADDED', f'Tracking position {symbol} @ {entry_price}', qty=position_size)
    
    def update_position_price(self, symbol: str, current_price: float, update_time: datetime) -> None:
        """
        Update position with current market price
        
        Args:
            symbol: Stock symbol
            current_price: Current market price
            update_time: Update time
        """
        if symbol in self.open_positions:
            self.open_positions[symbol]['current_price'] = current_price
            self.open_positions[symbol]['last_update'] = update_time
    
    def calculate_profit_percent(self, symbol: str) -> Optional[float]:
        """
        Calculate unrealized profit percentage
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Profit % or None if position not found
        """
        if symbol not in self.open_positions:
            return None
        
        pos = self.open_positions[symbol]
        entry = pos['entry_price']
        current = pos['current_price']
        
        if entry == 0:
            return None
        
        return ((current - entry) / entry) * 100
    
    def get_minutes_from_open(self, time_ref: datetime) -> int:
        """
        Get minutes elapsed since market open (9:15 AM IST)
        
        Args:
            time_ref: Reference time
            
        Returns:
            Minutes from market open
        """
        # Market open is 9:15 AM IST
        market_open = time_ref.replace(hour=9, minute=15, second=0, microsecond=0)
        
        # If before market open, assume yesterday's open
        if time_ref.time() < market_open.time():
            market_open = market_open - timedelta(days=1)
        
        elapsed = time_ref - market_open
        return int(elapsed.total_seconds() / 60)
    
    # ===== EXIT RULE 1: Time-Based Exits =====
    def check_time_based_exit(self, symbol: str, current_time: datetime) -> Optional[Dict]:
        """
        Check for time-based exit rules
        
        Args:
            symbol: Stock symbol
            current_time: Current time
            
        Returns:
            Exit signal dict or None
        """
        if symbol not in self.open_positions:
            return None
        
        pos = self.open_positions[symbol]
        minutes_held = self.get_minutes_from_open(current_time)
        profit = self.calculate_profit_percent(symbol)
        
        # Rule 1a: 2:30 PM exit 50% if holding 1+ hour
        if minutes_held >= self.early_exit_time_min and minutes_held < self.final_exit_time_min:
            if profit is not None and profit > -0.5:  # Exit if not at loss
                quantity = max(1, int(pos['position_size'] * 0.5))
                return {
                    'type': self.EXIT_TYPES['TIME_BASED'],
                    'symbol': symbol,
                    'quantity': quantity,
                    'current_price': pos['current_price'],
                    'profit_percent': profit,
                    'reason': '2:30 PM partial exit (50%)',
                    'exit_time': current_time
                }
        
        # Rule 1b: 3:15 PM exit remaining 50%
        if minutes_held >= self.final_exit_time_min:
            remaining = pos['position_size'] - pos.get('exited_quantity', 0)
            if remaining > 0:
                return {
                    'type': self.EXIT_TYPES['TIME_BASED'],
                    'symbol': symbol,
                    'quantity': remaining,
                    'current_price': pos['current_price'],
                    'profit_percent': profit,
                    'reason': '3:15 PM final exit (100%)',
                    'exit_time': current_time
                }
        
        return None
    
    # ===== EXIT RULE 2: Profit-Locking (Scale Out) =====
    def check_profit_lock_exit(self, symbol: str) -> Optional[Dict]:
        """
        Check for profit-locking exit rules
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Exit signal dict or None
        """
        if symbol not in self.open_positions:
            return None
        
        pos = self.open_positions[symbol]
        profit = self.calculate_profit_percent(symbol)
        
        if profit is None or profit <= 0:
            return None
        
        current_level = pos.get('profit_lock_level', 0)
        
        # +1% profit: Mark for +2% exit
        if profit >= self.profit_lock_levels[0] and current_level == 0:
            pos['profit_lock_level'] = 1
            log_event('PROFIT_LOCK_LEVEL_1', f'{symbol} hit +{self.profit_lock_levels[0]}% profit, marking for +{self.profit_lock_levels[1]}% exit')
            return None
        
        # +2% profit: Exit 50%
        if profit >= self.profit_lock_levels[1] and current_level >= 1:
            quantity = max(1, int(pos['position_size'] * 0.5))
            pos['profit_lock_level'] = 2
            return {
                'type': self.EXIT_TYPES['PROFIT_LOCK'],
                'symbol': symbol,
                'quantity': quantity,
                'current_price': pos['current_price'],
                'profit_percent': profit,
                'reason': f'Profit lock at +{self.profit_lock_levels[1]}% (scale out 50%)',
                'exit_time': pos['last_update']
            }
        
        # +3% profit: Exit remaining 100%
        if profit >= self.profit_lock_levels[2] and current_level >= 2:
            remaining = pos['position_size'] - pos.get('exited_quantity', 0)
            if remaining > 0:
                return {
                    'type': self.EXIT_TYPES['PROFIT_LOCK'],
                    'symbol': symbol,
                    'quantity': remaining,
                    'current_price': pos['current_price'],
                    'profit_percent': profit,
                    'reason': f'Full profit lock at +{self.profit_lock_levels[2]}% (exit 100%)',
                    'exit_time': pos['last_update']
                }
        
        return None
    
    # ===== EXIT RULE 3: Resistance-Based Exits =====
    def check_resistance_exit(self, symbol: str, nearest_resistance: Optional[float]) -> Optional[Dict]:
        """
        Check for resistance-based exits
        
        Args:
            symbol: Stock symbol
            nearest_resistance: Nearest resistance level
            
        Returns:
            Exit signal dict or None
        """
        if symbol not in self.open_positions or nearest_resistance is None:
            return None
        
        pos = self.open_positions[symbol]
        current = pos['current_price']
        
        # Check if at resistance (within 0.2%)
        resistance_tolerance = nearest_resistance * 0.002
        if abs(current - nearest_resistance) < resistance_tolerance:
            quantity = max(1, int(pos['position_size'] * 0.5))
            return {
                'type': self.EXIT_TYPES['RESISTANCE'],
                'symbol': symbol,
                'quantity': quantity,
                'current_price': current,
                'profit_percent': self.calculate_profit_percent(symbol),
                'reason': f'At resistance level ₹{nearest_resistance}',
                'exit_time': pos['last_update']
            }
        
        # Check if 2% above entry (secondary resistance exit)
        if current > pos['entry_price'] * 1.02:
            remaining = pos['position_size'] - pos.get('exited_quantity', 0)
            if remaining > 0 and pos.get('profit_lock_level', 0) >= 1:
                return {
                    'type': self.EXIT_TYPES['RESISTANCE'],
                    'symbol': symbol,
                    'quantity': remaining,
                    'current_price': current,
                    'profit_percent': self.calculate_profit_percent(symbol),
                    'reason': '2% above entry - resistance exit',
                    'exit_time': pos['last_update']
                }
        
        return None
    
    # ===== EXIT RULE 4: Market Crash Protection =====
    def check_crash_protection(self, symbol: str, nifty_percent_drop: float) -> Optional[Dict]:
        """
        Check for market crash protection exits
        
        Args:
            symbol: Stock symbol
            nifty_percent_drop: Nifty drop % from open
            
        Returns:
            Exit signal dict or None
        """
        if symbol not in self.open_positions:
            return None
        
        pos = self.open_positions[symbol]
        
        # Trigger on crash: Nifty drops > threshold %
        if abs(nifty_percent_drop) > self.crash_threshold and not pos.get('crash_marked', False):
            pos['crash_marked'] = True
            quantity = max(1, int(pos['position_size'] * 0.75))
            return {
                'type': self.EXIT_TYPES['CRASH_PROTECTION'],
                'symbol': symbol,
                'quantity': quantity,
                'current_price': pos['current_price'],
                'profit_percent': self.calculate_profit_percent(symbol),
                'reason': f'Crash protection triggered (Nifty {nifty_percent_drop:.2f}%)',
                'exit_time': pos['last_update']
            }
        
        return None
    
    # ===== EXIT RULE 5: Trend Reversal Exits =====
    def check_trend_reversal_exit(self, symbol: str, trend_reversed: bool, 
                                  rsi: Optional[float] = None) -> Optional[Dict]:
        """
        Check for trend reversal exits
        
        Args:
            symbol: Stock symbol
            trend_reversed: Whether 5-min trend has reversed
            rsi: Current RSI value (optional)
            
        Returns:
            Exit signal dict or None
        """
        if symbol not in self.open_positions:
            return None
        
        pos = self.open_positions[symbol]
        
        # Exit on trend reversal
        if trend_reversed:
            return {
                'type': self.EXIT_TYPES['TREND_REVERSAL'],
                'symbol': symbol,
                'quantity': pos['position_size'],
                'current_price': pos['current_price'],
                'profit_percent': self.calculate_profit_percent(symbol),
                'reason': 'Trend reversal detected',
                'exit_time': pos['last_update']
            }
        
        # Exit if RSI at extreme levels
        if rsi is not None:
            if rsi > self.rsi_overbought:
                return {
                    'type': self.EXIT_TYPES['TREND_REVERSAL'],
                    'symbol': symbol,
                    'quantity': int(pos['position_size'] * 0.5),
                    'current_price': pos['current_price'],
                    'profit_percent': self.calculate_profit_percent(symbol),
                    'reason': f'RSI overbought ({rsi:.1f})',
                    'exit_time': pos['last_update']
                }
        
        return None
    
    # ===== EXIT RULE 6: Low-Conviction Exits =====
    def check_low_conviction_exit(self, symbol: str, minutes_held: int,
                                 volume_confirmed: bool) -> Optional[Dict]:
        """
        Check for low-conviction exits
        
        Args:
            symbol: Stock symbol
            minutes_held: Minutes since entry
            volume_confirmed: Whether entry had volume confirmation
            
        Returns:
            Exit signal dict or None
        """
        if symbol not in self.open_positions:
            return None
        
        pos = self.open_positions[symbol]
        profit = self.calculate_profit_percent(symbol)
        
        if profit is None:
            return None
        
        # No volume confirmation after entry - exit with small loss
        if not volume_confirmed and minutes_held >= 5:
            if profit < 0.5:
                return {
                    'type': self.EXIT_TYPES['LOW_CONVICTION'],
                    'symbol': symbol,
                    'quantity': pos['position_size'],
                    'current_price': pos['current_price'],
                    'profit_percent': profit,
                    'reason': 'Low conviction exit (no volume confirmation)',
                    'exit_time': pos['last_update']
                }
        
        # Profit < 0.5% after 5 minutes - exit bad setup
        if minutes_held >= 5 and profit < 0.5 and profit > -0.5:
            return {
                'type': self.EXIT_TYPES['LOW_CONVICTION'],
                'symbol': symbol,
                'quantity': pos['position_size'],
                'current_price': pos['current_price'],
                'profit_percent': profit,
                'reason': 'Low conviction exit (stalled profit)',
                'exit_time': pos['last_update']
            }
        
        return None
    
    # ===== CONSOLIDATED EXIT CHECK =====
    def get_exit_signal(self, symbol: str, current_time: datetime,
                       current_price: float, **kwargs) -> Optional[Dict]:
        """
        Check all exit rules and return best exit signal
        
        Args:
            symbol: Stock symbol
            current_time: Current time
            current_price: Current market price
            **kwargs: Additional data (nifty_drop, rsi, etc.)
            
        Returns:
            Best exit signal or None
        """
        self.update_position_price(symbol, current_price, current_time)
        
        if symbol not in self.open_positions:
            return None
        
        # Check all exit rules in priority order
        # Priority: Crash > Time-based > Profit-lock > Trend > Resistance > Low-conviction
        
        # 1. Crash protection (highest priority)
        nifty_drop = kwargs.get('nifty_percent_drop', 0)
        crash_exit = self.check_crash_protection(symbol, nifty_drop)
        if crash_exit:
            self.crash_exits += 1
            return crash_exit
        
        # 2. Time-based exits
        time_exit = self.check_time_based_exit(symbol, current_time)
        if time_exit:
            self.time_based_exits += 1
            return time_exit
        
        # 3. Profit-locking
        profit_exit = self.check_profit_lock_exit(symbol)
        if profit_exit:
            self.profit_lock_exits += 1
            return profit_exit
        
        # 4. Trend reversal
        trend_rev = kwargs.get('trend_reversed', False)
        rsi = kwargs.get('rsi', None)
        trend_exit = self.check_trend_reversal_exit(symbol, trend_rev, rsi)
        if trend_exit:
            self.trend_exits += 1
            return trend_exit
        
        # 5. Resistance
        resistance = kwargs.get('nearest_resistance', None)
        resistance_exit = self.check_resistance_exit(symbol, resistance)
        if resistance_exit:
            self.resistance_exits += 1
            return resistance_exit
        
        # 6. Low-conviction
        minutes_held = self.get_minutes_from_open(current_time) - self.get_minutes_from_open(
            self.open_positions[symbol]['entry_time']
        )
        volume_conf = kwargs.get('volume_confirmed', True)
        low_conv_exit = self.check_low_conviction_exit(symbol, minutes_held, volume_conf)
        if low_conv_exit:
            self.low_conviction_exits += 1
            return low_conv_exit
        
        return None
    
    def close_position(self, symbol: str, quantity: int) -> None:
        """
        Mark position as partially closed
        
        Args:
            symbol: Stock symbol
            quantity: Quantity closed
        """
        if symbol in self.open_positions:
            self.open_positions[symbol]['exited_quantity'] = \
                self.open_positions[symbol].get('exited_quantity', 0) + quantity
            
            remaining = self.open_positions[symbol]['position_size'] - \
                       self.open_positions[symbol]['exited_quantity']
            
            if remaining <= 0:
                del self.open_positions[symbol]
                log_event('POSITION_CLOSED', f'Position {symbol} fully closed')
            else:
                log_event('POSITION_PARTIAL_EXIT', f'Partial exit {symbol}, remaining: {remaining}')
    
    def get_exit_statistics(self) -> Dict:
        """
        Get exit strategy statistics
        
        Returns:
            Dictionary with statistics
        """
        return {
            'total_exits': self.total_exits,
            'time_based': self.time_based_exits,
            'profit_lock': self.profit_lock_exits,
            'resistance': self.resistance_exits,
            'crash_protection': self.crash_exits,
            'trend_reversal': self.trend_exits,
            'low_conviction': self.low_conviction_exits,
            'open_positions': len(self.open_positions)
        }
    
    def reset_statistics(self) -> None:
        """Reset all statistics"""
        self.total_exits = 0
        self.time_based_exits = 0
        self.profit_lock_exits = 0
        self.resistance_exits = 0
        self.crash_exits = 0
        self.trend_exits = 0
        self.low_conviction_exits = 0
        log_event('EXIT_STATS_RESET', 'Exit statistics reset')


# Global instance
exit_manager = ExitManager()


# Convenience wrapper functions
def add_position(symbol: str, entry_price: float, entry_time: datetime,
                position_size: int, target: Optional[float] = None,
                stop_loss: Optional[float] = None) -> None:
    """Add position to exit manager"""
    try:
        exit_manager.add_position(symbol, entry_price, entry_time, position_size, target, stop_loss)
    except Exception as e:
        log_event('EXIT_ERROR', f'Error adding position: {str(e)}')


def check_exit(symbol: str, current_time: datetime, current_price: float, **kwargs) -> Optional[Dict]:
    """Check for exit signal"""
    try:
        return exit_manager.get_exit_signal(symbol, current_time, current_price, **kwargs)
    except Exception as e:
        log_event('EXIT_ERROR', f'Error checking exit: {str(e)}')
        return None


def close_position(symbol: str, quantity: int) -> None:
    """Close position in exit manager"""
    try:
        exit_manager.close_position(symbol, quantity)
    except Exception as e:
        log_event('EXIT_ERROR', f'Error closing position: {str(e)}')


def get_exit_stats() -> Dict:
    """Get exit statistics"""
    try:
        return exit_manager.get_exit_statistics()
    except Exception as e:
        log_event('EXIT_ERROR', f'Error getting statistics: {str(e)}')
        return {}
