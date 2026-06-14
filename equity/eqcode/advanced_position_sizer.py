"""
Advanced Position Sizing Module (Week 3 P3.3)
Sizes positions based on volatility, correlation, win rate, and risk management
Implements Kelly Criterion inspired sizing with adjustments for risk tolerance
"""

import json
import os
from datetime import datetime
from typing import Dict, Optional, Tuple

# Safe imports with fallbacks
try:
    from eqcode.bot_logging import log_event
except ImportError:
    def log_event(event_type: str, message: str, **kwargs):
        print(f"[{event_type}] {message}")


class AdvancedPositionSizer:
    """
    Advanced position sizing based on multiple factors:
    - Volatility (ATR, historical vol)
    - Correlation risk
    - Win rate and performance
    - Risk tolerance and capital
    - Consecutive loss/win streaks
    """
    
    def __init__(self, 
                 base_size: int = 100,
                 max_position_size: int = 500,
                 min_position_size: int = 1,
                 risk_per_trade_pct: float = 1.0,
                 kelly_fraction: float = 0.25):
        """
        Initialize advanced position sizer
        
        Args:
            base_size: Base position size (default 100 shares)
            max_position_size: Maximum position size
            min_position_size: Minimum position size
            risk_per_trade_pct: Risk per trade as % of capital (default 1%)
            kelly_fraction: Fraction of Kelly Criterion to use (0.25 = conservative)
        """
        self.base_size = base_size
        self.max_position_size = max_position_size
        self.min_position_size = min_position_size
        self.risk_per_trade_pct = risk_per_trade_pct
        self.kelly_fraction = kelly_fraction
        
        # Performance tracking
        self.win_rate = 0.75  # Default assumption
        self.avg_win_pct = 1.5  # Average win %
        self.avg_loss_pct = 0.8  # Average loss %
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        
        # Capital management
        self.total_capital = 20000  # Default 20K capital
        self.used_capital = 0
        
        # Statistics
        self.size_adjustments = 0
        
        log_event('POSITIONER_INIT', f'AdvancedPositionSizer initialized',
                 base_size=base_size, risk_per_trade=f'{risk_per_trade_pct}%')
    
    def set_capital(self, total: float, used: float = 0) -> None:
        """
        Set available capital
        
        Args:
            total: Total capital
            used: Currently used capital
        """
        self.total_capital = total
        self.used_capital = used
        log_event('POSITIONER_CAPITAL', f'Capital set: ₹{total:.0f} (used: ₹{used:.0f})')
    
    def set_performance(self, win_rate: float, avg_win: float, avg_loss: float) -> None:
        """
        Set performance metrics for Kelly calculation
        
        Args:
            win_rate: Win rate (0.0 - 1.0)
            avg_win: Average win % per trade
            avg_loss: Average loss % per trade
        """
        self.win_rate = max(0.0, min(1.0, win_rate))
        self.avg_win_pct = avg_win
        self.avg_loss_pct = avg_loss
    
    def update_streak(self, is_win: bool) -> None:
        """
        Update consecutive win/loss streak
        
        Args:
            is_win: True if last trade was winning
        """
        if is_win:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
    
    def calculate_kelly_size(self) -> float:
        """
        Calculate position size using Kelly Criterion
        
        Kelly % = (Win% * Avg_Win - Loss% * Avg_Loss) / Avg_Win
        
        Returns:
            Position size multiplier (1.0 = base size)
        """
        if self.avg_win_pct == 0:
            return 1.0
        
        # Kelly formula
        win_prob = self.win_rate
        loss_prob = 1.0 - win_prob
        
        kelly = (win_prob * self.avg_win_pct - loss_prob * self.avg_loss_pct) / self.avg_win_pct
        
        # Apply Kelly fraction (conservative: use 25% of Kelly)
        kelly_fraction_applied = kelly * self.kelly_fraction
        
        # Clamp between 0.5x and 2.0x
        kelly_size = max(0.5, min(2.0, kelly_fraction_applied))
        
        return kelly_size
    
    def calculate_volatility_adjustment(self, atr_value: float, entry_price: float) -> float:
        """
        Adjust position size based on volatility (ATR)
        
        Args:
            atr_value: ATR value (absolute, not percentage)
            entry_price: Current entry price
            
        Returns:
            Position size multiplier
        """
        if entry_price <= 0:
            return 1.0
        
        # Calculate ATR as percentage of price
        atr_percent = (atr_value / entry_price) * 100
        
        # Lower volatility → larger positions
        # Higher volatility → smaller positions
        
        if atr_percent < 0.5:
            # Very low volatility - increase size (1.2x)
            multiplier = 1.2
        elif atr_percent < 1.0:
            # Low volatility - increase size (1.1x)
            multiplier = 1.1
        elif atr_percent < 2.0:
            # Normal volatility - base size (1.0x)
            multiplier = 1.0
        elif atr_percent < 3.0:
            # Higher volatility - reduce size (0.8x)
            multiplier = 0.8
        elif atr_percent < 4.0:
            # Very high volatility - reduce size (0.6x)
            multiplier = 0.6
        else:
            # Extreme volatility - minimal size (0.4x)
            multiplier = 0.4
        
        return multiplier
    
    def calculate_correlation_adjustment(self, correlation_risk: float) -> float:
        """
        Adjust position size based on correlation risk
        
        Args:
            correlation_risk: Correlation risk score (0.0 - 1.0)
            
        Returns:
            Position size multiplier
        """
        # correlation_risk = 0.0 → 1.0x multiplier
        # correlation_risk = 1.0 → 0.3x multiplier
        
        return 1.0 - (correlation_risk * 0.7)
    
    def calculate_streak_adjustment(self) -> float:
        """
        Adjust position size based on consecutive wins/losses
        
        Returns:
            Position size multiplier
        """
        # Increase size on winning streaks, decrease on losing streaks
        
        if self.consecutive_wins >= 3:
            # On 3+ win streak - increase confidence (1.2x)
            return 1.2
        elif self.consecutive_wins >= 1:
            # On win streak - slight increase (1.1x)
            return 1.1
        elif self.consecutive_losses >= 2:
            # On 2+ loss streak - reduce to protect capital (0.6x)
            return 0.6
        elif self.consecutive_losses >= 1:
            # On loss - reduce slightly (0.8x)
            return 0.8
        else:
            # No streak - base size
            return 1.0
    
    def calculate_capital_adjustment(self) -> float:
        """
        Adjust position size based on available capital
        
        Returns:
            Position size multiplier
        """
        available = self.total_capital - self.used_capital
        available_ratio = available / self.total_capital if self.total_capital > 0 else 1.0
        
        # Less capital available → smaller positions
        if available_ratio > 0.8:
            # Plenty of capital - normal size
            return 1.0
        elif available_ratio > 0.6:
            # Moderate capital - slight reduction
            return 0.9
        elif available_ratio > 0.4:
            # Low capital - reduce (0.7x)
            return 0.7
        elif available_ratio > 0.2:
            # Very low capital - minimal trading (0.4x)
            return 0.4
        else:
            # Critical low capital - stop trading (0.0x)
            return 0.0
    
    def calculate_position_size(self,
                               entry_price: float,
                               stop_loss: float,
                               atr_value: Optional[float] = None,
                               correlation_risk: Optional[float] = None,
                               max_available: Optional[float] = None) -> int:
        """
        Calculate optimal position size with all adjustments
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            atr_value: ATR value (optional, for volatility adjustment)
            correlation_risk: Correlation risk score (optional)
            max_available: Maximum capital available (optional)
            
        Returns:
            Position size in shares
        """
        # Start with Kelly-based size
        kelly_mult = self.calculate_kelly_size()
        
        # Apply adjustments
        multipliers = [kelly_mult]
        
        # Volatility adjustment
        if atr_value is not None and atr_value > 0:
            vol_mult = self.calculate_volatility_adjustment(atr_value, entry_price)
            multipliers.append(vol_mult)
        
        # Correlation adjustment
        if correlation_risk is not None:
            corr_mult = self.calculate_correlation_adjustment(correlation_risk)
            multipliers.append(corr_mult)
        
        # Streak adjustment
        streak_mult = self.calculate_streak_adjustment()
        multipliers.append(streak_mult)
        
        # Capital adjustment
        capital_mult = self.calculate_capital_adjustment()
        multipliers.append(capital_mult)
        
        # Combine all multipliers (geometric mean)
        combined_mult = 1.0
        for mult in multipliers:
            combined_mult *= mult
        combined_mult = combined_mult ** (1 / len(multipliers))
        
        # Calculate base size in rupees
        risk_amount = (self.total_capital * self.risk_per_trade_pct) / 100
        
        # Position size based on risk and stop loss
        if entry_price == stop_loss:
            # No valid stop loss - use base size
            position_size_rs = self.base_size * entry_price
        else:
            risk_per_share = abs(entry_price - stop_loss)
            shares_for_risk = risk_amount / risk_per_share if risk_per_share > 0 else self.base_size
            position_size_rs = shares_for_risk * entry_price
        
        # Apply combined multiplier
        adjusted_size_rs = position_size_rs * combined_mult
        
        # Convert to shares and apply limits
        position_shares = int(adjusted_size_rs / entry_price) if entry_price > 0 else 0
        position_shares = max(self.min_position_size, min(self.max_position_size, position_shares))
        
        # Respect max available capital
        if max_available and (position_shares * entry_price) > max_available:
            position_shares = int(max_available / entry_price) if entry_price > 0 else 0
            position_shares = max(self.min_position_size, min(self.max_position_size, position_shares))
        
        return position_shares
    
    def get_sizing_breakdown(self, position_size: int, entry_price: float,
                            stop_loss: float, atr_value: Optional[float] = None,
                            correlation_risk: Optional[float] = None) -> Dict:
        """
        Get detailed breakdown of position sizing factors
        
        Returns:
            Dictionary with sizing details
        """
        kelly_mult = self.calculate_kelly_size()
        vol_mult = self.calculate_volatility_adjustment(atr_value or 1.5, entry_price)
        corr_mult = self.calculate_correlation_adjustment(correlation_risk or 0.0)
        streak_mult = self.calculate_streak_adjustment()
        capital_mult = self.calculate_capital_adjustment()
        
        risk_per_share = abs(entry_price - stop_loss)
        risk_amount = position_size * risk_per_share
        
        return {
            'position_size': position_size,
            'position_value_rs': position_size * entry_price,
            'risk_amount_rs': risk_amount,
            'risk_pct_of_capital': (risk_amount / self.total_capital * 100) if self.total_capital > 0 else 0,
            'kelly_multiplier': f'{kelly_mult:.2f}x',
            'volatility_multiplier': f'{vol_mult:.2f}x',
            'correlation_multiplier': f'{corr_mult:.2f}x',
            'streak_multiplier': f'{streak_mult:.2f}x',
            'capital_multiplier': f'{capital_mult:.2f}x',
            'combined_multiplier': f'{kelly_mult * vol_mult * corr_mult * streak_mult * capital_mult:.2f}x',
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'risk_per_share': risk_per_share,
            'win_rate': f'{self.win_rate * 100:.1f}%',
            'consecutive_wins': self.consecutive_wins,
            'consecutive_losses': self.consecutive_losses
        }
    
    def reset_streaks(self) -> None:
        """Reset win/loss streaks"""
        self.consecutive_wins = 0
        self.consecutive_losses = 0


# Global instance
position_sizer = AdvancedPositionSizer()


# Convenience wrapper functions
def set_performance(win_rate: float, avg_win: float, avg_loss: float) -> None:
    """Set performance metrics"""
    try:
        position_sizer.set_performance(win_rate, avg_win, avg_loss)
    except Exception as e:
        log_event('POSITIONER_ERROR', f'Error setting performance: {str(e)}')


def calculate_size(entry_price: float, stop_loss: float,
                  atr_value: Optional[float] = None,
                  correlation_risk: Optional[float] = None,
                  max_available: Optional[float] = None) -> int:
    """Calculate optimal position size"""
    try:
        return position_sizer.calculate_position_size(
            entry_price, stop_loss, atr_value, correlation_risk, max_available
        )
    except Exception as e:
        log_event('POSITIONER_ERROR', f'Error calculating size: {str(e)}')
        return position_sizer.base_size


def update_streak(is_win: bool) -> None:
    """Update win/loss streak"""
    try:
        position_sizer.update_streak(is_win)
    except Exception as e:
        log_event('POSITIONER_ERROR', f'Error updating streak: {str(e)}')


def set_capital(total: float, used: float = 0) -> None:
    """Set available capital"""
    try:
        position_sizer.set_capital(total, used)
    except Exception as e:
        log_event('POSITIONER_ERROR', f'Error setting capital: {str(e)}')


def get_sizing_breakdown(position_size: int, entry_price: float,
                        stop_loss: float, atr_value: Optional[float] = None,
                        correlation_risk: Optional[float] = None) -> Dict:
    """Get sizing breakdown"""
    try:
        return position_sizer.get_sizing_breakdown(
            position_size, entry_price, stop_loss, atr_value, correlation_risk
        )
    except Exception as e:
        log_event('POSITIONER_ERROR', f'Error getting breakdown: {str(e)}')
        return {}
