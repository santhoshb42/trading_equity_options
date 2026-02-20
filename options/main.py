#!/usr/bin/env python3
"""
Options Trading Bot - Main Entry Point

Standalone options bot for derivative trading.
Receives TradingView alerts (same stream as equity bot) but executes independently.
Focus: Directional options trading (Long CE/PE based on trend signals).

Architecture:
- Independent from equity bot (no shared state)
- Shares only TradingView webhook alert stream
- Separate capital allocation, risk management, positions
- Options-specific Greeks and IV monitoring
"""

import signal
import sys
import time
import os
import atexit
import threading
import math
import json
import fcntl
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Add parent directory for alert system
sys.path.insert(0, str(Path(__file__).parent.parent))

from optcode.optconfig import (
    WebhookConfig, OptionsTradingConfig, DevConfig, MonitoringConfig,
    get_optconfig_summary, validate_optconfig, OptionsCapitalConfig
)
from optcode.angelone_options import get_options_broker
from optcode.optmonitor import get_option_monitor, call_with_timeout
from optcode.optapi import get_options_api_server
from optcode.optlogging import logger, log_event, log_state, print_session_summary
from optcode.instrument_manager import get_instrument_manager

# ML Learning engine
try:
    from optcode.options_learning_engine import SymbolPerformanceTracker
    HAS_LEARNING_ENGINE = True
except ImportError:
    HAS_LEARNING_ENGINE = False
    SymbolPerformanceTracker = None

# Live Data Tracking system
try:
    from optcode.live_data_tracker import get_live_data_tracker
    from optcode.live_data_table_formatter import get_table_formatter
    from optcode.live_data_updater import start_live_data_updater_service
    HAS_LIVE_DATA = True
except ImportError:
    HAS_LIVE_DATA = False
    start_live_data_updater_service = None

# EOD Backup handler
try:
    from eod_backup_handler import run_eod_backup
    HAS_EOD_BACKUP = True
except ImportError:
    HAS_EOD_BACKUP = False

# Alert system integration
try:
    from alert_system import AlertManager, AlertLevel, AlertCategory
    ALERT_SYSTEM_AVAILABLE = True
except ImportError:
    ALERT_SYSTEM_AVAILABLE = False
    print("⚠️  Alert system not available - alerts disabled")

# Neural ML Integration - DISABLED
# Disabled due to system constraints (2GB RAM) and candle fetching disabled
# Keep neural_ml_integration.py for future use with proper caching
HAS_NEURAL_ML = False

# =============================================================================
# Options Trading Bot
# =============================================================================

class OptionsTradingBot:
    """Main options trading bot orchestrator"""
    
    def __init__(self):
        self.running = False
        self.startup_time = None
        self.broker = None
        self.monitor = None
        self.api_server = None
        self.position_monitor_thread = None
        self.instrument_manager = None
        self.alert_manager = None
        self.learning_engine = None  # ML learning for trade outcomes
        self.live_data_tracker = None  # Live data tracking
        self.live_data_formatter = None  # Table format generator
        
        self.trading_stats = {
            'orders_placed': 0,
            'orders_filled': 0,
            'orders_rejected': 0,
            'total_pnl': 0.0
        }
    
    def initialize(self):
        """Initialize options bot components"""
        print("\n" + "="*70)
        print("  🎯 OPTIONS TRADING BOT - INITIALIZATION")
        print("="*70 + "\n")
        
        logger.info("BOT_INIT: START")
        
        # Validate configuration
        is_valid, msg = validate_optconfig()
        if not is_valid:
            print(f"❌ Configuration Error: {msg}")
            logger.error(f"BOT_INIT: CONFIG_INVALID | {msg}")
            return False
        
        logger.debug(f"BOT_INIT: CONFIG_VALID")
        
        # Print configuration
        print("📋 Configuration Summary:")
        config = get_optconfig_summary()
        for key, value in config.items():
            print(f"   {key}: {value}")
        
        log_state("Configuration loaded", **config)
        
        print(f"\n🔐 Broker authentication...")
        logger.debug(f"BOT_INIT: AUTHENTICATING")
        self.broker = get_options_broker()
        # NOTE: Broker authenticates automatically in __init__, don't call again to avoid rate limiting
        if self.broker.authenticated:
            print("✅ Broker authenticated")
            logger.info(f"BOT_INIT: AUTH_SUCCESS")
        else:
            print("⚠️ Broker authentication in progress or failed - continuing")
            logger.warning(f"BOT_INIT: AUTH_WAITING | will retry on API calls")
        
        # Initialize monitor
        print(f"\n📊 Initializing position monitor...")
        logger.debug(f"BOT_INIT: MONITOR_INIT")
        self.monitor = get_option_monitor(self.broker)
        print(f"✅ Monitor ready - loaded {len(self.monitor.positions)} existing positions")
        logger.info(f"BOT_INIT: MONITOR_READY | positions_loaded={len(self.monitor.positions)}")
        
        # Initialize instrument manager
        print(f"\n📦 Initializing instrument manager...")
        logger.debug(f"BOT_INIT: INSTRUMENT_MGR_INIT")
        self.instrument_manager = get_instrument_manager()
        stats = self.instrument_manager.get_stats()
        print(f"✅ Instrument manager ready")
        print(f"   - Total instruments: {stats['total_instruments']}")
        print(f"   - F&O stocks: {stats['fo_stocks']}")
        print(f"   - Last updated: {stats['last_updated'] or 'Loading...'}")
        logger.info(f"BOT_INIT: INSTRUMENT_MGR_READY | total={stats['total_instruments']} | fo_stocks={stats['fo_stocks']}")
        
        # Get API server
        self.api_server = get_options_api_server()
        logger.debug(f"BOT_INIT: API_SERVER_READY | port={WebhookConfig.PORT}")
        
        # Initialize ML Learning engine
        print(f"\n🤖 Initializing ML Learning Engine...")
        if HAS_LEARNING_ENGINE:
            try:
                options_learning_file = Path(__file__).parent / "data" / "learning" / "symbol_stats.json"
                self.learning_engine = SymbolPerformanceTracker(symbol_stats_file=options_learning_file)
                print(f"✅ Learning engine ready - will track trade outcomes")
                logger.info(f"BOT_INIT: LEARNING_ENGINE_READY | data_file={options_learning_file}")
            except Exception as e:
                print(f"⚠️  Learning engine failed to initialize: {str(e)}")
                logger.warning(f"BOT_INIT: LEARNING_ENGINE_INIT_FAILED | {str(e)}")
        else:
            print(f"⚠️  Learning engine not available")
            logger.warning(f"BOT_INIT: LEARNING_ENGINE_UNAVAILABLE")
        
        # Initialize Live Data Tracking system
        print(f"\n📊 Initializing Live Data Tracking...")
        if HAS_LIVE_DATA:
            try:
                self.live_data_tracker = get_live_data_tracker()
                self.live_data_formatter = get_table_formatter()
                # Clear daily data at startup
                self.live_data_tracker.clear_daily_data()
                print(f"✅ Live data tracking ready - data will be saved to JSON/CSV/Markdown")
                logger.info(f"BOT_INIT: LIVE_DATA_READY")
            except Exception as e:
                print(f"⚠️  Live data tracking failed to initialize: {str(e)}")
                logger.warning(f"BOT_INIT: LIVE_DATA_INIT_FAILED | {str(e)}")
        else:
            print(f"⚠️  Live data tracking not available")
            logger.warning(f"BOT_INIT: LIVE_DATA_UNAVAILABLE")
        
        # Initialize alert system
        if ALERT_SYSTEM_AVAILABLE:
            print(f"\n🔔 Initializing Alert System...")
            self.alert_manager = AlertManager()
            print(f"✅ Alert system ready - alerts will be logged")
            logger.info(f"BOT_INIT: ALERT_SYSTEM_READY")
            
            # Send startup alert
            try:
                self.alert_manager.alert_bot_started(
                    bot_type='options',
                    config={
                        'mode': OptionsTradingConfig.TRADING_MODE,
                        'capital': OptionsCapitalConfig.MAX_CAPITAL
                    }
                )
            except Exception as e:
                logger.warning(f"BOT_INIT: STARTUP_ALERT_FAILED | {str(e)}")
        else:
            print(f"⚠️  Alert system not available - alerts disabled")
        
        logger.info("BOT_INIT: COMPLETE")
        print("\n" + "="*70)
        print("  ✅ OPTIONS BOT INITIALIZED")
        print("="*70 + "\n")
        
        return True
    
    def start(self):
        """Start options trading bot"""
        if not self.initialize():
            return
        
        self.running = True
        self.startup_time = datetime.now()
        
        print("🚀 STARTING OPTIONS TRADING BOT\n")
        
        # Start position monitor thread
        self._start_position_monitor()
        
        # Start instrument refresh thread
        self._start_instrument_refresh()
        
        # Start live data updater service (keeps live_data.json fresh every 10s)
        if HAS_LIVE_DATA and start_live_data_updater_service:
            start_live_data_updater_service()
        
        # Start CSV updater service (keeps live_data_trades.csv fresh every 30s)
        self._start_csv_updater_service()
        
        # Start EOD cleanup scheduler (to prevent stale positions)
        self._start_eod_cleanup_scheduler()
        
        # Start API server
        self._start_api_server()
    
    def _start_eod_cleanup_scheduler(self):
        """Start EOD cleanup scheduler to prevent stale positions from accumulating"""
        def eod_cleanup_loop():
            """Background thread to clean up stale positions and run learning aggregation at EOD"""
            import schedule
            
            print("📍 EOD Scheduler: Starting (Cleanup + Learning Aggregation)")
            print("   ⏱️ Scheduled time: 15:15 PM (after square-off at 15:12)")
            logger.info("EOD_SCHEDULER: SCHEDULER_START | scheduled_time=15:15 | tasks=cleanup,learning")
            
            # Schedule both cleanup and learning for 3:15 PM daily (after square-off at 3:12 PM)
            schedule.every().day.at("15:15").do(self._eod_full_update)
            
            while self.running:
                try:
                    schedule.run_pending()
                    time.sleep(5)  # Check every 5 seconds if a scheduled task should run
                except Exception as e:
                    logger.error(f"EOD_SCHEDULER: SCHEDULER_ERROR | {str(e)}")
                    time.sleep(60)
        
        eod_cleanup_thread = threading.Thread(
            target=eod_cleanup_loop,
            daemon=True,
            name="OptionsEODCleanup"
        )
        eod_cleanup_thread.start()
        print("   ✅ EOD cleanup scheduler thread started")
        logger.debug("EOD_CLEANUP: THREAD_STARTED")
    
    def _eod_full_update(self):
        """Execute full EOD update: cleanup stale positions + run learning aggregation (NON-BLOCKING)
        
        GUARD: Only runs after market hours (09:15-15:15 is blocked to prevent interference with live trading)
        Scheduled for 15:15 PM after square-off at 15:12 PM
        """
        from datetime import time as dt_time
        
        current_time = datetime.now().time()
        market_open = dt_time(9, 15)  # 9:15 AM
        market_close = dt_time(15, 15)  # 3:15 PM
        
        # GUARD: Prevent EOD aggregation during market hours
        if market_open <= current_time < market_close:
            logger.warning(f"EOD_FULL_UPDATE: BLOCKED | time={current_time.strftime('%H:%M:%S')} is within trading hours (09:15-15:15)")
            print(f"⚠️  EOD Update blocked: Cannot run during market hours (09:15-15:15)")
            print(f"   Current time: {current_time.strftime('%H:%M:%S')}")
            print(f"   EOD will run at: 15:15 (3:15 PM) after square-off at 15:12")
            return
        
        logger.info(f"EOD_FULL_UPDATE: START (async) | timestamp={datetime.now().isoformat()} | market_hours_guard=PASSED")
        
        # Run EOD tasks in a separate thread to NOT BLOCK the monitor
        def eod_async_tasks():
            try:
                self._cleanup_stale_positions()
            except Exception as e:
                logger.error(f"EOD_FULL_UPDATE: CLEANUP_FAILED | {str(e)}")
        
        eod_thread = threading.Thread(
            target=eod_async_tasks,
            daemon=True,
            name="OptionsEODAsyncTasks"
        )
        eod_thread.start()
        logger.info(f"EOD_FULL_UPDATE: Spawned async thread for cleanup (non-blocking)")
        # Return immediately - don't wait for EOD to complete
    
    def _cleanup_stale_positions(self):
        """Clean up stale paper trading positions to prevent daily accumulation"""
        try:
            logger.info("EOD_CLEANUP: START | time=" + datetime.now().isoformat())
            print(f"\n🧹 EOD Cleanup: Running stale position cleanup at {datetime.now().strftime('%H:%M:%S')}")
            
            # Load positions from file
            positions_file = Path(__file__).parent / "data" / "option_positions.json"
            if not positions_file.exists():
                logger.info("EOD_CLEANUP: NO_POSITIONS_FILE")
                print("   ℹ️ No positions file found")
                return
            
            with open(positions_file, 'r') as f:
                data = json.load(f)
            
            positions = data.get('positions', [])
            if not positions:
                logger.info("EOD_CLEANUP: NO_OPEN_POSITIONS")
                print("   ℹ️ No open positions to clean")
                return
            
            # Identify stale positions (entry_premium = 0 or very old)
            stale_positions = []
            cutoff_time = datetime.now() - timedelta(hours=24)  # Positions older than 24 hours
            
            for pos in positions:
                # Mark as stale if: entry_premium is 0 OR position is older than 24 hours
                entry_premium = pos.get('entry_premium', 0)
                entry_time_str = pos.get('entry_time', '')
                
                is_zero_premium = entry_premium == 0 or entry_premium == 0.0
                
                is_old = False
                if entry_time_str:
                    try:
                        entry_time = datetime.fromisoformat(entry_time_str)
                        is_old = entry_time < cutoff_time
                    except Exception:
                        pass
                
                if is_zero_premium or is_old:
                    stale_positions.append(pos)
                    reason = "ZERO_PREMIUM" if is_zero_premium else "OLDER_THAN_24H"
                    symbol = pos.get('symbol', 'UNKNOWN')
                    logger.warning(f"EOD_CLEANUP: STALE_DETECTED | symbol={symbol} | reason={reason}")
            
            if not stale_positions:
                logger.info(f"EOD_CLEANUP: NO_STALE_POSITIONS | total_positions={len(positions)}")
                print(f"   ✅ No stale positions found ({len(positions)} active positions)")
                return
            
            # Archive stale positions
            archive_file = Path(__file__).parent / "data" / "option_positions_archive.json"
            existing_archive = []
            if archive_file.exists():
                with open(archive_file, 'r') as f:
                    archive_data = json.load(f)
                    existing_archive = archive_data.get('positions', [])
            
            # Add closed timestamps to stale positions
            closed_positions = []
            for pos in stale_positions:
                pos['status'] = 'CLOSED'
                pos['closed_at'] = datetime.now().isoformat()
                pos['close_reason'] = 'EOD_STALE_CLEANUP'
                pos['exit_price'] = pos.get('current_premium', pos.get('entry_premium', 0))
                closed_positions.append(pos)
            
            # Save archive
            with open(archive_file, 'w') as f:
                json.dump({
                    'positions': existing_archive + closed_positions,
                    'last_cleanup': datetime.now().isoformat()
                }, f, indent=2)
            
            # RUN EOD ML LEARNING UPDATE (NEW)
            self._run_eod_learning_update()
            
            # RUN EOD BACKUP AND CLEAR LIVE DATA
            if HAS_EOD_BACKUP:
                try:
                    logger.info("EOD_BACKUP: START | Backing up and clearing live data")
                    print(f"\n📦 EOD Backup: Backing up live trading data...")
                    run_eod_backup()
                    logger.info("EOD_BACKUP: SUCCESS | Live data backed up and cleared")
                except Exception as e:
                    logger.error(f"EOD_BACKUP: FAILED | {str(e)}")
                    print(f"   ⚠️  EOD backup failed: {str(e)}")
            
            # Remove stale positions from active file
            active_positions = [p for p in positions if p not in stale_positions]
            with open(positions_file, 'w') as f:
                json.dump({'positions': active_positions}, f, indent=2)
            
            logger.info(f"EOD_CLEANUP: SUCCESS | cleaned={len(stale_positions)} | remaining={len(active_positions)}")
            print(f"   ✅ Cleaned {len(stale_positions)} stale position(s)")
            print(f"   📊 Remaining active positions: {len(active_positions)}")
            
        except Exception as e:
            logger.error(f"EOD_CLEANUP: FAILED | {str(e)}")
            print(f"   ❌ Cleanup failed: {str(e)}")
    
    def _run_eod_learning_update(self):
        """Run end-of-day ML learning update after market close"""
        try:
            logger.info("EOD_LEARNING: START | Analyzing daily trades for ML patterns")
            print(f"\n🤖 EOD Learning: Analyzing trades for ML pattern updates...")
            
            # Step 1: Run the comprehensive EOD data aggregator
            # This parses option_positions.json, option_pnl_history.json, and live_data
            # to build rich ML training data per symbol (Greeks, PnL, movements, IV, etc.)
            try:
                from optcode.eod_learning_aggregator import run_eod_learning
                
                aggregator_result = run_eod_learning()
                if aggregator_result.get('status') == 'success':
                    logger.info(f"EOD_DATA_AGGREGATOR: SUCCESS | symbols={aggregator_result.get('symbols_processed')} | "
                               f"closed_trades={aggregator_result.get('closed_trades_analyzed')} | "
                               f"open_positions={aggregator_result.get('open_positions_analyzed')}")
                    print(f"   ✅ Data aggregation completed")
                    print(f"   📊 Processed {aggregator_result.get('symbols_processed')} symbols")
                    print(f"   📈 Analyzed {aggregator_result.get('closed_trades_analyzed')} closed trades")
                    print(f"   🔄 Processed {aggregator_result.get('open_positions_analyzed')} open positions")
                else:
                    logger.warning(f"EOD_DATA_AGGREGATOR: FAILED | {aggregator_result.get('error', 'Unknown error')}")
                    print(f"   ⚠️ Data aggregation failed: {aggregator_result.get('error', 'Unknown error')}")
            except ImportError:
                logger.warning("EOD_DATA_AGGREGATOR: MODULE_NOT_FOUND | Skipping")
                print("   ⚠️ Data aggregator module not found - skipping")
            except Exception as e:
                logger.error(f"EOD_DATA_AGGREGATOR: ERROR | {str(e)}")
                print(f"   ⚠️ Data aggregation error: {str(e)}")
            
            # Step 2: Import ML integration modules (existing learning engines)
            try:
                from optcode.opt_ml_integration import get_ml_integration
                from optcode.ml_integration_engine import get_ml_integration_engine
                
                ml_integration = get_ml_integration()
                ml_engine = get_ml_integration_engine()
            except ImportError:
                logger.warning("EOD_LEARNING: ML_MODULES_NOT_AVAILABLE | Skipping")
                print("   ⚠️ ML modules not available - skipping ML learning update")
                return
            
            # Run EOD learning update with daily trades
            # This analyzes entry/exit Greeks patterns from today's trades
            learning_results = ml_integration.run_eod_learning_update()
            
            # Also run the new ML integration engine's EOD learning
            ml_engine_results = ml_engine.run_eod_learning()
            
            if learning_results:
                logger.info(f"EOD_LEARNING: SUCCESS | {json.dumps(learning_results, indent=2)}")
                print(f"   ✅ Learning update completed")
                print(f"   📊 Analyzed {learning_results.get('daily_trades', 0)} trades")
                print(f"   🎯 Win rate: {learning_results.get('win_rate', 0):.1f}%")
                
                # Log key insights
                if learning_results.get('top_winners'):
                    print(f"   ⭐ Best performers: {', '.join(learning_results['top_winners'][:3])}")
                if learning_results.get('improvement_areas'):
                    print(f"   📈 Areas for improvement: {', '.join(learning_results['improvement_areas'][:2])}")
            else:
                logger.warning("EOD_LEARNING: FAILED | No results returned")
                print("   ⚠️ Learning update returned no results")
            
            # Log ML engine results
            if ml_engine_results:
                logger.info(f"EOD_ML_ENGINE: SUCCESS | {json.dumps(ml_engine_results, indent=2)}")
                print(f"   ✅ ML engine learning completed: {ml_engine_results.get('status', 'unknown')}")
        
        except Exception as e:
            logger.error(f"EOD_LEARNING: ERROR | {str(e)}")
            print(f"   ❌ Learning update failed: {str(e)}")
    
    def _record_closed_positions_to_learning(self, closed_positions_list, exit_reason):
        """Record closed positions to learning engine for ML training"""
        if not closed_positions_list:
            return
        
        try:
            # Import underlying extraction function
            from optcode.symbol_utils import extract_underlying_from_symbol
            
            for pos in closed_positions_list:
                symbol = pos.get('symbol', 'UNKNOWN')
                pnl = pos.get('pnl', 0)
                is_win = pnl > 0
                
                # Extract underlying from option symbol (BANKNIFTY25DEC24000CE -> BANKNIFTY)
                # This ensures learning persists across contract expirations
                underlying = extract_underlying_from_symbol(symbol)
                
                # Record to learning engine using underlying
                if self.learning_engine:
                    try:
                        self.learning_engine.record_trade(
                            symbol=underlying,  # Use underlying, not full contract symbol!
                            won=is_win,
                            profit=pnl,
                            predicted_prob=0.5,
                            trading_mode=OptionsTradingConfig.TRADING_MODE
                        )
                        logger.debug(f"LEARNING: TRADE_RECORDED | {underlying} | exit={exit_reason} | won={is_win} | pnl=₹{pnl:.2f}")
                    except Exception as e:
                        logger.warning(f"LEARNING: RECORD_ERROR | {str(e)}")
                
                # NEW: Record to Neural ML outcome recorder
                if self.trade_outcome_recorder and HAS_NEURAL_ML:
                    try:
                        entry_premium = pos.get('entry_premium', 0)
                        exit_premium = pos.get('exit_premium', pos.get('exit_price', 0))
                        quantity = pos.get('quantity', 1)
                        
                        # Get neural ML metadata if available
                        neural_ml_metadata = pos.get('neural_ml_metadata', {})
                        ml_signal = neural_ml_metadata.get('ml_signal') if neural_ml_metadata else None
                        ml_probability = neural_ml_metadata.get('ml_probability') if neural_ml_metadata else None
                        ml_confidence = neural_ml_metadata.get('ml_confidence') if neural_ml_metadata else None
                        
                        # Determine if prediction was correct
                        # Entry price < exit price means profit (price went up = BUY signal correct)
                        # Entry price > exit price means loss (price went down = SELL signal correct)
                        actual_direction = 'UP' if exit_premium > entry_premium else 'DOWN'
                        predicted_direction = 'UP' if ml_signal == 'BUY' else 'DOWN' if ml_signal == 'SELL' else 'HOLD'
                        prediction_correct = actual_direction == predicted_direction if ml_signal else None
                        
                        # Record outcome
                        self.trade_outcome_recorder.record_outcome(
                            symbol=underlying,
                            entry_price=entry_premium,
                            exit_price=exit_premium,
                            pnl=pnl,
                            quantity=quantity,
                            predicted_prob=ml_probability if ml_probability else 0.50,
                            signal_type=ml_signal if ml_signal else 'UNKNOWN'
                        )
                        logger.info(f"NEURAL_ML: OUTCOME_RECORDED | {underlying} | signal={ml_signal} | predicted={predicted_direction} | actual={actual_direction} | correct={prediction_correct} | pnl=₹{pnl:.2f}")
                    except Exception as e:
                        logger.warning(f"NEURAL_ML: OUTCOME_RECORD_ERROR | {str(e)}")
        except Exception as e:
            logger.warning(f"LEARNING: RECORD_ERROR | {str(e)}")
    
    def _start_position_monitor(self):
        """Start position monitoring thread with adaptive IV-aware intervals"""
        def monitor_positions():
            print("📍 Position Monitor Thread: Starting")
            print(f"   ⏱️ Monitoring interval: {MonitoringConfig.MONITOR_INTERVAL_SECONDS}s (Fast: {MonitoringConfig.MONITOR_INTERVAL_FAST}s, Slow: {MonitoringConfig.MONITOR_INTERVAL_SLOW}s)")
            logger.info(f"POSITION_MONITOR: START | default_interval={MonitoringConfig.MONITOR_INTERVAL_SECONDS}s | fast={MonitoringConfig.MONITOR_INTERVAL_FAST}s | slow={MonitoringConfig.MONITOR_INTERVAL_SLOW}s")
            
            last_interval_log = 0
            current_interval = MonitoringConfig.MONITOR_INTERVAL_NORMAL
            last_greeks_refresh = time.time()  # Track when Greeks were last refreshed
            
            while self.running:
                try:
                    # Check and refresh authentication if needed
                    if not self.broker.ensure_authenticated():
                        logger.error("POSITION_MONITOR: AUTH_FAILED | cannot proceed without valid session")
                        print("❌ Authentication failed - waiting for reconnection")
                        time.sleep(60)
                        continue
                    
                    # FAST LTP REFRESH: Every 10 seconds (only bulk API calls)
                    # Separated from Greeks refresh to keep main loop responsive
                    ltps_refreshed = False
                    if len(self.monitor.positions) > 0:
                        try:
                            # CRITICAL FIX: Add timeout to prevent monitoring thread from hanging
                            # If refresh_position_ltps takes > 120 seconds, skip this cycle and continue
                            refresh_stats = call_with_timeout(
                                self.monitor.refresh_position_ltps,
                                timeout_seconds=120.0  # 120-second timeout for entire LTP refresh (bulk API calls only)
                            )
                            
                            # FIX: Accept refresh as valid if we got stats back (even if 0 LTPs updated)
                            # Previously required ltps_updated > 0, but this skipped exits when no updates occurred
                            # Now we treat a successful call (stats returned) as valid for exit checks
                            if refresh_stats is not None:
                                ltps_refreshed = True
                                if refresh_stats.get('ltps_updated', 0) > 0:
                                    logger.debug(f"POSITION_MONITOR: LTP_REFRESH | updated={refresh_stats['ltps_updated']}/{len(self.monitor.positions)}")
                                else:
                                    logger.debug(f"POSITION_MONITOR: LTP_REFRESH | no changes, but still valid for exit checks")
                            else:
                                logger.error("POSITION_MONITOR: LTP_REFRESH_TIMEOUT | skipping this cycle")
                                time.sleep(current_interval)
                                continue
                            
                            # Also refresh candle data for fake move detection
                            candle_stats = self.monitor.refresh_underlying_candles()
                            if candle_stats['candles_fetched'] > 0:
                                logger.debug(f"POSITION_MONITOR: CANDLE_REFRESH | updated={candle_stats['candles_fetched']} | underlyings={candle_stats['underlyings']}")
                        except Exception as ltp_err:
                            logger.warning(f"POSITION_MONITOR: LTP_REFRESH_ERROR | {str(ltp_err)}")
                            # If LTP refresh fails, likely market is closed - skip exit checks
                            time.sleep(current_interval)
                            continue
                    
                    # GREEKS REFRESH: Every 60 seconds (expensive chain fetches)
                    # ⭐ ASYNC: Run in background thread to NOT block monitoring loop
                    # Critical fix: Greeks fetch was blocking main loop for 20+ seconds
                    current_time = time.time()
                    if current_time - last_greeks_refresh >= 60.0:
                        last_greeks_refresh = current_time
                        # Start Greeks refresh in background thread (non-blocking)
                        def background_greeks_refresh():
                            try:
                                start_time = time.time()
                                logger.debug(f"POSITION_MONITOR: GREEKS_REFRESH_ASYNC | starting background fetch | time={start_time}")
                                greeks_stats = self.monitor.refresh_position_greeks()
                                duration = time.time() - start_time
                                if greeks_stats and greeks_stats.get('greeks_updated', 0) > 0:
                                    logger.info(f"POSITION_MONITOR: GREEKS_REFRESH_ASYNC | completed | updated={greeks_stats['greeks_updated']}/{len(self.monitor.positions)} | duration={duration:.2f}s")
                                else:
                                    logger.info(f"POSITION_MONITOR: GREEKS_REFRESH_ASYNC | completed | no updates | duration={duration:.2f}s")
                            except Exception as e:
                                logger.warning(f"POSITION_MONITOR: GREEKS_REFRESH_ASYNC_ERROR | {str(e)}")
                        
                        # Spawn background thread (daemon=True so it doesn't block shutdown)
                        greeks_thread = threading.Thread(target=background_greeks_refresh, daemon=True, name="GreeksRefreshAsync")
                        greeks_thread.start()
                    
                    # Only check exits if we successfully refreshed at least some LTPs
                    if not ltps_refreshed:
                        logger.debug(f"POSITION_MONITOR: No LTPs refreshed, skipping exit checks")
                        time.sleep(current_interval)
                        continue
                    
                    # Check and close expired positions
                    expired = self.monitor.check_expiry_close()
                    if expired:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(expired, "EXPIRY")
                        
                        for pos in expired:
                            print(f"   ✅ Expired position closed: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
                            logger.info(f"POSITION_MONITOR: EXPIRY_CLOSED | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_price', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0)
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: EXPIRY_ALERT_FAILED | {str(e)}")
                    
                    # Check profit targets
                    profitable = self.monitor.check_profit_targets()
                    if profitable:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(profitable, "PROFIT_TARGET")
                        
                        for pos in profitable:
                            print(f"   🎯 Profit target hit: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
                            logger.info(f"POSITION_MONITOR: PROFIT_TARGET | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_price', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0)
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: PROFIT_ALERT_FAILED | {str(e)}")
                    
                    # ⭐ CRITICAL: Check HARD SL FIRST (ultimate safety net)
                    # This runs BEFORE all other exit checks to ensure positions never exceed -10% loss
                    hard_sl_exits = self.monitor.check_hard_stop_loss()
                    if hard_sl_exits:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(hard_sl_exits, "HARD_SL")
                        
                        for pos in hard_sl_exits:
                            print(f"   🛑 HARD SL hit: {pos['symbol']} PnL: ₹{pos['pnl']:.2f} ({pos.get('pnl_percent', 0):.1f}%)")
                            logger.error(f"POSITION_MONITOR: HARD_SL_HIT | {pos['symbol']} | PnL=₹{pos['pnl']:.2f} | CRITICAL SAFETY TRIGGERED")
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_price', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0),
                                            'reason': 'HARD STOP LOSS (-10%) - CRITICAL'
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: HARD_SL_ALERT_FAILED | {str(e)}")
                    
                    # ⭐ NEW: Check momentum reversal (EARLY EXIT to prevent hard SL)
                    # This should run AFTER hard SL check for layered protection
                    momentum_exits = self.monitor.check_momentum_reversal()
                    if momentum_exits:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(momentum_exits, "MOMENTUM_REVERSAL")
                        
                        for pos in momentum_exits:
                            print(f"   ⚡ Momentum reversal exit: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
                            logger.warning(f"POSITION_MONITOR: MOMENTUM_EXIT | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_price', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0),
                                            'reason': 'Momentum reversal (smart exit)'
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: MOMENTUM_ALERT_FAILED | {str(e)}")
                    
                    # ⭐ STALE CONSOLIDATION: Exit positions held >15min without hitting 10% peak
                    # Prevents getting caught in low-gain consolidations that reverse into losses
                    stale_consol_exits = self.monitor.check_stale_consolidation_exits()
                    if stale_consol_exits:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(stale_consol_exits, "STALE_CONSOLIDATION")
                        
                        for pos in stale_consol_exits:
                            print(f"   ⏱️  Stale consolidation exit: {pos['symbol']} (held {pos.get('duration', 0):.0f}s) PnL: ₹{pos['pnl']:.2f}")
                            logger.warning(f"POSITION_MONITOR: STALE_CONSOL_EXIT | {pos['symbol']} | Duration: {pos.get('duration', 0):.0f}s | PnL=₹{pos['pnl']:.2f}")
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_premium', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0),
                                            'reason': 'Stale consolidation (no momentum after 15min)'
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: STALE_CONSOL_ALERT_FAILED | {str(e)}")
                    
                    # ⭐ STALE TIMEOUT: Exit non-trending positions >20min without momentum
                    # Catch positions that never developed into trending moves
                    stale_timeout_exits = self.monitor.check_stale_positions()
                    if stale_timeout_exits:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(stale_timeout_exits, "STALE_TIMEOUT")
                        
                        for pos in stale_timeout_exits:
                            print(f"   ⏰ Stale timeout exit: {pos['symbol']} (held {pos.get('duration', 0):.0f}s) PnL: ₹{pos['pnl']:.2f}")
                            logger.warning(f"POSITION_MONITOR: STALE_TIMEOUT_EXIT | {pos['symbol']} | Duration: {pos.get('duration', 0):.0f}s | PnL=₹{pos['pnl']:.2f}")
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_premium', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0),
                                            'reason': 'Stale non-trending (>20min no momentum)'
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: STALE_TIMEOUT_ALERT_FAILED | {str(e)}")
                    
                    # ⭐ NEW: Check IV crash (EARLY EXIT when premium dies)
                    # This is complementary to momentum reversal - catches IV collapse signal
                    iv_crash_exits = self.monitor.check_iv_crash()
                    if iv_crash_exits:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(iv_crash_exits, "IV_CRASH")
                        
                        for pos in iv_crash_exits:
                            entry_iv = pos.get('entry_iv', 'N/A')
                            exit_iv = pos.get('exit_iv', 'N/A')
                            iv_drop = ((entry_iv - exit_iv) / entry_iv * 100) if isinstance(entry_iv, (int, float)) and isinstance(exit_iv, (int, float)) and entry_iv > 0 else 'N/A'
                            
                            print(f"   💥 IV crash exit: {pos['symbol']} | Entry IV: {entry_iv} → Exit IV: {exit_iv} | Drop: {iv_drop}% | PnL: ₹{pos['pnl']:.2f}")
                            logger.warning(
                                f"IV_CRASH_EXIT_LOGGED: {pos['symbol']} | "
                                f"Entry IV: {entry_iv} | Exit IV: {exit_iv} | IV Drop: {iv_drop}% | "
                                f"Premium: ₹{pos.get('entry_premium', 0):.2f} → ₹{pos.get('exit_premium', 0):.2f} | "
                                f"PnL: ₹{pos['pnl']:.2f} ({pos.get('pnl_percent', 0):.1f}%) | "
                                f"Exit Reason: {pos.get('exit_reason', 'IV_CRASH')} | "
                                f"Duration: {pos.get('duration', 0):.1f}s"
                            )
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_premium', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0),
                                            'reason': f'IV crash (Drop: {iv_drop}%)'
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: IV_CRASH_ALERT_FAILED | {str(e)}")

                    
                    # Check stop losses
                    stopped = self.monitor.check_stop_losses()
                    if stopped:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(stopped, "STOPLOSS")
                        
                        for pos in stopped:
                            print(f"   🛑 Stop loss hit: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
                            logger.warning(f"POSITION_MONITOR: STOPLOSS | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_price', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0)
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: STOPLOSS_ALERT_FAILED | {str(e)}")
                    
                    # Check trailing stop losses (TRIAL_SL) - profit locking mechanism
                    trailing_exited = self.monitor.check_trailing_stop_losses()
                    if trailing_exited:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(trailing_exited, "TRAILING_SL")
                        
                        for pos in trailing_exited:
                            print(f"   📈 Trailing SL exit: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
                            logger.info(f"POSITION_MONITOR: TRIAL_SL_EXIT | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                            if self.alert_manager:
                                try:
                                    self.alert_manager.alert_position_closed(
                                        bot_type='options',
                                        position={
                                            'symbol': pos['symbol'],
                                            'entry_price': pos.get('entry_premium', 0),
                                            'exit_price': pos.get('exit_price', 0),
                                            'pnl': pos['pnl'],
                                            'pnl_percent': pos.get('pnl_percent', 0),
                                            'reason': 'Trailing stop loss'
                                        }
                                    )
                                except Exception as e:
                                    logger.warning(f"POSITION_MONITOR: TRIAL_SL_ALERT_FAILED | {str(e)}")
                    
                    # NEW: Check sentiment exit (PCR + OI Buildup fade)
                    # ⭐ ASYNC: Run in background thread to NOT block monitoring loop
                    # Critical fix: Sentiment checks were blocking main loop for 2-5 seconds every 5 seconds
                    should_check_sentiment = False
                    if self.monitor.last_sentiment_check_time is None:
                        should_check_sentiment = True
                    else:
                        time_since_last_check = (datetime.now() - self.monitor.last_sentiment_check_time).total_seconds()
                        if time_since_last_check >= MonitoringConfig.SENTIMENT_CHECK_INTERVAL_SECONDS:
                            should_check_sentiment = True
                    
                    if should_check_sentiment:
                        self.monitor.last_sentiment_check_time = datetime.now()
                        # Start sentiment check in background thread (non-blocking)
                        def background_sentiment_check():
                            try:
                                start_time = time.time()
                                logger.debug(f"POSITION_MONITOR: SENTIMENT_CHECK_ASYNC | starting background fetch")
                                sentiment_exits = self.monitor.check_sentiment_exit()
                                duration = time.time() - start_time
                                
                                if sentiment_exits:
                                    logger.info(f"POSITION_MONITOR: SENTIMENT_CHECK_ASYNC | completed | exits={len(sentiment_exits)} | duration={duration:.2f}s")
                                    
                                    # Record and alert for sentiment exits
                                    try:
                                        self._record_closed_positions_to_learning(sentiment_exits, "SENTIMENT")
                                        
                                        for pos in sentiment_exits:
                                            logger.warning(f"POSITION_MONITOR: SENTIMENT_EXIT | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                                            if self.alert_manager:
                                                try:
                                                    self.alert_manager.alert_position_closed(
                                                        bot_type='options',
                                                        position={
                                                            'symbol': pos['symbol'],
                                                            'entry_price': pos.get('entry_premium', 0),
                                                            'exit_price': pos.get('exit_price', 0),
                                                            'pnl': pos['pnl'],
                                                            'pnl_percent': pos.get('pnl_percent', 0),
                                                            'reason': 'Sentiment deterioration'
                                                        }
                                                    )
                                                except Exception as e:
                                                    logger.warning(f"POSITION_MONITOR: SENTIMENT_EXIT_ALERT_FAILED | {str(e)}")
                                    except Exception as e:
                                        logger.warning(f"POSITION_MONITOR: SENTIMENT_EXIT_PROCESSING_ERROR | {str(e)}")
                                else:
                                    logger.debug(f"POSITION_MONITOR: SENTIMENT_CHECK_ASYNC | completed | no exits | duration={duration:.2f}s")
                            except Exception as e:
                                logger.warning(f"POSITION_MONITOR: SENTIMENT_CHECK_ASYNC_ERROR | {str(e)}")
                        
                        # Spawn background thread (daemon=True so it doesn't block shutdown)
                        sentiment_thread = threading.Thread(target=background_sentiment_check, daemon=True, name="SentimentCheckAsync")
                        sentiment_thread.start()
                    
                    # Get position summary
                    summary = self.monitor.get_position_summary()
                    
                    # Adaptive monitoring interval based on rate limiter health
                    rate_limiter_stats = self.broker.get_rate_limiter_stats() if self.broker else {}
                    if rate_limiter_stats:
                        # Calculate utilization percentage (use per-minute bucket as primary)
                        total_calls = rate_limiter_stats.get('total_calls', 0)
                        queued_calls = rate_limiter_stats.get('queued_calls', 0)
                        
                        # If we have significant queue or high call volume, slow down
                        if queued_calls > 0:
                            current_interval = MonitoringConfig.MONITOR_INTERVAL_SLOW
                        else:
                            # Use success rate or call volume to determine interval
                            success_rate = rate_limiter_stats.get('success_rate', 100)
                            if success_rate < 95:  # If we're seeing failures, slow down
                                current_interval = MonitoringConfig.MONITOR_INTERVAL_SLOW
                            else:
                                current_interval = MonitoringConfig.MONITOR_INTERVAL_NORMAL
                    
                    if summary['open_positions'] > 0:
                        # Safely format portfolio metrics to handle NaN/inf values
                        try:
                            upnl = summary['total_unrealized_pnl'] if not (isinstance(summary['total_unrealized_pnl'], float) and (float('nan') if isinstance(summary['total_unrealized_pnl'], float) else False)) else 0
                            delta = summary['portfolio_delta'] if isinstance(summary['portfolio_delta'], (int, float)) and not math.isnan(summary['portfolio_delta']) else 0
                            gamma = summary['portfolio_gamma'] if isinstance(summary['portfolio_gamma'], (int, float)) and not math.isnan(summary['portfolio_gamma']) else 0
                            theta = summary['portfolio_theta'] if isinstance(summary['portfolio_theta'], (int, float)) and not math.isnan(summary['portfolio_theta']) else 0
                            
                            print(f"\n📊 Portfolio Status:")
                            print(f"   Open Positions: {summary['open_positions']}")
                            print(f"   Total Unrealized P&L: ₹{upnl:.2f}")
                            print(f"   Portfolio Delta: {delta:.2f}")
                            print(f"   Portfolio Gamma: {gamma:.4f}")
                            print(f"   Portfolio Theta: {theta:.4f}")
                            print(f"   Next check in: {current_interval}s\n")
                            
                            logger.debug(f"POSITION_MONITOR: STATE | open={summary['open_positions']} | upnl=₹{upnl:.2f} | delta={delta:.2f} | gamma={gamma:.4f} | interval={current_interval}s")
                        except Exception as format_err:
                            logger.warning(f"POSITION_MONITOR: Format error in portfolio display: {str(format_err)}")
                            print(f"\n📊 Portfolio: {summary['open_positions']} positions (display error, check logs)")
                        
                        # Send portfolio monitoring alert every 30 seconds
                        if self.alert_manager and int(time.time()) % 30 == 0:
                            try:
                                self.alert_manager.alert_capital_low(
                                    bot_type='options',
                                    capital_data={
                                        'open_positions': summary['open_positions'],
                                        'total_unrealized_pnl': summary['total_unrealized_pnl'],
                                        'delta': summary['portfolio_delta'],
                                        'gamma': summary['portfolio_gamma'],
                                        'theta': summary['portfolio_theta']
                                    }
                                )
                            except Exception as e:
                                logger.warning(f"POSITION_MONITOR: MONITOR_ALERT_FAILED | {str(e)}")
                    
                    # Log interval changes
                    if time.time() - last_interval_log > 300:  # Log every 5 minutes
                        logger.info(f"POSITION_MONITOR: INTERVAL_ADAPTIVE | current={current_interval}s | normal={MonitoringConfig.MONITOR_INTERVAL_NORMAL}s | slow={MonitoringConfig.MONITOR_INTERVAL_SLOW}s")
                        last_interval_log = time.time()
                    
                    # Save live data summary at end of each monitoring cycle
                    if HAS_LIVE_DATA and self.live_data_tracker:
                        try:
                            self.live_data_tracker.save()
                            logger.debug("POSITION_MONITOR: LIVE_DATA_SAVED")
                            
                            # Also update CSV file (for Excel viewing)
                            if self.live_data_formatter:
                                csv_data = self.live_data_formatter.generate_csv()
                                csv_file = Path('/root/santhosh/trading/options/data/live_data_trades.csv')
                                with open(csv_file, 'w') as f:
                                    f.write(csv_data)
                                logger.debug("POSITION_MONITOR: CSV_UPDATED")
                        except Exception as live_err:
                            logger.debug(f"POSITION_MONITOR: LIVE_DATA_SAVE_FAILED | {str(live_err)}")
                    
                    time.sleep(current_interval)  # Adaptive monitoring interval (default 10s, can go to 8s or 20s)
                
                except Exception as e:
                    import traceback
                    print(f"❌ Position monitor error: {str(e)}")
                    tb = traceback.format_exc()
                    logger.error(f"POSITION_MONITOR: ERROR | {str(e)} | traceback={tb}")
                    print(tb)
                    time.sleep(5)
            
            logger.info("POSITION_MONITOR: STOPPED")

        
        self.position_monitor_thread = threading.Thread(
            target=monitor_positions,
            daemon=True,
            name="OptionsPositionMonitor"
        )
        self.position_monitor_thread.start()
        print("   ✅ Position monitor thread started")
        logger.debug(f"POSITION_MONITOR: THREAD_STARTED")
    
    def _start_instrument_refresh(self):
        """Start daily instrument refresh thread"""
        if self.instrument_manager:
            # The scheduler is already running in the background
            logger.info("INSTRUMENT_REFRESH: SCHEDULER_ACTIVATED | managed by InstrumentManager")
            print("   ✅ Instrument refresh scheduler activated (daily at 09:00 AM)")
        else:
            logger.warning("INSTRUMENT_REFRESH: MANAGER_NOT_READY")
    
    def _start_csv_updater_service(self):
        """Start background service to keep live_data_trades.csv fresh"""
        try:
            from optcode.csv_updater import start_csv_update_service
            start_csv_update_service()
            print("   ✅ CSV updater service started (updates every 30 seconds)")
            logger.info("CSV_UPDATER: SERVICE_STARTED")
        except Exception as e:
            logger.warning(f"CSV_UPDATER: FAILED_TO_START | {str(e)}")
            print(f"   ⚠️  CSV updater service failed to start: {str(e)}")
    
    def _refresh_instruments_now(self):
        """Refresh instruments from broker immediately (legacy, not used)"""
        try:
            if self.instrument_manager:
                logger.info("INSTRUMENT_REFRESH: MANUAL_START")
                if self.instrument_manager.download_instruments():
                    stats = self.instrument_manager.get_stats()
                    print(f"   ✅ Instrument refresh: {stats['total_instruments']} contracts updated")
                    logger.info(f"INSTRUMENT_REFRESH: SUCCESS | total={stats['total_instruments']}")
                else:
                    logger.warning("INSTRUMENT_REFRESH: FAILED")
        except Exception as e:
            logger.error(f"INSTRUMENT_REFRESH: ERROR | {str(e)}")
    
    def _start_api_server(self):
        """Start API server with robust error handling and auto-restart"""
        def run_api_with_restart():
            """Run API server with automatic restart on crash"""
            restart_count = 0
            max_restarts = 100  # Prevent infinite restart loops
            
            while restart_count < max_restarts and self.running:
                try:
                    if restart_count > 0:
                        print(f"🔄 API server restart attempt #{restart_count}")
                        logger.warning(f"API_SERVER: RESTART_ATTEMPT | count={restart_count}")
                        time.sleep(2)  # Wait before restart
                    
                    print(f"🚀 Starting API server (attempt {restart_count + 1})")
                    self.api_server.start(
                        host=WebhookConfig.HOST,
                        port=WebhookConfig.PORT
                    )
                    
                except KeyboardInterrupt:
                    logger.info("API_SERVER: INTERRUPTED")
                    self.stop()
                    break
                    
                except OSError as e:
                    # Port binding error
                    restart_count += 1
                    logger.error(f"API_SERVER: BIND_ERROR | error={str(e)} | restart_count={restart_count}")
                    print(f"❌ Port binding error: {str(e)} - retrying in 2 seconds...")
                    if restart_count >= max_restarts:
                        logger.critical(f"API_SERVER: MAX_RESTARTS_EXCEEDED | {restart_count} attempts failed")
                        print(f"❌ API server failed after {max_restarts} restart attempts!")
                        break
                    
                except Exception as e:
                    # Any other error in API server
                    restart_count += 1
                    logger.error(f"API_SERVER: CRASH | error={str(e)} | type={type(e).__name__} | restart_count={restart_count}")
                    print(f"❌ API server crashed: {str(e)}")
                    print(f"   Restarting... (attempt {restart_count})")
                    
                    if restart_count >= max_restarts:
                        logger.critical(f"API_SERVER: UNRECOVERABLE | {restart_count} restart attempts exhausted")
                        print(f"❌ API server unrecoverable after {max_restarts} restarts!")
                        break
        
        api_thread = threading.Thread(
            target=run_api_with_restart,
            daemon=True,
            name="OptionsAPIServer"
        )
        api_thread.start()
        print("✅ API server thread started (with auto-restart enabled)")
    
    def stop(self):
        """Stop options trading bot"""
        if not self.running:
            return
        
        print("\n🛑 STOPPING OPTIONS TRADING BOT")
        logger.info("BOT_STOP: START")
        
        self.running = False
        
        if self.api_server:
            self.api_server.stop()
            logger.debug(f"BOT_STOP: API_SERVER_STOPPED")
        
        if self.instrument_manager:
            self.instrument_manager.stop_scheduler()
            logger.debug(f"BOT_STOP: INSTRUMENT_MGR_STOPPED")
        
        if self.monitor:
            summary = self.monitor.get_position_summary()
            print(f"\n📊 Final Position Summary:")
            print(f"   Open Positions: {summary['open_positions']}")
            print(f"   Total Unrealized P&L: ₹{summary['total_unrealized_pnl']:.2f}")
            logger.info(f"BOT_STOP: FINAL_SUMMARY | open={summary['open_positions']} | upnl=₹{summary['total_unrealized_pnl']:.2f}")
        
        uptime = (datetime.now() - self.startup_time).total_seconds() if self.startup_time else 0
        print(f"   Uptime: {uptime:.0f} seconds")
        logger.info(f"BOT_STOP: UPTIME | {uptime:.0f}s")
        
        logger.info("BOT_STOP: COMPLETE")
        print("\n✅ OPTIONS BOT STOPPED\n")
    
    def run(self):
        """Main bot loop (blocking)"""
        self.start()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n⏸️  Keyboard interrupt received")
            self.stop()

# =============================================================================
# Signal Handlers
# =============================================================================

bot_instance = None

def signal_handler(sig, frame):
    """Handle system signals"""
    print("\n⚡ Signal received - shutting down gracefully...")
    if bot_instance:
        bot_instance.stop()
    sys.exit(0)

def cleanup():
    """Cleanup on exit"""
    if bot_instance and bot_instance.running:
        bot_instance.stop()

# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point"""
    global bot_instance
    
    print("""
╔════════════════════════════════════════════════════════════════════════╗
║                   OPTIONS TRADING BOT v1.0                            ║
║         Independent Directional Derivatives Trading Engine             ║
║              Shared Webhook, Separate Execution Stack                  ║
╚════════════════════════════════════════════════════════════════════════╝
    """)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup)
    
    # Create and run bot
    bot_instance = OptionsTradingBot()
    
    try:
        bot_instance.run()
    except Exception as e:
        print(f"\n❌ Fatal error: {str(e)}")
        bot_instance.stop()
        sys.exit(1)

if __name__ == "__main__":
    """Run the bot when script is executed directly"""
    
    # Ensure we're in the right directory
    script_dir = Path(__file__).parent
    if script_dir.name != "options":
        print("Error: This script must be run from the options directory")
        sys.exit(1)
    
    # Prevent multiple instances using PID file with atomic locking
    pid_file = script_dir / "options_bot.pid"
    lock_file = script_dir / ".options_bot.lock"
    
    # Use exclusive file lock for atomic PID check+write
    try:
        # ENHANCED: Check for stale lock file BEFORE trying to acquire
        if lock_file.exists() and pid_file.exists():
            try:
                with open(pid_file, 'r') as f:
                    lock_pid = int(f.read().strip())
                # Check if that PID is actually running
                try:
                    os.kill(lock_pid, 0)
                except OSError:
                    # PID is stale - force clean the lock file
                    print(f"🧹 Cleaning stale lock file (PID {lock_pid} not running)")
                    try:
                        lock_file.unlink()
                        pid_file.unlink()
                    except:
                        pass
            except:
                pass
        
        # Open lock file for writing (create if doesn't exist)
        lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY, 0o644)
        
        try:
            # Try to get exclusive lock (non-blocking)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"❌ ERROR: Options bot already running (another process holds lock)")
            print(f"   If bot is not running, delete: {lock_file}")
            os.close(lock_fd)
            sys.exit(1)
        
        # Lock acquired - check if PID file exists with running process
        if pid_file.exists():
            try:
                with open(pid_file, 'r') as f:
                    old_pid = int(f.read().strip())
                
                # Check if process is actually running
                try:
                    os.kill(old_pid, 0)  # Signal 0 just checks if process exists
                    print(f"❌ ERROR: Options bot already running (PID {old_pid})")
                    print(f"   If bot is not running, delete: {pid_file}")
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                    sys.exit(1)
                except OSError:
                    # Process doesn't exist, remove stale PID file
                    print(f"⚠️  Removing stale PID file (process {old_pid} not found)")
                    pid_file.unlink()
            except Exception as e:
                print(f"⚠️  Error checking PID file: {e}")
                if pid_file.exists():
                    pid_file.unlink()
        
        # Write our PID atomically
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
        
        # Cleanup PID and lock files on exit
        def cleanup_pid():
            try:
                if pid_file.exists():
                    pid_file.unlink()
            except:
                pass
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                if lock_file.exists():
                    lock_file.unlink()
            except:
                pass
        
        atexit.register(cleanup_pid)
        
        print(f"✅ Started options bot (PID {os.getpid()})")
    
    except Exception as e:
        print(f"❌ ERROR: Failed to acquire PID lock: {e}")
        sys.exit(1)
    
    main()
