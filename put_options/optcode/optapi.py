"""
Options Webhook API

Flask server for TradingView alerts integrated into options bot.
Completely independent from equity bot - shares only alert stream.
With extensive logging and alert system integration.
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import queue
import time
import traceback
from threading import RLock
import atexit

try:
    from flask import Flask, request, jsonify
except ImportError:
    Flask = None
    request = None
    def jsonify(obj):
        return obj

from .optconfig import WebhookConfig, OptionsTradingConfig, OptionsCapitalConfig
from .angelone_options import get_options_broker
from .optmonitor import get_option_monitor
from .optsignalvalidator import (
    OptionsSignalValidator, get_options_signal_filter
)
from .optlogging import logger, log_alert, log_signal_validation, log_event

# Entry filter engine integration
try:
    from .entry_filter_engine import get_entry_filter
    HAS_ENTRY_FILTER = True
except ImportError:
    HAS_ENTRY_FILTER = False
    get_entry_filter = None

# Learning engine integration
try:
    from .options_learning_engine import SymbolPerformanceTracker, get_symbol_tracker
    HAS_LEARNING_ENGINE = True
except ImportError:
    HAS_LEARNING_ENGINE = False
    SymbolPerformanceTracker = None
    get_symbol_tracker = None

# Alert system integration
try:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from alert_system import AlertManager, AlertLevel, AlertCategory
    ALERT_SYSTEM_AVAILABLE = True
except ImportError:
    ALERT_SYSTEM_AVAILABLE = False
    AlertManager = None
    AlertLevel = None
    AlertCategory = None

# Sector strength analyzer integration
try:
    from .sector_strength_analyzer import SectorStrengthAnalyzer
    HAS_SECTOR_ANALYZER = True
except ImportError:
    HAS_SECTOR_ANALYZER = False
    SectorStrengthAnalyzer = None

# =============================================================================
# Flask App Setup
# =============================================================================

def create_options_api_app():
    """Create Flask app for options webhook"""
    if Flask is None:
        print("⚠️ Flask not available - API disabled")
        return None
    
    app = Flask(__name__)
    app.config['JSON_SORT_KEYS'] = False
    
    # Options trading state
    from optcode.instrument_manager import get_instrument_manager
    from pathlib import Path
    
    # Initialize learning engine for ML trade recording
    learning_engine = None
    if HAS_LEARNING_ENGINE:
        try:
            # Use options-specific data directory for learning
            options_learning_file = Path(__file__).parent.parent / "data" / "learning" / "symbol_stats.json"
            learning_engine = get_symbol_tracker(options_learning_file)
            logger.info("API: LEARNING_ENGINE_INITIALIZED")
        except Exception as e:
            logger.warning(f"API: LEARNING_ENGINE_INIT_FAILED | {str(e)}")
    
    # Initialize sector analyzer for LIVE mode sector strength logging
    sector_analyzer = None
    if HAS_SECTOR_ANALYZER:
        try:
            sector_analyzer = SectorStrengthAnalyzer(broker=get_options_broker())
            logger.info("API: SECTOR_ANALYZER_INITIALIZED")
        except Exception as e:
            logger.warning(f"API: SECTOR_ANALYZER_INIT_FAILED | {str(e)}")
    
    state = {
        'broker': get_options_broker(),
        'monitor': get_option_monitor(),
        'signal_filter': get_options_signal_filter(),
        'instrument_manager': get_instrument_manager(),
        'learning_engine': learning_engine,
        'sector_analyzer': sector_analyzer,
        'entry_filter': None,  # Will be initialized below
        'alert_manager': None,  # Will be set by main bot
        'active': False,
        'startup_time': None
    }
    
    # Initialize entry filter if available
    if HAS_ENTRY_FILTER:
        try:
            state['entry_filter'] = get_entry_filter()
            logger.info("API: ENTRY_FILTER_INITIALIZED")
        except Exception as e:
            logger.warning(f"API: ENTRY_FILTER_INIT_FAILED | {str(e)}")
            state['entry_filter'] = None
    
    # Initialize alert manager if available
    if ALERT_SYSTEM_AVAILABLE:
        try:
            state['alert_manager'] = AlertManager()
        except Exception as e:
            logger.warning(f"API: ALERT_SYSTEM_INIT_FAILED | {str(e)}")
    
    # ==========================================================================
    # WEBHOOK ASYNC IMPLEMENTATION - Alert Queue & Background Worker
    # ==========================================================================
    
    # Alert queue for non-blocking webhook processing
    ALERT_QUEUE = queue.Queue(maxsize=1000)  # Max 1000 queued alerts
    WEBHOOK_WORKER_ACTIVE = True
    state_lock = RLock()  # Thread-safe access to state dict
    
    def webhook_alert_worker():
        """Background worker that processes alerts from queue"""
        logger.info("WEBHOOK_WORKER: Starting background alert worker thread")
        while WEBHOOK_WORKER_ACTIVE:
            try:
                # Wait for alert with timeout
                alert_data = ALERT_QUEUE.get(timeout=1)
                if alert_data is None:  # Shutdown signal
                    logger.info("WEBHOOK_WORKER: Received shutdown signal")
                    break
                
                alert, local_state = alert_data
                symbol = alert.get('symbol', 'UNKNOWN')
                
                # 🔧 NEW: Log at bot level - what we pulled from webhook queue
                logger.info(f"WEBHOOK_WORKER: Pulled from queue | symbol={symbol} | action={alert.get('action', '?')} | price={alert.get('price', '?')} | queue_remaining={ALERT_QUEUE.qsize()}")
                log_alert(alert=alert, status='bot_processing_started', details={
                    'queue_remaining': ALERT_QUEUE.qsize()
                })
                
                try:
                    # Process the alert (this may take 30+ seconds)
                    start_time = time.time()
                    result = _process_options_alert(alert, local_state)
                    elapsed = time.time() - start_time
                    result_status = result.get('status', 'unknown') if isinstance(result, dict) else 'unknown'
                    
                    # 🔧 NEW: Log processing result
                    logger.info(f"WEBHOOK_WORKER: Alert processed | symbol={symbol} | status={result_status} | elapsed_ms={elapsed*1000:.1f}")
                    log_alert(
                        alert=alert,
                        status='bot_processing_completed',
                        details=_build_alert_completion_details(alert, result, elapsed * 1000)
                    )
                except Exception as e:
                    elapsed = time.time() - start_time
                    logger.error(f"WEBHOOK_WORKER: Alert processing failed | symbol={symbol} | error={str(e)} | elapsed_ms={elapsed*1000:.1f}", exc_info=True)
                    log_alert(alert=alert, status='bot_processing_error', details={
                        'error': str(e),
                        'elapsed_ms': round(elapsed * 1000, 2),
                        'attempted_symbol': alert.get('symbol'),
                        'alert_action': alert.get('action'),
                        'alert_price': alert.get('price'),
                    })
            except queue.Empty:
                # Normal timeout waiting for next alert
                continue
            except Exception as e:
                logger.error(f"WEBHOOK_WORKER: Fatal error | {str(e)}", exc_info=True)
                break
        
        logger.info("WEBHOOK_WORKER: Background worker thread stopped")
    
    # Start background worker thread (daemon=False for graceful shutdown)
    try:
        _worker_thread = threading.Thread(target=webhook_alert_worker, daemon=False, name="WebhookAlertWorker")
        _worker_thread.start()
        logger.info("API: Background webhook worker thread started")
    except Exception as e:
        logger.error(f"API: Failed to start webhook worker thread | {str(e)}")
    
    def shutdown_webhook_worker():
        """Stop the background worker thread gracefully"""
        global WEBHOOK_WORKER_ACTIVE
        WEBHOOK_WORKER_ACTIVE = False
        try:
            ALERT_QUEUE.put(None)  # Send shutdown signal
            logger.info("WEBHOOK_WORKER: Shutdown signal sent")
        except:
            pass
    
    # Register shutdown handler
    atexit.register(shutdown_webhook_worker)
    
    # ==========================================================================
    # Health Check Endpoint
    # ==========================================================================
    
    @app.route('/health', methods=['GET'])
    def health():
        """Health check endpoint"""
        summary = state['monitor'].get_position_summary()
        
        return jsonify({
            'status': 'healthy' if state['active'] else 'initializing',
            'service': 'options_bot',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': (datetime.now() - state['startup_time']).total_seconds() if state['startup_time'] else 0,
            'broker_status': 'connected' if state['broker'].authenticated else 'disconnected',
            'open_positions': summary['open_positions'],
            'unrealized_pnl': summary['total_unrealized_pnl'],
            'capital': {
                'max': OptionsCapitalConfig.MAX_CAPITAL,
                'per_trade': OptionsCapitalConfig.CAP_PER_TRADE,
                'max_slots': OptionsCapitalConfig.MAX_SLOTS
            },
            'mode': OptionsTradingConfig.TRADING_MODE
        }), 200
    
    # ==========================================================================
    # Options Webhook Endpoint
    # ==========================================================================
    
    @app.route(WebhookConfig.ENDPOINT, methods=['POST'])
    def options_webhook():
        """
        NON-BLOCKING webhook endpoint for options trading signals.
        
        CRITICAL FIX: Returns immediately to avoid TradingView timeouts.
        Alert processing happens in background worker thread.
        
        This implements the correct async pattern:
        - Receive alert
        - Queue for processing
        - Return 202 Accepted immediately
        - Process in background (non-blocking)
        
        This prevents:
        - TradingView timeouts (no response in 5-10s)
        - Duplicate alerts (from TradingView retry)
        - System overload (non-blocking processing)
        """
        try:
            logger.debug(f"WEBHOOK: Received request | remote_addr={request.remote_addr}")
            
            # Parse request
            data = request.get_json()
            if not data:
                logger.warning("WEBHOOK: Empty request body")
                return jsonify({'error': 'Empty request body'}), 400
            
            # 🔧 NEW: Log raw webhook data from TradingView at source
            log_alert(alert=data, status='received', details={'source': 'tradingview'})
            
            # Extract alert(s)
            alerts = data if isinstance(data, list) else [data]
            logger.info(f"WEBHOOK: Received {len(alerts)} alert(s) | raw_data={json.dumps(data)[:200]}")
            
            # CIRCUIT BREAKER: Check broker API rate limiter
            try:
                from .bulk_order_fetcher import rate_limiter
                rate_util = getattr(rate_limiter, 'utilization_percent', 0) if rate_limiter else 0
                if rate_util > 95:
                    import os
                    recovery_file = '/root/santhosh/trading/put_options/data/alert_recovery_queue.jsonl'
                    os.makedirs(os.path.dirname(recovery_file), exist_ok=True)
                    with open(recovery_file, 'a') as f:
                        f.write(json.dumps({'timestamp': time.time(), 'data': data}) + '\n')
                    logger.warning(f"WEBHOOK: CIRCUIT_BREAKER | rate_util={rate_util:.1f}% | queued to disk")
                    return jsonify({
                        'status': 'queued_to_disk',
                        'message': 'high_load_backoff',
                        'count': len(alerts)
                    }), 202
            except Exception as cb_err:
                logger.warning(f"WEBHOOK: Circuit breaker check failed | proceeding")
            
            # ✅ CRITICAL FIX: Queue alerts for background processing
            # Return IMMEDIATELY WITHOUT waiting for processing
            queued_count = 0
            for alert in alerts:
                try:
                    # Non-blocking queue with timeout
                    ALERT_QUEUE.put((alert, state), timeout=1)
                    queued_count += 1
                    symbol = alert.get('symbol', 'UNKNOWN')
                    logger.debug(f"WEBHOOK: Alert queued | symbol={symbol} | action={alert.get('action', '?')} | price={alert.get('price', '?')}")
                    # 🔧 NEW: Log parsed alert at bot level
                    log_alert(alert=alert, status='queued', details={'bot_level': True})
                except queue.Full:
                    logger.warning(f"WEBHOOK: Alert queue full | symbol={alert.get('symbol')} | dropped")
                    log_alert(alert=alert, status='dropped', details={'reason': 'queue_full'})
                    # Queue is full - alert will be lost, but we return success to avoid TradingView resend
            
            # ✅ IMMEDIATE RESPONSE to TradingView (before any processing!)
            logger.info(f"WEBHOOK: Returning immediately | queued={queued_count}/{len(alerts)} | response_time=<100ms")
            return jsonify({
                'status': 'accepted',
                'message': f'Queued {queued_count} alert(s) for background processing',
                'queued': queued_count,
                'total': len(alerts)
            }), 202  # 202 Accepted = processing asynchronously
        
        except Exception as e:
            logger.error(f"WEBHOOK: ERROR | {str(e)}", exc_info=True)
            return jsonify({
                'error': str(e)
            }), 500
    
    # ==========================================================================
    # Position Summary Endpoint
    # ==========================================================================
    
    @app.route('/positions', methods=['GET'])
    def get_positions():
        """Get all open option positions"""
        try:
            summary = state['monitor'].get_position_summary()
            return jsonify(summary), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    # ==========================================================================
    # Signal Statistics Endpoint
    # ==========================================================================
    
    @app.route('/stats', methods=['GET'])
    def get_stats():
        """Get signal validation statistics"""
        try:
            stats = state['signal_filter'].get_statistics()
            return jsonify(stats), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    # ==========================================================================
    # Market Data Endpoints (NEW)
    # ==========================================================================
    
    @app.route('/market/ltp/<symbol>', methods=['GET'])
    def get_ltp_endpoint(symbol: str):
        """Get LTP for a symbol (options contract or underlying)"""
        try:
            exchange = request.args.get('exchange', 'NFO')
            ltp = state['broker'].get_ltp(symbol, exchange)
            
            # Log broker data fetch
            if state['alert_manager']:
                try:
                    state['alert_manager'].alert_daily_pnl(
                        bot_type='options',
                        pnl_data={
                            'metric': 'LTP Fetch',
                            'symbol': symbol,
                            'ltp': ltp
                        }
                    )
                except Exception as e:
                    logger.warning(f"LTP_ALERT: FAILED | {str(e)}")
            
            if ltp is not None:
                return jsonify({
                    'symbol': symbol,
                    'ltp': ltp,
                    'exchange': exchange,
                    'timestamp': datetime.now().isoformat()
                }), 200
            else:
                return jsonify({'error': 'LTP not available'}), 404
        except Exception as e:
            logger.error(f"LTP_ENDPOINT: ERROR | {symbol} | {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/market/data/<symbol>', methods=['GET'])
    def get_market_data_endpoint(symbol: str):
        """Get comprehensive market data for a symbol"""
        try:
            exchange = request.args.get('exchange', 'NFO')
            data = state['broker'].get_market_data(symbol, exchange)
            
            # Log broker data fetch
            if state['alert_manager'] and data:
                try:
                    state['alert_manager'].alert_daily_pnl(
                        bot_type='options',
                        pnl_data={
                            'metric': 'Market Data Fetch',
                            'symbol': symbol,
                            'ltp': data.get('ltp', 0)
                        }
                    )
                except Exception as e:
                    logger.warning(f"MARKET_DATA_ALERT: FAILED | {str(e)}")
            
            if data:
                return jsonify({
                    'symbol': symbol,
                    'exchange': exchange,
                    'data': data
                }), 200
            else:
                return jsonify({'error': 'Market data not available'}), 404
        except Exception as e:
            logger.error(f"MARKET_DATA_ENDPOINT: ERROR | {symbol} | {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/market/indicators/<symbol>', methods=['GET'])
    def get_indicators_endpoint(symbol: str):
        """Get technical indicators (RSI, ATR) for underlying symbol"""
        try:
            exchange = request.args.get('exchange', 'NSE')
            period_rsi = int(request.args.get('rsi_period', 14))
            period_atr = int(request.args.get('atr_period', 14))
            
            indicators = state['broker'].calculate_technical_indicators(
                symbol, exchange, period_rsi, period_atr
            )
            
            # Log technical indicator calculation
            if state['alert_manager'] and indicators:
                try:
                    state['alert_manager'].alert_daily_pnl(
                        bot_type='options',
                        pnl_data={
                            'metric': 'Technical Indicators',
                            'symbol': symbol,
                            'rsi': indicators.get('rsi', 0)
                        }
                    )
                except Exception as e:
                    logger.warning(f"INDICATORS_ALERT: FAILED | {str(e)}")
            
            if indicators:
                return jsonify({
                    'symbol': symbol,
                    'exchange': exchange,
                    'indicators': indicators
                }), 200
            else:
                return jsonify({'error': 'Indicators not available'}), 404
        except Exception as e:
            logger.error(f"INDICATORS_ENDPOINT: ERROR | {symbol} | {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    # ==========================================================================
    # Market Sentiment Endpoints (PCR + OI Buildup)
    # ==========================================================================
    
    @app.route('/market/sentiment', methods=['GET'])
    def get_market_sentiment_endpoint():
        """Get overall market sentiment (PCR aggregate, bullish/bearish breakdown)"""
        try:
            from .market_sentiment import get_market_sentiment
            
            sentiment_engine = get_market_sentiment(state['broker'])
            sentiment_summary = sentiment_engine.get_market_sentiment_summary()
            
            logger.info(f"MARKET_SENTIMENT: overall={sentiment_summary.get('overall_sentiment')}")
            
            return jsonify(sentiment_summary), 200
        except Exception as e:
            logger.error(f"MARKET_SENTIMENT_ENDPOINT: ERROR | {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/market/sentiment/<symbol>', methods=['GET'])
    def get_symbol_sentiment_endpoint(symbol: str):
        """Get detailed sentiment data for specific symbol (PCR, OI Buildup, entry/exit signals)"""
        try:
            from .market_sentiment import get_market_sentiment
            
            sentiment_engine = get_market_sentiment(state['broker'])
            sentiment_data = sentiment_engine.get_symbol_sentiment(symbol)
            
            logger.info(f"SYMBOL_SENTIMENT: {symbol} | entry={sentiment_data['entry_signal']['ok']} | exit={sentiment_data['exit_signal']['triggered']}")
            
            return jsonify(sentiment_data), 200
        except Exception as e:
            logger.error(f"SYMBOL_SENTIMENT_ENDPOINT: ERROR | {symbol} | {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/market/pcr', methods=['GET'])
    def get_pcr_endpoint():
        """Get Put-Call Ratio for all symbols"""
        try:
            from .market_sentiment import get_market_sentiment
            
            sentiment_engine = get_market_sentiment(state['broker'])
            pcr_data = sentiment_engine.fetch_pcr_ratio()
            
            if not pcr_data:
                return jsonify({'error': 'No PCR data available'}), 404
            
            # Calculate statistics
            pcr_values = list(pcr_data.values())
            avg_pcr = sum(pcr_values) / len(pcr_values) if pcr_values else 0
            
            bullish = sum(1 for pcr in pcr_values if pcr < 0.8)
            neutral = sum(1 for pcr in pcr_values if 0.8 <= pcr < 1.2)
            bearish = sum(1 for pcr in pcr_values if pcr >= 1.2)
            
            return jsonify({
                'timestamp': datetime.now().isoformat(),
                'total_symbols': len(pcr_data),
                'average_pcr': avg_pcr,
                'statistics': {
                    'bullish': bullish,
                    'neutral': neutral,
                    'bearish': bearish
                },
                'top_bullish': sorted(pcr_data.items(), key=lambda x: x[1])[:5],
                'top_bearish': sorted(pcr_data.items(), key=lambda x: x[1], reverse=True)[:5],
                'all_data': pcr_data
            }), 200
        except Exception as e:
            logger.error(f"PCR_ENDPOINT: ERROR | {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/market/oi-buildup', methods=['GET'])
    def get_oi_buildup_endpoint():
        """Get OI Buildup data (Long/Short buildup, covering, unwinding)"""
        try:
            from .market_sentiment import get_market_sentiment
            
            buildup_type = request.args.get('type', 'Long Built Up')
            expiry = request.args.get('expiry', 'NEAR')
            
            sentiment_engine = get_market_sentiment(state['broker'])
            buildup_data = sentiment_engine.fetch_oi_buildup(buildup_type=buildup_type, expiry_type=expiry)
            
            if not buildup_data:
                return jsonify({'error': f'No OI Buildup data for {buildup_type}'}), 404
            
            # Sort by OI change
            sorted_data = sorted(
                buildup_data.items(),
                key=lambda x: x[1].get('oi_change', 0),
                reverse=True
            )
            
            return jsonify({
                'timestamp': datetime.now().isoformat(),
                'buildup_type': buildup_type,
                'expiry_type': expiry,
                'total_symbols': len(buildup_data),
                'top_buildup': sorted_data[:10],
                'all_data': buildup_data
            }), 200
        except Exception as e:
            logger.error(f"OI_BUILDUP_ENDPOINT: ERROR | {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/square-off', methods=['POST'])
    def square_off_all_positions():
        """
        Square off all open options positions at 3:12 PM (EOD).
        
        Enhanced Logic:
        1. Close ALL open positions (no exceptions)
        2. Avoid EOD market volatility/pressure (3:12 PM = 18 min before close)
        3. Lock in profits and losses before 3:30 PM expiry
        4. Free up capital for next day
        5. Detect and force-close stagnant positions (0% gain for 24+ hours)
        
        Stagnant Position Detection:
        - Positions with ~0% gain (±0.1%) = likely illiquid/stuck
        - Held for 24+ hours = confirmed stagnant (e.g., INFY30DEC251840CE from Dec 15)
        - These are closed at best available bid even if at minor loss
        - Marked in logs as "STAGNANT" for tracking
        
        Called at: 3:12 PM (15:12 IST) daily via cron job
        
        Returns:
            {
                "status": "success",
                "message": "All positions squared off",
                "positions_closed": N,
                "positions_stagnant_closed": M,
                "total_pnl": X.XX,
                "timestamp": "..."
            }
        """
        try:
            from datetime import datetime, timedelta
            
            log_event("EOD_SQUARE_OFF", "🛑 Attempting to square off all options positions at 3:12 PM")
            
            if not state.get('broker'):
                return jsonify({"error": "Broker not initialized"}), 503
            
            monitor = state.get('monitor')
            if not monitor:
                return jsonify({"error": "Monitor not initialized"}), 503
            
            # Get all active positions from monitor pool
            all_positions = monitor.get_all_positions()
            
            # CRITICAL: Filter to only ACTIVE positions (not already closed)
            # Closed positions have exit_premium and exit_time set
            positions_to_close = [
                pos for pos in all_positions 
                if not pos.get('exit_premium') and not pos.get('exit_time')
            ]
            
            # VERIFICATION: Check with broker to confirm positions
            broker_verification = state['broker'].verify_positions_with_broker()
            broker_open_positions = broker_verification.get('net_positions', {})
            
            log_event("EOD_SQUARE_OFF_START", 
                     f"Starting EOD square-off | Internal active: {len(positions_to_close)} | "
                     f"Broker open: {len(broker_open_positions)} | "
                     f"Already closed (filtered): {len(all_positions) - len(positions_to_close)}")
            
            if not positions_to_close:
                log_event("EOD_SQUARE_OFF_COMPLETE", "✅ No open positions to close")
                return jsonify({
                    "status": "success",
                    "message": "No open positions to close",
                    "positions_closed": 0,
                    "positions_stagnant_closed": 0,
                    "total_pnl": 0,
                    "timestamp": datetime.now().isoformat()
                }), 200
            
            # Analyze and close positions
            total_pnl = 0
            closed_count = 0
            stagnant_count = 0
            errors = []
            current_time = datetime.now()
            
            for pos in positions_to_close:
                try:
                    symbol = pos.get('symbol', 'UNKNOWN')
                    contract = pos.get('contract', '')
                    quantity = pos.get('quantity', 0)
                    entry_premium = pos.get('entry_premium', 0)
                    
                    # 🔴 CRITICAL FIX: Use current_premium from position dict (updated by monitor)
                    # This ensures EOD squareoff uses LIVE market price, not stale data
                    # Fallback hierarchy: current_premium > current_ltp > broker LTP > entry_premium
                    current_ltp = pos.get('current_premium')  # Primary source - updated by monitor
                    
                    if not current_ltp:
                        # Try alternate field names
                        current_ltp = pos.get('current_ltp')
                    
                    if not current_ltp:
                        # Try to fetch live from broker
                        try:
                            live_ltp = state['broker'].get_ltp(symbol, 'NFO')
                            current_ltp = float(live_ltp) if live_ltp else None
                        except:
                            pass
                    
                    # Last resort: use entry_premium (means no update available)
                    if not current_ltp:
                        current_ltp = entry_premium
                        logger.warning(f"EOD_SQUAREOFF: {symbol} | No LTP available, using entry_premium={entry_premium}")
                    
                    entry_time_str = pos.get('entry_time')
                    
                    # Calculate gain percentage
                    gain_percent = 0
                    if entry_premium > 0:
                        gain_percent = ((current_ltp - entry_premium) / entry_premium) * 100
                    
                    # Detect stagnant positions (0% gain for 24+ hours)
                    is_stagnant = False
                    hours_held = None
                    if entry_time_str and abs(gain_percent) < 0.1:  # Almost 0% gain
                        try:
                            # Parse entry time and calculate hold duration
                            if 'T' in entry_time_str:
                                entry_time = datetime.fromisoformat(entry_time_str.split('T')[0] + 'T' + entry_time_str.split('T')[1].split('.')[0])
                            else:
                                entry_time = datetime.fromisoformat(entry_time_str)
                            hours_held = (current_time - entry_time).total_seconds() / 3600
                            # Stagnant if: same price for 24+ hours = illiquid/stuck
                            if hours_held >= 24:
                                is_stagnant = True
                        except Exception as time_err:
                            logger.debug(f"Could not parse entry time for {symbol}: {str(time_err)}")
                            pass
                    
                    # Use monitor.close_position() as the single exit path.
                    # In LIVE mode close_position() cancels the SL order first, then places the
                    # SELL with 5-attempt retry — calling broker.place_options_order() directly
                    # here as well caused a double SELL on the broker in LIVE mode.
                    pnl_result = monitor.close_position(symbol, current_ltp, "EOD_SQUAREOFF")

                    if pnl_result:
                        # Use PnL returned by close_position (correctly accounts for quantity/lot size)
                        pnl = pnl_result.get('pnl', (current_ltp - entry_premium) * quantity)
                        total_pnl += pnl
                        closed_count += 1

                        if is_stagnant:
                            stagnant_count += 1
                            log_event("EOD_STAGNANT_POSITION_CLOSED", 
                                     f"🔴 Force-closed stagnant position: {symbol} | Gain: {gain_percent:.2f}% | Held: {hours_held:.1f}h | PnL: ₹{pnl:.2f}",
                                     symbol=symbol, contract=contract, gain_percent=round(gain_percent, 2), 
                                     hours_held=round(hours_held, 1) if hours_held else None, pnl=round(pnl, 2))
                            logger.warning(f"EOD_STAGNANT_CLOSE: {symbol} | {gain_percent:.2f}% gain in {hours_held:.1f}h | PnL: ₹{pnl:.2f}")
                        else:
                            log_event("EOD_POSITION_CLOSED", 
                                     f"✅ Closed {symbol} | Gain: {gain_percent:.2f}% | PnL: ₹{pnl:.2f}",
                                     symbol=symbol, contract=contract, gain_percent=round(gain_percent, 2), pnl=round(pnl, 2))
                    else:
                        errors.append(f"{symbol}: Failed to close position")
                        log_event("EOD_POSITION_CLOSE_FAILED", f"❌ Failed to close {symbol}",
                                 symbol=symbol, contract=contract)
                except Exception as pos_err:
                    errors.append(f"{pos.get('symbol', 'UNKNOWN')}: {str(pos_err)}")
                    logger.error(f"EOD_POSITION_CLOSE_ERROR: {pos.get('symbol')} | {str(pos_err)}")
            
            log_event("EOD_SQUARE_OFF_COMPLETE", 
                     f"✅ EOD square-off complete | Active closed: {closed_count}/{len(positions_to_close)} | Stagnant: {stagnant_count} | Already closed: {len(all_positions) - len(positions_to_close)} | PnL: ₹{total_pnl:.2f}",
                     closed=closed_count, stagnant=stagnant_count, total=len(positions_to_close), 
                     already_closed=len(all_positions) - len(positions_to_close), pnl=round(total_pnl, 2))
            
            # CLEANUP: Remove closed positions from the positions dictionary and save
            try:
                closed_symbols = []
                for symbol in list(monitor.positions.keys()):
                    position = monitor.positions[symbol]
                    # If position has exit_premium or exit_time, it's closed - remove it
                    if hasattr(position, 'exit_premium') and position.exit_premium is not None:
                        closed_symbols.append(symbol)
                        del monitor.positions[symbol]
                    elif hasattr(position, 'exit_time') and position.exit_time is not None:
                        closed_symbols.append(symbol)
                        del monitor.positions[symbol]
                
                # Save cleaned positions to JSON
                if closed_symbols:
                    monitor._save_positions()
                    log_event("EOD_CLEANUP_COMPLETE", 
                             f"🧹 Cleaned up {len(closed_symbols)} closed positions from tracking | Removed: {', '.join(closed_symbols[:3])}{'...' if len(closed_symbols) > 3 else ''}",
                             removed_count=len(closed_symbols),
                             removed_symbols=closed_symbols)
                    logger.info(f"EOD_CLEANUP: Removed {len(closed_symbols)} closed positions from tracking")
            except Exception as cleanup_err:
                logger.warning(f"EOD_CLEANUP: Error cleaning positions | {str(cleanup_err)}")
            
            return jsonify({
                "status": "success",
                "message": f"✅ Squared off {closed_count}/{len(positions_to_close)} positions at 3:12 PM",
                "positions_closed": closed_count,
                "positions_stagnant_closed": stagnant_count,
                "positions_total": len(positions_to_close),
                "broker_verified_open": len(broker_open_positions),
                "broker_positions": broker_open_positions,
                "positions_remaining": len(monitor.positions),  # After cleanup
                "total_pnl": round(total_pnl, 2),
                "errors": errors if errors else None,
                "timestamp": datetime.now().isoformat()
            }), 200
        except Exception as e:
            logger.error(f"EOD_SQUARE_OFF_ERROR: {str(e)}")
            log_event("EOD_SQUARE_OFF_ERROR", f"❌ Square-off failed: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"❌ Square-off failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }), 500
    
    # Store state for access in routes
    app.options_state = state
    
    return app

# =============================================================================
# Alert Processing
# =============================================================================

def _compact_alert_details(details: Dict[str, Any]) -> Dict[str, Any]:
    """Drop empty values before writing alert summaries."""
    return {key: value for key, value in details.items() if value is not None}


def _build_alert_completion_details(alert: Dict[str, Any], result: Any, elapsed_ms: float) -> Dict[str, Any]:
    """Create a precise final alert summary for alerts.jsonl."""
    details = {
        'result_status': result.get('status', 'unknown') if isinstance(result, dict) else 'unknown',
        'elapsed_ms': round(elapsed_ms, 2),
        'attempted_symbol': alert.get('symbol'),
        'alert_action': alert.get('action'),
        'alert_price': alert.get('price'),
    }

    if not isinstance(result, dict):
        return _compact_alert_details(details)

    details.update(_compact_alert_details({
        'decision_stage': result.get('stage'),
        'underlying': result.get('underlying'),
        'normalized_action': result.get('normalized_action'),
        'attempted_contract': result.get('contract'),
        'contract_type': result.get('contract_type'),
        'strike': result.get('strike'),
        'expiry': result.get('expiry'),
        'entry_premium': result.get('entry_premium'),
        'quantity': result.get('quantity'),
        'actual_cost': result.get('actual_cost'),
        'budget': result.get('budget'),
        'order_id': result.get('order_id'),
        'trades_today': result.get('trades_today'),
        'max_trades': result.get('max_trades'),
        'open_positions': result.get('open_positions'),
        'max_slots': result.get('max_slots'),
        'message': result.get('message'),
        'reason': result.get('reason') or result.get('error'),
        'liquidity_metrics': result.get('liquidity_metrics'),
        'filter_details': result.get('filter_details'),
        'data_fetch_status': result.get('data_fetch_status'),
    }))

    return details


# Neural ML Signal - DISABLED due to system constraints
# Disabled neural ML to reduce memory footprint and simplify alert processing
# Kept as separate file for future use with proper integration

def _process_options_alert(alert: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """Process single options alert with detailed logging"""
    try:
        timestamp = datetime.now().isoformat()
        symbol = alert.get('symbol', 'UNKNOWN')
        
        # Initialize neural ML signal as None (disabled)
        neural_ml_signal = None
        neural_ml_multiplier = 1.0
        
        logger.debug(f"ALERT_PROCESS: START | symbol={symbol} | action={alert.get('action')}")
        
        # CRITICAL: Check broker session health BEFORE processing alert
        # This prevents alerts from being silently dropped due to Invalid Token
        state['broker']._detect_and_fix_invalid_token()
        
        # Validate signal
        is_valid, processed, reason = state['signal_filter'].validate(alert)
        
        if not is_valid:
            logger.warning(f"ALERT_PROCESS: REJECTED | symbol={symbol} | reason={reason}")
            # 🔧 NEW: Log validation rejection at bot level
            log_alert(alert=alert, status='validation_rejected', details={'reason': reason})
            log_signal_validation(symbol, False, reason)
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': reason
            }
        
        logger.debug(f"ALERT_PROCESS: VALIDATED | symbol={symbol}")
        # 🔧 NEW: Log validation passed at bot level
        log_alert(alert=alert, status='signal_validated', details={'symbol': symbol})
        
        # Authorized to process
        symbol = processed['symbol']
        underlying = processed['underlying']
        action = processed['action']
        alert_price = float(alert.get('price', 0) or 0)

        # If no price in alert, fetch live spot price from broker for correct ATM selection
        # (critical for large-value indices like SENSEX where wrong ATM = wrong contract)
        if alert_price <= 0:
            try:
                broker = state['broker']
                cash_exch = broker._get_underlying_cash_exchange(underlying) if hasattr(broker, '_get_underlying_cash_exchange') else 'NSE'
                spot = broker.get_ltp(underlying, cash_exch)
                if spot and spot > 0:
                    alert_price = spot
                    logger.debug(f"ALERT_PROCESS: SPOT_FETCHED | {underlying} | spot=₹{alert_price:.2f} (no price in alert)")
            except Exception as _e:
                logger.debug(f"ALERT_PROCESS: SPOT_FETCH_FAILED | {underlying} | {_e} | proceeding with alert_price=0")

        base_context = {
            'underlying': underlying,
            'normalized_action': action,
            'alert_price': alert_price if alert_price > 0 else None,
        }
        
        logger.debug(f"ALERT_PROCESS: MAPPED | underlying={underlying} | action={action}")

        market_trend = str(alert.get('market_trend', 'NEUTRAL')).strip().upper()
        
        # NEW: Check market sentiment (PCR + OI Buildup) for entry decision
        from .market_sentiment import get_market_sentiment
        from .optconfig import SentimentConfig
        
        if SentimentConfig.ENABLE_SENTIMENT_FILTER:
            try:
                sentiment_engine = get_market_sentiment(state['broker'])
                entry_ok, entry_reason = sentiment_engine.check_entry_signal(underlying)
                
                logger.info(f"SENTIMENT_CHECK: {underlying} | entry_ok={entry_ok} | {entry_reason}")
                
                # HARD BLOCKING: Reject entries if sentiment conditions are poor
                # This prevents trading during unfavorable market conditions (high PCR, no OI buildup, etc)
                if not entry_ok:
                    # Distinguish between "data not ready" vs "actual poor conditions"
                    if "PCR data not available" in entry_reason or "PCR=None" in entry_reason:
                        # Data temporarily unavailable (market open, data lag) - still reject
                        # to be conservative and wait for proper market data
                        logger.error(f"ALERT_PROCESS: REJECTED_NO_DATA | symbol={symbol} | {entry_reason}")
                        return {
                            'symbol': symbol,
                            'timestamp': timestamp,
                            'status': 'rejected',
                            'reason': f'PCR data not available - waiting for market data',
                            'stage': 'sentiment_check',
                            **base_context,
                        }
                    else:
                        # Actual poor market condition (PCR too extreme, no buildup, etc)
                        logger.error(f"ALERT_PROCESS: REJECTED_SENTIMENT | symbol={symbol} | {entry_reason}")
                        return {
                            'symbol': symbol,
                            'timestamp': timestamp,
                            'status': 'rejected',
                            'reason': f'Poor market conditions: {entry_reason}',
                            'stage': 'sentiment_check',
                            **base_context,
                        }
                
                # Entry passed sentiment check - log the good conditions
                if SentimentConfig.LOG_SENTIMENT_CHECKS:
                    sentiment_data = sentiment_engine.get_symbol_sentiment(underlying)
                    logger.info(f"SENTIMENT_DATA: {underlying} | pcr={sentiment_data.get('pcr')} | buildup={sentiment_data.get('oi_long_buildup')} | entry_approved")
            
            except Exception as e:
                # On sentiment check errors, log but allow the trade to proceed
                # (PCR data may be temporarily unavailable from broker)
                logger.warning(f"SENTIMENT_CHECK: WARNING | {underlying} | {str(e)} | proceeding with trade (sentiment data unavailable)")
                # Don't reject - allow trading even if sentiment data is missing
                pass
        
        cap_this_trade = OptionsCapitalConfig.get_cap_for_market_trend(market_trend)
        if cap_this_trade == 0.0:
            logger.warning(
                f"ALERT_PROCESS: BAD_MARKET_TREND_REJECTED | symbol={symbol} | market_trend={market_trend}"
            )
            log_alert(alert=alert, status='bad_market_trend_rejected', details={'market_trend': market_trend})
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'BAD market trend - no new PE entries',
                'stage': 'market_trend_gate',
                'market_trend': market_trend,
                **base_context,
            }

        # Fetch option chain
        available_capital = OptionsCapitalConfig.get_available_capital(0)
        if available_capital < cap_this_trade:
            logger.warning(f"ALERT_PROCESS: INSUFFICIENT_CAPITAL | symbol={symbol} | available={available_capital:.2f}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Insufficient capital',
                'stage': 'capital_check',
                'budget': cap_this_trade,
                **base_context,
            }
        
        logger.debug(f"ALERT_PROCESS: CAPITAL_OK | available=₹{available_capital:.2f}")
        
        # ========================================================================
        # CHECK DAILY TRADES LIMIT (HARDCODED 30 TRADES MAX PER DAY)
        # ========================================================================
        daily_trade_count = OptionsCapitalConfig.get_daily_trade_count()
        if daily_trade_count >= OptionsCapitalConfig.MAX_TRADES_PER_DAY:
            logger.warning(f"ALERT_PROCESS: DAILY_LIMIT_REACHED | symbol={symbol} | trades_today={daily_trade_count} | max={OptionsCapitalConfig.MAX_TRADES_PER_DAY}")
            # 🔧 NEW: Log daily limit rejection at bot level
            log_alert(alert=alert, status='daily_limit_rejected', details={
                'trades_today': daily_trade_count,
                'max_trades': OptionsCapitalConfig.MAX_TRADES_PER_DAY
            })
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': f'Daily trade limit reached ({daily_trade_count}/{OptionsCapitalConfig.MAX_TRADES_PER_DAY})',
                'stage': 'daily_trade_limit',
                'trades_today': daily_trade_count,
                'max_trades': OptionsCapitalConfig.MAX_TRADES_PER_DAY,
                **base_context,
            }
        
        logger.debug(f"ALERT_PROCESS: DAILY_LIMIT_OK | trades_today={daily_trade_count}/{OptionsCapitalConfig.MAX_TRADES_PER_DAY}")
        # 🔧 NEW: Log daily limit passed at bot level
        log_alert(alert=alert, status='daily_limit_passed', details={
            'trades_today': daily_trade_count,
            'max_trades': OptionsCapitalConfig.MAX_TRADES_PER_DAY
        })

        pct_limit = OptionsCapitalConfig.DAILY_LOSS_LIMIT_PCT
        min_trades = OptionsCapitalConfig.DAILY_CB_MIN_TRADES
        if pct_limit > 0:
            live = OptionsCapitalConfig.get_today_live_summary()
            trades_today = live['trades_today']
            total_pnl_percent = live['total_pnl_percent']
            total_pnl = live['total_pnl']
            budget_used = live['budget_used']
            if trades_today >= min_trades and total_pnl_percent <= -pct_limit:
                logger.warning(
                    f"ALERT_PROCESS: DAILY_LOSS_CIRCUIT_BREAKER | symbol={symbol} | total_pnl_percent={total_pnl_percent:.2f}%"
                )
                log_alert(alert=alert, status='circuit_breaker_rejected', details={
                    'total_pnl': round(total_pnl, 2),
                    'total_pnl_percent': round(total_pnl_percent, 2),
                    'limit_pct': pct_limit,
                    'budget_used': round(budget_used, 2),
                    'trades_today': trades_today,
                })
                return {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'status': 'rejected',
                    'reason': (
                        f'Daily loss circuit breaker: {total_pnl_percent:.2f}% ≤ -{pct_limit}% '
                        f'(₹{total_pnl:.0f} on ₹{budget_used:.0f} deployed)'
                    ),
                    'stage': 'daily_loss_circuit_breaker',
                    'budget': round(budget_used, 2),
                    **base_context,
                }
        
        # Check position slots
        summary = state['monitor'].get_position_summary()
        if summary['open_positions'] >= OptionsCapitalConfig.MAX_SLOTS:
            logger.warning(f"ALERT_PROCESS: MAX_POSITIONS_REACHED | symbol={symbol} | open={summary['open_positions']}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Max positions reached',
                'stage': 'position_slot_check',
                'open_positions': summary['open_positions'],
                'max_slots': OptionsCapitalConfig.MAX_SLOTS,
                **base_context,
            }
        
        logger.debug(f"ALERT_PROCESS: SLOTS_OK | open={summary['open_positions']}/{OptionsCapitalConfig.MAX_SLOTS}")
        
        # Fetch option chain with automatic re-auth on Invalid Token errors
        logger.debug(f"ALERT_PROCESS: FETCHING_CHAIN | underlying={underlying}")
        expiry = state['broker'].get_next_expiry(underlying)
        
        # Try to fetch chain with exponential backoff retry logic
        import time
        chain = None
        max_retries = 3
        retry_delays = [1, 2, 4]  # exponential backoff: 1s, 2s, 4s
        
        for attempt in range(max_retries):
            chain = state['broker'].fetch_option_chain(underlying, expiry, current_price=alert_price if alert_price > 0 else None)
            
            if chain:
                if attempt > 0:
                    logger.info(f"ALERT_PROCESS: CHAIN_FETCH_SUCCESS_AFTER_RETRY | underlying={underlying} | attempts={attempt+1}")
                break
            
            # Chain fetch failed, attempt retry (no artificial delays - just retry immediately)
            if attempt < max_retries - 1:
                logger.warning(f"ALERT_PROCESS: CHAIN_FETCH_FAILED | underlying={underlying} | attempt={attempt+1}/{max_retries} | retrying immediately")
                # ✅ FIX: Removed blocking time.sleep() call
                # Just continue to next retry iteration without artificial delays
            
            # On last attempt, try re-authentication
            if attempt == max_retries - 2:  # Second-to-last attempt
                if state['broker']._handle_invalid_token_error():
                    logger.info(f"ALERT_PROCESS: RE_AUTHENTICATED | attempting final chain fetch")
        
        if not chain:
            logger.error(f"ALERT_PROCESS: CHAIN_FAILED | underlying={underlying} | expiry={expiry} | all_retries_exhausted")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Failed to fetch option chain',
                'stage': 'option_chain_fetch',
                'expiry': expiry,
                **base_context,
            }
        
        logger.debug(f"ALERT_PROCESS: CHAIN_OK | contracts={len(chain.contracts)} | atm={chain.atm_strike}")
        
        # NEW: Comprehensive Entry Filter (PCR + Momentum + Trend + IV + Market Hours + DTE)
        if state['entry_filter']:
            try:
                # REAL DATA FETCH FROM BROKER - No bullshit fallback
                from .technical_analyzer import get_technical_analyzer
                from .market_sentiment import get_market_sentiment
                
                # Initialize data collection
                broker = state['broker']
                market_data = {}
                fetch_results = {}

                # 0. ENTRY PREMIUM (PE bot should use nearest ATM PE premium)
                try:
                    pe_contracts = [c for c in chain.contracts.values() if c.contract_type == 'PE' and c.ltp > 0]
                    if pe_contracts:
                        spot_price = float(chain.atm_strike or alert_price or 0)
                        nearest_pe = min(pe_contracts, key=lambda c: abs(c.strike - spot_price))
                        market_data['entry_premium'] = nearest_pe.ltp
                        logger.debug(
                            f"ENTRY_FILTER: entry_premium from nearest ATM PE | {underlying} | "
                            f"strike={nearest_pe.strike} | ltp=₹{nearest_pe.ltp:.2f}"
                        )
                    else:
                        market_data['entry_premium'] = 0
                        logger.debug(f"ENTRY_FILTER: no PE contracts with ltp>0 in chain | {underlying}")
                except Exception as e:
                    market_data['entry_premium'] = 0
                    logger.debug(f"ENTRY_FILTER: entry_premium error | {underlying} | {str(e)[:40]}")
                
                # 1. MARKET SENTIMENT (PCR + OI)
                try:
                    sentiment_engine = get_market_sentiment(broker)
                    sentiment = sentiment_engine.get_symbol_sentiment(underlying)
                    market_data['pcr'] = sentiment.get('pcr')
                    market_data['oi_buildup'] = sentiment.get('oi_long_buildup')
                    fetch_results['sentiment'] = 'OK' if market_data['pcr'] else 'NO_DATA'
                    if market_data['pcr']:
                        logger.debug(f"ENTRY_FILTER: PCR fetched | {underlying} | PCR={market_data['pcr']:.2f}")
                except Exception as e:
                    # Safe error message without dict formatting issues - truncate to prevent braces
                    error_str = str(e)[:40].replace('{', '[').replace('}', ']')
                    fetch_results['sentiment'] = f'SENTIMENT_ERROR'
                    market_data['pcr'] = None
                    market_data['oi_buildup'] = None
                    logger.warning(f"ENTRY_FILTER: PCR fetch failed | {underlying} | {error_str}")
                
                # 2. TECHNICAL INDICATORS (RSI, MACD, MA)
                try:
                    tech_analyzer = get_technical_analyzer(broker, underlying)
                    if tech_analyzer:
                        # RSI
                        try:
                            market_data['rsi_15m'] = tech_analyzer.get_rsi(15)
                            if market_data['rsi_15m']:
                                logger.debug(f"ENTRY_FILTER: RSI fetched | {underlying} | RSI={market_data['rsi_15m']:.2f}")
                                fetch_results['rsi'] = 'OK'
                            else:
                                fetch_results['rsi'] = 'NO_DATA'
                        except Exception as e:
                            fetch_results['rsi'] = 'RSI_ERROR'
                            market_data['rsi_15m'] = None
                            logger.debug(f"ENTRY_FILTER: RSI error | {underlying} | {str(e)}")
                        
                        # MACD
                        try:
                            market_data['macd_15m'] = tech_analyzer.get_macd(15)
                            if market_data['macd_15m']:
                                logger.debug(f"ENTRY_FILTER: MACD fetched | {underlying}")
                                fetch_results['macd'] = 'OK'
                            else:
                                fetch_results['macd'] = 'NO_DATA'
                        except Exception as e:
                            fetch_results['macd'] = 'MACD_ERROR'
                            market_data['macd_15m'] = None
                            logger.debug(f"ENTRY_FILTER: MACD error | {underlying} | {str(e)}")
                        
                        # MA 10 (short)
                        try:
                            market_data['ma_short'] = tech_analyzer.get_ma(10, 60)
                            if market_data['ma_short']:
                                logger.debug(f"ENTRY_FILTER: MA10 fetched | {underlying} | MA10={market_data['ma_short']:.2f}")
                                fetch_results['ma_short'] = 'OK'
                            else:
                                fetch_results['ma_short'] = 'NO_DATA'
                        except Exception as e:
                            fetch_results['ma_short'] = 'MA10_ERROR'
                            market_data['ma_short'] = None
                            logger.debug(f"ENTRY_FILTER: MA10 error | {underlying}")
                        
                        # MA 20 (long)
                        try:
                            market_data['ma_long'] = tech_analyzer.get_ma(20, 60)
                            if market_data['ma_long']:
                                logger.debug(f"ENTRY_FILTER: MA20 fetched | {underlying} | MA20={market_data['ma_long']:.2f}")
                                fetch_results['ma_long'] = 'OK'
                            else:
                                fetch_results['ma_long'] = 'NO_DATA'
                        except Exception as e:
                            fetch_results['ma_long'] = 'MA20_ERROR'
                            market_data['ma_long'] = None
                            logger.debug(f"ENTRY_FILTER: MA20 error | {underlying}")
                        
                        # Slope
                        try:
                            market_data['slope'] = tech_analyzer.get_ma_slope(10, 60)
                            if market_data['slope'] is not None:
                                logger.debug(f"ENTRY_FILTER: Slope fetched | {underlying} | slope={market_data['slope']:.4f}")
                                fetch_results['slope'] = 'OK'
                            else:
                                fetch_results['slope'] = 'NO_DATA'
                        except Exception as e:
                            fetch_results['slope'] = 'SLOPE_ERROR'
                            market_data['slope'] = None
                    else:
                        fetch_results['tech'] = 'ANALYZER_NOT_AVAILABLE'
                        market_data['rsi_15m'] = None
                        market_data['macd_15m'] = None
                        market_data['ma_short'] = None
                        market_data['ma_long'] = None
                        market_data['slope'] = None
                except Exception as e:
                    fetch_results['tech'] = 'TECH_ANALYZER_ERROR'
                    market_data['rsi_15m'] = None
                    market_data['macd_15m'] = None
                    market_data['ma_short'] = None
                    market_data['ma_long'] = None
                    market_data['slope'] = None
                    logger.warning(f"ENTRY_FILTER: Tech analyzer error | {underlying} | {str(e)}")
                
                # 3. OPTION CHAIN DATA (IV, DTE)
                try:
                    if hasattr(chain, 'get_iv_percentile'):
                        market_data['iv_percentile'] = chain.get_iv_percentile()
                        if market_data['iv_percentile'] is not None:
                            logger.debug(f"ENTRY_FILTER: IV fetched | {underlying} | IV={market_data['iv_percentile']:.1f}%")
                            fetch_results['iv'] = 'OK'
                        else:
                            fetch_results['iv'] = 'NO_DATA'
                    else:
                        market_data['iv_percentile'] = None
                        fetch_results['iv'] = 'METHOD_NOT_AVAILABLE'
                except Exception as e:
                    fetch_results['iv'] = f'ERROR: {str(e)[:30]}'
                    market_data['iv_percentile'] = None
                    logger.debug(f"ENTRY_FILTER: IV error | {underlying} | {str(e)}")
                
                try:
                    if hasattr(chain, 'get_days_to_expiry'):
                        market_data['days_to_expiry'] = chain.get_days_to_expiry()
                        if market_data['days_to_expiry'] is not None:
                            logger.debug(f"ENTRY_FILTER: DTE fetched | {underlying} | DTE={market_data['days_to_expiry']}")
                            fetch_results['dte'] = 'OK'
                        else:
                            fetch_results['dte'] = 'NO_DATA'
                    else:
                        market_data['days_to_expiry'] = None
                        fetch_results['dte'] = 'METHOD_NOT_AVAILABLE'
                except Exception as e:
                    fetch_results['dte'] = f'ERROR: {str(e)[:30]}'
                    market_data['days_to_expiry'] = None
                    logger.debug(f"ENTRY_FILTER: DTE error | {underlying} | {str(e)}")
                
                # Log what we got
                data_available = sum(1 for v in market_data.values() if v is not None)
                logger.info(f"ENTRY_FILTER: DATA_FETCH | {underlying} | collected {data_available}/9 data points | status: {fetch_results}")
                
                # Build entry signal with validated confidence from signal filter
                entry_signal = {
                    'symbol': symbol,
                    'action': action,
                    'confidence': processed.get('confidence', 0.5)
                }
                
                # Validate entry with whatever data we have
                is_entry_valid, entry_reason, entry_details = state['entry_filter'].validate(entry_signal, market_data)
                
                if not is_entry_valid:
                    logger.warning(f"ENTRY_FILTER: REJECTED | symbol={symbol} | action={action} | reason={entry_reason} | data_collected={data_available}")
                    return {
                        'symbol': symbol,
                        'timestamp': timestamp,
                        'status': 'rejected',
                        'reason': entry_reason,
                        'stage': 'entry_filter',
                        'filter_details': entry_details,
                        'data_fetch_status': fetch_results,
                        **base_context,
                    }
                
                validators_passed = entry_details.get('validators_passed', 0)
                logger.info(f"ENTRY_FILTER: PASSED | symbol={symbol} | action={action} | validators_passed={validators_passed} | data_collected={data_available}")
            
            except Exception as e:
                # Log error but continue (don't block entry on filter error)
                logger.error(f"ENTRY_FILTER: EXCEPTION | symbol={symbol} | {str(e)} | continuing anyway")
                # Don't reject - let the trade proceed if other checks pass
        
        # Get ATM contracts with offset
        # Use alert's price as current price for ATM calculation (already extracted above)
        contract_type = processed['recommended_contract']
        
        # VALIDATION: Check if fetched chain has strikes to cover alert price
        # For the PUT bot, validate against PE strikes because we always buy PE contracts.
        # If alert price is outside the available PE strike range, refresh the chain.
        if chain and hasattr(chain, 'contracts') and chain.contracts:
            pe_strikes = []
            for contract in chain.contracts.values():
                if contract.contract_type == 'PE':
                    pe_strikes.append(contract.strike)
            
            if pe_strikes and alert_price > 0:
                min_strike = min(pe_strikes)
                max_strike = max(pe_strikes)
                
                # If alert price is outside the available range, fetch fresh chain
                if alert_price < min_strike or alert_price > max_strike:
                    gap = max(alert_price - max_strike, min_strike - alert_price) if alert_price > max_strike else min_strike - alert_price
                    logger.warning(f"ALERT_PROCESS: STALE_CHAIN | {underlying} | alert_price=₹{alert_price} | available_strikes=[₹{min_strike}-₹{max_strike}] | gap=₹{abs(gap)} | re-fetching with expanded range")
                    
                    # Fetch fresh chain to ensure we have the right strikes
                    fresh_chain = state['broker'].fetch_option_chain(underlying, expiry, current_price=alert_price, force_refresh=True)
                    if fresh_chain:
                        chain = fresh_chain
                        logger.info(f"ALERT_PROCESS: CHAIN_REFRESHED | {underlying} | using fresh data for alert_price=₹{alert_price}")
        
        ce, pe = chain.get_atm_contracts(alert_price, processed['strike_offset'], contract_type=contract_type) or (None, None)
        
        if not ce or not pe:
            logger.error(f"ALERT_PROCESS: NO_ATM_CONTRACTS | symbol={symbol} | ce={ce is not None} | pe={pe is not None}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'No ATM contracts available',
                'stage': 'atm_contract_selection',
                'expiry': expiry,
                **base_context,
            }
        
        logger.debug(f"ALERT_PROCESS: ATM_CONTRACTS | ce={ce.symbol} | pe={pe.symbol}")
        
        # Select the actual tradable contract. For the PUT bot this should resolve to the PE leg.
        selected_contract = ce if contract_type == 'CE' else pe
        contract_context = {
            **base_context,
            'contract': selected_contract.symbol,
            'contract_type': contract_type,
            'strike': selected_contract.strike,
            'expiry': expiry,
            'entry_premium': selected_contract.ltp,
        }
        
        logger.debug(f"ALERT_PROCESS: SELECTED | contract={selected_contract.symbol} | type={contract_type} | ltp=₹{selected_contract.ltp:.2f}")

        min_entry_premium = 5.0
        if state.get('entry_filter'):
            premium_validator = getattr(state['entry_filter'], 'validators', {}).get('premium')
            if premium_validator and hasattr(premium_validator, 'min_premium'):
                min_entry_premium = float(premium_validator.min_premium)

        if selected_contract.ltp < min_entry_premium:
            logger.warning(
                f"ALERT_PROCESS: PREMIUM_FAILED | symbol={symbol} | contract={selected_contract.symbol} | "
                f"premium=₹{selected_contract.ltp:.2f} < ₹{min_entry_premium:.2f}"
            )
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': f'Premium ₹{selected_contract.ltp:.2f} < ₹{min_entry_premium:.2f} (low liquidity, high gap risk)',
                'stage': 'selected_contract_premium_check',
                **contract_context,
            }
        
        # Check Liquidity (Minimum OI threshold)
        if SentimentConfig.CHECK_MIN_OI_ON_ENTRY:
            if selected_contract.open_interest < SentimentConfig.MIN_OI_LIQUIDITY_THRESHOLD:
                logger.warning(f"ALERT_PROCESS: LIQUIDITY_FAILED | symbol={symbol} | contract={selected_contract.symbol} | oi={selected_contract.open_interest:,.0f} < {SentimentConfig.MIN_OI_LIQUIDITY_THRESHOLD:,.0f}")
                return {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'status': 'rejected',
                    'reason': f'Insufficient liquidity: OI={selected_contract.open_interest:,.0f} < {SentimentConfig.MIN_OI_LIQUIDITY_THRESHOLD:,.0f}',
                    'stage': 'minimum_oi_check',
                    **contract_context,
                }
            else:
                logger.debug(f"ALERT_PROCESS: LIQUIDITY_OK | contract={selected_contract.symbol} | oi={selected_contract.open_interest:,.0f}")
        
        # Check Greeks constraints
        greeks_valid, greeks_msg = OptionsSignalValidator.check_greeks_constraints(
            selected_contract.to_dict()['greeks']
        )
        
        if not greeks_valid:
            logger.warning(f"ALERT_PROCESS: GREEKS_FAILED | symbol={symbol} | {greeks_msg}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': greeks_msg,
                'stage': 'greeks_validation',
                **contract_context,
            }
        
        logger.debug(f"ALERT_PROCESS: GREEKS_OK | delta={selected_contract.delta:.3f} | gamma={selected_contract.gamma:.5f}")
        
        # ML VALIDATION (WITH REAL GREEKS) - Now that we have actual Greeks data
        # If broker doesn't provide Greeks (all zeros), skip ML validation gracefully
        try:
            from .opt_ml_integration import get_ml_integration
            ml_integration = get_ml_integration()
            
            # Check if broker actually provided Greeks data (or we estimated them)
            greeks_data = selected_contract.to_dict()['greeks']
            has_real_greeks = any([
                greeks_data.get('delta', 0) != 0,
                greeks_data.get('gamma', 0) != 0,
                greeks_data.get('vega', 0) != 0,
                greeks_data.get('theta', 0) != 0
            ])
            
            if not has_real_greeks:
                logger.warning(f"ALERT_REJECTED: symbol={symbol} | contract={selected_contract.symbol} | reason=No Greeks data (broker not provided, estimation failed)")
                # SKIP THIS TRADE - Greeks are critical for entry filters
                return {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'status': 'rejected',
                    'reason': 'No Greeks data available for entry filter validation',
                    'stage': 'greeks_data_check',
                    **contract_context,
                }
            else:
                # Enrich alert with actual Greeks data before ML validation
                alert_with_greeks = alert.copy()
                alert_with_greeks['greeks'] = greeks_data
                alert_with_greeks['contract_type'] = contract_type
                alert_with_greeks['underlying_price'] = float(alert.get('price', 0))
                alert_with_greeks['strike'] = selected_contract.strike
                alert_with_greeks['iv'] = selected_contract.iv
                
                ml_valid, ml_reason, ml_details = ml_integration.validate_with_ml_filter(alert_with_greeks)
                
                if not ml_valid:
                    logger.warning(f"ML_VALIDATION_REJECTED: symbol={symbol} | contract={selected_contract.symbol} | reason={ml_reason}")
                    return {
                        'symbol': symbol,
                        'timestamp': timestamp,
                        'status': 'rejected',
                        'reason': f"ML filter: {ml_reason}",
                        'stage': 'ml_validation',
                        'ml_details': ml_details,
                        **contract_context,
                    }
                
                # Log successful ML validation with details
                logger.info(f"ML_VALIDATION_PASSED: symbol={symbol} | contract={selected_contract.symbol} | pop={ml_details.get('final_pop', 0):.1f}% | "
                           f"greeks_score={ml_details.get('greeks_score', 0):.2f}")
            
        except Exception as e:
            logger.warning(f"ML_VALIDATION_ERROR: symbol={symbol} | {str(e)} | continuing without ML")
            # Don't block on ML errors - continue processing
        
        # Calculate dynamic quantity based on budget utilization
        # NOTE: OptionsCapitalConfig is already imported at module level - don't re-import here
        # For options, lot_size=1 means each contract is 1 unit (not bundled)
        # The formula: quantity = (capital / premium) * lot_size
        # With lot_size=1: quantity = capital / premium (direct contract count)
        quantity = OptionsCapitalConfig.calculate_quantity_for_capital(
            premium=selected_contract.ltp,
            capital=cap_this_trade,
            lot_size=1
        )
        
        # Apply neural ML position size multiplier if signal available
        if neural_ml_signal and 'neural_ml_multiplier' in locals():
            original_quantity = quantity
            quantity = max(1, int(quantity * neural_ml_multiplier))
            logger.info(f"NEURAL_ML: POSITION_SIZE_APPLIED | symbol={symbol} | multiplier={neural_ml_multiplier:.2f}x | qty: {original_quantity} → {quantity}")
        
        # Calculate actual cost and utilization percentage
        # quantity is already in contracts (lot_size=1), so cost = quantity * premium
        actual_cost = quantity * selected_contract.ltp
        utilization_pct = (actual_cost / cap_this_trade) * 100 if cap_this_trade > 0 else 0
        order_context = {
            **contract_context,
            'quantity': quantity,
            'actual_cost': round(actual_cost, 2),
            'budget': cap_this_trade,
            'market_trend': market_trend,
        }

        liquidity_market_data = state['broker'].get_market_data(selected_contract.symbol, "NFO") or {}
        liquidity_oi_map = state['broker'].get_oi_data([selected_contract.symbol], "NFO") or {}
        live_volume = int(liquidity_market_data.get('volume') or selected_contract.volume or 0)
        live_oi = int(liquidity_oi_map.get(selected_contract.symbol) or selected_contract.open_interest or 0)
        selected_contract.volume = live_volume
        selected_contract.open_interest = live_oi

        liquidity_ok, liquidity_reason, liquidity_metrics = OptionsCapitalConfig.evaluate_liquidity_for_order(
            budget=cap_this_trade,
            quantity=quantity,
            premium=selected_contract.ltp,
            volume=live_volume,
            open_interest=live_oi,
            lot_size=1,
        )

        logger.info(
            f"ALERT_PROCESS: LIQUIDITY_CHECK | contract={selected_contract.symbol} | qty={quantity} "
            f"| budget=₹{cap_this_trade:.0f} | volume={live_volume:,} | oi={live_oi:,} | result={liquidity_ok}"
        )

        if not liquidity_ok:
            logger.warning(
                f"ALERT_PROCESS: REJECTED_LIQUIDITY | symbol={symbol} | contract={selected_contract.symbol} "
                f"| reason={liquidity_reason} | metrics={liquidity_metrics}"
            )
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': liquidity_reason,
                'liquidity_metrics': liquidity_metrics,
                'stage': 'dynamic_liquidity_check',
                **order_context,
            }
        
        logger.debug(f"ALERT_PROCESS: DYNAMIC_LOT_SIZING | contract={selected_contract.symbol} | premium=₹{selected_contract.ltp:.2f} | budget=₹{cap_this_trade} | market_trend={market_trend} | qty={quantity} | actual_cost=₹{actual_cost:.2f} | utilization={utilization_pct:.1f}%")
        
        logger.info(f"ALERT_PROCESS: PLACING_ORDER | contract={selected_contract.symbol} | qty={quantity} | premium=₹{selected_contract.ltp:.2f}")
        
        # Send order placement alert
        if state['alert_manager']:
            try:
                state['alert_manager'].alert_order_placed(
                    bot_type='options',
                    order_details={
                        'symbol': selected_contract.symbol,
                        'action': 'BUY',
                        'quantity': quantity,
                        'price': selected_contract.ltp
                    }
                )
            except Exception as e:
                logger.warning(f"ORDER_PLACEMENT_ALERT: FAILED | {str(e)}")
        
        order_id = None
        for _attempt in range(3):
            _raw = state['broker'].place_options_order(
                symbol=selected_contract.symbol,
                action='BUY',
                quantity=quantity,
                price=selected_contract.ltp,
                order_type='MARKET',
                allow_queue=False,
            )
            if _raw and not str(_raw).startswith("QUEUED_"):
                order_id = _raw
                break
            logger.warning(f"ALERT_PROCESS: BUY_ATTEMPT_{_attempt + 1}_FAILED | contract={selected_contract.symbol} | result={_raw}")
            if _attempt < 2:
                time.sleep(2 ** _attempt)

        if not order_id:
            logger.error(f"ALERT_PROCESS: ORDER_FAILED_ALL_ATTEMPTS | symbol={symbol} | contract={selected_contract.symbol}")
            
            # Send order rejection alert
            if state['alert_manager']:
                try:
                    state['alert_manager'].alert_order_rejected(
                        bot_type='options',
                        order_details={
                            'symbol': selected_contract.symbol,
                            'action': 'BUY',
                            'quantity': quantity
                        },
                        reason="Broker failed to place BUY order after 3 attempts"
                    )
                except Exception as e:
                    logger.warning(f"ORDER_REJECTION_ALERT: FAILED | {str(e)}")
            
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Failed to place BUY order after 3 attempts',
                'stage': 'broker_order_placement',
                **order_context,
            }
        
        logger.info(f"ALERT_PROCESS: ORDER_PLACED | order_id={order_id} | attempts={_attempt + 1}")

        if OptionsTradingConfig.TRADING_MODE == "LIVE" and state.get('broker'):
            logger.info(f"BUY_CONFIRMATION: WAITING | {selected_contract.symbol} | order_id={order_id} | timeout=30s")
            confirmed = state['broker'].wait_for_buy_confirmation(selected_contract.symbol, timeout=30, order_id=order_id)
            if not confirmed:
                order_status = state['broker'].get_order_status(order_id) or {}
                status = order_status.get('status', 'UNKNOWN')

                if status not in {'COMPLETE', 'FILLED', 'FULLY_FILLED'}:
                    cancel_success = state['broker'].cancel_order(order_id, selected_contract.symbol, order_type='MARKET')
                    reason_suffix = 'cancelled' if cancel_success else 'manual intervention required'
                    logger.error(
                        f"BUY_CONFIRMATION: FAILED | {selected_contract.symbol} | order_id={order_id} | status={status} | {reason_suffix}"
                    )
                    return {
                        'symbol': symbol,
                        'timestamp': timestamp,
                        'status': 'rejected',
                        'reason': f'BUY order not confirmed filled (status={status}) - {reason_suffix}',
                        'stage': 'buy_confirmation',
                        'order_id': order_id,
                        **order_context,
                    }
                logger.warning(f"BUY_CONFIRMATION: STATUS_RECOVERED_AS_FILLED | {selected_contract.symbol} | order_id={order_id}")

        logger.info(f"BUY_ORDER: Filled | order_id={order_id} | {selected_contract.symbol}")
        
        # Prepare entry Greek data for learning
        entry_greeks_data = {
            'delta': selected_contract.delta,
            'gamma': selected_contract.gamma,
            'theta': selected_contract.theta,
            'vega': selected_contract.vega,
            'iv': selected_contract.iv
        }
        
        # Prepare neural ML metadata for outcome recording
        neural_ml_metadata = {
            'ml_signal': neural_ml_signal,
            'ml_probability': neural_ml_result.get('probability') if neural_ml_result.get('error') is None else None,
            'ml_confidence': neural_ml_result.get('confidence') if neural_ml_result.get('error') is None else None,
            'ml_multiplier': neural_ml_result.get('position_size_multiplier', 1.0) if neural_ml_result.get('error') is None else None
        } if 'neural_ml_result' in locals() else {
            'ml_signal': None,
            'ml_probability': None,
            'ml_confidence': None,
            'ml_multiplier': 1.0
        }
        
        # Fetch and log sector strength at entry (PAPER and LIVE modes)
        sector_data = {
            'sector': 'UNKNOWN',
            'sector_rsi': None,
            'sector_performance': None,
            'sector_participation': None,
            'sector_bullish': None
        }
        
        if state['sector_analyzer']:
            try:
                # 🔧 FIX: Get sector for this symbol (using underlying, not full contract symbol)
                sector = state['sector_analyzer'].get_sector(symbol)
                if sector and sector != 'UNKNOWN':
                    sector_data['sector'] = sector
                    logger.info(f"SECTOR_ENTRY_LOG | symbol={symbol} | sector={sector} | mapping_found=YES")
                    
                    # Get sector strength metrics
                    performance = state['sector_analyzer'].get_sector_performance(sector)
                    if performance:
                        sector_data['sector_rsi'] = performance.get('rsi')
                        sector_data['sector_performance'] = performance.get('performance_pct')
                        sector_data['sector_participation'] = performance.get('participation_pct')
                    
                    # Check if sector is bullish
                    is_bullish, reason = state['sector_analyzer'].is_sector_bullish(symbol, threshold=60)
                    sector_data['sector_bullish'] = is_bullish
                    
                    logger.debug(f"SECTOR_DATA_COMPLETE | symbol={symbol} | sector={sector} | rsi={sector_data['sector_rsi']} | perf={sector_data['sector_performance']}% | participation={sector_data['sector_participation']}% | bullish={is_bullish}")
                else:
                    logger.warning(f"SECTOR_ENTRY_LOG | symbol={symbol} | sector=NOT_MAPPED | mapping_found=NO")
                    
                logger.debug(f"SECTOR_DATA_TO_POSITION | symbol={symbol} | sector_data={sector_data}")  # DEBUG
            except Exception as e:
                # Log error but don't block the trade
                logger.error(f"SECTOR_ANALYZER: ENTRY_LOG_FAILED | symbol={symbol} | error={str(e)}")
                import traceback
                logger.debug(f"SECTOR_ANALYZER: TRACEBACK | {traceback.format_exc()}")
        
        # 🔧 FIX: Check if position was successfully added before incrementing counter
        position_added = state['monitor'].add_position(
            symbol=selected_contract.symbol,
            underlying=underlying,
            strike=selected_contract.strike,
            expiry=expiry,
            contract_type=contract_type,
            action='BUY',
            quantity=quantity,
            entry_premium=selected_contract.ltp,
            order_id=order_id,
            underlying_alert_price=alert_price if alert_price > 0 else None,
            entry_greeks=entry_greeks_data,  # ADDED: Pass entry Greeks
            sector_data=sector_data,
            market_trend=market_trend,
            trend_strength=float(alert.get('trend_strength', 0) or 0)
        )
        
        # 🔧 CRITICAL: Only increment counter if position was actually added (not rejected as duplicate)
        if not position_added:
            logger.warning(f"ALERT_PROCESS: POSITION_NOT_ADDED | symbol={symbol} | contract={selected_contract.symbol} | order_id={order_id} | likely_duplicate")
            if OptionsTradingConfig.TRADING_MODE == "LIVE":
                logger.error(f"ALERT_PROCESS: POSITION_TRACKING_FAILED_AFTER_FILL | symbol={symbol} | contract={selected_contract.symbol} | emergency flattening")
                state['broker'].place_options_order(
                    symbol=selected_contract.symbol,
                    action='SELL',
                    quantity=quantity,
                    order_type='MARKET',
                    product_type='INTRADAY',
                    allow_queue=False,
                )
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Position not added (likely duplicate)',
                'stage': 'position_registration',
                'order_id': order_id,
                **order_context,
            }
        # 🔧 FIX: Increment daily trade counter ONLY after position is successfully added
        # This ensures we count actual trades, not just order attempts
        new_trade_count = OptionsCapitalConfig.increment_daily_trade_count()
        logger.info(f"DAILY_TRADE_COUNT: Incremented to {new_trade_count}/{OptionsCapitalConfig.MAX_TRADES_PER_DAY}")
        
        # 🔧 NEW: Log successful position creation at bot level
        log_alert(alert=alert, status='position_created', details={
            'symbol': selected_contract.symbol,
            'order_id': order_id,
            'quantity': quantity,
            'entry_premium': selected_contract.ltp,
            'new_trade_count': new_trade_count
        })
        
        logger.info(f"ALERT_PROCESS: SUCCESS | symbol={symbol} | contract={selected_contract.symbol} | order_id={order_id}")
        
        return {
            'symbol': symbol,
            'underlying': underlying,
            'contract': selected_contract.symbol,
            'timestamp': timestamp,
            'status': 'success',
            'stage': 'position_opened',
            'order_id': order_id,
            'contract_type': contract_type,
            'strike': selected_contract.strike,
            'expiry': expiry,
            'entry_premium': selected_contract.ltp,
            'quantity': quantity,
            'actual_cost': round(actual_cost, 2),
            'budget': OptionsCapitalConfig.CAP_PER_TRADE,
            'alert_price': alert_price if alert_price > 0 else None,
            'normalized_action': action,
            'message': f"{processed.get('execution_action', 'BUY')} {contract_type} position opened",
            'neural_ml': neural_ml_metadata  # ADDED: Include in response for debugging
        }
    
    except Exception as e:
        logger.error(f"ALERT_PROCESS: EXCEPTION | symbol={alert.get('symbol')} | {str(e)}")
        return {
            'symbol': alert.get('symbol', 'UNKNOWN'),
            'timestamp': datetime.now().isoformat(),
            'status': 'error',
            'stage': 'exception',
            'error': str(e)
        }

# =============================================================================
# API Server Management
# =============================================================================

class OptionsAPIServer:
    """Manages options webhook API server"""
    
    def __init__(self):
        self.app = create_options_api_app()
        self.running = False
    
    def start(self, host: str = WebhookConfig.HOST, port: int = WebhookConfig.PORT):
        """Start webhook server using Werkzeug WSGI server (non-blocking for daemon threads)
        
        Added robustness:
        - Better exception handling with logging
        - Port binding retry logic
        - Graceful shutdown handling
        """
        if not self.app:
            logger.error("API_SERVER: FLASK_NOT_AVAILABLE")
            print("❌ Cannot start API server - Flask not available")
            raise RuntimeError("Flask not imported")
        
        print(f"🚀 Starting Options Webhook Server on {host}:{port}")
        print(f"   Endpoint: {WebhookConfig.ENDPOINT}")
        print(f"   Mode: {OptionsTradingConfig.TRADING_MODE}")
        
        self.running = True
        self.app.options_state['active'] = True
        self.app.options_state['startup_time'] = datetime.now()
        
        try:
            # Use Werkzeug WSGI server directly instead of app.run()
            # app.run() uses reloader which fails in daemon threads
            from werkzeug.serving import make_server
            
            # Create server with timeout and error handling
            self.server = make_server(host, port, self.app, threaded=True)
            logger.info(f"API_SERVER: BOUND | host={host} | port={port}")
            print(f"✅ Webhook server bound to {host}:{port} - listening for alerts")
            
            # Serve with timeout for cleaner shutdown
            try:
                self.server.serve_forever()
            finally:
                logger.info("API_SERVER: SERVE_ENDED")
                self.running = False
                self.server.server_close()
                
        except OSError as e:
            logger.error(f"API_SERVER: BIND_FAILED | error={str(e)}")
            print(f"❌ Failed to bind port {port}: {str(e)}")
            self.running = False
            raise  # Re-raise for restart logic
            
        except Exception as e:
            logger.error(f"API_SERVER: EXCEPTION | error={str(e)} | type={type(e).__name__}")
            print(f"❌ API server error: {str(e)}")
            self.running = False
            raise  # Re-raise for restart logic
    
    def stop(self):
        """Stop webhook server"""
        self.running = False
        if self.app and hasattr(self.app, 'options_state'):
            self.app.options_state['active'] = False
        print("🛑 Options Webhook Server stopped")

# Global API server instance
_options_api_server = None

def get_options_api_server() -> OptionsAPIServer:
    """Get or create API server instance"""
    global _options_api_server
    if _options_api_server is None:
        _options_api_server = OptionsAPIServer()
    return _options_api_server
