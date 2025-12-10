"""
Complete Candle-Based Trading Bot Template
Integrates: Candle fetching + Indicators + Trading logic
Works for both Equity and Options
"""

import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from candle_fetcher import CandleFetcher
from indicators import IndicatorEngine

logger = logging.getLogger(__name__)


class Signal(Enum):
    """Trading signals"""
    BUY = "BUY"
    SELL = "SELL"
    STRONG_BUY = "STRONG_BUY"
    STRONG_SELL = "STRONG_SELL"
    NEUTRAL = "NEUTRAL"


@dataclass
class TradeSignal:
    """A trading signal with confidence and reasoning"""
    symbol: str
    exchange: str
    token: str
    signal: Signal
    confidence: float  # 0.0 to 1.0
    reasons: List[str]
    price: float
    timestamp: datetime
    indicators_snapshot: Dict
    
    def __repr__(self):
        return f"{self.signal.value} {self.symbol} @ ₹{self.price} (Confidence: {self.confidence:.0%})"


class CandleBot:
    """
    Complete candle-based trading bot
    
    Usage:
        bot = CandleBot(broker_api, smart_api)
        signals = bot.scan_symbols([("NSE", "3045"), ("NFO", "46294")])
        for signal in signals:
            if signal.confidence > 0.7:
                bot.execute_signal(signal)
    """
    
    def __init__(
        self,
        broker_api,
        smart_api,
        candle_interval: str = "FIVE_MINUTE",
        lookback_candles: int = 100
    ):
        """
        Args:
            broker_api: Angel One broker API instance
            smart_api: Angel One SmartAPI instance
            candle_interval: Interval for candle data
            lookback_candles: Historical candles to analyze
        """
        self.broker = broker_api
        self.smart_api = smart_api
        self.candle_interval = candle_interval
        self.lookback_candles = lookback_candles
        
        self.candle_fetcher = CandleFetcher(smart_api)
        self.indicator_engine = IndicatorEngine()
    
    # ========================================================================
    # CANDLE FETCHING
    # ========================================================================
    
    def get_latest_candles(
        self,
        exchange: str,
        token: str,
        num_candles: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        Fetch latest candles for analysis
        
        Args:
            exchange: "NSE", "NFO", etc.
            token: Instrument token
            num_candles: Number of candles (default: self.lookback_candles)
        
        Returns:
            DataFrame with candles and indicators
        """
        if num_candles is None:
            num_candles = self.lookback_candles
        
        df = self.candle_fetcher.fetch_latest_candles(
            exchange=exchange,
            token=token,
            interval=self.candle_interval,
            num_candles=num_candles
        )
        
        if df is None or len(df) == 0:
            logger.warning(f"No candles for {exchange}:{token}")
            return None
        
        # Compute indicators
        df = IndicatorEngine.compute_all_indicators(df)
        
        return df
    
    # ========================================================================
    # SIGNAL GENERATION
    # ========================================================================
    
    def analyze_symbol(
        self,
        exchange: str,
        token: str,
        symbol: str
    ) -> Optional[TradeSignal]:
        """
        Analyze a single symbol and generate trading signal
        
        Args:
            exchange: "NSE", "NFO"
            token: Instrument token
            symbol: Symbol name (for logging)
        
        Returns:
            TradeSignal or None
        """
        
        df = self.get_latest_candles(exchange, token)
        if df is None or len(df) < 20:
            logger.warning(f"Insufficient data for {symbol}")
            return None
        
        # Get latest candle
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        signal = Signal.NEUTRAL
        confidence = 0.0
        reasons = []
        
        # =====================================================================
        # EXAMPLE STRATEGY: Multi-factor signal
        # =====================================================================
        
        # 1. TREND: EMA crossover
        if pd.notna(latest['EMA20']) and pd.notna(latest['EMA50']):
            if latest['EMA20'] > latest['EMA50'] and prev['EMA20'] <= prev['EMA50']:
                signal = Signal.BUY
                confidence += 0.2
                reasons.append("EMA20 crossed above EMA50")
            elif latest['EMA20'] < latest['EMA50'] and prev['EMA20'] >= prev['EMA50']:
                signal = Signal.SELL
                confidence += 0.2
                reasons.append("EMA20 crossed below EMA50")
        
        # 2. MOMENTUM: RSI divergence
        if pd.notna(latest['RSI']):
            if latest['RSI'] < 30:
                signal = Signal.BUY
                confidence += 0.15
                reasons.append(f"RSI oversold ({latest['RSI']:.1f})")
            elif latest['RSI'] > 70:
                signal = Signal.SELL
                confidence += 0.15
                reasons.append(f"RSI overbought ({latest['RSI']:.1f})")
        
        # 3. VOLATILITY: Price breakout beyond Bollinger Bands
        if pd.notna(latest['BB_Upper']) and pd.notna(latest['BB_Lower']):
            if latest['close'] > latest['BB_Upper'] and prev['close'] <= prev['BB_Upper']:
                signal = Signal.BUY
                confidence += 0.15
                reasons.append("Price broke above Bollinger Band")
            elif latest['close'] < latest['BB_Lower'] and prev['close'] >= prev['BB_Lower']:
                signal = Signal.SELL
                confidence += 0.15
                reasons.append("Price broke below Bollinger Band")
        
        # 4. TREND STRENGTH: ADX strong trend
        if pd.notna(latest['ADX']):
            if latest['ADX'] > 25:
                confidence += 0.1
                reasons.append(f"Strong trend (ADX: {latest['ADX']:.1f})")
            elif latest['ADX'] < 20:
                signal = Signal.NEUTRAL
                confidence = 0.0
                reasons = ["Weak/ranging market"]
        
        # 5. VOLUME: Confirmation
        if len(df) > 1:
            avg_volume = df['volume'].tail(20).mean()
            if latest['volume'] > avg_volume * 1.5:
                confidence += 0.1
                reasons.append("High volume confirmation")
        
        # 6. SUPERTREND: Trend confirmation
        if pd.notna(latest['SuperTrend']):
            if latest['SuperTrend_Trend'] == 1 and signal == Signal.BUY:
                confidence += 0.15
                reasons.append("SuperTrend confirms uptrend")
            elif latest['SuperTrend_Trend'] == -1 and signal == Signal.SELL:
                confidence += 0.15
                reasons.append("SuperTrend confirms downtrend")
        
        # Cap confidence at 1.0
        confidence = min(confidence, 1.0)
        
        # Upgrade to STRONG if confidence high
        if confidence >= 0.75 and signal == Signal.BUY:
            signal = Signal.STRONG_BUY
        elif confidence >= 0.75 and signal == Signal.SELL:
            signal = Signal.STRONG_SELL
        
        # Create signal object
        trade_signal = TradeSignal(
            symbol=symbol,
            exchange=exchange,
            token=token,
            signal=signal,
            confidence=confidence,
            reasons=reasons,
            price=float(latest['close']),
            timestamp=datetime.now(),
            indicators_snapshot={
                'EMA20': float(latest['EMA20']) if pd.notna(latest['EMA20']) else None,
                'EMA50': float(latest['EMA50']) if pd.notna(latest['EMA50']) else None,
                'RSI': float(latest['RSI']) if pd.notna(latest['RSI']) else None,
                'ADX': float(latest['ADX']) if pd.notna(latest['ADX']) else None,
                'ATR': float(latest['ATR']) if pd.notna(latest['ATR']) else None,
                'Volume': float(latest['volume']) if pd.notna(latest['volume']) else None,
            }
        )
        
        return trade_signal
    
    def scan_symbols(
        self,
        symbols_list: List[Tuple[str, str, str]],
        min_confidence: float = 0.5
    ) -> List[TradeSignal]:
        """
        Scan multiple symbols for trading signals
        
        Args:
            symbols_list: List of (exchange, token, symbol) tuples
            min_confidence: Minimum confidence threshold
        
        Returns:
            List of TradeSignal objects
        """
        signals = []
        
        for exchange, token, symbol in symbols_list:
            try:
                signal = self.analyze_symbol(exchange, token, symbol)
                
                if signal and signal.confidence >= min_confidence:
                    signals.append(signal)
                    logger.info(f"Signal: {signal}")
            
            except Exception as e:
                logger.error(f"Error analyzing {symbol}: {e}")
        
        return signals
    
    # ========================================================================
    # EXECUTION
    # ========================================================================
    
    def execute_signal(
        self,
        signal: TradeSignal,
        quantity: int = 1,
        order_type: str = "MARKET"
    ) -> Optional[Dict]:
        """
        Execute trading signal (place order)
        
        Args:
            signal: TradeSignal to execute
            quantity: Order quantity
            order_type: "MARKET" or "LIMIT"
        
        Returns:
            Order response from broker
        """
        
        if signal.signal == Signal.NEUTRAL:
            logger.warning(f"Skipping neutral signal: {signal}")
            return None
        
        try:
            side = "BUY" if signal.signal in [Signal.BUY, Signal.STRONG_BUY] else "SELL"
            
            logger.info(f"Executing {side} order for {signal.symbol}")
            logger.info(f"  Reasons: {', '.join(signal.reasons)}")
            logger.info(f"  Confidence: {signal.confidence:.0%}")
            
            # Place order via broker
            order_response = self.broker.place_order(
                symbol=signal.symbol,
                exchange=signal.exchange,
                token=signal.token,
                side=side,
                quantity=quantity,
                order_type=order_type,
                price=signal.price if order_type == "LIMIT" else None
            )
            
            logger.info(f"Order placed: {order_response}")
            return order_response
        
        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            return None
    
    # ========================================================================
    # MONITORING & ANALYTICS
    # ========================================================================
    
    def get_symbol_analysis(
        self,
        exchange: str,
        token: str,
        symbol: str
    ) -> Optional[Dict]:
        """
        Get detailed analysis for a symbol
        
        Args:
            exchange: "NSE", "NFO"
            token: Instrument token
            symbol: Symbol name
        
        Returns:
            Dict with OHLC, indicators, levels
        """
        
        df = self.get_latest_candles(exchange, token)
        if df is None or len(df) == 0:
            return None
        
        latest = df.iloc[-1]
        
        return {
            'symbol': symbol,
            'exchange': exchange,
            'timestamp': datetime.now().isoformat(),
            'price': {
                'open': float(latest['open']),
                'high': float(latest['high']),
                'low': float(latest['low']),
                'close': float(latest['close']),
                'volume': float(latest['volume'])
            },
            'indicators': {
                'EMA20': float(latest['EMA20']) if pd.notna(latest['EMA20']) else None,
                'EMA50': float(latest['EMA50']) if pd.notna(latest['EMA50']) else None,
                'EMA200': float(latest['EMA200']) if pd.notna(latest['EMA200']) else None,
                'RSI': float(latest['RSI']) if pd.notna(latest['RSI']) else None,
                'MACD': float(latest['MACD']) if pd.notna(latest['MACD']) else None,
                'ADX': float(latest['ADX']) if pd.notna(latest['ADX']) else None,
                'ATR': float(latest['ATR']) if pd.notna(latest['ATR']) else None,
            },
            'support_resistance': {
                'bb_upper': float(latest['BB_Upper']) if pd.notna(latest['BB_Upper']) else None,
                'bb_middle': float(latest['BB_Middle']) if pd.notna(latest['BB_Middle']) else None,
                'bb_lower': float(latest['BB_Lower']) if pd.notna(latest['BB_Lower']) else None,
                'supertrend': float(latest['SuperTrend']) if pd.notna(latest['SuperTrend']) else None,
            },
            'trend': {
                'ema_trend': 'UP' if latest['EMA20'] > latest['EMA50'] else 'DOWN',
                'supertrend_trend': 'UP' if latest['SuperTrend_Trend'] == 1 else 'DOWN',
                'adx_strength': 'STRONG' if latest['ADX'] > 25 else 'WEAK',
            }
        }
    
    def get_portfolio_signals(
        self,
        watchlist: List[Tuple[str, str, str]],
        min_confidence: float = 0.6
    ) -> Dict:
        """
        Get all signals for a watchlist
        
        Args:
            watchlist: List of (exchange, token, symbol) tuples
            min_confidence: Minimum confidence
        
        Returns:
            Dict with buy signals, sell signals, etc.
        """
        
        signals = self.scan_symbols(watchlist, min_confidence)
        
        buy_signals = [s for s in signals if s.signal in [Signal.BUY, Signal.STRONG_BUY]]
        sell_signals = [s for s in signals if s.signal in [Signal.SELL, Signal.STRONG_SELL]]
        
        return {
            'timestamp': datetime.now().isoformat(),
            'total_scanned': len(watchlist),
            'buy_signals': buy_signals,
            'sell_signals': sell_signals,
            'strong_buys': [s for s in buy_signals if s.signal == Signal.STRONG_BUY],
            'strong_sells': [s for s in sell_signals if s.signal == Signal.STRONG_SELL],
        }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    """
    Example: How to use the CandleBot
    """
    
    logging.basicConfig(level=logging.INFO)
    
    # Initialize bot (assuming broker and smart_api are already authenticated)
    # bot = CandleBot(broker_api, smart_api, candle_interval="FIVE_MINUTE")
    
    # Scan equity symbols
    # equity_signals = bot.scan_symbols([
    #     ("NSE", "3045", "RELIANCE"),
    #     ("NSE", "881", "INFY"),
    #     ("NSE", "4963", "ICICIBANK"),
    # ])
    
    # Scan option symbols
    # option_signals = bot.scan_symbols([
    #     ("NFO", "46294", "BANKNIFTY 30-Dec 45500CE"),
    #     ("NFO", "46295", "BANKNIFTY 30-Dec 45600CE"),
    # ])
    
    # Execute high-confidence signals
    # for signal in equity_signals:
    #     if signal.confidence > 0.75:
    #         bot.execute_signal(signal, quantity=1)
    
    # Get detailed analysis
    # analysis = bot.get_symbol_analysis("NSE", "3045", "RELIANCE")
    # print(analysis)
    
    # Get portfolio signals
    # portfolio = bot.get_portfolio_signals([
    #     ("NSE", "3045", "RELIANCE"),
    #     ("NSE", "881", "INFY"),
    # ])
    # print(portfolio)
