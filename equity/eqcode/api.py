"""
API Module - Equity Trading Bot

Flask webhook server for TradingView integration.
Handles:
- TradingView webhook alerts
- Symbol extraction and processing
- Capital and slot availability checks
- Order placement coordination
- Risk management validation
- Indestructible crash recovery
"""

import json
import time
import signal
import atexit
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
try:
    from flask import Flask, request, jsonify
except Exception:
    # Allow importing module in minimal test environments without Flask installed.
    Flask = None
    request = None
    def jsonify(obj):
        # Minimal jsonify fallback for non-HTTP test runs
        import json as _json
        return _json.dumps(obj)
import threading

from .config import (
    CapitalConfig, TradingConfig, WebhookConfig, 
    AngelOneConfig, get_config_summary, validate_config, DevConfig
)
try:
    from .angelone import AngelOneBroker, OrderStatus
except Exception:
    AngelOneBroker = None
    OrderStatus = None

try:
    from .bot_logging import (log_event, log_alert, log_webhook, 
                             log_order, log_monitor, log_error, log_analytics,
                             log_system_state, log_startup_info, log_trade)
    from .dummy_trade_tracker import get_dummy_tracker  # Paper trading for ML learning
except Exception:
    # Fallback no-op logging if bot_logging fails
    def log_event(*args, **kwargs):
        print(f"LOG_EVENT: {args}, {kwargs}")
    def log_alert(*args, **kwargs):
        print(f"LOG_ALERT: {args}, {kwargs}")
    def log_trade(*args, **kwargs):
        print(f"LOG_TRADE: {args}, {kwargs}")
    def log_webhook(*args, **kwargs):
        print(f"LOG_WEBHOOK: {args}, {kwargs}")
    def log_order(*args, **kwargs):
        print(f"LOG_ORDER: {args}, {kwargs}")
    def log_monitor(*args, **kwargs):
        print(f"LOG_MONITOR: {args}, {kwargs}")
    def log_error(*args, **kwargs):
        print(f"LOG_ERROR: {args}, {kwargs}")
    def log_analytics(*args, **kwargs):
        print(f"LOG_ANALYTICS: {args}, {kwargs}")
    def log_system_state(*args, **kwargs):
        print(f"LOG_SYSTEM_STATE: {args}, {kwargs}")
    def log_startup_info(*args, **kwargs):
        print(f"LOG_STARTUP_INFO: {args}, {kwargs}")

try:
    from .monitor import PositionMonitor
except Exception:
    PositionMonitor = None

try:
    from .state_recovery import state_manager, ensure_indestructible_startup
except Exception:
    state_manager = None
    def ensure_indestructible_startup():
        return None, None, None

# Import the single source of truth for alert validation
from .webhook_parser import validate_alert, process_symbol

# Import Priority Queue and Alert Queue
from .priority_queue import PriorityAPIQueue, AlertQueue, APIPriority

# Import Week 1 enhancements
try:
    from .signal_filters import validate_signal_quality, get_filter_statistics
except Exception as e:
    print(f"Warning: Could not import signal_filters: {e}")
    def validate_signal_quality(*args, **kwargs):
        return True, "Filter unavailable"
    def get_filter_statistics():
        return {}

try:
    from .drawdown_protection import drawdown_protector, can_take_trade as check_drawdown
except Exception as e:
    print(f"Warning: Could not import drawdown_protection: {e}")
    drawdown_protector = None
    def check_drawdown():
        return True, "Protection unavailable"

try:
    from .dynamic_risk import get_risk_parameters, calculate_dynamic_stop_loss
except Exception as e:
    print(f"Warning: Could not import dynamic_risk: {e}")
    def get_risk_parameters(*args, **kwargs):
        return {}
    def calculate_dynamic_stop_loss(*args, **kwargs):
        return 0

try:
    from .market_regime import detect_regime, get_regime_multiplier, can_trade_in_regime, get_regime_info
except Exception as e:
    print(f"Warning: Could not import market_regime: {e}")
    def detect_regime(*args, **kwargs):
        return 'choppy_low_vol'
    def get_regime_multiplier():
        return 1.0
    def can_trade_in_regime():
        return True
    def get_regime_info():
        return {}

try:
    from .correlation_risk import CorrelationAnalyzer
except Exception as e:
    # 🔧 FIX GAP-005: Correlation module is CRITICAL - log error clearly
    print(f"CRITICAL ERROR: Could not import correlation_risk: {e}")
    import sys
    sys.stderr.write(f"CRITICAL: Week 3 P3.1 (Correlation Risk) module failed to load: {e}\n")
    CorrelationAnalyzer = None
    _correlation_import_error = True

try:
    from .advanced_position_sizer import AdvancedPositionSizer
except Exception as e:
    # 🔧 FIX GAP-005: Advanced Position Sizer is CRITICAL - log error clearly
    print(f"CRITICAL ERROR: Could not import advanced_position_sizer: {e}")
    import sys
    sys.stderr.write(f"CRITICAL: Week 3 P3.3 (Advanced Position Sizer) module failed to load: {e}\n")
    AdvancedPositionSizer = None
    _position_sizer_import_error = True

# ===== HYBRID LEARNING INTEGRATION =====
try:
    from .hybrid_integration import (
        process_webhook_alerts,
        finalize_trade_learning,
        get_eod_analysis,
        get_integration_status
    )
    HYBRID_LEARNING_AVAILABLE = True
except Exception as e:
    print(f"Warning: Could not import hybrid_learning: {e}")
    HYBRID_LEARNING_AVAILABLE = False
    def process_webhook_alerts(*args, **kwargs):
        return {"error": "Hybrid learning unavailable"}
    def finalize_trade_learning(*args, **kwargs):
        return {"error": "Hybrid learning unavailable"}
    def get_eod_analysis(*args, **kwargs):
        return {"error": "Hybrid learning unavailable"}
    def get_integration_status(*args, **kwargs):
        return {"error": "Hybrid learning unavailable"}

# ===== MISSED TRADE LOGGING (Paper Trading) =====
try:
    from .missed_trade_logger import get_missed_trade_logger, log_missed_alert
    MISSED_TRADE_LOGGER_AVAILABLE = True
except Exception as e:
    print(f"Warning: Could not import missed_trade_logger: {e}")
    MISSED_TRADE_LOGGER_AVAILABLE = False
    def log_missed_alert(*args, **kwargs):
        pass  # No-op if logger unavailable
    def get_missed_trade_logger(*args, **kwargs):
        return None

# =============================================================================
# Global State Management (INDESTRUCTIBLE)
# =============================================================================

class TradingState:
    """Global trading state management with crash recovery"""
    
    def __init__(self):
        self.broker = None
        self.monitor = None
        self.active_positions = {}  # symbol -> position_data
        self.used_capital = 0.0
        self.available_slots = CapitalConfig.MAX_SLOTS
        self.correlation_analyzer = CorrelationAnalyzer() if CorrelationAnalyzer else None  # Week 3 P3.1
        self.position_sizer = AdvancedPositionSizer() if AdvancedPositionSizer else None  # Week 3 P3.3
        
        # ===== ALERT QUEUE: Burst-safe alert processing =====
        # Prevents rate limit timeouts when multiple alerts arrive rapidly
        self.alert_queue = None  # Will be initialized in initialize()
        self.alert_batch_log = []  # Accumulate alerts during session (for hybrid learning)
        self.alert_batch_lock = threading.Lock()
        
        self.lock = threading.Lock()
        self._start_time = time.time()
        self._startup_logged = False
        
        # Setup graceful shutdown handlers
        signal.signal(signal.SIGTERM, self._graceful_shutdown)
        signal.signal(signal.SIGINT, self._graceful_shutdown)
        atexit.register(self._cleanup_on_exit)
    
    def initialize(self):
        """Initialize broker and monitor with crash recovery"""
        from .bot_logging import log_broker_error
        
        print("🛡️ INITIALIZING INDESTRUCTIBLE TRADING BOT...")
        
        # Log startup
        startup_time = datetime.now().isoformat()
        log_event("BOT_STARTUP", "Trading bot starting up",
                 startup_time=startup_time,
                 capital_per_trade=CapitalConfig.CAP_PER_TRADE,
                 max_slots=CapitalConfig.MAX_SLOTS,
                 paper_trading=DevConfig.is_paper_trading())
        
        # Check for crash recovery
        recovered_positions, recovered_orders, recovered_config = ensure_indestructible_startup()
        
        if recovered_positions or recovered_orders:
            log_broker_error(
                error_type="BOT_CRASH_RECOVERY",
                message=f"Bot restarted after crash - recovering state",
                context={
                    "recovered_positions": len(recovered_positions) if recovered_positions else 0,
                    "recovered_orders": len(recovered_orders) if recovered_orders else 0,
                    "startup_time": startup_time
                },
                recovery_attempted=True
            )
        
        try:
            self.broker = AngelOneBroker()
            # In PAPER mode allow startup even if login fails (credentials may be absent)
            if DevConfig.is_paper_trading():
                login_ok = self.broker.login()
                if not login_ok:
                    log_event("BROKER_PAPER_MODE", "Broker login failed - continuing in PAPER mode with simulated orders")
            else:
                # LIVE mode requires successful login with exponential backoff
                max_login_attempts = 5
                base_wait_time = 60  # Start with 60 seconds
                
                for attempt in range(1, max_login_attempts + 1):
                    log_event("BROKER_LOGIN", f"Login attempt {attempt}/{max_login_attempts}")
                    
                    if self.broker.login():
                        log_event("BROKER_LOGIN", "✅ Login successful")
                        break
                    
                    if attempt < max_login_attempts:
                        # Exponential backoff: 60s, 120s, 240s, 480s
                        wait_time = base_wait_time * (2 ** (attempt - 1))
                        log_event("BROKER_LOGIN", f"❌ Login failed, waiting {wait_time}s before retry {attempt + 1}/{max_login_attempts}")
                        log_broker_error(
                            error_type="LOGIN_RETRY",
                            message=f"Login attempt {attempt} failed, retrying in {wait_time}s",
                            endpoint="initialize -> login",
                            context={"attempt": attempt, "wait_time": wait_time}
                        )
                        time.sleep(wait_time)
                    else:
                        # All attempts exhausted
                        log_broker_error(
                            error_type="BROKER_CRASH",
                            message=f"Bot startup failed - all {max_login_attempts} login attempts failed",
                            endpoint="initialize -> login",
                            context={"startup_time": startup_time, "total_attempts": max_login_attempts}
                        )
                        raise Exception(f"Failed to login to AngelOne after {max_login_attempts} attempts")
            
            # Restore positions if recovered from crash
            if recovered_positions:
                print(f"🔄 RESTORING {len(recovered_positions)} positions from crash recovery...")
                self.active_positions.update(recovered_positions)
                
                # ✅ FIX: Use capital_used field which is the source of truth
                # capital_used was set when position was created via allocate_capital()
                self.used_capital = sum(
                    pos.get('capital_used', 0)
                    for pos in recovered_positions.values()
                )
                self.available_slots = max(0, CapitalConfig.MAX_SLOTS - len(recovered_positions))
                
                print(f"✅ RECOVERY COMPLETE: {len(recovered_positions)} positions, ₹{self.used_capital:.2f} capital used")
                
                log_broker_error(
                    error_type="BOT_CRASH_RECOVERY",
                    message=f"Successfully recovered from crash",
                    context={
                        "recovered_positions": len(recovered_positions),
                        "used_capital": self.used_capital,
                        "available_slots": self.available_slots
                    },
                    recovery_attempted=True,
                    recovery_success=True
                )
        
        except Exception as e:
            log_broker_error(
                error_type="BROKER_CRASH",
                message=f"Bot initialization failed: {str(e)}",
                endpoint="initialize",
                context={
                    "exception_type": type(e).__name__,
                    "startup_time": startup_time
                }
            )
            raise
        
        # ===== INITIALIZE ALERT QUEUE: Burst-safe alert processing =====
        # This prevents rate limit timeouts when multiple alerts arrive rapidly
        try:
            # Create alert queue with async processor
            async def async_process_alert(alert_data):
                """Async wrapper for alert processing"""
                return await handle_buy_alert_async(alert_data)
            
            self.alert_queue = AlertQueue(
                process_alert_func=async_process_alert,
                processing_rate=1.5,  # 1 alert per 1.5 seconds = safe rate
                max_queue_size=500
            )
            log_event("ALERT_QUEUE", "Alert queue initialized - burst-safe processing enabled")
            
        except Exception as e:
            log_event("ALERT_QUEUE_ERROR", f"Failed to initialize alert queue: {e}")
            self.alert_queue = None
        
        # Initialize monitor with capital release callback
        # Monitor now enabled for position tracking and SL placement
        if PositionMonitor:
            try:
                self.monitor = PositionMonitor(self.broker, self.release_capital)
                log_event("MONITOR", "PositionMonitor instance created successfully")
                
                # Start monitor in background thread
                monitor_thread = threading.Thread(target=self.monitor.start_monitoring, daemon=True)
                monitor_thread.start()
                log_event("MONITOR", "Position monitor started in background")
                
                # Small delay to ensure monitor is fully initialized
                time.sleep(0.1)
                log_event("MONITOR", f"Monitor initialized with {len(self.monitor.positions)} existing positions")
                
            except Exception as e:
                log_event("MONITOR_ERROR", f"Failed to initialize monitor: {str(e)}")
                import traceback
                log_event("MONITOR_ERROR_DETAIL", traceback.format_exc())
                self.monitor = None
        else:
            log_event("MONITOR", "PositionMonitor NOT AVAILABLE - check import (SL orders will NOT be placed!)")
            log_event("MONITOR_DISABLED", "PositionMonitor class is None - check for import errors in monitor.py")
            self.monitor = None
        
        log_event("SYSTEM", "Trading system initialized successfully")
    
    def _graceful_shutdown(self, signum, frame):
        """Handle graceful shutdown"""
        from .bot_logging import log_broker_error
        
        print(f"\n🛑 GRACEFUL SHUTDOWN: Received signal {signum}")
        
        uptime_seconds = int(time.time() - self._start_time)
        
        log_event("BOT_SHUTDOWN", f"Trading bot shutting down gracefully",
                 signal=signum,
                 uptime_seconds=uptime_seconds,
                 active_positions=len(self.active_positions),
                 used_capital=self.used_capital,
                 reason="SIGTERM" if signum == signal.SIGTERM else "SIGINT" if signum == signal.SIGINT else "UNKNOWN")
        
        self._save_state()
        print("✅ State saved successfully")
        
        log_event("BOT_SHUTDOWN", "Shutdown complete")
        exit(0)
    
    def _cleanup_on_exit(self):
        """Cleanup on exit"""
        uptime_seconds = int(time.time() - self._start_time)
        
        log_event("BOT_CLEANUP", "Bot cleanup on exit",
                 uptime_seconds=uptime_seconds,
                 active_positions=len(self.active_positions))
        
        self._save_state()
        state_manager.stop_auto_save()
    
    def _save_state(self):
        """Save current state for crash recovery"""
        try:
            # Save all active positions
            for symbol, position in self.active_positions.items():
                state_manager.save_position(position)
            
            # Save current config
            config_data = {
                'used_capital': self.used_capital,
                'available_slots': self.available_slots,
                'uptime_seconds': int(time.time() - self._start_time),
                'last_save': datetime.now().isoformat()
            }
            state_manager.save_config(config_data)
            
        except Exception as e:
            print(f"❌ Error saving state: {e}")
    
    def add_position(self, symbol: str, position_data: Dict):
        """Add position with automatic state saving"""
        with self.lock:
            self.active_positions[symbol] = position_data
            
            # ✅ FIX: Capital and slots are ALREADY allocated in allocate_capital()
            # DO NOT allocate again here - just add to tracking and save
            
            # Save to persistent state immediately - WITH ERROR HANDLING
            try:
                if state_manager is None:
                    raise Exception("State manager not available")
                state_manager.save_position(position_data)
            except Exception as e:
                # CRITICAL: Position added to memory but failed to persist
                # Must rollback to maintain consistency
                log_event("ERROR", f"CRITICAL: Failed to save position {symbol} to database: {e}")
                del self.active_positions[symbol]
                raise Exception(f"Failed to persist position {symbol}: {str(e)}")
            
            capital_used = position_data.get('capital_used', 0)
            log_event("position_added", f"Added position {symbol} with ₹{capital_used:.2f} capital")
    
    def remove_position(self, symbol: str):
        """Remove position with state cleanup and persistence"""
        with self.lock:
            if symbol in self.active_positions:
                position = self.active_positions.pop(symbol)
                
                # ✅ FIX: Use capital_used field which is the source of truth
                # This was pre-allocated in allocate_capital() and stored in position_data
                capital_freed = position.get('capital_used', 0)
                self.used_capital = max(0, self.used_capital - capital_freed)
                self.available_slots = min(CapitalConfig.MAX_SLOTS, self.available_slots + 1)
                
                # Update position status in persistent state
                position['status'] = 'CLOSED'
                position['closed_at'] = datetime.now().isoformat()
                
                # 🔧 CRITICAL FIX #3: Error handling with rollback for position exit
                try:
                    if state_manager is None:
                        raise Exception("State manager not available")
                    state_manager.save_position(position)
                except Exception as e:
                    # CRITICAL: Restore state if save fails
                    self.active_positions[symbol] = position
                    self.used_capital += capital_freed
                    self.available_slots = max(0, self.available_slots - 1)
                    log_event("ERROR", f"CRITICAL: Failed to persist position close for {symbol}: {e}")
                    raise Exception(f"Failed to persist position close for {symbol}: {str(e)}")
                
                log_event("position_removed", f"Removed position {symbol}, freed ₹{capital_freed:.2f} capital")
    
    def get_capital_status(self) -> Dict[str, float]:
        """Get current capital status"""
        with self.lock:
            return {
                "max_capital": CapitalConfig.MAX_CAPITAL,
                "used_capital": self.used_capital,
                "available_capital": CapitalConfig.MAX_CAPITAL - self.used_capital,
                "cap_per_trade": CapitalConfig.CAP_PER_TRADE,
                "available_slots": self.available_slots,
                "max_slots": CapitalConfig.MAX_SLOTS
            }
    
    def can_take_position(self, required_capital: float) -> Tuple[bool, str]:
        """Check if we can take a new position"""
        with self.lock:
            # Check slots
            if self.available_slots <= 0:
                return False, "No available slots"
            
            # Check capital
            available_capital = CapitalConfig.MAX_CAPITAL - self.used_capital
            if required_capital > available_capital:
                return False, f"Insufficient capital (need: {required_capital}, available: {available_capital})"
            
            # Check per-trade limit
            if required_capital > CapitalConfig.CAP_PER_TRADE:
                return False, f"Trade capital exceeds limit (need: {required_capital}, limit: {CapitalConfig.CAP_PER_TRADE})"
            
            return True, "OK"
    
    def allocate_capital(self, symbol: str, capital: float):
        """Allocate capital for a position"""
        with self.lock:
            self.used_capital += capital
            self.available_slots -= 1
            log_event("CAPITAL", f"Allocated capital for {symbol}", 
                     amount=capital, used_total=self.used_capital, slots_left=self.available_slots)
    
    def release_capital(self, symbol: str, capital: float):
        """Release capital when position is closed"""
        with self.lock:
            self.used_capital = max(0, self.used_capital - capital)
            self.available_slots = min(CapitalConfig.MAX_SLOTS, self.available_slots + 1)
            
            # Remove from active positions
            if symbol in self.active_positions:
                del self.active_positions[symbol]
            
            log_event("CAPITAL", f"Released capital for {symbol}", 
                     amount=capital, used_total=self.used_capital, slots_left=self.available_slots)


# Global trading state
trading_state = TradingState()


# =============================================================================
# Flask App Setup
# =============================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = WebhookConfig.WEBHOOK_SECRET or 'default-secret-key'


# =============================================================================
# Webhook Alert Processing
# =============================================================================

# Note: validate_alert and process_symbol are now imported from webhook_parser.py


def calculate_position_size(symbol: str, price: float, ml_score: float = 0.65, alert: Dict = None) -> Tuple[int, float, float]:
    """
    Calculate position size based on capital and margin requirements with dynamic capital allocation
    
    With Week 3 P3.3 AdvancedPositionSizer:
    - Kelly Criterion for optimal sizing
    - Volatility-based adjustments
    - Correlation risk reduction
    - Win rate multiplier
    - Capital availability factor
    
    ENHANCED with Dynamic Capital Allocation:
    - ML confidence-based sizing (higher ML score → more capital)
    - Win/loss streak adjustments
    - Market regime considerations
    - Volatility adjustments
    
    Args:
        symbol: Stock symbol
        price: Current price
        ml_score: ML confidence score (0-1) for dynamic capital allocation
        alert: Full alert data (optional, for additional context)
        
    Returns:
        Tuple of (quantity, margin_required, total_charges)
    """
    # ===== DYNAMIC CAPITAL ALLOCATION: Adjust capital based on ML confidence =====
    try:
        from .dynamic_capital_allocator import calculate_dynamic_capital
        from .regime_filter import get_regime_info
        
        # Get recent trade outcomes for streak detection
        recent_trades = []
        try:
            from .pnl_analytics import get_pnl_analytics
            pnl = get_pnl_analytics()
            recent_trades_data = pnl.get_recent_trades(limit=5)
            recent_trades = [t.get('ml_outcome', 'UNKNOWN') for t in recent_trades_data if t.get('ml_outcome')]
        except Exception:
            pass
        
        # Get market regime
        market_regime = 'BULL'
        try:
            regime_info = get_regime_info()
            market_regime = regime_info.get('regime', 'BULL')
        except Exception:
            pass
        
        # Determine volatility (could be enhanced with actual volatility calculation)
        volatility = 'NORMAL'  # Default, can be enhanced later
        
        # Calculate dynamic capital allocation
        allocated_capital, breakdown = calculate_dynamic_capital(
            symbol=symbol,
            ml_score=ml_score,
            price=price,
            recent_trades=recent_trades,
            market_regime=market_regime,
            volatility=volatility
        )
        
        log_event("DYNAMIC_CAPITAL_ALLOCATED", f"💰 Dynamic capital for {symbol}",
                 symbol=symbol,
                 ml_score=round(ml_score, 3),
                 base_capital=CapitalConfig.CAP_PER_TRADE,
                 allocated_capital=round(allocated_capital, 2),
                 multiplier=round(breakdown['final_multiplier'], 2),
                 regime=market_regime,
                 recent_trades_count=len(recent_trades))
        
    except Exception as e:
        log_event("DYNAMIC_CAPITAL_ERROR", f"Failed to calculate dynamic capital for {symbol}: {e}")
        allocated_capital = CapitalConfig.CAP_PER_TRADE
    
    # ===== WEEK 3 P3.3: Use Advanced Position Sizer if available =====
    if trading_state.position_sizer:
        try:
            # Set capital for sizing
            trading_state.position_sizer.set_capital(CapitalConfig.MAX_CAPITAL)
            
            # Get advanced position size using DYNAMIC capital
            sizing_result = trading_state.position_sizer.calculate_position_size(
                symbol=symbol,
                current_price=price,
                quantity_base=CapitalConfig.calculate_quantity_for_capital(price, allocated_capital)
            )
            
            quantity = sizing_result.get('quantity', 0)
            details = sizing_result.get('details', {})
            
            # Calculate capital and charges
            trade_value = quantity * price
            margin_required = trade_value * AngelOneConfig.MARGIN_PERCENTAGE
            total_charges = CapitalConfig.calculate_total_charges(trade_value)
            total_capital_needed = margin_required + total_charges
            
            log_event("ADVANCED_POSITION_SIZE", f"Advanced sizing for {symbol}",
                     base_quantity=details.get('quantity_base'),
                     adjusted_quantity=quantity,
                     dynamic_capital=round(allocated_capital, 2),
                     kelly_ratio=details.get('kelly_ratio'),
                     volatility_multiplier=details.get('volatility_multiplier'),
                     correlation_multiplier=details.get('correlation_multiplier'),
                     streak_multiplier=details.get('streak_multiplier'),
                     capital_multiplier=details.get('capital_multiplier'),
                     final_multiplier=details.get('final_multiplier'),
                     win_rate=details.get('win_rate'),
                     loss_streak=details.get('loss_streak'))
            
            return quantity, margin_required + total_charges, total_charges
            
        except Exception as e:
            log_event("ADVANCED_SIZING_ERROR", f"Failed to use advanced sizer for {symbol}: {e}")
            # Fall back to standard sizing with dynamic capital
    
    # ===== FALLBACK: Standard Position Sizing with Dynamic Capital =====
    # Use dynamically allocated capital
    available_capital = allocated_capital
    
    # Calculate quantity considering margin (20% for MIS)
    quantity = CapitalConfig.calculate_quantity_for_capital(price, available_capital)
    
    # Calculate actual trade value
    trade_value = quantity * price
    
    # Calculate margin required (20% for MIS)
    margin_required = trade_value * AngelOneConfig.MARGIN_PERCENTAGE
    
    # Calculate charges
    total_charges = CapitalConfig.calculate_total_charges(trade_value)
    
    # Total capital needed
    total_capital_needed = margin_required + total_charges
    
    log_event("POSITION_SIZE", f"Calculated position for {symbol}",
             symbol=symbol,
             price=price, 
             quantity=quantity, 
             ml_score=round(ml_score, 3),
             dynamic_capital=round(available_capital, 2),
             margin=margin_required, 
             charges=total_charges, 
             total_needed=total_capital_needed)
    
    return quantity, margin_required + total_charges, total_charges


def validate_buy_signal_with_analytics(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate TradingView BUY signal using AngelOne enhanced analytics
    
    Args:
        alert: TradingView alert data with symbol, price, score, etc.
        
    Returns:
        Dictionary with validation result:
        {
            "approved": bool,
            "reason": str,
            "signal": str,
            "details": dict
        }
    """
    symbol = alert["symbol"]
    
    # Extract score and confidence from the processed alert
    quality = alert.get("quality", {})
    alert_indicators = alert.get("indicators", {})  # FIXED: Renamed to avoid shadowing
    
    tv_score = quality.get("score", 0) or alert_indicators.get("score", 0) or alert.get("score", 0)
    tv_confidence = quality.get("confidence", 0) or alert_indicators.get("confidence", 0) or alert.get("confidence", 0)
    
    try:
        # Skip analytics validation in paper trading or if broker not available
        if DevConfig.is_paper_trading() or not trading_state.broker:
            log_event("ANALYTICS", f"Skipping analytics validation for {symbol} (paper trading or no broker)")
            return {
                "approved": True,
                "reason": "Paper trading mode - analytics validation skipped",
                "signal": "SKIP",
                "details": {"mode": "paper_trading"}
            }
        
        # CRITICAL FIX: Skip analytics validation if rate limiter is exhausted
        # This prevents order rejection due to burst of validation API calls
        try:
            rate_limiter = trading_state.broker.rate_limiter
            if hasattr(rate_limiter, 'get_statistics'):
                stats = rate_limiter.get_statistics()
                second_bucket = stats.get('second_bucket', {})
                available_tokens = second_bucket.get('tokens', 1)
                
                # If <3 tokens available, skip analytics to reserve tokens for order placement
                if available_tokens < 3:
                    log_event("ANALYTICS_SKIPPED_RATE_LIMIT", 
                             f"Skipping analytics for {symbol} - rate limiter exhausted ({available_tokens:.1f} tokens available)",
                             symbol=symbol, available_tokens=round(available_tokens, 1))
                    return {
                        "approved": True,
                        "reason": "Rate limit protection - analytics validation skipped to preserve tokens for order placement",
                        "signal": "RATE_LIMIT_PROTECTED",
                        "details": {"tokens_available": round(available_tokens, 1)}
                    }
        except Exception as e:
            # If rate limiter check fails, continue normally
            log_event("RATE_LIMIT_CHECK_ERROR", f"Error checking rate limiter: {e}")
        
        # Get enhanced analytics from AngelOne
        log_event("ANALYTICS", f"Fetching enhanced analytics for {symbol}")
        analytics = trading_state.broker.get_enhanced_analytics(
            symbol=symbol,
            interval="FIVE_MINUTE",  # 5-minute analysis for intraday
            days_back=3              # Last 3 days of data
        )
        
        if analytics.get('error'):
            log_event("ANALYTICS_ERROR", f"Failed to get analytics for {symbol}: {analytics['error']}")
            # Don't block trading if analytics fail - fall back to TradingView only
            return {
                "approved": True,
                "reason": f"Analytics unavailable ({analytics['error']}) - using TradingView signal only",
                "signal": "FALLBACK",
                "details": {"error": analytics['error']}
            }
        
        # Extract technical indicators and signals
        technical_indicators = analytics.get('technical_indicators', {})  # FIXED: Renamed to avoid shadowing
        signals = analytics.get('signals', {})
        
        rsi = technical_indicators.get('rsi', 50)
        bb_position = technical_indicators.get('bb_position', 0.5)
        price_vs_sma20 = technical_indicators.get('price_vs_sma20', 0)
        
        rsi_signal = signals.get('rsi_signal', 'NEUTRAL')
        bb_signal = signals.get('bb_signal', 'MIDDLE') 
        trend_signal = signals.get('trend_signal', 'NEUTRAL')
        
        # Analytics-based validation logic
        validation_reasons = []
        
        # 1. RSI Check - avoid overbought conditions
        if rsi > 75:
            validation_reasons.append(f"RSI overbought ({rsi:.1f})")
        
        # 2. Bollinger Bands Check - prefer lower/middle band entries
        if bb_position > 0.85:
            validation_reasons.append(f"Price near upper Bollinger Band ({bb_position:.2f})")
        
        # 3. Trend Check - prefer bullish trend
        if trend_signal == "BEARISH" and price_vs_sma20 < -2:
            validation_reasons.append(f"Strong bearish trend (price {price_vs_sma20:.1f}% below SMA20)")
        
        # 4. TradingView Signal Quality Check (relaxed for testing)
        if tv_score < 50 or tv_confidence < 70:  # Lowered thresholds for testing
            validation_reasons.append(f"Low TradingView signal quality (score:{tv_score}, confidence:{tv_confidence})")
        
        # Decide approval based on validation
        if validation_reasons:
            return {
                "approved": False,
                "reason": "; ".join(validation_reasons),
                "signal": "REJECT",
                "details": {
                    "rsi": rsi,
                    "bb_position": bb_position,
                    "trend_signal": trend_signal,
                    "tv_score": tv_score,
                    "tv_confidence": tv_confidence
                }
            }
        else:
            # All checks passed - approve the signal
            approval_factors = []
            if rsi < 70:
                approval_factors.append(f"RSI healthy ({rsi:.1f})")
            if bb_position < 0.8:
                approval_factors.append(f"BB position good ({bb_position:.2f})")
            if trend_signal == "BULLISH":
                approval_factors.append("Bullish trend")
            if tv_score >= 70:
                approval_factors.append(f"Strong TV signal ({tv_score})")
            
            return {
                "approved": True,
                "reason": "; ".join(approval_factors) if approval_factors else "All analytics checks passed",
                "signal": "APPROVE",
                "details": {
                    "rsi": rsi,
                    "bb_position": bb_position,
                    "trend_signal": trend_signal,
                    "tv_score": tv_score,
                    "tv_confidence": tv_confidence,
                    "data_points": analytics.get('data_points', 0)
                }
            }
        
    except Exception as e:
        log_event("ANALYTICS_ERROR", f"Exception in analytics validation for {symbol}: {str(e)}")
        # Don't block trading if analytics validation fails
        return {
            "approved": True,
            "reason": f"Analytics validation error ({str(e)}) - using TradingView signal only",
            "signal": "ERROR_FALLBACK",
            "details": {"exception": str(e)}
        }


def classify_rejection_reason(error_msg: str, symbol: str = None) -> Tuple[str, str]:
    """
    Classify broker rejection reasons to identify scrutiny, blacklist, observation status.
    
    Args:
        error_msg: Error message from broker
        symbol: Symbol being traded (optional, for logging)
        
    Returns:
        Tuple of (rejection_type, detailed_reason)
        
    Classification Types:
    - SCRUTINY: Symbol under regulatory scrutiny (NSE/BSE action)
    - BLACKLIST: Symbol blacklisted by broker
    - OBSERVATION: Symbol under observation (caution list)
    - TRADING_HALT: Trading halted on symbol
    - CIRCUIT_BREAKER: Upper/lower circuit limit
    - RATE_LIMITED: API rate limited
    - SESSION_ERROR: Session/auth error
    - INSUFFICIENT_FUNDS: Not enough capital
    - INSTRUMENT_ERROR: Invalid/unavailable instrument
    - API_ERROR: Generic API error
    - UNKNOWN: Unclassified error
    """
    if not error_msg:
        return "UNKNOWN", "No error message provided"
    
    error_lower = error_msg.lower()
    
    # ===== SCRUTINY / BLACKLIST / OBSERVATION DETECTION =====
    
    # Scrutiny patterns - NSE/BSE regulatory action
    scrutiny_patterns = [
        'under scrutiny', 'regulatory scrutiny', 'nse scrutiny', 'bse scrutiny',
        'scrutinized', 'under examination', 'regulatory examination',
        'under regulatory action', 'regulatory action pending'
    ]
    
    # Blacklist patterns
    blacklist_patterns = [
        'blacklist', 'blacklisted', 'in blacklist', 'blacklist list',
        'suspended symbol', 'symbol suspended', 'banned symbol', 'symbol banned',
        'delisted', 'de-list', 'removal from listing'
    ]
    
    # Observation/Caution patterns
    observation_patterns = [
        'under observation', 'observation list', 'caution list', 'under caution',
        'monitoring', 'under monitoring', 'watch list', 'on watch',
        'restricted trading', 'restricted symbol', 'restricted for trading'
    ]
    
    # Trading halt patterns
    halt_patterns = [
        'trading halt', 'halted', 'halt', 'trading halted', 'market halt',
        'trading suspended', 'suspended from trading', 'no trading',
        'not available for trading', 'not tradeable'
    ]
    
    # Circuit breaker patterns
    circuit_patterns = [
        'upper circuit', 'lower circuit', 'circuit limit', 'circuit breaker',
        'circuit hit', 'at circuit limit', 'price limit'
    ]
    
    # Rate limit patterns
    rate_patterns = [
        'rate limit', 'rate exceeded', 'api rate', 'request limit', 'too many',
        'access denied', 'throttl', 'request timeout'
    ]
    
    # Session/Auth patterns
    session_patterns = [
        'invalid token', 'token expired', 'session invalid', 'session expired',
        'unauthorized', 'not authenticated', 'auth failed', 'login failed'
    ]
    
    # Funds patterns
    funds_patterns = [
        'insufficient fund', 'not enough balance', 'margin shortfall',
        'capital insuffic', 'exceeds limit', 'exceed margin'
    ]
    
    # Instrument patterns
    instrument_patterns = [
        'token not found', 'invalid instrument', 'unknown instrument',
        'instrument error', 'symbol not found', 'unknown symbol',
        'invalid symbol', 'not available', 'unavailable'
    ]
    
    # Check patterns in order of importance
    if any(p in error_lower for p in scrutiny_patterns):
        detail = f"Symbol {symbol} is under regulatory scrutiny (NSE/BSE action)"
        return "SCRUTINY", detail
    
    if any(p in error_lower for p in blacklist_patterns):
        detail = f"Symbol {symbol} is blacklisted or delisted"
        return "BLACKLIST", detail
    
    if any(p in error_lower for p in observation_patterns):
        detail = f"Symbol {symbol} is under observation (caution list)"
        return "OBSERVATION", detail
    
    if any(p in error_lower for p in halt_patterns):
        detail = f"Trading halted for symbol {symbol}"
        return "TRADING_HALT", detail
    
    if any(p in error_lower for p in circuit_patterns):
        detail = f"Circuit limit hit on symbol {symbol} - cannot trade at current price"
        return "CIRCUIT_BREAKER", detail
    
    if any(p in error_lower for p in rate_patterns):
        detail = "API rate limit exceeded - retry after backoff"
        return "RATE_LIMITED", detail
    
    if any(p in error_lower for p in session_patterns):
        detail = "Session/authentication error - may need to refresh token"
        return "SESSION_ERROR", detail
    
    if any(p in error_lower for p in funds_patterns):
        detail = "Insufficient capital or margin for this trade"
        return "INSUFFICIENT_FUNDS", detail
    
    if any(p in error_lower for p in instrument_patterns):
        detail = f"Instrument/symbol {symbol} not found or unavailable"
        return "INSTRUMENT_ERROR", detail
    
    if 'error' in error_lower or 'failed' in error_lower:
        return "API_ERROR", f"Broker API error: {error_msg}"
    
    return "UNKNOWN", error_msg


def handle_buy_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle BUY alert from TradingView with enhanced analytics validation
    
    Args:
        alert: Processed alert data
        
    Returns:
        Response dictionary
    """
    symbol = alert["symbol"]
    price = alert["price"]
    
    try:
        from .bot_logging import log_trade_execution
        
        log_trade_execution("ALERT_RECEIVED", symbol, "BUY", 
                          price=price, 
                          confidence=alert.get("confidence"),
                          score=alert.get("score"))
        
        # Check if already have position in this symbol
        if symbol in trading_state.active_positions:
            existing_pos = trading_state.active_positions[symbol]
            existing_qty = existing_pos.get("quantity", 0)
            existing_status = existing_pos.get("status", "UNKNOWN")

            # 🔧 CRITICAL FIX: Reject BUY if position is CLOSED (square-off in progress)
            if existing_status == "CLOSED":
                log_event("BUY_REJECTED_SQUARE_OFF", f"BUY rejected for {symbol} - position is being squared off",
                         symbol=symbol, status=existing_status, close_reason=existing_pos.get("close_reason"))
                log_trade_execution("EXECUTION_BLOCKED", symbol, "BUY",
                                  reason="Position being squared off - cannot re-enter",
                                  existing_status=existing_status)
                return {
                    "status": "rejected",
                    "reason": f"Cannot trade {symbol} - position being squared off at EOD",
                    "symbol": symbol,
                    "reason_detail": "Position already marked for closure"
                }

            # If the existing position has invalid quantity, remove it and continue with BUY
            if existing_qty <= 0:
                log_event("SUSPICIOUS_STATE", f"Existing position for {symbol} has invalid quantity",
                         symbol=symbol,
                         quantity=existing_qty,
                         status=existing_status,
                         action="REMOVING_INVALID_POSITION")
                del trading_state.active_positions[symbol]
            else:
                # Normal duplicate: block re-entry
                log_trade_execution("EXECUTION_BLOCKED", symbol, "BUY",
                                  reason="Already have position",
                                  existing_position=existing_pos.get("entry_price"),
                                  existing_quantity=existing_qty,
                                  existing_status=existing_status)
                
                # ===== MISSED TRADE LOGGING: Log for paper trading simulation =====
                # When a signal is rejected due to duplicate position, log it
                try:
                    if MISSED_TRADE_LOGGER_AVAILABLE:
                        log_missed_alert(
                            symbol=symbol,
                            action="BUY",
                            entry_price=price,
                            quantity=1,  # Unknown qty, use 1 as placeholder
                            reason="DUPLICATE_POSITION",
                            alert_data=alert
                        )
                        log_event("MISSED_ALERT_LOGGED", 
                                 f"Logged missed BUY alert for {symbol} (duplicate position)",
                                 symbol=symbol, entry_price=price)
                except Exception as e:
                    log_event("MISSED_ALERT_LOG_ERROR", f"Failed to log missed alert: {e}")
                
                # Log missed opportunity for PNL analysis
                try:
                    from .pnl_analytics import get_pnl_analytics
                    pnl_analytics = get_pnl_analytics()
                    pnl_analytics.log_missed_signal(
                        symbol=symbol,
                        action="BUY",
                        signal_price=price,
                        reason="Already have position",
                        alert_data=alert
                    )
                    log_event("MISSED_OPPORTUNITY", f"Logged missed BUY signal for {symbol} (duplicate position)")
                except Exception as e:
                    log_event("ERROR", f"Failed to log missed signal for {symbol}: {e}")
                
                return {
                    "status": "rejected",
                    "reason": f"Already have position in {symbol}",
                    "symbol": symbol
                }
        
        log_trade_execution("VALIDATION_PASSED", symbol, "BUY",
                          price=price, 
                          confidence=alert.get("confidence"),
                          score=alert.get("score"))
        
        # ===== WEEK 2: Market Regime Check =====
        # Check if market conditions allow trading
        if not can_trade_in_regime():
            regime_info = get_regime_info()
            log_event("REGIME_BLOCKED", f"Trading blocked for {symbol} - CRISIS mode detected")
            log_trade_execution("REGIME_REJECTED", symbol, "BUY",
                              reason="CRISIS market regime",
                              regime_info=regime_info)
            
            # ===== MISSED TRADE LOGGING: Log for paper trading simulation =====
            try:
                if MISSED_TRADE_LOGGER_AVAILABLE:
                    log_missed_alert(
                        symbol=symbol,
                        action="BUY",
                        entry_price=price,
                        quantity=1,
                        reason="CRISIS_REGIME",
                        alert_data=alert
                    )
                    log_event("MISSED_ALERT_LOGGED", 
                             f"Logged missed BUY alert for {symbol} (CRISIS regime)",
                             symbol=symbol, entry_price=price)
            except Exception as e:
                log_event("MISSED_ALERT_LOG_ERROR", f"Failed to log missed alert: {e}")
            
            # Log missed opportunity
            try:
                from .pnl_analytics import get_pnl_analytics
                pnl_analytics = get_pnl_analytics()
                pnl_analytics.log_missed_signal(
                    symbol=symbol,
                    action="BUY",
                    signal_price=price,
                    reason="CRISIS market regime",
                    alert_data=alert
                )
            except Exception:
                pass
            
            return {
                "status": "rejected",
                "reason": "CRISIS market regime - trading temporarily disabled",
                "symbol": symbol,
                "regime": regime_info
            }
        
        # ===== ML SIGNAL FILTERING: Validate signal quality before execution =====
        try:
            from .ml_signal_filter import validate_with_ml
            is_ml_valid, ml_score, ml_details = validate_with_ml(symbol, alert, price)
            
            log_event("ML_VALIDATION", f"{symbol} ML score: {ml_score:.3f} (threshold: 0.60)",
                     symbol=symbol, ml_score=ml_score, is_valid=is_ml_valid)
            
            if not is_ml_valid:
                log_event("ML_REJECTED", f"Trade blocked for {symbol} - ML score too low: {ml_score:.3f}",
                         symbol=symbol, ml_score=ml_score, threshold=0.60)
                log_trade_execution("ML_REJECTED", symbol, "BUY",
                                  ml_score=ml_score,
                                  threshold=0.60,
                                  reason=f"ML quality score below threshold: {ml_score:.3f} < 0.60")
                
                # Log as missed trade for paper trading
                try:
                    if MISSED_TRADE_LOGGER_AVAILABLE:
                        log_missed_alert(
                            symbol=symbol,
                            action="BUY",
                            entry_price=price,
                            quantity=1,
                            reason=f"ML_SCORE_LOW:{ml_score:.3f}",
                            alert_data=alert
                        )
                        log_event("MISSED_ALERT_LOGGED", 
                                 f"Logged missed BUY alert for {symbol} (ML rejected)",
                                 symbol=symbol, entry_price=price, ml_score=ml_score)
                except Exception as e:
                    log_event("MISSED_ALERT_LOG_ERROR", f"Failed to log missed alert: {e}")
                
                # Log missed opportunity for PNL analysis
                try:
                    from .pnl_analytics import get_pnl_analytics
                    pnl_analytics = get_pnl_analytics()
                    pnl_analytics.log_missed_signal(
                        symbol=symbol,
                        action="BUY",
                        signal_price=price,
                        reason=f"ML score too low: {ml_score:.3f}",
                        alert_data=alert
                    )
                except Exception:
                    pass
                
                return {
                    "status": "rejected",
                    "reason": f"ML quality score below threshold: {ml_score:.3f} < 0.60",
                    "symbol": symbol,
                    "ml_score": ml_score,
                    "ml_details": ml_details
                }
            
            # ML approved - log success
            log_event("ML_APPROVED", f"Trade approved for {symbol} - ML score: {ml_score:.3f}",
                     symbol=symbol, ml_score=ml_score)
            log_trade_execution("ML_APPROVED", symbol, "BUY",
                              ml_score=ml_score,
                              threshold=0.60)
            
        except Exception as ml_error:
            # If ML fails, log error but don't block trade
            log_event("ML_ERROR", f"ML validation failed for {symbol}, allowing trade: {ml_error}",
                     symbol=symbol, error=str(ml_error))
            ml_score = 0.5  # Neutral score on error
        
        # Enhanced Analytics Validation - validate TradingView signal with AngelOne technical analysis
        analytics_validation = validate_buy_signal_with_analytics(alert)
        if not analytics_validation["approved"]:
            log_event("ANALYTICS_FILTER", f"Buy signal filtered out for {symbol}: {analytics_validation['reason']}")
            log_trade_execution("ANALYTICS_REJECTED", symbol, "BUY",
                              reason=analytics_validation['reason'],
                              analytics_details=analytics_validation.get("details", {}))
            
            # ===== MISSED TRADE LOGGING: Log for paper trading simulation =====
            try:
                if MISSED_TRADE_LOGGER_AVAILABLE:
                    log_missed_alert(
                        symbol=symbol,
                        action="BUY",
                        entry_price=price,
                        quantity=1,
                        reason="ANALYTICS_REJECTED",
                        alert_data=alert
                    )
                    log_event("MISSED_ALERT_LOGGED", 
                             f"Logged missed BUY alert for {symbol} (analytics rejected)",
                             symbol=symbol, entry_price=price)
            except Exception as e:
                log_event("MISSED_ALERT_LOG_ERROR", f"Failed to log missed alert: {e}")
            
            # Track filtered signal for analytics
            try:
                from .analytics.alert_integration import track_execution_failure
                track_execution_failure(alert, f"Analytics filter: {analytics_validation['reason']}")
            except Exception:
                pass  # Never break trading for analytics
            
            # Log missed opportunity for PNL analysis
            try:
                from .pnl_analytics import get_pnl_analytics
                pnl_analytics = get_pnl_analytics()
                pnl_analytics.log_missed_signal(
                    symbol=symbol,
                    action="BUY",
                    signal_price=price,
                    reason=f"Analytics filter: {analytics_validation['reason']}",
                    alert_data=alert
                )
                log_event("MISSED_OPPORTUNITY", f"Logged missed BUY signal for {symbol}")
            except Exception as e:
                log_event("ERROR", f"Failed to log missed signal for {symbol}: {e}")
            
            return {
                "status": "rejected",
                "reason": f"Analytics validation failed: {analytics_validation['reason']}",
                "symbol": symbol,
                "analytics": analytics_validation.get("details", {})
            }
        
        # Log successful analytics validation
        log_event("ANALYTICS_APPROVED", f"Buy signal approved for {symbol}", 
                 tv_score=alert.get('score', 'N/A'),
                 analytics_signal=analytics_validation.get('signal', 'N/A'))
        log_trade_execution("ANALYTICS_APPROVED", symbol, "BUY",
                          tv_score=alert.get('score'),
                          analytics_signal=analytics_validation.get('signal'))
        
        # Calculate position size with dynamic capital allocation based on ML score
        quantity, required_capital, charges = calculate_position_size(symbol, price, ml_score=ml_score, alert=alert)
        
        log_trade_execution("POSITION_SIZE_CALCULATED", symbol, "BUY",
                          quantity=quantity,
                          required_capital=required_capital,
                          charges=charges,
                          ml_score=round(ml_score, 3))
        
        if quantity <= 0:
            log_trade_execution("EXECUTION_FAILED", symbol, "BUY",
                              reason=f"Invalid quantity: {quantity}")
            return {
                "status": "rejected", 
                "reason": f"Invalid quantity calculated: {quantity}",
                "symbol": symbol
            }
        
        # Check capital and slots availability
        can_trade, reason = trading_state.can_take_position(required_capital)
        if not can_trade:
            log_trade_execution("CAPITAL_CHECK_FAILED", symbol, "BUY",
                              required_capital=required_capital,
                              reason=reason,
                              available_capital=trading_state.get_capital_status()["available_capital"],
                              available_slots=trading_state.get_capital_status()["available_slots"])
            
            # ===== MISSED TRADE LOGGING: Log for paper trading simulation =====
            # When a signal is rejected, log it so we can paper trade at EOD
            try:
                if MISSED_TRADE_LOGGER_AVAILABLE:
                    log_missed_alert(
                        symbol=symbol,
                        action="BUY",
                        entry_price=price,
                        quantity=quantity,
                        reason="CAPITAL_UNAVAILABLE" if "capital" in reason.lower() else "SLOT_FULL",
                        alert_data=alert
                    )
                    log_event("MISSED_ALERT_LOGGED", 
                             f"Logged missed BUY alert for {symbol} (will paper trade at EOD)",
                             symbol=symbol, entry_price=price, quantity=quantity, reason=reason)
            except Exception as e:
                log_event("MISSED_ALERT_LOG_ERROR", f"Failed to log missed alert: {e}")
            
            # ===== PAPER TRADING: Create dummy entry for ML learning =====
            # When a high-quality signal is rejected, track it hypothetically
            try:
                ml_score = alert.get('ml_score', 0)
                
                # Only create dummy trades for signals with decent ML confidence
                if ml_score >= 0.60:  # Match our ML threshold
                    dummy_tracker = get_dummy_tracker()
                    dummy_id = dummy_tracker.create_dummy_entry(
                        alert=alert,
                        entry_price=price,
                        ml_score=ml_score,
                        rejection_reason=reason,
                        quantity=quantity
                    )
                    log_event("DUMMY_TRADE_CREATED", 
                             f"Created dummy entry for {symbol} (ML: {ml_score:.2f})",
                             dummy_id=dummy_id,
                             entry_price=price,
                             rejection_reason=reason)
            except Exception as e:
                # Never break trading for dummy trades
                log_event("DUMMY_TRADE_ERROR", f"Failed to create dummy trade: {e}")
            
            # Track missed opportunity for analytics
            try:
                from .analytics.alert_integration import track_execution_failure
                track_execution_failure(alert, reason)
            except Exception:
                pass  # Never break trading for analytics
            
            return {
                "status": "rejected",
                "reason": reason,
                "symbol": symbol,
                "required_capital": required_capital
            }
        
        log_trade_execution("CAPITAL_CHECK_PASSED", symbol, "BUY",
                          available_capital=trading_state.get_capital_status()["available_capital"],
                          available_slots=trading_state.get_capital_status()["available_slots"])
        
        # ===== WEEK 3: Correlation Risk Check (P3.1) =====
        # Check if position is correlated with losing positions
        if trading_state.correlation_analyzer:
            try:
                correlated_losses = trading_state.correlation_analyzer.detect_correlated_losses()
                
                if correlated_losses:
                    # Check if this symbol would be correlated with any losing position
                    correlation_risk = trading_state.correlation_analyzer.get_position_correlation_risk(symbol)
                    
                    if correlation_risk > 0.5:  # High correlation threshold
                        log_event("CORRELATION_RISK_DETECTED", 
                                 f"Trade blocked for {symbol} - correlated with losing positions",
                                 symbol=symbol, 
                                 correlation_risk=correlation_risk,
                                 losing_symbols=[loss['symbol'] for loss in correlated_losses])
                        log_trade_execution("CORRELATION_RISK_BLOCKED", symbol, "BUY",
                                          correlation_risk=correlation_risk,
                                          reason="Correlated with losing positions",
                                          losing_positions=correlated_losses)
                        
                        # ===== MISSED TRADE LOGGING: Log for paper trading simulation =====
                        try:
                            if MISSED_TRADE_LOGGER_AVAILABLE:
                                log_missed_alert(
                                    symbol=symbol,
                                    action="BUY",
                                    entry_price=price,
                                    quantity=1,
                                    reason="CORRELATION_RISK",
                                    alert_data=alert
                                )
                                log_event("MISSED_ALERT_LOGGED", 
                                         f"Logged missed BUY alert for {symbol} (correlation risk)",
                                         symbol=symbol, entry_price=price)
                        except Exception as e:
                            log_event("MISSED_ALERT_LOG_ERROR", f"Failed to log missed alert: {e}")
                        
                        # Log missed opportunity
                        try:
                            from .pnl_analytics import get_pnl_analytics
                            pnl_analytics = get_pnl_analytics()
                            pnl_analytics.log_missed_signal(
                                symbol=symbol,
                                action="BUY",
                                signal_price=price,
                                reason=f"Correlation risk ({correlation_risk:.2f})",
                                alert_data=alert
                            )
                        except Exception:
                            pass
                        
                        return {
                            "status": "rejected",
                            "reason": f"Correlation risk too high ({correlation_risk:.2f}) - avoid opening correlated positions",
                            "symbol": symbol,
                            "correlation_risk": correlation_risk,
                            "correlated_losses": correlated_losses
                        }
                    
                    log_event("CORRELATION_CHECK_PASSED", f"Correlation risk acceptable for {symbol}",
                             correlation_risk=correlation_risk)
                
                # Track this position for correlation analysis
                trading_state.correlation_analyzer.add_position(symbol, price, datetime.now(), quantity)
                log_event("CORRELATION_TRACKING_ADDED", f"Position {symbol} added to correlation tracking")
                
            except Exception as e:
                # Never break trading for correlation analysis
                log_event("CORRELATION_CHECK_ERROR", f"Error in correlation risk check: {e}", symbol=symbol)
        
        # Check drawdown protection (Week 1 P1.2)
        if drawdown_protector:
            can_trade_dd, dd_reason = check_drawdown()
            if not can_trade_dd:
                log_event("DRAWDOWN_PROTECTION_HIT", f"Trade blocked by drawdown protection: {dd_reason}",
                         symbol=symbol, reason=dd_reason)
                log_trade_execution("DRAWDOWN_CHECK_FAILED", symbol, "BUY",
                                  reason=dd_reason,
                                  status=drawdown_protector.get_status())
                
                # ===== MISSED TRADE LOGGING: Log for paper trading simulation =====
                try:
                    if MISSED_TRADE_LOGGER_AVAILABLE:
                        log_missed_alert(
                            symbol=symbol,
                            action="BUY",
                            entry_price=price,
                            quantity=1,
                            reason="DRAWDOWN_PROTECTION",
                            alert_data=alert
                        )
                        log_event("MISSED_ALERT_LOGGED", 
                                 f"Logged missed BUY alert for {symbol} (drawdown protection)",
                                 symbol=symbol, entry_price=price)
                except Exception as e:
                    log_event("MISSED_ALERT_LOG_ERROR", f"Failed to log missed alert: {e}")
                
                return {
                    "status": "rejected",
                    "reason": f"Drawdown protection: {dd_reason}",
                    "symbol": symbol
                }
            
            # Adjust position size for recovery mode (Week 1 P1.2)
            size_multiplier = drawdown_protector.get_position_size_multiplier()
            
            # Apply regime-based position sizing (Week 2 P2.1)
            regime_multiplier = get_regime_multiplier()
            combined_multiplier = size_multiplier * regime_multiplier
            
            if combined_multiplier < 1.0:
                original_quantity = quantity
                quantity = max(1, int(quantity * combined_multiplier))
                required_capital = quantity * price + charges
                
                multiplier_reasons = []
                if size_multiplier < 1.0:
                    multiplier_reasons.append(f"recovery_mode({size_multiplier:.2f}x)")
                if regime_multiplier < 1.0:
                    multiplier_reasons.append(f"regime({regime_multiplier:.2f}x)")
                
                log_event("POSITION_SIZE_ADJUSTED", f"Position size reduced for {symbol}",
                         symbol=symbol, original_qty=original_quantity, new_qty=quantity,
                         combined_multiplier=combined_multiplier,
                         reasons=multiplier_reasons)
                log_trade_execution("SIZE_ADJUSTED", symbol, "BUY",
                                  original_quantity=original_quantity,
                                  adjusted_quantity=quantity,
                                  drawdown_multiplier=size_multiplier,
                                  regime_multiplier=regime_multiplier,
                                  combined_multiplier=combined_multiplier)
        
        # ===== CANDLE CONFIRMATION (NEW - Entry Validation) =====
        # Confirm BUY signal with candle analysis to reduce false signals
        try:
            from .candle_integration import EntryConfirmationEngine
            
            confirmation_engine = EntryConfirmationEngine(
                broker_api=trading_state.broker,
                smart_api=trading_state.smart_api,
                min_confidence=0.75  # 75% minimum confidence
            )
            
            # Get token for symbol (hardcoded mapping for now)
            SYMBOL_TOKEN_MAP = {
                "RELIANCE": "3045",
                "SBIN": "4119",
                "INFY": "4963",
                "TCS": "3789",
                "HDFC": "1333",
                "ICICIBANK": "5920",
                "WIPRO": "7229",
                "AXIS": "3456",
                "BAJAJFINSV": "5087",
                "JSWSTEEL": "5980",
                "MARUTI": "7718",
                "M&M": "7701",
                "BAJAJ-AUTO": "5040",
                "HCLTECH": "5010",
                "ITC": "4419",
                "BHARTIARTL": "4957",
            }
            
            token = SYMBOL_TOKEN_MAP.get(symbol)
            
            # Only confirm if we have token mapping
            if token:
                confirmed, reason, confidence = confirmation_engine.confirm_buy_signal(
                    symbol=symbol,
                    exchange="NSE",
                    token=token
                )
                
                if not confirmed:
                    # Entry rejected by candle analysis
                    log_event("ENTRY_REJECTED_CANDLE", f"BUY signal rejected by candle analysis",
                             symbol=symbol, confidence=confidence, reason=reason)
                    log_trade_execution("ENTRY_REJECTED_CANDLE", symbol, "BUY",
                                      confidence=confidence,
                                      reason=reason,
                                      price=price)
                    
                    # Log missed opportunity
                    try:
                        from .pnl_analytics import get_pnl_analytics
                        pnl_analytics = get_pnl_analytics()
                        pnl_analytics.log_missed_signal(
                            symbol=symbol,
                            action="BUY",
                            signal_price=price,
                            reason=f"Candle confirmation failed: {reason}",
                            alert_data=alert
                        )
                    except:
                        pass
                    
                    return {
                        "status": "rejected",
                        "reason": f"Candle confirmation failed: {reason}",
                        "symbol": symbol,
                        "confidence": confidence
                    }
                
                log_event("ENTRY_CONFIRMED_CANDLE", f"BUY signal confirmed by candles",
                         symbol=symbol, confidence=confidence)
                log_trade_execution("ENTRY_CONFIRMED_CANDLE", symbol, "BUY",
                                  confidence=confidence,
                                  price=price)
            else:
                log_event("CANDLE_CONFIRMATION_SKIPPED", f"No token mapping for {symbol}",
                         symbol=symbol)
        
        except Exception as e:
            log_event("CANDLE_CONFIRMATION_ERROR", f"Candle confirmation error: {str(e)}",
                     symbol=symbol, error=str(e))
            # Continue with order (fail-open)
        
        # ===== DYNAMIC STOP LOSS CALCULATION (NEW) =====
        dynamic_sl_price = None
        try:
            from .candle_integration import DynamicStopLossEngine
            
            sl_engine = DynamicStopLossEngine(trading_state.broker)
            
            if token:
                sl_price_dyn, sl_reason = sl_engine.calculate_stop_loss(
                    symbol=symbol,
                    exchange="NSE",
                    token=token,
                    entry_price=price,
                    multiplier=2.0  # 2x ATR (medium volatility)
                )
                
                dynamic_sl_price = sl_price_dyn
                log_event("DYNAMIC_SL_CALCULATED", f"Dynamic SL calculated for {symbol}",
                         symbol=symbol, sl_price=sl_price_dyn, reason=sl_reason)
                log_trade_execution("DYNAMIC_SL_CALCULATED", symbol, "BUY",
                                  sl_price=sl_price_dyn, reason=sl_reason)
        
        except Exception as e:
            log_event("DYNAMIC_SL_ERROR", f"Dynamic SL calculation error: {str(e)}",
                     symbol=symbol, error=str(e))
            # Will use hardcoded SL below
        
        # Place BUY order
        log_trade_execution("ORDER_PLACING", symbol, "BUY",
                          quantity=quantity,
                          price=price,
                          order_type="MARKET")
        
        # Pre-allocate capital before order placement (need to track for rollback)
        trading_state.allocate_capital(symbol, required_capital)
        
        order = trading_state.broker.place_order_safe(
            symbol=symbol,
            action="BUY",
            quantity=quantity,
            price=0  # Market order
        )
        
        if not order:
            # 🔧 FIX GAP-001: Release capital if order placement fails
            
            # 🔍 RETRIEVE ACTUAL ERROR REASON & CLASSIFY IT
            actual_reason = "Broker API call failed"
            if (hasattr(trading_state.broker, 'last_order_error') and 
                trading_state.broker.last_order_error):
                actual_reason = trading_state.broker.last_order_error
            elif (hasattr(trading_state.broker, 'last_order_error_symbol') and
                  trading_state.broker.last_order_error_symbol == symbol):
                actual_reason = f"Order rejected for {symbol}"
            
            # 🔍 CLASSIFY REJECTION TYPE (scrutiny, blacklist, observation, etc.)
            rejection_type, detailed_reason = classify_rejection_reason(actual_reason, symbol)
            
            # 🔴 LOG SPECIAL ALERTS FOR SCRUTINY/BLACKLIST/OBSERVATION
            if rejection_type in ["SCRUTINY", "BLACKLIST", "OBSERVATION", "TRADING_HALT"]:
                log_event("SYMBOL_RESTRICTION", 
                         f"Symbol {symbol} has trading restriction",
                         rejection_type=rejection_type,
                         reason=detailed_reason,
                         error_message=actual_reason)
            
            trading_state.release_capital(symbol, required_capital)
            log_trade_execution("ORDER_PLACEMENT_FAILED", symbol, "BUY",
                              reason=actual_reason,
                              detailed_reason=detailed_reason,
                              rejection_type=rejection_type,
                              quantity=quantity,
                              capital_released=required_capital)
            return {
                "status": "failed",
                "reason": "Failed to place BUY order",
                "symbol": symbol
            }
        
        log_trade_execution("ORDER_PLACED", symbol, "BUY",
                          order_id=order.order_id,
                          quantity=quantity,
                          price=price)
        
        # 🚨 CRITICAL: Wait for BUY order confirmation before proceeding
        log_event("ORDER_CONFIRM", f"Waiting for BUY order confirmation for {symbol}", order_id=order.order_id)
        log_trade_execution("ORDER_CONFIRMING", symbol, "BUY",
                          order_id=order.order_id,
                          timeout=30)
        
        confirmation_timeout = 30  # Reduced from 60s for better burst handling
        is_confirmed = trading_state.broker.wait_for_order_confirmation(order, timeout=confirmation_timeout)
        
        if not is_confirmed:
            # 🔧 FIX GAP-002: Release capital if confirmation times out
            trading_state.release_capital(symbol, required_capital)
            log_event("ORDER_TIMEOUT", f"BUY order confirmation timeout for {symbol}", order_id=order.order_id)
            log_trade_execution("ORDER_TIMEOUT", symbol, "BUY",
                              order_id=order.order_id,
                              timeout_seconds=confirmation_timeout,
                              final_status=order.status,
                              capital_released=required_capital)
            return {
                "status": "failed", 
                "reason": f"BUY order confirmation timeout ({confirmation_timeout}s)",
                "symbol": symbol,
                "order_id": order.order_id
            }
        
        # Check if order was filled or rejected
        if order.status == OrderStatus.REJECTED:
            # 🔧 FIX GAP-001: Release capital if order is rejected
            trading_state.release_capital(symbol, required_capital)
            log_event("ORDER_REJECTED", f"BUY order rejected for {symbol}", 
                     order_id=order.order_id, reason=order.rejection_reason)
            log_trade_execution("ORDER_REJECTED", symbol, "BUY",
                              order_id=order.order_id,
                              rejection_reason=order.rejection_reason,
                              capital_released=required_capital)
            return {
                "status": "failed",
                "reason": f"BUY order rejected: {order.rejection_reason}",
                "symbol": symbol,
                "order_id": order.order_id
            }
        
        if order.status != OrderStatus.FILLED:
            # 🔧 FIX GAP-001: Release capital if not filled
            trading_state.release_capital(symbol, required_capital)
            log_event("ORDER_NOT_FILLED", f"BUY order not filled for {symbol}", 
                     order_id=order.order_id, status=order.status)
            log_trade_execution("ORDER_NOT_FILLED", symbol, "BUY",
                              order_id=order.order_id,
                              status=order.status,
                              capital_released=required_capital)
            return {
                "status": "failed",
                "reason": f"BUY order not filled (status: {order.status})",
                "symbol": symbol,
                "order_id": order.order_id
            }
        
        # 🚨 BUY ORDER IS NOW CONFIRMED AND FILLED - Safe to proceed
        log_event("ORDER_FILLED", f"BUY order confirmed and filled for {symbol}", order_id=order.order_id)
        log_trade_execution("ORDER_CONFIRMED", symbol, "BUY",
                          order_id=order.order_id,
                          filled_price=order.average_price,  # 🔧 FIX: Use actual filled price
                          quantity=quantity)
        
        # Capital was already allocated before order placement (now safe since order is filled)
        
        # 🔧 CRITICAL FIX #6: Use broker's actual filled price (averageprice) not alert price
        # This prevents SL from being set too high when order fills below alert price
        filled_price = order.average_price  # Get actual filled price from broker
        log_event("ENTRY_PRICE_SOURCE", f"Entry price determined for {symbol}",
                 alert_price=price,
                 filled_price=filled_price,
                 difference=price - filled_price)
        
        # Calculate stop loss price and ROUND to valid NSE tick size (multiple of ₹0.05)
        # 🔴 FIX: Must round SL price to avoid tick size errors from broker
        # AngelOne STOPLOSS orders require 0.10 (10 paise) tick size, NOT 0.05!
        # Use the FILLED price, not alert price, for SL calculation
        
        # ===== USE DYNAMIC SL IF CALCULATED, OTHERWISE FALLBACK TO FIXED PERCENTAGE =====
        if dynamic_sl_price is not None:
            # Use calculated dynamic SL
            sl_price_raw = dynamic_sl_price
            log_event("SL_SOURCE", f"Using dynamic ATR-based stop loss",
                     symbol=symbol, sl_price=sl_price_raw)
        else:
            # Fallback to fixed percentage
            sl_price_raw = filled_price * (1 - TradingConfig.DEFAULT_SL_PERCENTAGE / 100)
            log_event("SL_SOURCE", f"Using fixed {TradingConfig.DEFAULT_SL_PERCENTAGE}% stop loss",
                     symbol=symbol, sl_price=sl_price_raw)
        
        # Round to nearest 0.05 rupees (5 paise intervals - NSE tick size)
        # Use NEAREST rounding (not floor) to minimize distance from target SL
        # Convert to paise, round to nearest 5, convert back
        sl_paise = round(sl_price_raw * 100)  # Convert to paise (with rounding)
        sl_paise_rounded = round(sl_paise / 5) * 5  # Round to nearest 5 paise (0.05 tick)
        sl_price = sl_paise_rounded / 100.0  # Convert back to rupees
        
        log_event("SL_PRICE_ROUNDING", f"Rounded SL price for {symbol}",
                 raw_sl_price=sl_price_raw,
                 rounded_sl_price=sl_price,
                 adjustment_paise=(sl_paise - sl_paise_rounded),
                 base_filled_price=filled_price)
        
        # Log executed trade for PNL tracking
        try:
            from .pnl_analytics import get_pnl_analytics
            pnl_analytics = get_pnl_analytics()
            trade_id = pnl_analytics.log_executed_trade(
                symbol=symbol,
                action="BUY",
                entry_price=price,
                quantity=quantity,
                alert_data=alert
            )
            log_event("PNL_TRACKING", f"Logged BUY trade for {symbol} (trade_id: {trade_id})")
        except Exception as e:
            log_event("ERROR", f"Failed to log PNL trade for {symbol}: {e}")
            trade_id = 0
        
        # 🎯 LIVE_TRADE MARKER - ML Training Marker #1
        # This explicit marker helps ML identify: Trade entered, capital deployed, risk set
        log_event("LIVE_TRADE", f"✅ LIVE TRADE ENTERED | {symbol} | Entry: ₹{filled_price:.2f} | Qty: {quantity} | SL: ₹{sl_price:.2f}",
                 symbol=symbol,
                 trade_type="LIVE",
                 entry_price=filled_price,
                 quantity=quantity,
                 sl_price=sl_price,
                 trade_id=trade_id,
                 order_id=order.order_id,
                 capital_used=required_capital,
                 entry_time=datetime.now().isoformat())
        
        # Store position data with CONFIRMED status
        position_data = {
            "symbol": symbol,
            "action": "BUY",
            "quantity": quantity,
            "entry_price": filled_price,  # 🔧 FIX: Use actual filled price, not alert price
            "capital_used": required_capital,
            "sl_price": sl_price,
            "order_id": order.order_id,
            "trade_id": trade_id,  # Add trade_id for PNL tracking
            "status": "OPEN",  # Status is OPEN since BUY is confirmed and filled
            "created_at": datetime.now().isoformat(),
            "confirmed_at": datetime.now().isoformat(),
            "charges": charges,
            "producttype": "INTRADAY"  # 🔧 FIX: Track product type used for BUY order for SL matching
        }
        
        trading_state.active_positions[symbol] = position_data
        
        log_trade_execution("POSITION_ADDED", symbol, "BUY",
                          entry_price=filled_price,
                          quantity=quantity,
                          capital_used=required_capital,
                          sl_price=sl_price,
                          trade_id=trade_id)
        
        # Add position to monitor (MANDATORY - no trading without monitor)
        if trading_state.monitor is None or not hasattr(trading_state.monitor, 'add_position'):
            # 🔧 CRITICAL FIX #2: Monitor is MANDATORY - fail trade if unavailable
            trading_state.release_capital(symbol, required_capital)
            del trading_state.active_positions[symbol]
            log_event("MONITOR_CRITICAL_FAILURE", f"Monitor system unavailable - cannot proceed with position {symbol}", critical=True)
            log_trade_execution("MONITOR_UNAVAILABLE_ROLLBACK", symbol, "BUY",
                              reason="Monitor required but unavailable",
                              capital_released=required_capital,
                              position_removed=True)
            return {
                "status": "failed",
                "reason": "Monitor system unavailable - cannot proceed with position",
                "symbol": symbol,
                "capital_released": required_capital,
                "impact": "This should not happen - check monitor initialization"
            }
        
        try:
            trading_state.monitor.add_position(position_data)
            log_event("MONITOR_SUCCESS", f"Position {symbol} added to monitor")
            log_trade_execution("MONITOR_ADDED", symbol, "BUY",
                              monitor_status="SUCCESS")
        except Exception as e:
            # 🔧 FIX GAP-003: Rollback capital if monitor fails
            trading_state.release_capital(symbol, required_capital)
            # Remove position from in-memory tracking since we can't monitor it
            del trading_state.active_positions[symbol]
            log_event("MONITOR_CRITICAL_ERROR", f"Failed to add position {symbol} to monitor - rolling back trade", error=str(e))
            log_trade_execution("MONITOR_FAILED_ROLLBACK", symbol, "BUY",
                              error=str(e),
                              capital_released=required_capital,
                              position_removed=True)
            return {
                "status": "failed",
                "reason": f"Monitor unavailable - cannot track position {symbol}",
                "symbol": symbol,
                "error": str(e)
            }
        
        # Log trade
        log_trade(
            action="BUY",
            symbol=symbol,
            quantity=quantity,
            entry_price=price,
            capital_used=required_capital,
            sl_price=sl_price,
            status="PENDING"
        )
        
        # Track successful execution for analytics
        # 🔧 CRITICAL FIX: Wrap in try-except to ensure trading never breaks for analytics
        try:
            from .analytics.alert_integration import track_execution_success
            trade_results = {
                "capital_used": required_capital,
                "quantity": quantity,
                "order_id": order.order_id,
                "execution_price": price
            }
            track_execution_success(alert, trade_results)
            log_event("ANALYTICS_TRACKED", f"Successfully tracked execution for analytics", symbol=symbol)
        except Exception as analytics_error:
            # Log but never break trading for analytics failures
            log_event("ANALYTICS_ERROR", f"Analytics tracking failed but trade succeeded: {str(analytics_error)}",
                     symbol=symbol, error_type=type(analytics_error).__name__)
            # Continue execution - trading must complete
        
        log_trade_execution("EXECUTION_COMPLETE", symbol, "BUY",
                          order_id=order.order_id,
                          entry_price=price,
                          quantity=quantity,
                          capital_used=required_capital,
                          sl_price=sl_price,
                          trade_id=trade_id,
                          status="SUCCESS")
        
        return {
            "status": "success",
            "message": f"BUY order placed for {symbol}",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "order_id": order.order_id,
            "sl_price": sl_price,
            "capital_used": required_capital
        }
        
    except Exception as e:
        log_event("ERROR", f"Error handling BUY alert: {str(e)}", symbol=symbol)
        log_trade_execution("EXECUTION_EXCEPTION", symbol, "BUY",
                          error=str(e),
                          error_type=type(e).__name__)
        return {
            "status": "error",
            "reason": f"Exception: {str(e)}",
            "symbol": symbol
        }


async def handle_buy_alert_async(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Async wrapper for handle_buy_alert to integrate with AlertQueue.
    
    This runs handle_buy_alert in an executor to avoid blocking the async event loop.
    
    Args:
        alert: Processed alert data
        
    Returns:
        Response dictionary
    """
    import asyncio
    
    try:
        loop = asyncio.get_event_loop()
        # Run synchronous function in thread pool to avoid blocking
        result = await loop.run_in_executor(None, handle_buy_alert, alert)
        
        # Log alert to batch for hybrid learning
        with trading_state.alert_batch_lock:
            trading_state.alert_batch_log.append({
                'symbol': alert.get('symbol'),
                'action': 'BUY',
                'status': result.get('status'),
                'timestamp': time.time()
            })
        
        return result
        
    except Exception as e:
        log_error("ASYNC_ALERT_ERROR", f"Error in async alert handler", e,
                 context={"alert": alert})
        return {
            "status": "error",
            "reason": f"Async processing failed: {str(e)}",
            "symbol": alert.get('symbol', 'UNKNOWN')
        }


def handle_sell_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle SELL alert from TradingView with enhanced analytics validation
    
    Args:
        alert: Processed alert data
        
    Returns:
        Response dictionary
    """
    symbol = alert["symbol"]
    
    try:
        # Check if we have position in this symbol
        if symbol not in trading_state.active_positions:
            log_event("SELL_REJECTED_NO_POSITION", f"SELL rejected - no position in bot for {symbol}",
                     symbol=symbol)
            return {
                "status": "rejected",
                "reason": f"No position found for {symbol}",
                "symbol": symbol
            }
        
        position = trading_state.active_positions[symbol]
        
        # 🚨 CRITICAL: Double-check broker still has the position (prevent orphaned shorts)
        # If position was synced but has since been closed on broker, don't allow SELL
        try:
            broker_positions = trading_state.broker.smart_api.position()
            if broker_positions and broker_positions.get('data'):
                broker_pos_symbols = [p.get('tradingsymbol') for p in broker_positions.get('data', [])
                                     if int(p.get('netqty', 0)) > 0]  # Only count LONG positions
                if symbol not in broker_pos_symbols:
                    log_event("SELL_REJECTED_BROKER_MISMATCH", 
                             f"SELL rejected - position not found in broker for {symbol}",
                             symbol=symbol,
                             reason="Position exists in bot but not on broker (possible sync issue)")
                    return {
                        "status": "rejected",
                        "reason": f"Position no longer exists in broker holdings for {symbol}",
                        "symbol": symbol
                    }
        except Exception as broker_check_error:
            log_event("BROKER_CHECK_ERROR", f"Failed to verify position on broker: {str(broker_check_error)}")
            # Continue anyway - don't block SELL due to API error
        
        # 🚨 CRITICAL: Check position status - must be OPEN (not PENDING)
        if position.get("status") != "OPEN":
            return {
                "status": "rejected", 
                "reason": f"Position not ready for SELL - status: {position.get('status')} for {symbol}",
                "symbol": symbol
            }
        
        # Enhanced Analytics Validation for SELL signal
        analytics_validation = validate_sell_signal_with_analytics(alert, position)
        if not analytics_validation["approved"]:
            log_event("ANALYTICS_FILTER", f"Sell signal filtered out for {symbol}: {analytics_validation['reason']}")
            
            # Track filtered signal for analytics
            try:
                from .analytics.alert_integration import track_execution_failure
                track_execution_failure(alert, f"Analytics filter: {analytics_validation['reason']}")
            except Exception:
                pass  # Never break trading for analytics
            
            return {
                "status": "rejected",
                "reason": f"Analytics validation failed: {analytics_validation['reason']}",
                "symbol": symbol,
                "analytics": analytics_validation.get("details", {})
            }
        
        # Log successful analytics validation
        log_event("ANALYTICS_APPROVED", f"Sell signal approved for {symbol}", 
                 tv_score=alert.get('score', 'N/A'),
                 analytics_signal=analytics_validation.get('signal', 'N/A'))
        
        # 🚨 CRITICAL: Check if position already has an exit order (prevent duplicate sells)
        if position.get("exit_order_id"):
            log_event("DUPLICATE_SELL_ALERT", 
                     f"SELL signal received but position {symbol} already has exit order",
                     symbol=symbol,
                     existing_exit_order_id=position.get("exit_order_id"),
                     position_status=position.get("status"))
            return {
                "status": "rejected",
                "reason": f"SELL order already placed for {symbol} (order_id: {position.get('exit_order_id')})",
                "symbol": symbol,
                "existing_order_id": position.get("exit_order_id")
            }
        
        # 🚨 CRITICAL: Enhanced pending order check with race condition prevention
        can_place, reason = trading_state.broker.can_place_order(symbol, "SELL")
        if not can_place:
            return {
                "status": "rejected",
                "reason": f"Cannot place SELL order: {reason}",
                "symbol": symbol
            }
        
        # Double-check for any pending BUY orders (extra safety)
        if trading_state.broker.has_pending_buy_order(symbol):
            return {
                "status": "rejected",
                "reason": f"CRITICAL: BUY order still pending for {symbol} - SELL blocked to prevent race condition",
                "symbol": symbol
            }
        
        # Place SELL order
        order = trading_state.broker.place_order_safe(
            symbol=symbol,
            action="SELL",
            quantity=position["quantity"],
            price=0  # Market order
        )
        
        if not order:
            # 🔍 RETRIEVE ACTUAL ERROR REASON & CLASSIFY IT
            actual_reason = "Broker API call failed"
            if (hasattr(trading_state.broker, 'last_order_error') and 
                trading_state.broker.last_order_error):
                actual_reason = trading_state.broker.last_order_error
            elif (hasattr(trading_state.broker, 'last_order_error_symbol') and
                  trading_state.broker.last_order_error_symbol == symbol):
                actual_reason = f"Order rejected for {symbol}"
            
            # 🔍 CLASSIFY REJECTION TYPE (scrutiny, blacklist, observation, etc.)
            rejection_type, detailed_reason = classify_rejection_reason(actual_reason, symbol)
            
            # 🔴 LOG SPECIAL ALERTS FOR SCRUTINY/BLACKLIST/OBSERVATION
            if rejection_type in ["SCRUTINY", "BLACKLIST", "OBSERVATION", "TRADING_HALT"]:
                log_event("SYMBOL_RESTRICTION", 
                         f"Symbol {symbol} has trading restriction",
                         rejection_type=rejection_type,
                         reason=detailed_reason,
                         error_message=actual_reason)
            
            log_trade_execution("ORDER_PLACEMENT_FAILED", symbol, "SELL",
                              reason=actual_reason,
                              detailed_reason=detailed_reason,
                              rejection_type=rejection_type,
                              quantity=position["quantity"],
                              capital_involved=position.get("required_capital", 0))
            
            return {
                "status": "failed",
                "reason": actual_reason,
                "detailed_reason": detailed_reason,
                "rejection_type": rejection_type,
                "symbol": symbol
            }
        
        # Update position status
        position["exit_order_id"] = order.order_id
        position["exit_requested_at"] = datetime.now().isoformat()
        position["status"] = "EXITING"
        
        # 🎯 SELL MARKER - ML Training Marker #4 (From Manual Signal)
        # This explicit marker captures exit via TradingView SELL signal
        entry_price = position.get("entry_price", 0)
        current_price = alert.get("price", 0)
        quantity = position.get("quantity", 0)
        if entry_price > 0 and quantity > 0:
            pnl = (current_price - entry_price) * quantity
            pnl_percent = ((current_price - entry_price) / entry_price) * 100
            hold_duration = (datetime.now() - datetime.fromisoformat(position.get("created_at", datetime.now().isoformat()))).total_seconds()
            
            log_event("SELL", 
                     f"📤 SELL SIGNAL EXECUTED | {symbol} | Entry: ₹{entry_price:.2f} | Exit: ₹{current_price:.2f} | PnL: ₹{pnl:.2f} ({pnl_percent:.2f}%)",
                     symbol=symbol,
                     exit_reason="SELL_SIGNAL",
                     entry_price=entry_price,
                     exit_price=current_price,
                     quantity=quantity,
                     pnl=round(pnl, 2),
                     pnl_percent=round(pnl_percent, 2),
                     charges=position.get("charges", 0),
                     trade_id=position.get("trade_id", 0),
                     duration_seconds=hold_duration,
                     duration_minutes=round(hold_duration / 60, 1))
        
        log_event("EXIT", f"Manual SELL order placed for {symbol}", order_id=order.order_id)
        
        return {
            "status": "success",
            "message": f"SELL order placed for {symbol}",
            "symbol": symbol,
            "quantity": position["quantity"],
            "order_id": order.order_id
        }
        
    except Exception as e:
        log_event("ERROR", f"Error handling SELL alert: {str(e)}", symbol=symbol)
        return {
            "status": "error",
            "reason": f"Exception: {str(e)}",
            "symbol": symbol
        }


def validate_sell_signal_with_analytics(alert: Dict[str, Any], position: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate TradingView SELL signal using AngelOne enhanced analytics and position data
    
    Args:
        alert: TradingView alert data
        position: Current position data
        
    Returns:
        Dictionary with validation result
    """
    symbol = alert["symbol"]
    tv_score = alert.get("score", 0)
    entry_price = position.get("entry_price", 0)
    current_price = alert.get("price", 0)
    
    try:
        # Skip analytics validation in paper trading or if broker not available
        if DevConfig.is_paper_trading() or not trading_state.broker:
            return {
                "approved": True,
                "reason": "Paper trading mode - analytics validation skipped",
                "signal": "SKIP",
                "details": {"mode": "paper_trading"}
            }
        
        # Calculate current profit/loss
        if entry_price > 0 and current_price > 0:
            profit_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            profit_pct = 0
        
        # CRITICAL FIX: Skip analytics validation if rate limiter is exhausted
        # This prevents order rejection due to burst of validation API calls
        try:
            rate_limiter = trading_state.broker.rate_limiter
            if hasattr(rate_limiter, 'get_statistics'):
                stats = rate_limiter.get_statistics()
                second_bucket = stats.get('second_bucket', {})
                available_tokens = second_bucket.get('tokens', 1)
                
                # If <3 tokens available, skip analytics to reserve tokens for order placement
                if available_tokens < 3:
                    log_event("ANALYTICS_SKIPPED_RATE_LIMIT_SELL", 
                             f"Skipping analytics for SELL {symbol} - rate limiter exhausted ({available_tokens:.1f} tokens available)",
                             symbol=symbol, available_tokens=round(available_tokens, 1), profit_pct=round(profit_pct, 2))
                    return {
                        "approved": True,
                        "reason": "Rate limit protection - analytics validation skipped to preserve tokens for order placement",
                        "signal": "RATE_LIMIT_PROTECTED",
                        "details": {"tokens_available": round(available_tokens, 1), "profit_pct": round(profit_pct, 2)}
                    }
        except Exception as e:
            # If rate limiter check fails, continue normally
            log_event("RATE_LIMIT_CHECK_ERROR", f"Error checking rate limiter for SELL: {e}")
        
        # Get enhanced analytics
        log_event("ANALYTICS", f"Fetching enhanced analytics for SELL signal {symbol}")
        analytics = trading_state.broker.get_enhanced_analytics(
            symbol=symbol,
            interval="FIVE_MINUTE",
            days_back=2  # Shorter window for sell decisions
        )
        
        if analytics.get('error'):
            log_event("ANALYTICS_ERROR", f"Failed to get analytics for {symbol}: {analytics['error']}")
            # For SELL signals, be more permissive if analytics fail
            return {
                "approved": True,
                "reason": f"Analytics unavailable ({analytics['error']}) - using TradingView signal and P&L",
                "signal": "FALLBACK",
                "details": {"error": analytics['error'], "profit_pct": profit_pct}
            }
        
        # Extract technical indicators and signals
        technical_indicators = analytics.get('technical_indicators', {})  # FIXED: Renamed to avoid shadowing
        signals = analytics.get('signals', {})
        
        rsi = technical_indicators.get('rsi', 50)
        bb_position = technical_indicators.get('bb_position', 0.5)
        trend_signal = signals.get('trend_signal', 'NEUTRAL')
        
        # SELL signal validation logic
        validation_reasons = []
        
        # 1. Profit Protection - don't sell at significant loss unless strong technical reason
        if profit_pct < -3 and tv_score < 80:
            # Only allow selling at >3% loss if very strong signal
            validation_reasons.append(f"Large loss ({profit_pct:.1f}%) with weak TV signal ({tv_score})")
        
        # 2. RSI Check - confirm overbought for sells or oversold for loss-cutting
        if profit_pct > 1 and rsi < 60:  # Profit-taking: prefer higher RSI
            validation_reasons.append(f"Taking profit but RSI not elevated ({rsi:.1f})")
        
        # 3. Trend Check - be cautious selling in strong uptrends unless profit-taking
        if trend_signal == "BULLISH" and profit_pct < 2 and bb_position < 0.7:
            validation_reasons.append("Selling in bullish trend without sufficient profit")
        
        # 4. TradingView Signal Quality for SELL
        if tv_score < 60:  # Lower threshold for sells
            validation_reasons.append(f"Weak TradingView sell signal ({tv_score})")
        
        # SELL approval logic (more permissive than BUY)
        if validation_reasons:
            # Allow overrides for profit-taking or loss-cutting
            override_reasons = []
            
            if profit_pct > 3:  # Good profit - allow even with weak signals
                override_reasons.append(f"Good profit ({profit_pct:.1f}%)")
            
            if profit_pct < -2 and rsi > 70:  # Loss cutting with overbought RSI
                override_reasons.append(f"Loss cutting with overbought RSI ({rsi:.1f})")
            
            if bb_position > 0.9:  # Very high in Bollinger Bands
                override_reasons.append(f"Price at upper Bollinger Band ({bb_position:.2f})")
            
            if override_reasons:
                return {
                    "approved": True,
                    "reason": f"Override: {'; '.join(override_reasons)}",
                    "signal": "OVERRIDE_APPROVE",
                    "details": {
                        "profit_pct": profit_pct,
                        "rsi": rsi,
                        "bb_position": bb_position,
                        "trend_signal": trend_signal,
                        "original_concerns": validation_reasons
                    }
                }
            else:
                return {
                    "approved": False,
                    "reason": "; ".join(validation_reasons),
                    "signal": "REJECT",
                    "details": {
                        "profit_pct": profit_pct,
                        "rsi": rsi,
                        "bb_position": bb_position,
                        "trend_signal": trend_signal
                    }
                }
        else:
            # All checks passed
            approval_factors = [f"P&L: {profit_pct:.1f}%"]
            if rsi > 65:
                approval_factors.append(f"RSI elevated ({rsi:.1f})")
            if bb_position > 0.7:
                approval_factors.append(f"High BB position ({bb_position:.2f})")
            if tv_score >= 70:
                approval_factors.append(f"Strong TV signal ({tv_score})")
            
            return {
                "approved": True,
                "reason": "; ".join(approval_factors),
                "signal": "APPROVE",
                "details": {
                    "profit_pct": profit_pct,
                    "rsi": rsi,
                    "bb_position": bb_position,
                    "trend_signal": trend_signal,
                    "tv_score": tv_score
                }
            }
        
    except Exception as e:
        log_event("ANALYTICS_ERROR", f"Exception in SELL analytics validation for {symbol}: {str(e)}")
        # Don't block SELL orders if analytics validation fails
        return {
            "approved": True,
            "reason": f"Analytics validation error ({str(e)}) - using TradingView signal",
            "signal": "ERROR_FALLBACK",
            "details": {"exception": str(e), "profit_pct": profit_pct if 'profit_pct' in locals() else 0}
        }


# =============================================================================
# Flask Routes
# =============================================================================

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Main webhook endpoint for TradingView alerts with comprehensive autonomous logging
    Accepts both application/json and text/plain (TradingView sends text/plain with JSON body)
    """
    start_time = datetime.now()
    alert_data = None
    processing_status = "UNKNOWN"
    
    try:
        # Log every webhook attempt
        log_event("WEBHOOK_RECEIVED", f"Incoming webhook from {request.remote_addr}")
        
        # Get JSON data - accept both application/json and text/plain
        if request.is_json:
            alert_data = request.get_json()
        elif request.content_type and 'text/plain' in request.content_type:
            # TradingView sends text/plain with JSON body - parse it
            try:
                import json
                alert_data = json.loads(request.data.decode('utf-8'))
                log_event("WEBHOOK_TEXTPLAIN_PARSED", "Parsed JSON from text/plain Content-Type")
            except Exception as e:
                error_msg = f"Failed to parse JSON from text/plain: {str(e)}"
                log_webhook("INVALID_FORMAT", {"content_type": request.content_type}, 
                           error_details=error_msg)
                return jsonify({"error": error_msg}), 400
        else:
            error_msg = f"Unsupported Content-Type: {request.content_type}"
            log_webhook("INVALID_FORMAT", {"content_type": request.content_type}, 
                       error_details=error_msg)
            return jsonify({"error": error_msg}), 400
        
        if not alert_data:
            error_msg = "Empty request body"
            log_webhook("EMPTY_BODY", {}, error_details=error_msg)
            return jsonify({"error": error_msg}), 400

        # Log raw incoming data for debugging
        log_webhook("RAW_RECEIVED", alert_data)
        
        # Validate alert (supports new TradingView 'Alerts' wrapper)
        is_valid, error_msg, processed_alert = validate_alert(alert_data)
        
        # Extract alert details for logging (works for both valid and invalid)
        symbol = "UNKNOWN"
        action = "UNKNOWN"
        price = 0.0
        confidence = None
        score = None
        
        if processed_alert:
            symbol = processed_alert.get("symbol", "UNKNOWN")
            action = processed_alert.get("action", "UNKNOWN")
            price = float(processed_alert.get("price", 0))
            confidence = processed_alert.get("confidence") or processed_alert.get("quality", {}).get("confidence")
            score = processed_alert.get("score") or processed_alert.get("quality", {}).get("score")
        
        if not is_valid:
            processing_status = "VALIDATION_FAILED"
            log_webhook(processing_status, alert_data, error_details=error_msg)
            log_event("WEBHOOK_ERROR", f"Invalid alert: {error_msg}", raw_data=str(alert_data))
            
            # Log REJECTED alert to all_alerts.csv
            try:
                log_alert(
                    symbol=symbol,
                    action=action,
                    price=price,
                    source="TradingView",
                    validation_status="REJECTED",
                    rejection_reason=error_msg,
                    confidence=confidence,
                    score=score
                )
            except Exception as e:
                log_event("ERROR", f"Failed to log rejected alert: {e}")
            
            return jsonify({"error": error_msg}), 400

        processing_status = "VALIDATED"
        log_webhook(processing_status, processed_alert)
        
        # Log ACCEPTED alert to all_alerts.csv
        try:
            log_alert(
                symbol=symbol,
                action=action,
                price=price,
                source="TradingView",
                validation_status="ACCEPTED",
                confidence=confidence,
                score=score
            )
            
            # Track alert for analytics (Phase 1 enhancement)
            from .analytics.alert_integration import track_webhook_alert
            track_webhook_alert(processed_alert)
            log_analytics("ALERT_TRACKED", {"symbol": processed_alert.get("symbol")})
            
        except Exception as e:
            # Non-fatal logging failure
            log_event("WEBHOOK", "Failed to log incoming alert")
            log_error("ANALYTICS_TRACKING", "Failed to track webhook alert", e, 
                     context={"alert": processed_alert})
        
        # Process based on action with detailed logging
        action = processed_alert["action"]
        symbol = processed_alert["symbol"]
        
        log_event("WEBHOOK_PROCESSING", f"Processing {action} signal for {symbol}")
        
        if action == "BUY":
            processing_status = "QUEUEING_BUY"
            log_webhook(processing_status, processed_alert)
            
            # ===== USE ALERT QUEUE FOR BUY ALERTS =====
            # This prevents rate limit timeouts when multiple alerts arrive rapidly
            if trading_state.alert_queue:
                response = trading_state.alert_queue.enqueue_alert_sync(processed_alert)
                log_event("ALERT_QUEUED", f"BUY alert for {symbol} queued",
                         queue_status=response)
            else:
                # Fallback to direct processing if queue unavailable
                log_event("ALERT_QUEUE_UNAVAILABLE", f"Processing {symbol} directly (queue unavailable)")
                response = handle_buy_alert(processed_alert)
        elif action == "SELL":
            processing_status = "PROCESSING_SELL"
            log_webhook(processing_status, processed_alert)
            response = handle_sell_alert(processed_alert)
        elif action == "EXIT":
            # EXIT is same as SELL for now
            processing_status = "PROCESSING_EXIT"
            log_webhook(processing_status, processed_alert)
            response = handle_sell_alert(processed_alert)
        else:
            processing_status = "UNKNOWN_ACTION"
            error_msg = f"Unknown action: {action}"
            log_webhook(processing_status, processed_alert, error_details=error_msg)
            return jsonify({"error": error_msg}), 400
        
        # Calculate processing time
        processing_time = (datetime.now() - start_time).total_seconds() * 1000
        
        # Log final response with comprehensive details
        processing_status = "COMPLETED"
        log_webhook(processing_status, processed_alert, 
                   processing_time_ms=processing_time, response_code=200)
        
        log_event("WEBHOOK_RESPONSE", f"Alert processed successfully", 
                 action=action, symbol=symbol, 
                 status=response.get("status", "unknown"),
                 processing_time_ms=processing_time)
        
        return jsonify(response), 200
        
    except Exception as e:
        # Calculate processing time even for errors
        processing_time = (datetime.now() - start_time).total_seconds() * 1000
        
        # Comprehensive error logging
        log_error("WEBHOOK_EXCEPTION", f"Webhook processing failed", e,
                 context={
                     "alert_data": alert_data,
                     "processing_status": processing_status,
                     "processing_time_ms": processing_time,
                     "request_ip": request.remote_addr
                 },
                 recovery_action="Returning 500 error to client")
        
        if alert_data:
            log_webhook("ERROR", alert_data, processing_time_ms=processing_time, 
                       response_code=500, error_details=str(e))
        
        log_event("ERROR", f"Webhook exception: {str(e)}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


# ===== HYBRID LEARNING ENDPOINTS =====

@app.route('/webhook/batch', methods=['POST'])
def webhook_batch():
    """
    Batch alert processing for hybrid learning system.
    
    Accepts:
    - Single alert (added to queue)
    - Multiple alerts in 'alerts' array
    
    Can be triggered periodically to process accumulated alerts and rank them.
    """
    try:
        alert_data = request.get_json()
        if not alert_data:
            return jsonify({"error": "Empty request body"}), 400
        
        # Support both single alert and multiple
        alerts = alert_data.get('alerts', [])
        if not alerts:
            # Single alert
            alerts = [alert_data]
        
        log_event("WEBHOOK_BATCH", f"Received batch of {len(alerts)} alerts")
        
        # Add to queue
        with trading_state.alert_queue_lock:
            for alert in alerts:
                # Validate alert first
                is_valid, error_msg, processed_alert = validate_alert(alert)
                if is_valid and processed_alert:
                    trading_state.alert_queue.append(processed_alert)
                    log_event("ALERT_QUEUED", f"Alert {processed_alert.get('symbol')} added to queue")
                else:
                    log_event("ALERT_VALIDATION_FAILED", f"Alert rejected: {error_msg}")
        
        return jsonify({
            "status": "success",
            "alerts_queued": len(trading_state.alert_queue),
            "message": f"Added {len(alerts)} alert(s) to processing queue"
        }), 200
    
    except Exception as e:
        log_error("WEBHOOK_BATCH_ERROR", "Batch webhook processing failed", e)
        return jsonify({"error": str(e)}), 500


@app.route('/learning/process-queue', methods=['POST'])
def process_learning_queue():
    """
    Process accumulated alerts using hybrid learning system.
    
    This endpoint:
    1. Takes all queued alerts
    2. Ranks them using hybrid learning engine
    3. Splits into real trades (top 10) and paper trades (rest)
    4. Executes the trades
    5. Returns execution summary
    
    Typically called:
    - Manually when you want to process alerts
    - Periodically (e.g., every 30 minutes)
    - At strategic times (e.g., 10:30 AM for first batch)
    """
    try:
        if not HYBRID_LEARNING_AVAILABLE:
            return jsonify({"error": "Hybrid learning system not available"}), 503
        
        # Get all queued alerts
        with trading_state.alert_queue_lock:
            if not trading_state.alert_queue:
                return jsonify({
                    "status": "no_alerts",
                    "message": "No alerts in queue to process"
                }), 200
            
            alerts_to_process = trading_state.alert_queue.copy()
            trading_state.alert_queue = []
        
        log_event("LEARNING_QUEUE_PROCESS", f"Processing {len(alerts_to_process)} alerts")
        
        # Use hybrid learning to rank and select
        try:
            selection = process_webhook_alerts(alerts_to_process, real_slots=10)
        except Exception as e:
            log_error("LEARNING_RANKING_ERROR", "Hybrid learning ranking failed", e)
            return jsonify({
                "error": f"Hybrid learning ranking failed: {str(e)}"
            }), 500
        
        real_trades = selection.get('real_trades', [])
        paper_trades = selection.get('paper_trades', [])
        
        log_event("LEARNING_SELECTION", 
                 f"Selected {len(real_trades)} real trades, {len(paper_trades)} paper trades",
                 real_count=len(real_trades),
                 paper_count=len(paper_trades))
        
        # Execute real trades
        executed_real = []
        for alert in real_trades:
            try:
                if alert.get('action') == 'BUY':
                    response = handle_buy_alert(alert)
                    executed_real.append({
                        'symbol': alert.get('symbol'),
                        'action': 'BUY',
                        'status': response.get('status'),
                        'score': alert.get('final_score', 0)
                    })
                else:
                    # SELL/EXIT
                    response = handle_sell_alert(alert)
                    executed_real.append({
                        'symbol': alert.get('symbol'),
                        'action': 'SELL',
                        'status': response.get('status'),
                        'score': alert.get('final_score', 0)
                    })
            except Exception as e:
                log_error("REAL_TRADE_EXECUTION", f"Failed to execute real trade for {alert.get('symbol')}", e)
        
        # Create paper trades (dummy trades for learning)
        executed_paper = []
        dummy_tracker = None
        try:
            dummy_tracker = get_dummy_tracker()
        except Exception:
            pass
        
        for alert in paper_trades:
            try:
                if alert.get('action') == 'BUY' and dummy_tracker:
                    dummy_id = dummy_tracker.create_dummy_entry(
                        alert=alert,
                        entry_price=alert.get('price', 0),
                        ml_score=alert.get('ml_score', 0),
                        rejection_reason="Paper trading for learning",
                        quantity=1
                    )
                    executed_paper.append({
                        'symbol': alert.get('symbol'),
                        'action': 'BUY',
                        'dummy_id': dummy_id,
                        'score': alert.get('final_score', 0)
                    })
                    
                    # 🎯 PAPER_TRADE MARKER - ML Training Marker #2
                    # This explicit marker helps ML learn from unlimited paper trades
                    entry_price = alert.get('price', 0)
                    # 🔴 FIX: Round SL price to valid STOPLOSS order tick size (multiple of ₹0.05)
                    # NSE requires 5 paise intervals for all orders
                    sl_price_raw = entry_price * (1 - TradingConfig.DEFAULT_SL_PERCENTAGE / 100)
                    sl_paise = round(sl_price_raw * 100)
                    sl_paise_rounded = round(sl_paise / 5) * 5  # Round to nearest 5 paise
                    sl_price = sl_paise_rounded / 100.0
                    
                    log_event("PAPER_TRADE", 
                             f"📚 PAPER TRADE ENTERED | {alert.get('symbol')} | Entry: ₹{entry_price:.2f} | SL: ₹{sl_price:.2f} | ML Score: {alert.get('ml_score', 0):.1f}%",
                             symbol=alert.get('symbol'),
                             trade_type="PAPER",
                             entry_price=entry_price,
                             quantity=1,
                             sl_price=sl_price,
                             dummy_id=dummy_id,
                             ml_score=alert.get('ml_score', 0),
                             entry_time=datetime.now().isoformat())
                    
                    log_event("PAPER_TRADE_CREATED", 
                             f"Created paper trade {dummy_id} for {alert.get('symbol')}")
            except Exception as e:
                log_error("PAPER_TRADE_CREATION", f"Failed to create paper trade for {alert.get('symbol')}", e)
        
        return jsonify({
            "status": "success",
            "real_trades_executed": len(executed_real),
            "paper_trades_created": len(executed_paper),
            "real_trades": executed_real,
            "paper_trades": executed_paper,
            "learning_available": HYBRID_LEARNING_AVAILABLE
        }), 200
    
    except Exception as e:
        log_error("LEARNING_QUEUE_ERROR", "Failed to process learning queue", e)
        return jsonify({"error": str(e)}), 500


@app.route('/learning/status', methods=['GET'])
def learning_status():
    """Get current learning engine status and statistics"""
    try:
        if not HYBRID_LEARNING_AVAILABLE:
            return jsonify({"error": "Hybrid learning system not available"}), 503
        
        status = get_integration_status()
        
        # Add queue info
        with trading_state.alert_queue_lock:
            queue_size = len(trading_state.alert_queue)
        
        return jsonify({
            "status": "success",
            "learning_engine": status,
            "alert_queue_size": queue_size,
            "hybrid_learning_available": HYBRID_LEARNING_AVAILABLE
        }), 200
    
    except Exception as e:
        log_error("LEARNING_STATUS_ERROR", "Failed to get learning status", e)
        return jsonify({"error": str(e)}), 500




@app.route('/square-off', methods=['POST'])
def square_off_all_positions():
    """
    Square off all open positions at 3:12 PM.
    
    This endpoint is called by EOD scheduler to:
    1. Close all open positions before market close
    2. Avoid EOD market volatility/pressure
    3. Lock in profits and losses before 3:30 PM
    4. Free up capital for next day
    
    Called at: 3:12 PM (18 minutes before market close)
    Followed by: Learning update at 3:15 PM (15 minutes before close)
    
    Request:
        {
            "reason": "EOD_SQUAREOFF"  (optional)
        }
    
    Returns:
        {
            "status": "success",
            "message": "All positions squared off",
            "positions_closed": N,
            "total_pnl": X.XX,
            "timestamp": "..."
        }
    """
    try:
        log_event("SQUARE_OFF", "Attempting to square off all positions at 3:12 PM")
        
        if not trading_state.broker:
            return jsonify({"error": "Broker not initialized"}), 503
        
        reason = request.json.get('reason', 'MANUAL') if request.json else 'MANUAL'
        
        # Get all active positions from file (trading_state.active_positions may be empty)
        # Load from positions.json which is the source of truth
        import json
        from pathlib import Path
        positions_file = Path(__file__).parent.parent / "data" / "positions.json"
        positions_to_close = []
        
        if positions_file.exists():
            try:
                with open(positions_file, 'r') as f:
                    positions_data = json.load(f)
                    # Filter for OPEN positions only
                    positions_to_close = [pos for pos in positions_data.values() 
                                         if pos.get('status') == 'OPEN']
                log_event("SQUARE_OFF_LOADED_FROM_FILE", f"Loaded positions from file",
                         file=str(positions_file), count=len(positions_to_close))
            except Exception as e:
                log_error("SQUARE_OFF_LOAD_ERROR", f"Failed to load positions from file: {str(e)}")
                positions_to_close = list(trading_state.active_positions.values())  # Fallback
        else:
            # Fallback to active_positions if file doesn't exist
            positions_to_close = list(trading_state.active_positions.values())
        
        log_event("SQUARE_OFF_START", f"Starting square-off of {len(positions_to_close)} positions", 
                 reason=reason)
        
        if not positions_to_close:
            log_event("SQUARE_OFF_COMPLETE", "No positions to square off")
            return jsonify({
                "status": "success",
                "message": "No open positions to close",
                "positions_closed": 0,
                "total_pnl": 0,
                "timestamp": datetime.now().isoformat(),
                "reason": reason
            }), 200
        
        total_pnl = 0
        closed_count = 0
        logged_count = 0
        errors = []
        
        # Close each position
        for position in positions_to_close:
            try:
                symbol = position.get('symbol')
                quantity = position.get('quantity', 0)
                action = position.get('action', 'BUY')
                entry_price = position.get('entry_price', 0)
                
                log_event("SQUARE_OFF_PROCESSING", f"Processing {symbol}",
                         quantity=quantity, action=action, entry_price=entry_price)
                
                # 🔧 CRITICAL FIX: Mark position as CLOSED immediately to prevent new entries
                # This blocks new BUY alerts for this symbol during square-off
                if symbol in trading_state.active_positions:
                    trading_state.active_positions[symbol]['status'] = 'CLOSED'
                    trading_state.active_positions[symbol]['closed_at'] = datetime.now().isoformat()
                    trading_state.active_positions[symbol]['close_reason'] = 'SQUARE_OFF'
                    log_event("SQUARE_OFF_BLOCKED_NEW_ENTRY", f"Position {symbol} marked CLOSED to block new entries")
                
                # Opposite action to close
                close_action = 'SELL' if action == 'BUY' else 'BUY'
                
                # Get current LTP
                try:
                    log_event("SQUARE_OFF_GET_QUOTE", f"Fetching quote for {symbol}")
                    current_ltp = trading_state.broker.get_ltp(symbol)
                    if current_ltp is None or current_ltp <= 0:
                        current_ltp = entry_price  # Fallback
                        log_event("SQUARE_OFF_QUOTE_FALLBACK", f"Invalid LTP, using entry price",
                                 symbol=symbol, fallback_price=current_ltp)
                    else:
                        log_event("SQUARE_OFF_QUOTE_SUCCESS", f"Got LTP for {symbol}",
                                 symbol=symbol, ltp=current_ltp)
                except Exception as quote_error:
                    log_error("SQUARE_OFF_QUOTE_ERROR", f"Failed to get quote for {symbol}: {str(quote_error)}")
                    current_ltp = entry_price  # Fallback
                    log_event("SQUARE_OFF_QUOTE_FALLBACK", f"Using entry price as fallback",
                             symbol=symbol, fallback_price=current_ltp)
                
                # Place close order
                log_event("SQUARE_OFF_PLACE_ORDER", f"Placing {close_action} order for {symbol}",
                         symbol=symbol, action=close_action, quantity=quantity, price=current_ltp)
                
                close_order = trading_state.broker.place_order(
                    symbol=symbol,
                    action=close_action,
                    quantity=quantity,
                    price=current_ltp,
                    order_type="MARKET"
                )
                
                # close_order can be an Order object or dict
                order_id = None
                if close_order:
                    if hasattr(close_order, 'order_id'):
                        order_id = close_order.order_id
                    elif isinstance(close_order, dict) and 'order_id' in close_order:
                        order_id = close_order.get('order_id')
                
                if order_id:
                    log_event("SQUARE_OFF_ORDER_SUCCESS", f"Order placed for {symbol}",
                             symbol=symbol, order_id=order_id)
                    closed_count += 1
                else:
                    log_error("SQUARE_OFF_ORDER_FAILED", f"No order ID returned for {symbol}", 
                             context={'symbol': symbol, 'close_order': str(close_order)})
                
                # Calculate P&L
                if action == 'BUY':
                    pnl = (current_ltp - entry_price) * quantity
                else:
                    pnl = (entry_price - current_ltp) * quantity
                
                log_event("SQUARE_OFF_PNL_CALCULATED", f"P&L calculated for {symbol}",
                         symbol=symbol, pnl=pnl, entry=entry_price, exit=current_ltp)
                
                total_pnl += pnl
                
                # Release capital - use 'capital_used' key from position dict
                capital_to_release = position.get('capital_used', 0)  # Try 'capital_used' first
                if capital_to_release == 0:
                    capital_to_release = position.get('capital_allocated', 0)  # Fallback to old key
                
                log_event("SQUARE_OFF_CAPITAL_READY", f"Capital to release for {symbol}",
                         symbol=symbol, capital=capital_to_release)
                
                if capital_to_release > 0:
                    try:
                        log_event("SQUARE_OFF_RELEASE_CAPITAL_START", f"Releasing capital for {symbol}",
                                 symbol=symbol, capital=capital_to_release)
                        trading_state.release_capital(symbol, capital_to_release)
                        log_event("SQUARE_OFF_RELEASE_CAPITAL_SUCCESS", f"Capital released for {symbol}",
                                 symbol=symbol, capital=capital_to_release)
                    except Exception as release_error:
                        log_error("CAPITAL_RELEASE_ERROR", f"Failed to release capital for {symbol}: {str(release_error)}")
                        errors.append(f"{symbol}: Capital release failed - {str(release_error)}")
                
                # Log trade to CSV for ML learning
                try:
                    log_event("SQUARE_OFF_LOG_TRADE_START", f"Logging trade to CSV for {symbol}",
                             symbol=symbol, action=close_action, quantity=quantity)
                    
                    from eqcode.logging import log_trade as csv_log_trade
                    csv_log_trade(
                        action=close_action,
                        symbol=symbol,
                        quantity=quantity,
                        entry_price=entry_price,
                        exit_price=current_ltp,
                        capital_used=capital_to_release,
                        sl_price=position.get('sl_price', 0),
                        pnl=pnl,
                        status="CLOSED"
                    )
                    
                    log_event("SQUARE_OFF_LOG_TRADE_SUCCESS", f"Trade logged to CSV for {symbol}",
                             symbol=symbol, exit_price=current_ltp, pnl=pnl)
                    logged_count += 1
                except Exception as log_error_ex:
                    log_error("TRADE_LOGGING_ERROR", f"Failed to log trade for {symbol}: {str(log_error_ex)}")
                    errors.append(f"{symbol}: Trade logging failed - {str(log_error_ex)}")
                
                # Handle order_id extraction (close_order can be Order object or dict)
                close_order_id = None
                if close_order:
                    if hasattr(close_order, 'order_id'):
                        close_order_id = close_order.order_id
                    elif isinstance(close_order, dict):
                        close_order_id = close_order.get('orderId') or close_order.get('order_id')
                
                log_event("SQUARE_OFF_POSITION_COMPLETE", f"Completed processing {symbol}",
                         symbol=symbol, order_id=close_order_id)
                
            except Exception as e:
                error_msg = f"Failed to close {position.get('symbol')}: {str(e)}"
                log_error("SQUARE_OFF_ERROR", error_msg, exception=e,
                         context={'symbol': position.get('symbol')})
                errors.append(error_msg)
        
        # 🔧 CRITICAL FIX: Save closed positions to disk to prevent re-entry
        try:
            trading_state.save_positions()
            log_event("SQUARE_OFF_POSITIONS_SAVED", f"Closed positions persisted to disk")
        except Exception as save_error:
            log_error("SQUARE_OFF_SAVE_ERROR", f"Failed to save positions after square-off: {str(save_error)}")
            errors.append(f"Failed to persist closed positions to disk: {str(save_error)}")
        
        log_event("SQUARE_OFF_COMPLETE", 
                 f"Square-off complete: {closed_count} positions closed, {logged_count} trades logged, P&L: ₹{total_pnl:.2f}",
                 positions_closed=closed_count,
                 trades_logged=logged_count,
                 total_pnl=total_pnl,
                 errors=len(errors),
                 reason=reason)
        
        return jsonify({
            "status": "success" if not errors else "partial",
            "message": f"Squared off {closed_count} positions",
            "positions_closed": closed_count,
            "total_pnl": total_pnl,
            "errors": errors,
            "timestamp": datetime.now().isoformat(),
            "reason": reason
        }), 200
    
    except Exception as e:
        log_error("SQUARE_OFF_ENDPOINT_ERROR", "Failed to execute square-off", e)
        return jsonify({"error": str(e)}), 500


@app.route('/learning/eod-update', methods=['POST'])
def learning_eod_update():
    """
    Trigger end-of-day learning update at 3:15 PM.
    
    TIMING:
    - 3:12 PM: /square-off called (closes all positions)
    - 3:15 PM: /learning/eod-update called (THIS endpoint)
    - 3:30 PM: Market closes
    
    This should be called daily at 3:15 PM to:
    1. Get missed trades logged during the day
    2. Fetch current LTP for each missed symbol (15 min before close)
    3. Simulate paper trades using EOD close prices
    4. Analyze all executed trades
    5. Update feature importance weights
    6. Update symbol performance metrics
    7. Save learning for next day
    
    Benefits of 3:15 PM timing:
    - 15 minutes before market close (more complete data)
    - All real positions already closed (no interference)
    - Rate limit safe (50+ tokens available)
    - Paper trading gets fresh LTP prices
    - Learning ingestion complete before 3:30 PM
    """
    try:
        if not HYBRID_LEARNING_AVAILABLE:
            return jsonify({"error": "Hybrid learning system not available"}), 503
        
        log_event("EOD_LEARNING_UPDATE", "Triggering end-of-day learning update")
        
        analysis = get_eod_analysis()
        
        log_event("EOD_ANALYSIS_COMPLETE", "Learning update completed",
                 summary=analysis.get('daily_summary'))
        
        return jsonify({
            "status": "success",
            "message": "End-of-day learning update completed",
            "analysis": analysis
        }), 200
    
    except Exception as e:
        log_error("EOD_UPDATE_ERROR", "Failed to execute EOD learning update", e)
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint with smart session caching to avoid rate limits"""
    try:
        # Use cached broker status if available (cache for 2 minutes)
        now = datetime.now()
        if hasattr(trading_state, '_health_cache'):
            cache_age = (now - trading_state._health_cache_time).total_seconds()
            if cache_age < 120:  # 2 minute cache
                cached_data = trading_state._health_cache.copy()
                cached_data["timestamp"] = now.isoformat()
                cached_data["cached"] = True
                cached_data["cache_age_seconds"] = int(cache_age)
                return jsonify(cached_data), 200
        
        # Check broker session (uses internal caching)
        broker_status = "connected" if trading_state.broker and trading_state.broker.logged_in() else "disconnected"
        
        # Get capital status
        capital_status = trading_state.get_capital_status()
        
        # Get position count
        position_count = len(trading_state.active_positions)
        
        # Get rate limiter statistics (safely)
        rate_limiter_stats = None
        if trading_state.broker and hasattr(trading_state.broker, 'rate_limiter'):
            try:
                rate_limiter_stats = {
                    "per_second": {
                        "available": trading_state.broker.rate_limiter.second_bucket.tokens,
                        "capacity": trading_state.broker.rate_limiter.second_bucket.capacity
                    },
                    "per_minute": {
                        "available": trading_state.broker.rate_limiter.minute_bucket.tokens,
                        "capacity": trading_state.broker.rate_limiter.minute_bucket.capacity
                    }
                }
            except:
                rate_limiter_stats = {"status": "rate_limiter_active"}
        
        # Get session info
        session_info = {}
        if trading_state.broker and hasattr(trading_state.broker, 'session_created_at') and trading_state.broker.session_created_at:
            age_minutes = (datetime.now() - trading_state.broker.session_created_at).total_seconds() / 60
            session_info = {
                "age_minutes": round(age_minutes, 1),
                "created_at": trading_state.broker.session_created_at.isoformat()
            }
        
        health_data = {
            "status": "healthy",
            "timestamp": now.isoformat(),
            "broker_status": broker_status,
            "trading_mode": TradingConfig.TRADING_MODE,
            "position_count": position_count,
            "capital_status": capital_status,
            "config_valid": validate_config()[0],
            "rate_limiter": rate_limiter_stats,
            "session_info": session_info,
            "cached": False
        }
        
        # Cache the result
        trading_state._health_cache = health_data.copy()
        trading_state._health_cache_time = now
        
        return jsonify(health_data), 200
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500


@app.route('/smartapi-status', methods=['GET'])
def smartapi_status():
    """Detailed SmartAPI login and session status"""
    try:
        if not trading_state.broker:
            return jsonify({
                "status": "error",
                "message": "Broker not initialized",
                "logged_in": False,
                "timestamp": datetime.now().isoformat()
            }), 503
        
        # Check login status with auto-refresh
        is_logged_in = trading_state.broker.logged_in()
        
        # Get session details
        session_info = {}
        if hasattr(trading_state.broker, 'smart_api') and trading_state.broker.smart_api:
            try:
                # Try a simple API call to get session info
                orders = trading_state.broker.smart_api.orderBook()
                if orders and orders.get('status'):
                    session_info = {
                        "session_valid": True,
                        "api_responsive": True,
                        "last_check": datetime.now().isoformat()
                    }
                    
                    # Get session age if available
                    if hasattr(trading_state.broker, 'session_created_at') and trading_state.broker.session_created_at:
                        age_minutes = (datetime.now() - trading_state.broker.session_created_at).total_seconds() / 60
                        session_info["session_age_minutes"] = round(age_minutes, 1)
                        session_info["session_created_at"] = trading_state.broker.session_created_at.isoformat()
                    
                else:
                    session_info = {"session_valid": False, "error": "Order book fetch failed"}
            except Exception as e:
                session_info = {"session_valid": False, "error": str(e)}
        
        # Check rate limiter status
        rate_limiter_status = None
        if hasattr(trading_state.broker, 'rate_limiter'):
            try:
                rate_limiter_status = {
                    "per_second_available": trading_state.broker.rate_limiter.per_second.tokens,
                    "per_minute_available": trading_state.broker.rate_limiter.per_minute.tokens,
                    "per_second_capacity": trading_state.broker.rate_limiter.per_second.capacity,
                    "per_minute_capacity": trading_state.broker.rate_limiter.per_minute.capacity
                }
            except:
                rate_limiter_status = {"status": "active"}
        
        # Get last successful API call timestamp
        last_api_call = None
        if hasattr(trading_state.broker, 'rate_limiter') and hasattr(trading_state.broker.rate_limiter, 'last_call_time'):
            try:
                last_api_call = trading_state.broker.rate_limiter.last_call_time.isoformat() if trading_state.broker.rate_limiter.last_call_time else None
            except:
                last_api_call = "unknown"
        
        status_data = {
            "status": "success",
            "logged_in": is_logged_in,
            "session_info": session_info,
            "rate_limiter": rate_limiter_status,
            "last_api_call": last_api_call,
            "trading_mode": TradingConfig.TRADING_MODE,
            "paper_trading": TradingConfig.TRADING_MODE == "PAPER",
            "timestamp": datetime.now().isoformat()
        }
        
        return jsonify(status_data), 200
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "logged_in": False,
            "timestamp": datetime.now().isoformat()
        }), 500


@app.route('/session-refresh', methods=['POST'])
def session_refresh():
    """Force session refresh/re-login"""
    try:
        if not trading_state.broker:
            return jsonify({
                "status": "error",
                "message": "Broker not initialized"
            }), 503
        
        # Force session refresh
        log_event("API", "Manual session refresh requested")
        
        # Clear current session
        trading_state.broker.session_token = None
        trading_state.broker.refresh_token = None
        
        # Attempt to establish new session
        success = trading_state.broker.ensure_session()
        
        if success:
            session_age = 0
            if hasattr(trading_state.broker, 'session_created_at') and trading_state.broker.session_created_at:
                session_age = (datetime.now() - trading_state.broker.session_created_at).total_seconds() / 60
            
            return jsonify({
                "status": "success",
                "message": "Session refreshed successfully",
                "logged_in": True,
                "session_age_minutes": round(session_age, 1),
                "timestamp": datetime.now().isoformat()
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Session refresh failed",
                "logged_in": False,
                "timestamp": datetime.now().isoformat()
            }), 500
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Session refresh error: {str(e)}",
            "logged_in": False,
            "timestamp": datetime.now().isoformat()
        }), 500


@app.route('/status-lite', methods=['GET'])
def status_lite():
    """Ultra-lightweight status check - NO API calls to AngelOne"""
    try:
        now = datetime.now()
        
        # Basic system status without API calls
        session_status = "unknown"
        session_age_minutes = None
        
        if trading_state.broker:
            if hasattr(trading_state.broker, 'session_created_at') and trading_state.broker.session_created_at:
                session_age_minutes = (now - trading_state.broker.session_created_at).total_seconds() / 60
                if session_age_minutes < 45:
                    session_status = "likely_valid"
                else:
                    session_status = "likely_expired"
            
            if trading_state.broker.session_token and trading_state.broker.smart_api:
                session_status = f"{session_status}_with_token"
        
        # Rate limiter status (local only)
        rate_status = {}
        if trading_state.broker and hasattr(trading_state.broker, 'rate_limiter'):
            try:
                rate_status = {
                    "per_second_available": trading_state.broker.rate_limiter.second_bucket.tokens,
                    "per_minute_available": trading_state.broker.rate_limiter.minute_bucket.tokens,
                    "total_capacity_rps": trading_state.broker.rate_limiter.second_bucket.capacity,
                    "total_capacity_rpm": trading_state.broker.rate_limiter.minute_bucket.capacity
                }
            except:
                rate_status = {"error": "rate_limiter_unavailable"}
        
        status_data = {
            "status": "success",
            "type": "lightweight",
            "timestamp": now.isoformat(),
            "session_status": session_status,
            "session_age_minutes": round(session_age_minutes, 1) if session_age_minutes else None,
            "trading_mode": TradingConfig.TRADING_MODE,
            "position_count": len(trading_state.active_positions),
            "rate_limiter": rate_status,
            "api_calls_used": "NONE - This endpoint makes no API calls",
            "process_uptime_minutes": round((now - trading_state.startup_time).total_seconds() / 60, 1) if hasattr(trading_state, 'startup_time') else None
        }
        
        return jsonify(status_data), 200
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500


@app.route('/rate-limit-status', methods=['GET'])
def rate_limit_status():
    """Dedicated rate limiter monitoring - NO API calls"""
    try:
        if not trading_state.broker or not hasattr(trading_state.broker, 'rate_limiter'):
            return jsonify({
                "status": "error",
                "message": "Rate limiter not available"
            }), 503
        
        rate_limiter = trading_state.broker.rate_limiter
        
        # TokenBucket in angelone.py uses rps_tokens and rpm_tokens
        status_data = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "type": type(rate_limiter).__name__,
            "per_second": {
                "available": getattr(rate_limiter, 'rps_tokens', 0),
                "capacity": getattr(rate_limiter, 'rps_limit', 6),
                "usage_percent": 0  # Will calculate below
            },
            "per_minute": {
                "available": getattr(rate_limiter, 'rpm_tokens', 0),
                "capacity": getattr(rate_limiter, 'rpm_limit', 150),
                "usage_percent": 0  # Will calculate below
            }
        }
        
        # Calculate usage percentages
        rps_available = status_data["per_second"]["available"]
        rps_capacity = status_data["per_second"]["capacity"]
        rpm_available = status_data["per_minute"]["available"]
        rpm_capacity = status_data["per_minute"]["capacity"]
        
        status_data["per_second"]["usage_percent"] = round((1 - rps_available / rps_capacity) * 100, 1) if rps_capacity > 0 else 0
        status_data["per_minute"]["usage_percent"] = round((1 - rpm_available / rpm_capacity) * 100, 1) if rpm_capacity > 0 else 0
        
        # Add health assessment
        status_data["health"] = {
            "per_second_ok": rps_available > 2,
            "per_minute_ok": rpm_available > 10,
            "overall_ok": rps_available > 2 and rpm_available > 10
        }
        
        # Add recommendations
        status_data["recommendations"] = []
        if rps_available <= 2:
            status_data["recommendations"].append("CRITICAL: Per-second rate limit nearly exhausted. Pause non-essential API calls.")
        
        if rpm_available <= 10:
            status_data["recommendations"].append("WARNING: Per-minute rate limit low. Reduce API call frequency.")
        
        if rpm_available <= 30:
            status_data["recommendations"].append("CAUTION: High API usage. Monitor closely.")
        
        # Add statistics if available
        if hasattr(rate_limiter, 'total_requests'):
            status_data["statistics"] = {
                "total_requests": rate_limiter.total_requests,
                "blocked_requests": getattr(rate_limiter, 'blocked_requests', 0),
                "recent_requests": len(getattr(rate_limiter, 'recent_requests', [])),
                "last_rps_refill": getattr(rate_limiter, 'last_rps_refill', 0),
                "last_rpm_refill": getattr(rate_limiter, 'last_rpm_refill', 0)
            }
        
        return jsonify(status_data), 200
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500


@app.route('/login-test', methods=['GET'])
def login_test():
    """Test SmartAPI login and basic API functionality"""
    try:
        if not trading_state.broker:
            return jsonify({
                "status": "error",
                "message": "Broker not initialized"
            }), 503
        
        # Test login
        login_success = trading_state.broker.logged_in()
        
        if not login_success:
            return jsonify({
                "status": "error",
                "message": "Not logged in to SmartAPI",
                "logged_in": False
            }), 401
        
        test_results = {
            "login_check": "✅ Logged in successfully",
            "timestamp": datetime.now().isoformat()
        }
        
        # Test profile fetch
        try:
            profile = trading_state.broker.smart_api.getProfile()
            if profile and profile.get('status'):
                test_results["profile_fetch"] = "✅ Profile fetch successful"
                test_results["user_name"] = profile.get('data', {}).get('name', 'Unknown')
            else:
                test_results["profile_fetch"] = "❌ Profile fetch failed"
        except Exception as e:
            test_results["profile_fetch"] = f"❌ Profile fetch error: {str(e)}"
        
        # Test LTP fetch for a sample stock
        try:
            sample_token = "2885"  # RELIANCE
            ltp_data = trading_state.broker.smart_api.ltpData("NSE", "RELIANCE-EQ", sample_token)
            if ltp_data and ltp_data.get('status'):
                test_results["ltp_fetch"] = "✅ LTP fetch successful"
                test_results["reliance_ltp"] = ltp_data.get('data', {}).get('ltp', 'Unknown')
            else:
                test_results["ltp_fetch"] = "❌ LTP fetch failed"
        except Exception as e:
            test_results["ltp_fetch"] = f"❌ LTP fetch error: {str(e)}"
        
        # Test holdings fetch
        try:
            holdings = trading_state.broker.smart_api.holding()
            if holdings and holdings.get('status'):
                test_results["holdings_fetch"] = "✅ Holdings fetch successful"
                test_results["holdings_count"] = len(holdings.get('data', []))
            else:
                test_results["holdings_fetch"] = "❌ Holdings fetch failed"
        except Exception as e:
            test_results["holdings_fetch"] = f"❌ Holdings fetch error: {str(e)}"
        
        return jsonify({
            "status": "success",
            "logged_in": True,
            "tests": test_results
        }), 200
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "logged_in": False
        }), 500


@app.route('/force-login', methods=['POST'])
def force_login():
    """Force a fresh login to SmartAPI"""
    try:
        if not trading_state.broker:
            return jsonify({
                "status": "error",
                "message": "Broker not initialized"
            }), 503
        
        # Clear existing session
        trading_state.broker.session_token = None
        trading_state.broker.refresh_token = None
        trading_state.broker.smart_api = None
        
        # Clear cached session file
        import os
        session_file = os.path.join("data", "session.json")
        if os.path.exists(session_file):
            os.remove(session_file)
            log_event("LOGIN", "Cleared cached session file")
        
        # Attempt fresh login
        login_success = trading_state.broker.login()
        
        if login_success:
            # Verify login with profile fetch
            try:
                if hasattr(trading_state.broker, 'smart_api') and trading_state.broker.smart_api:
                    profile = trading_state.broker.smart_api.getProfile()
                    if profile and profile.get('status'):
                        user_info = {
                            "name": profile.get('data', {}).get('name', 'Unknown'),
                            "client_code": profile.get('data', {}).get('clientcode', 'Unknown'),
                            "email": profile.get('data', {}).get('email', 'Unknown')
                        }
                        return jsonify({
                            "status": "success",
                            "message": "Login successful",
                            "logged_in": True,
                            "user_info": user_info,
                            "timestamp": datetime.now().isoformat()
                        }), 200
            except Exception as e:
                return jsonify({
                    "status": "partial_success",
                    "message": f"Login successful but profile fetch failed: {str(e)}",
                    "logged_in": True,
                    "timestamp": datetime.now().isoformat()
                }), 200
        
        return jsonify({
            "status": "error",
            "message": "Login failed",
            "logged_in": False,
            "timestamp": datetime.now().isoformat()
        }), 401
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Login attempt failed: {str(e)}",
            "logged_in": False,
            "timestamp": datetime.now().isoformat()
        }), 500


@app.route('/positions', methods=['GET'])
def positions():
    """Get current positions"""
    try:
        positions_data = []
        
        for symbol, position in trading_state.active_positions.items():
            # Get current LTP
            current_ltp = trading_state.broker.get_ltp(symbol)
            
            # Calculate unrealized P&L
            unrealized_pnl = 0
            if current_ltp and position.get("entry_price"):
                price_diff = current_ltp - position["entry_price"]
                unrealized_pnl = price_diff * position["quantity"]
            
            position_info = {
                **position,
                "current_ltp": current_ltp,
                "unrealized_pnl": unrealized_pnl
            }
            
            positions_data.append(position_info)
        
        return jsonify({
            "positions": positions_data,
            "count": len(positions_data),
            "capital_status": trading_state.get_capital_status()
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/config', methods=['GET'])
def config():
    """Get current configuration"""
    try:
        config_summary = get_config_summary()
        is_valid, errors = validate_config()
        
        return jsonify({
            "config": config_summary,
            "valid": is_valid,
            "errors": errors if not is_valid else []
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/rate-limiter', methods=['GET'])
def rate_limiter_status():
    """Get detailed rate limiter statistics"""
    try:
        if not trading_state.broker:
            return jsonify({"error": "Broker not initialized"}), 500
        
        stats = trading_state.broker.get_rate_limiter_stats()
        
        return jsonify({
            "rate_limiter_stats": stats,
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/rate-limiter/reset', methods=['POST'])
def reset_rate_limiter():
    """Reset rate limiter statistics"""
    try:
        if not trading_state.broker:
            return jsonify({"error": "Broker not initialized"}), 500
        
        if hasattr(trading_state.broker.rate_limiter, 'reset_stats'):
            trading_state.broker.rate_limiter.reset_stats()
        
        return jsonify({
            "message": "Rate limiter statistics reset",
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Analytics Endpoints - 5% Target Tracking
# =============================================================================

# Initialize analytics (lazy loading to avoid import issues)
_analytics_instance = None

def get_analytics():
    """Get analytics instance with lazy loading"""
    global _analytics_instance
    if _analytics_instance is None:
        try:
            from .analytics.target_analytics import TargetAnalytics
            _analytics_instance = TargetAnalytics()
        except Exception as e:
            log_event("ERROR", f"Failed to initialize analytics: {str(e)}")
            return None
    return _analytics_instance


@app.route('/analytics/target-dashboard', methods=['GET'])
def analytics_target_dashboard():
    """Get comprehensive dashboard for 5% daily target tracking"""
    try:
        analytics = get_analytics()
        if not analytics:
            return jsonify({"error": "Analytics not available"}), 500
        
        dashboard = analytics.get_daily_dashboard()
        
        # Add real-time calculations
        current_time = datetime.now()
        market_hours_elapsed = max(0, (current_time.hour - 9) + (current_time.minute / 60))
        market_hours_remaining = max(0, 6.5 - market_hours_elapsed)  # 9:15 AM to 3:30 PM
        
        dashboard['timing'] = {
            'current_time': current_time.strftime('%H:%M:%S'),
            'market_hours_elapsed': round(market_hours_elapsed, 1),
            'market_hours_remaining': round(market_hours_remaining, 1),
            'pace_required': dashboard['target']['daily_target_amount'] / max(market_hours_remaining, 0.1) if market_hours_remaining > 0 else 0
        }
        
        # Add urgency indicators
        achievement_percent = dashboard['performance']['achievement_percent']
        time_percent = (market_hours_elapsed / 6.5) * 100
        
        dashboard['urgency'] = {
            'behind_schedule': achievement_percent < time_percent * 0.8,
            'on_track': time_percent * 0.8 <= achievement_percent <= time_percent * 1.2,
            'ahead_of_schedule': achievement_percent > time_percent * 1.2,
            'critical_time': market_hours_remaining < 2 and achievement_percent < 70
        }
        
        return jsonify(dashboard), 200
        
    except Exception as e:
        log_event("ERROR", f"Error getting target dashboard: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/analytics/target-recommendations', methods=['GET'])
def analytics_target_recommendations():
    """Get AI-driven recommendations for achieving 5% target"""
    try:
        analytics = get_analytics()
        if not analytics:
            return jsonify({"error": "Analytics not available"}), 500
        
        recommendations = analytics.get_recommendations()
        
        # Add real-time strategy adjustments
        dashboard = analytics.get_daily_dashboard()
        achievement_percent = dashboard['performance']['achievement_percent']
        trades_completed = dashboard['performance']['trades_completed']
        
        # Dynamic strategy suggestions
        if achievement_percent < 30 and trades_completed >= 3:
            recommendations['optimization_suggestions'].append(
                "Consider increasing position size within risk limits or switching to higher volatility stocks"
            )
        
        if trades_completed < 2 and datetime.now().hour >= 14:  # After 2 PM
            recommendations['optimization_suggestions'].append(
                "Time pressure: Focus on quick scalping opportunities with 0.5-1% targets"
            )
        
        return jsonify(recommendations), 200
        
    except Exception as e:
        log_event("ERROR", f"Error getting recommendations: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/analytics/track-trade', methods=['POST'])
def analytics_track_trade():
    """Track a completed trade and analyze against 5% target"""
    try:
        analytics = get_analytics()
        if not analytics:
            return jsonify({"error": "Analytics not available"}), 500
        
        trade_data = request.get_json()
        if not trade_data:
            return jsonify({"error": "No trade data provided"}), 400
        
        analysis = analytics.track_trade_completion(trade_data)
        
        # Add immediate next-action suggestions
        if analysis.get('target_achieved'):
            analysis['next_action'] = "Excellent! Continue with similar strategy for remaining positions"
        else:
            profit_percent = analysis.get('profit_percent', 0)
            if profit_percent > 0:
                analysis['next_action'] = f"Positive but below target. Consider tighter entry criteria"
            else:
                analysis['next_action'] = "Loss incurred. Review entry strategy and risk management"
        
        return jsonify(analysis), 200
        
    except Exception as e:
        log_event("ERROR", f"Error tracking trade: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/analytics/symbol-performance/<symbol>', methods=['GET'])
def analytics_symbol_performance(symbol: str):
    """Get detailed performance analysis for a specific symbol"""
    try:
        analytics = get_analytics()
        if not analytics:
            return jsonify({"error": "Analytics not available"}), 500
        
        import sqlite3
        
        with sqlite3.connect(analytics.db_file) as conn:
            # Symbol stats
            cursor = conn.execute("""
                SELECT * FROM symbol_performance WHERE symbol = ?
            """, (symbol,))
            symbol_data = cursor.fetchone()
            
            if not symbol_data:
                return jsonify({"error": f"No data found for symbol {symbol}"}), 404
            
            # Recent trades for this symbol
            cursor = conn.execute("""
                SELECT date, profit_percent, target_achieved, hold_duration_minutes, exit_reason
                FROM trade_efficiency 
                WHERE symbol = ? 
                ORDER BY exit_time DESC 
                LIMIT 10
            """, (symbol,))
            recent_trades = cursor.fetchall()
            
            # Performance trends (last 7 days)
            cursor = conn.execute("""
                SELECT date, AVG(profit_percent) as avg_profit, 
                       COUNT(CASE WHEN target_achieved = 1 THEN 1 END) as successful_trades,
                       COUNT(*) as total_trades
                FROM trade_efficiency 
                WHERE symbol = ? AND date >= date('now', '-7 days')
                GROUP BY date
                ORDER BY date DESC
            """, (symbol,))
            trends = cursor.fetchall()
        
        performance = {
            'symbol': symbol,
            'overall_stats': {
                'total_trades': symbol_data[1],
                'profitable_trades': symbol_data[2],
                'win_rate': symbol_data[3],
                'avg_profit_percent': symbol_data[4],
                'avg_hold_duration': symbol_data[5],
                'best_profit': symbol_data[6],
                'worst_profit': symbol_data[7],
                'target_achievement_rate': symbol_data[8]
            },
            'recent_trades': [
                {
                    'date': row[0],
                    'profit_percent': row[1],
                    'target_achieved': bool(row[2]),
                    'hold_duration_minutes': row[3],
                    'exit_reason': row[4]
                }
                for row in recent_trades
            ],
            'weekly_trends': [
                {
                    'date': row[0],
                    'avg_profit': row[1],
                    'successful_trades': row[2],
                    'total_trades': row[3],
                    'success_rate': (row[2] / row[3] * 100) if row[3] > 0 else 0
                }
                for row in trends
            ]
        }
        
        # Add recommendation based on statistical significance for intraday trading
        target_achievement_rate = symbol_data[8]
        avg_profit = symbol_data[4]
        total_trades = symbol_data[1]
        
        # Intraday trading requires proper sample size for reliable recommendations
        if total_trades < 10:
            performance['recommendation'] = "INSUFFICIENT DATA - Need minimum 10 trades for intraday analysis"
            performance['confidence'] = "VERY_LOW"
            performance['sample_size_note'] = f"Only {total_trades} trades - need at least 10 for basic patterns"
        elif total_trades < 20:
            performance['recommendation'] = "PRELIMINARY DATA - Early patterns emerging, use with caution"
            performance['confidence'] = "LOW"
            performance['sample_size_note'] = f"{total_trades} trades - patterns starting to emerge"
        elif total_trades < 30:
            if target_achievement_rate >= 60 and avg_profit >= 0.8:
                performance['recommendation'] = "EMERGING PATTERN - Showing promise, continue monitoring"
            else:
                performance['recommendation'] = "MIXED RESULTS - Pattern unclear, need more data"
            performance['confidence'] = "MEDIUM"
            performance['sample_size_note'] = f"{total_trades} trades - medium confidence level"
        else:
            # Only make strong recommendations with 30+ trades (statistically significant)
            if target_achievement_rate >= 70 and avg_profit >= 1:
                performance['recommendation'] = "HIGH PRIORITY - Statistically proven performer"
            elif target_achievement_rate >= 50:
                performance['recommendation'] = "MEDIUM PRIORITY - Consistent performer with good sample size"
            else:
                performance['recommendation'] = "LOW PRIORITY - Consistently underperforms with sufficient data"
            performance['confidence'] = "HIGH"
            performance['sample_size_note'] = f"{total_trades} trades - statistically significant sample"
        
        return jsonify(performance), 200
        
    except Exception as e:
        log_event("ERROR", f"Error getting symbol performance: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/analytics/margin-utilization', methods=['GET'])
def analytics_margin_utilization():
    """Get real-time margin utilization analysis"""
    try:
        analytics = get_analytics()
        if not analytics:
            return jsonify({"error": "Analytics not available"}), 500
        
        import sqlite3
        
        with sqlite3.connect(analytics.db_file) as conn:
            # Get latest portfolio state
            cursor = conn.execute("""
                SELECT * FROM portfolio_utilization 
                ORDER BY timestamp DESC LIMIT 1
            """)
            portfolio = cursor.fetchone()
            
            # Calculate optimal allocation
            total_capital = analytics.total_capital
            margin_per_trade = analytics.capital_per_trade
            max_positions = analytics.max_positions
            
            current_positions = portfolio[1] if portfolio else 0
            margin_utilized = portfolio[2] if portfolio else 0
            
            utilization = {
                'capital_allocation': {
                    'total_capital': total_capital,
                    'margin_per_trade': margin_per_trade,
                    'max_positions': max_positions,
                    'max_possible_margin': margin_per_trade * max_positions
                },
                'current_state': {
                    'active_positions': current_positions,
                    'margin_utilized': margin_utilized,
                    'available_slots': max_positions - current_positions,
                    'available_margin': (max_positions - current_positions) * margin_per_trade,
                    'utilization_percent': (current_positions / max_positions) * 100
                },
                'optimization': {
                    'underutilized': current_positions < max_positions * 0.8,
                    'fully_utilized': current_positions == max_positions,
                    'can_add_positions': current_positions < max_positions,
                    'recommended_action': None
                }
            }
            
            # Add recommendations
            if current_positions < max_positions * 0.5:
                utilization['optimization']['recommended_action'] = "SCALE UP - Only using 50% of capacity"
            elif current_positions < max_positions:
                utilization['optimization']['recommended_action'] = f"ADD POSITIONS - {max_positions - current_positions} slots available"
            else:
                utilization['optimization']['recommended_action'] = "FULLY ALLOCATED - Monitor for exit opportunities"
            
            return jsonify(utilization), 200
            
    except Exception as e:
        log_event("ERROR", f"Error getting margin utilization: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/analytics/target-progress', methods=['GET'])
def analytics_target_progress():
    """Get real-time progress toward 5% daily target"""
    try:
        analytics = get_analytics()
        if not analytics:
            return jsonify({"error": "Analytics not available"}), 500
        
        dashboard = analytics.get_daily_dashboard()
        
        target_amount = dashboard['target']['daily_target_amount']
        achieved_amount = dashboard['performance']['achieved_amount']
        achievement_percent = dashboard['performance']['achievement_percent']
        
        # Calculate required performance for remaining time
        current_hour = datetime.now().hour
        current_minute = datetime.now().minute
        
        if 9 <= current_hour <= 15:  # Market hours
            elapsed_minutes = (current_hour - 9) * 60 + current_minute
            remaining_minutes = (15 * 60 + 30) - elapsed_minutes  # Until 3:30 PM
            
            if remaining_minutes > 0:
                required_rate = (target_amount - achieved_amount) / (remaining_minutes / 60)
            else:
                required_rate = 0
        else:
            elapsed_minutes = 0
            remaining_minutes = 0
            required_rate = 0
        
        progress = {
            'target': {
                'amount': target_amount,
                'percentage': 5.0
            },
            'current': {
                'achieved_amount': achieved_amount,
                'achievement_percent': achievement_percent,
                'remaining_amount': target_amount - achieved_amount
            },
            'timing': {
                'elapsed_minutes': elapsed_minutes,
                'remaining_minutes': max(0, remaining_minutes),
                'required_hourly_rate': required_rate,
                'pace_status': 'on_track' if achievement_percent >= 60 else 'behind' if achievement_percent >= 30 else 'critical'
            },
            'milestones': {
                '25_percent_target': target_amount * 0.25,
                '50_percent_target': target_amount * 0.50,
                '75_percent_target': target_amount * 0.75,
                '25_percent_achieved': achieved_amount >= target_amount * 0.25,
                '50_percent_achieved': achieved_amount >= target_amount * 0.50,
                '75_percent_achieved': achieved_amount >= target_amount * 0.75,
                'target_achieved': achieved_amount >= target_amount
            }
        }
        
        return jsonify(progress), 200
        
    except Exception as e:
        log_event("ERROR", f"Error getting target progress: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/analytics/post-session-analysis', methods=['POST'])
def analytics_post_session_analysis():
    """Run complete post-session analysis from trading logs"""
    try:
        from .analytics.log_parser import run_post_session_analysis
        
        # Get date from request (optional)
        data = request.get_json() if request.is_json else {}
        date_str = data.get('date') if data else None
        
        # Run complete analysis
        analysis = run_post_session_analysis(date_str)
        
        return jsonify(analysis), 200
        
    except Exception as e:
        log_event("ERROR", f"Error in post-session analysis: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/analytics/backfill-logs', methods=['POST'])
def analytics_backfill_logs():
    """Backfill analytics data from historical logs"""
    try:
        from .analytics.log_parser import LogParser
        
        data = request.get_json()
        if not data or 'start_date' not in data:
            return jsonify({"error": "start_date required in request body"}), 400
        
        parser = LogParser()
        results = parser.backfill_analytics(
            data['start_date'],
            data.get('end_date')
        )
        
        return jsonify(results), 200
        
    except Exception as e:
        log_event("ERROR", f"Error in backfill operation: {str(e)}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# PNL Analytics
# =============================================================================

@app.route('/pnl/summary', methods=['GET'])
def pnl_summary():
    """Get PNL summary and trading performance metrics"""
    try:
        from .pnl_analytics import get_pnl_analytics
        pnl_analytics = get_pnl_analytics()
        
        days_back = int(request.args.get('days_back', 30))
        summary = pnl_analytics.get_pnl_summary(days_back)
        
        return jsonify(summary), 200
        
    except Exception as e:
        log_event("ERROR", f"Error getting PNL summary: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/pnl/missed-opportunities', methods=['GET'])
def missed_opportunities():
    """Get missed opportunity analysis"""
    try:
        from .pnl_analytics import get_pnl_analytics
        pnl_analytics = get_pnl_analytics()
        
        days_back = int(request.args.get('days_back', 7))
        analysis = pnl_analytics.calculate_missed_opportunity_pnl(days_back)
        
        return jsonify(analysis), 200
        
    except Exception as e:
        log_event("ERROR", f"Error getting missed opportunities: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/pnl/daily-performance', methods=['GET'])
def daily_performance():
    """Get daily performance breakdown"""
    try:
        from .pnl_analytics import get_pnl_analytics
        pnl_analytics = get_pnl_analytics()
        
        days_back = int(request.args.get('days_back', 30))
        performance = pnl_analytics.get_daily_performance(days_back)
        
        return jsonify({
            'daily_performance': performance,
            'period_days': days_back,
            'total_days': len(performance)
        }), 200
        
    except Exception as e:
        log_event("ERROR", f"Error getting daily performance: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/alerts/summary', methods=['GET'])
def alert_tracking_summary():
    """Get comprehensive alert tracking summary"""
    try:
        days_back = int(request.args.get('days_back', 7))
        
        # Get alert tracking data
        from .analytics.alert_tracker import get_comprehensive_alert_summary
        summary_data = get_comprehensive_alert_summary(days_back)
        
        return jsonify(summary_data), 200
        
    except Exception as e:
        log_event("ERROR", f"Error getting alert summary: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/ml/signal-validation', methods=['POST'])
def ml_signal_validation():
    """
    Advanced ML-based signal validation
    
    POST body:
    {
        "symbol": "HDFC",
        "alert_data": {
            "confidence": 0.8,
            "volume": 50000,
            "technical": {"rsi": 35}
        },
        "entry_price": 1500.0
    }
    
    Returns signal quality score (0.0-1.0) and validation details
    """
    try:
        data = request.get_json()
        symbol = data.get('symbol')
        alert_data = data.get('alert_data', {})
        entry_price = data.get('entry_price', 0.0)
        
        if not symbol or not entry_price:
            return jsonify({"error": "Missing symbol or entry_price"}), 400
        
        from .ml_signal_filter import validate_with_ml
        is_valid, confidence, details = validate_with_ml(symbol, alert_data, entry_price)
        
        return jsonify({
            "symbol": symbol,
            "is_valid": is_valid,
            "ml_confidence": confidence,
            "validation_details": details,
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except ImportError:
        return jsonify({
            "error": "ML module not available",
            "reason": "sklearn not installed. Run: pip install scikit-learn numpy scipy"
        }), 503
    except Exception as e:
        log_event("ERROR", f"ML validation error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/ml/statistics', methods=['GET'])
def ml_statistics():
    """Get ML module statistics and performance metrics"""
    try:
        from .ml_signal_filter import get_ml_statistics
        stats = get_ml_statistics()
        
        return jsonify({
            "ml_statistics": stats,
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except ImportError:
        return jsonify({
            "error": "ML module not available",
            "stats": None
        }), 503
    except Exception as e:
        log_event("ERROR", f"Error getting ML stats: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/ml/set-threshold', methods=['POST'])
def ml_set_threshold():
    """
    Adjust ML signal acceptance threshold dynamically
    
    POST body: {"threshold": 0.65}
    Range: 0.0 (accept all) to 1.0 (accept only best)
    """
    try:
        data = request.get_json()
        threshold = data.get('threshold', 0.6)
        
        if not 0.0 <= threshold <= 1.0:
            return jsonify({"error": "Threshold must be between 0.0 and 1.0"}), 400
        
        from .ml_signal_filter import set_ml_threshold
        set_ml_threshold(threshold)
        
        return jsonify({
            "status": "updated",
            "new_threshold": threshold,
            "message": f"ML acceptance threshold set to {threshold*100:.1f}%"
        }), 200
        
    except ImportError:
        return jsonify({"error": "ML module not available"}), 503
    except Exception as e:
        log_event("ERROR", f"Error setting ML threshold: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/ml/reset-stats', methods=['POST'])
def ml_reset_stats():
    """Reset ML module statistics"""
    try:
        from .ml_signal_filter import reset_ml_stats
        reset_ml_stats()
        
        return jsonify({
            "status": "reset",
            "message": "ML statistics reset successfully"
        }), 200
        
    except ImportError:
        return jsonify({"error": "ML module not available"}), 503
    except Exception as e:
        log_event("ERROR", f"Error resetting ML stats: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/pnl/dashboard', methods=['GET'])
def pnl_dashboard():
    """Simple HTML dashboard for PNL analysis"""
    try:
        return '''
<!DOCTYPE html>
<html>
<head>
    <title>PNL Analysis Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .card { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .header { text-align: center; color: #333; }
        .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin: 20px 0; }
        .metric { text-align: center; padding: 15px; background: #f8f9fa; border-radius: 6px; }
        .metric-value { font-size: 24px; font-weight: bold; color: #007bff; }
        .metric-label { color: #666; margin-top: 5px; }
        .positive { color: #28a745; }
        .negative { color: #dc3545; }
        table { width: 100%; border-collapse: collapse; margin: 10px 0; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background: #f8f9fa; }
        .btn { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; margin: 5px; }
        .btn:hover { background: #0056b3; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 PNL Analysis Dashboard</h1>
            <p>Trading Performance and Missed Opportunities</p>
        </div>
        
        <div class="card">
            <h3>📈 PNL Summary (Last 30 Days)</h3>
            <div id="pnlSummary">Loading...</div>
        </div>
        
        <div class="card">
            <h3>❌ Missed Opportunities (Last 7 Days)</h3>
            <div id="missedOpportunities">Loading...</div>
        </div>
        
        <div class="card">
            <h3>📅 Daily Performance</h3>
            <div id="dailyPerformance">Loading...</div>
        </div>
    </div>
    
    <script>
        // Fetch PNL Summary
        fetch('/pnl/summary?days_back=30')
            .then(response => response.json())
            .then(data => {
                const html = `
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-value ${data.total_pnl >= 0 ? 'positive' : 'negative'}">₹${data.total_pnl || 0}</div>
                            <div class="metric-label">Total PNL</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">${data.total_trades || 0}</div>
                            <div class="metric-label">Total Trades</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value ${data.win_rate >= 50 ? 'positive' : 'negative'}">${data.win_rate || 0}%</div>
                            <div class="metric-label">Win Rate</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">₹${data.avg_pnl_per_trade || 0}</div>
                            <div class="metric-label">Avg PNL/Trade</div>
                        </div>
                    </div>
                    
                    ${data.best_trade ? `
                    <h4>🏆 Best Trade</h4>
                    <p><strong>${data.best_trade.symbol}</strong>: ₹${data.best_trade.pnl} on ${new Date(data.best_trade.date).toLocaleDateString()}</p>
                    ` : ''}
                    
                    ${data.worst_trade ? `
                    <h4>📉 Worst Trade</h4>
                    <p><strong>${data.worst_trade.symbol}</strong>: ₹${data.worst_trade.pnl} on ${new Date(data.worst_trade.date).toLocaleDateString()}</p>
                    ` : ''}
                    
                    ${Object.keys(data.pnl_by_symbol || {}).length > 0 ? `
                    <h4>💼 PNL by Symbol</h4>
                    <table>
                        <tr><th>Symbol</th><th>Trades</th><th>PNL</th></tr>
                        ${Object.entries(data.pnl_by_symbol).map(([symbol, info]) => 
                            `<tr><td>${symbol}</td><td>${info.trades}</td><td class="${info.pnl >= 0 ? 'positive' : 'negative'}">₹${info.pnl}</td></tr>`
                        ).join('')}
                    </table>
                    ` : ''}
                `;
                document.getElementById('pnlSummary').innerHTML = html;
            })
            .catch(error => {
                document.getElementById('pnlSummary').innerHTML = 'Error loading PNL data';
            });
        
        // Fetch Missed Opportunities
        fetch('/pnl/missed-opportunities?days_back=7')
            .then(response => response.json())
            .then(data => {
                const html = `
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-value">${data.total_missed_opportunities || 0}</div>
                            <div class="metric-label">Missed Signals</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">${data.actual_trades_executed || 0}</div>
                            <div class="metric-label">Executed Trades</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">${data.missed_vs_executed_ratio || 0}</div>
                            <div class="metric-label">Miss/Execute Ratio</div>
                        </div>
                    </div>
                    
                    ${Object.keys(data.missed_by_reason || {}).length > 0 ? `
                    <h4>📋 Missed by Reason</h4>
                    <table>
                        <tr><th>Reason</th><th>Count</th></tr>
                        ${Object.entries(data.missed_by_reason).map(([reason, count]) => 
                            `<tr><td>${reason}</td><td>${count}</td></tr>`
                        ).join('')}
                    </table>
                    ` : ''}
                    
                    ${Object.keys(data.missed_by_symbol || {}).length > 0 ? `
                    <h4>🎯 Missed by Symbol</h4>
                    <table>
                        <tr><th>Symbol</th><th>Count</th></tr>
                        ${Object.entries(data.missed_by_symbol).map(([symbol, count]) => 
                            `<tr><td>${symbol}</td><td>${count}</td></tr>`
                        ).join('')}
                    </table>
                    ` : ''}
                `;
                document.getElementById('missedOpportunities').innerHTML = html;
            })
            .catch(error => {
                document.getElementById('missedOpportunities').innerHTML = 'Error loading missed opportunities data';
            });
        
        // Fetch Daily Performance
        fetch('/pnl/daily-performance?days_back=14')
            .then(response => response.json())
            .then(data => {
                if (data.daily_performance && data.daily_performance.length > 0) {
                    const html = `
                        <table>
                            <tr>
                                <th>Date</th>
                                <th>Trades</th>
                                <th>Win Rate</th>
                                <th>Gross PNL</th>
                                <th>Charges</th>
                                <th>Net PNL</th>
                            </tr>
                            ${data.daily_performance.map(day => `
                                <tr>
                                    <td>${day.date}</td>
                                    <td>${day.trades_count}</td>
                                    <td>${day.win_rate?.toFixed(1)}%</td>
                                    <td class="${day.gross_pnl >= 0 ? 'positive' : 'negative'}">₹${day.gross_pnl?.toFixed(2)}</td>
                                    <td>₹${day.charges?.toFixed(2)}</td>
                                    <td class="${day.net_pnl >= 0 ? 'positive' : 'negative'}">₹${day.net_pnl?.toFixed(2)}</td>
                                </tr>
                            `).join('')}
                        </table>
                    `;
                    document.getElementById('dailyPerformance').innerHTML = html;
                } else {
                    document.getElementById('dailyPerformance').innerHTML = '<p>No trading data available for the selected period.</p>';
                }
            })
            .catch(error => {
                document.getElementById('dailyPerformance').innerHTML = 'Error loading daily performance data';
            });
    </script>
</body>
</html>
        '''
        
    except Exception as e:
        log_event("ERROR", f"Error rendering PNL dashboard: {str(e)}")
        return f"Error: {str(e)}", 500


# =============================================================================
# Server Management
# =============================================================================

def start_webhook_server():
    """Start the Flask webhook server"""
    try:
        # Initialize trading system
        trading_state.initialize()
        
        # ===== START ALERT QUEUE WORKER =====
        # This runs in the background to process queued alerts
        if trading_state.alert_queue:
            try:
                import asyncio
                import threading
                
                # Create new event loop for alert queue worker
                def run_alert_queue_worker():
                    """Run alert queue in a separate asyncio event loop"""
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(trading_state.alert_queue.start())
                        log_event("ALERT_QUEUE", "Alert queue worker started successfully")
                        # Keep running until shutdown
                        loop.run_forever()
                    except Exception as e:
                        log_event("ALERT_QUEUE_ERROR", f"Error in alert queue worker: {e}")
                    finally:
                        loop.close()
                
                # Start worker thread in background (daemon)
                queue_thread = threading.Thread(target=run_alert_queue_worker, daemon=True)
                queue_thread.start()
                log_event("ALERT_QUEUE", "Alert queue worker thread started")
                
            except Exception as e:
                log_event("ALERT_QUEUE_STARTUP_ERROR", f"Failed to start alert queue worker: {e}")
        else:
            log_event("ALERT_QUEUE", "Alert queue not available - using direct processing")
        
        log_event("SERVER", f"Starting webhook server on {WebhookConfig.WEBHOOK_HOST}:{WebhookConfig.WEBHOOK_PORT}")
        
        # Start Flask server
        app.run(
            host=WebhookConfig.WEBHOOK_HOST,
            port=WebhookConfig.WEBHOOK_PORT,
            debug=False,  # Never use debug in production
            threaded=True
        )
        
    except Exception as e:
        log_event("ERROR", f"Failed to start webhook server: {str(e)}")
        raise


# =============================================================================
# Testing
# =============================================================================

if __name__ == "__main__":
    """Test webhook functionality"""
    print("=== Webhook API Test ===")
    
    # Test alert validation
    test_alert = {
        "symbol": "RELIANCE",
        "action": "BUY",
        "price": 2450.50
    }
    
    is_valid, error, processed = validate_alert(test_alert)
    if is_valid:
        print(f"✅ Alert validation passed: {processed}")
    else:
        print(f"❌ Alert validation failed: {error}")
    
    # Test position size calculation with different ML scores
    symbol = "RELIANCE-EQ"
    price = 2450.50
    
    print(f"✅ Position size calculation with dynamic capital allocation:")
    print(f"   Symbol: {symbol}")
    print(f"   Price: ₹{price}")
    
    # Test different ML scores
    for ml_score in [0.90, 0.75, 0.65, 0.58]:
        quantity, capital, charges = calculate_position_size(symbol, price, ml_score=ml_score)
        print(f"   ML={ml_score:.2f}: Qty={quantity}, Capital=₹{capital:.0f}")
    print(f"   Capital required: ₹{capital}")
    print(f"   Charges: ₹{charges}")
    
    print("\n🚀 Starting webhook server...")
    start_webhook_server()