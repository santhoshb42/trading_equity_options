"""
Options Webhook API

Flask server for TradingView alerts integrated into options bot.
Completely independent from equity bot - shares only alert stream.
With extensive logging and alert system integration.
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional

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
    state = {
        'broker': get_options_broker(),
        'monitor': get_option_monitor(),
        'signal_filter': get_options_signal_filter(),
        'instrument_manager': get_instrument_manager(),
        'alert_manager': None,  # Will be set by main bot
        'active': False,
        'startup_time': None
    }
    
    # Initialize alert manager if available
    if ALERT_SYSTEM_AVAILABLE:
        try:
            state['alert_manager'] = AlertManager()
        except Exception as e:
            logger.warning(f"API: ALERT_SYSTEM_INIT_FAILED | {str(e)}")
    
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
        Main webhook endpoint for options trading signals.
        Accepts TradingView alerts and processes them.
        """
        try:
            logger.debug(f"WEBHOOK: Received request | remote_addr={request.remote_addr}")
            
            # Parse request
            data = request.get_json()
            if not data:
                logger.warning("WEBHOOK: Empty request body")
                return jsonify({'error': 'Empty request body'}), 400
            
            logger.debug(f"WEBHOOK: Request data | {type(data).__name__}")
            
            # Extract alert(s)
            alerts = data if isinstance(data, list) else [data]
            logger.info(f"WEBHOOK: Processing {len(alerts)} alert(s)")
            
            results = []
            for idx, alert in enumerate(alerts, 1):
                logger.debug(f"WEBHOOK: Processing alert {idx}/{len(alerts)} | symbol={alert.get('symbol')}")
                result = _process_options_alert(alert, state)
                results.append(result)
                log_alert(alert, result['status'], result)
            
            successful = sum(1 for r in results if r['status'] == 'success')
            logger.info(f"WEBHOOK: Completed | total={len(alerts)} | successful={successful}")
            
            return jsonify({
                'status': 'processed',
                'total': len(alerts),
                'successful': successful,
                'results': results
            }), 200
        
        except Exception as e:
            logger.error(f"WEBHOOK: ERROR | {str(e)}")
            return jsonify({
                'error': f'Webhook error: {str(e)}'
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
    
    # Store state for access in routes
    app.options_state = state
    
    return app

# =============================================================================
# Alert Processing
# =============================================================================

def _process_options_alert(alert: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """Process single options alert with detailed logging"""
    try:
        timestamp = datetime.now().isoformat()
        symbol = alert.get('symbol', 'UNKNOWN')
        
        logger.debug(f"ALERT_PROCESS: START | symbol={symbol} | action={alert.get('action')}")
        
        # Validate signal
        is_valid, processed, reason = state['signal_filter'].validate(alert)
        
        if not is_valid:
            logger.warning(f"ALERT_PROCESS: REJECTED | symbol={symbol} | reason={reason}")
            log_signal_validation(symbol, False, reason)
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': reason
            }
        
        logger.debug(f"ALERT_PROCESS: VALIDATED | symbol={symbol}")
        log_signal_validation(symbol, True)
        
        # Authorized to process
        symbol = processed['symbol']
        underlying = processed['underlying']
        action = processed['action']
        
        logger.debug(f"ALERT_PROCESS: MAPPED | underlying={underlying} | action={action}")
        
        # NEW: Check market sentiment (PCR + OI Buildup) for entry decision
        from .market_sentiment import get_market_sentiment
        from .optconfig import SentimentConfig
        
        if SentimentConfig.ENABLE_SENTIMENT_FILTER:
            try:
                sentiment_engine = get_market_sentiment(state['broker'])
                entry_ok, entry_reason = sentiment_engine.check_entry_signal(underlying)
                
                logger.info(f"SENTIMENT_CHECK: {underlying} | entry_ok={entry_ok} | {entry_reason}")
                
                # IMPORTANT: Make sentiment filter non-blocking
                # Log the result but don't reject the alert due to sentiment
                # This allows trading during market hours when PCR/OI data is available
                # but gracefully continues when data is not available (paper trading, outside hours)
                if not entry_ok:
                    logger.warning(f"ALERT_PROCESS: SENTIMENT_WARNING | symbol={symbol} | {entry_reason} | continuing anyway (sentiment non-blocking)")
                    # Don't return/reject - continue with the trade
                
                if SentimentConfig.LOG_SENTIMENT_CHECKS and entry_ok:
                    sentiment_data = sentiment_engine.get_symbol_sentiment(underlying)
                    logger.info(f"SENTIMENT_DATA: {underlying} | pcr={sentiment_data.get('pcr')} | buildup={sentiment_data.get('oi_long_buildup')}")
            
            except Exception as e:
                logger.warning(f"SENTIMENT_CHECK: ERROR | {underlying} | {str(e)} | continuing anyway")
                # Don't block on sentiment errors - continue with trade
        
        # Fetch option chain
        available_capital = OptionsCapitalConfig.get_available_capital(0)
        if available_capital < OptionsCapitalConfig.CAP_PER_TRADE:
            logger.warning(f"ALERT_PROCESS: INSUFFICIENT_CAPITAL | symbol={symbol} | available={available_capital:.2f}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Insufficient capital'
            }
        
        logger.debug(f"ALERT_PROCESS: CAPITAL_OK | available=₹{available_capital:.2f}")
        
        # Check position slots
        summary = state['monitor'].get_position_summary()
        if summary['open_positions'] >= OptionsCapitalConfig.MAX_SLOTS:
            logger.warning(f"ALERT_PROCESS: MAX_POSITIONS_REACHED | symbol={symbol} | open={summary['open_positions']}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Max positions reached'
            }
        
        logger.debug(f"ALERT_PROCESS: SLOTS_OK | open={summary['open_positions']}/{OptionsCapitalConfig.MAX_SLOTS}")
        
        # Fetch option chain
        logger.debug(f"ALERT_PROCESS: FETCHING_CHAIN | underlying={underlying}")
        expiry = state['broker'].get_next_expiry(underlying)
        alert_price = float(alert.get('price', 0))
        chain = state['broker'].fetch_option_chain(underlying, expiry, current_price=alert_price if alert_price > 0 else None)
        
        if not chain:
            logger.error(f"ALERT_PROCESS: CHAIN_FAILED | underlying={underlying} | expiry={expiry}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Failed to fetch option chain'
            }
        
        logger.debug(f"ALERT_PROCESS: CHAIN_OK | contracts={len(chain.contracts)} | atm={chain.atm_strike}")
        
        # Get ATM contracts with offset
        # Use alert's price as current price for ATM calculation (already extracted above)
        contract_type = processed['recommended_contract']
        ce, pe = chain.get_atm_contracts(alert_price, processed['strike_offset']) or (None, None)
        
        if not ce or not pe:
            logger.error(f"ALERT_PROCESS: NO_ATM_CONTRACTS | symbol={symbol} | ce={ce is not None} | pe={pe is not None}")
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'No ATM contracts available'
            }
        
        logger.debug(f"ALERT_PROCESS: ATM_CONTRACTS | ce={ce.symbol} | pe={pe.symbol}")
        
        # Select contract based on action
        selected_contract = ce if contract_type == 'CE' else pe
        
        logger.debug(f"ALERT_PROCESS: SELECTED | contract={selected_contract.symbol} | type={contract_type} | ltp=₹{selected_contract.ltp:.2f}")
        
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
                'reason': greeks_msg
            }
        
        logger.debug(f"ALERT_PROCESS: GREEKS_OK | delta={selected_contract.delta:.3f} | gamma={selected_contract.gamma:.5f}")
        
        # ML VALIDATION (WITH REAL GREEKS) - Now that we have actual Greeks data
        # If broker doesn't provide Greeks (all zeros), skip ML validation gracefully
        try:
            from .opt_ml_integration import get_ml_integration
            ml_integration = get_ml_integration()
            
            # Check if broker actually provided Greeks data
            greeks_data = selected_contract.to_dict()['greeks']
            has_real_greeks = any([
                greeks_data.get('delta', 0) != 0,
                greeks_data.get('gamma', 0) != 0,
                greeks_data.get('vega', 0) != 0,
                greeks_data.get('theta', 0) != 0
            ])
            
            if not has_real_greeks:
                logger.warning(f"ML_VALIDATION_SKIPPED: symbol={symbol} | contract={selected_contract.symbol} | reason=Broker did not provide Greeks data (all zeros)")
                # Continue without ML validation - broker data limitation
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
                        'ml_details': ml_details
                    }
                
                # Log successful ML validation with details
                logger.info(f"ML_VALIDATION_PASSED: symbol={symbol} | contract={selected_contract.symbol} | pop={ml_details.get('final_pop', 0):.1f}% | "
                           f"greeks_score={ml_details.get('greeks_score', 0):.2f}")
            
        except Exception as e:
            logger.warning(f"ML_VALIDATION_ERROR: symbol={symbol} | {str(e)} | continuing without ML")
            # Don't block on ML errors - continue processing
        
        # Get lot size from instrument manager and apply NO_OF_LOTS multiplier for scaling
        from optcode.optconfig import OptionsTradingConfig
        base_lot_size = state['instrument_manager'].get_lot_size(selected_contract.symbol)
        no_of_lots = OptionsTradingConfig.NO_OF_LOTS
        quantity = base_lot_size * no_of_lots
        
        logger.debug(f"ALERT_PROCESS: LOT_SIZE | contract={selected_contract.symbol} | base_lotsize={base_lot_size} | no_of_lots={no_of_lots} | qty={quantity}")
        
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
        
        order_id = state['broker'].place_options_order(
            symbol=selected_contract.symbol,
            action='BUY',
            quantity=quantity,
            price=selected_contract.ltp,
            order_type='MARKET'
        )
        
        if not order_id:
            logger.error(f"ALERT_PROCESS: ORDER_FAILED | symbol={symbol} | contract={selected_contract.symbol}")
            
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
                        reason="Broker failed to place order"
                    )
                except Exception as e:
                    logger.warning(f"ORDER_REJECTION_ALERT: FAILED | {str(e)}")
            
            return {
                'symbol': symbol,
                'timestamp': timestamp,
                'status': 'rejected',
                'reason': 'Failed to place options order'
            }
        
        logger.info(f"ALERT_PROCESS: ORDER_PLACED | order_id={order_id}")
        
        # Send order filled/success alert
        if state['alert_manager']:
            try:
                state['alert_manager'].alert_order_filled(
                    bot_type='options',
                    order_details={
                        'symbol': selected_contract.symbol,
                        'quantity': quantity,
                        'price': selected_contract.ltp,
                        'order_id': order_id
                    }
                )
            except Exception as e:
                logger.warning(f"ORDER_FILLED_ALERT: FAILED | {str(e)}")
        
        # Add position to monitor
        state['monitor'].add_position(
            symbol=selected_contract.symbol,
            underlying=underlying,
            strike=selected_contract.strike,
            expiry=expiry,
            contract_type=contract_type,
            action='BUY',
            quantity=quantity,
            entry_premium=selected_contract.ltp,
            order_id=order_id,
            underlying_alert_price=alert_price if alert_price > 0 else None
        )
        
        logger.info(f"ALERT_PROCESS: SUCCESS | symbol={symbol} | contract={selected_contract.symbol} | order_id={order_id}")
        
        return {
            'symbol': symbol,
            'contract': selected_contract.symbol,
            'timestamp': timestamp,
            'status': 'success',
            'order_id': order_id,
            'contract_type': contract_type,
            'strike': selected_contract.strike,
            'expiry': expiry,
            'entry_premium': selected_contract.ltp,
            'message': f'{action} {contract_type} position opened'
        }
    
    except Exception as e:
        logger.error(f"ALERT_PROCESS: EXCEPTION | symbol={alert.get('symbol')} | {str(e)}")
        return {
            'symbol': alert.get('symbol', 'UNKNOWN'),
            'timestamp': datetime.now().isoformat(),
            'status': 'error',
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
        """Start webhook server"""
        if not self.app:
            print("❌ Cannot start API server - Flask not available")
            return
        
        print(f"🚀 Starting Options Webhook Server on {host}:{port}")
        print(f"   Endpoint: {WebhookConfig.ENDPOINT}")
        print(f"   Mode: {OptionsTradingConfig.TRADING_MODE}")
        
        self.running = True
        self.app.options_state['active'] = True
        self.app.options_state['startup_time'] = datetime.now()
        
        try:
            self.app.run(host=host, port=port, debug=False, threaded=True)
        except Exception as e:
            print(f"❌ Failed to start API server: {str(e)}")
            self.running = False
    
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
