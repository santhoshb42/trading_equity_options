"""
Implied Volatility Calculator - Dynamic IV based on market conditions

Since Angel One broker doesn't provide IV in real-time, this module calculates
realistic IV using:
1. Historical volatility from 20 periods of price data
2. Market condition adjustment (ADX-based)
3. Safety cap to prevent over-estimation

Usage:
    calc = VolatilityCalculator()
    iv = calc.get_dynamic_iv(symbol, adx_value=30.5)
"""

import math
from typing import Dict, Optional, List
from datetime import datetime
from collections import defaultdict
from .optlogging import logger

# =============================================================================
# Volatility Calculator
# =============================================================================

class VolatilityCalculator:
    """
    Calculate realistic IV based on historical volatility and market conditions.
    
    Formula:
        Historical Vol = StdDev(Returns) * sqrt(252)
        Dynamic IV = Historical Vol * Market Condition Multiplier
        Final IV = min(Dynamic IV, 50%) [safety cap]
    """
    
    def __init__(self):
        """Initialize volatility tracker"""
        self.price_history: Dict[str, List[float]] = defaultdict(list)
        self.iv_cache: Dict[str, float] = {}
        self.last_update: Dict[str, datetime] = {}
        self.lookback_periods = 20  # 20-period historical volatility
        
    def add_price(self, symbol: str, price: float) -> None:
        """
        Add price to history (called on each candle close).
        
        Args:
            symbol: Stock symbol (e.g., 'RELIANCE')
            price: Close price of the candle
        """
        if price <= 0:
            return
            
        self.price_history[symbol].append(price)
        
        # Keep only last 50 prices (need 20 for calculation, rest for rolling window)
        if len(self.price_history[symbol]) > 50:
            self.price_history[symbol].pop(0)
        
        # Invalidate cache for this symbol
        if symbol in self.iv_cache:
            del self.iv_cache[symbol]
    
    def calculate_historical_volatility(self, symbol: str) -> Optional[float]:
        """
        Calculate historical volatility from price data.
        
        Formula:
            returns = log(price[i] / price[i-1])
            volatility = StdDev(returns) * sqrt(252)
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Annualized volatility (e.g., 0.25 for 25%)
        """
        if symbol not in self.price_history:
            return None
        
        prices = self.price_history[symbol]
        
        # Need at least lookback_periods prices
        if len(prices) < self.lookback_periods:
            return None
        
        # Calculate returns (use last lookback_periods)
        recent_prices = prices[-self.lookback_periods:]
        returns = []
        
        for i in range(1, len(recent_prices)):
            try:
                ret = math.log(recent_prices[i] / recent_prices[i-1])
                returns.append(ret)
            except (ValueError, ZeroDivisionError):
                continue
        
        if len(returns) < 5:  # Need at least 5 returns for meaningful std dev
            return None
        
        # Calculate standard deviation of returns
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        
        # Annualize: multiply by sqrt(252) trading days per year
        annualized_vol = std_dev * math.sqrt(252)
        
        logger.debug(f"VOLATILITY: {symbol} | historical_vol={annualized_vol:.2%} | periods={len(returns)}")
        
        return annualized_vol
    
    def get_market_condition_multiplier(self, adx: Optional[float] = None, 
                                       rsi: Optional[float] = None) -> float:
        """
        Get IV multiplier based on market conditions.
        
        Market conditions affect option IV:
        - Calm market (ADX < 20, RSI 40-60): Lower IV → 0.9x multiplier
        - Normal market (ADX 20-40): Normal IV → 1.0x multiplier
        - Volatile market (ADX > 40): Higher IV → 1.5x multiplier
        - Panic (RSI < 20 or > 80): Very high IV → 2.0x multiplier
        
        Args:
            adx: Average Directional Index (0-100)
            rsi: Relative Strength Index (0-100)
            
        Returns:
            Multiplier to apply to base IV
        """
        multiplier = 1.0  # Default
        
        # Check panic conditions first
        if rsi is not None:
            if rsi < 20 or rsi > 80:  # Extreme RSI = panic/euphoria
                multiplier = 2.0
                logger.debug(f"VOLATILITY: PANIC_CONDITION | RSI={rsi} | multiplier={multiplier}")
                return multiplier
        
        # Apply ADX-based multiplier
        if adx is not None:
            if adx < 20:
                # Calm, ranging market - lower volatility
                multiplier = 0.9
                logger.debug(f"VOLATILITY: CALM_MARKET | ADX={adx} | multiplier={multiplier}")
            elif adx < 40:
                # Normal trending market - baseline volatility
                multiplier = 1.0
                logger.debug(f"VOLATILITY: NORMAL_MARKET | ADX={adx} | multiplier={multiplier}")
            else:
                # Strong trending/volatile market - higher volatility
                multiplier = 1.5
                logger.debug(f"VOLATILITY: VOLATILE_MARKET | ADX={adx} | multiplier={multiplier}")
        
        return multiplier
    
    def get_dynamic_iv(self, symbol: str, adx: Optional[float] = None, 
                      rsi: Optional[float] = None, 
                      default_iv: float = 0.25) -> float:
        """
        Get dynamic IV for a symbol based on market conditions.
        
        Algorithm:
        1. Calculate historical volatility from recent prices
        2. Apply market condition multiplier
        3. Cap at 50% maximum (safety buffer)
        4. Floor at 15% minimum (very calm markets)
        
        Args:
            symbol: Stock symbol
            adx: Average Directional Index value (optional)
            rsi: Relative Strength Index value (optional)
            default_iv: Default IV if calculation not possible (default 25%)
            
        Returns:
            Dynamic IV as decimal (e.g., 0.25 for 25%)
        """
        # Check cache
        if symbol in self.iv_cache:
            age = (datetime.now() - self.last_update.get(symbol, datetime.now())).total_seconds()
            if age < 60:  # Cache valid for 60 seconds
                return self.iv_cache[symbol]
        
        # Calculate historical volatility
        hist_vol = self.calculate_historical_volatility(symbol)
        
        if hist_vol is None:
            # Not enough data, use default adjusted by market conditions
            multiplier = self.get_market_condition_multiplier(adx, rsi)
            iv = default_iv * multiplier
            logger.debug(f"VOLATILITY: NOT_ENOUGH_DATA | {symbol} | using default={default_iv:.0%} * {multiplier} = {iv:.0%}")
        else:
            # Apply market condition multiplier
            multiplier = self.get_market_condition_multiplier(adx, rsi)
            iv = hist_vol * multiplier
            logger.debug(f"VOLATILITY: CALCULATED | {symbol} | hist_vol={hist_vol:.0%} * multiplier={multiplier} = {iv:.0%}")
        
        # Apply safety bounds
        MIN_IV = 0.15  # 15% minimum (very calm markets)
        MAX_IV = 0.50  # 50% maximum (safety cap)
        
        iv = max(MIN_IV, min(MAX_IV, iv))
        
        # Cache result
        self.iv_cache[symbol] = iv
        self.last_update[symbol] = datetime.now()
        
        logger.info(f"VOLATILITY: FINAL_IV | {symbol} | {iv:.0%} (range: {MIN_IV:.0%}-{MAX_IV:.0%})")
        
        return iv
    
    def get_batch_iv(self, symbols: List[str], adx: Optional[float] = None, 
                    rsi: Optional[float] = None) -> Dict[str, float]:
        """
        Get IV for multiple symbols at once.
        
        Args:
            symbols: List of stock symbols
            adx: Market ADX value (applies to all)
            rsi: Market RSI value (applies to all)
            
        Returns:
            Dictionary of {symbol: iv}
        """
        return {symbol: self.get_dynamic_iv(symbol, adx, rsi) for symbol in symbols}
    
    def clear_symbol(self, symbol: str) -> None:
        """Clear price history for a symbol (e.g., on symbol removal)"""
        if symbol in self.price_history:
            del self.price_history[symbol]
        if symbol in self.iv_cache:
            del self.iv_cache[symbol]
        if symbol in self.last_update:
            del self.last_update[symbol]
        
        logger.debug(f"VOLATILITY: CLEARED | {symbol}")


# =============================================================================
# Global Volatility Calculator Instance
# =============================================================================

_volatility_calc: Optional[VolatilityCalculator] = None

def get_volatility_calculator() -> VolatilityCalculator:
    """Get or create global volatility calculator instance"""
    global _volatility_calc
    if _volatility_calc is None:
        _volatility_calc = VolatilityCalculator()
    return _volatility_calc
