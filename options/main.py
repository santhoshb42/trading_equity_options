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

# Alert system integration
try:
    from alert_system import AlertManager, AlertLevel, AlertCategory
    ALERT_SYSTEM_AVAILABLE = True
except ImportError:
    ALERT_SYSTEM_AVAILABLE = False
    print("⚠️  Alert system not available - alerts disabled")

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
        
        print(f"\n🔐 Authenticating with broker...")
        logger.debug(f"BOT_INIT: AUTHENTICATING")
        self.broker = get_options_broker()
        if not self.broker.authenticate():
            print("⚠️ Broker authentication failed - continuing in demo mode")
            logger.warning(f"BOT_INIT: AUTH_FAILED | continuing in demo mode")
        else:
            print("✅ Broker authenticated")
            logger.info(f"BOT_INIT: AUTH_SUCCESS")
        
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
        
        # Start EOD cleanup scheduler (to prevent stale positions)
        self._start_eod_cleanup_scheduler()
        
        # Start API server
        self._start_api_server()
    
    def _start_eod_cleanup_scheduler(self):
        """Start EOD cleanup scheduler to prevent stale positions from accumulating"""
        def eod_cleanup_loop():
            """Background thread to clean up stale positions at EOD"""
            import schedule
            
            print("📍 EOD Cleanup Scheduler: Starting")
            print("   ⏱️ Scheduled cleanup time: 15:15 PM (after square-off at 15:12)")
            logger.info("EOD_CLEANUP: SCHEDULER_START | scheduled_time=15:15")
            
            # Schedule cleanup for 3:15 PM daily (after square-off at 3:12 PM)
            schedule.every().day.at("15:15").do(self._cleanup_stale_positions)
            
            while self.running:
                try:
                    schedule.run_pending()
                    time.sleep(5)  # Check every 5 seconds if a scheduled task should run
                except Exception as e:
                    logger.error(f"EOD_CLEANUP: SCHEDULER_ERROR | {str(e)}")
                    time.sleep(60)
        
        eod_cleanup_thread = threading.Thread(
            target=eod_cleanup_loop,
            daemon=True,
            name="OptionsEODCleanup"
        )
        eod_cleanup_thread.start()
        print("   ✅ EOD cleanup scheduler thread started")
        logger.debug("EOD_CLEANUP: THREAD_STARTED")
    
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
            
            # Import ML integration
            try:
                from optcode.opt_ml_integration import get_ml_integration
                from optcode.ml_integration_engine import get_ml_integration_engine
                
                ml_integration = get_ml_integration()
                ml_engine = get_ml_integration_engine()
            except ImportError:
                logger.warning("EOD_LEARNING: ML_MODULES_NOT_AVAILABLE | Skipping")
                print("   ⚠️ ML modules not available - skipping learning update")
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
        if not self.learning_engine or not closed_positions_list:
            return
        
        try:
            for pos in closed_positions_list:
                symbol = pos.get('symbol', 'UNKNOWN')
                pnl = pos.get('pnl', 0)
                is_win = pnl > 0
                
                # Extract base symbol (remove -EQ if present)
                base_symbol = symbol.split('-')[0] if '-' in symbol else symbol
                
                # Record to learning engine
                self.learning_engine.record_trade(
                    symbol=base_symbol,
                    won=is_win,
                    profit=pnl,
                    predicted_prob=0.5,
                    trading_mode=OptionsTradingConfig.TRADING_MODE
                )
                logger.debug(f"LEARNING: TRADE_RECORDED | {base_symbol} | exit={exit_reason} | won={is_win} | pnl=₹{pnl:.2f}")
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
            
            while self.running:
                try:
                    # Check and refresh authentication if needed
                    if not self.broker.ensure_authenticated():
                        logger.error("POSITION_MONITOR: AUTH_FAILED | cannot proceed without valid session")
                        print("❌ Authentication failed - waiting for reconnection")
                        time.sleep(60)
                        continue
                    
                    # CRITICAL: Refresh LTP for all positions from broker
                    ltps_refreshed = False
                    if len(self.monitor.positions) > 0:
                        try:
                            # CRITICAL FIX: Add timeout to prevent monitoring thread from hanging
                            # If refresh_position_ltps takes > 60 seconds, skip this cycle and continue
                            refresh_stats = call_with_timeout(
                                self.monitor.refresh_position_ltps,
                                timeout_seconds=60.0  # 60-second timeout for entire LTP refresh (37 positions * ~1.5s per position)
                            )
                            
                            if refresh_stats and refresh_stats.get('ltps_updated', 0) > 0:
                                ltps_refreshed = True
                                logger.debug(f"POSITION_MONITOR: LTP_REFRESH | updated={refresh_stats['ltps_updated']}/{len(self.monitor.positions)}")
                            elif refresh_stats is None:
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
                    
                    # ⭐ NEW: Check momentum reversal (EARLY EXIT to prevent hard SL)
                    # This should run BEFORE check_stop_losses to catch reversals early
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
                    # HIGH IV: Check sentiment every 5 seconds (not 60) - profit bookings change IV rapidly
                    should_check_sentiment = False
                    if self.monitor.last_sentiment_check_time is None:
                        should_check_sentiment = True
                    else:
                        time_since_last_check = (datetime.now() - self.monitor.last_sentiment_check_time).total_seconds()
                        if time_since_last_check >= MonitoringConfig.SENTIMENT_CHECK_INTERVAL_SECONDS:
                            should_check_sentiment = True
                    
                    sentiment_exits = []
                    if should_check_sentiment:
                        sentiment_exits = self.monitor.check_sentiment_exit()
                        self.monitor.last_sentiment_check_time = datetime.now()  # Update timestamp
                        if sentiment_exits:
                            logger.info(f"POSITION_MONITOR: SENTIMENT_CHECK | {len(self.positions)} positions checked | {len(sentiment_exits)} exits triggered")
                    
                    if sentiment_exits:
                        # Record to learning engine
                        self._record_closed_positions_to_learning(sentiment_exits, "SENTIMENT")
                        
                        for pos in sentiment_exits:
                            print(f"   📊 Sentiment exit: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
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
        """Start API server"""
        def run_api():
            try:
                self.api_server.start(
                    host=WebhookConfig.HOST,
                    port=WebhookConfig.PORT
                )
            except KeyboardInterrupt:
                self.stop()
        
        api_thread = threading.Thread(
            target=run_api,
            daemon=True,
            name="OptionsAPIServer"
        )
        api_thread.start()
        print("✅ API server thread started")
    
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
