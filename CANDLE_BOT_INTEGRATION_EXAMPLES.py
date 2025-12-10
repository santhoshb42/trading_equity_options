"""
EXAMPLE: Complete Integration of Candle Bot with Your Trading System
Demonstrates:
  1. Webhook integration with candle confirmation
  2. Monitor integration with candle-based exits
  3. Real-time portfolio monitoring
  4. Signal generation and execution
"""

from equity.eqcode.candle_bot import CandleBot, Signal
from equity.eqcode.candle_fetcher import CandleFetcher
from equity.eqcode.indicators import IndicatorEngine
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# EXAMPLE 1: Webhook Integration with Candle Confirmation
# =============================================================================

class WebhookHandlerWithCandleConfirmation:
    """
    Enhanced webhook handler that confirms entry with candle analysis
    Reduces false signals by validating with technical indicators
    """
    
    def __init__(self, broker_api, smart_api):
        self.broker = broker_api
        self.smart_api = smart_api
        self.bot = CandleBot(broker_api, smart_api, candle_interval="FIVE_MINUTE")
    
    def on_alert_received(self, alert_data):
        """
        Process incoming alert from webhook
        Confirm with candle analysis before placing order
        
        Args:
            alert_data: {
                'symbol': 'RELIANCE',
                'token': '3045',
                'exchange': 'NSE',
                'side': 'BUY',
                'alert_time': '2024-02-10 10:30:00'
            }
        """
        
        symbol = alert_data['symbol']
        token = alert_data['token']
        exchange = alert_data['exchange']
        side = alert_data['side']
        
        logger.info(f"🔔 Alert received: {side} {symbol} @ {alert_data['alert_time']}")
        
        # STEP 1: Get candle signal
        try:
            signal = self.bot.analyze_symbol(exchange, token, symbol)
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            signal = None
        
        # STEP 2: Validate with confidence threshold
        if signal is None:
            logger.warning(f"⚠️  No candle data for {symbol}, skipping")
            return False
        
        logger.info(f"📊 Candle analysis: {signal}")
        logger.info(f"   Confidence: {signal.confidence:.0%}")
        logger.info(f"   Reasons: {', '.join(signal.reasons)}")
        
        # STEP 3: Check if candle confirms the alert direction
        alert_matches_candle = (
            (side == "BUY" and signal.signal in [Signal.BUY, Signal.STRONG_BUY]) or
            (side == "SELL" and signal.signal in [Signal.SELL, Signal.STRONG_SELL])
        )
        
        if not alert_matches_candle:
            logger.warning(f"❌ Alert {side} doesn't match candle signal {signal.signal.value}")
            logger.warning(f"   Skipping to avoid false entry")
            return False
        
        # STEP 4: Check confidence threshold
        min_confidence = 0.6  # 60% minimum
        if signal.confidence < min_confidence:
            logger.warning(f"❌ Low confidence: {signal.confidence:.0%} < {min_confidence:.0%}")
            logger.warning(f"   Waiting for stronger signal")
            return False
        
        # STEP 5: Place order with high confidence
        logger.info(f"✅ Confirmed entry: {signal.confidence:.0%} confidence")
        
        try:
            order = self.broker.place_order(
                symbol=symbol,
                exchange=exchange,
                quantity=1,
                side=side,
                order_type="MARKET"
            )
            logger.info(f"✅ Order placed: {order}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to place order: {e}")
            return False


# =============================================================================
# EXAMPLE 2: Monitor Integration with Candle-Based Exit Logic
# =============================================================================

class PositionMonitorWithCandleExits:
    """
    Enhanced position monitor that uses candle analysis for smart exits
    Combines price levels with technical patterns
    """
    
    def __init__(self, broker_api, smart_api):
        self.broker = broker_api
        self.smart_api = smart_api
        self.bot = CandleBot(broker_api, smart_api, candle_interval="ONE_MINUTE")
    
    def should_exit_position(self, position):
        """
        Determine if position should be exited based on:
        1. Profit target (5%)
        2. Stop loss (2% or ₹500)
        3. Technical pattern break (SuperTrend, ADX)
        4. Momentum reversal (RSI > 70 for longs)
        
        Returns:
            (should_exit: bool, reason: str, price: float)
        """
        
        # Get latest candle with all indicators
        df = self.bot.get_latest_candles(
            position.exchange,
            position.token,
            num_candles=50
        )
        
        if df is None or len(df) == 0:
            logger.warning(f"No candle data for {position.symbol}")
            return False, "No data", position.current_price
        
        latest = df.iloc[-1]
        current_price = latest['close']
        
        # Calculate P&L
        if position.side == "LONG":
            pnl = current_price - position.entry_price
            pnl_pct = (pnl / position.entry_price) * 100
        else:
            pnl = position.entry_price - current_price
            pnl_pct = (pnl / position.entry_price) * 100
        
        # =====================================================================
        # EXIT RULE 1: PROFIT TARGET (5%)
        # =====================================================================
        if pnl_pct >= 5.0:
            return True, f"Profit target 5% reached ({pnl_pct:.2f}%)", current_price
        
        # =====================================================================
        # EXIT RULE 2: STOP LOSS (2% or ₹500)
        # =====================================================================
        if pnl_pct <= -2.0:
            return True, f"Stop loss 2% hit ({pnl_pct:.2f}%)", current_price
        
        if pnl <= -500:
            return True, f"Max loss ₹500 exceeded (₹{pnl:.0f})", current_price
        
        # =====================================================================
        # EXIT RULE 3: SUPERTREND BREAK (Trend reversal)
        # =====================================================================
        if pd.notna(latest['SuperTrend_Trend']):
            if position.side == "LONG" and latest['SuperTrend_Trend'] == -1:
                return True, "SuperTrend turned down (trend break)", current_price
            elif position.side == "SHORT" and latest['SuperTrend_Trend'] == 1:
                return True, "SuperTrend turned up (trend break)", current_price
        
        # =====================================================================
        # EXIT RULE 4: ADX WEAKNESS (Trend fading)
        # =====================================================================
        if pd.notna(latest['ADX']) and latest['ADX'] < 20:
            # Only if we're already in profit
            if pnl > 0:
                return True, f"Trend weakening (ADX: {latest['ADX']:.1f})", current_price
        
        # =====================================================================
        # EXIT RULE 5: MOMENTUM REVERSAL (RSI extreme)
        # =====================================================================
        if pd.notna(latest['RSI']):
            if position.side == "LONG" and latest['RSI'] > 75:
                # Exit some profit if overbought
                if pnl > 100:
                    return True, f"Overbought exit (RSI: {latest['RSI']:.1f})", current_price
            elif position.side == "SHORT" and latest['RSI'] < 25:
                # Exit some profit if oversold
                if pnl > 100:
                    return True, f"Oversold exit (RSI: {latest['RSI']:.1f})", current_price
        
        # =====================================================================
        # EXIT RULE 6: BOLLINGER BAND EXTREME
        # =====================================================================
        if pd.notna(latest['BB_Upper']) and pd.notna(latest['BB_Lower']):
            if position.side == "LONG" and current_price > latest['BB_Upper']:
                # At upper band = possibly overbought
                if latest['RSI'] > 70:
                    return True, "Bollinger Band + RSI extreme", current_price
            elif position.side == "SHORT" and current_price < latest['BB_Lower']:
                # At lower band = possibly oversold
                if latest['RSI'] < 30:
                    return True, "Bollinger Band + RSI extreme", current_price
        
        # No exit signal
        return False, "", current_price
    
    def monitor_positions(self, positions, check_interval_seconds=60):
        """
        Continuously monitor positions and execute exits
        
        Args:
            positions: List of Position objects
            check_interval_seconds: How often to check (default 60)
        """
        
        import time
        
        while True:
            for position in positions:
                should_exit, reason, price = self.should_exit_position(position)
                
                if should_exit:
                    logger.info(f"📉 EXIT SIGNAL: {position.symbol}")
                    logger.info(f"    Reason: {reason}")
                    logger.info(f"    Price: ₹{price:.2f}")
                    
                    # Place exit order
                    try:
                        exit_side = "SELL" if position.side == "LONG" else "BUY"
                        order = self.broker.place_order(
                            symbol=position.symbol,
                            side=exit_side,
                            quantity=position.quantity,
                            order_type="MARKET"
                        )
                        logger.info(f"✅ Exit executed: {order}")
                        positions.remove(position)
                        
                    except Exception as e:
                        logger.error(f"❌ Error exiting position: {e}")
            
            time.sleep(check_interval_seconds)


# =============================================================================
# EXAMPLE 3: Real-time Portfolio Monitoring & Analysis
# =============================================================================

class PortfolioAnalyzer:
    """
    Real-time portfolio analyzer with candle-based metrics
    """
    
    def __init__(self, broker_api, smart_api):
        self.broker = broker_api
        self.smart_api = smart_api
        self.bot = CandleBot(broker_api, smart_api)
    
    def get_portfolio_status(self, positions):
        """
        Get detailed portfolio status with candle analysis
        
        Returns:
            Dict with portfolio metrics and position analysis
        """
        
        portfolio = {
            'timestamp': datetime.now().isoformat(),
            'total_positions': len(positions),
            'positions': [],
            'summary': {
                'total_capital': 0,
                'total_pnl': 0,
                'total_pnl_pct': 0,
                'avg_rsi': 0,
                'avg_adx': 0,
                'strong_trends': 0,
            }
        }
        
        total_capital = 0
        total_pnl = 0
        rsi_values = []
        adx_values = []
        
        for position in positions:
            # Get candle analysis
            analysis = self.bot.get_symbol_analysis(
                position.exchange,
                position.token,
                position.symbol
            )
            
            if analysis is None:
                continue
            
            # Calculate P&L
            current_price = analysis['price']['close']
            if position.side == "LONG":
                pnl = (current_price - position.entry_price) * position.quantity
            else:
                pnl = (position.entry_price - current_price) * position.quantity
            
            total_capital += position.entry_price * position.quantity
            total_pnl += pnl
            
            # Collect indicator values
            if analysis['indicators']['RSI'] is not None:
                rsi_values.append(analysis['indicators']['RSI'])
            if analysis['indicators']['ADX'] is not None:
                adx_values.append(analysis['indicators']['ADX'])
            
            # Position detail
            position_detail = {
                'symbol': position.symbol,
                'side': position.side,
                'quantity': position.quantity,
                'entry_price': position.entry_price,
                'current_price': current_price,
                'pnl': pnl,
                'pnl_pct': (pnl / (position.entry_price * position.quantity)) * 100,
                'trend': analysis['trend']['ema_trend'],
                'strength': analysis['trend']['adx_strength'],
                'rsi': analysis['indicators']['RSI'],
                'adx': analysis['indicators']['ADX'],
            }
            portfolio['positions'].append(position_detail)
            
            if analysis['trend']['adx_strength'] == 'STRONG':
                portfolio['summary']['strong_trends'] += 1
        
        # Calculate portfolio summary
        if total_capital > 0:
            portfolio['summary']['total_capital'] = total_capital
            portfolio['summary']['total_pnl'] = total_pnl
            portfolio['summary']['total_pnl_pct'] = (total_pnl / total_capital) * 100
        
        if rsi_values:
            portfolio['summary']['avg_rsi'] = sum(rsi_values) / len(rsi_values)
        if adx_values:
            portfolio['summary']['avg_adx'] = sum(adx_values) / len(adx_values)
        
        return portfolio


# =============================================================================
# EXAMPLE 4: Signal Generation & Execution Loop
# =============================================================================

class AutomatedTradingBot:
    """
    Complete automated trading system
    Monitors watchlist → generates signals → executes orders → manages exits
    """
    
    def __init__(self, broker_api, smart_api, watchlist):
        """
        Args:
            broker_api: Angel One broker instance
            smart_api: SmartAPI instance
            watchlist: List of (exchange, token, symbol) tuples
        """
        self.broker = broker_api
        self.smart_api = smart_api
        self.watchlist = watchlist
        
        # Initialize components
        self.bot = CandleBot(broker_api, smart_api, candle_interval="FIVE_MINUTE")
        self.webhook_handler = WebhookHandlerWithCandleConfirmation(broker_api, smart_api)
        self.monitor = PositionMonitorWithCandleExits(broker_api, smart_api)
        self.analyzer = PortfolioAnalyzer(broker_api, smart_api)
        
        self.active_positions = []
    
    def scan_and_execute(self, min_confidence=0.7, max_positions=10):
        """
        Scan watchlist for signals and execute orders
        """
        
        logger.info(f"🔍 Scanning {len(self.watchlist)} symbols...")
        
        # Scan for signals
        signals = self.bot.scan_symbols(self.watchlist, min_confidence=min_confidence)
        
        logger.info(f"📊 Found {len(signals)} signals")
        
        # Execute high-confidence signals
        for signal in signals:
            # Skip if already at max positions
            if len(self.active_positions) >= max_positions:
                logger.warning(f"Max positions reached ({max_positions}), skipping {signal.symbol}")
                continue
            
            # Execute only if high confidence
            if signal.confidence >= 0.75:
                logger.info(f"✅ Executing: {signal}")
                
                try:
                    order = self.bot.execute_signal(signal, quantity=1)
                    
                    if order:
                        # Add to active positions
                        position = {
                            'symbol': signal.symbol,
                            'exchange': signal.exchange,
                            'token': signal.token,
                            'side': "BUY" if signal.signal == Signal.BUY else "SELL",
                            'entry_price': signal.price,
                            'quantity': 1,
                            'entry_time': datetime.now()
                        }
                        self.active_positions.append(position)
                        
                except Exception as e:
                    logger.error(f"Error executing signal: {e}")
    
    def run_continuous(self, scan_interval=300, monitor_interval=60):
        """
        Run continuous trading bot
        
        Args:
            scan_interval: Scan for new signals every N seconds (default 5 min)
            monitor_interval: Check positions every N seconds (default 1 min)
        """
        
        import threading
        import time
        
        # Scanning thread
        def scan_thread():
            while True:
                self.scan_and_execute()
                time.sleep(scan_interval)
        
        # Monitoring thread
        def monitor_thread():
            while True:
                # Check exits
                for position in self.active_positions[:]:
                    should_exit, reason, price = self.monitor.should_exit_position(position)
                    if should_exit:
                        logger.info(f"📉 Exiting {position['symbol']}: {reason}")
                        # Place exit order...
                        self.active_positions.remove(position)
                
                # Print portfolio status
                portfolio = self.analyzer.get_portfolio_status(self.active_positions)
                logger.info(f"📊 Portfolio: P&L ₹{portfolio['summary']['total_pnl']:.0f}")
                
                time.sleep(monitor_interval)
        
        # Start threads
        t1 = threading.Thread(target=scan_thread, daemon=True)
        t2 = threading.Thread(target=monitor_thread, daemon=True)
        
        t1.start()
        t2.start()
        
        logger.info("🚀 Trading bot started!")
        t1.join()  # Keep running


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

if __name__ == "__main__":
    """
    Example: Start the automated trading bot
    """
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Initialize (assuming broker and smart_api are already authenticated)
    # bot = AutomatedTradingBot(
    #     broker_api=your_broker_instance,
    #     smart_api=your_smart_api_instance,
    #     watchlist=[
    #         ("NSE", "3045", "RELIANCE"),
    #         ("NSE", "881", "INFY"),
    #         ("NSE", "4963", "ICICIBANK"),
    #         ("NFO", "46294", "BANKNIFTY 30Dec 45500CE"),
    #     ]
    # )
    
    # Run bot
    # bot.run_continuous(scan_interval=300, monitor_interval=60)
    
    pass
