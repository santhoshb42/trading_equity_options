"""
Dynamic Capital Allocator - Smart Capital Allocation Based on Signal Quality

Allocates capital dynamically based on:
1. ML confidence score (higher confidence → more capital)
2. Recent win rate (winning streak → more aggressive)
3. Market regime (bull/bear/volatile → adjust sizing)
4. Volatility (high volatility → reduce size)
5. Account performance (drawdown → reduce size)

Strategy:
- Base: ₹2000 per trade (CAP_PER_TRADE)
- ML confidence 0.8+ → 1.5x capital (₹3000)
- ML confidence 0.7-0.8 → 1.2x capital (₹2400)
- ML confidence 0.6-0.7 → 1.0x capital (₹2000)
- ML confidence 0.5-0.6 → 0.7x capital (₹1400)
- Winning streak 3+ → +20% bonus
- Losing streak 2+ → -30% reduction
- High volatility → -20% reduction
- CRISIS regime → -50% reduction
"""

from typing import Dict, Any, Tuple
from datetime import datetime, timedelta
import json
from pathlib import Path

try:
    from .bot_logging import log_event
except ImportError:
    def log_event(*args, **kwargs):
        pass

try:
    from .config import CapitalConfig
except ImportError:
    class CapitalConfig:
        CAP_PER_TRADE = 2000
        MAX_CAPITAL = 20000


class DynamicCapitalAllocator:
    """
    Dynamically allocate capital per trade based on signal quality and market conditions
    """
    
    def __init__(self):
        """Initialize the capital allocator"""
        self.base_capital = CapitalConfig.CAP_PER_TRADE
        self.max_capital_per_trade = CapitalConfig.CAP_PER_TRADE * 2.0  # Max 2x base
        self.min_capital_per_trade = CapitalConfig.CAP_PER_TRADE * 0.5  # Min 0.5x base
        
        # ML confidence thresholds
        self.ml_confidence_levels = {
            0.85: 1.5,   # Very high confidence → 1.5x capital
            0.75: 1.2,   # High confidence → 1.2x capital
            0.65: 1.0,   # Normal confidence → 1.0x capital
            0.55: 0.7,   # Low confidence → 0.7x capital
        }
        
        # Streak adjustments
        self.win_streak_bonus = 0.20   # +20% on 3+ win streak
        self.loss_streak_penalty = 0.30  # -30% on 2+ loss streak
        
        # Volatility adjustment
        self.high_volatility_reduction = 0.20  # -20% in high volatility
        
        # Regime adjustments
        self.regime_multipliers = {
            'BULL': 1.0,      # Normal in bull market
            'BEAR': 0.8,      # Reduce 20% in bear
            'VOLATILE': 0.7,  # Reduce 30% in volatile
            'CRISIS': 0.5     # Reduce 50% in crisis
        }
    
    def calculate_capital_allocation(
        self,
        symbol: str,
        ml_score: float,
        price: float,
        recent_trades: list = None,
        market_regime: str = 'BULL',
        volatility: str = 'NORMAL',
        account_drawdown: float = 0.0
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calculate optimal capital allocation for this trade
        
        Args:
            symbol: Stock symbol
            ml_score: ML confidence score (0-1)
            price: Entry price
            recent_trades: List of recent trade outcomes (for streak detection)
            market_regime: Current market regime (BULL/BEAR/VOLATILE/CRISIS)
            volatility: Volatility level (LOW/NORMAL/HIGH)
            account_drawdown: Current account drawdown % (0-100)
        
        Returns:
            Tuple of (allocated_capital, breakdown_dict)
        """
        capital = self.base_capital
        breakdown = {
            'base_capital': self.base_capital,
            'ml_multiplier': 1.0,
            'streak_multiplier': 1.0,
            'regime_multiplier': 1.0,
            'volatility_multiplier': 1.0,
            'drawdown_multiplier': 1.0,
            'final_multiplier': 1.0,
            'allocated_capital': 0.0
        }
        
        # 1. ML Confidence Adjustment
        ml_multiplier = self._get_ml_multiplier(ml_score)
        breakdown['ml_multiplier'] = ml_multiplier
        capital *= ml_multiplier
        
        log_event("CAPITAL_ML_ADJUSTMENT", f"ML confidence adjustment for {symbol}",
                 symbol=symbol,
                 ml_score=round(ml_score, 3),
                 ml_multiplier=ml_multiplier,
                 capital_after_ml=round(capital, 2))
        
        # 2. Streak Adjustment (if recent trades provided)
        if recent_trades:
            streak_multiplier = self._get_streak_multiplier(recent_trades)
            breakdown['streak_multiplier'] = streak_multiplier
            capital *= streak_multiplier
            
            log_event("CAPITAL_STREAK_ADJUSTMENT", f"Streak adjustment for {symbol}",
                     symbol=symbol,
                     streak_multiplier=streak_multiplier,
                     recent_trades=len(recent_trades),
                     capital_after_streak=round(capital, 2))
        
        # 3. Market Regime Adjustment
        regime_multiplier = self.regime_multipliers.get(market_regime, 1.0)
        breakdown['regime_multiplier'] = regime_multiplier
        capital *= regime_multiplier
        
        if regime_multiplier < 1.0:
            log_event("CAPITAL_REGIME_ADJUSTMENT", f"Regime adjustment for {symbol}",
                     symbol=symbol,
                     market_regime=market_regime,
                     regime_multiplier=regime_multiplier,
                     capital_after_regime=round(capital, 2))
        
        # 4. Volatility Adjustment
        volatility_multiplier = 1.0
        if volatility == 'HIGH':
            volatility_multiplier = 1.0 - self.high_volatility_reduction
            breakdown['volatility_multiplier'] = volatility_multiplier
            capital *= volatility_multiplier
            
            log_event("CAPITAL_VOLATILITY_ADJUSTMENT", f"Volatility adjustment for {symbol}",
                     symbol=symbol,
                     volatility=volatility,
                     volatility_multiplier=volatility_multiplier,
                     capital_after_volatility=round(capital, 2))
        
        # 5. Drawdown Protection
        if account_drawdown > 5.0:  # If drawdown > 5%
            drawdown_multiplier = max(0.5, 1.0 - (account_drawdown / 100))
            breakdown['drawdown_multiplier'] = drawdown_multiplier
            capital *= drawdown_multiplier
            
            log_event("CAPITAL_DRAWDOWN_ADJUSTMENT", f"Drawdown protection for {symbol}",
                     symbol=symbol,
                     account_drawdown=round(account_drawdown, 2),
                     drawdown_multiplier=round(drawdown_multiplier, 2),
                     capital_after_drawdown=round(capital, 2))
        
        # Apply min/max limits
        capital = max(self.min_capital_per_trade, min(self.max_capital_per_trade, capital))
        
        # Calculate final multiplier
        breakdown['final_multiplier'] = capital / self.base_capital
        breakdown['allocated_capital'] = round(capital, 2)
        
        log_event("CAPITAL_ALLOCATION_FINAL", f"💰 Final capital allocation for {symbol}",
                 symbol=symbol,
                 base_capital=self.base_capital,
                 allocated_capital=round(capital, 2),
                 final_multiplier=round(breakdown['final_multiplier'], 2),
                 ml_score=round(ml_score, 3),
                 breakdown=breakdown)
        
        return capital, breakdown
    
    def _get_ml_multiplier(self, ml_score: float) -> float:
        """Get capital multiplier based on ML confidence score"""
        # Sort thresholds in descending order
        for threshold in sorted(self.ml_confidence_levels.keys(), reverse=True):
            if ml_score >= threshold:
                return self.ml_confidence_levels[threshold]
        
        # Below lowest threshold → minimum multiplier
        return 0.6
    
    def _get_streak_multiplier(self, recent_trades: list) -> float:
        """
        Get capital multiplier based on recent win/loss streak
        
        Args:
            recent_trades: List of recent trade outcomes (e.g., ['WIN', 'WIN', 'LOSS', ...])
        
        Returns:
            Multiplier (0.7 to 1.2)
        """
        if not recent_trades or len(recent_trades) == 0:
            return 1.0
        
        # Get last 5 trades
        recent = recent_trades[-5:] if len(recent_trades) >= 5 else recent_trades
        
        # Count consecutive wins/losses from most recent
        streak_type = recent[-1]  # Most recent trade outcome
        streak_count = 1
        
        for i in range(len(recent) - 2, -1, -1):
            if recent[i] == streak_type:
                streak_count += 1
            else:
                break
        
        # Apply streak bonus/penalty
        if streak_type == 'WIN' and streak_count >= 3:
            # Winning streak: increase capital
            return 1.0 + self.win_streak_bonus
        elif streak_type == 'LOSS' and streak_count >= 2:
            # Losing streak: decrease capital
            return 1.0 - self.loss_streak_penalty
        
        return 1.0
    
    def get_allocation_summary(self, symbol: str, allocated_capital: float, breakdown: Dict) -> str:
        """
        Get human-readable summary of capital allocation
        
        Returns:
            Summary string
        """
        parts = []
        
        # Base
        parts.append(f"Base: ₹{breakdown['base_capital']:.0f}")
        
        # ML adjustment
        if breakdown['ml_multiplier'] != 1.0:
            parts.append(f"ML: {breakdown['ml_multiplier']:.2f}x")
        
        # Streak adjustment
        if breakdown.get('streak_multiplier', 1.0) != 1.0:
            parts.append(f"Streak: {breakdown['streak_multiplier']:.2f}x")
        
        # Regime adjustment
        if breakdown.get('regime_multiplier', 1.0) != 1.0:
            parts.append(f"Regime: {breakdown['regime_multiplier']:.2f}x")
        
        # Volatility adjustment
        if breakdown.get('volatility_multiplier', 1.0) != 1.0:
            parts.append(f"Vol: {breakdown['volatility_multiplier']:.2f}x")
        
        # Drawdown adjustment
        if breakdown.get('drawdown_multiplier', 1.0) != 1.0:
            parts.append(f"DD: {breakdown['drawdown_multiplier']:.2f}x")
        
        # Final
        parts.append(f"Final: ₹{allocated_capital:.0f} ({breakdown['final_multiplier']:.2f}x)")
        
        return " | ".join(parts)


# Singleton instance
_allocator_instance = None

def get_capital_allocator() -> DynamicCapitalAllocator:
    """Get singleton instance of capital allocator"""
    global _allocator_instance
    if _allocator_instance is None:
        _allocator_instance = DynamicCapitalAllocator()
    return _allocator_instance


def calculate_dynamic_capital(
    symbol: str,
    ml_score: float,
    price: float,
    recent_trades: list = None,
    market_regime: str = 'BULL',
    volatility: str = 'NORMAL'
) -> Tuple[float, Dict[str, Any]]:
    """
    Convenience function to calculate dynamic capital allocation
    
    Args:
        symbol: Stock symbol
        ml_score: ML confidence score (0-1)
        price: Entry price
        recent_trades: List of recent trade outcomes
        market_regime: Market regime (BULL/BEAR/VOLATILE/CRISIS)
        volatility: Volatility level (LOW/NORMAL/HIGH)
    
    Returns:
        Tuple of (allocated_capital, breakdown_dict)
    """
    allocator = get_capital_allocator()
    return allocator.calculate_capital_allocation(
        symbol=symbol,
        ml_score=ml_score,
        price=price,
        recent_trades=recent_trades,
        market_regime=market_regime,
        volatility=volatility
    )


# Test function
if __name__ == "__main__":
    print("=== Dynamic Capital Allocator Test ===\n")
    
    allocator = DynamicCapitalAllocator()
    
    # Test scenarios
    scenarios = [
        {
            'name': 'High confidence, winning streak',
            'ml_score': 0.85,
            'recent_trades': ['WIN', 'WIN', 'WIN'],
            'regime': 'BULL',
            'volatility': 'NORMAL'
        },
        {
            'name': 'Low confidence, losing streak',
            'ml_score': 0.58,
            'recent_trades': ['LOSS', 'LOSS'],
            'regime': 'BULL',
            'volatility': 'NORMAL'
        },
        {
            'name': 'Good confidence, CRISIS regime',
            'ml_score': 0.75,
            'recent_trades': ['WIN', 'LOSS'],
            'regime': 'CRISIS',
            'volatility': 'HIGH'
        },
        {
            'name': 'Very high confidence, normal conditions',
            'ml_score': 0.90,
            'recent_trades': ['WIN', 'WIN'],
            'regime': 'BULL',
            'volatility': 'LOW'
        },
    ]
    
    for scenario in scenarios:
        print(f"\n{scenario['name']}:")
        capital, breakdown = allocator.calculate_capital_allocation(
            symbol='TEST',
            ml_score=scenario['ml_score'],
            price=1000,
            recent_trades=scenario['recent_trades'],
            market_regime=scenario['regime'],
            volatility=scenario['volatility']
        )
        print(f"  Allocated Capital: ₹{capital:.0f}")
        print(f"  Final Multiplier: {breakdown['final_multiplier']:.2f}x")
        print(f"  Breakdown: ML={breakdown['ml_multiplier']:.2f}x, "
              f"Streak={breakdown['streak_multiplier']:.2f}x, "
              f"Regime={breakdown['regime_multiplier']:.2f}x")
