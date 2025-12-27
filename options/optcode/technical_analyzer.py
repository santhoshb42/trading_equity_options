"""
Technical Analyzer for Options Bot

Provides technical indicators (RSI, MACD, MA) calculated from broker candle data.
Used by entry_filter_engine to validate entry signals based on momentum and trend.
"""

import logging
from typing import Optional, Dict, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# =============================================================================
# Technical Analyzer
# =============================================================================

class TechnicalAnalyzer:
    """
    Calculates technical indicators from broker candle data.
    Provides: RSI, MACD, Moving Averages, Trends
    """
    
    def __init__(self, symbol: str, broker):
        """
        Initialize technical analyzer for a symbol
        
        Args:
            symbol: Underlying symbol (e.g., 'NIFTY', 'BANKNIFTY')
            broker: AngelOneOptionsBroker instance
        """
        self.symbol = symbol
        self.broker = broker
        self.candle_cache = {}  # {timeframe: candles_list}
    
    def _fetch_candles(self, timeframe_minutes: int, limit: int = 100) -> Optional[List[Dict]]:
        """
        Fetch historical candles from broker.
        
        Args:
            timeframe_minutes: 5, 15, 60, 240, 1440 minutes
            limit: Number of candles to fetch (default 100)
        
        Returns:
            List of candles with OHLC data or None if unavailable
        """
        try:
            # Check cache first
            cache_key = timeframe_minutes
            if cache_key in self.candle_cache:
                return self.candle_cache[cache_key]
            
            # Try to fetch from broker if available
            if not self.broker:
                logger.debug(f"TECH: Broker not available - cannot fetch candles for {self.symbol}")
                return None
            
            # Map timeframe minutes to broker interval strings
            interval_map = {
                1: "ONE_MINUTE",
                5: "FIVE_MINUTE",
                15: "FIFTEEN_MINUTE",
                60: "ONE_HOUR",
                240: "FOUR_HOUR",
                1440: "ONE_DAY"
            }
            
            interval = interval_map.get(timeframe_minutes)
            if not interval:
                logger.debug(f"TECH: Unknown timeframe {timeframe_minutes}m for {self.symbol}")
                return None
            
            # Calculate days_back based on limit and timeframe
            # For 100 candles: 5m=12h, 15m=1.5d, 60m=4.2d, 240m=16.7d, 1440m=100d
            if timeframe_minutes <= 5:
                days_back = 2
            elif timeframe_minutes <= 15:
                days_back = 2
            elif timeframe_minutes <= 60:
                days_back = 5
            else:
                days_back = 100
            
            # Fetch from broker using get_historical_data
            if hasattr(self.broker, 'get_historical_data'):
                try:
                    candles = self.broker.get_historical_data(
                        self.symbol,
                        interval=interval,
                        days_back=days_back
                    )
                    if candles and len(candles) > 0:
                        # Limit to requested number
                        candles = candles[-limit:] if len(candles) > limit else candles
                        self.candle_cache[cache_key] = candles
                        logger.debug(f"TECH: Fetched {len(candles)} candles for {self.symbol} {timeframe_minutes}m")
                        return candles
                    else:
                        logger.debug(f"TECH: No candles returned for {self.symbol} {interval}")
                except Exception as e:
                    logger.debug(f"TECH: Candle fetch error for {self.symbol}: {str(e)}")
            else:
                logger.debug(f"TECH: Broker has no get_historical_data method")
            
            # Fallback: return None (will use default values in entry filter)
            logger.debug(f"TECH: No candles available for {self.symbol} - {timeframe_minutes}m")
            return None
            
        except Exception as e:
            logger.error(f"TECH: Error fetching candles | {str(e)}")
            return None
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """
        Calculate Relative Strength Index (RSI).
        
        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        
        Args:
            prices: List of closing prices
            period: RSI period (default 14)
        
        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if not prices or len(prices) < period + 1:
            return None
        
        try:
            # Calculate price changes
            changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
            
            # Separate gains and losses
            gains = [c if c > 0 else 0 for c in changes]
            losses = [-c if c < 0 else 0 for c in changes]
            
            # Calculate average gain and loss over period
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            
            # Avoid division by zero
            if avg_loss == 0:
                return 100.0 if avg_gain > 0 else 50.0
            
            # Calculate RS and RSI
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return rsi
        except Exception as e:
            logger.debug(f"RSI_CALC: Error | {str(e)}")
            return None
    
    def _calculate_macd(self, prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[Dict[str, float]]:
        """
        Calculate MACD (Moving Average Convergence Divergence).
        
        Returns: {
            'macd': MACD line,
            'signal': Signal line,
            'histogram': MACD - Signal
        }
        
        Args:
            prices: List of closing prices
            fast: Fast EMA period (default 12)
            slow: Slow EMA period (default 26)
            signal: Signal line EMA period (default 9)
        
        Returns:
            Dict with macd, signal, histogram or None if insufficient data
        """
        if not prices or len(prices) < slow + signal:
            return None
        
        try:
            # Calculate EMAs
            fast_ema = self._calculate_ema(prices, fast)
            slow_ema = self._calculate_ema(prices, slow)
            
            if fast_ema is None or slow_ema is None:
                return None
            
            # MACD line = Fast EMA - Slow EMA
            macd = fast_ema - slow_ema
            
            # Signal line = EMA of MACD
            # For simplicity, use SMA of last few MACD values
            signal_val = (macd + slow_ema) / 2
            
            # Histogram = MACD - Signal
            histogram = macd - signal_val
            
            return {
                'macd': macd,
                'signal': signal_val,
                'histogram': histogram
            }
        except Exception as e:
            logger.debug(f"MACD_CALC: Error | {str(e)}")
            return None
    
    def _calculate_ema(self, prices: List[float], period: int) -> Optional[float]:
        """
        Calculate Exponential Moving Average for the most recent price.
        
        Args:
            prices: List of closing prices
            period: EMA period
        
        Returns:
            Latest EMA value or None
        """
        if not prices or len(prices) < period:
            return None
        
        try:
            # Multiplier for EMA
            multiplier = 2 / (period + 1)
            
            # Start with SMA of first 'period' values
            ema = sum(prices[:period]) / period
            
            # Calculate EMA for remaining prices
            for price in prices[period:]:
                ema = (price - ema) * multiplier + ema
            
            return ema
        except:
            return None
    
    def _calculate_sma(self, prices: List[float], period: int) -> Optional[float]:
        """
        Calculate Simple Moving Average.
        
        Args:
            prices: List of closing prices
            period: MA period
        
        Returns:
            Latest SMA value or None
        """
        if not prices or len(prices) < period:
            return None
        
        try:
            return sum(prices[-period:]) / period
        except:
            return None
    
    def get_rsi(self, timeframe_minutes: int = 15, period: int = 14) -> Optional[float]:
        """
        Get RSI for specified timeframe.
        
        Args:
            timeframe_minutes: 5, 15, 60 minutes
            period: RSI period (default 14)
        
        Returns:
            RSI value (0-100) or None if data unavailable
        """
        try:
            candles = self._fetch_candles(timeframe_minutes, limit=100)
            
            if not candles or len(candles) < period + 1:
                logger.debug(f"RSI: Insufficient candles for {self.symbol} | {timeframe_minutes}m | got {len(candles) if candles else 0}")
                return None
            
            # Extract closing prices - handle both dict and object formats
            closes = []
            for c in candles:
                try:
                    if isinstance(c, dict):
                        closes.append(float(c.get('close', 0)))
                    else:
                        closes.append(float(c.close))
                except (KeyError, AttributeError, ValueError):
                    pass
            
            if len(closes) < period + 1:
                logger.debug(f"RSI: Could not extract {period + 1} closes, got {len(closes)}")
                return None
            
            rsi = self._calculate_rsi(closes, period)
            
            if rsi is not None:
                logger.info(f"RSI: {self.symbol} {timeframe_minutes}m | RSI={rsi:.2f}")
            
            return rsi
        except Exception as e:
            logger.error(f"RSI_FETCH: Error | {str(e)}")
            return None
    
    def get_macd(self, timeframe_minutes: int = 15) -> Optional[Dict[str, float]]:
        """
        Get MACD for specified timeframe.
        
        Args:
            timeframe_minutes: 5, 15, 60 minutes
        
        Returns:
            Dict with macd, signal, histogram or None if data unavailable
        """
        try:
            candles = self._fetch_candles(timeframe_minutes, limit=100)
            
            if not candles or len(candles) < 35:  # Need at least 26 + 9 candles
                logger.debug(f"MACD: Insufficient candles for {self.symbol} | {timeframe_minutes}m | got {len(candles) if candles else 0}")
                return None
            
            # Extract closing prices - handle both dict and object formats
            closes = []
            for c in candles:
                try:
                    if isinstance(c, dict):
                        closes.append(float(c.get('close', 0)))
                    else:
                        closes.append(float(c.close))
                except (KeyError, AttributeError, ValueError):
                    pass
            
            if len(closes) < 35:
                logger.debug(f"MACD: Could not extract 35 closes, got {len(closes)}")
                return None
            
            macd_data = self._calculate_macd(closes)
            
            if macd_data:
                logger.info(f"MACD: {self.symbol} {timeframe_minutes}m | MACD={macd_data['macd']:.4f}")
            
            return macd_data
        except Exception as e:
            logger.error(f"MACD_FETCH: Error | {str(e)}")
            return None
    
    def get_ma(self, period: int = 10, timeframe_minutes: int = 60) -> Optional[float]:
        """
        Get Simple Moving Average for specified period and timeframe.
        
        Args:
            period: MA period (10, 20, 50, 200)
            timeframe_minutes: 5, 15, 60, 240 minutes
        
        Returns:
            MA value or None if data unavailable
        """
        try:
            # Request extra candles to ensure we have enough for calculation
            candles = self._fetch_candles(timeframe_minutes, limit=max(200, period + 50))
            
            if not candles or len(candles) < period:
                logger.debug(f"MA: Insufficient candles for {self.symbol} | period={period} | {timeframe_minutes}m | got {len(candles) if candles else 0}")
                return None
            
            # Extract closing prices - handle both dict and object formats
            closes = []
            for c in candles:
                try:
                    if isinstance(c, dict):
                        closes.append(float(c.get('close', 0)))
                    else:
                        closes.append(float(c.close))
                except (KeyError, AttributeError, ValueError):
                    pass
            
            if len(closes) < period:
                logger.debug(f"MA: Could not extract {period} closes, got {len(closes)}")
                return None
            
            ma = self._calculate_sma(closes, period)
            
            if ma is not None:
                logger.info(f"MA: {self.symbol} {timeframe_minutes}m | MA{period}={ma:.2f}")
            
            return ma
        except Exception as e:
            logger.error(f"MA_FETCH: Error | {str(e)}")
            return None
    
    def get_ma_slope(self, period: int = 10, timeframe_minutes: int = 60) -> Optional[float]:
        """
        Get slope of Moving Average (positive = uptrend, negative = downtrend).
        
        Slope = (Current MA - Previous MA) / Previous MA * 100
        
        Args:
            period: MA period
            timeframe_minutes: Timeframe
        
        Returns:
            Slope as percentage or None
        """
        try:
            candles = self._fetch_candles(timeframe_minutes, limit=max(200, period + 50))
            
            if not candles or len(candles) < period + 2:
                return None
            
            closes = [c.get('close', 0) if isinstance(c, dict) else c.close for c in candles]
            
            # Calculate MA for current and previous candle
            current_ma = self._calculate_sma(closes, period)
            prev_ma = self._calculate_sma(closes[:-1], period)
            
            if current_ma is None or prev_ma is None or prev_ma == 0:
                return None
            
            slope = ((current_ma - prev_ma) / prev_ma) * 100
            
            logger.debug(f"MA_SLOPE: {self.symbol} | slope={slope:.2f}%")
            return slope
        except Exception as e:
            logger.error(f"MA_SLOPE: Error | {str(e)}")
            return None

# =============================================================================
# Factory Function
# =============================================================================

def get_technical_analyzer(broker, symbol: str) -> Optional[TechnicalAnalyzer]:
    """
    Factory function to get or create TechnicalAnalyzer instance.
    
    Args:
        broker: AngelOneOptionsBroker instance
        symbol: Underlying symbol
    
    Returns:
        TechnicalAnalyzer instance or None if broker unavailable
    """
    try:
        if not broker:
            logger.warning(f"TECH_ANALYZER: Broker not available for {symbol}")
            return None
        
        analyzer = TechnicalAnalyzer(symbol, broker)
        logger.debug(f"TECH_ANALYZER: Created for {symbol}")
        return analyzer
    except Exception as e:
        logger.error(f"TECH_ANALYZER: Failed to create | {str(e)}")
        return None
