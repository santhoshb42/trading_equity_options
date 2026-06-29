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
import os

# Per-bot concurrency cap: limits simultaneous broker-heavy alert threads to prevent OOM.
# Threads start immediately (webhook returns 202 at once); only the broker work is gated.
# Configurable via OPTIONS_ALERT_CONCURRENCY env var. Default 4 for 2GB VPS; raise on larger hosts.
_ALERT_SEMAPHORE = threading.Semaphore(int(os.environ.get('OPTIONS_ALERT_CONCURRENCY', '4')))

try:
    from flask import Flask, request, jsonify
except ImportError:
    Flask = None
    request = None
    def jsonify(obj):
        return obj

from .optconfig import WebhookConfig, OptionsTradingConfig, OptionsCapitalConfig, DATA_DIR
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
    from .options_learning_engine import SymbolPerformanceTracker
    HAS_LEARNING_ENGINE = True
except ImportError:
    HAS_LEARNING_ENGINE = False
    SymbolPerformanceTracker = None

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
            options_learning_file = DATA_DIR / "learning" / "symbol_stats.json"
            learning_engine = SymbolPerformanceTracker(symbol_stats_file=options_learning_file)
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
        'startup_time': None,
        'entry_in_progress': set(),           # underlyings actively being processed by a worker thread
        'entry_in_progress_lock': threading.Lock(),  # protects the set above
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
    NUM_ALERT_WORKERS = int(os.getenv('OPTIONS_ALERT_WORKERS', '3'))

    def webhook_alert_worker():
        """Background worker that processes alerts from queue"""
        thread_name = threading.current_thread().name
        logger.info(f"WEBHOOK_WORKER [{thread_name}]: Starting background alert worker thread")
        while WEBHOOK_WORKER_ACTIVE:
            try:
                # Wait for alert with timeout
                alert_data = ALERT_QUEUE.get(timeout=1)
                if alert_data is None:  # Shutdown signal
                    logger.info("WEBHOOK_WORKER: Received shutdown signal")
                    break
                
                alert, local_state = alert_data
                symbol = alert.get('symbol', 'UNKNOWN')
                
                logger.info(f"WEBHOOK_WORKER [{thread_name}]: Pulled from queue | symbol={symbol} | action={alert.get('action', '?')} | price={alert.get('price', '?')} | queue_remaining={ALERT_QUEUE.qsize()}")
                log_alert(alert=alert, status='bot_processing_started', details={
                    'queue_remaining': ALERT_QUEUE.qsize(),
                    'worker': thread_name,
                })

                try:
                    # Process the alert (this may take 30+ seconds)
                    start_time = time.time()
                    result = _process_options_alert(alert, local_state)
                    elapsed = time.time() - start_time
                    result_status = result.get('status', 'unknown') if isinstance(result, dict) else 'unknown'

                    logger.info(f"WEBHOOK_WORKER [{thread_name}]: Alert processed | symbol={symbol} | status={result_status} | elapsed_ms={elapsed*1000:.1f}")
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

    def webhook_alert_worker_with_restart():
        """Wraps webhook_alert_worker so a fatal exception causes an automatic restart."""
        while WEBHOOK_WORKER_ACTIVE:
            try:
                webhook_alert_worker()
            except Exception as e:
                logger.critical(f"WEBHOOK_WORKER: CRASHED — restarting in 2s | {str(e)}", exc_info=True)
                time.sleep(2)
        logger.info("WEBHOOK_WORKER: Restart-wrapper exiting (shutdown requested)")

    def _process_alert_in_thread(alert, local_state):
        """Process ONE alert in its own thread — true per-alert parallelism (no fixed worker
        pool, no queue wait). Each alert starts immediately and overlaps its broker I/O with
        others; the per-endpoint rate limiter is the only throughput ceiling. Non-blocking for
        TradingView (the webhook already returned 202)."""
        symbol = alert.get('symbol', 'UNKNOWN')
        tname = threading.current_thread().name
        log_alert(alert=alert, status='bot_processing_started', details={'worker': tname, 'parallel': True})
        start_time = time.time()
        try:
            _sem_t0 = time.time()
            with _ALERT_SEMAPHORE:  # gate broker-heavy work; prevents OOM under large bursts
                sem_wait_ms = (time.time() - _sem_t0) * 1000  # queue wait (starvation metric)
                _proc_t0 = time.time()
                result = _process_options_alert(alert, local_state)
                proc_ms = (time.time() - _proc_t0) * 1000     # alert→order processing
            elapsed = time.time() - start_time
            result_status = result.get('status', 'unknown') if isinstance(result, dict) else 'unknown'
            logger.info(f"ALERT_TIMING [{tname}]: symbol={symbol} | status={result_status} | sem_wait_ms={sem_wait_ms:.0f} | proc_ms={proc_ms:.0f} | total_ms={elapsed*1000:.0f}")
            _details = _build_alert_completion_details(alert, result, elapsed * 1000)
            if isinstance(_details, dict):
                _details['sem_wait_ms'] = round(sem_wait_ms, 0)
                _details['proc_ms'] = round(proc_ms, 0)
            log_alert(alert=alert, status='bot_processing_completed', details=_details)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"ALERT_THREAD [{tname}]: failed | symbol={symbol} | error={str(e)} | elapsed_ms={elapsed*1000:.1f}", exc_info=True)
            log_alert(alert=alert, status='bot_processing_error', details={
                'error': str(e), 'elapsed_ms': round(elapsed * 1000, 2), 'attempted_symbol': symbol,
            })

    # Expose for the webhook route (per-alert parallel dispatch).
    state['_process_alert_in_thread'] = _process_alert_in_thread

    # NOTE: the fixed ALERT_QUEUE + worker pool below is now a VESTIGIAL fallback (kept for the
    # shutdown machinery). Live alerts are dispatched per-alert in parallel by the webhook route,
    # so these workers idle on an empty queue.
    # Start N parallel worker threads (daemon=True — process can exit cleanly via atexit)
    try:
        for _wi in range(NUM_ALERT_WORKERS):
            _t = threading.Thread(
                target=webhook_alert_worker_with_restart,
                daemon=True,
                name=f"WebhookAlertWorker-{_wi + 1}"
            )
            _t.start()
        logger.info(f"API: {NUM_ALERT_WORKERS} parallel webhook worker threads started")
    except Exception as e:
        logger.error(f"API: Failed to start webhook worker threads | {str(e)}")

    def shutdown_webhook_worker():
        """Stop all background worker threads gracefully"""
        global WEBHOOK_WORKER_ACTIVE
        WEBHOOK_WORKER_ACTIVE = False
        try:
            for _ in range(NUM_ALERT_WORKERS):
                ALERT_QUEUE.put(None)  # One shutdown signal per worker thread
            logger.info(f"WEBHOOK_WORKER: Shutdown signals sent to {NUM_ALERT_WORKERS} workers")
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
        rate_limiter_stats = {}
        if state.get('broker'):
            try:
                rate_limiter_stats = state['broker'].get_rate_limiter_stats() or {}
            except Exception as exc:
                rate_limiter_stats = {'error': str(exc)}

        order_book_cooldown_remaining = 0.0
        trade_book_cooldown_remaining = 0.0
        if state.get('broker'):
            now = time.time()
            order_book_cooldown_remaining = max(
                0.0,
                float(getattr(state['broker'], '_order_book_rate_limited_until', 0.0) or 0.0) - now,
            )
            trade_book_cooldown_remaining = max(
                0.0,
                float(getattr(state['broker'], '_trade_book_rate_limited_until', 0.0) or 0.0) - now,
            )
        
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
            },
            'mode': OptionsTradingConfig.TRADING_MODE,
            'alert_queue_size': ALERT_QUEUE.qsize(),
            'rate_limiter': rate_limiter_stats,
            'broker_rate_limit': {
                'order_book_hits': int(getattr(state['broker'], '_order_book_rate_limit_hits', 0) or 0),
                'order_book_cooldown_seconds': round(order_book_cooldown_remaining, 3),
                'trade_book_hits': int(getattr(state['broker'], '_trade_book_rate_limit_hits', 0) or 0),
                'trade_book_cooldown_seconds': round(trade_book_cooldown_remaining, 3),
            },
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
                    recovery_file = str(DATA_DIR / 'alert_recovery_queue.jsonl')
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
            
            # ✅ PER-ALERT PARALLEL DISPATCH: every alert gets its OWN thread immediately —
            # no fixed worker pool, no queue wait. Alerts overlap their broker I/O; the
            # per-endpoint rate limiter is the only throughput ceiling. Returns 202 instantly.
            spawned = 0
            for alert in alerts:
                try:
                    symbol = alert.get('symbol', 'UNKNOWN')
                    log_alert(alert=alert, status='queued', details={'bot_level': True, 'parallel': True})
                    threading.Thread(
                        target=state['_process_alert_in_thread'],
                        args=(alert, state),
                        daemon=True,
                        name=f"alert-{symbol}",
                    ).start()
                    spawned += 1
                    logger.debug(f"WEBHOOK: Alert dispatched (parallel) | symbol={symbol} | action={alert.get('action', '?')} | price={alert.get('price', '?')}")
                except Exception as _disp_err:
                    logger.error(f"WEBHOOK: Dispatch failed | symbol={alert.get('symbol')} | {str(_disp_err)}")
                    log_alert(alert=alert, status='dropped', details={'reason': f'dispatch_error: {_disp_err}'})

            # ✅ IMMEDIATE RESPONSE to TradingView (before any processing!)
            logger.info(f"WEBHOOK: Returning immediately | dispatched={spawned}/{len(alerts)} parallel | response_time=<100ms")
            return jsonify({
                'status': 'accepted',
                'message': f'Processing {spawned} alert(s) in parallel',
                'processing': spawned,
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
                    
                    # BUG FIX: Use monitor.close_position() as the single exit path.
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
                        errors.append(f"{symbol}: Failed to place exit order")
                        log_event("EOD_POSITION_CLOSE_FAILED", f"❌ Failed to close {symbol}",
                                 symbol=symbol, contract=contract)
                except Exception as pos_err:
                    errors.append(f"{pos.get('symbol', 'UNKNOWN')}: {str(pos_err)}")
                    logger.error(f"EOD_POSITION_CLOSE_ERROR: {pos.get('symbol')} | {str(pos_err)}")
            
            log_event("EOD_SQUARE_OFF_COMPLETE", 
                     f"✅ EOD square-off complete | Active closed: {closed_count}/{len(positions_to_close)} | Stagnant: {stagnant_count} | Already closed: {len(all_positions) - len(positions_to_close)} | PnL: ₹{total_pnl:.2f}",
                     closed=closed_count, stagnant=stagnant_count, total=len(positions_to_close), 
                     already_closed=len(all_positions) - len(positions_to_close), pnl=round(total_pnl, 2))
            
            # ── BROKER ORPHAN SWEEP (LIVE safety net) ──────────────────────────────
            # Close any position OPEN AT THE BROKER that the monitor isn't tracking (lost after a
            # crash/restart, or a fill that arrived post-desync). Without this, an orphan stays
            # open past 3:30 = unhedged overnight risk. Re-verify AFTER the normal close loop.
            orphan_closed = 0
            orphan_errors = []
            try:
                if OptionsTradingConfig.TRADING_MODE == "LIVE" and state.get('broker'):
                    tracked_symbols = {str(p.get('symbol')) for p in all_positions if p.get('symbol')}
                    reverify = state['broker'].verify_positions_with_broker() or {}
                    broker_net = reverify.get('net_positions', {}) or {}
                    for b_sym, b_qty in broker_net.items():
                        try:
                            qty = int(b_qty)
                        except (TypeError, ValueError):
                            continue
                        if qty <= 0 or b_sym in tracked_symbols:
                            continue  # flat/short (bot is long-only) or already handled above
                        logger.error(f"EOD_ORPHAN: untracked broker position {b_sym} qty={qty} → force MARKET SELL")
                        log_event("EOD_ORPHAN_DETECTED",
                                 f"⚠️ Untracked broker position {b_sym} qty={qty} — force-closing",
                                 symbol=b_sym, quantity=qty)
                        try:
                            state['broker'].cancel_outstanding_orders_for_symbol(b_sym, [])
                        except Exception as _ce:
                            logger.warning(f"EOD_ORPHAN: cancel outstanding failed | {b_sym} | {str(_ce)}")
                        sell_id = state['broker'].place_options_order(
                            symbol=b_sym, action='SELL', quantity=qty,
                            order_type='MARKET', product_type='INTRADAY', allow_queue=False,
                        )
                        if sell_id:
                            orphan_closed += 1
                            log_event("EOD_ORPHAN_CLOSED", f"✅ Force-closed orphan {b_sym}",
                                     symbol=b_sym, quantity=qty, order_id=sell_id)
                        else:
                            orphan_errors.append(b_sym)
                            log_event("EOD_ORPHAN_CLOSE_FAILED", f"❌ Failed to close orphan {b_sym}",
                                     symbol=b_sym, quantity=qty)
                    if orphan_closed or orphan_errors:
                        log_event("EOD_ORPHAN_SWEEP_COMPLETE",
                                 f"Orphan sweep: closed={orphan_closed} | failed={len(orphan_errors)}",
                                 closed=orphan_closed, failed=orphan_errors)
            except Exception as orphan_err:
                logger.error(f"EOD_ORPHAN_SWEEP_ERROR: {str(orphan_err)}")

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
                "orphans_closed": orphan_closed,
                "orphan_errors": orphan_errors if orphan_errors else None,
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


def _compact_entry_filter_inputs(
    entry_signal: Optional[Dict[str, Any]],
    market_data: Optional[Dict[str, Any]],
    fetch_results: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Persist only the tune-relevant alert and live-data inputs used by the entry filter."""
    signal = entry_signal or {}
    market = market_data or {}
    inputs = {
        'alert_signal': _compact_alert_details({
            'entry_type': signal.get('entry_type'),
            'confidence': signal.get('confidence'),
            'score': signal.get('score'),
            'market_trend': signal.get('market_trend'),
            'trend_strength': signal.get('trend_strength'),
            'momentum_score': signal.get('momentum_score'),
            'rsi_value': signal.get('rsi_value'),
            'rsi_expansion': signal.get('rsi_expansion'),
            'macd_hist': signal.get('macd_hist'),
            'vwap_distance': signal.get('vwap_distance'),
            'volume_ratio': signal.get('volume_ratio'),
            'ema_spread': signal.get('ema_spread'),
            'atr_pc': signal.get('atr_pc'),
            'adx': signal.get('adx'),
            'volume_cooling': signal.get('volume_cooling'),
            'day_change': signal.get('day_change'),
            'setup_sequence': signal.get('setup_sequence'),
            'is_reentry_setup': signal.get('is_reentry_setup'),
            'tv_trigger_flag': signal.get('tv_trigger_flag'),
            'tv_setup_label': signal.get('tv_setup_label'),
            'reentry_context_active': signal.get('reentry_context_active'),
        }),
        'market_snapshot': _compact_alert_details({
            'entry_premium': market.get('entry_premium'),
            'pcr': market.get('pcr'),
            'oi_buildup': market.get('oi_buildup'),
            'rsi_15m': market.get('rsi_15m'),
            'macd_15m': market.get('macd_15m'),
            'ma_short': market.get('ma_short'),
            'ma_long': market.get('ma_long'),
            'slope': market.get('slope'),
            'iv_percentile': market.get('iv_percentile'),
            'days_to_expiry': market.get('days_to_expiry'),
            'trend_strength': market.get('trend_strength'),
            'market_trend': market.get('market_trend'),
            'entry_confidence': market.get('entry_confidence'),
            'entry_score': market.get('entry_score'),
            'top_gainer_rank': market.get('top_gainer_rank'),
            'is_top_gainer': market.get('is_top_gainer'),
        }),
        'data_fetch_status': fetch_results or None,
    }
    return _compact_alert_details(inputs)


def _build_position_entry_context(
    alert: Dict[str, Any],
    processed: Dict[str, Any],
    market_trend: str,
    filter_inputs: Optional[Dict[str, Any]],
    filter_details: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Store the entry-time snapshot needed for later performance slicing."""
    return _compact_alert_details({
        'entry_type': str(alert.get('entry_type', '') or '').upper(),
        'confidence': processed.get('confidence'),
        'score': processed.get('score'),
        'market_trend': market_trend,
        'trend_strength': processed.get('trend_strength') or alert.get('trend_strength'),
        'momentum_score': alert.get('momentum_score'),
        'rsi_value': alert.get('rsi_value'),
        'rsi_expansion': alert.get('rsi_expansion'),
        'macd_hist': alert.get('macd_hist'),
        'vwap_distance': alert.get('vwap_distance'),
        'volume_ratio': alert.get('volume_ratio'),
        'ema_spread': alert.get('ema_spread'),
        'atr_pc': alert.get('atr_pc'),
        'adx': alert.get('adx'),
        'volume_cooling': alert.get('volume_cooling'),
        'day_change': alert.get('day_change'),
        'setup_sequence': alert.get('setup_sequence'),
        'tv_trigger_flag': alert.get('tv_trigger_flag'),
        'tv_setup_label': alert.get('tv_setup_label'),
        'reentry_context_active': alert.get('reentry_context_active'),
        'is_reentry_setup': processed.get('is_reentry_setup'),
        'filter_inputs': filter_inputs,
        'filter_details': filter_details,
    })


def _append_liquidity_decision_log(bot_type: str, payload: Dict[str, Any]) -> None:
    """Persist entry-time liquidity decisions for later review/backtesting."""
    try:
        log_path = DATA_DIR / "liquidity_decisions.jsonl"
        record = {
            'timestamp': datetime.now().isoformat(),
            'bot_type': bot_type,
            **payload,
        }
        with open(log_path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        logger.warning(f"LIQUIDITY_DECISION_LOG: FAILED | {str(exc)}")


def _extract_probation_snapshot(filter_details: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return trade-time probation state when the entry filter captured it."""
    if not isinstance(filter_details, dict):
        return None
    reputation_details = filter_details.get('symbol_reputation')
    if not isinstance(reputation_details, dict):
        return None
    snapshot = reputation_details.get('snapshot')
    return snapshot if isinstance(snapshot, dict) else None


def _log_entry_filter_decision(
    symbol: str,
    action: str,
    is_valid: bool,
    entry_reason: str,
    entry_details: Dict[str, Any],
    data_available: int,
    filter_inputs: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist the exact entry-filter decision, including probation state when present."""
    validators_passed = entry_details.get('validators_passed', 0) if isinstance(entry_details, dict) else 0
    probation_snapshot = _extract_probation_snapshot(entry_details)
    log_event(
        "ENTRY_FILTER_DECISION",
        f"{'PASSED' if is_valid else 'REJECTED'}: {symbol}",
        symbol=symbol,
        action=action,
        passed=is_valid,
        reason=entry_reason,
        validators_passed=validators_passed,
        data_collected=data_available,
        probation_snapshot=probation_snapshot,
        filter_details=entry_details,
        filter_inputs=filter_inputs,
    )


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
        'market_trend': result.get('market_trend'),
        'trades_today': result.get('trades_today'),
        'max_trades': result.get('max_trades'),
        'index_trades_today': result.get('index_trades_today'),
        'max_index_trades': result.get('max_index_trades'),
        'open_positions': result.get('open_positions'),
        'message': result.get('message'),
        'reason': result.get('reason') or result.get('error'),
        'liquidity_metrics': result.get('liquidity_metrics'),
        'filter_details': result.get('filter_details'),
        'filter_inputs': result.get('filter_inputs'),
        'probation_snapshot': result.get('probation_snapshot'),
        'data_fetch_status': result.get('data_fetch_status'),
    }))

    return details


def _get_open_position_capital_used(state: Dict[str, Any]) -> float:
    """Calculate currently deployed capital from open positions only."""
    try:
        summary = state['monitor'].get_position_summary()
    except Exception:
        return 0.0

    used_capital = 0.0
    for position in summary.get('positions', []):
        entry_total = position.get('entry_premium_total')
        if entry_total is None:
            entry_total = float(position.get('entry_premium', 0) or 0) * float(position.get('quantity', 0) or 0)
        used_capital += max(0.0, float(entry_total or 0.0))
    return used_capital


def _evaluate_entry_limits(
    *,
    alert: Dict[str, Any],
    state: Dict[str, Any],
    symbol: str,
    underlying: str,
    timestamp: str,
    cap_this_trade: float,
    base_context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Enforce total deployed capital and daily trade count before entry."""

    # Reject if this underlying already has an open position (prevents duplicate trades on same stock)
    existing_for_underlying = [
        sym for sym, pos in state['monitor']._snapshot_positions_items()
        if pos.underlying == underlying
    ]
    if existing_for_underlying:
        logger.warning(
            f"ALERT_PROCESS: UNDERLYING_ALREADY_ACTIVE | symbol={symbol} | underlying={underlying} "
            f"| existing_position={existing_for_underlying[0]}"
        )
        log_alert(alert=alert, status='duplicate_underlying_rejected', details={
            'underlying': underlying,
            'existing_position': existing_for_underlying[0],
        })
        return {
            'symbol': symbol,
            'timestamp': timestamp,
            'status': 'rejected',
            'reason': f'Underlying {underlying} already has an open position ({existing_for_underlying[0]})',
            'stage': 'duplicate_underlying_check',
            **base_context,
        }

    used_capital = _get_open_position_capital_used(state)
    available_capital = OptionsCapitalConfig.get_available_capital(used_capital)
    if available_capital < cap_this_trade:
        logger.warning(
            f"ALERT_PROCESS: INSUFFICIENT_CAPITAL | symbol={symbol} "
            f"| used=₹{used_capital:.2f} | available=₹{available_capital:.2f} | needed=₹{cap_this_trade:.2f}"
        )
        return {
            'symbol': symbol,
            'timestamp': timestamp,
            'status': 'rejected',
            'reason': 'Insufficient capital',
            'stage': 'capital_check',
            'budget': cap_this_trade,
            'budget_used': round(used_capital, 2),
            'budget_remaining': round(available_capital, 2),
            **base_context,
        }

    logger.debug(
        f"ALERT_PROCESS: CAPITAL_OK | used=₹{used_capital:.2f} "
        f"| available=₹{available_capital:.2f} | needed=₹{cap_this_trade:.2f}"
    )

    daily_counts = OptionsCapitalConfig.get_daily_trade_counts()
    is_index_trade = OptionsCapitalConfig.is_index_underlying(underlying)
    segment_trade_count = daily_counts['index'] if is_index_trade else daily_counts['non_index']
    segment_trade_limit = OptionsCapitalConfig.get_trade_limit_for_underlying(underlying)
    if segment_trade_count >= segment_trade_limit:
        stage = 'index_daily_trade_limit' if is_index_trade else 'non_index_daily_trade_limit'
        status = 'index_daily_limit_rejected' if is_index_trade else 'non_index_daily_limit_rejected'
        logger.warning(
            f"ALERT_PROCESS: DAILY_LIMIT_REACHED | symbol={symbol} | underlying={underlying} "
            f"| segment_trades_today={segment_trade_count} | segment_max={segment_trade_limit} "
            f"| total_trades_today={daily_counts['total']} | total_max={OptionsCapitalConfig.MAX_TRADES_PER_DAY}"
        )
        log_alert(alert=alert, status=status, details={
            'trades_today': segment_trade_count,
            'max_trades': segment_trade_limit,
            'total_trades_today': daily_counts['total'],
            'total_max_trades': OptionsCapitalConfig.MAX_TRADES_PER_DAY,
            'index_trades_today': daily_counts['index'],
            'max_index_trades': OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY,
            'non_index_trades_today': daily_counts['non_index'],
            'max_non_index_trades': OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY,
        })
        return {
            'symbol': symbol,
            'timestamp': timestamp,
            'status': 'rejected',
            'reason': f'Daily trade limit reached ({segment_trade_count}/{segment_trade_limit})',
            'stage': stage,
            'trades_today': segment_trade_count,
            'max_trades': segment_trade_limit,
            'total_trades_today': daily_counts['total'],
            'total_max_trades': OptionsCapitalConfig.MAX_TRADES_PER_DAY,
            'index_trades_today': daily_counts['index'],
            'max_index_trades': OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY,
            'non_index_trades_today': daily_counts['non_index'],
            'max_non_index_trades': OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY,
            **base_context,
        }

    logger.debug(
        f"ALERT_PROCESS: DAILY_LIMIT_OK | underlying={underlying} "
        f"| segment_trades_today={segment_trade_count}/{segment_trade_limit} "
        f"| total_trades_today={daily_counts['total']}/{OptionsCapitalConfig.MAX_TRADES_PER_DAY}"
    )
    log_alert(alert=alert, status='daily_limit_passed', details={
        'trades_today': segment_trade_count,
        'max_trades': segment_trade_limit,
        'total_trades_today': daily_counts['total'],
        'total_max_trades': OptionsCapitalConfig.MAX_TRADES_PER_DAY,
        'index_trades_today': daily_counts['index'],
        'max_index_trades': OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY,
        'non_index_trades_today': daily_counts['non_index'],
        'max_non_index_trades': OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY,
    })
    return None


def _evaluate_broker_funds(
    *,
    alert: Dict[str, Any],
    state: Dict[str, Any],
    symbol: str,
    timestamp: str,
    required_cash: float,
    base_context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Require verified Angel One cash before placing a LIVE BUY order."""
    if OptionsTradingConfig.TRADING_MODE != "LIVE":
        return None

    broker = state.get('broker')
    if broker is None or not hasattr(broker, 'get_funds_snapshot'):
        logger.error(f"ALERT_PROCESS: FUNDS_CHECK_UNAVAILABLE | symbol={symbol} | broker missing funds helper")
        log_alert(alert=alert, status='funds_check_failed', details={'required_cash': round(required_cash, 2)})
        return {
            'symbol': symbol,
            'timestamp': timestamp,
            'status': 'rejected',
            'reason': 'Unable to verify Angel funds',
            'stage': 'broker_funds_check',
            'required_cash': round(required_cash, 2),
            **base_context,
        }

    funds_snapshot = broker.get_funds_snapshot(force_refresh=True)
    if not funds_snapshot:
        logger.error(f"ALERT_PROCESS: FUNDS_CHECK_FAILED | symbol={symbol} | required=₹{required_cash:.2f}")
        log_alert(alert=alert, status='funds_check_failed', details={'required_cash': round(required_cash, 2)})
        return {
            'symbol': symbol,
            'timestamp': timestamp,
            'status': 'rejected',
            'reason': 'Unable to verify Angel funds',
            'stage': 'broker_funds_check',
            'required_cash': round(required_cash, 2),
            **base_context,
        }

    available_cash = float(funds_snapshot.get('available_cash') or 0.0)
    if available_cash + 1e-6 < required_cash:
        logger.warning(
            f"ALERT_PROCESS: NO_FUNDS | symbol={symbol} | available=₹{available_cash:.2f} | required=₹{required_cash:.2f}"
        )
        log_alert(alert=alert, status='no_funds_rejected', details={
            'available_cash': round(available_cash, 2),
            'required_cash': round(required_cash, 2),
        })
        return {
            'symbol': symbol,
            'timestamp': timestamp,
            'status': 'rejected',
            'reason': 'NO FUNDS',
            'stage': 'broker_funds_check',
            'required_cash': round(required_cash, 2),
            'available_cash': round(available_cash, 2),
            **base_context,
        }

    logger.info(
        f"ALERT_PROCESS: BROKER_FUNDS_OK | symbol={symbol} | available=₹{available_cash:.2f} | required=₹{required_cash:.2f}"
    )
    return None


def _confirm_premium_response(
    *,
    state: Dict[str, Any],
    contract_symbol: str,
    baseline_ltp: float,
    baseline_bid: float,
    baseline_ask: float,
    baseline_spread_pct: Optional[float],
) -> tuple[bool, str, Dict[str, Any]]:
    """Require a small positive premium response before entry placement."""
    if os.getenv("OPTIONS_PREMIUM_RESPONSE_CONFIRM_ENABLED", "true").lower() != "true":
        return True, "premium response confirmation disabled", {}

    if baseline_ltp <= 0:
        return False, "Baseline premium unavailable for confirmation", {
            'baseline_ltp': baseline_ltp,
        }

    sample_count = max(int(os.getenv("OPTIONS_PREMIUM_RESPONSE_CONFIRM_SAMPLES", "2")), 1)
    sample_delay_ms = max(int(os.getenv("OPTIONS_PREMIUM_RESPONSE_CONFIRM_DELAY_MS", "750")), 0)
    min_peak_change_pct = float(os.getenv("OPTIONS_PREMIUM_RESPONSE_MIN_PEAK_CHANGE_PCT", "0.20"))
    max_latest_drawdown_pct = float(os.getenv("OPTIONS_PREMIUM_RESPONSE_MAX_LATEST_DRAWDOWN_PCT", "0.35"))
    max_spread_pct = float(os.getenv("OPTIONS_PREMIUM_RESPONSE_MAX_SPREAD_PCT", "4.0"))
    max_spread_widening_mult = float(os.getenv("OPTIONS_PREMIUM_RESPONSE_MAX_SPREAD_WIDENING_MULT", "1.50"))

    samples = []
    latest_quote = {
        'ltp': baseline_ltp,
        'bid': baseline_bid,
        'ask': baseline_ask,
        'bid_ask_spread_pct': baseline_spread_pct,
        'sample_index': 0,
    }

    for sample_index in range(1, sample_count + 1):
        if sample_index > 1 and sample_delay_ms > 0:
            time.sleep(sample_delay_ms / 1000.0)

        quote = state['broker'].get_market_data(contract_symbol, "NFO") or {}
        ltp = float(quote.get('ltp') or 0.0)
        bid = float(quote.get('bid') or 0.0)
        ask = float(quote.get('ask') or 0.0)
        spread_pct_raw = quote.get('bid_ask_spread_pct')
        spread_pct = float(spread_pct_raw) if spread_pct_raw is not None else None

        if ltp <= 0:
            return False, "Unable to confirm premium response - live quote unavailable", {
                'baseline_ltp': baseline_ltp,
                'sample_index': sample_index,
                'samples_collected': samples,
            }

        latest_quote = {
            'ltp': ltp,
            'bid': bid,
            'ask': ask,
            'bid_ask_spread_pct': spread_pct,
            'sample_index': sample_index,
        }
        samples.append(latest_quote)

    peak_ltp = max([baseline_ltp] + [sample['ltp'] for sample in samples])
    latest_ltp = latest_quote['ltp']
    latest_spread_pct = latest_quote['bid_ask_spread_pct']
    latest_bid = latest_quote['bid']
    latest_ask = latest_quote['ask']
    peak_change_pct = ((peak_ltp - baseline_ltp) / baseline_ltp) * 100.0
    latest_change_pct = ((latest_ltp - baseline_ltp) / baseline_ltp) * 100.0
    allowed_spread_pct = max_spread_pct
    if baseline_spread_pct is not None and baseline_spread_pct > 0:
        allowed_spread_pct = min(max_spread_pct, baseline_spread_pct * max_spread_widening_mult)

    metrics = {
        'baseline_ltp': round(baseline_ltp, 4),
        'baseline_bid': round(baseline_bid, 4),
        'baseline_ask': round(baseline_ask, 4),
        'baseline_spread_pct': round(float(baseline_spread_pct), 4) if baseline_spread_pct is not None else None,
        'latest_ltp': round(latest_ltp, 4),
        'latest_bid': round(latest_bid, 4),
        'latest_ask': round(latest_ask, 4),
        'latest_spread_pct': round(float(latest_spread_pct), 4) if latest_spread_pct is not None else None,
        'peak_ltp': round(peak_ltp, 4),
        'peak_change_pct': round(peak_change_pct, 4),
        'latest_change_pct': round(latest_change_pct, 4),
        'min_peak_change_pct': min_peak_change_pct,
        'max_latest_drawdown_pct': max_latest_drawdown_pct,
        'allowed_spread_pct': round(allowed_spread_pct, 4),
        'sample_delay_ms': sample_delay_ms,
        'sample_count': sample_count,
        'samples': samples,
    }

    if peak_change_pct < min_peak_change_pct:
        return False, (
            f"Premium response weak: peak +{peak_change_pct:.2f}% < required +{min_peak_change_pct:.2f}%"
        ), metrics

    if latest_change_pct < -max_latest_drawdown_pct:
        return False, (
            f"Premium faded during confirmation: {latest_change_pct:.2f}% < -{max_latest_drawdown_pct:.2f}%"
        ), metrics

    if latest_spread_pct is not None and latest_spread_pct > allowed_spread_pct:
        return False, (
            f"Spread widened during confirmation: {latest_spread_pct:.2f}% > {allowed_spread_pct:.2f}%"
        ), metrics

    if latest_bid <= 0 or latest_ask <= 0:
        return False, "Bid/ask unavailable during premium confirmation", metrics

    return True, "Premium response confirmed", metrics


def _round_price_to_tick(price: float, tick_size: float) -> float:
    safe_tick = max(float(tick_size or 0.05), 0.01)
    return round(round(price / safe_tick) * safe_tick, 2)


def _build_entry_order_pricing(*, ltp: float, bid: float, ask: float) -> tuple[str, float]:
    order_type = str(os.getenv("OPTIONS_ENTRY_ORDER_TYPE", "MARKET") or "MARKET").strip().upper()
    if order_type != "LIMIT":
        return "MARKET", 0.0

    limit_mode = str(os.getenv("OPTIONS_ENTRY_LIMIT_PRICE_MODE", "ASK_PLUS_1_TICK") or "ASK_PLUS_1_TICK").strip().upper()
    tick_size = float(os.getenv("OPTIONS_ENTRY_LIMIT_TICK_SIZE", "0.05") or 0.05)

    base_price = max(float(ask or 0.0), float(ltp or 0.0), float(bid or 0.0), 0.0)
    if base_price <= 0:
        return "MARKET", 0.0

    limit_price = float(ask or 0.0) if float(ask or 0.0) > 0 else base_price
    if limit_mode in {"ASK_PLUS_1", "ASK_PLUS_1_TICK", "ASK_PLUS_ONE_TICK"}:
        limit_price += tick_size

    limit_price = max(limit_price, base_price)
    return "LIMIT", _round_price_to_tick(limit_price, tick_size)


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_alert_indicator_snapshot(*, alert: Dict[str, Any], market_trend: str, processed: Dict[str, Any]) -> Dict[str, Any]:
    volume_ratio = _optional_float(alert.get('volume_ratio'))
    macd_hist = _optional_float(alert.get('macd_hist'))
    rsi_value = _optional_float(alert.get('rsi_value'))

    snapshot = {
        'trend_strength': float(alert.get('trend_strength', 0) or 0),
        'market_trend': market_trend,
        'entry_confidence': float(processed.get('confidence', 0) or 0),
        'entry_score': float(processed.get('score', 0) or 0),
        'volume_spike': bool(volume_ratio is not None and volume_ratio >= 1.2),
        'rsi_15m': rsi_value,
        'macd_15m': {'macd': macd_hist} if macd_hist is not None else None,
        'ma_short': None,
        'ma_long': None,
        'slope': None,
        'iv_percentile': None,
        'days_to_expiry': None,
        'pcr': None,
        'oi_buildup': None,
    }
    return snapshot


# Neural ML Signal - DISABLED due to system constraints
# Disabled neural ML to reduce memory footprint and simplify alert processing
# Kept as separate file for future use with proper integration

def _process_options_alert(alert: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """Process single options alert with detailed logging"""
    _entry_ip: set = state.get('entry_in_progress', set())
    _entry_ip_lock: threading.Lock = state.get('entry_in_progress_lock')
    _underlying_reserved = False
    _underlying_for_cleanup = None

    try:
        timestamp = datetime.now().isoformat()
        symbol = alert.get('symbol', 'UNKNOWN')
        
        # Initialize neural ML signal as None (disabled)
        neural_ml_signal = None
        neural_ml_multiplier = 1.0
        
        logger.debug(f"ALERT_PROCESS: START | symbol={symbol} | action={alert.get('action')}")

        # ── LATENCY INSTRUMENTATION (read-only; never affects the entry) ──
        # Sub-stage timing so we can see WHICH part of alert→order is slow (target <2s).
        _entry_timing = {'t0': time.monotonic()}
        try:
            from .options_rate_limiter import get_options_rate_limiter as _g_rl
            _entry_timing['rl0'] = _g_rl().wait_time_total
        except Exception:
            _entry_timing['rl0'] = 0.0

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

        # Reserve this underlying so parallel workers don't double-enter the same stock
        _underlying_for_cleanup = underlying
        if _entry_ip_lock is not None:
            with _entry_ip_lock:
                if underlying in _entry_ip:
                    logger.warning(f"ALERT_PROCESS: WORKER_RACE_BLOCKED | symbol={symbol} | underlying={underlying} | another worker is already processing this underlying")
                    return {
                        'symbol': symbol, 'timestamp': timestamp, 'status': 'rejected',
                        'reason': f'Underlying {underlying} already being processed by another worker',
                        'stage': 'entry_in_progress_check',
                    }
                _entry_ip.add(underlying)
                _underlying_reserved = True

        # If no price in alert, fetch live spot price from broker for correct ATM selection
        # (critical for index underlyings where wrong ATM = wrong contract)
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
        
        # NEW: Check market sentiment (PCR + OI Buildup) for entry decision
        from .market_sentiment import get_market_sentiment
        from .optconfig import SentimentConfig
        
        sentiment_data = None
        if SentimentConfig.ENABLE_SENTIMENT_FILTER:
            try:
                sentiment_engine = get_market_sentiment(state['broker'])
                sentiment_data = sentiment_engine.get_symbol_sentiment(underlying, include_exit_data=False)
                entry_signal = sentiment_data.get('entry_signal') or {}
                entry_ok = bool(entry_signal.get('ok'))
                entry_reason = str(entry_signal.get('reason') or 'Sentiment data unavailable')
                
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
                    logger.info(f"SENTIMENT_DATA: {underlying} | pcr={sentiment_data.get('pcr')} | buildup={sentiment_data.get('oi_long_buildup')} | entry_approved")
            
            except Exception as e:
                # On sentiment check errors, log but allow the trade to proceed
                # (PCR data may be temporarily unavailable from broker)
                logger.warning(f"SENTIMENT_CHECK: WARNING | {underlying} | {str(e)} | proceeding with trade (sentiment data unavailable)")
                # Don't reject - allow trading even if sentiment data is missing
                pass
        
        # ====================================================================
        # MARKET TREND GATE  (Pine Script 7.18-E → market_trend alert field)
        # GOOD    → trade at CAP_PER_TRADE_GOOD    (2x when capital allows)
        # NEUTRAL → trade at CAP_PER_TRADE_NEUTRAL  (1x, default for missing)
        # BAD     → use the configured BAD-trend budget for the underlying
        # ====================================================================
        market_trend   = str(alert.get('market_trend', 'NEUTRAL')).strip().upper()
        cap_this_trade = OptionsCapitalConfig.get_cap_for_symbol_and_trend(underlying, market_trend)
        if cap_this_trade == 0.0:
            logger.warning(
                f"ALERT_PROCESS: MARKET_TREND_BUDGET_REJECTED | symbol={symbol} "
                f"| market_trend={market_trend} | underlying={underlying} | configured budget is zero"
            )
            log_alert(alert=alert, status='bad_market_trend_rejected', details={
                'market_trend': market_trend,
            })
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'BAD market trend — NIFTY bearish, no new entries today',
                'stage': 'market_trend_gate',
                'market_trend': market_trend,
                **base_context,
            }
        logger.debug(
            f"ALERT_PROCESS: MARKET_TREND_OK | symbol={symbol} "
            f"| market_trend={market_trend} | cap_this_trade=₹{cap_this_trade:.0f}"
        )

        limit_rejection = _evaluate_entry_limits(
            alert=alert,
            state=state,
            symbol=symbol,
            underlying=underlying,
            timestamp=timestamp,
            cap_this_trade=cap_this_trade,
            base_context=base_context,
        )
        if limit_rejection:
            return limit_rejection

        # ========================================================================
        # DAILY LOSS CIRCUIT BREAKER
        # Threshold : OPTIONS_DAILY_LOSS_LIMIT_PCT % of budget_used for the day
        # Guard     : only activates after OPTIONS_DAILY_CB_MIN_TRADES trades
        #             (prevents 2 early HARD_SL hits from killing the whole day)
        # Source    : live_data.json → summary.total_pnl / summary.budget_used
        # Disable   : set OPTIONS_DAILY_LOSS_LIMIT_PCT=0 in .env
        # ========================================================================
        pct_limit  = OptionsCapitalConfig.DAILY_LOSS_LIMIT_PCT
        min_trades = OptionsCapitalConfig.DAILY_CB_MIN_TRADES
        if pct_limit > 0 and OptionsTradingConfig.TRADING_MODE != "PAPER":
            live = OptionsCapitalConfig.get_today_live_summary()
            trades_today      = live['trades_today']
            total_pnl         = live['total_pnl']
            total_pnl_percent = live['total_pnl_percent']   # pre-computed by live_data_tracker
            budget_used       = live['budget_used']
            if trades_today < min_trades:
                # Not enough trades yet — skip the check
                logger.debug(
                    f"ALERT_PROCESS: CIRCUIT_BREAKER_SKIP | trades_today={trades_today} "
                    f"< min_trades={min_trades} | warming up"
                )
            elif total_pnl_percent <= -pct_limit:
                logger.warning(
                    f"ALERT_PROCESS: DAILY_LOSS_CIRCUIT_BREAKER | symbol={symbol} "
                    f"| total_pnl_percent={total_pnl_percent:.2f}% "
                    f"≤ -{pct_limit}% limit "
                    f"| today_pnl=₹{total_pnl:.0f} "
                    f"| budget_used=₹{budget_used:.0f} | trades={trades_today}"
                )
                log_alert(alert=alert, status='circuit_breaker_rejected', details={
                    'total_pnl':         round(total_pnl, 2),
                    'total_pnl_percent': round(total_pnl_percent, 2),
                    'limit_pct':         pct_limit,
                    'budget_used':       round(budget_used, 2),
                    'trades_today':      trades_today,
                })
                return {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'status': 'rejected',
                    'reason': (
                        f'Daily loss circuit breaker: '
                        f'{total_pnl_percent:.2f}% ≤ -{pct_limit}% '
                        f'(₹{total_pnl:.0f} on ₹{budget_used:.0f} deployed)'
                    ),
                    'stage': 'daily_loss_circuit_breaker',
                    'budget': round(budget_used, 2),
                    **base_context,
                }
            else:
                logger.debug(
                    f"ALERT_PROCESS: CIRCUIT_BREAKER_OK "
                    f"| total_pnl_percent={total_pnl_percent:.2f}% vs limit=-{pct_limit}% "
                    f"| trades={trades_today}"
                )

        summary = state['monitor'].get_position_summary()
        
        # Fetch option chain with automatic re-auth on Invalid Token errors
        logger.debug(f"ALERT_PROCESS: FETCHING_CHAIN | underlying={underlying}")
        expiry = state['broker'].get_next_expiry(underlying)
        
        # Try to fetch chain with exponential backoff retry logic
        # NOTE: do NOT 'import time' here — it makes `time` a function-local and breaks the
        # latency instrumentation (time.monotonic) at the top of process_alert. Use module-level time.
        chain = None
        max_retries = 3
        retry_delays = [1, 2, 4]  # exponential backoff: 1s, 2s, 4s
        
        for attempt in range(max_retries):
            chain = state['broker'].fetch_option_chain(underlying, expiry, current_price=alert_price if alert_price > 0 else None, light=True)
            
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
                market_data = _build_alert_indicator_snapshot(
                    alert=alert,
                    market_trend=market_trend,
                    processed=processed,
                )
                fetch_results = {}

                # 0. ENTRY PREMIUM from already-fetched chain (ATM CE ltp)
                # Without this, PremiumValidator always sees ₹0.00 and rejects every trade.
                # Use nearest-strike CE rather than exact atm_strike match — in PAPER mode
                # chain.atm_strike is set to raw spot price (e.g. 304.55), not snapped to a
                # real strike, so get_contract(atm_strike, 'CE') always returns None.
                try:
                    ce_contracts = [c for c in chain.contracts.values() if c.contract_type == 'CE' and c.ltp > 0]
                    if ce_contracts:
                        spot_price = float(chain.atm_strike or 0)
                        nearest_ce = min(ce_contracts, key=lambda c: abs(c.strike - spot_price))
                        market_data['entry_premium'] = nearest_ce.ltp
                        logger.debug(f"ENTRY_FILTER: entry_premium from nearest ATM CE | {underlying} | strike={nearest_ce.strike} | ltp=₹{nearest_ce.ltp:.2f}")
                    else:
                        market_data['entry_premium'] = 0
                        logger.debug(f"ENTRY_FILTER: no CE contracts with ltp>0 in chain | {underlying}")
                except Exception as e:
                    market_data['entry_premium'] = 0
                    logger.debug(f"ENTRY_FILTER: entry_premium error | {underlying} | {str(e)[:40]}")

                # 1. MARKET SENTIMENT (PCR + OI)
                try:
                    sentiment = sentiment_data
                    if sentiment is None:
                        sentiment_engine = get_market_sentiment(broker)
                        sentiment = sentiment_engine.get_symbol_sentiment(underlying, include_exit_data=False)
                    pcr_payload = sentiment.get('pcr')
                    if isinstance(pcr_payload, dict):
                        market_data['pcr'] = pcr_payload.get('value')
                    else:
                        market_data['pcr'] = pcr_payload
                    oi_buildup_payload = sentiment.get('oi_long_buildup')
                    if isinstance(oi_buildup_payload, dict):
                        market_data['oi_buildup'] = oi_buildup_payload.get('oi_change')
                    else:
                        market_data['oi_buildup'] = oi_buildup_payload
                    fetch_results['sentiment'] = 'OK' if (market_data['pcr'] is not None or market_data['oi_buildup'] is not None) else 'NO_DATA'
                    if market_data['pcr'] is not None:
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
                    tech_analyzer = None
                    needs_live_tech = any(
                        market_data.get(metric) is None
                        for metric in ('rsi_15m', 'macd_15m', 'ma_short', 'ma_long', 'slope')
                    )
                    if needs_live_tech:
                        tech_analyzer = get_technical_analyzer(broker, underlying)
                    if tech_analyzer:
                        # RSI
                        try:
                            if market_data['rsi_15m'] is None:
                                market_data['rsi_15m'] = tech_analyzer.get_rsi(15)
                            if market_data['rsi_15m'] is not None:
                                logger.debug(f"ENTRY_FILTER: RSI fetched | {underlying} | RSI={market_data['rsi_15m']:.2f}")
                                fetch_results['rsi'] = fetch_results.get('rsi', 'OK')
                            else:
                                fetch_results['rsi'] = 'NO_DATA'
                        except Exception as e:
                            fetch_results['rsi'] = 'RSI_ERROR'
                            market_data['rsi_15m'] = None
                            logger.debug(f"ENTRY_FILTER: RSI error | {underlying} | {str(e)}")
                        
                        # MACD
                        try:
                            if market_data['macd_15m'] is None:
                                market_data['macd_15m'] = tech_analyzer.get_macd(15)
                            if market_data['macd_15m']:
                                logger.debug(f"ENTRY_FILTER: MACD fetched | {underlying}")
                                fetch_results['macd'] = fetch_results.get('macd', 'OK')
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
                    elif needs_live_tech:
                        fetch_results['tech'] = 'ANALYZER_NOT_AVAILABLE'
                        market_data['ma_short'] = None
                        market_data['ma_long'] = None
                        market_data['slope'] = None
                    else:
                        if market_data['rsi_15m'] is not None:
                            fetch_results['rsi'] = 'ALERT'
                        if market_data['macd_15m'] is not None:
                            fetch_results['macd'] = 'ALERT'
                        fetch_results['tech'] = 'ALERT_ONLY'
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

                top_gainer_max_rank = int(os.getenv('CALL_OPTIONS_REENTRY_TOP_GAINERS_MAX_RANK', '15'))
                market_data['top_gainer_rank'] = None
                market_data['is_top_gainer'] = None
                if bool(processed.get('is_reentry_setup')) or str(alert.get('entry_type', '') or '').upper() == 'PRE_BREAKOUT':
                    broker = state.get('broker')
                    if broker and hasattr(broker, 'get_top_gainers'):
                        try:
                            top_gainers = broker.get_top_gainers(limit=top_gainer_max_rank) or []
                            normalized_top = []
                            for item in top_gainers:
                                if isinstance(item, dict):
                                    normalized_top.append(str(item.get('symbol', '')).upper())
                                else:
                                    normalized_top.append(str(item).upper())
                            try:
                                rank = normalized_top.index(underlying.upper()) + 1
                            except ValueError:
                                rank = None
                            market_data['top_gainer_rank'] = rank
                            market_data['is_top_gainer'] = rank is not None
                            logger.debug(
                                f"ENTRY_FILTER: TOP_GAINERS | {underlying} | rank={rank} | max_rank={top_gainer_max_rank}"
                            )
                        except Exception as e:
                            logger.debug(f"ENTRY_FILTER: TOP_GAINERS_FETCH_FAILED | {underlying} | {str(e)}")

                def _optional_int(value):
                    if value in (None, ""):
                        return None
                    try:
                        return int(float(value))
                    except (TypeError, ValueError):
                        return None
                
                # Build entry signal with validated confidence from signal filter
                entry_signal = {
                    'symbol': symbol,
                    'action': action,
                    'confidence': processed.get('confidence', 0.5),
                    'entry_type': str(alert.get('entry_type', '') or '').upper(),
                    'market_trend': market_trend,
                    'trend_strength': market_data.get('trend_strength', 0),
                    'momentum_score': _optional_float(alert.get('momentum_score')),
                    'rsi_value': _optional_float(alert.get('rsi_value')),
                    'rsi_expansion': _optional_float(alert.get('rsi_expansion')),
                    'macd_hist': _optional_float(alert.get('macd_hist')),
                    'vwap_distance': _optional_float(alert.get('vwap_distance')),
                    'volume_ratio': _optional_float(alert.get('volume_ratio')),
                    'ema_spread': _optional_float(alert.get('ema_spread')),
                    'atr_pc': _optional_float(alert.get('atr_pc')),
                    'adx': _optional_float(alert.get('adx')),
                    'volume_cooling': str(alert.get('volume_cooling', 'false')).lower() == 'true',
                    'score': _optional_float(alert.get('score')),
                    'day_change': _optional_float(alert.get('day_change')),
                    'setup_sequence': _optional_int(alert.get('setup_sequence')),
                    'is_reentry_setup': bool(processed.get('is_reentry_setup')),
                    'tv_trigger_flag': str(alert.get('tv_trigger_flag', '') or '').upper(),
                    'tv_setup_label': str(alert.get('tv_setup_label', '') or ''),
                    'reentry_context_active': str(alert.get('reentry_context_active', 'false')).lower() == 'true',
                }
                filter_inputs = _compact_entry_filter_inputs(entry_signal, market_data, fetch_results)
                
                # Validate entry with whatever data we have
                is_entry_valid, entry_reason, entry_details = state['entry_filter'].validate(entry_signal, market_data)
                probation_snapshot = _extract_probation_snapshot(entry_details)
                _log_entry_filter_decision(symbol, action, is_entry_valid, entry_reason, entry_details, data_available, filter_inputs)
                
                if not is_entry_valid:
                    logger.warning(f"ENTRY_FILTER: REJECTED | symbol={symbol} | action={action} | reason={entry_reason} | data_collected={data_available}")
                    return {
                        'symbol': symbol,
                        'timestamp': timestamp,
                        'status': 'rejected',
                        'reason': entry_reason,
                        'stage': 'entry_filter',
                        'filter_details': entry_details,
                        'filter_inputs': filter_inputs,
                        'probation_snapshot': probation_snapshot,
                        'data_fetch_status': fetch_results,
                        **base_context,
                    }
                
                validators_passed = entry_details.get('validators_passed', 0)
                logger.info(f"ENTRY_FILTER: PASSED | symbol={symbol} | action={action} | validators_passed={validators_passed} | data_collected={data_available}")
                try: _entry_timing['t_filter'] = time.monotonic()
                except Exception: pass
                base_context['probation_snapshot'] = probation_snapshot
                entry_context = _build_position_entry_context(alert, processed, market_trend, filter_inputs, entry_details)
            
            except Exception as e:
                # Log error but continue (don't block entry on filter error)
                logger.error(f"ENTRY_FILTER: EXCEPTION | symbol={symbol} | {str(e)} | continuing anyway")
                # Don't reject - let the trade proceed if other checks pass
                entry_context = _build_position_entry_context(alert, processed, market_trend, None, None)
        else:
            entry_context = _build_position_entry_context(alert, processed, market_trend, None, None)
        
        # Get ATM contracts with offset
        # Use alert's price as current price for ATM calculation (already extracted above)
        contract_type = processed['recommended_contract']
        
        # VALIDATION: Check if fetched chain has strikes to cover alert price
        # If alert price is outside available strikes, refresh chain with expanded range
        if chain and hasattr(chain, 'contracts') and chain.contracts:
            # Extract available CE strikes
            ce_strikes = []
            for contract in chain.contracts.values():
                if contract.contract_type == 'CE':
                    ce_strikes.append(contract.strike)
            
            if ce_strikes and alert_price > 0:
                min_strike = min(ce_strikes)
                max_strike = max(ce_strikes)
                
                # If alert price is outside the available range, fetch fresh chain
                if alert_price < min_strike or alert_price > max_strike:
                    gap = max(alert_price - max_strike, min_strike - alert_price) if alert_price > max_strike else min_strike - alert_price
                    logger.warning(f"ALERT_PROCESS: STALE_CHAIN | {underlying} | alert_price=₹{alert_price} | available_strikes=[₹{min_strike}-₹{max_strike}] | gap=₹{abs(gap)} | re-fetching with expanded range")
                    
                    # Fetch fresh chain to ensure we have the right strikes
                    fresh_chain = state['broker'].fetch_option_chain(underlying, expiry, current_price=alert_price, force_refresh=True, light=True)
                    if fresh_chain:
                        chain = fresh_chain
                        logger.info(f"ALERT_PROCESS: CHAIN_REFRESHED | {underlying} | using fresh data for alert_price=₹{alert_price}")
        
        ce, pe = chain.get_atm_contracts(alert_price, processed['strike_offset']) or (None, None)
        
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
        
        # Select contract based on action
        selected_contract = ce if contract_type == 'CE' else pe

        # LIGHT chain skipped per-contract greeks (the ~2s bottleneck). Compute REAL greeks for
        # the SELECTED contract ONLY — this is the only contract whose greeks are used (greeks
        # check + ML + exit analysis). Selection above is by strike, so this changes nothing
        # about WHICH contract we trade.
        try:
            from .angelone_options import estimate_greeks as _estimate_greeks
            _dte = 7
            try:
                _exp_dt = datetime.strptime(expiry, "%Y-%m-%d")
                _dte = max(1, (_exp_dt - datetime.now()).days)
            except Exception:
                pass
            _sel_greeks = _estimate_greeks(
                underlying=underlying,
                strike=selected_contract.strike,
                spot=alert_price if alert_price > 0 else selected_contract.strike,
                contract_type=contract_type,
                time_to_expiry_days=_dte,
                iv=0.25,
            )
            selected_contract.delta = _sel_greeks.get('delta', 0.5 if contract_type == 'CE' else -0.5)
            selected_contract.gamma = _sel_greeks.get('gamma', 0.05)
            selected_contract.theta = _sel_greeks.get('theta', -0.02)
            selected_contract.vega = _sel_greeks.get('vega', 0.1)
            logger.debug(f"SELECTED_GREEKS: {selected_contract.symbol} | D={selected_contract.delta:.3f} G={selected_contract.gamma:.4f} (light-chain, selected-only)")
        except Exception as _ge:
            logger.warning(f"SELECTED_GREEKS: estimation failed | {selected_contract.symbol} | {str(_ge)} — using chain defaults")

        contract_context = {
            **base_context,
            'contract': selected_contract.symbol,
            'contract_type': contract_type,
            'strike': selected_contract.strike,
            'expiry': expiry,
            'entry_premium': selected_contract.ltp,
        }
        
        logger.debug(f"ALERT_PROCESS: SELECTED | contract={selected_contract.symbol} | type={contract_type} | ltp=₹{selected_contract.ltp:.2f}")
        
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
        
        # CHECK MINIMUM PREMIUM: reject only very low premium trades
        min_premium = float(os.getenv("ENTRY_FILTER_MIN_PREMIUM", "3.0"))
        if selected_contract.ltp < min_premium:
            logger.warning(f"ALERT_PROCESS: PREMIUM_TOO_LOW | symbol={symbol} | contract={selected_contract.symbol} | ltp=₹{selected_contract.ltp:.2f} < ₹{min_premium}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': f'Premium too low: ₹{selected_contract.ltp:.2f} < ₹{min_premium} (low liquidity, high gap risk)',
                'stage': 'minimum_premium_check',
                **contract_context,
            }
        
        logger.debug(f"ALERT_PROCESS: PREMIUM_OK | contract={selected_contract.symbol} | ltp=₹{selected_contract.ltp:.2f} >= ₹{min_premium}")
        
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
        
        # Calculate dynamic quantity based on budget utilization in whole exchange lots.
        lot_size = max(state['instrument_manager'].get_lot_size(selected_contract.symbol), 1)
        # get_market_data (getMarketData FULL) already returns OI + volume — same source/extraction
        # as get_oi_data, so a separate OI call is redundant. One call instead of two halves the
        # broker round-trips in this (now-dominant) liquidity step.
        liquidity_market_data = state['broker'].get_market_data(selected_contract.symbol, "NFO") or {}
        live_volume = int(liquidity_market_data.get('volume') or selected_contract.volume or 0)
        live_oi = int(liquidity_market_data.get('open_interest') or selected_contract.open_interest or 0)

        if live_volume <= 0 or live_oi <= 0:
            logger.warning(
                f"ALERT_PROCESS: LIQUIDITY_REFETCH_TRIGGERED | contract={selected_contract.symbol} "
                f"| volume={live_volume:,} | oi={live_oi:,} | retrying once"
            )
            time.sleep(0.25)
            refetch_market_data = state['broker'].get_market_data(selected_contract.symbol, "NFO") or {}
            refetch_volume = int(refetch_market_data.get('volume') or 0)
            refetch_oi = int(refetch_market_data.get('open_interest') or 0)
            if refetch_volume > 0:
                liquidity_market_data = refetch_market_data
                live_volume = refetch_volume
            if refetch_oi > 0:
                live_oi = refetch_oi
            logger.info(
                f"ALERT_PROCESS: LIQUIDITY_REFETCH_RESULT | contract={selected_contract.symbol} "
                f"| volume={live_volume:,} | oi={live_oi:,}"
            )

        try: _entry_timing['t_liquidity'] = time.monotonic()
        except Exception: pass
        live_bid = float(liquidity_market_data.get('bid') or selected_contract.bid or 0.0)
        live_ask = float(liquidity_market_data.get('ask') or selected_contract.ask or 0.0)
        live_spread_pct = liquidity_market_data.get('bid_ask_spread_pct')
        # Detect synthetic bid/ask (chain fallback sets bid=ltp*0.98, ask=ltp*1.02 → ~4.08% flat).
        # Real depth comes from get_market_data; mark when the spread looks synthetic so analysis
        # can exclude it rather than treat it as a real market spread.
        _ltp_ref = float(selected_contract.ltp or 0.0)
        spread_is_synthetic = bool(
            _ltp_ref > 0 and live_bid > 0 and live_ask > 0
            and abs(live_bid - _ltp_ref * 0.98) < 0.01 and abs(live_ask - _ltp_ref * 1.02) < 0.01
        )
        if live_spread_pct is None and live_bid > 0 and live_ask > 0 and _ltp_ref > 0:
            live_spread_pct = (live_ask - live_bid) / _ltp_ref * 100.0
        selected_contract.volume = live_volume
        selected_contract.open_interest = live_oi
        selected_contract.bid = live_bid
        selected_contract.ask = live_ask

        # ── REAL-SPREAD ENTRY GATE (advisory by default) ──────────────────────
        # Logs the real spread on every entry so we can study the slippage distribution in PAPER.
        # Only blocks when OPTIONS_ENTRY_SPREAD_GATE_ENFORCE=true (and the spread isn't synthetic).
        if live_spread_pct is not None and not spread_is_synthetic:
            _spread_breach = live_spread_pct > OptionsTradingConfig.MAX_ENTRY_SPREAD_PCT
            logger.info(
                f"ENTRY_SPREAD_CHECK: {selected_contract.symbol} | spread={live_spread_pct:.2f}% "
                f"| bid=₹{live_bid:.2f} ask=₹{live_ask:.2f} ltp=₹{_ltp_ref:.2f} "
                f"| limit={OptionsTradingConfig.MAX_ENTRY_SPREAD_PCT:.1f}% | breach={_spread_breach} "
                f"| enforce={OptionsTradingConfig.ENTRY_SPREAD_GATE_ENFORCE}"
            )
            if _spread_breach and OptionsTradingConfig.ENTRY_SPREAD_GATE_ENFORCE:
                logger.warning(
                    f"ALERT_PROCESS: SPREAD_TOO_WIDE | {selected_contract.symbol} | "
                    f"spread={live_spread_pct:.2f}% > {OptionsTradingConfig.MAX_ENTRY_SPREAD_PCT:.1f}%"
                )
        contract_context['entry_premium'] = selected_contract.ltp

        entry_order_type, entry_order_price = _build_entry_order_pricing(
            ltp=selected_contract.ltp,
            bid=live_bid,
            ask=live_ask,
        )
        pricing_premium = entry_order_price if entry_order_type == 'LIMIT' and entry_order_price > 0 else selected_contract.ltp
        contract_context['entry_order_type'] = entry_order_type
        contract_context['entry_order_price'] = round(pricing_premium, 2)

        effective_budget = cap_this_trade
        liquidity_scale_attempts = 0
        scaled_down_for_oi = False

        # Lot cap: order lots = min(budget_lots, 10% of OI lots, 10% of volume lots), floored at 1.
        # Never reject for thin OI/volume — always enter at least 1 lot if budget allows.
        if lot_size > 0:
            oi_max_lots  = max(1, int(live_oi     / lot_size * 0.10)) if live_oi     > 0 else None
            vol_max_lots = max(1, int(live_volume  / lot_size * 0.10)) if live_volume > 0 else None
            max_lots = min(x for x in [oi_max_lots, vol_max_lots] if x is not None) if (oi_max_lots or vol_max_lots) else None
            if max_lots:
                lot_cap_budget = max_lots * lot_size * pricing_premium
                if lot_cap_budget < effective_budget:
                    logger.info(
                        f"ALERT_PROCESS: LOT_CAP | symbol={symbol} | contract={selected_contract.symbol} "
                        f"| oi_lots={live_oi // lot_size if live_oi else 'n/a'} oi_max={oi_max_lots} "
                        f"| vol_lots={live_volume // lot_size if live_volume else 'n/a'} vol_max={vol_max_lots} "
                        f"| capped_lots={max_lots} | budget: ₹{effective_budget:.0f} → ₹{lot_cap_budget:.0f}"
                    )
                    effective_budget = lot_cap_budget
                    scaled_down_for_oi = True

        quantity = 0
        affordable_lots = 0
        actual_cost = 0.0
        utilization_pct = 0.0
        order_context = {**contract_context, 'market_trend': market_trend}
        liquidity_ok = False
        liquidity_reason = ""
        liquidity_metrics = {}

        while True:
            quantity = OptionsCapitalConfig.calculate_quantity_for_capital(
                premium=pricing_premium,
                capital=effective_budget,
                lot_size=lot_size,
            )
            affordable_lots = quantity // lot_size if lot_size > 0 else 0

            if quantity <= 0:
                lot_cost = pricing_premium * lot_size
                logger.warning(
                    f"ALERT_PROCESS: LOT_NOT_AFFORDABLE | contract={selected_contract.symbol} | premium=₹{pricing_premium:.2f} "
                    f"| lot_size={lot_size} | lot_cost=₹{lot_cost:.2f} | budget=₹{effective_budget:.2f}"
                )
                return {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'status': 'rejected',
                    'reason': f'Cannot afford 1 lot within ₹{effective_budget:.0f} budget',
                    'stage': 'lot_sizing',
                    **contract_context,
                }

            if neural_ml_signal and 'neural_ml_multiplier' in locals() and affordable_lots > 0:
                original_quantity = quantity
                adjusted_lots = min(affordable_lots, max(1, int(affordable_lots * neural_ml_multiplier)))
                quantity = adjusted_lots * lot_size
                logger.info(
                    f"NEURAL_ML: POSITION_SIZE_APPLIED | symbol={symbol} | multiplier={neural_ml_multiplier:.2f}x "
                    f"| lots: {affordable_lots} → {adjusted_lots} | qty: {original_quantity} → {quantity}"
                )

            actual_cost = quantity * pricing_premium
            utilization_pct = (actual_cost / effective_budget) * 100 if effective_budget > 0 else 0
            order_context = {
                **contract_context,
                'quantity': quantity,
                'lot_size': lot_size,
                'order_lots': quantity // lot_size if lot_size > 0 else 0,
                'actual_cost': round(actual_cost, 2),
                'budget': effective_budget,
                'requested_budget': cap_this_trade,
                'scaled_down_for_oi': scaled_down_for_oi,
                'liquidity_scale_attempts': liquidity_scale_attempts,
                'market_trend': market_trend,
            }

            liquidity_ok, liquidity_reason, liquidity_metrics = OptionsCapitalConfig.evaluate_liquidity_for_order(
                budget=effective_budget,
                quantity=quantity,
                premium=pricing_premium,
                volume=live_volume,
                open_interest=live_oi,
                bid=live_bid,
                ask=live_ask,
                bid_ask_spread_pct=live_spread_pct,
                lot_size=lot_size,
            )

            logger.info(
                f"ALERT_PROCESS: LIQUIDITY_CHECK | contract={selected_contract.symbol} | qty={quantity} | lots={quantity // lot_size} "
                f"| budget=₹{effective_budget:.0f} | volume={live_volume:,} | oi={live_oi:,} | bid={live_bid:.2f} | ask={live_ask:.2f} "
                f"| spread_pct={float(live_spread_pct or 0.0):.2f} | result={liquidity_ok} | attempt={liquidity_scale_attempts}"
            )

            if liquidity_metrics.get('spread_advisory_reason'):
                logger.warning(
                    f"ALERT_PROCESS: SPREAD_ADVISORY | symbol={symbol} | contract={selected_contract.symbol} "
                    f"| requested_budget=₹{cap_this_trade:.0f} | effective_budget=₹{effective_budget:.0f} "
                    f"| reason={liquidity_metrics['spread_advisory_reason']}"
                )

            if liquidity_ok:
                break

            is_oi_failure = liquidity_reason.startswith("Open interest") or " of OI " in liquidity_reason
            next_budget = effective_budget / 2.0
            next_quantity = OptionsCapitalConfig.calculate_quantity_for_capital(
                premium=pricing_premium,
                capital=next_budget,
                lot_size=lot_size,
            )

            if not is_oi_failure or next_quantity <= 0:
                logger.warning(
                    f"ALERT_PROCESS: REJECTED_LIQUIDITY | symbol={symbol} | contract={selected_contract.symbol} "
                    f"| requested_budget=₹{cap_this_trade:.0f} | effective_budget=₹{effective_budget:.0f} "
                    f"| reason={liquidity_reason} | metrics={liquidity_metrics}"
                )
                _append_liquidity_decision_log('options', {
                    'decision': 'rejected',
                    'symbol': symbol,
                    'contract': selected_contract.symbol,
                    'requested_budget': round(cap_this_trade, 2),
                    'effective_budget': round(effective_budget, 2),
                    'scale_attempts': liquidity_scale_attempts,
                    'scaled_down_for_oi': scaled_down_for_oi,
                    'quantity': quantity,
                    'lot_size': lot_size,
                    'premium': round(pricing_premium, 2),
                    'volume': live_volume,
                    'open_interest': live_oi,
                    'bid': round(live_bid, 2),
                    'ask': round(live_ask, 2),
                    'spread_pct': round(float(live_spread_pct or 0.0), 4),
                    'reason': liquidity_reason,
                    'liquidity_metrics': liquidity_metrics,
                })
                return {
                    'symbol': symbol,
                    'timestamp': timestamp,
                    'status': 'rejected',
                    'reason': liquidity_reason,
                    'liquidity_metrics': liquidity_metrics,
                    'stage': 'dynamic_liquidity_check',
                    **order_context,
                }

            logger.warning(
                f"ALERT_PROCESS: LIQUIDITY_SCALE_DOWN | symbol={symbol} | contract={selected_contract.symbol} "
                f"| requested_budget=₹{cap_this_trade:.0f} | effective_budget=₹{effective_budget:.0f} → ₹{next_budget:.0f} "
                f"| reason={liquidity_reason}"
            )
            effective_budget = next_budget
            liquidity_scale_attempts += 1
            scaled_down_for_oi = True
        
        if scaled_down_for_oi:
            logger.warning(
                f"ALERT_PROCESS: LIQUIDITY_SCALE_DOWN_ACCEPTED | symbol={symbol} | contract={selected_contract.symbol} "
                f"| requested_budget=₹{cap_this_trade:.0f} | effective_budget=₹{effective_budget:.0f} "
                f"| qty={quantity} | lots={quantity // lot_size} | attempts={liquidity_scale_attempts}"
            )

        _append_liquidity_decision_log('options', {
            'decision': 'accepted',
            'symbol': symbol,
            'contract': selected_contract.symbol,
            'requested_budget': round(cap_this_trade, 2),
            'effective_budget': round(effective_budget, 2),
            'scale_attempts': liquidity_scale_attempts,
            'scaled_down_for_oi': scaled_down_for_oi,
            'quantity': quantity,
            'lot_size': lot_size,
            'premium': round(pricing_premium, 2),
            'volume': live_volume,
            'open_interest': live_oi,
            'bid': round(live_bid, 2),
            'ask': round(live_ask, 2),
            'spread_pct': round(float(live_spread_pct or 0.0), 4),
            'spread_advisory_reason': liquidity_metrics.get('spread_advisory_reason'),
            'liquidity_metrics': liquidity_metrics,
        })

        logger.debug(f"ALERT_PROCESS: DYNAMIC_LOT_SIZING | contract={selected_contract.symbol} | premium=₹{pricing_premium:.2f} | order_type={entry_order_type} | lot_size={lot_size} | budget=₹{effective_budget} | requested_budget=₹{cap_this_trade} | market_trend={market_trend} | qty={quantity} | lots={quantity // lot_size} | actual_cost=₹{actual_cost:.2f} | utilization={utilization_pct:.1f}% | scaled_down_for_oi={scaled_down_for_oi}")

        funds_rejection = _evaluate_broker_funds(
            alert=alert,
            state=state,
            symbol=symbol,
            timestamp=timestamp,
            required_cash=actual_cost,
            base_context=order_context,
        )
        if funds_rejection:
            return funds_rejection
        
        logger.info(f"ALERT_PROCESS: PLACING_ORDER | contract={selected_contract.symbol} | qty={quantity} | order_type={entry_order_type} | price=₹{pricing_premium:.2f}")
        
        # Send order placement alert
        if state['alert_manager']:
            try:
                state['alert_manager'].alert_order_placed(
                    bot_type='options',
                    order_details={
                        'symbol': selected_contract.symbol,
                        'action': 'BUY',
                        'quantity': quantity,
                        'price': pricing_premium,
                        'order_type': entry_order_type,
                    }
                )
            except Exception as e:
                logger.warning(f"ORDER_PLACEMENT_ALERT: FAILED | {str(e)}")
        
        # BUG FIX: Retry BUY order up to 3 times with exponential backoff.
        # Also treat QUEUED_ markers as failure — the BUY hasn't reached the exchange yet.
        order_id = None
        for _attempt in range(3):
            _raw = state['broker'].place_options_order(
                symbol=selected_contract.symbol,
                action='BUY',
                quantity=quantity,
                price=pricing_premium,
                order_type=entry_order_type,
                allow_queue=False,
            )
            if _raw and not str(_raw).startswith("QUEUED_"):
                order_id = _raw
                break
            logger.warning(f"ALERT_PROCESS: BUY_ATTEMPT_{_attempt + 1}_FAILED | contract={selected_contract.symbol} | result={_raw}")
            if _attempt < 2:
                time.sleep(2 ** _attempt)  # 1s then 2s backoff before retry
        
        if not order_id:
            broker_error = getattr(state['broker'], 'last_order_error', '') if state.get('broker') else ''
            broker_error_code = getattr(state['broker'], 'last_order_error_code', '') if state.get('broker') else ''
            rejection_reason = 'NO FUNDS' if broker_error_code == 'NO_FUNDS' else 'Failed to place BUY order after 3 attempts'
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
                        reason=rejection_reason
                    )
                except Exception as e:
                    logger.warning(f"ORDER_REJECTION_ALERT: FAILED | {str(e)}")
            
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': rejection_reason,
                'stage': 'broker_order_placement',
                'broker_error': broker_error or None,
                **order_context,
            }
        
        logger.info(f"ALERT_PROCESS: ORDER_PLACED | order_id={order_id} | attempts={_attempt + 1}")

        # ── EMIT ENTRY_TIMING breakdown (read-only) — drives the <2s latency target ──
        try:
            _now = time.monotonic()
            from .options_rate_limiter import get_options_rate_limiter as _g_rl
            _rl_wait = _g_rl().wait_time_total - _entry_timing.get('rl0', 0.0)
            _tf = _entry_timing.get('t_filter', _entry_timing['t0'])
            _tl = _entry_timing.get('t_liquidity', _tf)
            log_event(
                "ENTRY_TIMING",
                f"⏱ entry pipeline timing for {selected_contract.symbol}",
                symbol=selected_contract.symbol,
                chain_filter_ms=round((_tf - _entry_timing['t0']) * 1000, 1),
                select_liquidity_ms=round((_tl - _tf) * 1000, 1),
                pricing_order_ms=round((_now - _tl) * 1000, 1),
                total_ms=round((_now - _entry_timing['t0']) * 1000, 1),
                rate_limit_wait_ms=round(_rl_wait * 1000, 1),
            )
            logger.info(
                f"ENTRY_TIMING: {selected_contract.symbol} | total={round((_now - _entry_timing['t0']) * 1000)}ms "
                f"| chain+filter={round((_tf - _entry_timing['t0']) * 1000)}ms "
                f"| sel+liq={round((_tl - _tf) * 1000)}ms | pricing+order={round((_now - _tl) * 1000)}ms "
                f"| rl_wait={round(_rl_wait * 1000)}ms"
            )
        except Exception as _te:
            logger.debug(f"ENTRY_TIMING: skipped | {_te}")

        actual_entry_premium = pricing_premium

        if OptionsTradingConfig.TRADING_MODE == "LIVE" and state.get('broker'):
            logger.info(f"BUY_CONFIRMATION: WAITING | {selected_contract.symbol} | order_id={order_id} | timeout=30s")
            _bc_t0 = time.monotonic()  # LIVE order→fill latency (PAPER never exercises this)
            confirmed = state['broker'].wait_for_buy_confirmation(selected_contract.symbol, timeout=30, order_id=order_id)
            if not confirmed:
                order_status = state['broker'].get_order_status(order_id) or {}
                status = order_status.get('status', 'UNKNOWN')

                if status not in {'COMPLETE', 'FILLED', 'FULLY_FILLED'}:
                    cancel_success = state['broker'].cancel_order(order_id, selected_contract.symbol, order_type=entry_order_type)
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

            _buy_confirm_ms = (time.monotonic() - _bc_t0) * 1000.0
            fill_status = state['broker'].get_order_status(order_id) or {}
            fill_price = float(fill_status.get('average_price') or 0.0)
            if fill_price > 0:
                actual_entry_premium = fill_price
                # Real LIVE execution slippage: actual fill vs the price we tried to buy at,
                # and vs the LTP at decision-time (the slippage that historically bled PnL).
                _exec_slip = ((actual_entry_premium - pricing_premium) / pricing_premium * 100.0) if pricing_premium else 0.0
                _dec_ltp = float(getattr(selected_contract, 'ltp', 0.0) or 0.0)
                _ltp_slip = ((actual_entry_premium - _dec_ltp) / _dec_ltp * 100.0) if _dec_ltp else 0.0
                logger.info(
                    f"BUY_CONFIRMATION: FILL_PRICE_CAPTURED | {selected_contract.symbol} | "
                    f"order_id={order_id} | broker_avg=₹{actual_entry_premium:.2f} | order_price=₹{pricing_premium:.2f} "
                    f"| decision_ltp=₹{_dec_ltp:.2f} | exec_slippage={_exec_slip:+.2f}% | ltp_slippage={_ltp_slip:+.2f}% "
                    f"| confirm_ms={_buy_confirm_ms:.0f}"
                )
                log_event(
                    "BUY_FILL_SLIPPAGE",
                    f"💱 LIVE entry fill for {selected_contract.symbol}",
                    symbol=selected_contract.symbol, order_id=order_id,
                    broker_avg=round(actual_entry_premium, 2), order_price=round(pricing_premium, 2),
                    decision_ltp=round(_dec_ltp, 2), exec_slippage_pct=round(_exec_slip, 2),
                    ltp_slippage_pct=round(_ltp_slip, 2), confirm_ms=round(_buy_confirm_ms, 0),
                )
            else:
                logger.warning(
                    f"BUY_CONFIRMATION: FILL_PRICE_UNAVAILABLE | {selected_contract.symbol} | "
                    f"order_id={order_id} | using_order_price=₹{pricing_premium:.2f} | confirm_ms={_buy_confirm_ms:.0f}"
                )

            # PARTIAL-FILL GUARD: size the position (and therefore the SL) to the qty actually
            # filled, not the qty requested. If a market BUY partials, using the requested qty
            # would place an SL for MORE than we own → the unfilled portion sells SHORT.
            try:
                _filled_qty = int(fill_status.get('filled_quantity') or 0)
                if 0 < _filled_qty < quantity:
                    logger.warning(
                        f"BUY_CONFIRMATION: PARTIAL_FILL | {selected_contract.symbol} | "
                        f"requested={quantity} | filled={_filled_qty} → sizing position/SL to filled qty"
                    )
                    log_event("PARTIAL_FILL", f"Partial BUY fill for {selected_contract.symbol}",
                              symbol=selected_contract.symbol, requested_qty=quantity,
                              filled_qty=_filled_qty, order_id=order_id)
                    quantity = _filled_qty
            except Exception as _pfe:
                logger.debug(f"BUY_CONFIRMATION: filled-qty check skipped | {_pfe}")

        # ── ENTRY SLIPPAGE MODELING + METADATA (PAPER analysis for LIVE readiness) ──
        # LTP is the "ideal" entry the old PAPER booked; a real BUY fills at the ask.
        # In PAPER (modeling ON) we book actual_entry_premium at the real ask so PnL reflects
        # entry slippage. Metadata is captured on entry_context (persists to positions.jsonl) and
        # logged as an event regardless of mode, so LIVE can be compared against this baseline.
        _entry_ideal_ltp = float(selected_contract.ltp or pricing_premium or 0.0)
        _real_ask = float(live_ask or 0.0)
        _real_bid = float(live_bid or 0.0)
        entry_slippage_meta = {
            'ideal_ltp': round(_entry_ideal_ltp, 2),
            'real_bid': round(_real_bid, 2),
            'real_ask': round(_real_ask, 2),
            'spread_pct': round(float(live_spread_pct), 3) if live_spread_pct is not None else None,
            'spread_is_synthetic': spread_is_synthetic,
            'order_type': entry_order_type,
            'order_price': round(float(pricing_premium), 2),
            'mode': OptionsTradingConfig.TRADING_MODE,
        }
        if (OptionsTradingConfig.TRADING_MODE != "LIVE"
                and OptionsTradingConfig.PAPER_SLIPPAGE_MODELING
                and _real_ask > 0 and not spread_is_synthetic):
            actual_entry_premium = _real_ask
            entry_slippage_meta['applied'] = True
        else:
            entry_slippage_meta['applied'] = False
        entry_slippage_meta['fill'] = round(float(actual_entry_premium), 2)
        entry_slippage_meta['slippage_pct'] = (
            round((actual_entry_premium - _entry_ideal_ltp) / _entry_ideal_ltp * 100, 3)
            if _entry_ideal_ltp > 0 else 0.0
        )
        try:
            if isinstance(entry_context, dict):
                entry_context['entry_slippage'] = entry_slippage_meta
        except Exception:
            pass
        log_event(
            "SLIPPAGE_ENTRY",
            f"📐 Entry slippage modeled for {selected_contract.symbol}",
            symbol=selected_contract.symbol,
            **entry_slippage_meta,
        )
        logger.info(
            f"SLIPPAGE_ENTRY: {selected_contract.symbol} | ideal_ltp=₹{_entry_ideal_ltp:.2f} "
            f"| ask=₹{_real_ask:.2f} | fill=₹{actual_entry_premium:.2f} "
            f"| slippage={entry_slippage_meta['slippage_pct']:.2f}% | applied={entry_slippage_meta['applied']} "
            f"| synthetic_spread={spread_is_synthetic}"
        )

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
        
        # Sector strength is post-order ENRICHMENT (it fetches LTP for ~5 sector peers ≈ 1s) and
        # is NOT an entry gate. It used to run here, BEFORE add_position — blocking SL placement and
        # adding ~1s to position_created. Moved to a background thread after the position is in
        # (see below), so order→SL→position stays fast. Position starts with placeholder sector_data.
        sector_data = {
            'sector': 'UNKNOWN',
            'sector_rsi': None,
            'sector_performance': None,
            'sector_participation': None,
            'sector_bullish': None
        }

        # 🔧 FIX: Check if position was successfully added before incrementing counter
        position_added = state['monitor'].add_position(
            symbol=selected_contract.symbol,
            underlying=underlying,
            strike=selected_contract.strike,
            expiry=expiry,
            contract_type=contract_type,
            action='BUY',
            quantity=quantity,
            entry_premium=actual_entry_premium,
            order_id=order_id,
            underlying_alert_price=alert_price if alert_price > 0 else None,
            entry_greeks=entry_greeks_data,  # ADDED: Pass entry Greeks
            sector_data=sector_data,          # ADDED: Pass sector strength data
            market_trend=market_trend,        # NEW: Pine Script market trend
            trend_strength=float(alert.get('trend_strength', 0) or 0),  # NEW: raw emaSpread %
            entry_context=entry_context,
        )

        # BACKGROUND sector enrichment (peer-LTP fetch ~1s) — never blocks order→SL→position.
        # Attaches real sector_data to the position once ready; entry decision already made.
        if position_added and state.get('sector_analyzer'):
            def _bg_sector(_sym=symbol, _contract=selected_contract.symbol):
                try:
                    sa = state['sector_analyzer']
                    sec = sa.get_sector(_sym)
                    if not sec or sec == 'UNKNOWN':
                        logger.warning(f"SECTOR_ENTRY_LOG(bg) | symbol={_sym} | sector=NOT_MAPPED")
                        return
                    sd = {'sector': sec, 'sector_rsi': None, 'sector_performance': None,
                          'sector_participation': None, 'sector_bullish': None}
                    perf = sa.get_sector_performance(sec)
                    if perf:
                        sd['sector_rsi'] = perf.get('rsi')
                        sd['sector_performance'] = perf.get('performance_pct')
                        sd['sector_participation'] = perf.get('participation_pct')
                    try:
                        sd['sector_bullish'], _ = sa.is_sector_bullish(_sym, threshold=60)
                    except Exception:
                        pass
                    pos = state['monitor']._get_position(_contract)
                    if pos:
                        pos.sector_data = sd
                    logger.info(f"SECTOR_ENTRY_LOG(bg) | symbol={_sym} | sector={sec} | bullish={sd['sector_bullish']}")
                except Exception as _se:
                    logger.debug(f"SECTOR_BG: failed | {_sym} | {str(_se)}")
            threading.Thread(target=_bg_sector, daemon=True, name=f"sector-{symbol}").start()

        # 🔧 CRITICAL: Only increment counter if position was actually added (not rejected as duplicate)
        if not position_added:
            logger.warning(f"ALERT_PROCESS: POSITION_NOT_ADDED | symbol={symbol} | contract={selected_contract.symbol} | order_id={order_id} | likely_duplicate")
            if OptionsTradingConfig.TRADING_MODE == "LIVE":
                existing_position = None
                try:
                    for _, tracked_position in state['monitor']._snapshot_positions_items():
                        if tracked_position.underlying == underlying:
                            existing_position = tracked_position
                            break
                except Exception as snapshot_error:
                    logger.warning(f"ALERT_PROCESS: POSITION_TRACKING_SNAPSHOT_FAILED | symbol={symbol} | {snapshot_error}")

                if existing_position:
                    logger.warning(
                        f"ALERT_PROCESS: POSITION_ALREADY_TRACKED_AFTER_FILL | symbol={symbol} | "
                        f"contract={selected_contract.symbol} | existing_symbol={existing_position.symbol} | "
                        f"existing_order_id={existing_position.order_id}"
                    )
                    return {
                        'symbol': symbol,
                        'underlying': underlying,
                        'contract': selected_contract.symbol,
                        'timestamp': timestamp,
                        'status': 'success',
                        'stage': 'position_already_tracked',
                        'order_id': existing_position.order_id or order_id,
                        'message': f'BUY filled; position already tracked as {existing_position.symbol}',
                        'tracked_symbol': existing_position.symbol,
                        'tracked_order_id': existing_position.order_id,
                        **order_context,
                    }

                logger.error(f"ALERT_PROCESS: POSITION_TRACKING_FAILED_AFTER_FILL | symbol={symbol} | contract={selected_contract.symbol} | emergency flattening with confirmation")
                flatten_order_id = state['broker'].place_options_order(
                    symbol=selected_contract.symbol,
                    action='SELL',
                    quantity=quantity,
                    order_type='MARKET',
                    product_type='INTRADAY',
                    allow_queue=False,
                )

                if not flatten_order_id or str(flatten_order_id).startswith("QUEUED_"):
                    return {
                        'symbol': symbol,
                        'timestamp': timestamp,
                        'status': 'rejected',
                        'reason': 'Position add failed after BUY fill and emergency SELL could not be placed',
                        'stage': 'position_registration',
                        'order_id': order_id,
                        'emergency_exit_order_id': flatten_order_id,
                        **order_context,
                    }

                emergency_flatten_filled = False
                emergency_flatten_status = None
                for _ in range(40):
                    emergency_flatten_status = state['broker'].get_order_status(flatten_order_id)
                    if emergency_flatten_status:
                        status = str(emergency_flatten_status.get('status', '')).upper()
                        if status in {'COMPLETE', 'FILLED', 'FULLY_FILLED'}:
                            emergency_flatten_filled = True
                            break
                        if status in {'REJECTED', 'CANCELLED', 'EXPIRED'}:
                            break
                    time.sleep(0.5)

                if not emergency_flatten_filled:
                    status = (emergency_flatten_status or {}).get('status', 'UNKNOWN')
                    logger.error(
                        f"ALERT_PROCESS: EMERGENCY_FLATTEN_UNCONFIRMED | symbol={symbol} | "
                        f"contract={selected_contract.symbol} | exit_order_id={flatten_order_id} | status={status}"
                    )
                    return {
                        'symbol': symbol,
                        'timestamp': timestamp,
                        'status': 'rejected',
                        'reason': f'Position add failed after BUY fill; emergency SELL not confirmed (status={status})',
                        'stage': 'position_registration',
                        'order_id': order_id,
                        'emergency_exit_order_id': flatten_order_id,
                        **order_context,
                    }

            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Position add failed after BUY fill; emergency flatten executed',
                'stage': 'position_registration',
                'order_id': order_id,
                'emergency_exit_order_id': flatten_order_id if OptionsTradingConfig.TRADING_MODE == "LIVE" else None,
                **order_context,
            }
        
        # 🔧 FIX: Increment daily trade counter ONLY after position is successfully added
        # This ensures we count actual trades, not just order attempts
        new_trade_counts = OptionsCapitalConfig.increment_daily_trade_count(underlying)
        logger.info(
            f"DAILY_TRADE_COUNT: Incremented total={new_trade_counts['total']}/{OptionsCapitalConfig.MAX_TRADES_PER_DAY} "
            f"| index={new_trade_counts['index']}/{OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY} "
            f"| non_index={new_trade_counts['non_index']}/{OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY}"
        )
        
        # 🔧 NEW: Log successful position creation at bot level
        log_alert(alert=alert, status='position_created', details={
            'symbol': selected_contract.symbol,
            'order_id': order_id,
            'quantity': quantity,
            'entry_premium': actual_entry_premium,
            'new_trade_count': new_trade_counts['total'],
            'new_index_trade_count': new_trade_counts['index'],
            'new_non_index_trade_count': new_trade_counts['non_index'],
            'entry_context': entry_context,
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
            'entry_premium': actual_entry_premium,
            'quantity': quantity,
            'actual_cost': round(actual_entry_premium * quantity, 2),
            'budget': cap_this_trade,
            'alert_price': alert_price if alert_price > 0 else None,
            'normalized_action': action,
            'market_trend': market_trend,
            'message': f'{action} {contract_type} position opened',
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
    finally:
        # Always release the underlying reservation so future alerts for this stock are not blocked
        if _underlying_reserved and _underlying_for_cleanup and _entry_ip_lock is not None:
            with _entry_ip_lock:
                _entry_ip.discard(_underlying_for_cleanup)

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
