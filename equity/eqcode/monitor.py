"""
Monitor Module - Equity Trading Bot

Position monitoring and management system.
Handles:
- Trade monitoring after order placement
- Stop-loss placement for all BUY orders
- LTP checking every 1 second
- Trailing stop-loss implementation
- Profit locking and exit signals
"""

import time
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import json

from .config import TradingConfig, CapitalConfig, BASE_DIR
from .angelone import AngelOneBroker, Order, OrderStatus
from .logging import log_event, log_trade
from .priority_queue import PriorityAPIQueue, APIPriority

# =============================================================================
# LTP BUCKET MANAGER - Reduce API calls via rotation
# =============================================================================

class APIPriorityManager:
    """
    Manages API call priority levels to maximize critical operations.
    
    Priority Levels (Highest to Lowest):
    1. CRITICAL (Immediate): BUY/SELL/MODIFY orders - Always called
    2. HIGH (Frequent): Live trade monitoring (bucketed LTP) - 5 calls/sec
    3. MEDIUM (Occasional): Paper trade monitoring - 1 call/min (~0.017 calls/sec)
    
    This ensures rate limiting doesn't affect critical trading operations.
    """
    
    def __init__(self):
        """Initialize priority manager"""
        self.last_paper_trade_check = datetime.now()
        self.paper_trade_check_interval = 60  # seconds (1 minute)
        
    def should_check_paper_trades(self) -> bool:
        """Check if enough time has passed for paper trade update"""
        now = datetime.now()
        elapsed = (now - self.last_paper_trade_check).total_seconds()
        
        if elapsed >= self.paper_trade_check_interval:
            self.last_paper_trade_check = now
            return True
        return False
    
    def get_next_paper_trade_check_in(self) -> float:
        """Get seconds until next paper trade check"""
        elapsed = (datetime.now() - self.last_paper_trade_check).total_seconds()
        return max(0, self.paper_trade_check_interval - elapsed)


class LTPBucketManager:
    """
    Manages bucketed LTP checking to reduce API calls.
    
    Instead of checking LTP for all positions every cycle,
    divide positions into buckets and rotate through them.
    
    Example: 20 positions → 4 buckets of 5 each
    Cycle 1: Check bucket 1 (5 LTP calls)
    Cycle 2: Check bucket 2 (5 LTP calls)
    Cycle 3: Check bucket 3 (5 LTP calls)
    Cycle 4: Check bucket 4 (5 LTP calls)
    Cycle 5: Back to bucket 1
    
    Result: 5 API calls/second instead of 20! ✅
    """
    
    def __init__(self, bucket_size: int = 5):
        """Initialize bucket manager"""
        self.bucket_size = bucket_size
        self.buckets: List[List[str]] = []
        self.current_bucket_index = 0
        self.last_update_time: Dict[str, datetime] = {}
    
    def create_buckets(self, symbols: List[str]):
        """Divide symbols into buckets"""
        self.buckets = []
        
        for i in range(0, len(symbols), self.bucket_size):
            bucket = symbols[i:i+self.bucket_size]
            self.buckets.append(bucket)
        
        self.current_bucket_index = 0
        
        if self.buckets:
            log_event("BUCKET_MANAGER", f"Created {len(self.buckets)} buckets",
                     bucket_size=self.bucket_size,
                     total_positions=len(symbols),
                     bucket_distribution=[len(b) for b in self.buckets])
    
    def get_current_bucket(self) -> List[str]:
        """Get current bucket and advance to next"""
        if not self.buckets:
            return []
        
        current_bucket = self.buckets[self.current_bucket_index]
        self.current_bucket_index = (self.current_bucket_index + 1) % len(self.buckets)
        
        return current_bucket
    
    def record_check(self, symbol: str):
        """Record when symbol was last checked"""
        self.last_update_time[symbol] = datetime.now()
    
    def get_check_status(self) -> Dict[str, Any]:
        """Get check status for all symbols"""
        status = {
            "total_buckets": len(self.buckets),
            "current_bucket_index": self.current_bucket_index,
            "bucket_size": self.bucket_size,
            "symbols_per_bucket": [len(b) for b in self.buckets],
            "last_checks": {}
        }
        
        now = datetime.now()
        for symbol, check_time in self.last_update_time.items():
            seconds_ago = (now - check_time).total_seconds()
            status["last_checks"][symbol] = f"{seconds_ago:.1f}s ago"
        
        return status

# Paper trading for ML learning
try:
    from .dummy_trade_tracker import get_dummy_tracker
except Exception as e:
    print(f"Warning: Could not import dummy_trade_tracker: {e}")
    get_dummy_tracker = None

# Import Week 1 dynamic risk module
try:
    from .dynamic_risk import get_risk_parameters, calculate_dynamic_stop_loss
except Exception as e:
    print(f"Warning: Could not import dynamic_risk: {e}")
    def get_risk_parameters(*args, **kwargs):
        return {}
    def calculate_dynamic_stop_loss(*args, **kwargs):
        return 0

# Import Week 3 P3.2: Performance Feedback
try:
    from .performance_feedback import PerformanceFeedback
except Exception as e:
    # 🔧 FIX GAP-005: Performance feedback is CRITICAL - log error clearly
    print(f"CRITICAL ERROR: Could not import performance_feedback: {e}")
    import sys
    sys.stderr.write(f"CRITICAL: Week 3 P3.2 (Performance Feedback) module failed to load: {e}\n")
    PerformanceFeedback = None
    _performance_feedback_import_error = True

# ===== HYBRID LEARNING: Outcome recording =====
try:
    from .hybrid_integration import finalize_trade_learning
    HYBRID_LEARNING_AVAILABLE = True
except Exception as e:
    print(f"Warning: Could not import hybrid_learning outcome recorder: {e}")
    HYBRID_LEARNING_AVAILABLE = False
    def finalize_trade_learning(*args, **kwargs):
        return {"status": "hybrid_learning_unavailable"}


# =============================================================================
# Position Class
# =============================================================================

class Position:
    """
    Represents an active trading position
    """
    
    def __init__(self, position_data: Dict[str, Any]):
        self.symbol = position_data["symbol"]
        self.action = position_data["action"]  # BUY, SELL
        self.quantity = position_data["quantity"]
        self.entry_price = position_data["entry_price"]
        self.capital_used = position_data["capital_used"]
        self.sl_price = position_data["sl_price"]
        self.order_id = position_data["order_id"]
        self.status = position_data.get("status", "PENDING")
        self.created_at = datetime.fromisoformat(position_data["created_at"])
        self.charges = position_data.get("charges", 0)
        
        # Trailing SL variables (handle both new and loaded positions)
        self.highest_price = position_data.get("highest_price", self.entry_price)
        self.trail_activated = position_data.get("trail_activated", False)
        self.trail_sl_price = position_data.get("trail_sl_price", self.sl_price)
        
        # Stepped trailing SL: track current profit milestone (in 0.5% increments)
        # This enables stepping up SL at each 0.5% profit level
        self.last_executed_step = position_data.get("last_executed_step", 0)  # e.g., 0, 1, 2, 3... (representing 0%, 0.5%, 1%, 1.5%...)
        
        # Exit tracking
        self.exit_order_id = position_data.get("exit_order_id")
        self.exit_price = position_data.get("exit_price")
        self.exit_requested_at = position_data.get("exit_requested_at")
        
        # Stop-loss order tracking
        self.sl_order_id = position_data.get("sl_order_id")
        self.sl_order_product = position_data.get("sl_order_product", "INTRADAY")  # Product type when SL was placed
        self.sl_order_price = position_data.get("sl_order_price")  # Trigger price when SL was placed
        self.sl_retry_count = position_data.get("sl_retry_count", 0)  # Track SL placement attempts
        
        # P&L tracking
        self.unrealized_pnl = 0.0
        self.realized_pnl = position_data.get("realized_pnl", 0.0)
        
        # Last update
        self.last_ltp = self.entry_price
        self.last_updated = datetime.now()
    
    def update_ltp(self, ltp: float):
        """Update last traded price and calculate metrics"""
        self.last_ltp = ltp
        self.last_updated = datetime.now()
        
        # Calculate unrealized P&L
        if self.action == "BUY":
            price_diff = ltp - self.entry_price
        else:  # SELL (short position)
            price_diff = self.entry_price - ltp
        
        self.unrealized_pnl = (price_diff * self.quantity) - self.charges
        
        # Update highest price for trailing SL (only for BUY positions)
        if self.action == "BUY" and ltp > self.highest_price:
            self.highest_price = ltp
            # Note: Stepped trailing SL is now handled by PositionMonitor._update_trailing_sl()
            # which is called every monitoring interval

    
    def should_exit_sl(self) -> bool:
        """Check if position should be exited due to stop-loss"""
        if self.action == "BUY":
            # Use trailing SL if activated, otherwise regular SL
            effective_sl = self.trail_sl_price if self.trail_activated else self.sl_price
            return self.last_ltp <= effective_sl
        else:  # SELL position
            return self.last_ltp >= self.sl_price
    
    def should_exit_profit(self) -> bool:
        """Check if position should be exited to lock profits"""
        # For now, we only use trailing SL for profit taking
        # This method can be extended for other profit-taking strategies
        return False
    
    def get_effective_sl(self) -> float:
        """Get the effective stop-loss price"""
        if self.action == "BUY" and self.trail_activated:
            return self.trail_sl_price
        return self.sl_price
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert position to dictionary"""
        return {
            "symbol": self.symbol,
            "action": self.action,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "capital_used": self.capital_used,
            "sl_price": self.sl_price,
            "order_id": self.order_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "charges": self.charges,
            "highest_price": self.highest_price,
            "trail_activated": self.trail_activated,
            "trail_sl_price": self.trail_sl_price,
            "last_executed_step": self.last_executed_step,  # Track which 0.5% step we're on
            "exit_order_id": self.exit_order_id,
            "exit_price": self.exit_price,
            "exit_requested_at": self.exit_requested_at,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "last_ltp": self.last_ltp,
            "last_updated": self.last_updated.isoformat(),
            "effective_sl": self.get_effective_sl(),
            "sl_order_id": self.sl_order_id,  # SL order ID from broker
            "sl_order_product": self.sl_order_product,  # Product type when SL was placed (INTRADAY/CNC)
            "sl_order_price": self.sl_order_price,  # Trigger price when SL was placed
            "sl_retry_count": self.sl_retry_count  # Track SL retry attempts
        }


# =============================================================================
# Position Monitor Class
# =============================================================================

class PositionMonitor:
    """
    Monitors all active positions and manages exits
    Uses ThreadPoolExecutor for parallel position monitoring (5 workers)
    """
    
    def __init__(self, broker: AngelOneBroker, capital_release_callback=None):
        self.broker = broker
        self.capital_release_callback = capital_release_callback  # Callback to release capital
        self.positions: Dict[str, Position] = {}
        self.monitoring = False
        self.monitor_thread = None
        self.lock = threading.Lock()
        
        # 🔧 CRITICAL FIX: Flag to prevent SL placement during startup
        # Only place SL orders AFTER initialization is complete
        self.startup_complete = False
        
        # Thread pool for parallel position monitoring
        self.executor = ThreadPoolExecutor(max_workers=5)
        
        # SL retry mechanism
        self.sl_retry_thread = None
        self.sl_retry_running = False
        
        # Position persistence
        self.positions_file = BASE_DIR / "data" / "positions.json"
        self.positions_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Week 3 P3.2: Performance Feedback for real-time adaptation
        self.performance_feedback = PerformanceFeedback() if PerformanceFeedback else None
        if self.performance_feedback:
            log_event("FEEDBACK_INIT", "PerformanceFeedback initialized for adaptive trading")
        
        # 🆕 PRIORITY QUEUE: Queue all API calls with priority
        # Priority 1 (Highest): Order placement from alerts
        # Priority 2 (Medium): SL placement
        # Priority 3 (Lowest): LTP checks for monitoring
        self.api_queue = PriorityAPIQueue(rate_limiter=broker.rate_limiter)
        
        # 🆕 API PRIORITY MANAGER: Prioritize critical operations
        # Priority 1 (Immediate): BUY/SELL/MODIFY orders
        # Priority 2 (Frequent): Live monitoring (5 calls/sec via bucketing)
        # Priority 3 (Rare): Paper trading (1 call/min)
        self.api_priority = APIPriorityManager()
        
        # 🆕 BUCKETED LTP CHECKING: Divide positions into buckets
        # Reduces API calls: 20 positions → 5 calls/second instead of 20/second
        bucket_size = getattr(TradingConfig, 'LTP_BUCKET_SIZE', 5)
        self.bucket_manager = LTPBucketManager(bucket_size=bucket_size)
        
        # Event loop for async queue processing
        self.loop = None
        self.loop_thread = None
        
        # Load existing positions
        self.load_positions()
        
        # Sync with broker holdings (discover positions even if positions.json was cleared)
        self._sync_with_broker_holdings()
        
        # Reconcile SL orders with broker on startup
        self._reconcile_sl_orders()
        
        # Initialize buckets after loading positions
        self._initialize_ltp_buckets()
        
        # Mark startup as complete - now SL placement is allowed
        self.startup_complete = True

    
    def save_positions(self):
        """Save positions to file"""
        try:
            positions_data = {}
            for symbol, position in self.positions.items():
                # Validate before saving: skip invalid quantities
                if position.quantity <= 0:
                    log_event("SAVE_VALIDATION_ERROR",
                             f"Refusing to save position {symbol} with invalid quantity",
                             symbol=symbol,
                             quantity=position.quantity)
                    continue
                positions_data[symbol] = position.to_dict()
            
            with open(self.positions_file, 'w') as f:
                json.dump(positions_data, f, indent=2)
                
        except Exception as e:
            log_event("ERROR", f"Failed to save positions: {str(e)}")
    
    def load_positions(self):
        """
        🔧 CRITICAL FIX: Clear stale positions.json and rely on broker as source of truth
        
        At bot startup, we CLEAR the positions.json file completely to remove any stale/phantom
        positions from previous sessions. The file will be immediately repopulated by 
        _sync_with_broker_holdings() which queries the broker's LIVE holdings.
        
        This ensures:
        1. No phantom qty=0 positions from old trades
        2. Only CURRENT broker holdings are tracked
        3. Broker is the single source of truth
        
        Previous approach: Load stale positions from file → Risk of phantom orders
        New approach: Clear file → Query broker → Only track live positions
        """
        try:
            # 🔧 CRITICAL: Clear positions.json at startup to remove stale data
            # The file WILL be repopulated immediately from broker holdings in _sync_with_broker_holdings()
            if self.positions_file.exists():
                try:
                    # Back up old file for debugging (just once)
                    import shutil
                    from datetime import datetime
                    import time as time_module
                    
                    timestamp = int(time_module.time())
                    backup_path = self.positions_file.parent / f"positions.json.startup_{timestamp}"
                    
                    # Only keep backup if we haven't backed up in last 60 seconds
                    existing_backups = list(self.positions_file.parent.glob("positions.json.startup_*"))
                    if not existing_backups or (time_module.time() - int(existing_backups[-1].name.split('_')[-1])) > 60:
                        shutil.copy2(self.positions_file, backup_path)
                        log_event("MONITOR", f"Backed up stale positions.json to {backup_path.name}")
                    
                    # CLEAR the file - broker will repopulate it
                    with open(self.positions_file, 'w') as f:
                        json.dump({}, f)
                    
                    log_event("MONITOR", "✓ Cleared stale positions.json - will rebuild from broker holdings")
                    
                except Exception as backup_error:
                    log_event("WARNING", f"Failed to backup old positions.json: {backup_error}")
                    # Continue anyway - clearing is more important than backup
                    with open(self.positions_file, 'w') as f:
                        json.dump({}, f)
            
            # Do NOT load from file - positions will be loaded from broker in next step
            log_event("MONITOR", "Skipping file load - broker will be source of truth")
                
        except Exception as e:
            log_event("ERROR", f"Failed to clear positions.json: {str(e)}")
    
    def _reconcile_sl_orders(self):
        """
        Reconcile SL orders from broker on startup to avoid duplicate placements.
        Query broker for existing STOPLOSS orders and update positions.json.
        """
        try:
            if not self.positions:
                return
            
            log_event("SL_RECONCILE", "Starting SL order reconciliation with broker")
            
            # Get all orders from broker
            order_book = self.broker.smart_api.orderBook()
            
            if not order_book or not order_book.get('data'):
                log_event("SL_RECONCILE", "No orders found in broker order book")
                return
            
            orders = order_book.get('data', [])
            reconciled_count = 0
            
            # Find pending/open STOPLOSS orders
            for order in orders:
                symbol = order.get('tradingsymbol')
                order_type = order.get('ordertype')
                status = order.get('status')
                order_id = order.get('orderid')
                transaction_type = order.get('transactiontype')
                
                # Only interested in pending/open SELL STOPLOSS orders
                if (order_type in ['STOPLOSS_MARKET', 'STOPLOSS_LIMIT'] and
                    status in ['open', 'pending', 'trigger pending', 'after market order req received'] and
                    transaction_type == 'SELL'):
                    
                    # Check if we have this position
                    if symbol in self.positions:
                        position = self.positions[symbol]
                        
                        # Only update if SL order is missing
                        if not position.sl_order_id:
                            position.sl_order_id = order_id
                            reconciled_count += 1
                            
                            log_event("SL_RECONCILED", f"Found existing SL order for {symbol}",
                                    symbol=symbol,
                                    order_id=order_id,
                                    status=status,
                                    trigger_price=order.get('triggerprice'))
            
            # Reset retry counter for positions that have no valid SL at broker
            # This allows the bot to retry SL placement after restart even if previous attempts failed
            reset_count = 0
            for symbol, position in self.positions.items():
                if (position.action == "BUY" and 
                    position.status == "OPEN" and 
                    not position.sl_order_id and 
                    position.sl_retry_count > 0):
                    
                    log_event("SL_RETRY_RESET", f"Resetting retry counter for {symbol} (no valid SL found at broker)",
                             symbol=symbol,
                             old_retry_count=position.sl_retry_count,
                             sl_price=position.sl_price)
                    position.sl_retry_count = 0
                    reset_count += 1
            
            if reconciled_count > 0 or reset_count > 0:
                # Save updated positions
                log_event("SL_RECONCILE", f"Saving reconciled positions to file...")
                self.save_positions()
                log_event("SL_RECONCILE", f"Reconciled {reconciled_count} SL orders and reset {reset_count} retry counters")
                
                # Log what was saved for verification
                for symbol, position in self.positions.items():
                    if position.sl_order_id:
                        log_event("SL_RECONCILE_VERIFY", f"{symbol}: sl_order_id={position.sl_order_id}")
            else:
                log_event("SL_RECONCILE", "No SL orders needed reconciliation")
                
        except Exception as e:
            log_event("ERROR", f"Failed to reconcile SL orders: {str(e)}")
    
    def _sync_with_broker_holdings(self):
        """
        Sync positions from broker's holdings and positions on startup.
        This ensures the bot discovers and protects existing positions even if positions.json is cleared.
        
        Tries both:
        1. position() API - intraday positions with netqty > 0
        2. holding() API - delivery holdings
        """
        try:
            log_event("HOLDINGS_SYNC", "Fetching positions and holdings from broker...")
            
            all_positions = []
            
            # Try position() API first (includes intraday)
            try:
                positions_response = self.broker.smart_api.position()
                if positions_response and positions_response.get('data'):
                    positions = positions_response.get('data', [])
                    # Filter for LONG positions only (netqty > 0, no shorts)
                    for pos in positions:
                        netqty = int(pos.get('netqty', 0))
                        if netqty > 0:  # Only LONG positions
                            # Try multiple price fields (netavgprice, buyavgprice, or calculate from netvalue)
                            avg_price = 0
                            if pos.get('netavgprice'):
                                avg_price = abs(float(pos.get('netavgprice')))
                            elif pos.get('buyavgprice'):
                                avg_price = float(pos.get('buyavgprice'))
                            elif pos.get('netvalue') and netqty > 0:
                                # Calculate from netvalue / quantity
                                avg_price = abs(float(pos.get('netvalue'))) / netqty
                            
                            all_positions.append({
                                'symbol': pos.get('tradingsymbol'),
                                'quantity': netqty,
                                'avg_price': avg_price,
                                'product_type': pos.get('producttype', 'INTRADAY')
                            })
                    log_event("HOLDINGS_SYNC", f"Found {len([p for p in all_positions])} open LONG positions from position() API")
            except Exception as e:
                log_event("HOLDINGS_SYNC_ERROR", f"Failed to fetch positions: {str(e)}")
            
            # Try holding() API (delivery holdings - long only)
            try:
                holdings_response = self.broker.smart_api.holding()
                if holdings_response and holdings_response.get('data'):
                    holdings = holdings_response.get('data', [])
                    for hold in holdings:
                        quantity = int(hold.get('quantity', 0))
                        if quantity > 0:  # Only LONG holdings
                            all_positions.append({
                                'symbol': hold.get('tradingsymbol'),
                                'quantity': quantity,
                                'avg_price': float(hold.get('averageprice', 0)),
                                'product_type': 'DELIVERY'
                            })
                    log_event("HOLDINGS_SYNC", f"Found {len(holdings)} delivery holdings from holding() API")
            except Exception as e:
                log_event("HOLDINGS_SYNC_ERROR", f"Failed to fetch holdings: {str(e)}")
            
            if not all_positions:
                log_event("HOLDINGS_SYNC", "No positions or holdings found at broker")
                return
            
            synced_count = 0
            skipped_count = 0
            
            for pos_data in all_positions:
                try:
                    symbol = pos_data.get('symbol')
                    quantity = pos_data.get('quantity', 0)
                    avg_price = pos_data.get('avg_price', 0)
                    product_type = pos_data.get('product_type', 'DELIVERY')
                    
                    # Skip if already in positions
                    if symbol in self.positions:
                        log_event("HOLDINGS_SKIP", f"Position {symbol} already exists, skipping",
                                 symbol=symbol)
                        skipped_count += 1
                        continue
                    
                    # Skip if no quantity or price (reject negative quantities)
                    if quantity <= 0 or avg_price <= 0:
                        log_event("HOLDINGS_SKIP", f"Invalid holding data for {symbol}",
                                 symbol=symbol,
                                 quantity=quantity,
                                 avg_price=avg_price,
                                 reason="NEGATIVE_OR_ZERO_QUANTITY" if quantity <= 0 else "INVALID_PRICE")
                        skipped_count += 1
                        continue
                    
                    # Calculate SL and target (using default percentages)
                    # 🔧 NEW: Initial SL = -0.5% of order price (entry_price)
                    sl_percentage = 0.5  # Hard-coded to 0.5% as per new requirement
                    # Use 1% target since we primarily use trailing SL
                    target_percentage = 1.0
                    
                    # Calculate SL price with proper NSE tick rounding (multiple of 0.05 paise)
                    sl_price_raw = avg_price * (1 - sl_percentage / 100)
                    sl_paise = int(sl_price_raw * 100)  # Convert to paise
                    sl_paise_rounded = (sl_paise // 5) * 5  # Round DOWN to nearest 5 paise
                    sl_price = sl_paise_rounded / 100.0  # Convert back to rupees
                    
                    target_price = round(avg_price * (1 + target_percentage / 100), 2)
                    
                    # Create position object
                    from datetime import datetime
                    position = Position({
                        "symbol": symbol,
                        "action": "BUY",
                        "quantity": quantity,
                        "entry_price": avg_price,
                        "current_price": avg_price,
                        "sl_price": sl_price,
                        "target_price": target_price,
                        "status": "OPEN",
                        "timestamp": time.time(),
                        "capital_used": avg_price * quantity,
                        "sl_order_id": None,  # Will be reconciled
                        "sl_retry_count": 0,
                        "product_type": product_type,
                        "order_id": f"SYNCED_{symbol}_{int(time.time())}",  # Synthetic order ID for synced positions
                        "created_at": datetime.now().isoformat(),
                        "charges": 0
                    })
                    
                    self.positions[symbol] = position
                    synced_count += 1
                    
                    log_event("HOLDINGS_SYNCED", f"Synced position from holdings: {symbol}",
                             symbol=symbol,
                             quantity=quantity,
                             entry_price=avg_price,
                             sl_price=sl_price,
                             target_price=target_price)
                    
                except Exception as e:
                    log_event("ERROR", f"Failed to sync position {pos_data.get('symbol')}: {str(e)}")
                    continue
            
            # Save synced positions
            if synced_count > 0:
                self.save_positions()
                log_event("HOLDINGS_SYNC_COMPLETE", 
                         f"Synced {synced_count} positions from broker holdings",
                         synced=synced_count,
                         skipped=skipped_count,
                         total_positions=len(self.positions))
            else:
                log_event("HOLDINGS_SYNC_COMPLETE", 
                         "No new positions to sync",
                         skipped=skipped_count)
                
        except Exception as e:
            log_event("ERROR", f"Failed to sync with broker holdings: {str(e)}")
    
    def _initialize_ltp_buckets(self):
        """Initialize LTP checking buckets from current positions"""
        symbols = list(self.positions.keys())
        if symbols:
            self.bucket_manager.create_buckets(symbols)
            log_event("LTP_BUCKETS", "LTP buckets initialized",
                     total_positions=len(symbols),
                     num_buckets=len(self.bucket_manager.buckets),
                     bucket_size=self.bucket_manager.bucket_size)
    
    def add_position(self, position_data: Dict[str, Any]) -> bool:
        """
        Add a new position to monitor
        
        Args:
            position_data: Position data dictionary
            
        Returns:
            True if added successfully, False otherwise
        """
        try:
            with self.lock:
                symbol = position_data["symbol"]
                quantity = position_data.get("quantity", 0)
                
                # Reject invalid/negative quantities to prevent malformed states
                if quantity <= 0:
                    log_event("POSITION_REJECTED", f"Cannot add position {symbol} with invalid quantity",
                             symbol=symbol,
                             quantity=quantity,
                             reason="NEGATIVE_OR_ZERO_QUANTITY",
                             action="REJECTED")
                    return False
                
                # Check if position already exists
                if symbol in self.positions:
                    existing = self.positions[symbol]
                    log_event("WARNING", f"Position already exists for {symbol}",
                             symbol=symbol,
                             existing_status=existing.status,
                             existing_quantity=existing.quantity)
                    return False
                
                # Create position object
                position = Position(position_data)
                self.positions[symbol] = position
                
                # 🔧 CRITICAL FIX #5: Save to file WITH ERROR HANDLING
                try:
                    self.save_positions()
                except Exception as save_error:
                    # If save fails, rollback the position from memory
                    del self.positions[symbol]
                    log_event("ERROR", f"CRITICAL: Failed to save position {symbol} to file: {save_error}")
                    raise Exception(f"Failed to persist position {symbol} to monitor file: {str(save_error)}")
                
                # 🆕 REBUILD BUCKETS when new position added
                self._initialize_ltp_buckets()
                
                log_event("POSITION_ADDED", f"Added position for monitoring",
                         symbol=symbol, action=position.action, quantity=position.quantity)
                
                # Place stop-loss order for BUY positions that are already OPEN (filled immediately)
                # 🔴 CRITICAL FIX: Skip initial SL placement on startup to avoid rate limiter timeouts
                # The background _sl_retry_loop will handle SL placement with proper delays
                log_event("SL_CHECK", f"Checking if should place SL for {symbol}",
                         action=position.action, status=position.status)
                if position.action == "BUY" and position.status == "OPEN":
                    log_event("SL_DEFERRED", f"SL placement deferred for {symbol} - will retry in background loop",
                             reason="Avoiding initial SL spike that causes rate limiter timeouts at startup",
                             symbol=symbol)
                    # Don't place SL here - let background retry loop handle it
                    # This prevents rate limit timeouts from multiple simultaneous placeOrder calls
                else:
                    log_event("SL_SKIPPED", f"SL not placed for {symbol}",
                             reason="action={}, status={}".format(position.action, position.status))
                
                return True
                
        except Exception as e:
            log_event("ERROR", f"Failed to add position: {str(e)}")
            return False
    
    def remove_position(self, symbol: str) -> bool:
        """
        Remove position from monitoring
        
        Args:
            symbol: Symbol to remove
            
        Returns:
            True if removed, False if not found
        """
        try:
            with self.lock:
                if symbol in self.positions:
                    position = self.positions.pop(symbol)
                    
                    # 🔧 CRITICAL FIX #5: Save to file WITH ERROR HANDLING
                    try:
                        self.save_positions()
                    except Exception as save_error:
                        # If save fails, rollback the position back to memory
                        self.positions[symbol] = position
                        log_event("ERROR", f"CRITICAL: Failed to save position removal for {symbol}: {save_error}")
                        raise Exception(f"Failed to persist position removal for {symbol}: {str(save_error)}")
                    
                    log_event("POSITION_REMOVED", f"Removed position from monitoring",
                             symbol=symbol, final_pnl=position.realized_pnl)
                    
                    return True
                else:
                    return False
                    
        except Exception as e:
            log_event("ERROR", f"Failed to remove position: {str(e)}")
            return False
    
    def update_position_status(self, symbol: str, status: str, **kwargs):
        """Update position status and additional data"""
        with self.lock:
            if symbol in self.positions:
                position = self.positions[symbol]
                position.status = status
                
                # Update additional fields
                for key, value in kwargs.items():
                    if hasattr(position, key):
                        setattr(position, key, value)
                
                # Save to file
                self.save_positions()
                
                log_event("POSITION_UPDATE", f"Updated position status",
                         symbol=symbol, status=status)
    
    def update_dynamic_stop_loss(self, symbol: str, atr: Optional[float] = None) -> bool:
        """
        Update position's stop-loss based on ATR (dynamic risk adjustment)
        
        Args:
            symbol: Position symbol
            atr: ATR value (if None, will be fetched from broker)
            
        Returns:
            True if SL was updated, False otherwise
        """
        try:
            with self.lock:
                if symbol not in self.positions:
                    return False
                
                position = self.positions[symbol]
                
                # Only update SL for BUY positions
                if position.action != "BUY":
                    return False
                
                # Get ATR if not provided
                if atr is None:
                    atr = self.broker.get_atr(symbol)
                    if atr is None:
                        log_event("WARNING", f"Could not get ATR for {symbol}, skipping SL update")
                        return False
                
                # Calculate new SL as (entry_price - 2x ATR)
                new_sl_raw = position.entry_price - (atr * 2.0)
                
                # Round to nearest 0.05 paise (NSE tick size)
                new_sl_paise = int(new_sl_raw * 100)
                new_sl_paise_rounded = (new_sl_paise // 5) * 5
                new_sl = new_sl_paise_rounded / 100.0
                
                # Only update if new SL is higher than current (protects profits)
                if new_sl > position.sl_price:
                    old_sl = position.sl_price
                    position.sl_price = new_sl
                    self.save_positions()
                    
                    log_event("SL_UPDATED", f"Dynamic SL updated for {symbol}",
                             old_sl=round(old_sl, 2),
                             new_sl=round(new_sl, 2),
                             atr=round(atr, 2),
                             entry=round(position.entry_price, 2))
                    return True
                
                return False
                
        except Exception as e:
            log_event("ERROR", f"Error updating dynamic SL for {symbol}: {str(e)}")
            return False
    
    def check_order_confirmations(self):
        """Check for order confirmations and update position status"""
        # Group positions by status to optimize API calls
        pending_positions = []
        exiting_positions = []
        
        for symbol, position in list(self.positions.items()):
            if position.status == "PENDING" and position.order_id:
                pending_positions.append((symbol, position))
            elif position.exit_order_id:
                exiting_positions.append((symbol, position))
        
        # Check entry orders (limit frequency to avoid rate limiting)
        if pending_positions:
            self._check_entry_orders(pending_positions)
        
        # Check exit orders (limit frequency)
        if exiting_positions:
            self._check_exit_orders(exiting_positions)
    
    def _check_entry_orders(self, pending_positions):
        """Check entry order confirmations with rate limiting consideration"""
        for symbol, position in pending_positions:
            try:
                order = Order(position.order_id, symbol, position.action, 
                            position.quantity, position.entry_price)
                
                if self.broker.check_order_status(order):
                    if order.status == OrderStatus.FILLED:
                        # Entry order filled - position is now OPEN
                        self.update_position_status(symbol, "OPEN")
                        
                        # Place stop-loss order for BUY positions
                        if position.action == "BUY":
                            self.place_stop_loss(position)
                        
                        log_event("ENTRY_FILLED", f"Entry order filled for {symbol}",
                                 order_id=position.order_id)
                    
                    elif order.status == OrderStatus.REJECTED:
                        # Entry order rejected - remove position
                        log_event("ENTRY_REJECTED", f"Entry order rejected for {symbol}",
                                 order_id=position.order_id, reason=order.rejection_reason)
                        self.remove_position(symbol)
                
                # Add small delay between order checks to respect rate limits
                time.sleep(0.1)  # 100ms delay
                
            except Exception as e:
                log_event("ERROR", f"Error checking entry order for {symbol}: {str(e)}")
    
    def _check_exit_orders(self, exiting_positions):
        """Check exit order confirmations with rate limiting consideration"""
        for symbol, position in exiting_positions:
            try:
                exit_order = Order(position.exit_order_id, symbol, "SELL", 
                                 position.quantity, position.last_ltp)
                
                if self.broker.check_order_status(exit_order):
                    if exit_order.status == OrderStatus.FILLED:
                        # Exit order filled - position closed
                        exit_price = exit_order.price if exit_order.price > 0 else position.last_ltp
                        self.handle_position_exit(position, exit_price, "FILLED")
                    
                    elif exit_order.status == OrderStatus.REJECTED:
                        # Exit order rejected - reset exit request
                        log_event("EXIT_REJECTED", f"Exit order rejected for {symbol}",
                                 order_id=position.exit_order_id)
                        position.exit_order_id = None
                        position.exit_requested_at = None
                        self.save_positions()
                
                # Add small delay between order checks
                time.sleep(0.1)  # 100ms delay
                
            except Exception as e:
                log_event("ERROR", f"Error checking exit order for {symbol}: {str(e)}")
    
    def sync_manual_sl_orders(self):
        """
        Detect manually-placed SL orders from broker and sync with bot state.
        
        SAFETY FALLBACK: If automated SL placement fails (rate limits, broker issues),
        user can manually place SL via broker UI. This method detects and syncs it,
        ensuring trailing SL modifications can work.
        
        Called periodically to ensure SL protection is always active.
        """
        try:
            # Only check positions that are OPEN and missing SL
            positions_missing_sl = []
            for symbol, position in self.positions.items():
                if (position.status == "OPEN" and 
                    position.action == "BUY" and 
                    not position.sl_order_id):
                    positions_missing_sl.append((symbol, position))
            
            if not positions_missing_sl:
                return  # All positions have SL or no open positions
            
            # Fetch pending SL orders from broker
            for symbol, position in positions_missing_sl:
                try:
                    pending_sl_orders = self.broker.find_pending_sl_orders(symbol)
                    
                    if pending_sl_orders:
                        # Use the first (should be only) pending SL order
                        sl_order = pending_sl_orders[0]
                        
                        # Sync SL order details with bot state
                        position.sl_order_id = sl_order.get('orderid')
                        position.sl_order_product = sl_order.get('producttype', 'INTRADAY')
                        
                        # Extract and sync SL trigger price
                        trigger_price = sl_order.get('triggerprice', 0)
                        if trigger_price > 0:
                            position.sl_price = trigger_price
                            if position.trail_sl_enabled:
                                position.trail_sl_price = trigger_price
                        
                        log_event("MANUAL_SL_DETECTED", 
                                 f"Synced manual SL for {symbol}",
                                 order_id=position.sl_order_id,
                                 trigger_price=trigger_price,
                                 product=position.sl_order_product,
                                 message="✅ Manual SL detected and synced - trailing SL now enabled")
                        
                        # Save updated position state
                        self.save_positions()
                    
                    # Small delay between broker API calls
                    time.sleep(0.2)
                    
                except Exception as e:
                    log_event("ERROR", 
                             f"Error syncing manual SL for {symbol}: {str(e)}",
                             traceback=traceback.format_exc())
            
        except Exception as e:
            log_event("ERROR", 
                     f"Error in sync_manual_sl_orders: {str(e)}",
                     traceback=traceback.format_exc())
    
    def _verify_order_status(self, order_id: str) -> str:
        """
        Verify order status from broker order book
        
        CRITICAL FIX: Wait longer (3s) for broker to accept the order before checking.
        Angel One has a processing delay of 1-2 seconds after place_order returns.
        
        Args:
            order_id: Order ID to verify
            
        Returns:
            Order status string (e.g., 'open', 'rejected', 'cancelled', etc.)
        """
        try:
            import time
            # CRITICAL FIX: Increase delay from 0.5s to 3s
            # Angel One takes 1-2 seconds to process order after place_order() returns
            # If we check too quickly (0.5s), broker shows "rejected" even though it's just processing
            # This was causing SL orders to appear rejected when they were actually pending
            time.sleep(3.0)
            
            order_book = self.broker.smart_api.orderBook()
            if not order_book or not order_book.get('data'):
                log_event("ORDER_VERIFY_FAILED", f"Cannot verify order {order_id} - order book unavailable")
                return 'unknown'
            
            for order in order_book['data']:
                if order.get('orderid') == order_id:
                    status = order.get('status', 'unknown').lower()
                    log_event("ORDER_VERIFIED", f"Order {order_id} status: {status}")
                    return status
            
            log_event("ORDER_NOT_FOUND", f"Order {order_id} not found in order book")
            return 'not_found'
            
        except Exception as e:
            log_event("ERROR", f"Failed to verify order {order_id}: {str(e)}")
            return 'unknown'
    
    def place_stop_loss(self, position: Position) -> bool:
        """
        Place stop-loss order for a position
        
        Args:
            position: Position object
            
        Returns:
            True if SL placed successfully, False otherwise
            
        NOTE: Priority queue integration available but currently disabled.
        Direct broker.place_order call respects rate limiter and gets natural priority
        over monitoring LTP checks due to bucketing strategy.
        """
        def _round_variants(price: float):
            """Yield candidate prices using integer paise arithmetic (NO float errors)"""
            from decimal import Decimal
            
            try:
                # Convert to paise (integer) - Angel One requires multiples of 5 paise
                # Use Decimal for exact conversion to avoid float errors
                price_decimal = Decimal(str(price))
                paise = int(price_decimal * 100)
                
                # Nearest: round to nearest 5 paise
                remainder = paise % 5
                if remainder < 2.5:
                    nearest_paise = paise - remainder  # Round down
                else:
                    nearest_paise = paise + (5 - remainder)  # Round up
                
                # Ceil: round up to next 5 paise
                if paise % 5 == 0:
                    ceil_paise = paise
                else:
                    ceil_paise = paise + (5 - (paise % 5))
                
                # Floor: round down to previous 5 paise
                floor_paise = paise - (paise % 5)
                
                # Convert back to rupees
                def paise_to_rupees(p):
                    return f"{p / 100:.2f}"
                
                # Return unique variants (deduplicate)
                variants = [paise_to_rupees(nearest_paise), 
                           paise_to_rupees(ceil_paise), 
                           paise_to_rupees(floor_paise)]
                
                # Remove duplicates while preserving order
                seen = set()
                unique = []
                for v in variants:
                    if v not in seen:
                        seen.add(v)
                        unique.append(v)
                
                return unique if unique else [paise_to_rupees(nearest_paise)]
            except Exception as e:
                # Fallback - try to round to nearest 5 paise
                paise = int(price * 100)
                rounded_paise = round(paise / 5) * 5
                return [f"{rounded_paise / 100:.2f}"]

        # Validate quantity first
        if not position.quantity or position.quantity <= 0:
            log_event("SL_SKIPPED_INVALID_QUANTITY",
                     f"Cannot place SL for {position.symbol} - quantity is {position.quantity}",
                     symbol=position.symbol,
                     quantity=position.quantity,
                     reason="Invalid quantity - skipping SL placement")
            return False

        # 🔧 CRITICAL FIX: Prevent SL placement outside market hours
        # This prevents phantom SL orders during bot startup before 9:15 AM
        from .config import is_market_open, get_market_status
        if not is_market_open():
            market_status = get_market_status()
            log_event("SL_BLOCKED_OUTSIDE_MARKET_HOURS",
                     f"Cannot place SL for {position.symbol} - market closed",
                     symbol=position.symbol,
                     current_time=market_status.get('current_time'),
                     market_hours=f"{market_status.get('market_open')} - {market_status.get('market_close')}")
            return False

        # 🔧 CRITICAL FIX: Prevent SL placement during startup initialization
        # Only place SL for positions created via webhook alerts, not loaded from file
        if not self.startup_complete:
            log_event("SL_BLOCKED_DURING_STARTUP",
                     f"Cannot place SL for {position.symbol} - startup in progress",
                     symbol=position.symbol,
                     reason="SL placement deferred until startup is complete and market is open")
            return False

        # 🔧 CRITICAL FIX: Use the position's producttype (INTRADAY) for SL placement
        # SL must match the BUY order's product type - DON'T rotate through different product types
        # INTRADAY BUY orders must have INTRADAY SL orders, not DELIVERY
        product = position.producttype if hasattr(position, 'producttype') else 'INTRADAY'
        
        # Only rotate price variants on retry, NOT product type
        # This ensures we use the correct product type that matches the entry order
        retry_count = getattr(position, 'sl_retry_count', 0)
        
        # Get price variants and rotate based on retry count
        price_variants = _round_variants(position.sl_price)
        price_index = retry_count % len(price_variants)
        candidate_price = price_variants[price_index]
        
        log_event("SL_PLACEMENT_STRATEGY",
                 f"Placing SL with retry {retry_count + 1} for {position.symbol}",
                 product=product,
                 price=candidate_price,
                 retry_count=retry_count,
                 strategy="same product type as entry order, rotate price rounding variants on retry")
        
        log_event("SL_ORDER_PLACING",
                 f"Placing SL-M order for {position.symbol}",
                 sl_price=position.sl_price,
                 candidate_price=candidate_price,
                 product_type=product,
                 quantity=position.quantity)

        try:
            # DETAILED DEBUG: Log before calling place_order
            log_event("SL_PLACE_ORDER_CALLING",
                     f"About to call broker.place_order for {position.symbol}",
                     symbol=position.symbol,
                     action="SELL",
                     quantity=position.quantity,
                     candidate_price=candidate_price,
                     order_type="STOPLOSS-MARKET",
                     product_type=product)
            
            sl_order = self.broker.place_order(
                symbol=position.symbol,
                action="SELL",
                quantity=position.quantity,
                price=candidate_price,
                order_type="STOPLOSS-MARKET",
                product_type=product
            )

            # DETAILED DEBUG: Log what was returned
            if sl_order:
                log_event("SL_PLACE_ORDER_SUCCESS",
                         f"broker.place_order returned Order object for {position.symbol}",
                         symbol=position.symbol,
                         order_id=getattr(sl_order, 'order_id', 'NO_ORDER_ID'),
                         order_type=type(sl_order).__name__)
            else:
                log_event("SL_PLACE_ORDER_RETURNED_NONE",
                         f"broker.place_order returned None/False for {position.symbol}",
                         symbol=position.symbol,
                         broker_last_error=getattr(self.broker, 'last_order_error', 'NONE'),
                         broker_error_code=getattr(self.broker, 'last_api_error_code', 'NONE'),
                         broker_error_message=getattr(self.broker, 'last_api_error_message', 'NONE'),
                         broker_error_time=getattr(self.broker, 'last_order_error_time', 'NONE'))

            # If order object returned, verify it's actually accepted (not rejected)
            if sl_order:
                # Verify order status from broker to ensure it's not rejected
                order_status = self._verify_order_status(sl_order.order_id)
                
                log_event("SL_ORDER_STATUS_CHECK",
                         f"SL order placed, checking status for {position.symbol}",
                         order_id=sl_order.order_id,
                         order_status=order_status,
                         expected_statuses=['open', 'pending', 'trigger pending', 'after market order req received'])
                
                if order_status in ['open', 'pending', 'trigger pending', 'after market order req received']:
                    # Order is valid - save it
                    position.sl_order_id = sl_order.order_id
                    position.sl_order_product = product
                    position.sl_order_price = candidate_price
                    log_event("SL_ORDER_PLACED",
                             f"SL order placed and verified for {position.symbol}",
                             order_id=sl_order.order_id,
                             trigger_price=candidate_price,
                             product=product,
                             order_status=order_status)
                    # Persist immediately
                    try:
                        self.save_positions()
                    except Exception:
                        pass
                    return True
                else:
                    # Order was placed but rejected/cancelled
                    log_event("SL_ORDER_REJECTED",
                             f"SL order placed but rejected by broker for {position.symbol}",
                             order_id=sl_order.order_id,
                             trigger_price=candidate_price,
                             product=product,
                             order_status=order_status,
                             reason="Order rejected after placement - will try different variant next cycle")
            else:
                # If place_order returned falsy, inspect broker last error if available
                error_msg = getattr(self.broker, 'last_order_error', None)
                error_code = getattr(self.broker, 'last_api_error_code', None)
                error_msg_full = getattr(self.broker, 'last_api_error_message', None)
                log_event("SL_ATTEMPT_FAILED",
                         f"SL order placement returned None for {position.symbol}",
                         product=product,
                         candidate_price=candidate_price,
                         last_order_error=error_msg,
                         last_api_error_code=error_code,
                         last_api_error_message=error_msg_full,
                         note="Broker may be rate-limited or session expired")

        except Exception as e:
            log_event("ERROR", f"Exception placing SL for {position.symbol}: {str(e)}",
                     product=product, candidate_price=candidate_price)
            import traceback
            log_event("DEBUG", f"Exception traceback: {traceback.format_exc()}")
        
        # Note: DO NOT increment retry_count here - the retry loop (_sl_retry_loop) handles that
        # Double-incrementing was causing retry_count to jump by 2 per attempt instead of 1
        
        log_event("SL_ATTEMPT_FAILED",
                 f"SL placement attempt failed for {position.symbol}",
                 product=product,
                 price=candidate_price)
        
        # Activate monitoring fallback
        log_event("SL_MONITORING", f"Stop-loss monitoring activated (fallback) for {position.symbol}",
                 sl_price=position.sl_price)
        return False
    
    def _sl_retry_loop(self):
        """
        Background thread that continuously retries placing SL orders
        for positions that don't have them yet.
        
        Attempts one SL order per cycle with long delays to respect
        Angel One's strict rate limits for order placement.
        """
        MAX_SL_RETRY_ATTEMPTS = 10  # Increased from 5 to 10 - give more time for SL placement with rate limit recovery
        SL_RETRY_INTERVAL = 20  # Increased from 15s to 20s - give rate limiter more time to refill between SL attempts
        
        # 🔴 CRITICAL: Wait 30 seconds before starting initial SL placement attempts
        # This prevents rate limiter timeouts by spacing out SL placements from initial startup API calls
        log_event("SL_RETRY", f"SL retry thread started - waiting 30s before initial placement (conserve startup API quota)")
        time.sleep(30)  # Wait for initial API storm to settle
        
        log_event("SL_RETRY", f"SL retry thread active - conservative mode (1 SL per {SL_RETRY_INTERVAL}s, max {MAX_SL_RETRY_ATTEMPTS} retries)")
        
        while self.sl_retry_running:
            try:
                with self.lock:
                    # Find all OPEN BUY positions without SL orders (excluding max retry exceeded)
                    positions_needing_sl = []
                    for symbol, position in self.positions.items():
                        if (position.action == "BUY" and 
                            position.status == "OPEN" and 
                            not position.sl_order_id and
                            position.sl_retry_count < MAX_SL_RETRY_ATTEMPTS):  # Only retry if under max attempts
                            positions_needing_sl.append((symbol, position))
                
                if positions_needing_sl:
                    # Only attempt ONE position per cycle to avoid rate limits
                    symbol, position = positions_needing_sl[0]
                    
                    log_event("SL_RETRY", f"Attempting SL for 1 of {len(positions_needing_sl)} positions: {symbol}",
                             sl_price=position.sl_price, quantity=position.quantity,
                             retry_attempt=position.sl_retry_count + 1,
                             max_retries=MAX_SL_RETRY_ATTEMPTS,
                             pending_count=len(positions_needing_sl))
                    
                    try:
                        # Increment attempt counter and persist before trying (visibility)
                        position.sl_retry_count += 1
                        position.sl_retry_last = datetime.now().isoformat()
                        try:
                            self.save_positions()
                        except Exception:
                            pass

                        try:
                            success = self.place_stop_loss(position)
                        except Exception as place_error:
                            log_event("ERROR", f"Exception in place_stop_loss for {symbol}: {str(place_error)}", 
                                     exception_type=type(place_error).__name__)
                            success = False
                            import traceback
                            log_event("DEBUG", f"Traceback: {traceback.format_exc()}")

                        if success:
                            log_event("SL_RETRY_SUCCESS", f"Successfully placed SL for {symbol}",
                                     order_id=position.sl_order_id,
                                     attempts=position.sl_retry_count,
                                     remaining=len(positions_needing_sl)-1)
                            # Save positions after successful SL placement
                            try:
                                self.save_positions()
                            except Exception:
                                pass
                        else:
                            if position.sl_retry_count >= MAX_SL_RETRY_ATTEMPTS:
                                log_event("SL_RETRY_EXHAUSTED",
                                         f"Stopped retrying SL for {symbol} after {MAX_SL_RETRY_ATTEMPTS} attempts",
                                         reason="Max retry limit exceeded - falling back to monitoring-based SL",
                                         last_sl_retry=position.sl_retry_last if hasattr(position, 'sl_retry_last') else 'unknown')
                                try:
                                    self.save_positions()
                                except Exception:
                                    pass
                            else:
                                log_event("SL_RETRY_FAILED", f"Failed to place SL for {symbol}, will retry next cycle",
                                         attempt=position.sl_retry_count,
                                         remaining=len(positions_needing_sl))
                        
                    except Exception as e:
                        # On exception, ensure retry counter is recorded
                        log_event("ERROR", f"Error in SL retry for {symbol}: {str(e)}", exception_type=type(e).__name__)
                        import traceback
                        log_event("DEBUG", f"Traceback: {traceback.format_exc()}")
                        try:
                            self.save_positions()
                        except Exception:
                            pass
                
                # Sleep for configured interval before next retry cycle (conservative to avoid rate limits)
                time.sleep(SL_RETRY_INTERVAL)
                
            except Exception as e:
                log_event("ERROR", f"Error in SL retry loop: {str(e)}")
                time.sleep(SL_RETRY_INTERVAL)
        
        log_event("SL_RETRY", "SL retry thread stopped")
    
    def start_sl_retry_thread(self):
        """Start the SL retry background thread"""
        if not self.sl_retry_running:
            self.sl_retry_running = True
            self.sl_retry_thread = threading.Thread(target=self._sl_retry_loop, daemon=True)
            self.sl_retry_thread.start()
            log_event("SL_RETRY", "SL retry thread started successfully")
    
    def stop_sl_retry_thread(self):
        """Stop the SL retry background thread"""
        self.sl_retry_running = False
        if self.sl_retry_thread:
            self.sl_retry_thread.join(timeout=15)
            log_event("SL_RETRY", "SL retry thread stopped")
    
    def get_positions_without_sl(self):
        """
        Get list of BUY positions that don't have SL orders placed
        
        Returns:
            List of tuples (symbol, position)
        """
        positions_without_sl = []
        with self.lock:
            for symbol, position in self.positions.items():
                if (position.action == "BUY" and 
                    position.status == "OPEN" and 
                    not position.sl_order_id):
                    positions_without_sl.append((symbol, position))
        return positions_without_sl
    
    def _check_ltp_for_bucket(self):
        """
        🆕 BUCKETED LTP CHECKING
        
        This method is the KEY to reducing API calls:
        - Gets current bucket (5 positions max)
        - Checks LTP only for those positions (direct API calls)
        - Rotates to next bucket next cycle
        
        Result: 5-10 API calls per second instead of 20+ ✅
        Each position still checked every 5 seconds (acceptable for SL) ✅
        
        NOTE: Priority queue integration is available but currently disabled.
        See priority_queue.py and PRIORITY_QUEUE_IMPLEMENTATION.md for details.
        """
        if not self.positions:
            return
        
        # Get which symbols to check this cycle
        symbols_to_check = self.bucket_manager.get_current_bucket()
        
        if not symbols_to_check:
            return
        
        try:
            bucket_number = self.bucket_manager.current_bucket_index
            total_buckets = len(self.bucket_manager.buckets)
            
            log_event("BUCKET_LTP_CHECK", f"Checking bucket {bucket_number}/{total_buckets}",
                     symbols=symbols_to_check, count=len(symbols_to_check))
            
            checked_count = 0
            skipped_count = 0
            
            for symbol in symbols_to_check:
                if symbol not in self.positions:
                    skipped_count += 1
                    continue
                
                position = self.positions[symbol]
                
                try:
                    # 🔧 RATE LIMITER FIX: Smart delay between position checks
                    # Previous: 0.25s per position = wastes 1.25s per bucket = blocks orders
                    # Without delay: 5+ LTP calls back-to-back = exhausts 6 RPS limit immediately
                    # Solution: ~0.17s delay per position = 6 calls/second (matches RPS limit exactly)
                    # This allows 5 LTP calls + 1 order call per second = no blockage
                    #
                    # With 50 positions across 10 buckets:
                    # - 0.17s × 5 positions = 0.85s per bucket (acceptable)
                    # - Still leaves 1.15s per second for order placement without blocking
                    if checked_count > 0:
                        time.sleep(0.15)  # Smart delay: 167ms between checks = ~6 req/sec
                    
                    # Get LTP for this position (direct API call)
                    ltp = self.broker.get_ltp(symbol)
                    
                    if ltp and ltp > 0:
                        # 🔧 CRITICAL FIX: Use position.update_ltp() instead of manual field updates
                        # This ensures:
                        # 1. highest_price is updated when LTP > highest_price (needed for trailing SL)
                        # 2. Trail SL activation logic is triggered
                        # 3. All position metrics are recalculated correctly
                        position.update_ltp(ltp)
                        
                        # Record that we checked this symbol
                        self.bucket_manager.record_check(symbol)
                        
                        checked_count += 1
                    else:
                        skipped_count += 1
                
                except Exception as e:
                    log_event("ERROR", f"Failed to get LTP for {symbol}: {str(e)}")
                    skipped_count += 1
                    # Keep using last known price if API fails
            
            if checked_count > 0:
                log_event("BUCKET_LTP_RESULT", 
                         f"Bucket LTP check complete",
                         checked=checked_count, skipped=skipped_count,
                         bucket_number=bucket_number, total_buckets=total_buckets)
        
        except Exception as e:
            log_event("ERROR", f"Bucket LTP check failed: {str(e)}")
    
    def _check_stop_losses(self):
        """🔧 FIX GAP-006: CRITICAL SL check that never gets skipped
        
        Enhanced to:
        1. Check if SL order is MISSING and place it immediately
        2. Implement trailing SL logic
        3. Check if SL is hit using last known LTP
        
        This method checks stop losses WITHOUT making LTP API calls.
        It uses the last known LTP from previous calls.
        This prevents SL from being skipped during rate limit backoff.
        """
        if not self.positions:
            return
        
        # DEBUG: Log entry to _check_stop_losses
        log_event("CHECK_STOP_LOSSES", f"Checking stop losses for {len(self.positions)} positions")
        
        for symbol, position in list(self.positions.items()):
            try:
                # ✅ STEP 1: Check if SL order is MISSING (let SL retry thread handle placement)
                # NOTE: We don't place SL here anymore to avoid duplicate attempts
                # The dedicated SL retry thread handles placement with proper backoff
                if (position.action == "BUY" and 
                    position.status == "OPEN" and 
                    not position.sl_order_id):
                    
                    log_event("SL_MISSING_DETECTED", f"SL order missing for {symbol} (retry thread will handle)",
                             symbol=symbol, sl_price=position.sl_price,
                             retry_count=position.sl_retry_count)
                
                # ✅ STEP 2: Implement trailing SL logic if enabled
                ltp = position.last_ltp
                if ltp is not None and TradingConfig.TRAIL_SL_ENABLED:
                    self._update_trailing_sl(position, ltp)
                
                # ✅ STEP 3: Check if SL is hit using last known price
                if ltp is None:
                    continue
                
                # Check if SL is hit using last known price
                if position.should_exit_sl():
                    log_event("SL_CHECK", f"Stop-loss hit for {symbol} (last_ltp: {ltp}, sl: {position.sl_price})")
                    # Log but don't exit here - let check_exits() handle it
                    # This is just to ensure we detect it even during rate limit backoff
                
            except Exception as e:
                log_event("ERROR", f"Error in stop-loss check for {symbol}: {str(e)}")
    
    def _update_trailing_sl(self, position: Position, current_ltp: float):
        """
        Update trailing stop-loss using stepped approach (every 0.5% profit milestone)
        
        🔴 PERMANENT STRATEGY - DO NOT MODIFY
        Decision: Dec 1, 2025 - This is the official SL strategy for all positions
        
        STRATEGY DETAILS:
        - Initial SL: entry_price * (1 - 0.5%)  [0.5% below entry]
        - Trailing SL activates IMMEDIATELY for ALL positions (from entry)
        - Every 0.5% LTP gain → Move SL up by 0.5%
        - At LTP = entry_price: SL → entry_price - 0.5% (initial, protect entry)
        - At LTP +0.5%: SL → entry_price (break-even)
        - At LTP +1.0%: SL → entry_price + 0.5%
        - At LTP +1.5%: SL → entry_price + 1.0%
        - At LTP +2.0%: SL → entry_price + 1.5%
        - Pattern: SL always stays ~0.5% below LTP (continuous profit protection)
        
        KEY POINTS (DO NOT CHANGE):
        - SL protection starts from ENTRY PRICE, not after profit
        - Every position gets trailing SL immediately
        - Step size is FIXED at 0.5% profit
        - SL increase per step is FIXED at 0.5%
        - Buffer distance from LTP is FIXED at ~0.5%
        
        Args:
            position: Position object
            current_ltp: Current LTP
        """
        try:
            # DEBUG: Log all trailing SL checks (temporary, for verification)
            profit_percentage = ((current_ltp - position.entry_price) / position.entry_price) * 100
            log_event("TRAIL_DEBUG", f"Trailing SL check for {position.symbol}",
                     symbol=position.symbol, ltp=current_ltp, entry=position.entry_price,
                     profit_pct=profit_percentage, trail_activated=position.trail_activated)
            
            # Allow trailing for ALL prices, not just > entry_price
            # SL can still protect even if price dips slightly below entry
            if current_ltp < position.entry_price * 0.99:  # Only skip if down > 1%
                # Severe downside, don't update trailing SL
                return
            
            step_size = 0.5  # Every 0.5% profit milestone
            
            # Calculate which step we should be on
            # Step 0: -0.5% to +0.499% profit (initial state)
            # Step 1: +0.5% to +0.999% profit
            # Step 2: +1.0% to +1.499% profit
            # Step N: N * 0.5%+ profit
            current_step = max(0, int(profit_percentage / step_size))  # Allow step 0
            
            if not position.trail_activated:
                position.trail_activated = True
                position.last_executed_step = -1  # Start at step -1 (before step 0)
                position.last_trail_update = datetime.now().isoformat()
                log_event("TRAIL_ACTIVATED", f"Trailing SL activated for {position.symbol}",
                         symbol=position.symbol,
                         profit_pct=profit_percentage,
                         current_ltp=current_ltp,
                         entry_price=position.entry_price,
                         message="✅ Trailing SL activated - will step up 0.5% for every 0.5% LTP gain")
            
            # Check if we've crossed into a new 0.5% step
            if current_step <= getattr(position, 'last_executed_step', -1):
                # Already processed this step
                return
            
            # Calculate new SL for this step
            # 🔧 CORRECTED FORMULA: SL = entry_price - 0.5% + (current_step * 0.5%)
            # Step -1: Entry < 0% → SL = entry_price - 0.5% (initial)
            # Step 0: 0% to <0.5% → SL = entry_price - 0.5% (initial)
            # Step 1: 0.5% to <1.0% → SL = entry_price (break-even)
            # Step 2: 1.0% to <1.5% → SL = entry_price + 0.5%
            # Step 3: 1.5% to <2.0% → SL = entry_price + 1.0%
            # Step N: SL = entry_price - 0.5% + (N * 0.5%)
            sl_profit_percentage = -0.5 + (current_step * step_size)  # -0.5 + (step * 0.5)
            new_trail_sl_raw = position.entry_price * (1 + sl_profit_percentage / 100.0)
            
            # Round to nearest 0.05 paise (NSE tick size)
            new_trail_sl_paise = int(new_trail_sl_raw * 100)
            new_trail_sl_paise_rounded = (new_trail_sl_paise // 5) * 5
            new_trail_sl = new_trail_sl_paise_rounded / 100.0
            
            # Only update if new SL is better (higher) than current SL
            current_sl = getattr(position, 'trail_sl_price', position.sl_price)
            
            # 🔧 DEBUG: Log the trailing SL calculation
            ltp_distance_pct = ((current_ltp - new_trail_sl) / current_ltp) * 100
            log_event("TRAIL_SL_CALCULATION", f"Trailing SL calculation for {position.symbol}",
                     symbol=position.symbol,
                     step=current_step,
                     profit_pct=profit_percentage,
                     current_ltp=current_ltp,
                     entry_price=position.entry_price,
                     calculated_sl=new_trail_sl,
                     distance_below_ltp_pct=ltp_distance_pct,
                     formula=f"entry_price ({position.entry_price}) - 0.5% + (step * 0.5%) = {new_trail_sl}")
            
            if new_trail_sl <= current_sl:
                # New SL is not better, skip
                return
            
            # Update position with new trailing SL
            old_sl = current_sl
            position.trail_sl_price = new_trail_sl
            position.last_executed_step = current_step
            position.last_trail_update = datetime.now().isoformat()
            
            log_event("TRAIL_SL_STEPPED", f"Trailing SL stepped up for {position.symbol}",
                     symbol=position.symbol,
                     step=current_step,
                     profit_pct=profit_percentage,
                     old_sl=old_sl,
                     new_sl=new_trail_sl,
                     current_ltp=current_ltp,
                     distance_from_ltp_pct=((current_ltp - new_trail_sl) / current_ltp) * 100,
                     message="🔄 Stepped SL: +0.5% for every +0.5% LTP gain")
            
            # 🔧 CRITICAL FIX: Check if SL order exists before trying to modify
            # If SL order is missing, log warning and don't update local state
            # This prevents LOCAL/BROKER inconsistency
            if not position.sl_order_id:
                log_event("TRAIL_SL_SKIP_NO_ORDER", f"Trailing SL update skipped - SL order missing for {position.symbol}",
                         symbol=position.symbol,
                         step=current_step,
                         new_sl=new_trail_sl,
                         current_ltp=current_ltp,
                         profit_pct=profit_percentage,
                         reason="SL order not yet placed - cannot modify non-existent order")
                # REVERT the local update since we can't modify on broker
                position.trail_sl_price = old_sl
                position.last_executed_step = getattr(position, 'last_executed_step', 0)
                return
            
            # 🔧 CRITICAL FIX: Double-check SL product type exists (set during placement)
            # If SL was placed but product_type wasn't saved, we can't safely modify
            product_type = getattr(position, 'sl_order_product', None)
            if not product_type:
                log_event("TRAIL_SL_SKIP_MISSING_PRODUCT", f"Trailing SL update skipped - SL product type missing for {position.symbol}",
                         symbol=position.symbol,
                         step=current_step,
                         order_id=position.sl_order_id,
                         reason="SL product type not stored - cannot safely modify")
                # REVERT the local update
                position.trail_sl_price = old_sl
                position.last_executed_step = getattr(position, 'last_executed_step', 0)
                return
            
            # 🔧 CRITICAL FIX: Actually modify the SL order on broker
            if position.sl_order_id:
                # Get order details stored when SL was placed
                order_type = "STOPLOSS_MARKET"  # SL orders are placed as STOPLOSS-MARKET
                product_type = getattr(position, 'sl_order_product', 'INTRADAY')
                
                success = self.broker.modify_order(
                    order_id=position.sl_order_id,
                    symbol=position.symbol,
                    quantity=position.quantity,
                    price=new_trail_sl,
                    order_type=order_type,           # ✅ Pass order type
                    product_type=product_type        # ✅ Pass product type
                )
                
                if success:
                    log_event("TRAIL_SL_MODIFIED", f"Trailing SL modified on broker for {position.symbol}",
                             symbol=position.symbol,
                             step=current_step,
                             order_id=position.sl_order_id,
                             old_sl=old_sl,
                             new_sl=new_trail_sl,
                             current_ltp=current_ltp,
                             profit_pct=profit_percentage,
                             order_type=order_type,
                             product=product_type)
                    # 🔧 CRITICAL FIX: Persist updated trailing SL to file
                    # Without this, if bot crashes, trailing SL modifications are lost
                    try:
                        self.save_positions()
                    except Exception:
                        pass
                else:
                    log_event("TRAIL_SL_MODIFY_FAILED", f"Failed to modify SL order on broker for {position.symbol}",
                             symbol=position.symbol,
                             step=current_step,
                             order_id=position.sl_order_id,
                             new_sl=new_trail_sl,
                             order_type=order_type,
                             product=product_type)
                    # 🔧 CRITICAL FIX: Revert local state if broker modification fails
                    # This prevents LOCAL/BROKER inconsistency
                    position.trail_sl_price = old_sl
                    position.last_executed_step = getattr(position, 'last_executed_step', 0)
                    log_event("TRAIL_SL_REVERTED", f"Reverted trailing SL update due to broker failure for {position.symbol}",
                             symbol=position.symbol,
                             reverted_to=old_sl)
        
        except Exception as e:
            log_event("ERROR", f"Error updating trailing SL for {position.symbol}: {str(e)}")
    
    def check_exits(self):
        """
        Check exit conditions for all positions using parallel monitoring with ThreadPoolExecutor
        """
        from .bot_logging import log_monitor, log_error
        
        if not self.positions:
            return
        
        log_monitor("CHECKING_EXITS", details={"active_positions": len(self.positions)})
        
        # Get list of open positions that need checking
        positions_to_check = []
        for symbol, position in list(self.positions.items()):
            if position.status == "OPEN" and not position.exit_order_id:
                positions_to_check.append((symbol, position))
        
        if not positions_to_check:
            return
        
        # Check positions sequentially with small delays to respect rate limits
        # Sequential checking with 0.2s delay = 5 positions/second = well within 8 RPS limit
        import time
        for i, (symbol, position) in enumerate(positions_to_check):
            try:
                self._check_single_position_exit(symbol, position)
                # Add small delay between checks (except for last position)
                if i < len(positions_to_check) - 1:
                    time.sleep(0.2)  # 200ms delay = max 5 checks/sec
            except Exception as e:
                log_event("ERROR", f"Error checking position {symbol}: {str(e)}")
        
        log_monitor("EXIT_CHECK_COMPLETE", details={"positions_checked": len(positions_to_check)})
    
    def _check_single_position_exit(self, symbol: str, position: Position) -> None:
        """
        Check exit conditions for a single position using LAST KNOWN LTP from bucket check
        
        CRITICAL: This method no longer calls broker.get_ltp() to avoid duplicate API calls.
        It uses position.last_ltp that was updated by _check_ltp_for_bucket().
        This eliminates duplicate LTP calls and respects the bucketing strategy.
        
        Args:
            symbol: Position symbol
            position: Position object
        """
        from .bot_logging import log_monitor, log_error
        
        try:
            # 🔧 FIX: Use last known LTP from bucket check instead of calling API again
            # This eliminates duplicate LTP calls (was causing 2x API usage)
            ltp = position.last_ltp
            
            # Check if LTP is stale (older than 60 seconds) - if so, skip this check
            ltp_age_seconds = (datetime.now() - position.last_updated).total_seconds() if hasattr(position, 'last_updated') else 999
            
            if ltp is None or ltp_age_seconds > 60:
                # LTP is stale or unavailable, skip this check
                log_monitor("LTP_STALE", symbol, details={
                    "reason": "LTP not yet updated by bucket check or too old",
                    "ltp_age_seconds": ltp_age_seconds,
                    "using_entry_price": position.entry_price
                })
                # Use entry price as fallback for very conservative decisions only
                return
            
            # Calculate PnL and percentages using last known LTP
            pnl = (ltp - position.entry_price) * position.quantity
            pnl_percent = ((ltp - position.entry_price) / position.entry_price) * 100
            
            # Note: position.update_ltp(ltp) already called by bucket check, no need to call again
            
            # Log detailed position monitoring
            log_monitor("PRICE_CHECK", symbol, ltp, pnl, pnl_percent,
                       target_price=position.entry_price * 1.01,  # 1% target
                       stop_loss=position.sl_price,
                       details={
                           "entry_price": position.entry_price,
                           "quantity": position.quantity,
                           "capital_used": position.capital_used,
                           "highest_price": getattr(position, 'highest_price', position.entry_price),
                           "trail_activated": getattr(position, 'trail_activated', False),
                           "ltp_age_seconds": ltp_age_seconds,
                           "ltp_from_bucket": True  # Indicates LTP came from bucket check, not fresh API call
                       })
            
            # Check for exit conditions
            exit_reason = None
            decision_details = {}
            
            if position.should_exit_sl():
                exit_reason = "SL_HIT"
                decision_details = {
                    "sl_price": position.sl_price,
                    "current_ltp": ltp,
                    "trail_sl_price": getattr(position, 'trail_sl_price', position.sl_price)
                }
                
                # 🎯 SL_HIT MARKER - ML Training Marker #3
                # This explicit marker captures exit via stop loss (risk management)
                hold_duration = (datetime.now() - position.created_at).total_seconds()
                log_event("SL_HIT", 
                         f"🛑 STOP LOSS HIT | {symbol} | Entry: ₹{position.entry_price:.2f} | Exit: ₹{ltp:.2f} | SL: ₹{position.sl_price:.2f} | PnL: ₹{pnl:.2f}",
                         symbol=symbol,
                         exit_reason="SL_HIT",
                         entry_price=position.entry_price,
                         exit_price=ltp,
                         sl_price=position.sl_price,
                         quantity=position.quantity,
                         pnl=round(pnl, 2),
                         pnl_percent=round(pnl_percent, 2),
                         charges=getattr(position, 'charges', 0),
                         trade_id=getattr(position, 'trade_id', 0),
                         duration_seconds=hold_duration,
                         duration_minutes=round(hold_duration / 60, 1))
                         
            elif position.should_exit_profit():
                exit_reason = "PROFIT_BOOK"
                decision_details = {
                    "profit_target": position.entry_price * 1.01,  # 1% target
                    "current_ltp": ltp,
                    "achieved_profit_percent": pnl_percent
                }
                
                # 🎯 SELL MARKER - ML Training Marker #4
                # This explicit marker captures exit via manual signal or profit target
                hold_duration = (datetime.now() - position.created_at).total_seconds()
                log_event("SELL", 
                         f"💰 SELL EXECUTED | {symbol} | Entry: ₹{position.entry_price:.2f} | Exit: ₹{ltp:.2f} | Profit: ₹{pnl:.2f} ({pnl_percent:.2f}%)",
                         symbol=symbol,
                         exit_reason="PROFIT_BOOK",
                         entry_price=position.entry_price,
                         exit_price=ltp,
                         quantity=position.quantity,
                         pnl=round(pnl, 2),
                         pnl_percent=round(pnl_percent, 2),
                         charges=getattr(position, 'charges', 0),
                         trade_id=getattr(position, 'trade_id', 0),
                         duration_seconds=hold_duration,
                         duration_minutes=round(hold_duration / 60, 1))
            
            # Log exit decision
            if exit_reason:
                log_monitor("EXIT_DECISION", symbol, ltp, pnl, pnl_percent,
                           decision=exit_reason, details=decision_details)
                
                # Place exit order if needed
                self.exit_position(position, exit_reason)
            else:
                # Log holding decision for key positions
                if abs(pnl_percent) > 0.5:  # Log if position has moved >0.5%
                    log_monitor("HOLDING", symbol, ltp, pnl, pnl_percent,
                               decision="CONTINUE_HOLDING", 
                               details={
                                   "sl_distance": abs(ltp - position.sl_price),
                                   "target_distance": abs(ltp - (position.entry_price * 1.01))
                               })
            
        except Exception as e:
            log_error("MONITOR_CHECK_EXIT", f"Error checking exit for {symbol}", e,
                     context={"symbol": symbol, "position_status": getattr(position, 'status', 'unknown')},
                     recovery_action="Continuing with next position")
            log_event("ERROR", f"Error checking exit for {symbol}: {str(e)}")
    
    def exit_position(self, position: Position, reason: str) -> bool:
        """
        Exit a position with comprehensive logging for autonomous debugging
        
        Args:
            position: Position to exit
            reason: Exit reason (SL_HIT, PROFIT_BOOK, MANUAL, etc.)
            
        Returns:
            True if exit order placed, False otherwise
        """
        from .bot_logging import log_monitor, log_order, log_error
        
        try:
            symbol = position.symbol
            current_ltp = getattr(position, 'last_ltp', position.entry_price)
            pnl = (current_ltp - position.entry_price) * position.quantity
            pnl_percent = ((current_ltp - position.entry_price) / position.entry_price) * 100
            
            # Log exit initiation
            log_monitor("EXIT_INITIATED", symbol, current_ltp, pnl, pnl_percent,
                       decision=reason, details={
                           "entry_price": position.entry_price,
                           "quantity": position.quantity,
                           "capital_used": position.capital_used,
                           "sl_price": position.sl_price,
                           "hold_duration_minutes": (datetime.now() - position.created_at).total_seconds() / 60
                       })
            
            # 🔧 CRITICAL FIX: Cancel existing SL order before placing exit order
            # This prevents DOUBLE FILLS when SL order is triggered at same time as exit decision
            # BUG: When SL is hit, both SL order AND new market order can fill, creating negative shares
            if hasattr(position, 'sl_order_id') and position.sl_order_id:
                log_event("SL_ORDER_CANCEL_ATTEMPT", 
                         f"Cancelling SL order before exit for {symbol}",
                         sl_order_id=position.sl_order_id,
                         reason=reason)
                try:
                    cancel_success = self.broker.cancel_order(position.sl_order_id)
                    if cancel_success:
                        log_event("SL_ORDER_CANCELLED",
                                 f"SL order cancelled successfully for {symbol}",
                                 sl_order_id=position.sl_order_id,
                                 reason=reason)
                        position.sl_order_id = None  # Clear SL order ID
                    else:
                        log_event("SL_ORDER_CANCEL_FAILED",
                                 f"Failed to cancel SL order for {symbol}",
                                 sl_order_id=position.sl_order_id,
                                 reason="Broker cancel returned False")
                except Exception as e:
                    log_event("SL_ORDER_CANCEL_ERROR",
                             f"Exception while cancelling SL order for {symbol}: {str(e)}",
                             sl_order_id=position.sl_order_id,
                             error=str(e))
            
            # Check if we can place exit order
            can_exit, check_reason = self.broker.can_place_order(symbol, "SELL")
            if not can_exit:
                log_monitor("EXIT_BLOCKED", symbol, current_ltp, pnl, pnl_percent,
                           decision="BLOCKED", details={"block_reason": check_reason})
                log_event("EXIT_BLOCKED", f"Cannot exit {symbol}: {check_reason}")
                return False
            
            # Place SELL order
            exit_order = self.broker.place_order_safe(
                symbol=symbol,
                action="SELL",
                quantity=position.quantity,
                price=0  # Market order
            )
            
            if exit_order:
                # Update position with exit order details
                self.update_position_status(
                    symbol, 
                    "EXITING",
                    exit_order_id=exit_order.order_id,
                    exit_requested_at=datetime.now().isoformat()
                )
                
                # Comprehensive exit logging
                log_monitor("EXIT_ORDER_PLACED", symbol, current_ltp, pnl, pnl_percent,
                           decision=reason, details={
                               "exit_order_id": exit_order.order_id,
                               "expected_pnl": pnl,
                               "expected_pnl_percent": pnl_percent,
                               "hold_duration_minutes": (datetime.now() - position.created_at).total_seconds() / 60,
                               "exit_price": current_ltp,
                               "original_target": position.entry_price * 1.01,
                               "sl_price": position.sl_price
                           })
                
                log_event("EXIT_PLACED", f"Exit order placed for {symbol}",
                         reason=reason, order_id=exit_order.order_id, ltp=current_ltp)
                
                return True
            else:
                log_monitor("EXIT_FAILED", symbol, current_ltp, pnl, pnl_percent,
                           decision="ORDER_FAILED", details={"reason": "Failed to place SELL order"})
                log_event("ERROR", f"Failed to place exit order for {symbol}")
                return False
                
        except Exception as e:
            log_error("EXIT_POSITION_EXCEPTION", f"Exception while exiting {position.symbol}", e,
                     context={
                         "symbol": position.symbol,
                         "reason": reason,
                         "position_status": position.status,
                         "entry_price": position.entry_price,
                         "quantity": position.quantity
                     },
                     recovery_action="Exit attempt failed, position remains open")
            
            log_event("ERROR", f"Exception while exiting {position.symbol}: {str(e)}")
            return False
    
    def handle_position_exit(self, position: Position, exit_price: float, exit_status: str):
        """
        Handle position exit completion
        
        Args:
            position: Position that was exited
            exit_price: Exit price
            exit_status: Exit status (FILLED, CANCELLED, etc.)
        """
        try:
            symbol = position.symbol
            
            # Calculate realized P&L
            if position.action == "BUY":
                price_diff = exit_price - position.entry_price
            else:  # SELL position
                price_diff = position.entry_price - exit_price
            
            realized_pnl = (price_diff * position.quantity) - position.charges
            
            # Update position
            position.exit_price = exit_price
            position.realized_pnl = realized_pnl
            position.status = "CLOSED"
            
            # Update PNL tracking for completed trade
            try:
                # Get trade_id from position data if available
                trade_id = getattr(position, 'trade_id', None) or position.data.get('trade_id', 0)
                
                if trade_id and trade_id > 0:
                    from .pnl_analytics import get_pnl_analytics
                    pnl_analytics = get_pnl_analytics()
                    
                    # Calculate total charges (entry + exit)
                    total_charges = position.charges + (exit_price * position.quantity * 0.001)  # Rough exit charges
                    
                    success = pnl_analytics.update_trade_exit(
                        trade_id=trade_id,
                        exit_price=exit_price,
                        charges=total_charges
                    )
                    
                    if success:
                        log_event("PNL_TRACKING", f"Updated PNL tracking for {symbol} (trade_id: {trade_id})")
                    else:
                        log_event("WARNING", f"Failed to update PNL tracking for {symbol} (trade_id: {trade_id})")
                else:
                    log_event("WARNING", f"No trade_id found for {symbol} - PNL tracking update skipped")
                    
            except Exception as e:
                log_event("ERROR", f"Failed to update PNL tracking for {symbol}: {e}")
            
            # Log trade closure
            log_trade(
                action="SELL",
                symbol=symbol,
                quantity=position.quantity,
                entry_price=position.entry_price,
                exit_price=exit_price,
                capital_used=position.capital_used,
                sl_price=position.get_effective_sl(),
                pnl=realized_pnl,
                status="CLOSED"
            )
            
            log_event("POSITION_CLOSED", f"Position closed for {symbol}",
                     entry_price=position.entry_price, exit_price=exit_price,
                     pnl=realized_pnl, duration_minutes=int((datetime.now() - position.created_at).total_seconds() / 60))
            
            # ===== WEEK 3 P3.2: Track performance for adaptive trading =====
            if self.performance_feedback:
                try:
                    pnl_percent = (realized_pnl / position.capital_used * 100) if position.capital_used > 0 else 0
                    self.performance_feedback.record_trade(
                        symbol=symbol,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        pnl_percent=pnl_percent,
                        duration_minutes=int((datetime.now() - position.created_at).total_seconds() / 60)
                    )
                    win_rate = self.performance_feedback.get_win_rate()
                    win_rate_str = f'{win_rate:.1f}%'
                    log_event("FEEDBACK_RECORDED", f"Trade performance recorded for {symbol}",
                             pnl_percent=pnl_percent, win_rate=win_rate_str)
                except Exception as e:
                    log_event("FEEDBACK_ERROR", f"Failed to record trade performance for {symbol}: {e}")
            
            # ===== HYBRID LEARNING: Record trade outcome for learning =====
            if HYBRID_LEARNING_AVAILABLE:
                try:
                    won = realized_pnl > 0
                    pnl_percent = (realized_pnl / position.capital_used * 100) if position.capital_used > 0 else 0
                    
                    # Record this trade outcome for hybrid learning
                    result = finalize_trade_learning(
                        alert_id=position.alert_id if hasattr(position, 'alert_id') else f"{symbol}_{int(position.created_at.timestamp())}",
                        won=won,
                        profit=realized_pnl
                    )
                    
                    log_event("LEARNING_OUTCOME_RECORDED", f"Trade outcome recorded for {symbol}",
                             symbol=symbol, won=won, profit=realized_pnl, pnl_percent=f"{pnl_percent:.2f}%")
                except Exception as e:
                    # Never block position closing for learning
                    log_event("LEARNING_OUTCOME_ERROR", f"Failed to record learning outcome for {symbol}: {e}")
            
            # Remove from active monitoring
            self.remove_position(symbol)
            
            # Release capital through callback
            if self.capital_release_callback:
                try:
                    self.capital_release_callback(symbol, position.capital_used)
                except Exception as e:
                    # 🔧 FIX #6: Protect callback from failures
                    log_event("ERROR", f"CRITICAL: Capital release callback failed for {symbol}: {str(e)}")
                    # Capital must be released even if callback fails
                    # This is a critical error that should be logged for debugging
            else:
                log_event("WARNING", f"No capital release callback set - capital not released for {symbol}")
            
        except Exception as e:
            log_event("ERROR", f"Error handling position exit for {position.symbol}: {str(e)}")
    
    def get_positions_summary(self) -> Dict[str, Any]:
        """Get summary of all positions"""
        with self.lock:
            total_positions = len(self.positions)
            total_unrealized_pnl = sum(pos.unrealized_pnl for pos in self.positions.values())
            total_capital_used = sum(pos.capital_used for pos in self.positions.values())
            
            positions_by_status = {}
            for position in self.positions.values():
                status = position.status
                if status not in positions_by_status:
                    positions_by_status[status] = 0
                positions_by_status[status] += 1
            
            return {
                "total_positions": total_positions,
                "total_unrealized_pnl": total_unrealized_pnl,
                "total_capital_used": total_capital_used,
                "positions_by_status": positions_by_status,
                "last_updated": datetime.now().isoformat()
            }
    
    def _update_dummy_trades(self):
        """
        Update dummy trades with current prices (for ML learning)
        Called every monitoring cycle to track rejected signals
        """
        if not get_dummy_tracker:
            return
        
        try:
            tracker = get_dummy_tracker()
            active_dummy_ids = tracker.get_active_dummies()
            
            if not active_dummy_ids:
                return
            
            log_event("DUMMY_TRADE_UPDATE", f"Updating {len(active_dummy_ids)} active dummy trades")
            
            # Update each dummy trade with current LTP
            for dummy_id in active_dummy_ids:
                dummy = tracker.get_dummy_trade(dummy_id)
                if not dummy:
                    continue
                
                symbol = dummy.get('symbol')
                
                try:
                    # Get current LTP
                    ltp = self.broker.get_ltp(symbol)
                    
                    if ltp is None:
                        # LTP not available, skip this update
                        continue
                    
                    # Update dummy trade with new price
                    exit_reason = tracker.update_dummy_price(dummy_id, ltp)
                    
                    if exit_reason:
                        # Dummy trade closed
                        closed_dummy = tracker.get_dummy_trade(dummy_id)
                        log_event("DUMMY_TRADE_CLOSED",
                                 f"Dummy trade {dummy_id} closed",
                                 symbol=symbol,
                                 entry_price=closed_dummy.get('entry_price'),
                                 exit_price=closed_dummy.get('exit_price'),
                                 outcome=closed_dummy.get('outcome'),
                                 reason=exit_reason)
                        
                        # ===== HYBRID LEARNING: Record dummy trade outcome =====
                        if HYBRID_LEARNING_AVAILABLE:
                            try:
                                outcome = closed_dummy.get('outcome', {})
                                won = outcome.get('won', False)
                                profit = outcome.get('pnl', 0)
                                
                                result = finalize_trade_learning(
                                    alert_id=dummy_id,
                                    won=won,
                                    profit=profit
                                )
                                
                                log_event("LEARNING_DUMMY_RECORDED", f"Paper trade outcome recorded",
                                         dummy_id=dummy_id, symbol=symbol, won=won, profit=profit)
                            except Exception as e:
                                log_event("LEARNING_DUMMY_ERROR", f"Failed to record paper trade outcome: {e}")
                
                except Exception as e:
                    log_event("DUMMY_UPDATE_ERROR", f"Error updating dummy {dummy_id}: {e}")
        
        except Exception as e:
            log_event("DUMMY_MONITOR_ERROR", f"Error in dummy trade monitoring: {e}")
    
    def start_monitoring(self):
        """Start the monitoring loop with bucketed LTP checking
        
        NOTE: Priority queue implementation is available in priority_queue.py but currently
        disabled to simplify integration. The bucketed LTP checking (75% reduction in API calls)
        combined with adaptive monitoring intervals provides sufficient rate limit protection.
        
        Priority queue can be enabled in future by:
        1. Uncommenting event loop and queue startup code below
        2. Uncommenting queue-based LTP checking in _check_ltp_for_bucket()
        3. Uncommenting queue-based SL placement in place_stop_loss()
        """
        self.monitoring = True
        log_event("MONITOR", "Position monitoring started with bucketed LTP checking")
        
        # Priority queue: start event loop and api queue if enabled in config
        # Using the priority queue ensures ORDER_PLACEMENT and SL_PLACEMENT
        # tasks get priority over LTP checks and other low-priority calls.
        try:
            if getattr(TradingConfig, 'ENABLE_API_QUEUE', True):
                self._start_event_loop()
                time.sleep(0.5)
                if self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.api_queue.start(), self.loop)
                    log_event("MONITOR", "Priority API queue enabled and started")
                else:
                    log_event("MONITOR", "Failed to start event loop for priority queue; continuing with direct calls")
            else:
                log_event("MONITOR", "Priority API queue disabled via config; using direct API calls")
        except Exception as e:
            log_event("MONITOR", f"Error starting priority API queue: {e}; continuing with direct calls")
        
        # Start the SL retry background thread
        self.start_sl_retry_thread()
        
        consecutive_rate_limit_errors = 0
        base_interval = TradingConfig.MONITOR_INTERVAL_SECONDS
        
        while self.monitoring:
            try:
                # 🔥 CRITICAL: Process any queued requests from rate limiting FIRST
                # This ensures rate-limited orders are automatically retried
                # This is what prevents the 11 order losses we saw today
                if self.broker and hasattr(self.broker, 'process_pending_rate_limited_requests'):
                    self.broker.process_pending_rate_limited_requests()
                
                # Check rate limiter status and adjust monitoring frequency
                if self.broker and hasattr(self.broker, 'get_rate_limiter_stats'):
                    rate_stats = self.broker.get_rate_limiter_stats()
                    
                    # If rate limiter is highly utilized, slow down monitoring
                    second_utilization = rate_stats.get('second_bucket', {}).get('utilization', 0)
                    minute_utilization = rate_stats.get('minute_bucket', {}).get('utilization', 0)
                    
                    # Adaptive interval based on utilization
                    # CRITICAL: More aggressive back-off to give placeOrder calls priority
                    if second_utilization > 60 or minute_utilization > 65:
                        # Very high utilization - PAUSE monitoring completely to give order placement priority
                        # Skip the entire monitoring cycle - check again after 30 seconds
                        monitor_interval = 30  # Skip monitoring for 30 seconds
                        if consecutive_rate_limit_errors < 5:  # Log only first 5 times to avoid spam
                            log_event("MONITOR", "🚨 CRITICAL: Rate limit CRITICAL - pausing ALL monitoring for 30s to prioritize BUY order placement")
                        consecutive_rate_limit_errors += 1
                    elif second_utilization > 50 or minute_utilization > 55:
                        # High utilization - slow down significantly
                        monitor_interval = base_interval * 4  # Use 20s instead of base_interval
                        if consecutive_rate_limit_errors < 2:
                            log_event("MONITOR", "⚠️  High rate limit utilization, reducing monitoring frequency to 4x normal")
                        consecutive_rate_limit_errors += 1
                    elif second_utilization > 40 or minute_utilization > 45:
                        # Medium utilization - slight slowdown
                        monitor_interval = base_interval * 1.5
                        consecutive_rate_limit_errors = max(0, consecutive_rate_limit_errors - 1)
                    else:
                        # Normal utilization
                        monitor_interval = base_interval
                        if consecutive_rate_limit_errors > 0:
                            log_event("MONITOR", "✅ Rate limit utilization normal, resuming normal frequency")
                        consecutive_rate_limit_errors = 0
                else:
                    monitor_interval = base_interval
                
                # 🔧 RATE LIMITER FIX #2: CRITICAL - Prioritize orders over monitoring
                # If rate limiter is approaching critical (>70% utilization),
                # SKIP all non-critical monitoring to free up API quota for:
                # - Incoming webhook orders (CRITICAL)
                # - Stop-loss orders (CRITICAL)
                # 
                # Can safely skip:
                # - Order confirmations (non-critical)
                # - LTP bucket checks (can use stale prices temporarily)
                # - Exit checks (can use stale LTP)
                #
                # MUST keep:
                # - Stop-loss placement (risk management)
                
                skip_monitoring = False
                if self.broker and hasattr(self.broker, 'get_rate_limiter_stats'):
                    rate_stats = self.broker.get_rate_limiter_stats()
                    second_utilization = rate_stats.get('second_bucket', {}).get('utilization', 0)
                    
                    if second_utilization > 70:
                        skip_monitoring = True
                        log_event("MONITOR", f"🚨 CRITICAL RATE LIMIT: {second_utilization}% utilized - Suspending monitoring to prioritize orders")
                
                if not skip_monitoring:
                    # Normal operation - perform all monitoring checks
                    # Check order confirmations (skip if rate limited - critical orders are more important!)
                    if consecutive_rate_limit_errors < 2:  # Only check if <2 consecutive rate limit errors
                        self.check_order_confirmations()
                    else:
                        log_event("MONITOR", "⏸️  Skipping order confirmations check due to critical rate limiting")
                    
                    # 🔧 SAFETY FALLBACK: Detect manually-placed SL orders
                    # If automated SL placement fails, user can manually place SL via broker UI
                    # This ensures trailing SL modifications work even with manual SL
                    # Check every 5 cycles to avoid rate limiting (not every second)
                    if not hasattr(self, '_manual_sl_sync_counter'):
                        self._manual_sl_sync_counter = 0
                    self._manual_sl_sync_counter += 1
                    if self._manual_sl_sync_counter >= 5:  # Check every 5 cycles (every ~5 seconds)
                        self.sync_manual_sl_orders()
                        self._manual_sl_sync_counter = 0
                    
                    # 🔧 CRITICAL FIX: Fetch LTP FIRST, then check stop-losses
                    # Previous order was: check SL → fetch LTP
                    # Problem: If rate limiting occurs, LTP is never fetched, so _check_stop_losses() uses stale price
                    # Result: Trailing SL modifications fail because profit calc uses old price
                    # Solution: ALWAYS fetch LTP first, even during rate limiting
                    # BUT: Skip LTP bucket during critical rate limiting
                    if consecutive_rate_limit_errors < 2:
                        # Normal: Fetch LTP for current bucket
                        self._check_ltp_for_bucket()
                    else:
                        log_event("MONITOR", "⏸️  Skipping LTP bucket check due to critical rate limiting")
                    
                    # 🔧 FIX GAP-006: ALWAYS check stop-loss AFTER LTP is fetched
                    # This ensures _update_trailing_sl() has fresh price data
                    # This is critical - we must exit losing positions immediately with current prices
                    self._check_stop_losses()
                    
                    # Check exit conditions (uses last known LTP from bucket check)
                    self.check_exits()
                else:
                    # CRITICAL RATE LIMITING - Minimal operations only
                    # SKIP: Order confirmations, LTP checks, exit checks (can use stale data)
                    # MUST DO: Stop-loss checks (risk management)
                    self._check_stop_losses()
                    
                    log_event("MONITOR", "⏸️  All non-critical monitoring skipped due to critical rate limiting")
                
                # ===== PAPER TRADING: Monitor dummy trades (LOW PRIORITY) =====
                # Only check paper trades once per minute (not every cycle)
                # API call budget: 1 call/minute vs 5/second for live trades
                # This keeps paper trading from stealing API budget from live trades
                # SKIP during critical rate limiting
                if not skip_monitoring and self.api_priority.should_check_paper_trades():
                    if get_dummy_tracker:
                        try:
                            self._update_dummy_trades()
                            log_event("MONITOR", f"Paper trades checked (next in {self.api_priority.paper_trade_check_interval}s)")
                        except Exception as e:
                            log_event("DUMMY_TRADE_ERROR", f"Error updating dummy trades: {e}")
                
                # Save positions periodically
                self.save_positions()
                
                # Sleep for calculated interval
                log_event("MONITOR_SLEEP", f"Sleeping for {monitor_interval}s (base={base_interval}s, consecutive_errors={consecutive_rate_limit_errors}, monitoring_suspended={skip_monitoring})")
                time.sleep(monitor_interval)

                
            except KeyboardInterrupt:
                log_event("MONITOR", "Monitoring stopped by user")
                break
            except Exception as e:
                log_event("ERROR", f"Monitoring loop error: {str(e)}")
                # Longer sleep on errors to avoid rapid error loops
                time.sleep(max(base_interval * 2, 5))
        
        log_event("MONITOR", "Position monitoring stopped")
        
        # Stop the SL retry thread
        self.stop_sl_retry_thread()
    
    def stop_monitoring(self):
        """Stop the monitoring loop"""
        self.monitoring = False
        self.stop_sl_retry_thread()
        
        # Stop priority queue
        if self.loop and self.api_queue:
            asyncio.run_coroutine_threadsafe(self.api_queue.stop(), self.loop)
            self.api_queue.log_stats()
        
        # Stop event loop
        self._stop_event_loop()
        
        log_event("MONITOR", "Monitoring stop requested")
    
    def _start_event_loop(self):
        """Start event loop for async operations"""
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            log_event("MONITOR", "Event loop thread started")
            self.loop.run_forever()
            log_event("MONITOR", "Event loop thread exiting")
        
        self.loop_thread = threading.Thread(target=run_loop, daemon=True, name="PriorityQueueEventLoop")
        self.loop_thread.start()
        
        # Wait for loop to start and be ready
        for i in range(20):  # Wait up to 2 seconds
            time.sleep(0.1)
            if self.loop and self.loop.is_running():
                break
        
        if self.loop and self.loop.is_running():
            log_event("MONITOR", "Event loop started successfully for priority queue")
        else:
            log_event("ERROR", "Event loop failed to start within timeout")
    
    def _stop_event_loop(self):
        """Stop event loop"""
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
            if self.loop_thread:
                self.loop_thread.join(timeout=5)
            log_event("MONITOR", "Event loop stopped")


# =============================================================================
# Testing
# =============================================================================

if __name__ == "__main__":
    """Test position monitoring functionality"""
    print("=== Position Monitor Test ===")
    
    from .angelone import AngelOneBroker
    
    # Create broker and monitor
    broker = AngelOneBroker()
    monitor = PositionMonitor(broker)
    
    # Test position creation
    test_position_data = {
        "symbol": "RELIANCE-EQ",
        "action": "BUY",
        "quantity": 10,
        "entry_price": 2450.50,
        "capital_used": 4901.00,
        "sl_price": 2401.49,
        "order_id": "TEST_ORDER_123",
        "status": "OPEN",
        "created_at": datetime.now().isoformat(),
        "charges": 50.0
    }
    
    # Add position
    if monitor.add_position(test_position_data):
        print("✅ Position added successfully")
    else:
        print("❌ Failed to add position")
    
    # Test position updates
    symbol = "RELIANCE-EQ"
    position = monitor.positions.get(symbol)
    
    if position:
        # Simulate price updates
        position.update_ltp(2470.50)  # Profit
        print(f"✅ Position updated - P&L: ₹{position.unrealized_pnl:.2f}")
        
        # Test SL check
        position.update_ltp(2400.00)  # Below SL
        if position.should_exit_sl():
            print("✅ Stop-loss trigger detected")
        
        # Get position summary
        summary = monitor.get_positions_summary()
        print(f"✅ Positions summary: {summary}")
    
    print("✅ Position monitor tests completed")