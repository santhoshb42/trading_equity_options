"""
Technical Indicators Engine
Computes: EMA, RSI, ATR, ADX, SuperTrend, Bollinger Bands, MACD, Stochastic, etc.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class IndicatorEngine:
    """
    Compute technical indicators from OHLCV candle data
    All calculations done locally (Angel One only provides raw candles)
    """
    
    # ============================================================================
    # TREND INDICATORS
    # ============================================================================
    
    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """
        Exponential Moving Average
        
        Args:
            series: Price series (usually close)
            period: EMA period (e.g., 20, 50, 200)
        
        Returns:
            EMA values
        """
        return series.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """
        Simple Moving Average
        
        Args:
            series: Price series
            period: SMA period
        
        Returns:
            SMA values
        """
        return series.rolling(window=period).mean()
    
    @staticmethod
    def wma(series: pd.Series, period: int) -> pd.Series:
        """
        Weighted Moving Average
        
        Args:
            series: Price series
            period: WMA period
        
        Returns:
            WMA values
        """
        weights = np.arange(1, period + 1)
        return series.rolling(period).apply(
            lambda x: np.sum(x * weights) / np.sum(weights), 
            raw=False
        )
    
    # ============================================================================
    # MOMENTUM INDICATORS
    # ============================================================================
    
    @staticmethod
    def rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """
        Relative Strength Index (RSI)
        Measures strength of recent price movements
        
        Range: 0-100
        - < 30: Oversold
        - > 70: Overbought
        
        Args:
            close: Close price series
            period: RSI period (default 14)
        
        Returns:
            RSI values
        """
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        
        rs = avg_gain / avg_loss
        rsi_val = 100 - (100 / (1 + rs))
        
        return rsi_val
    
    @staticmethod
    def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD (Moving Average Convergence Divergence)
        
        Args:
            close: Close price series
            fast: Fast EMA period (default 12)
            slow: Slow EMA period (default 26)
            signal: Signal line EMA period (default 9)
        
        Returns:
            Tuple of (MACD line, Signal line, Histogram)
        """
        ema_fast = IndicatorEngine.ema(close, fast)
        ema_slow = IndicatorEngine.ema(close, slow)
        
        macd_line = ema_fast - ema_slow
        signal_line = IndicatorEngine.ema(macd_line, signal)
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    @staticmethod
    def stochastic(df: pd.DataFrame, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> Tuple[pd.Series, pd.Series]:
        """
        Stochastic Oscillator
        
        Range: 0-100
        - < 20: Oversold
        - > 80: Overbought
        
        Args:
            df: DataFrame with high, low, close
            period: Lookback period (default 14)
            smooth_k: K smoothing (default 3)
            smooth_d: D smoothing (default 3)
        
        Returns:
            Tuple of (K line, D line)
        """
        low_min = df['low'].rolling(window=period).min()
        high_max = df['high'].rolling(window=period).max()
        
        k = 100 * (df['close'] - low_min) / (high_max - low_min)
        k_smooth = k.rolling(window=smooth_k).mean()
        d_smooth = k_smooth.rolling(window=smooth_d).mean()
        
        return k_smooth, d_smooth
    
    # ============================================================================
    # VOLATILITY INDICATORS
    # ============================================================================
    
    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Average True Range (ATR)
        Measures market volatility
        
        Args:
            df: DataFrame with high, low, close
            period: ATR period (default 14)
        
        Returns:
            ATR values
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_val = tr.rolling(window=period).mean()
        
        return atr_val
    
    @staticmethod
    def bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bollinger Bands
        
        Args:
            close: Close price series
            period: SMA period (default 20)
            std_dev: Standard deviation multiplier (default 2.0)
        
        Returns:
            Tuple of (Upper band, Middle band, Lower band)
        """
        sma = IndicatorEngine.sma(close, period)
        std = close.rolling(window=period).std()
        
        upper_band = sma + (std_dev * std)
        lower_band = sma - (std_dev * std)
        
        return upper_band, sma, lower_band
    
    @staticmethod
    def keltner_channel(df: pd.DataFrame, period: int = 20, atr_period: int = 10, atr_mult: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Keltner Channel
        Similar to Bollinger Bands but based on ATR
        
        Args:
            df: DataFrame with high, low, close
            period: EMA period (default 20)
            atr_period: ATR period (default 10)
            atr_mult: ATR multiplier (default 2.0)
        
        Returns:
            Tuple of (Upper channel, Middle line, Lower channel)
        """
        mid = IndicatorEngine.ema(df['close'], period)
        atr = IndicatorEngine.atr(df, atr_period)
        
        upper = mid + (atr_mult * atr)
        lower = mid - (atr_mult * atr)
        
        return upper, mid, lower
    
    # ============================================================================
    # TREND DIRECTION INDICATORS
    # ============================================================================
    
    @staticmethod
    def adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Average Directional Index (ADX)
        Measures trend strength (not direction)
        
        Range: 0-100
        - < 25: Weak trend
        - > 50: Strong trend
        - > 75: Very strong trend
        
        Args:
            df: DataFrame with high, low, close
            period: ADX period (default 14)
        
        Returns:
            Tuple of (+DI, -DI, ADX)
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Directional movements
        plus_dm = high.diff()
        minus_dm = low.diff().abs()
        
        # Apply rules
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        
        # True range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Smoothed values
        atr_val = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).sum() / atr_val)
        minus_di = 100 * (minus_dm.rolling(period).sum() / atr_val)
        
        # ADX
        di_sum = plus_di + minus_di
        dx = (abs(plus_di - minus_di) / di_sum) * 100
        adx_val = dx.rolling(period).mean()
        
        return plus_di, minus_di, adx_val
    
    @staticmethod
    def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
        """
        SuperTrend Indicator
        Acts as both trend follower and support/resistance
        
        Args:
            df: DataFrame with high, low, close
            period: ATR period (default 10)
            multiplier: ATR multiplier (default 3.0)
        
        Returns:
            Tuple of (SuperTrend line, Trend direction 1=up, -1=down)
        """
        hl2 = (df['high'] + df['low']) / 2
        atr = IndicatorEngine.atr(df, period)
        
        basic_upper_band = hl2 + multiplier * atr
        basic_lower_band = hl2 - multiplier * atr
        
        # Final bands
        final_upper_band = basic_upper_band.copy()
        final_lower_band = basic_lower_band.copy()
        
        for i in range(1, len(df)):
            final_upper_band.iloc[i] = min(basic_upper_band.iloc[i], final_upper_band.iloc[i-1])
            final_lower_band.iloc[i] = max(basic_lower_band.iloc[i], final_lower_band.iloc[i-1])
        
        # SuperTrend
        supertrend = pd.Series(index=df.index, dtype='float64')
        trend = pd.Series(1, index=df.index)  # 1 = uptrend, -1 = downtrend
        
        for i in range(len(df)):
            if df['close'].iloc[i] <= final_upper_band.iloc[i]:
                supertrend.iloc[i] = final_upper_band.iloc[i]
                trend.iloc[i] = -1
            else:
                supertrend.iloc[i] = final_lower_band.iloc[i]
                trend.iloc[i] = 1
        
        return supertrend, trend
    
    # ============================================================================
    # VOLUME INDICATORS
    # ============================================================================
    
    @staticmethod
    def obv(df: pd.DataFrame) -> pd.Series:
        """
        On Balance Volume (OBV)
        Cumulative volume indicator
        
        Args:
            df: DataFrame with close and volume
        
        Returns:
            OBV values
        """
        obv = pd.Series(0.0, index=df.index)
        obv.iloc[0] = df['volume'].iloc[0]
        
        for i in range(1, len(df)):
            if df['close'].iloc[i] > df['close'].iloc[i-1]:
                obv.iloc[i] = obv.iloc[i-1] + df['volume'].iloc[i]
            elif df['close'].iloc[i] < df['close'].iloc[i-1]:
                obv.iloc[i] = obv.iloc[i-1] - df['volume'].iloc[i]
            else:
                obv.iloc[i] = obv.iloc[i-1]
        
        return obv
    
    @staticmethod
    def adl(df: pd.DataFrame) -> pd.Series:
        """
        Accumulation/Distribution Line
        Volume-weighted price indicator
        
        Args:
            df: DataFrame with high, low, close, volume
        
        Returns:
            ADL values
        """
        clv = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low'])
        ad_line = (clv * df['volume']).cumsum()
        
        return ad_line
    
    @staticmethod
    def cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """
        Chaikin Money Flow (CMF)
        Measures buying/selling pressure
        
        Args:
            df: DataFrame with high, low, close, volume
            period: CMF period (default 20)
        
        Returns:
            CMF values
        """
        clv = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low'])
        ad = clv * df['volume']
        cmf_val = ad.rolling(period).sum() / df['volume'].rolling(period).sum()
        
        return cmf_val
    
    # ============================================================================
    # COMPLETE INDICATOR SUITE
    # ============================================================================
    
    @staticmethod
    def compute_all_indicators(df: pd.DataFrame, config: Optional[Dict] = None) -> pd.DataFrame:
        """
        Compute all common indicators and add to DataFrame
        
        Args:
            df: DataFrame with OHLCV data
            config: Optional config dict with periods {ema: [20, 50], rsi: 14, ...}
        
        Returns:
            DataFrame with all indicators added
        """
        if config is None:
            config = {
                'ema_periods': [20, 50, 200],
                'rsi_period': 14,
                'macd': {'fast': 12, 'slow': 26, 'signal': 9},
                'stoch': {'period': 14, 'k': 3, 'd': 3},
                'atr_period': 14,
                'bb_period': 20,
                'adx_period': 14,
                'supertrend': {'period': 10, 'mult': 3.0},
                'obv': True,
                'cmf_period': 20
            }
        
        result = df.copy()
        
        # Trend indicators
        for period in config.get('ema_periods', [20, 50, 200]):
            result[f'EMA{period}'] = IndicatorEngine.ema(df['close'], period)
        
        # Momentum
        result['RSI'] = IndicatorEngine.rsi(df['close'], config.get('rsi_period', 14))
        
        macd_cfg = config.get('macd', {})
        macd_line, signal_line, histogram = IndicatorEngine.macd(
            df['close'],
            macd_cfg.get('fast', 12),
            macd_cfg.get('slow', 26),
            macd_cfg.get('signal', 9)
        )
        result['MACD'] = macd_line
        result['MACD_Signal'] = signal_line
        result['MACD_Hist'] = histogram
        
        # Volatility
        result['ATR'] = IndicatorEngine.atr(df, config.get('atr_period', 14))
        
        bb_cfg = config.get('bb_period', 20)
        upper, middle, lower = IndicatorEngine.bollinger_bands(df['close'], bb_cfg)
        result['BB_Upper'] = upper
        result['BB_Middle'] = middle
        result['BB_Lower'] = lower
        
        # Trend direction
        plus_di, minus_di, adx = IndicatorEngine.adx(df, config.get('adx_period', 14))
        result['+DI'] = plus_di
        result['-DI'] = minus_di
        result['ADX'] = adx
        
        supertrend_cfg = config.get('supertrend', {})
        st, trend = IndicatorEngine.supertrend(
            df,
            supertrend_cfg.get('period', 10),
            supertrend_cfg.get('mult', 3.0)
        )
        result['SuperTrend'] = st
        result['SuperTrend_Trend'] = trend
        
        # Volume
        if config.get('obv', True):
            result['OBV'] = IndicatorEngine.obv(df)
        
        result['CMF'] = IndicatorEngine.cmf(df, config.get('cmf_period', 20))
        
        return result
