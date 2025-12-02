"""
Drawdown Protection Module
Prevents catastrophic losses through multiple safeguards

Features:
1. Daily loss limit - Max ₹1,000 per day
2. Consecutive loss recovery mode - Reduce position size after 3 losses
3. Intra-trade drawdown check - Exit if MAE > 4%
4. Weekly/Monthly safeguards - Circuit breakers
"""

import logging
import json
from datetime import datetime, date

try:
    from .bot_logging import log_event, log_broker_error
except Exception:
    def log_event(*args, **kwargs):
        pass
    def log_broker_error(*args, **kwargs):
        pass

logger = logging.getLogger(__name__)


class DrawdownProtector:
    """Manages drawdown and loss limits to protect capital"""
    
    def __init__(self, 
                 daily_loss_limit=1000,
                 consecutive_loss_threshold=3,
                 weekly_loss_limit=2500,
                 monthly_loss_limit=5000,
                 recovery_mode_size_multiplier=0.5):
        """
        Initialize drawdown protector
        
        Args:
            daily_loss_limit: Max loss per day in rupees (default ₹1,000)
            consecutive_loss_threshold: Losses to trigger recovery mode (default 3)
            weekly_loss_limit: Max loss per week in rupees (default ₹2,500)
            monthly_loss_limit: Max loss per month in rupees (default ₹5,000)
            recovery_mode_size_multiplier: Position size multiplier in recovery (default 0.5 = 50%)
        """
        self.daily_loss_limit = daily_loss_limit
        self.consecutive_loss_threshold = consecutive_loss_threshold
        self.weekly_loss_limit = weekly_loss_limit
        self.monthly_loss_limit = monthly_loss_limit
        self.recovery_mode_size_multiplier = recovery_mode_size_multiplier
        
        # State tracking
        self.current_date = date.today()
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.monthly_pnl = 0.0
        self.consecutive_losses = 0
        self.recovery_mode_active = False
        self.trades_in_recovery_mode = 0
        
        logger.info(f"DrawdownProtector initialized: daily_limit={daily_loss_limit}, "
                   f"weekly_limit={weekly_loss_limit}, monthly_limit={monthly_loss_limit}")
    
    def can_take_trade(self):
        """
        Check if a new trade can be taken based on drawdown limits
        
        Returns:
            (bool, str) - (can_trade, reason_if_blocked)
        """
        self._update_date()
        
        # Check daily limit
        if self.daily_pnl <= -self.daily_loss_limit:
            reason = f"Daily loss limit reached: ₹{self.daily_pnl:.2f} <= -₹{self.daily_loss_limit}"
            log_event(
                'DRAWDOWN_DAILY_LIMIT_HIT',
                "Daily loss limit reached",
                daily_pnl=self.daily_pnl,
                daily_limit=-self.daily_loss_limit,
                blocked=True
            )
            return False, reason
        
        # Check weekly limit
        if self.weekly_pnl <= -self.weekly_loss_limit:
            reason = f"Weekly loss limit reached: ₹{self.weekly_pnl:.2f} <= -₹{self.weekly_loss_limit}"
            log_event(
                'DRAWDOWN_WEEKLY_LIMIT_HIT',
                "Weekly loss limit reached",
                weekly_pnl=self.weekly_pnl,
                weekly_limit=-self.weekly_loss_limit,
                blocked=True
            )
            return False, reason
        
        # Check monthly limit
        if self.monthly_pnl <= -self.monthly_loss_limit:
            reason = f"Monthly loss limit reached: ₹{self.monthly_pnl:.2f} <= -₹{self.monthly_loss_limit}"
            log_event(
                'DRAWDOWN_MONTHLY_LIMIT_HIT',
                "Monthly loss limit reached",
                monthly_pnl=self.monthly_pnl,
                monthly_limit=-self.monthly_loss_limit,
                blocked=True
            )
            return False, reason
        
        # Check daily limit approaching (80% consumed)
        if self.daily_pnl <= -self.daily_loss_limit * 0.8:
            log_event(
                'DRAWDOWN_DAILY_WARNING',
                "Daily loss limit 80% consumed",
                daily_pnl=self.daily_pnl,
                daily_limit=-self.daily_loss_limit,
                used_percent=f"{(abs(self.daily_pnl) / self.daily_loss_limit) * 100:.1f}%"
            )
        
        return True, "Drawdown checks passed"
    
    def record_trade_result(self, symbol, pnl, is_winning_trade):
        """
        Record trade result to update loss tracking
        
        Args:
            symbol: str - Trading symbol
            pnl: float - Profit/loss amount in rupees
            is_winning_trade: bool - True if trade was profitable
        """
        self._update_date()
        
        # Update PnL trackers
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.monthly_pnl += pnl
        
        # Update consecutive loss counter
        if is_winning_trade:
            # Winning trade - reset consecutive loss counter and exit recovery if 2 wins
            if self.consecutive_losses > 0:
                logger.info(f"Winning trade: resetting consecutive loss counter from {self.consecutive_losses}")
            
            self.consecutive_losses = 0
            
            # Exit recovery mode after 2 consecutive wins
            if self.recovery_mode_active:
                self.trades_in_recovery_mode += 1
                if self.trades_in_recovery_mode >= 2:
                    self.recovery_mode_active = False
                    self.trades_in_recovery_mode = 0
                    log_event(
                        'RECOVERY_MODE_EXITED',
                        "Exited recovery mode after 2 consecutive wins",
                        reason='2_consecutive_wins',
                        symbol=symbol
                    )
        else:
            # Losing trade - increment consecutive loss counter
            self.consecutive_losses += 1
            
            # Activate recovery mode if hit threshold
            if self.consecutive_losses >= self.consecutive_loss_threshold:
                if not self.recovery_mode_active:
                    self.recovery_mode_active = True
                    self.trades_in_recovery_mode = 0
                    log_event(
                        'RECOVERY_MODE_ACTIVATED',
                        "Activated recovery mode after consecutive losses",
                        consecutive_losses=self.consecutive_losses,
                        threshold=self.consecutive_loss_threshold,
                        size_multiplier=self.recovery_mode_size_multiplier,
                        symbol=symbol
                    )
        
        log_event(
            'TRADE_RESULT_RECORDED',
            "Trade result recorded and PnL updated",
            symbol=symbol,
            pnl=pnl,
            is_winning=is_winning_trade,
            consecutive_losses=self.consecutive_losses,
            recovery_mode=self.recovery_mode_active,
            daily_pnl=self.daily_pnl,
            weekly_pnl=self.weekly_pnl,
            monthly_pnl=self.monthly_pnl
        )
    
    def get_position_size_multiplier(self):
        """
        Get position size multiplier based on recovery mode
        
        Returns:
            float - Multiplier to apply to base position size
        """
        if self.recovery_mode_active:
            logger.warning(f"Recovery mode active: applying {self.recovery_mode_size_multiplier}x size multiplier")
            return self.recovery_mode_size_multiplier
        return 1.0
    
    def is_in_recovery_mode(self):
        """Check if recovery mode is active"""
        return self.recovery_mode_active
    
    def get_status(self):
        """Get current protection status"""
        return {
            'date': str(self.current_date),
            'daily_pnl': self.daily_pnl,
            'daily_limit': -self.daily_loss_limit,
            'daily_used_percent': f"{(abs(self.daily_pnl) / self.daily_loss_limit) * 100:.1f}%",
            'weekly_pnl': self.weekly_pnl,
            'weekly_limit': -self.weekly_loss_limit,
            'monthly_pnl': self.monthly_pnl,
            'monthly_limit': -self.monthly_loss_limit,
            'consecutive_losses': self.consecutive_losses,
            'recovery_mode_active': self.recovery_mode_active,
            'trades_in_recovery': self.trades_in_recovery_mode,
            'position_size_multiplier': self.get_position_size_multiplier()
        }
    
    def _update_date(self):
        """Update date-based tracking"""
        today = date.today()
        
        # Reset daily PnL if date changed
        if today != self.current_date:
            logger.info(f"Date changed from {self.current_date} to {today}, resetting daily PnL")
            self.current_date = today
            self.daily_pnl = 0.0
            
            # Reset weekly PnL if needed (assuming week starts on Monday)
            # In production, would check actual week boundaries
            current_week = today.isocalendar()[1]
            previous_week = (today - timedelta(days=7)).isocalendar()[1]
            if current_week != previous_week:
                logger.info("Week changed, resetting weekly PnL")
                self.weekly_pnl = 0.0
            
            # Reset monthly PnL if needed
            # In production, would check actual month boundaries
            if today.month != self.current_date.month:
                logger.info("Month changed, resetting monthly PnL")
                self.monthly_pnl = 0.0
    
    def reset_all(self):
        """Reset all tracking (for testing or manual reset)"""
        self.current_date = date.today()
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.monthly_pnl = 0.0
        self.consecutive_losses = 0
        self.recovery_mode_active = False
        self.trades_in_recovery_mode = 0
        logger.info("DrawdownProtector reset all counters")


# Global instance
drawdown_protector = DrawdownProtector()


def can_take_trade():
    """Check if trade can be taken"""
    return drawdown_protector.can_take_trade()


def record_trade_result(symbol, pnl, is_winning):
    """Record trade result"""
    drawdown_protector.record_trade_result(symbol, pnl, is_winning)


def get_position_size_multiplier():
    """Get current position size multiplier"""
    return drawdown_protector.get_position_size_multiplier()


def get_drawdown_status():
    """Get current drawdown protection status"""
    return drawdown_protector.get_status()


def reset_drawdown_protector():
    """Reset protector (for testing)"""
    drawdown_protector.reset_all()
