"""
Main Entry Point - Equity Trading Bot

Orchestrates all components with comprehensive autonomous logging:
- Configuration validation
- Broker initialization
- Position monitor startup
- Webhook server startup
- Graceful shutdown handling
"""

import sys
import signal
import threading
import time
import atexit
import fcntl
from pathlib import Path

# Load environment variables from .env file
import os
from pathlib import Path as PathlibPath
from dotenv import load_dotenv
_env_file = PathlibPath(__file__).parent / ".env"
print(f"DEBUG: Loading .env from: {_env_file}")
print(f"DEBUG: TRADING_MODE before load_dotenv: {os.getenv('TRADING_MODE')}")
# Override=True to ensure .env values take precedence over any stale environment
load_dotenv(_env_file, override=True)
print(f"DEBUG: TRADING_MODE after load_dotenv: {os.getenv('TRADING_MODE')}")

# Add the eqcode directory to Python path
sys.path.append(str(Path(__file__).parent / "eqcode"))

from eqcode.config import validate_config, get_config_summary, TradingConfig, WebhookConfig
from eqcode.bot_logging import log_event, log_startup_info, log_system_state, log_error
from eqcode.api import start_webhook_server
from eqcode.hybrid_learning_engine import HybridLearningEngine


class EquityTradingBot:
    """
    Main trading bot class that orchestrates all components
    """
    
    def __init__(self):
        self.running = False
        self.startup_time = None
        self.learning_engine = None
        self.eod_scheduler = None
    
    def validate_configuration(self) -> bool:
        """Validate all configuration parameters with detailed logging"""
        log_system_state("CONFIG", "VALIDATING")
        log_event("STARTUP", "Validating configuration...")
        
        is_valid, errors = validate_config()
        
        if is_valid:
            log_system_state("CONFIG", "VALID")
            log_event("CONFIG", "Configuration validation passed")
            
            # Log configuration summary
            config = get_config_summary()
            log_event("CONFIG", "Current configuration", **config)
            
            # Log specific trading parameters for autonomous debugging
            log_system_state("TRADING_CONFIG", "LOADED", {
                "trading_mode": TradingConfig.TRADING_MODE,
                "capital_per_trade": TradingConfig.CAPITAL_PER_TRADE,
                "max_positions": TradingConfig.MAX_POSITIONS,
                "default_sl_percentage": TradingConfig.DEFAULT_SL_PERCENTAGE,
                "monitor_interval": TradingConfig.MONITOR_INTERVAL_SECONDS
            })
            
            return True
        else:
            log_system_state("CONFIG", "INVALID", {"error_count": len(errors)})
            log_event("ERROR", "Configuration validation failed")
            for error in errors:
                log_event("CONFIG_ERROR", error)
            return False
    
    def start_webhook_server(self):
        """Start webhook server with comprehensive logging"""
        try:
            log_system_state("WEBHOOK_SERVER", "STARTING")
            log_event("STARTUP", "Starting webhook server...")
            
            # The webhook server will initialize its own broker and monitor
            start_webhook_server()
            
        except Exception as e:
            log_system_state("WEBHOOK_SERVER", "FAILED", {"error": str(e)})
            log_error("WEBHOOK_SERVER_START", "Failed to start webhook server", e,
                     recovery_action="Bot will exit")
            log_event("ERROR", f"Failed to start webhook server: {str(e)}")
            raise
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            log_system_state("SIGNAL_HANDLER", "TRIGGERED", {"signal": signum})
            log_event("SHUTDOWN", f"Received signal {signum}, initiating graceful shutdown...")
            self.shutdown()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        log_system_state("SIGNAL_HANDLERS", "REGISTERED")
    
    def shutdown(self):
        """Graceful shutdown of all components"""
        if not self.running:
            return
        
        uptime = time.time() - self.startup_time if self.startup_time else 0
        log_system_state("BOT", "SHUTTING_DOWN", {"uptime_seconds": round(uptime, 1)})
        log_event("SHUTDOWN", "Initiating bot shutdown...")
        self.running = False
        
        try:
            # Stop EOD scheduler if running
            if self.eod_scheduler:
                try:
                    self.eod_scheduler.stop()
                    log_event("SHUTDOWN", "EOD scheduler stopped")
                except Exception as e:
                    log_error("SHUTDOWN", "Error stopping EOD scheduler", e)
            
            log_system_state("BOT", "SHUTDOWN_COMPLETE")
            log_event("SHUTDOWN", "Bot shutdown completed successfully")
            
        except Exception as e:
            log_error("SHUTDOWN", "Error during shutdown", e)
            log_event("ERROR", f"Error during shutdown: {str(e)}")
    
    def run(self):
        """Main bot execution method with comprehensive autonomous logging"""
        try:
            self.startup_time = time.time()
            
            # Log comprehensive system startup information
            log_startup_info()
            log_system_state("BOT", "STARTING")
            log_event("STARTUP", "Starting Equity Trading Bot...")
            
            # Setup signal handlers for graceful shutdown
            self.setup_signal_handlers()
            
            # Validate configuration
            if not self.validate_configuration():
                log_system_state("BOT", "FATAL_CONFIG_ERROR")
                log_event("FATAL", "Configuration validation failed, exiting...")
                return 1
            
            # Initialize learning engine for ML-based alert ranking
            try:
                storage_dir = Path(__file__).parent / "data" / "learning"
                
                # Ensure storage directory exists
                storage_dir.mkdir(parents=True, exist_ok=True)
                
                self.learning_engine = HybridLearningEngine(storage_dir=str(storage_dir))
                log_system_state("LEARNING_ENGINE", "INITIALIZED")
                log_event("STARTUP", "Hybrid learning engine initialized")
                log_event("STARTUP", f"  Storage: {storage_dir}")
                log_event("STARTUP", "  Status: ML system ready for real-time learning")
                
                # EOD scheduler disabled - was causing KILL signal during startup
                # TODO: Re-enable after server is fully started, or move to cron
                # webhook_port = WebhookConfig.WEBHOOK_PORT
                # 
                # self.eod_scheduler = start_eod_scheduler_daemon(
                #     bot_url=f"http://localhost:{webhook_port}",
                #     trigger_time="15:30"  # 3:30 PM IST
                # )
                # log_system_state("EOD_SCHEDULER", "STARTED")
                # log_event("STARTUP", f"EOD scheduler daemon started (daily update at 3:30 PM IST on port {webhook_port})")
                log_event("STARTUP", "EOD scheduler disabled (use cron at 3:15 PM instead)")
                
            except Exception as e:
                log_error("LEARNING_INIT", "Failed to initialize learning engine", e,
                         recovery_action="Bot will continue without learning")
                log_event("WARNING", f"Learning engine initialization failed: {str(e)}")
                self.learning_engine = None
                self.eod_scheduler = None
            
            # Mark as running
            self.running = True
            log_system_state("BOT", "RUNNING")
            
            # Log critical startup information for autonomous debugging
            startup_summary = {
                "startup_time": time.time(),
                "trading_mode": TradingConfig.TRADING_MODE,
                "python_version": sys.version,
                "working_directory": str(Path.cwd()),
                "environment_loaded": True
            }
            
            log_system_state("STARTUP", "COMPLETE", startup_summary)
            log_event("STARTUP", "All components initialized successfully")
            log_event("STARTUP", f"Trading Mode: {TradingConfig.TRADING_MODE}")
            log_event("STARTUP", "Bot is ready to receive webhook alerts")
            
            # Final system readiness check
            log_system_state("SYSTEM", "READY", {
                "webhook_server": "starting",
                "broker": "will_initialize",
                "monitor": "will_initialize",
                "rate_limiter": "will_initialize"
            })
            
            # Start webhook server (this will block and handle everything)
            self.start_webhook_server()
            
        except KeyboardInterrupt:
            log_system_state("BOT", "KEYBOARD_INTERRUPT")
            log_event("SHUTDOWN", "Received keyboard interrupt")
            self.shutdown()
            return 0
        except Exception as e:
            log_system_state("BOT", "FATAL_ERROR", {"error": str(e)})
            log_error("MAIN_LOOP", "Fatal error in main loop", e,
                     recovery_action="Bot will exit")
            log_event("FATAL", f"Fatal error in main loop: {str(e)}")
            self.shutdown()
            return 1


def main():
    """Entry point function"""
    
    # Print startup banner
    print("=" * 50)
    print("    EQUITY TRADING BOT")
    print("    Powered by AngelOne SmartAPI")
    print("=" * 50)
    print()
    
    try:
        # Create and run bot
        bot = EquityTradingBot()
        exit_code = bot.run()
        
        print()
        print("Bot execution completed.")
        return exit_code
        
    except Exception as e:
        print(f"Fatal error: {str(e)}")
        log_event("FATAL", f"Unhandled exception: {str(e)}")
        return 1


if __name__ == "__main__":
    """Run the bot when script is executed directly"""
    
    # Ensure we're in the right directory
    script_dir = Path(__file__).parent
    if script_dir.name != "equity":
        print("Error: This script must be run from the equity directory")
        sys.exit(1)
    
    # Prevent multiple instances using PID file with atomic locking
    pid_file = script_dir / "equity_bot.pid"
    lock_file = script_dir / ".equity_bot.lock"
    
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
            print(f"❌ ERROR: Equity bot already running (another process holds lock)")
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
                    print(f"❌ ERROR: Equity bot already running (PID {old_pid})")
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
        
        print(f"✅ Started equity bot (PID {os.getpid()})")
    
    except Exception as e:
        print(f"❌ ERROR: Failed to acquire PID lock: {e}")
        sys.exit(1)
    
    # Run the bot
    exit_code = main()
    sys.exit(exit_code)