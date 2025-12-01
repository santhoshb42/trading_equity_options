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
import atexit
import threading
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from optcode.optconfig import (
    WebhookConfig, OptionsTradingConfig, DevConfig, MonitoringConfig,
    get_optconfig_summary, validate_optconfig
)
from optcode.angelone_options import get_options_broker
from optcode.optmonitor import get_option_monitor
from optcode.optapi import get_options_api_server
from optcode.optlogging import logger, log_event, log_state, print_session_summary

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
        
        # Get API server
        self.api_server = get_options_api_server()
        logger.debug(f"BOT_INIT: API_SERVER_READY | port={WebhookConfig.PORT}")
        
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
        
        # Start API server
        self._start_api_server()
    
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
                    # Check and close expired positions
                    expired = self.monitor.check_expiry_close()
                    if expired:
                        for pos in expired:
                            print(f"   ✅ Expired position closed: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
                            logger.info(f"POSITION_MONITOR: EXPIRY_CLOSED | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                    
                    # Check profit targets
                    profitable = self.monitor.check_profit_targets()
                    if profitable:
                        for pos in profitable:
                            print(f"   🎯 Profit target hit: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
                            logger.info(f"POSITION_MONITOR: PROFIT_TARGET | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                    
                    # Check stop losses
                    stopped = self.monitor.check_stop_losses()
                    if stopped:
                        for pos in stopped:
                            print(f"   🛑 Stop loss hit: {pos['symbol']} PnL: ₹{pos['pnl']:.2f}")
                            logger.warning(f"POSITION_MONITOR: STOPLOSS | {pos['symbol']} | PnL=₹{pos['pnl']:.2f}")
                    
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
                        print(f"\n📊 Portfolio Status:")
                        print(f"   Open Positions: {summary['open_positions']}")
                        print(f"   Total Unrealized P&L: ₹{summary['total_unrealized_pnl']:.2f}")
                        print(f"   Portfolio Delta: {summary['portfolio_delta']:.2f}")
                        print(f"   Portfolio Gamma: {summary['portfolio_gamma']:.4f}")
                        print(f"   Portfolio Theta: {summary['portfolio_theta']:.4f}")
                        print(f"   Next check in: {current_interval}s\n")
                        
                        logger.debug(f"POSITION_MONITOR: STATE | open={summary['open_positions']} | upnl=₹{summary['total_unrealized_pnl']:.2f} | delta={summary['portfolio_delta']:.2f} | gamma={summary['portfolio_gamma']:.4f} | interval={current_interval}s")
                    
                    # Log interval changes
                    if time.time() - last_interval_log > 300:  # Log every 5 minutes
                        logger.info(f"POSITION_MONITOR: INTERVAL_ADAPTIVE | current={current_interval}s | normal={MonitoringConfig.MONITOR_INTERVAL_NORMAL}s | slow={MonitoringConfig.MONITOR_INTERVAL_SLOW}s")
                        last_interval_log = time.time()
                    
                    time.sleep(current_interval)  # Adaptive monitoring interval (default 10s, can go to 8s or 20s)
                
                except Exception as e:
                    print(f"❌ Position monitor error: {str(e)}")
                    logger.error(f"POSITION_MONITOR: ERROR | {str(e)}", exc_info=True)
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
    main()
