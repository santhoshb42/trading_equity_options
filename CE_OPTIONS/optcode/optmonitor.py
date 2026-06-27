"""
Options Position Monitor

Tracks open option contracts (CE/PE positions) with Greeks, IV, premium tracking,
and P&L calculation specific to derivatives trading.
"""

import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from .optconfig import OptionsCapitalConfig, OptionsTradingConfig, BASE_DIR, DATA_DIR, BOT_MODE
from .angelone_options import AngelOneOptionsBroker, get_options_broker
from .optlogging import logger, log_position, log_pnl, log_event
from .fake_move_detector import get_fake_move_detector, get_decay_monitor
from .trade_logger import get_trade_logger
from .options_rate_limiter import get_options_rate_limiter
from .live_data_tracker import get_live_data_tracker
from .market_detector import get_market_condition_detector
from .technical_analyzer import TechnicalAnalyzer

# =============================================================================
# Utility: Timeout wrapper for monitoring functions
# =============================================================================

def call_with_timeout(func, timeout_seconds: float, *args, **kwargs):
    """
    Execute a function with a timeout.
    
    Args:
        func: Function to call
        timeout_seconds: Max seconds to wait
        *args, **kwargs: Arguments to pass to func
    
    Returns:
        Function result or None if timeout
    """
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, *args, **kwargs)
            result = future.result(timeout=timeout_seconds)
            return result
    except FuturesTimeoutError:
        logger.warning(f"TIMEOUT: {func.__name__} exceeded {timeout_seconds}s")
        return None
    except Exception as e:
        logger.error(f"ERROR in {func.__name__}: {str(e)}")
        return None

# =============================================================================
# LTP Bucket Manager (for bulk fetching optimization)
# =============================================================================

class LTPBucketManager:
    """
    Manages bucketed LTP checking to reduce API calls for options positions.
    
    Instead of checking LTP for all positions every cycle,
    divide positions into buckets and rotate through them.
    
    Example: 10 positions → 2 buckets of 5 each
    Cycle 1: Check bucket 1 (5 get_market_data calls)
    Cycle 2: Check bucket 2 (5 get_market_data calls)
    Cycle 3: Back to bucket 1
    
    Result: 5 API calls/second instead of 10! ✅
    """
    
    def __init__(self, bucket_size: int = 50):  # 🔧 CRITICAL FIX: Increase from 5 to 50
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
            log_event("OPTIONS_BUCKET_MANAGER", f"Created {len(self.buckets)} buckets",
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

# =============================================================================
# Active Symbol Pool Manager
# =============================================================================

class ActiveSymbolPool:
    """
    Manages the pool of active symbols for bulk LTP/candle fetching.
    
    Ensures we only fetch data for symbols that:
    - Have an open position (entry made)
    - Have not hit stop-loss or profit target
    - Have not been manually closed
    
    Symbols are added to pool at order placement and removed at exit.
    This prevents wasting API calls on closed/historical positions.
    """
    
    def __init__(self):
        """Initialize empty symbol pool"""
        self.active_symbols: set = set()  # Set of symbols currently being monitored
        self.pool_history: Dict[str, Dict[str, Any]] = {}  # Track entry/exit times
        self.lock = None
    
    def add_symbol(self, symbol: str, order_id: str = "", entry_time: Optional[datetime] = None):
        """
        Add symbol to active pool when position is opened.
        
        Args:
            symbol: Contract symbol (e.g., BANKNIFTY25DEC47000CE)
            order_id: Order ID for tracking
            entry_time: When position was entered
        """
        if symbol not in self.active_symbols:
            self.active_symbols.add(symbol)
            self.pool_history[symbol] = {
                'added_at': entry_time or datetime.now(),
                'order_id': order_id,
                'removed_at': None,
                'exit_reason': None
            }
            logger.info(f"SYMBOL_POOL: ADDED | {symbol} | active_count={len(self.active_symbols)}")
    
    def remove_symbol(self, symbol: str, exit_reason: str = ""):
        """
        Remove symbol from active pool when position is closed.
        
        Args:
            symbol: Contract symbol
            exit_reason: Why position was closed (SL_HIT, PROFIT_TARGET, MANUAL_EXIT, etc.)
        """
        if symbol in self.active_symbols:
            self.active_symbols.remove(symbol)
            if symbol in self.pool_history:
                self.pool_history[symbol]['removed_at'] = datetime.now()
                self.pool_history[symbol]['exit_reason'] = exit_reason
            logger.info(f"SYMBOL_POOL: REMOVED | {symbol} | reason={exit_reason} | active_count={len(self.active_symbols)}")
    
    def get_active_symbols(self) -> List[str]:
        """Get list of currently active symbols for bulk fetching"""
        return list(self.active_symbols)
    
    def get_pool_status(self) -> Dict[str, Any]:
        """Get detailed status of symbol pool"""
        return {
            'active_count': len(self.active_symbols),
            'symbols': sorted(list(self.active_symbols)),
            'history_count': len(self.pool_history),
            'recent_additions': [
                (sym, info['added_at'].isoformat())
                for sym, info in sorted(self.pool_history.items(), 
                                       key=lambda x: x[1]['added_at'], reverse=True)[:5]
            ]
        }

# =============================================================================
# Options Position Model
# =============================================================================

class OptionPosition:
    """Represents an open option contract position"""
    
    def __init__(self,
                 symbol: str,  # BANKNIFTY25XXX1900CE
                 underlying: str,  # BANKNIFTY
                 strike: float,
                 expiry: str,  # YYYY-MM-DD
                 contract_type: str,  # CE or PE
                 action: str,  # BUY or SELL (opening action)
                 quantity: int,  # Lot size or total quantity (contracts)
                 entry_premium: float,  # Entry premium paid per contract
                 entry_time: datetime,
                 order_id: str = "",
                 underlying_alert_price: Optional[float] = None,
                 sector_data: Optional[Dict[str, Any]] = None,
                 market_trend: Optional[str] = None,
                 trend_strength: Optional[float] = None,
                 entry_context: Optional[Dict[str, Any]] = None):
        self.symbol = symbol
        self.underlying = underlying
        self.strike = strike
        self.expiry = expiry
        self.contract_type = contract_type  # CE or PE
        self.action = action
        self.quantity = quantity
        self.entry_premium = entry_premium
        self.entry_time = entry_time
        self.order_id = order_id
        self.trade_id = None  # Track trade_id from trade_logger
        self.underlying_alert_price = underlying_alert_price
        # Track latest underlying LTP (refreshed each greeks cycle) so we can record the
        # underlying's move during the trade — needed for OTM vs ITM PnL divergence analysis.
        self.current_underlying_price = underlying_alert_price

        # Sector strength at entry (LIVE mode logging only)
        # Use passed sector_data if it's not None, otherwise use defaults
        # Important: Empty dict {} should be preserved, not replaced with defaults
        if sector_data is None:
            self.sector_data = {
                'sector': 'UNKNOWN',
                'sector_rsi': None,
                'sector_performance': None,
                'sector_participation': None,
                'sector_bullish': None
            }
        else:
            self.sector_data = sector_data

        # Market trend at entry time (from Pine Script alert)
        # Stored for ML training: learn how each symbol behaves per trend
        self.market_trend   = (market_trend or 'NEUTRAL').strip().upper()
        self.trend_strength = trend_strength  # raw emaSpread % from Pine Script
        self.entry_context = entry_context or {}
        
        # Current state
        self.current_premium = entry_premium
        self.highest_premium = entry_premium  # Track highest premium for trailing exit
        self.lowest_premium = entry_premium   # Track lowest premium for reporting and loss analysis
        self.current_greeks = {
            'delta': 0.5,
            'gamma': 0.05,
            'theta': -0.02,
            'vega': 0.1
        }
        # Get dynamic IV from volatility calculator instead of hardcoded 20%
        from .volatility_calculator import get_volatility_calculator
        vol_calc = get_volatility_calculator()
        self.current_iv = vol_calc.get_dynamic_iv(symbol)
        self.entry_iv = None  # IV at entry (for IV crush detection)
        self.last_updated = entry_time
        
        # NEW: Sentiment tracking (for fade detection)
        self.entry_pcr = None  # PCR at entry
        self.current_pcr = None  # Current PCR (updated during monitoring)
        self.entry_oi_buildup = None  # OI buildup at entry
        self.current_oi = None  # Current OI (updated during monitoring)
        self.entry_sentiment_timestamp = None  # When sentiment was recorded
        
        # Market data tracking for liquidity
        self.bid_price = entry_premium
        self.ask_price = entry_premium
        self.volume = 0
        self.open_interest = 0
        
        # Entry Greeks (for ML learning)
        from .volatility_calculator import get_volatility_calculator
        vol_calc = get_volatility_calculator()
        entry_iv = vol_calc.get_dynamic_iv(symbol)
        self.entry_greeks = {
            'delta': 0.5,
            'gamma': 0.05,
            'theta': -0.02,
            'vega': 0.1,
            'iv': entry_iv
        }
        
        # Greeks tracking for smart exit detection (NEW)
        # Store individual delta, gamma, theta, vega at entry
        self.entry_delta = 0.5
        self.entry_gamma = 0.05
        self.entry_theta = -0.02
        self.entry_vega = 0.1
        from .volatility_calculator import get_volatility_calculator
        vol_calc = get_volatility_calculator()
        self.entry_iv = vol_calc.get_dynamic_iv(symbol)
        
        # Greeks history for trend detection (list of dicts with timestamp)
        self.greeks_history = []  # Format: [{'timestamp': dt, 'delta': x, 'gamma': y, 'theta': z, 'vega': v, 'iv': i}, ...]
        self.last_greeks_capture = None  # Last time Greeks were captured to history
        
        # Exit tracking
        self.exit_premium = None
        self.exit_time = None
        self.exit_order_id = None
        self.exit_reason = None  # PROFIT, LOSS, TIME, MANUAL, EXPIRY
        
        # Exit Greeks (for ML learning)
        self.exit_greeks = {
            'delta': 0.5,
            'gamma': 0.05,
            'theta': -0.02,
            'vega': 0.1,
            'iv': 20.0
        }
        
        # SL order tracking (for LIVE mode broker protection)
        self.sl_order_id = None  # SL order ID placed on broker
        self.sl_order_price = None  # Trigger price when SL was placed
        
        # Trailing SL tracking (for paper trading logging)
        self.trailing_sl_activated = False  # Track when trailing SL first activated
        self.trailing_sl_activation_time = None  # When trailing SL was activated
        self.last_trailing_sl_price = None  # Last calculated trailing SL price
        self.trailing_sl_update_count = 0  # Count of trailing SL adjustments
        
        # TRIAL SL tracking
        self.trial_sl_enabled = False  # TRIAL SL activated (5% from peak after 10% gain)
        self.trial_sl_price = None  # Current TRIAL SL price (5% below peak)
        self.trial_sl_activation_time = None  # When TRIAL SL was activated
        self.trial_sl_update_count = 0  # Count of TRIAL SL adjustments
        self.trial_sl_expected_threshold = None  # Expected threshold (5% or 10%) based on market at entry time
        self.hard_sl_price = None  # Hard SL: -20% from entry (default)
        self.breakeven_floor_active = False  # Profit-floor engaged: hard SL raised to breakeven after being green
        
        # Rate limit optimization for modify_order (HYBRID strategy)
        self.last_modify_time = None  # When SL was last modified on broker
        self.last_modified_sl_price = None  # Last SL price sent to broker
        self.modify_pending = False  # Waiting to modify due to rate limiting
        self.last_attempted_sl_price = None  # For adaptive modify detection
        self.next_modify_earliest_time = None  # Per-symbol cooldown to avoid clustered broker modifies
        
        # P&L tracking
        self.unrealized_pnl = 0.0
        self.realized_pnl = None
    
    def update_market_data(self, current_premium: float, greeks: Dict[str, float], iv: float):
        """Update position with current market data"""
        self.current_premium = current_premium
        
        # Track highest premium reached (for trailing exit)
        if current_premium > self.highest_premium:
            self.highest_premium = current_premium
        if current_premium < self.lowest_premium:
            self.lowest_premium = current_premium
        
        self.current_greeks = greeks
        self.current_iv = iv
        self.last_updated = datetime.now()
        
        # Update Greeks history for trend detection
        delta = greeks.get('delta', 0.5)
        gamma = greeks.get('gamma', 0.05)
        theta = greeks.get('theta', -0.02)
        vega = greeks.get('vega', 0.1)
        
        self.update_greeks_history(delta, gamma, theta, vega, iv)
        
        self._calculate_unrealized_pnl()
    
    def _calculate_unrealized_pnl(self):
        """Calculate unrealized P&L based on current premium"""
        premium_difference = self.current_premium - self.entry_premium
        
        if self.action == "BUY":
            # Long position: profit when premium increases
            self.unrealized_pnl = premium_difference * self.quantity
        else:  # SELL
            # Short position: profit when premium decreases
            self.unrealized_pnl = -premium_difference * self.quantity
    
    def close_position(self, exit_premium: float, exit_reason: str, exit_greeks: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Close option position and calculate final P&L"""
        self.exit_premium = exit_premium
        self.exit_time = datetime.now()
        self.exit_reason = exit_reason
        
        # DEBUG: Log sector_data at close time
        logger.debug(f"POSITION_CLOSE_DEBUG | {self.symbol} | sector_data={self.sector_data}")
        
        # STORE EXIT GREEKS FOR ML LEARNING
        if exit_greeks:
            self.exit_greeks = exit_greeks
        else:
            # If not provided, use current Greeks
            self.exit_greeks = {
                'delta': self.current_greeks.get('delta', 0.5),
                'gamma': self.current_greeks.get('gamma', 0.05),
                'theta': self.current_greeks.get('theta', -0.02),
                'vega': self.current_greeks.get('vega', 0.1),
                'iv': self.current_iv
            }
        
        # Calculate realized P&L
        premium_difference = exit_premium - self.entry_premium
        if self.action == "BUY":
            self.realized_pnl = premium_difference * self.quantity
        else:  # SELL
            self.realized_pnl = -premium_difference * self.quantity
        
        return {
            'symbol': self.symbol,
            'underlying': self.underlying,
            'entry_premium': self.entry_premium,
            'entry_premium_total': self.entry_premium * self.quantity,
            'exit_premium': exit_premium,
            'exit_premium_total': exit_premium * self.quantity,
            'quantity': self.quantity,
            'pnl': self.realized_pnl,
            'pnl_percent': (premium_difference / self.entry_premium * 100) if self.entry_premium else 0,
            'duration': (self.exit_time - self.entry_time).total_seconds(),
            'exit_reason': exit_reason,
            'underlying_alert_price': self.underlying_alert_price,
            'entry_time': self.entry_time.isoformat() if isinstance(self.entry_time, datetime) else self.entry_time,
            # Entry and Exit Greeks for ML learning
            'entry_greeks': self.entry_greeks,
            'exit_greeks': self.exit_greeks,
            'entry_delta': self.entry_greeks.get('delta', 0.5),
            'entry_gamma': self.entry_greeks.get('gamma', 0.05),
            'entry_theta': self.entry_greeks.get('theta', -0.02),
            'entry_vega': self.entry_greeks.get('vega', 0.1),
            'entry_iv': self.entry_greeks.get('iv', 20.0),
            'exit_delta': self.exit_greeks.get('delta', 0.5),
            'exit_gamma': self.exit_greeks.get('gamma', 0.05),
            'exit_theta': self.exit_greeks.get('theta', -0.02),
            'exit_vega': self.exit_greeks.get('vega', 0.1),
            'exit_iv': self.exit_greeks.get('iv', 20.0),
            # Contract details for ML
            'contract_type': self.contract_type,
            'action': self.action,
            # 🔴 IMPORTANT: Add trailing SL tracking for PNL analysis
            'highest_premium': self.highest_premium,
            'lowest_premium': self.lowest_premium,
            'trial_sl_enabled': self.trial_sl_enabled,
            'trial_sl_price': self.trial_sl_price,
            'trial_sl_updates': self.trial_sl_update_count,
            'trailing_sl_activated': self.trailing_sl_activated,
            'last_trailing_sl_price': self.last_trailing_sl_price,
            'trailing_sl_updates': self.trailing_sl_update_count,
            # 🆕 SECTOR STRENGTH DATA at entry (for EOD analysis)
            'sector': self.sector_data.get('sector', 'UNKNOWN') if self.sector_data is not None else 'UNKNOWN',
            'sector_rsi': self.sector_data.get('sector_rsi') if self.sector_data is not None else None,
            'sector_performance': self.sector_data.get('sector_performance') if self.sector_data is not None else None,
            'sector_participation': self.sector_data.get('sector_participation') if self.sector_data is not None else None,
            'sector_bullish': self.sector_data.get('sector_bullish') if self.sector_data is not None else None,
            'sector_check': "PASS" if (self.sector_data is not None and self.sector_data.get('sector_bullish', False)) else "FAIL",
            # Market trend at entry (Pine Script 7.18-E) — used for ML trend analysis
            'market_trend':   self.market_trend,
            'trend_strength': self.trend_strength,
            'entry_context': self.entry_context,
            # ── EXIT-CHANGE ANALYSIS (v2026-06-21: profit floor + trail-arm @4%) ──
            # Lets us measure, after 2-3 days, how the new exit rules behaved per trade.
            'peak_pct': ((self.highest_premium - self.entry_premium) / self.entry_premium * 100) if self.entry_premium else 0,
            'breakeven_floor_active': getattr(self, 'breakeven_floor_active', False),
            'trial_sl_activation_threshold': getattr(self, 'trial_sl_expected_threshold', None),
            'hard_sl_price': getattr(self, 'hard_sl_price', None),
            # ── OTM / ITM DIVERGENCE ANALYSIS ──
            # Same TradingView alert is fanned out to both OTM and ITM bots; they pick
            # different strikes and diverge in PnL. These fields let us pair the two legs
            # (alert_key) and decompose the divergence (moneyness + underlying move).
            'bot_mode': BOT_MODE,
            'strike': self.strike,
            'strike_offset': getattr(OptionsTradingConfig, 'STRIKE_OFFSET', None),
            # moneyness at entry: PE is ITM when strike > underlying; CE when strike < underlying.
            'moneyness_pct': (
                ((self.strike - self.underlying_alert_price) / self.underlying_alert_price * 100)
                if (self.contract_type == 'PE' and self.underlying_alert_price)
                else ((self.underlying_alert_price - self.strike) / self.underlying_alert_price * 100)
                if (self.contract_type == 'CE' and self.underlying_alert_price)
                else None
            ),
            'underlying_entry_price': self.underlying_alert_price,
            'underlying_exit_price': getattr(self, 'current_underlying_price', self.underlying_alert_price),
            'underlying_move_pct': (
                (getattr(self, 'current_underlying_price', self.underlying_alert_price) - self.underlying_alert_price)
                / self.underlying_alert_price * 100
            ) if self.underlying_alert_price else None,
            # alert_key pairs the OTM and ITM legs of the SAME alert: same underlying, same
            # alert price, same minute. Round price to 2dp and time to the minute for matching.
            'alert_key': (
                f"{self.underlying}|{round(self.underlying_alert_price, 2)}|"
                f"{(self.entry_time.strftime('%Y-%m-%dT%H:%M') if isinstance(self.entry_time, datetime) else str(self.entry_time)[:16])}"
            ) if self.underlying_alert_price else None,
        }
    
    # =========================================================================
    # Greeks Tracking Methods for Smart Exit Detection
    # =========================================================================
    
    def capture_entry_greeks(self, delta: float, gamma: float, theta: float, vega: float, iv: float):
        """Capture Greeks at position entry (called once when position opens)"""
        self.entry_delta = delta
        self.entry_gamma = gamma
        self.entry_theta = theta
        self.entry_vega = vega
        self.entry_iv = iv
        
        # Also update entry_greeks dict for backward compatibility
        self.entry_greeks = {
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'vega': vega,
            'iv': iv
        }
        
        # Initialize history with entry Greeks
        self.greeks_history = [{
            'timestamp': datetime.now(),
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'vega': vega,
            'iv': iv
        }]
        self.last_greeks_capture = datetime.now()
    
    def update_greeks_history(self, delta: float, gamma: float, theta: float, vega: float, iv: float) -> bool:
        """
        Update Greeks history with current values.
        Returns True if update was recorded, False if skipped (to prevent duplicate entries within same second).
        Maintains history of last 20 measurements for trend calculation.
        """
        now = datetime.now()
        
        # Avoid duplicate entries within the same second
        if self.last_greeks_capture and (now - self.last_greeks_capture).total_seconds() < 1.0:
            return False
        
        # Add to history
        self.greeks_history.append({
            'timestamp': now,
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'vega': vega,
            'iv': iv
        })
        
        # Keep only last 20 measurements (2 minutes of data at 10-second intervals)
        if len(self.greeks_history) > 20:
            self.greeks_history = self.greeks_history[-20:]
        
        self.last_greeks_capture = now
        return True
    
    def get_delta_trend(self) -> Optional[float]:
        """
        Get delta change from previous measurement.
        Returns: delta_change (current - previous)
        Returns None if less than 2 measurements available.
        """
        if len(self.greeks_history) < 2:
            return None
        
        current_delta = self.greeks_history[-1]['delta']
        previous_delta = self.greeks_history[-2]['delta']
        
        return current_delta - previous_delta
    
    def get_gamma_change(self) -> Optional[float]:
        """Get gamma change from entry"""
        if not self.entry_gamma or len(self.greeks_history) == 0:
            return None
        
        current_gamma = self.greeks_history[-1]['gamma']
        return current_gamma / self.entry_gamma if self.entry_gamma != 0 else None
    
    def get_theta_change(self) -> Optional[float]:
        """Get theta change from entry (absolute difference)"""
        if not self.entry_theta or len(self.greeks_history) == 0:
            return None
        
        current_theta = self.greeks_history[-1]['theta']
        return abs(current_theta) / abs(self.entry_theta) if self.entry_theta != 0 else None
    
    def get_iv_change_percent(self) -> Optional[float]:
        """Get IV change from entry as percentage"""
        if not self.entry_iv or len(self.greeks_history) == 0:
            return None
        
        current_iv = self.greeks_history[-1]['iv']
        return abs(current_iv - self.entry_iv) / self.entry_iv * 100 if self.entry_iv != 0 else None
    
    def get_greeks_age_seconds(self) -> Optional[float]:
        """Get age of current Greeks data (seconds since last update)"""
        if not self.last_greeks_capture:
            return None
        
        return (datetime.now() - self.last_greeks_capture).total_seconds()
    
    def get_current_greeks(self) -> Optional[Dict[str, float]]:
        """Get most recent Greeks values from history"""
        if not self.greeks_history:
            return None
        
        latest = self.greeks_history[-1]
        return {
            'delta': latest['delta'],
            'gamma': latest['gamma'],
            'theta': latest['theta'],
            'vega': latest['vega'],
            'iv': latest['iv'],
            'timestamp': latest['timestamp'].isoformat() if isinstance(latest['timestamp'], datetime) else latest['timestamp']
        }
    
    def is_expired(self) -> bool:
        """Check if option position has expired"""
        expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d").date()
        return datetime.now().date() > expiry_date
    
    def days_to_expiry(self) -> int:
        """Get days remaining to expiry"""
        expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d").date()
        return (expiry_date - datetime.now().date()).days
    
    # =========================================================================
    # NEW: Advanced Greeks Analysis Methods (Audit Enhancement)
    # =========================================================================
    
    def get_delta_trend_confirmed(self, required_cycles: int = 2, use_rolling_avg: bool = True) -> Tuple[bool, Optional[float]]:
        """
        Confirm delta reversal with multiple checks:
        1. Check if trend confirmed for N consecutive cycles, OR
        2. Check rolling average of last 3 samples
        
        Returns: (is_confirmed, delta_change_value)
        
        This reduces false positives from single-cycle volatility.
        """
        if len(self.greeks_history) < 2:
            return False, None
        
        # Method 1: Check last N consecutive cycles for consistent decline
        consecutive_declines = 0
        for i in range(len(self.greeks_history) - 1, max(0, len(self.greeks_history) - required_cycles - 1), -1):
            if i == len(self.greeks_history) - 1:
                continue
            
            current = self.greeks_history[i]['delta']
            previous = self.greeks_history[i - 1]['delta'] if i > 0 else None
            
            if previous and current < previous:
                consecutive_declines += 1
        
        if consecutive_declines >= required_cycles - 1:
            return True, self.get_delta_trend()
        
        # Method 2: Check rolling average of last 3 measurements
        if use_rolling_avg and len(self.greeks_history) >= 3:
            deltas = [m['delta'] for m in self.greeks_history[-3:]]
            rolling_avg = sum(deltas) / len(deltas)
            current_delta = self.greeks_history[-1]['delta']
            
            if current_delta < rolling_avg - 0.02:  # Declining trend in rolling avg
                return True, self.get_delta_trend()
        
        return False, self.get_delta_trend()
    
    def get_gamma_status(self) -> Tuple[bool, float, Optional[str]]:
        """
        Check gamma health with BOTH relative (multiplier) AND absolute cap.
        
        Returns: (is_dangerous, gamma_value, reason)
        
        Triggers if:
        - current_gamma > entry_gamma × 1.5, OR
        - current_gamma > 0.04 (absolute cap)
        """
        if len(self.greeks_history) == 0:
            return False, 0, None
        
        from .optconfig import OptionsTradingConfig
        
        current_gamma = self.greeks_history[-1]['gamma']
        
        # Check absolute cap
        if current_gamma > OptionsTradingConfig.GAMMA_ABSOLUTE_CAP:
            return True, current_gamma, f"absolute_cap_exceeded|gamma={current_gamma:.4f}|cap={OptionsTradingConfig.GAMMA_ABSOLUTE_CAP}"
        
        # Check multiplier
        if self.entry_gamma > 0:
            multiplier = current_gamma / self.entry_gamma
            if multiplier > OptionsTradingConfig.GAMMA_MULTIPLIER_THRESHOLD:
                return True, current_gamma, f"multiplier_exceeded|gamma_change={multiplier:.2f}x|entry={self.entry_gamma:.4f}"
        
        return False, current_gamma, None
    
    def get_theta_status(self) -> Tuple[bool, float, Optional[str], bool, bool]:
        """
        Check theta health with directional context.
        
        Returns: (is_dangerous, theta_value, reason, pnl_check_failed, delta_weakening)
        
        Only triggers theta exit if:
        - |theta| > |entry_theta| × 3.0, AND
        - (P&L <= 0 OR Delta is weakening)
        
        This avoids killing winning trades due to normal decay.
        """
        if len(self.greeks_history) == 0:
            return False, 0, None, False, False
        
        from .optconfig import OptionsTradingConfig
        
        current_theta = self.greeks_history[-1]['theta']
        
        # Check multiplier first
        if self.entry_theta != 0:
            multiplier = abs(current_theta) / abs(self.entry_theta)
            if multiplier <= OptionsTradingConfig.THETA_MULTIPLIER_THRESHOLD:
                return False, current_theta, None, False, False
        else:
            return False, current_theta, None, False, False
        
        # Theta acceleration confirmed - now check contextual conditions
        pnl_check = self.unrealized_pnl <= 0 if OptionsTradingConfig.ENABLE_THETA_PNL_CHECK else False
        
        delta_check = False
        if OptionsTradingConfig.ENABLE_THETA_DELTA_CHECK:
            delta_trend = self.get_delta_trend()
            delta_check = delta_trend is not None and delta_trend < -0.02  # Delta weakening
        
        # Only exit if P&L bad OR delta weakening (not just theta acceleration alone)
        is_dangerous = pnl_check or delta_check
        
        reason = None
        if is_dangerous:
            reason = f"theta_acceleration|multiplier={multiplier:.2f}x|pnl_check={pnl_check}|delta_weak={delta_check}"
        
        return is_dangerous, current_theta, reason, pnl_check, delta_check
    
    def get_vega_status(self) -> Tuple[bool, float, Optional[str]]:
        """
        Check vega crush with DYNAMIC thresholds based on IV regime.
        
        Returns: (is_dangerous, iv_change_percent, reason)
        
        If ENABLE_VEGA_DYNAMIC_THRESHOLD:
        - Low IV regime (entry_iv < 50): threshold = 1.0%
        - High IV regime (entry_iv >= 50): threshold = 3.0%
        Else:
        - Fixed threshold = 2.0%
        """
        if len(self.greeks_history) == 0 or self.entry_iv is None:
            return False, 0, None
        
        from .optconfig import OptionsTradingConfig
        
        current_iv = self.greeks_history[-1]['iv']
        iv_change_pct = abs(current_iv - self.entry_iv) / self.entry_iv * 100 if self.entry_iv > 0 else 0
        
        # Determine threshold
        if OptionsTradingConfig.ENABLE_VEGA_DYNAMIC_THRESHOLD:
            if self.entry_iv < OptionsTradingConfig.VEGA_IV_REGIME_BOUNDARY:
                threshold = OptionsTradingConfig.VEGA_LOW_IV_THRESHOLD
                regime = "low_iv"
            else:
                threshold = OptionsTradingConfig.VEGA_HIGH_IV_THRESHOLD
                regime = "high_iv"
        else:
            threshold = OptionsTradingConfig.VEGA_CRUSH_FIXED_THRESHOLD
            regime = "fixed"
        
        is_dangerous = iv_change_pct > threshold
        
        reason = None
        if is_dangerous:
            reason = f"vega_crush|iv_change={iv_change_pct:.2f}%|threshold={threshold}%|regime={regime}|entry_iv={self.entry_iv:.2f}|current_iv={current_iv:.2f}"
        
        return is_dangerous, iv_change_pct, reason
    
    def get_health_status(self) -> Tuple[bool, Dict[str, bool], str]:
        """
        Combined health check returning detailed breakdown of all signals.
        
        Returns: (is_unhealthy, conditions_dict, formatted_string)
        
        conditions_dict format:
        {
            'delta_reversal': bool,
            'gamma_explosion': bool,
            'theta_acceleration': bool,
            'vega_crush': bool
        }
        
        is_unhealthy = True if 2+ conditions are True
        formatted_string = "delta_bad=True,gamma_bad=False,theta_bad=True,vega_bad=False"
        """
        conditions = {
            'delta_reversal': False,
            'gamma_explosion': False,
            'theta_acceleration': False,
            'vega_crush': False
        }
        
        # Check delta reversal
        delta_confirmed, _ = self.get_delta_trend_confirmed()
        conditions['delta_reversal'] = delta_confirmed
        
        # Check gamma explosion
        gamma_dangerous, _, _ = self.get_gamma_status()
        conditions['gamma_explosion'] = gamma_dangerous
        
        # Check theta acceleration
        theta_dangerous, _, _, _, _ = self.get_theta_status()
        conditions['theta_acceleration'] = theta_dangerous
        
        # Check vega crush
        vega_dangerous, _, _ = self.get_vega_status()
        conditions['vega_crush'] = vega_dangerous
        
        # Count unhealthy conditions
        unhealthy_count = sum(1 for v in conditions.values() if v)
        is_unhealthy = unhealthy_count >= 2
        
        # Format string for logging
        formatted = ",".join([
            f"delta_bad={str(conditions['delta_reversal'])}",
            f"gamma_bad={str(conditions['gamma_explosion'])}",
            f"theta_bad={str(conditions['theta_acceleration'])}",
            f"vega_bad={str(conditions['vega_crush'])}"
        ])
        
        return is_unhealthy, conditions, formatted
    
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert position to dictionary"""
        # Calculate sector check status
        sector_check = "PASS"
        if self.sector_data and self.sector_data.get('sector') != 'UNKNOWN':
            # FAIL if sector is bearish or no bullish signal
            if not self.sector_data.get('sector_bullish', False):
                sector_check = "FAIL"
        
        return {
            'symbol': self.symbol,
            'underlying': self.underlying,
            'strike': self.strike,
            'expiry': self.expiry,
            'contract_type': self.contract_type,
            'action': self.action,
            'quantity': self.quantity,
            'entry_premium': self.entry_premium,
            'entry_premium_total': self.entry_premium * self.quantity,
            'entry_time': self.entry_time.isoformat() if isinstance(self.entry_time, datetime) else self.entry_time,
            'entry_greeks': self.entry_greeks,  # ADDED: Capture entry Greeks
            # Greeks tracking fields (NEW)
            'entry_delta': self.entry_delta,
            'entry_gamma': self.entry_gamma,
            'entry_theta': self.entry_theta,
            'entry_vega': self.entry_vega,
            'entry_iv': self.entry_iv,
            'greeks_history_length': len(self.greeks_history),
            'order_id': self.order_id,
            'current_premium': self.current_premium,
            'current_premium_total': self.current_premium * self.quantity,
            'current_greeks': self.current_greeks,
            'current_iv': self.current_iv,
            'unrealized_pnl': self.unrealized_pnl,
            'highest_premium': self.highest_premium,
            'lowest_premium': self.lowest_premium,
            'days_to_expiry': self.days_to_expiry(),
            'last_updated': self.last_updated.isoformat() if isinstance(self.last_updated, datetime) else self.last_updated,
            'underlying_alert_price': self.underlying_alert_price,
            'exit_greeks': self.exit_greeks,  # ADDED: Capture exit Greeks
            # SL order tracking (for LIVE mode)
            'sl_order_id': self.sl_order_id,
            'sl_order_price': self.sl_order_price,
            # Trailing SL tracking
            'trailing_sl_activated': self.trailing_sl_activated,
            'last_trailing_sl_price': self.last_trailing_sl_price,
            'trailing_sl_activation_time': self.trailing_sl_activation_time,
            'trailing_sl_update_count': self.trailing_sl_update_count,
            # TRIAL SL fields
            'hard_sl_price': self.hard_sl_price,
            'trial_sl_enabled': self.trial_sl_enabled,
            'trial_sl_price': self.trial_sl_price,
            'trial_sl_activation_time': self.trial_sl_activation_time,
            'trial_sl_update_count': self.trial_sl_update_count,
            # Exit fields (CRITICAL: Used to identify closed positions in squareoff)
            'exit_premium': self.exit_premium,
            'exit_time': self.exit_time.isoformat() if isinstance(self.exit_time, datetime) and self.exit_time else self.exit_time,
            # 🆕 SECTOR STRENGTH DATA at entry (for EOD analysis)
            'sector': self.sector_data.get('sector', 'UNKNOWN') if self.sector_data is not None else 'UNKNOWN',
            'sector_rsi': self.sector_data.get('sector_rsi') if self.sector_data is not None else None,
            'sector_performance': self.sector_data.get('sector_performance') if self.sector_data is not None else None,
            'sector_participation': self.sector_data.get('sector_participation') if self.sector_data is not None else None,
            'sector_bullish': self.sector_data.get('sector_bullish') if self.sector_data is not None else None,
            'sector_check': sector_check,
        }

# =============================================================================
# Options Position Monitor
# =============================================================================

class OptionPositionMonitor:
    """
    Manages open option positions (contracts).
    Tracks Greeks, IV, premium, P&L, and monitors expiry/exit conditions.
    """
    
    def __init__(self, broker: Optional[AngelOneOptionsBroker] = None):
        self.broker = broker or get_options_broker()
        self.positions: Dict[str, OptionPosition] = {}  # {symbol: position}
        self.closed_positions: List[OptionPosition] = []  # Historical positions
        self.positions_file = DATA_DIR / "option_positions.json"
        self.pnl_history_file = DATA_DIR / "option_pnl_history.json"
        
        # Thread-safety: prevent concurrent close_position() calls for the same symbol.
        # The async sentiment-exit thread and the main monitor loop both call close_position(),
        # so without a guard a race can place two SELL orders (creating a naked short).
        import threading as _th
        self._positions_lock = _th.RLock()
        self._closing_lock = _th.Lock()
        self._closing_symbols: set = set()
        
        # Bucket manager for bulk LTP fetching (optimization)
        # Uses buckets to distribute API calls: don't fetch all 100 positions in one call
        # 🔧 CRITICAL FIX: bucket_size increased from 5 to 50 to fix rate limiting
        # With bucket_size=50: 101 positions → 3 calls instead of 21 calls per cycle
        # Saves 360 API calls per minute (87% reduction)
        # This reduces monitoring from 464 calls/min to 104 calls/min (58% of rate limit)
        self.ltp_bucket_manager = LTPBucketManager(bucket_size=50)
        
        # Active symbol pool - tracks only currently open positions for bulk fetch
        self.symbol_pool = ActiveSymbolPool()
        
        # SENTIMENT CHECK TIMING: Track when we last checked sentiment
        # IV changes fast (5-10s), so check frequently for fades
        self.last_sentiment_check_time = None  # Will check immediately on first call
        self._realtime_fade_last_check: Dict[str, datetime] = {}
        
        # Load existing positions
        self._load_positions()
        
        # Re-populate symbol pool with loaded positions
        for symbol, position in self._snapshot_positions_items():
            self.symbol_pool.add_symbol(symbol, entry_time=position.entry_time)

    def _get_position(self, symbol: str) -> Optional[OptionPosition]:
        with self._positions_lock:
            return self.positions.get(symbol)

    def _snapshot_positions_items(self) -> List[Tuple[str, OptionPosition]]:
        with self._positions_lock:
            return list(self.positions.items())

    def _snapshot_positions_values(self) -> List[OptionPosition]:
        with self._positions_lock:
            return list(self.positions.values())

    def _get_trial_sl_profile(self, position: OptionPosition, market_threshold: float) -> Tuple[float, float, str]:
        """Choose a trailing profile: scalp cheaper contracts, let strong runners breathe."""
        activation_threshold = market_threshold
        trailing_gap = OptionsTradingConfig.TRIAL_SL_STANDARD_GAP
        profile = "STANDARD"

        if position.entry_premium <= OptionsTradingConfig.TRIAL_SL_SCALP_PREMIUM_MAX:
            activation_threshold = min(market_threshold, OptionsTradingConfig.TRIAL_SL_SCALP_ACTIVATION_PCT)
            trailing_gap = OptionsTradingConfig.TRIAL_SL_SCALP_GAP
            profile = "SCALP"
        elif (
            position.market_trend == 'GOOD'
            and float(position.trend_strength or 0) >= OptionsTradingConfig.TRIAL_SL_RUNNER_TREND_MIN
        ):
            trailing_gap = max(trailing_gap, OptionsTradingConfig.TRIAL_SL_RUNNER_GAP)
            profile = "RUNNER"

        # Cap the arm threshold so the trail engages early and protects the +3-6% give-back zone.
        # Trailing still follows peak-minus-gap upward, so upside is unaffected.
        activation_threshold = min(activation_threshold, OptionsTradingConfig.TRIAL_SL_BASE_ACTIVATION_PCT)

        return activation_threshold, trailing_gap, profile

    def _apply_profit_floor(self, symbol: str, position: 'OptionPosition') -> bool:
        """Breakeven profit-floor: once a trade has been green >= TRIGGER%, raise the hard SL to
        the LOCK% floor and shield it from stale/dead exits that would book a sub-floor loss.

        Returns True if the caller should SKIP its loss-booking exit (the breakeven hard SL is now
        guarding the downside; let it ride or get stopped at the floor instead of dumping at a loss).
        """
        if not OptionsTradingConfig.ENABLE_PROFIT_FLOOR:
            return False
        # Trail already protecting profit — nothing to add.
        if position.trial_sl_enabled:
            return False
        if not position.entry_premium or position.entry_premium <= 0:
            return False

        peak_pct = (position.highest_premium - position.entry_premium) / position.entry_premium * 100
        if peak_pct < OptionsTradingConfig.PROFIT_FLOOR_TRIGGER_PCT:
            return False  # never got green enough to deserve a floor

        # Raise hard SL up to the breakeven floor (one-way ratchet).
        floor_price = self._round_to_10_paise(
            position.entry_premium * (1 + OptionsTradingConfig.PROFIT_FLOOR_LOCK_PCT / 100)
        )
        if (position.hard_sl_price or 0) < floor_price:
            old_sl = position.hard_sl_price
            position.hard_sl_price = floor_price
            position.breakeven_floor_active = True
            self._save_positions()
            logger.warning(
                f"PROFIT_FLOOR_ARMED: {symbol} | peaked +{peak_pct:.1f}% | "
                f"hard SL raised {old_sl} -> ₹{floor_price:.2f} (breakeven floor)"
            )
            log_event(
                "PROFIT_FLOOR_ARMED",
                f"🛡️ Breakeven floor armed for {symbol} (peaked +{peak_pct:.1f}%)",
                symbol=symbol,
                peak_pct=round(peak_pct, 2),
                old_sl=old_sl,
                floor_price=floor_price,
                entry_premium=position.entry_premium,
            )
            # LIVE: push the raised stop to the broker.
            self.modify_sl_order(symbol, floor_price, position.sl_order_id)

        # Block any sub-floor loss exit; the breakeven hard SL now guards the downside.
        cur_pct = (position.current_premium - position.entry_premium) / position.entry_premium * 100
        if cur_pct < OptionsTradingConfig.PROFIT_FLOOR_LOCK_PCT:
            return True
        return False

    def add_position(self,
                    symbol: str,
                    underlying: str,
                    strike: float,
                    expiry: str,
                    contract_type: str,
                    action: str,
                    quantity: int,
                    entry_premium: float,
                    order_id: str,
                    underlying_alert_price: Optional[float] = None,
                    entry_greeks: Optional[Dict[str, float]] = None,
                    sector_data: Optional[Dict[str, Any]] = None,
                    market_trend: Optional[str] = None,
                    trend_strength: Optional[float] = None,
                    entry_context: Optional[Dict[str, Any]] = None) -> bool:
        """Add new option position"""
        try:
            # 🔧 CRITICAL FIX: Prevent duplicate position additions from retry logic
            # If position already exists with SAME order_id, it's idempotent (OK)
            # If position already exists with DIFFERENT order_id, it's a conflict (ERROR)
            
            # STUCK POSITION FILTER: Reject positions marked as stuck intraday
            STUCK_INTRADAY_SYMBOLS = {'SONACOMS30DEC25490CE', 'INDIANB30DEC25780CE'}
            if symbol in STUCK_INTRADAY_SYMBOLS:
                logger.warning(f"POSITION_ADD: REJECTED_STUCK_INTRADAY | {symbol}")
                print(f"⚠️ REJECTED: Position {symbol} is marked as stuck (cannot close in PAPER mode)")
                return False
            
            # SAFETY CHECK: Reject positions with zero entry premium (prevents stale positions)
            if entry_premium <= 0:
                logger.warning(f"POSITION_ADD: REJECTED_ZERO_PREMIUM | {symbol} | premium={entry_premium}")
                print(f"⚠️ REJECTED: Position {symbol} has zero/invalid entry premium (premium={entry_premium})")
                return False
            
            logger.debug(f"POSITION_ADD: {symbol} | underlying={underlying} | qty={quantity} | premium={entry_premium:.2f}")
            
            # 🔧 FIX: Check for duplicate by UNDERLYING symbol, not option contract
            # This prevents multiple positions in the same underlying (e.g., SAIL) even with different strikes
            existing_position_with_same_underlying = None
            for pos_symbol, pos in self._snapshot_positions_items():
                if pos.underlying == underlying:
                    existing_position_with_same_underlying = pos
                    break
            
            if existing_position_with_same_underlying:
                # Position already exists for this underlying - check if it's idempotent (same order_id)
                existing_pos = existing_position_with_same_underlying
                if existing_pos.order_id == order_id:
                    # Idempotent call - same order ID being added again (from retry logic)
                    logger.info(f"POSITION_ADD: IDEMPOTENT_CALL | underlying={underlying} | symbol={symbol} | order_id={order_id} (already added, returning success)")
                    return True
                else:
                    # Conflict - different order IDs for same underlying
                    logger.error(f"POSITION_ADD: DUPLICATE_CONFLICT | underlying={underlying} | new_symbol={symbol} vs existing_symbol={existing_pos.symbol} | existing_order_id={existing_pos.order_id} vs new_order_id={order_id}")
                    print(f"⚠️ Position for {underlying} already exists ({existing_pos.symbol}) - rejecting duplicate alert")
                    return False
            
            position = OptionPosition(
                symbol=symbol,
                underlying=underlying,
                strike=strike,
                expiry=expiry,
                contract_type=contract_type,
                action=action,
                quantity=quantity,
                entry_premium=entry_premium,
                entry_time=datetime.now(),
                order_id=order_id,
                underlying_alert_price=underlying_alert_price,
                sector_data=sector_data,
                market_trend=market_trend,
                trend_strength=trend_strength,
                entry_context=entry_context,
            )
            
            # DEBUG: Log what sector_data was stored in position
            logger.debug(f"POSITION_CREATED | {symbol} | received_sector_data={sector_data} | stored_sector_data={position.sector_data}")
            
            # STORE ENTRY GREEKS FOR ML LEARNING
            if entry_greeks:
                position.entry_greeks = entry_greeks
                # Capture individual Greeks fields for trend detection
                position.capture_entry_greeks(
                    delta=entry_greeks.get('delta', 0.5),
                    gamma=entry_greeks.get('gamma', 0.05),
                    theta=entry_greeks.get('theta', -0.02),
                    vega=entry_greeks.get('vega', 0.1),
                    iv=entry_greeks.get('iv', 0.25)  # Changed default from 20% to 25%
                )
                logger.debug(f"POSITION_INIT: Captured entry Greeks | symbol={symbol} | delta={position.entry_delta:.3f} | gamma={position.entry_gamma:.4f} | theta={position.entry_theta:.4f} | vega={position.entry_vega:.4f} | iv={position.entry_iv:.2f}")
            else:
                # No entry Greeks provided, use defaults
                logger.warning(f"POSITION_INIT: No entry Greeks provided | symbol={symbol} | using defaults")
                position.capture_entry_greeks(0.5, 0.05, -0.02, 0.1, 20.0)
            
            with self._positions_lock:
                self.positions[symbol] = position
            
            # 🔧 INITIALIZE HARD SL: use configured SL% from entry premium
            position.hard_sl_price = self._round_to_10_paise(
                position.entry_premium * (1 - OptionsTradingConfig.STOP_LOSS_PERCENTAGE / 100)
            )
            
            # 🔧 PRE-CALCULATE TRIAL_SL THRESHOLD based on current market conditions
            # Use the detector's cached Nifty data (updated every 4s by monitoring loop) —
            # avoid a fresh rate-limited API call here which delays SL placement.
            market_detector = get_market_condition_detector()
            expected_threshold, market_reason = market_detector.get_trial_sl_threshold()
            position.trial_sl_expected_threshold = expected_threshold
            logger.info(f"POSITION_ADD: TRIAL_SL_THRESHOLD_SET | {symbol} | Threshold={expected_threshold:.0f}% | Market={market_reason}")
            
            self._save_positions()
            
            # ADD TO ACTIVE SYMBOL POOL for bulk LTP fetching
            self.symbol_pool.add_symbol(symbol, order_id=order_id, entry_time=position.entry_time)
            
            # 🆕 PLACE STOP LOSS ORDER TO BROKER (for LIVE mode protection)
            # This ensures broker executes SL even if bot crashes
            if action == "BUY":
                sl_placed = self.place_stop_loss_order(symbol)
                if sl_placed:
                    logger.info(f"POSITION_ADD: SL_ORDER_PLACED | {symbol} | SL=₹{position.hard_sl_price:.2f}")
                else:
                    logger.warning(f"POSITION_ADD: SL_ORDER_FAILED | {symbol} | Will retry in monitoring loop")
            
            # Initialize decay-aware monitoring
            decay_monitor = get_decay_monitor()
            # Parse expiry date to calculate days remaining
            try:
                expiry_date = datetime.strptime(expiry, "%d-%b-%Y")
                days_to_expiry = (expiry_date - datetime.now()).days
                decay_monitor.initialize_position(symbol, entry_premium, max(0, days_to_expiry))
                logger.debug(f"POSITION_ADD: DECAY_MONITOR_INIT | {symbol} | days_to_expiry={days_to_expiry}")
            except Exception as e:
                logger.warning(f"POSITION_ADD: DECAY_MONITOR_INIT_FAILED | {symbol} | {str(e)}")
            
            # Register for fake move monitoring
            from .volatility_calculator import get_volatility_calculator
            vol_calc = get_volatility_calculator()
            # Extract underlying from option symbol (e.g., RELIANCE27JAN26... -> RELIANCE)
            underlying = ''.join([c for c in symbol if not c.isdigit()])
            entry_iv = vol_calc.get_dynamic_iv(underlying)
            fake_move_detector = get_fake_move_detector()
            fake_move_detector.monitor_position(symbol, entry_premium, entry_iv=entry_iv)
            
            # Log trade entry to CSV (order already placed above, so latency doesn't matter)
            try:
                trade_logger = get_trade_logger()

                # Build snapshot: TradingView signal values + live-computed candle metrics
                tech_snapshot = {}

                # 1. Values from the TradingView webhook signal (already in entry_context)
                if entry_context:
                    tech_snapshot.update({
                        'tv_rsi':          entry_context.get('rsi_value', ''),
                        'tv_adx':          entry_context.get('adx', ''),
                        'tv_vwap_pct':     entry_context.get('vwap_distance', ''),
                        'tv_macd_hist':    entry_context.get('macd_hist', ''),
                        'tv_ema_spread':   entry_context.get('ema_spread', ''),
                        'tv_vol_ratio':    entry_context.get('volume_ratio', ''),
                        'tv_day_change':   entry_context.get('day_change', ''),
                        'tv_market_trend': entry_context.get('market_trend', ''),
                    })

                # 2. Live-computed metrics from 1-min candles (EMA slopes, DI spread, BB, etc.)
                try:
                    from .technical_analyzer import TechnicalAnalyzer
                    analyzer = TechnicalAnalyzer(underlying, self.broker)
                    computed = analyzer.get_entry_snapshot()
                    tech_snapshot.update(computed)
                except Exception as snap_e:
                    logger.debug(f"POSITION_ADD: SNAPSHOT_SKIP | {underlying} | {snap_e}")

                confidence = float(entry_context.get('confidence', 85)) if entry_context else 85
                score      = float(entry_context.get('score', 85))      if entry_context else 85

                position.trade_id = trade_logger.log_trade_entry(
                    symbol=underlying,
                    action=action,
                    entry_premium=entry_premium,
                    confidence=confidence,
                    score=score,
                    features=[85, 85, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    sector_data=sector_data,
                    tech_snapshot=tech_snapshot,
                )
                logger.info(
                    f"POSITION_ADD: TRADE_LOGGED | {symbol} | trade_id={position.trade_id} | "
                    f"RSI={tech_snapshot.get('tv_rsi','?')} | ADX={tech_snapshot.get('tv_adx','?')} | "
                    f"EMA9↑={tech_snapshot.get('calc_ema9_up3','?')} | "
                    f"EMA20↑={tech_snapshot.get('calc_ema20_up3','?')} | "
                    f"DI_spread={tech_snapshot.get('calc_di_spread','?')} | "
                    f"Session={tech_snapshot.get('entry_session','?')}"
                )
            except Exception as e:
                logger.warning(f"POSITION_ADD: TRADE_LOG_FAILED | {symbol} | {str(e)}")
            
            logger.info(f"POSITION_ADD: SUCCESS | {symbol} | {contract_type} | {action} | qty={quantity} | premium=₹{entry_premium:.2f}")
            print(f"✅ Added option position: {symbol}")
            
            # 🔴 UPDATE LIVE DATA: Add trade to live tracking
            try:
                live_tracker = get_live_data_tracker()
                live_tracker.add_trade(
                    symbol=symbol,
                    underlying=underlying,
                    strike=strike,
                    contract_type=contract_type,
                    action=action,
                    quantity=quantity,
                    entry_time=position.entry_time.isoformat() if isinstance(position.entry_time, datetime) else position.entry_time,
                    entry_premium=entry_premium,
                    entry_greeks=position.entry_greeks,
                    entry_iv=position.entry_iv,
                    underlying_alert_price=underlying_alert_price,
                    trade_id=position.trade_id
                )
                live_tracker.save()
            except Exception as e:
                logger.warning(f"LIVE_DATA_TRACKING: ADD_TRADE_FAILED | {symbol} | {str(e)}")
            
            log_position("opened", {
                'symbol': symbol,
                'contract_type': contract_type,
                'action': action,
                'strike': strike,
                'expiry': expiry,
                'quantity': quantity,
                'entry_premium': entry_premium,
                'order_id': order_id,
                'underlying_alert_price': underlying_alert_price
            })
            
            return True
        except Exception as e:
            logger.error(f"POSITION_ADD: ERROR | {symbol} | {str(e)}")
            print(f"❌ Error adding option position: {str(e)}")
            return False
    
    def update_position_market_data(self, symbol: str, current_premium: float, 
                                   greeks: Dict[str, float], iv: float) -> bool:
        """Update position with current market data"""
        try:
            position = self._get_position(symbol)
            if not position:
                return False

            position.update_market_data(current_premium, greeks, iv)
            
            # 🔴 UPDATE LIVE DATA: Update trade market data
            try:
                live_tracker = get_live_data_tracker()
                trail_activation_threshold = position.trial_sl_expected_threshold or 10.0
                profile_activation_threshold, trailing_gap, trail_profile = self._get_trial_sl_profile(
                    position,
                    trail_activation_threshold,
                )
                live_tracker.update_trade(
                    symbol=symbol,
                    current_premium=current_premium,
                    current_greeks=greeks,
                    current_iv=iv,
                    highest_premium=position.highest_premium,
                    quantity=position.quantity,
                    lowest_premium=position.lowest_premium,
                    trial_sl_enabled=position.trial_sl_enabled,
                    trial_sl_price=position.trial_sl_price,
                    hard_sl_price=position.hard_sl_price,
                    trial_sl_updates=position.trial_sl_update_count,
                    trail_profile=trail_profile,
                    trail_activation_threshold=profile_activation_threshold,
                    trailing_gap=trailing_gap,
                    market_trend=position.market_trend,
                    trend_strength=position.trend_strength,
                )
            except Exception as e:
                logger.debug(f"LIVE_DATA_TRACKING: UPDATE_TRADE_FAILED | {symbol} | {str(e)}")
            
            # Check for false move reversion
            fake_move_detector = get_fake_move_detector()
            is_false_move, reason = fake_move_detector.check_false_move_exit(symbol, current_premium, iv)
            
            if is_false_move and reason:
                # Close position due to false move detected
                logger.warning(f"POSITION_UPDATE: FALSE_MOVE_DETECTED | {symbol} | {reason}")
                return self.close_position(symbol, current_premium, "FALSE_MOVE") is not None
            
            return True
        except Exception as e:
            logger.error(f"POSITION_UPDATE: ERROR | {symbol} | {str(e)}")
            print(f"❌ Error updating position {symbol}: {str(e)}")
            return False
    
    def close_position(
        self,
        symbol: str,
        exit_premium: float,
        exit_reason: str,
        *,
        broker_managed_exit: bool = False,
        skip_sl_cancel: bool = False,
        exit_order_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Close option position"""
        try:
            logger.debug(f"POSITION_CLOSE: {symbol} | reason={exit_reason} | premium={exit_premium:.2f}")
            
            if not self._get_position(symbol):
                logger.warning(f"POSITION_CLOSE: NOT_FOUND | {symbol}")
                print(f"⚠️ Position {symbol} not found")
                return None
            
            # 🔴 DOUBLE-CLOSE GUARD: Async threads (sentiment exit, BUY confirmation) and the
            # main monitor loop can both call close_position() for the same symbol at the
            # same time. Without this guard both threads place a market SELL, producing a
            # naked short position on the broker.
            with self._closing_lock:
                if symbol in self._closing_symbols:
                    logger.warning(f"POSITION_CLOSE: ALREADY_CLOSING | {symbol} | concurrent call blocked (double-SELL prevented)")
                    return None
                self._closing_symbols.add(symbol)
            
            try:
                return self._close_position_inner(
                    symbol,
                    exit_premium,
                    exit_reason,
                    broker_managed_exit=broker_managed_exit,
                    skip_sl_cancel=skip_sl_cancel,
                    exit_order_id=exit_order_id,
                )
            finally:
                with self._closing_lock:
                    self._closing_symbols.discard(symbol)
        except Exception as e:
            logger.error(f"POSITION_CLOSE: ERROR | {symbol} | {str(e)}")
            print(f"❌ Error closing position {symbol}: {str(e)}")
            with self._closing_lock:
                self._closing_symbols.discard(symbol)
            return None
    
    def _capture_live_greeks(self, position: 'OptionPosition') -> Optional[Dict[str, float]]:
        """Best-effort fetch of the position's CURRENT greeks from the live option chain.
        Used at exit so exit_greeks reflect reality instead of stale init defaults
        (refresh_position_greeks is not wired into the loop). Returns None on any failure
        so the caller falls back gracefully — must never block an exit.
        """
        if not self.broker:
            return None
        try:
            underlying_ltp = position.current_underlying_price or position.underlying_alert_price
            option_chain = self.broker.fetch_option_chain(
                position.underlying, position.expiry, current_price=underlying_ltp
            )
            if not option_chain:
                return None
            contract = option_chain.get_contract(position.strike, position.contract_type)
            if not contract or (contract.delta == 0.0 and contract.gamma == 0.0
                                and contract.theta == 0.0 and contract.vega == 0.0):
                return None
            from .volatility_calculator import get_volatility_calculator
            vol_calc = get_volatility_calculator()
            real_iv = contract.iv if contract.iv > 0 else vol_calc.get_dynamic_iv(position.symbol, default_iv=0.25)
            greeks = {
                'delta': contract.delta,
                'gamma': contract.gamma,
                'theta': contract.theta,
                'vega': contract.vega,
                'iv': real_iv,
            }
            # Keep current_greeks in sync so any downstream reader sees fresh values too.
            position.current_greeks = {k: greeks[k] for k in ('delta', 'gamma', 'theta', 'vega')}
            position.current_iv = real_iv
            logger.debug(f"EXIT_GREEKS_LIVE: {position.symbol} | delta={contract.delta:.3f} | iv={real_iv:.3f}")
            return greeks
        except Exception as e:
            logger.debug(f"EXIT_GREEKS_LIVE: fetch failed | {position.symbol} | {str(e)}")
            return None

    def _close_position_inner(
        self,
        symbol: str,
        exit_premium: float,
        exit_reason: str,
        *,
        broker_managed_exit: bool = False,
        skip_sl_cancel: bool = False,
        exit_order_id: Optional[str] = None,
    ):
        """Inner close logic (called only after the is_closing guard has been acquired)."""
        try:
            logger.debug(f"POSITION_CLOSE: INNER | {symbol} | reason={exit_reason} | premium={exit_premium:.2f}")
            
            position = self._get_position(symbol)
            if not position:
                logger.warning(f"POSITION_CLOSE: INNER_NOT_FOUND | {symbol}")
                return None
            
            # EXIT GREEKS (for ML learning) start as the cheap fallback so the critical exit
            # path — SL cancel + MARKET SELL — is NOT delayed by a chain fetch. The accurate
            # live greeks are captured AFTER the SELL is placed, just before PnL booking (below).
            exit_greeks = {
                'delta': position.current_greeks.get('delta', 0.5),
                'gamma': position.current_greeks.get('gamma', 0.05),
                'theta': position.current_greeks.get('theta', -0.02),
                'vega': position.current_greeks.get('vega', 0.1),
                'iv': position.current_iv
            }

            original_sl_order_id = position.sl_order_id
            if position.modify_pending:
                self._sync_active_sl_order_id(position)
                original_sl_order_id = position.sl_order_id

            # Cancel the active broker SL only for manual exits.
            if position.sl_order_id and self.broker and not broker_managed_exit and not skip_sl_cancel:
                logger.info(f"POSITION_CLOSE: Cancelling SL order | {symbol} | order_id={position.sl_order_id}")
                cancel_success = False
                for _cancel_attempt in range(3):
                    try:
                        cancel_success = self.broker.cancel_order(position.sl_order_id, symbol)
                        if cancel_success:
                            break
                        if _cancel_attempt < 2:
                            import time as _t; _t.sleep(0.5)
                    except Exception as cancel_error:
                        logger.error(f"POSITION_CLOSE: SL_CANCEL_EXCEPTION (attempt {_cancel_attempt+1}) | {symbol} | {str(cancel_error)}")
                
                if cancel_success:
                    logger.info(f"POSITION_CLOSE: SL_CANCELLED | {symbol} | order_id={position.sl_order_id}")
                    log_event("SL_ORDER_CANCELLED",
                             f"✅ SL order cancelled before manual exit for {symbol}",
                             symbol=symbol,
                             sl_order_id=position.sl_order_id,
                             exit_reason=exit_reason)
                    position.sl_order_id = None
                else:
                    # All 3 cancel attempts failed. Check if the SL order already fired
                    # (cancel rejected because order was COMPLETE) or is genuinely stuck.
                    failed_sl_id = position.sl_order_id
                    sl_already_filled = False
                    sl_fill_price = None
                    try:
                        order_status = self.broker.get_order_status(failed_sl_id) if self.broker else None
                        if order_status:
                            s = str(order_status.get('status', '')).upper()
                            if s in ('COMPLETE', 'FILLED', 'FULLY_FILLED'):
                                sl_already_filled = True
                                sl_fill_price = order_status.get('average_price') or position.sl_order_price
                    except Exception:
                        pass

                    if sl_already_filled:
                        # SL fired while we were trying to cancel — use fill price
                        logger.warning(
                            f"POSITION_CLOSE: SL_FIRED_DURING_CANCEL | {symbol} | "
                            f"order_id={failed_sl_id} | exit=₹{sl_fill_price} | closing as broker_managed"
                        )
                        exit_premium = sl_fill_price or exit_premium
                        broker_managed_exit = True
                        exit_order_id = failed_sl_id
                        position.sl_order_id = None
                    else:
                        # SL is still open OR status unknown. Proceed with SELL anyway.
                        # AngelOne will reject one of the two conflicting SELL orders;
                        # leaving the position permanently open is worse than the tiny
                        # double-fill risk from a SL that may execute a millisecond later.
                        logger.error(
                            f"POSITION_CLOSE: SL_CANCEL_FAILED_PROCEEDING | {symbol} | "
                            f"order_id={failed_sl_id} | placing MARKET SELL despite cancel failure"
                        )
                        log_event("SL_CANCEL_FAILED",
                                 f"⚠️ SL cancel failed for {symbol} — proceeding with MARKET SELL",
                                 symbol=symbol,
                                 sl_order_id=failed_sl_id,
                                 exit_reason=exit_reason,
                                 action="MARKET_SELL_PROCEEDING")
                        position.sl_order_id = None
            elif skip_sl_cancel and position.sl_order_id:
                position.sl_order_id = None
            
            # 🔴 CRITICAL: Place SELL order to broker in LIVE mode with RETRY LOGIC
            # This ensures position is actually closed on broker, not just locally
            if broker_managed_exit:
                position.exit_order_id = exit_order_id or original_sl_order_id
            elif self.broker and OptionsTradingConfig.TRADING_MODE == "LIVE":
                exit_order_id = None
                max_retries = 5
                retry_delays = [1, 2, 4, 8, 16]  # Exponential backoff in seconds
                last_error = None
                
                for attempt in range(max_retries):
                    logger.info(f"POSITION_CLOSE: Placing EXIT SELL order (attempt {attempt + 1}/{max_retries}) | {symbol} | qty={position.quantity} | premium=₹{exit_premium:.2f}")
                    
                    try:
                        exit_order_id = self.broker.place_options_order(
                            symbol=symbol,
                            action='SELL',
                            quantity=position.quantity,
                            price=exit_premium,  # Market order at current LTP
                            order_type='MARKET',
                            product_type='INTRADAY',
                            allow_queue=False,
                        )
                        
                        if exit_order_id:
                            position.exit_order_id = exit_order_id
                            logger.info(f"POSITION_CLOSE: EXIT_ORDER_PLACED (attempt {attempt + 1}/{max_retries}) | {symbol} | order_id={exit_order_id}")
                            log_event("EXIT_ORDER_PLACED",
                                     f"✅ Exit SELL order placed for {symbol}",
                                     symbol=symbol,
                                     order_id=exit_order_id,
                                     exit_premium=round(exit_premium, 2),
                                     quantity=position.quantity,
                                     exit_reason=exit_reason,
                                     attempt=attempt + 1)

                            fill_status = self._wait_for_exit_fill(exit_order_id, symbol)
                            if not fill_status:
                                log_event(
                                    "EXIT_ORDER_UNCONFIRMED",
                                    f"⚠️ Exit SELL order not confirmed filled for {symbol} - keeping local position open",
                                    symbol=symbol,
                                    order_id=exit_order_id,
                                    exit_reason=exit_reason,
                                )
                                return None

                            confirmed_exit_price = fill_status.get('average_price') or exit_premium
                            if confirmed_exit_price > 0:
                                exit_premium = confirmed_exit_price
                            break  # Success - stop retrying
                        
                        # Order failed - check if retryable
                        if attempt < max_retries - 1:
                            actual_reason = getattr(self.broker, 'last_order_error', 'Unknown error')
                            
                            # Check if this is retryable (broker API issue, not fundamental problem)
                            is_retryable = (
                                'NoneType' in str(actual_reason) or
                                'API' in str(actual_reason) or
                                'timeout' in str(actual_reason).lower()
                            )
                            
                            if is_retryable:
                                wait_time = retry_delays[attempt]
                                logger.warning(f"POSITION_CLOSE: EXIT_ORDER_RETRY_SCHEDULED | {symbol} will retry in {wait_time}s | Reason: {actual_reason}")
                                import time
                                time.sleep(wait_time)
                                continue
                            else:
                                # Not retryable - stop trying
                                last_error = actual_reason
                                logger.error(f"POSITION_CLOSE: EXIT_ORDER_NOT_RETRYABLE | {symbol} | {actual_reason}")
                                break
                        else:
                            # Max retries exhausted
                            last_error = actual_reason if 'actual_reason' in locals() else "Max retries exhausted"
                            logger.error(f"POSITION_CLOSE: EXIT_ORDER_RETRIES_EXHAUSTED | {symbol} | Final error: {last_error}")
                            break
                    
                    except Exception as exit_error:
                        logger.error(f"POSITION_CLOSE: EXIT_ORDER_EXCEPTION (attempt {attempt + 1}/{max_retries}) | {symbol} | {str(exit_error)}")
                        
                        if attempt < max_retries - 1:
                            wait_time = retry_delays[attempt]
                            logger.warning(f"POSITION_CLOSE: EXIT_ORDER_RETRY_AFTER_EXCEPTION | {symbol} will retry in {wait_time}s")
                            import time
                            time.sleep(wait_time)
                        else:
                            last_error = str(exit_error)
                
                # Check if exit order was eventually placed
                if not exit_order_id:
                    log_event("EXIT_ORDER_CRITICAL_FAILURE",
                             f"❌ Failed to place EXIT SELL order for {symbol} after {max_retries} attempts",
                             symbol=symbol,
                             final_error=last_error,
                             exit_reason=exit_reason,
                             risk="Position may remain open on broker - manual intervention may be needed")
                    return None
            
            # ── EXIT SLIPPAGE MODELING (PAPER): book the realistic bid fill, not the trigger ──
            # broker_managed_exit means a real broker SL fill price is already set — don't override.
            exit_slippage_meta = None
            if not broker_managed_exit:
                exit_slippage_meta = self._model_paper_exit_slippage(position, exit_premium, exit_reason)
                if exit_slippage_meta.get('applied'):
                    _ideal_exit = exit_premium
                    exit_premium = exit_slippage_meta['fill']
                    logger.info(
                        f"SLIPPAGE_EXIT: {symbol} | reason={exit_reason} | ideal=₹{_ideal_exit:.2f} "
                        f"| bid_fill=₹{exit_premium:.2f} | slippage={exit_slippage_meta['slippage_pct']:.2f}%"
                    )
                log_event(
                    "SLIPPAGE_EXIT",
                    f"📐 Exit slippage modeled for {symbol}",
                    symbol=symbol,
                    **exit_slippage_meta,
                )

            # Now that the SELL is placed/filled, capture accurate live greeks for ML (off the
            # critical path). Best-effort — falls back to the cheap greeks set above on failure.
            live_greeks = self._capture_live_greeks(position)
            if live_greeks:
                exit_greeks = live_greeks

            pnl_info = position.close_position(exit_premium, exit_reason, exit_greeks=exit_greeks)  # ADDED: Pass exit Greeks
            if exit_slippage_meta is not None and isinstance(pnl_info, dict):
                pnl_info['exit_slippage'] = exit_slippage_meta

            # Log trade exit to CSV
            try:
                trade_logger = get_trade_logger()
                if hasattr(position, 'trade_id') and position.trade_id:
                    trade_logger.log_trade_exit(
                        trade_id=position.trade_id,
                        exit_premium=exit_premium,
                        pnl=pnl_info['pnl'],
                        exit_reason=exit_reason
                    )
                    logger.debug(f"POSITION_CLOSE: TRADE_LOGGED | {symbol} | trade_id={position.trade_id} | PnL={pnl_info['pnl']}")
                else:
                    logger.warning(f"POSITION_CLOSE: NO_TRADE_ID | {symbol}")
            except Exception as e:
                logger.warning(f"POSITION_CLOSE: TRADE_LOG_FAILED | {symbol} | {str(e)}")
            
            # RECORD OUTCOME FOR ML LEARNING (NEW)
            try:
                from .opt_ml_integration import get_ml_integration
                from .ml_integration_engine import get_ml_integration_engine
                from .options_learning_engine import get_symbol_tracker
                
                ml_integration = get_ml_integration()
                ml_engine = get_ml_integration_engine()
                symbol_tracker = get_symbol_tracker()
                
                # Record trade outcome: WIN if PnL > 0, LOSS otherwise
                trade_outcome = {
                    'symbol': symbol,
                    'underlying': position.underlying,
                    'action': position.action,
                    'pnl': pnl_info['pnl'],
                    'pnl_percent': pnl_info['pnl_percent'],
                    'exit_reason': exit_reason,
                    'entry_time': pnl_info.get('entry_time'),
                    'duration_seconds': pnl_info.get('duration', 0),
                    'contract_type': position.contract_type,
                    'strike': position.strike,
                    'quantity': position.quantity,
                    'won': pnl_info['pnl'] > 0,  # WIN if profitable
                    'entry_greeks': position.entry_greeks,  # ADDED: Entry Greeks
                    'exit_greeks': position.exit_greeks,    # ADDED: Exit Greeks
                    'entry_iv': position.entry_iv,          # Entry IV
                    'exit_iv': position.current_iv,         # Exit IV
                    'entry_pcr': position.entry_pcr,        # Entry Put-Call Ratio (sentiment)
                    'exit_pcr': position.current_pcr,       # Exit PCR
                    'entry_oi': position.entry_oi_buildup,  # Entry Open Interest buildup
                    'exit_oi': position.current_oi          # Exit Open Interest
                }
                
                # Record the close once through the ML engine, which also forwards
                # the trade into the daily integration pipeline.
                ml_engine.record_closed_trade(trade_outcome)
                
                # CRITICAL FIX: Also record to SymbolPerformanceTracker for symbol_stats.json
                underlying = position.underlying
                symbol_tracker.record_trade(
                    symbol=underlying,  # Use underlying (BANKNIFTY, not BANKNIFTY26DEC24000CE)
                    won=pnl_info['pnl'] > 0,
                    profit=pnl_info['pnl'],
                    predicted_prob=0.5,  # Default prob if not available
                    trading_mode=OptionsTradingConfig.TRADING_MODE
                )
                
                logger.info(f"ML_OUTCOME_RECORDED: {symbol} | {'WIN' if trade_outcome['won'] else 'LOSS'} | "
                           f"PnL=₹{pnl_info['pnl']:.2f} | {exit_reason}")
                
            except Exception as e:
                logger.warning(f"ML_OUTCOME_RECORD_FAILED: {symbol} | {str(e)}")
                # Don't block position close on ML recording error

            if self.broker and OptionsTradingConfig.TRADING_MODE == "LIVE":
                try:
                    cleanup_exclusions = [original_sl_order_id, position.exit_order_id, exit_order_id]
                    cancelled_orders = self.broker.cancel_outstanding_orders_for_symbol(symbol, cleanup_exclusions)
                    if cancelled_orders:
                        log_event(
                            "ORDER_CLEANUP_EXECUTED",
                            f"🧹 Cancelled outstanding broker orders after close for {symbol}",
                            symbol=symbol,
                            cancelled_order_ids=cancelled_orders,
                            exit_reason=exit_reason,
                        )
                except Exception as cleanup_error:
                    logger.error(f"POSITION_CLOSE: ORDER_CLEANUP_ERROR | {symbol} | {str(cleanup_error)}")
            
            # Move to history (cap at 100 to prevent unbounded memory growth)
            self.closed_positions.append(position)
            if len(self.closed_positions) > 100:
                self.closed_positions = self.closed_positions[-100:]
            with self._positions_lock:
                self.positions.pop(symbol, None)
            self._realtime_fade_last_check.pop(symbol, None)
            
            # REMOVE FROM ACTIVE SYMBOL POOL
            self.symbol_pool.remove_symbol(symbol, exit_reason=exit_reason)
            
            # Clear fake move monitoring
            fake_move_detector = get_fake_move_detector()
            fake_move_detector.close_position_monitoring(symbol)
            
            # 🔴 UPDATE LIVE DATA: Close trade in live tracking
            try:
                live_tracker = get_live_data_tracker()
                trail_activation_threshold = position.trial_sl_expected_threshold or 10.0
                profile_activation_threshold, trailing_gap, trail_profile = self._get_trial_sl_profile(
                    position,
                    trail_activation_threshold,
                )
                live_tracker.close_trade(
                    symbol=symbol,
                    exit_time=datetime.now().isoformat(),
                    exit_premium=pnl_info['exit_premium'],
                    exit_reason=exit_reason,
                    exit_greeks=position.exit_greeks,
                    exit_iv=position.current_iv,
                    quantity=position.quantity,
                    entry_premium=pnl_info['entry_premium'],
                    entry_time=pnl_info.get('entry_time', ''),
                    trail_profile=trail_profile,
                    trail_activation_threshold=profile_activation_threshold,
                    trailing_gap=trailing_gap,
                    market_trend=position.market_trend,
                    trend_strength=position.trend_strength,
                )
                live_tracker.save()
            except Exception as e:
                logger.warning(f"LIVE_DATA_TRACKING: CLOSE_TRADE_FAILED | {symbol} | {str(e)}")
            
            self._save_positions()
            self._save_pnl_history(pnl_info)
            
            logger.info(f"POSITION_CLOSE: SUCCESS | {symbol} | {exit_reason} | PnL=₹{pnl_info['pnl']:.2f} ({pnl_info['pnl_percent']:.2f}%)")
            print(f"✅ Closed position {symbol}: {exit_reason} | PnL: ₹{pnl_info['pnl']:.2f}")
            
            log_position("closed", pnl_info)
            log_pnl(symbol, pnl_info['pnl'], pnl_info['pnl_percent'], exit_reason)
            
            return pnl_info
        except Exception as e:
            logger.error(f"POSITION_CLOSE: INNER_ERROR | {symbol} | {str(e)}")
            print(f"❌ Error closing position {symbol}: {str(e)}")
            return None

    def _wait_for_exit_fill(self, order_id: str, symbol: str, timeout: int = 20) -> Optional[Dict[str, Any]]:
        """Wait for a manual exit order to reach a filled broker state before closing local position state."""
        if OptionsTradingConfig.TRADING_MODE != "LIVE" or not self.broker or not order_id:
            return {'order_id': order_id, 'average_price': 0.0, 'status': 'SKIPPED'}

        start_time = time.time()
        filled_statuses = {'COMPLETE', 'FILLED', 'FULLY_FILLED'}
        inactive_statuses = {'REJECTED', 'CANCELLED', 'EXPIRED'}

        # Poll at 1.0s — AngelOne caps getOrderBook (the source of get_order_status) at 1/sec
        # per client. Polling faster just gets denied by the per-endpoint gate and wastes cycles.
        poll_interval = 1.0
        while time.time() - start_time < timeout:
            order_status = self.broker.get_order_status(order_id)
            if not order_status:
                time.sleep(poll_interval)
                continue

            status = str(order_status.get('status', '')).upper()
            if status in filled_statuses:
                logger.info(
                    f"EXIT_FILL_CONFIRMED: {symbol} | order_id={order_id} | status={status} | avg=₹{order_status.get('average_price', 0.0):.2f}"
                )
                return order_status

            if status in inactive_statuses:
                logger.error(f"EXIT_FILL_FAILED: {symbol} | order_id={order_id} | status={status}")
                return None

            logger.debug(f"EXIT_FILL_WAITING: {symbol} | order_id={order_id} | status={status}")
            time.sleep(poll_interval)

        logger.error(f"EXIT_FILL_TIMEOUT: {symbol} | order_id={order_id} | waited={timeout}s")
        return None

    def _sync_active_sl_order_id(self, position: OptionPosition) -> Optional[str]:
        """Refresh local SL order id from broker when modify flows may have replaced it."""
        if OptionsTradingConfig.TRADING_MODE != "LIVE" or not self.broker or not position:
            return getattr(position, 'sl_order_id', None)

        get_order_book = getattr(self.broker, 'get_order_book', None)
        if not callable(get_order_book):
            return position.sl_order_id

        try:
            order_book = get_order_book()
            if not order_book:
                return position.sl_order_id

            terminal_statuses = {'COMPLETE', 'FILLED', 'FULLY_FILLED', 'REJECTED', 'CANCELLED', 'CANCELED', 'EXPIRED'}
            latest_active_order_id = None

            for order in order_book:
                trading_symbol = str(order.get('tradingsymbol') or order.get('symbol') or '')
                if trading_symbol != position.symbol:
                    continue

                status = str(order.get('orderstatus') or order.get('orderstate') or order.get('status') or '').upper()
                if status in terminal_statuses:
                    continue

                transaction_type = str(order.get('transactiontype') or order.get('transaction_type') or '').upper()
                order_type = str(order.get('ordertype') or order.get('order_type') or '').upper()
                variety = str(order.get('variety') or '').upper()
                if transaction_type != 'SELL':
                    continue
                if 'STOPLOSS' not in order_type and variety != 'STOPLOSS':
                    continue

                candidate_order_id = str(order.get('orderid') or order.get('order_id') or '')
                if candidate_order_id:
                    latest_active_order_id = candidate_order_id

            if latest_active_order_id and latest_active_order_id != position.sl_order_id:
                logger.warning(
                    f"SL_ORDER_SYNC: UPDATED | {position.symbol} | old={position.sl_order_id} | new={latest_active_order_id}"
                )
                position.sl_order_id = latest_active_order_id
                self._save_positions()

            return position.sl_order_id
        except Exception as sync_error:
            logger.debug(f"SL_ORDER_SYNC: SKIPPED | {position.symbol} | {str(sync_error)}")
            return position.sl_order_id

    def _model_paper_exit_slippage(self, position, intended_exit: float, exit_reason: str) -> Dict[str, Any]:
        """
        PAPER-mode exit slippage model. The old PAPER path booked the SL trigger / current LTP as
        the exit (zero slippage). A real SELL of a long option fills at the BID. This fetches the
        live bid and returns the realistic fill + metadata for analysis. LIVE is untouched (it
        already books the broker fill). Falls back to intended_exit if depth is unavailable.
        """
        meta = {
            'intended_exit': round(float(intended_exit or 0.0), 2),
            'reason': exit_reason,
            'mode': OptionsTradingConfig.TRADING_MODE,
            'applied': False,
        }
        if OptionsTradingConfig.TRADING_MODE == "LIVE":
            return meta  # LIVE already books the real broker average_price
        try:
            md = self.broker.get_market_data(position.symbol, "NFO") if self.broker else None
        except Exception as e:
            logger.debug(f"SLIPPAGE_EXIT: depth fetch failed | {position.symbol} | {str(e)[:60]}")
            md = None
        if not md:
            meta['note'] = 'no_depth'
            return meta
        bid = float(md.get('bid') or 0.0)
        ask = float(md.get('ask') or 0.0)
        ltp = float(md.get('ltp') or 0.0)
        spread_pct = md.get('bid_ask_spread_pct')
        if spread_pct is None and bid > 0 and ask > 0 and ltp > 0:
            spread_pct = (ask - bid) / ltp * 100.0
        # Synthetic guard: chain fallback makes bid=ltp*0.98 / ask=ltp*1.02.
        synthetic = bool(
            ltp > 0 and bid > 0 and ask > 0
            and abs(bid - ltp * 0.98) < 0.01 and abs(ask - ltp * 1.02) < 0.01
        )
        meta.update({
            'real_bid': round(bid, 2),
            'real_ask': round(ask, 2),
            'ltp': round(ltp, 2),
            'spread_pct': round(float(spread_pct), 3) if spread_pct is not None else None,
            'spread_is_synthetic': synthetic,
        })
        if OptionsTradingConfig.PAPER_SLIPPAGE_MODELING and bid > 0 and not synthetic:
            meta['fill'] = round(bid, 2)
            meta['slippage_pct'] = (
                round((bid - intended_exit) / intended_exit * 100, 3) if intended_exit > 0 else 0.0
            )
            meta['applied'] = True
        else:
            meta['fill'] = round(float(intended_exit or 0.0), 2)
            meta['slippage_pct'] = 0.0
        return meta

    def _reconcile_broker_stop_exit(self, symbol: str, expected_exit_price: float, exit_reason: str) -> Optional[Dict[str, Any]]:
        """Finalize local position state only after the broker stop order reaches a terminal state."""
        position = self._get_position(symbol)
        if not position:
            return None

        if OptionsTradingConfig.TRADING_MODE != "LIVE" or not self.broker or not position.sl_order_id:
            return self.close_position(symbol, expected_exit_price, exit_reason)

        order_status = self.broker.get_order_status(position.sl_order_id)
        if not order_status:
            logger.warning(f"BROKER_SL_STATUS: UNKNOWN | {symbol} | order_id={position.sl_order_id} | waiting for broker update")
            return None

        status = order_status.get('status', '')
        filled_statuses = {'COMPLETE', 'FILLED', 'FULLY_FILLED'}
        inactive_statuses = {'REJECTED', 'CANCELLED', 'EXPIRED'}

        if status in filled_statuses:
            actual_exit_price = order_status.get('average_price') or position.sl_order_price or expected_exit_price
            logger.info(
                f"BROKER_SL_FILLED: {symbol} | order_id={position.sl_order_id} | status={status} | exit=₹{actual_exit_price:.2f}"
            )
            return self.close_position(
                symbol,
                actual_exit_price,
                exit_reason,
                broker_managed_exit=True,
                skip_sl_cancel=True,
                exit_order_id=position.sl_order_id,
            )

        if status in inactive_statuses:
            logger.error(
                f"BROKER_SL_INACTIVE: {symbol} | order_id={position.sl_order_id} | status={status} | attempting emergency manual exit"
            )
            return self.close_position(
                symbol,
                position.current_premium if position.current_premium > 0 else expected_exit_price,
                f"{exit_reason} | BROKER_SL_{status}",
                skip_sl_cancel=True,
            )

        logger.info(
            f"BROKER_SL_PENDING: {symbol} | order_id={position.sl_order_id} | status={status} | waiting for broker fill"
        )
        return None
    
    def place_stop_loss_order(self, symbol: str) -> bool:
        """
        Place SL order to broker after BUY (for LIVE mode protection)
        
        Similar to equity bot - ensures broker executes SL even if bot crashes.
        Uses AngelOne STOPLOSS_MARKET with the configured stop-loss percentage.
        
        Returns:
            True if SL order placed successfully, False otherwise
        """
        position = self._get_position(symbol)
        if not position:
            logger.warning(f"PLACE_SL: Position not found | {symbol}")
            return False
        
        # Skip if SL order already placed
        if position.sl_order_id:
            logger.debug(f"PLACE_SL: Already placed | {symbol} | order_id={position.sl_order_id}")
            return True
        
        # Calculate SL from entry premium using configured SL% with 10 paise rounding
        sl_premium_raw = position.entry_premium * (1 - OptionsTradingConfig.STOP_LOSS_PERCENTAGE / 100)
        sl_premium = self._round_to_10_paise(sl_premium_raw)
        
        logger.info(f"PLACE_SL: {symbol} | Entry: ₹{position.entry_premium:.2f} | SL: ₹{sl_premium:.2f} (-{OptionsTradingConfig.STOP_LOSS_PERCENTAGE:.0f}%)")
        
        try:
            # Place SELL order with STOPLOSS_MARKET so AngelOne executes the exit as soon as
            # the trigger is hit. We keep explicit no-queue handling so order_ids are real.
            sl_order_id = self.broker.place_options_order(
                symbol=symbol,
                action='SELL',
                quantity=position.quantity,
                price=sl_premium,
                order_type='STOPLOSS_MARKET',
                product_type='INTRADAY',
                allow_queue=False  # ❌ NEVER queue SL orders — must have real order_id
            )
            
            if sl_order_id and not str(sl_order_id).startswith("QUEUED_"):
                position.sl_order_id = sl_order_id
                position.sl_order_id = sl_order_id
                position.sl_order_price = sl_premium
                self._save_positions()
                
                logger.info(f"PLACE_SL: SUCCESS | {symbol} | order_id={sl_order_id} | SL=₹{sl_premium:.2f}")
                log_event("SL_ORDER_PLACED",
                         f"✅ SL order placed for {symbol}",
                         symbol=symbol,
                         order_id=sl_order_id,
                         sl_price=sl_premium,
                         entry_premium=position.entry_premium,
                         quantity=position.quantity)
                return True
            else:
                logger.error(f"PLACE_SL: FAILED | {symbol} | broker returned None")
                return False
                
        except Exception as e:
            logger.error(f"PLACE_SL: ERROR | {symbol} | {str(e)}")
            return False
    
    def _round_to_10_paise(self, price: float) -> float:
        """
        Round price to nearest 10 paise (₹0.10) for AngelOne broker compliance.
        
        AngelOne requires STOPLOSS orders to be in multiples of 10 paise.
        
        Examples:
            12.34 → 12.30
            12.37 → 12.40
            12.35 → 12.40 (round up at midpoint)
        """
        return round(price * 10) / 10  # Round to 1 decimal place (10 paise)
    
    def should_modify_sl(self, symbol: str, new_sl_price: float) -> Dict[str, Any]:
        """
        Intelligent SL modification with rate-limiting (OPTIMIZED):
        
        1. ADAPTIVE: Skip if SL change < 1% (avoid micro-updates)
        2. QUEUE: If rate limited, mark for retry
        3. IMMEDIATE: Modify as soon as milestone hit (no 30s delay)
        
        Returns: {
            'should_modify': bool,
            'reason': str,
            'strategy': str  # 'adaptive', 'queue', 'ready'
        }
        """
        position = self._get_position(symbol)
        if not position:
            return {'should_modify': False, 'reason': 'position_not_found', 'strategy': None}

        now = datetime.now()
        symbol_phase = (sum(ord(ch) for ch in symbol) % 5) * 0.15
        min_modify_gap_seconds = 1.0 + symbol_phase

        if position.next_modify_earliest_time and now < position.next_modify_earliest_time:
            wait_seconds = (position.next_modify_earliest_time - now).total_seconds()
            position.modify_pending = True
            position.last_attempted_sl_price = new_sl_price
            return {
                'should_modify': False,
                'reason': f'modify_cooldown_{wait_seconds:.1f}s',
                'strategy': 'cooldown'
            }
        
        # STRATEGY 1: ADAPTIVE MODIFY - Only if SL change > 1% (reduced from 2%)
        # Milestones happen fast, so need lower threshold to catch them
        if position.last_attempted_sl_price is not None:
            change_percent = abs(new_sl_price - position.last_attempted_sl_price) / position.last_attempted_sl_price * 100
            if change_percent < 1.0:  # Less than 1% change - skip
                return {
                    'should_modify': False,
                    'reason': f'small_change_{change_percent:.1f}%',
                    'strategy': 'adaptive'
                }
        
        # STRATEGY 2: CHECK RATE LIMIT (immediate, no batching)
        if self.broker:
            rate_limiter = get_options_rate_limiter()
            utilization = rate_limiter.get_utilization()
            
            # Only queue if critically high load (>90%), otherwise try immediately
            if utilization > 0.90:
                position.modify_pending = True
                position.last_attempted_sl_price = new_sl_price
                logger.debug(f"MODIFY_SL: QUEUED (critical load) | {symbol} | utilization={utilization:.1%} | new_sl=₹{new_sl_price:.2f}")
                return {
                    'should_modify': False,
                    'reason': f'rate_limit_critical_{utilization:.1%}',
                    'strategy': 'queue'
                }
        
        # All checks passed - ready to modify IMMEDIATELY
        position.last_attempted_sl_price = new_sl_price
        position.next_modify_earliest_time = now + timedelta(seconds=min_modify_gap_seconds)
        return {
            'should_modify': True,
            'reason': 'milestone_detected_modify_now',
            'strategy': 'ready'
        }
    
    def modify_sl_order(self, symbol: str, new_sl_price: float, order_id: Optional[str] = None) -> bool:
        """
        Modify stop-loss price for an options order with rate limiting
        
        Args:
            symbol: Option symbol (e.g., INFY30DEC251540CE)
            new_sl_price: New SL premium price
            order_id: Order ID (if not provided, uses first order for symbol)
        
        Returns:
            True if modify successful or queued, False if failed
        """
        if not self.broker:
            logger.warning(f"MODIFY_SL: No broker available | {symbol}")
            return False
        
        if symbol in self._closing_symbols:
            logger.debug(f"MODIFY_SL: SKIPPED | {symbol} | close already in progress")
            return False

        position = self._get_position(symbol)
        if not position:
            logger.warning(f"MODIFY_SL: Position not found | {symbol}")
            return False
        
        # Check if we should modify using intelligent strategy
        check_result = self.should_modify_sl(symbol, new_sl_price)
        if not check_result['should_modify']:
            logger.debug(f"MODIFY_SL: SKIPPED ({check_result['strategy']}) | {symbol} | reason={check_result['reason']}")
            return False  # Skipped due to rate limiting or adaptive logic
        
        try:
            rate_limiter = get_options_rate_limiter()  # kept for record_call() stats only

            # NO rate-limiter slot wait here. broker.modify_order hits AngelOne's modifyOrder
            # endpoint directly (separate, NOT gated by the shared market-data limiter) and does
            # NO market fetch (local token lookup only). Acquiring a slot here just added latency
            # to the TRIAL_SL trail for no protection — the should_modify_sl gate already throttles
            # modify frequency (skips <1% changes). Trail now modifies in milliseconds.

            # Update position tracking (last_modify_time first; last_modified_sl_price set ONLY on confirmed broker success)
            position.last_modify_time = datetime.now()
            position.modify_pending = False

            new_sl_price = self._round_to_10_paise(new_sl_price)
            active_order_id = order_id or position.sl_order_id

            if not active_order_id:
                logger.warning(f"MODIFY_SL: SKIPPED | {symbol} | no active SL order id")
                return False

            if order_id and position.sl_order_id and position.sl_order_id != order_id:
                logger.warning(
                    f"MODIFY_SL: STALE_ORDER_ID | {symbol} | requested={order_id} | current={position.sl_order_id} | modify skipped"
                )
                return False

            # Call broker API to actually modify the order (LIVE mode)
            if OptionsTradingConfig.TRADING_MODE == "LIVE":
                try:
                    result = self.broker.modify_order(
                        order_id=active_order_id,
                        symbol=symbol,
                        new_price=new_sl_price,
                        quantity=position.quantity,
                        order_type='STOPLOSS_MARKET'
                    )
                    if result and not str(result).startswith("QUEUED_"):
                        # AngelOne's modifyOrder returns a NEW order_id for the modified order.
                        # We MUST update sl_order_id here — cancel_order() in close_position()
                        # uses sl_order_id. If we don't update it, cancel sends the OLD id,
                        # the broker rejects it, and the modified SL stays live → fires → SHORT.
                        if result != active_order_id:
                            logger.info(f"MODIFY_SL: ORDER_ID_UPDATED | {symbol} | old={active_order_id} | new={result}")
                        position.sl_order_id = result
                        position.sl_order_price = new_sl_price
                        position.last_modified_sl_price = new_sl_price  # Only set after confirmed broker success
                    elif result and str(result).startswith("QUEUED_"):
                        logger.warning(f"MODIFY_SL: QUEUED | {symbol} | order_id={active_order_id} | queued_id={result}")
                        position.modify_pending = True
                        self._sync_active_sl_order_id(position)
                        return True
                    elif not result:
                        logger.warning(f"MODIFY_SL: Broker API failed | {symbol} | order_id={active_order_id}")
                        rate_limiter.record_call("modify_order", False)
                        return False
                except Exception as e:
                    logger.warning(f"MODIFY_SL: Broker API error | {symbol} | {str(e)}")
                    rate_limiter.record_call("modify_order", False)
                    return False
            
            # Record successful modify
            rate_limiter.record_call("modify_order", True)
            
            # previous SL may be None when the breakeven floor moves the SL before the trail arms.
            _prev_sl = position.trial_sl_price if position.trial_sl_price is not None else position.hard_sl_price
            logger.info(f"MODIFY_SL: SUCCESS | {symbol} | new_sl=₹{new_sl_price:.2f} | previous=₹{(_prev_sl or 0.0):.2f}")
            log_event("MODIFY_SL",
                     f"✅ SL modified for {symbol}",
                     symbol=symbol,
                     new_sl_price=round(new_sl_price, 2),
                     previous_sl=round(position.trial_sl_price, 2) if position.trial_sl_price else None,
                     peak_premium=round(position.highest_premium, 2),
                     current_premium=round(position.current_premium, 2))
            
            return True
        
        except Exception as e:
            logger.error(f"MODIFY_SL: ERROR | {symbol} | {str(e)}")
            rate_limiter.record_call("modify_order", False)
            return False
    
    def retry_failed_sl_orders(self) -> Dict[str, int]:
        """Retry SL placement for positions that don't have SL yet
        
        This runs in the background monitoring loop to ensure all positions
        have SL orders within 2-3 monitoring cycles (30-45 seconds).
        
        Strategy:
        - Track retry count per position
        - Max 5 retries with exponential backoff
        - Use price variants to work around execution delays
        - Log all retry attempts for debugging
        
        Returns:
            Dict with retry statistics: {'attempted': count, 'placed': count, 'max_retries': count}
        """
        stats = {'attempted': 0, 'placed': 0, 'max_retries': 0}
        max_sl_retries = 5
        
        for symbol, position in self._snapshot_positions_items():
            
            # Skip if already has SL
            if position.sl_order_id:
                if hasattr(position, 'sl_retry_count') and position.sl_retry_count > 0:
                    logger.debug(f"RETRY_SL: SL_PLACED_AFTER_RETRIES | {symbol} | retries={position.sl_retry_count}")
                    position.sl_retry_count = 0  # Reset count
                continue
            
            # Position missing SL - needs retry
            if not hasattr(position, 'sl_retry_count'):
                position.sl_retry_count = 0
            
            # Check max retries
            if position.sl_retry_count >= max_sl_retries:
                logger.error(f"RETRY_SL: MAX_RETRIES_EXCEEDED | {symbol} | retries={position.sl_retry_count} | SL STILL NOT PLACED - POSITION AT RISK")
                stats['max_retries'] += 1
                continue
            
            stats['attempted'] += 1
            position.sl_retry_count += 1
            # BUG FIX #7: Use entry_premium (not entry_price) — consistent with place_stop_loss_order()
            current_sl_price = self._round_to_10_paise(
                position.entry_premium * (1 - OptionsTradingConfig.STOP_LOSS_PERCENTAGE / 100))
            
            logger.warning(f"RETRY_SL: Attempting SL placement (retry #{position.sl_retry_count}/{max_sl_retries}) | {symbol} | sl_price=₹{current_sl_price:.2f}")
            
            try:
                # Get rate limiter
                rate_limiter = get_options_rate_limiter()
                
                # Wait for rate limit permission
                if not rate_limiter.wait_for_call_permission(timeout=5.0, request_type="place_order"):
                    logger.debug(f"RETRY_SL: RATE_LIMITED | {symbol} | will retry next cycle")
                    position.sl_retry_count -= 1  # Don't count rate limit as a real attempt
                    continue
                
                # Place SL order if in LIVE mode
                if self.broker and OptionsTradingConfig.TRADING_MODE == "LIVE":
                    sl_order_id = self.broker.place_options_order(
                        symbol=symbol,
                        action='SELL',          # BUG FIX #5: Must be SELL (bot is LONG options)
                        quantity=position.quantity,
                        price=current_sl_price,
                        order_type='STOPLOSS_MARKET',
                        product_type='INTRADAY',
                        allow_queue=False  # ❌ NEVER queue SL — must have real order_id
                    )
                    
                    if sl_order_id and not str(sl_order_id).startswith("QUEUED_"):
                        position.sl_order_id = sl_order_id
                        position.sl_order_price = current_sl_price
                        stats['placed'] += 1
                        logger.info(f"RETRY_SL: SUCCESS | {symbol} | order_id={sl_order_id} | retry_count={position.sl_retry_count}")
                        log_event("RETRY_SL_SUCCESS",
                                 f"✅ SL placement succeeded on retry #{position.sl_retry_count}",
                                 symbol=symbol,
                                 order_id=sl_order_id,
                                 sl_price=round(current_sl_price, 2),
                                 retry_number=position.sl_retry_count)
                    else:
                        logger.warning(f"RETRY_SL: FAILED | {symbol} | broker returned None | will retry next cycle")
                else:
                    logger.debug(f"RETRY_SL: SKIPPED (not LIVE mode or no broker) | {symbol}")
            
            except Exception as sl_error:
                logger.error(f"RETRY_SL: ERROR | {symbol} | {str(sl_error)} | will retry next cycle")
        
        # Log summary if any retries attempted
        if stats['attempted'] > 0:
            logger.info(f"RETRY_SL: CYCLE_SUMMARY | attempted={stats['attempted']} | placed={stats['placed']} | max_retries_hit={stats['max_retries']}")
        
        return stats
    
    def check_expiry_close(self) -> List[Dict[str, Any]]:
        """Check and close positions near expiry (configured days)
        
        Set EXPIRY_DAYS_TO_CLOSE = -1 to disable auto-close and allow trading through expiry day
        This enables NSE expiry Thursday scalping with peak liquidity
        """
        closed = []
        days_to_close = OptionsTradingConfig.EXPIRY_DAYS_TO_CLOSE
        
        # -1 = disabled (allow trading through expiry)
        if days_to_close < 0:
            logger.debug(f"POSITION_MONITOR: Expiry auto-close disabled | allowing positions to run through expiry")
            return closed
        
        for symbol, position in self._snapshot_positions_items():
            
            if position.days_to_expiry() <= days_to_close:
                # Close at current market price (current premium)
                pnl = self.close_position(symbol, position.current_premium, "EXPIRY")
                if pnl:
                    closed.append(pnl)
        
        return closed
    
    def check_profit_targets(self) -> List[Dict[str, Any]]:
        """Close positions with intelligent trailing exit strategy
        
        TRIAL MODE: NO profit targets - let winners run!
        Only exit on trailing SL (20% below peak) or hard SL (20% loss).
        
        Strategy:
        - No fixed profit target
        - Let winners run to maximum
        - Only exit when position deteriorates
        - Trail SL up every 10% gain to lock profits
        """
        # TRIAL MODE: Profit target disabled (set to 0)
        profit_target = OptionsTradingConfig.PROFIT_TARGET_PERCENTAGE
        
        # If profit target is 0 or disabled, skip profit target exits
        if profit_target <= 0:
            logger.debug("PROFIT_TARGETS: DISABLED (Trial mode - no target, let winners run)")
            return []
        
        # Original profit target logic (disabled in trial mode)
        closed = []
        enable_trailing = OptionsTradingConfig.ENABLE_TRAILING_EXIT
        trailing_buffer = OptionsTradingConfig.TRAILING_BUFFER_PERCENTAGE
        decay_monitor = get_decay_monitor()
        
        for symbol, position in self._snapshot_positions_items():
            
            # Guard against zero entry premium
            if not position.entry_premium or position.entry_premium <= 0:
                logger.warning(f"PROFIT_CHECK: Skipping {symbol} - entry_premium is {position.entry_premium}")
                continue
            
            if position.unrealized_pnl > 0:
                # Calculate current and peak profit percentages
                current_profit_pct = (position.current_premium - position.entry_premium) / position.entry_premium * 100
                peak_profit_pct = (position.highest_premium - position.entry_premium) / position.entry_premium * 100
                
                # Get decay-aware analysis
                decay_signal = decay_monitor.get_smart_monitoring_signal(
                    symbol, position.current_premium, position.entry_premium
                )
                
                should_exit = False
                exit_reason = "PROFIT"
                
                if enable_trailing and peak_profit_pct >= profit_target:
                    # Trailing exit with decay-aware validation
                    # Only exit if price pulls back from peak AND it's not a temporary dip
                    if current_profit_pct <= (peak_profit_pct - trailing_buffer):
                        # Check if it's just profit booking or real reversion
                        is_booking, booking_reason = decay_signal.get('is_booking', (False, None)), decay_signal.get('booking_reason', '')
                        
                        if decay_signal['signal'] != 'HOLD':  # Not held back by profit booking pattern
                            should_exit = True
                            exit_reason = f"TRAILING_EXIT (peak={peak_profit_pct:.1f}%, current={current_profit_pct:.1f}%, buffer={trailing_buffer:.1f}%)"
                            logger.info(f"TRAILING_EXIT: {symbol} | Peak profit: {peak_profit_pct:.1f}% → Current: {current_profit_pct:.1f}% | Exiting at: ₹{position.current_premium:.2f}")
                        else:
                            # Temporary dip - stay in position
                            logger.debug(f"TRAILING_HELD: {symbol} | Dip detected as temporary | {decay_signal['reason']}")
                else:
                    # Standard exit: exit at initial profit target (for non-trailing mode)
                    if current_profit_pct >= profit_target:
                        should_exit = True
                        exit_reason = f"PROFIT_TARGET ({current_profit_pct:.1f}%)"
                
                if should_exit:
                    pnl = self.close_position(symbol, position.current_premium, exit_reason)
                    if pnl:
                        # Track max profit that was available
                        max_profit = position.highest_premium - position.entry_premium
                        pnl['max_profit'] = max_profit
                        pnl['peak_profit_percent'] = peak_profit_pct
                        closed.append(pnl)
                        
                        # Log for analysis
                        logger.info(f"PROFIT_EXIT: {symbol} | Entry: ₹{position.entry_premium:.2f} | "
                                   f"Peak: ₹{position.highest_premium:.2f} ({peak_profit_pct:.1f}%) | "
                                   f"Exit: ₹{position.current_premium:.2f} ({current_profit_pct:.1f}%) | "
                                   f"PnL: ₹{pnl['pnl']:.2f}")
        
        return closed
    
    def check_trailing_stop_losses(self) -> List[Dict[str, Any]]:
        """
        TRIAL SL Implementation:
        
        Phase 1: DEFAULT -20% SL (Entry to +9.99% gain)
        - SL stays at -20% from entry
        - Example: Entry ₹100, SL = ₹80
        
        Phase 2: TRIAL SL ACTIVATION (Gain >= 10%)
        - Switch to TRIAL SL: 5% below current peak
        - Example: Peak ₹110 (+10%), TRIAL SL = ₹104.5
        - Log: "TRIAL_SL_ACTIVATED"
        
        Phase 3: TRIAL SL TRAILING (As price increases)
        - Update TRIAL SL = 5% below new peak
        - Example: Peak ₹121 (+21%), TRIAL SL = ₹114.95
        - Trail it up continuously as peak increases
        - Log: "TRIAL_SL_UPDATED"
        
        Exit: When price hits TRIAL SL or hard SL
        """
        closed = []
        positions = self._snapshot_positions_items()
        logger.info(f"CHECK_TRIAL_SL: Starting checks for {len(positions)} positions")
        
        for symbol, position in positions:
            
            # Calculate gain % based on PEAK (not current) for TRIAL SL activation
            # This ensures we catch the 10% milestone even if price has pulled back
            peak_gain_percent = ((position.highest_premium - position.entry_premium) / position.entry_premium) * 100
            
            # Calculate current gain % for logging
            gain_percent = ((position.current_premium - position.entry_premium) / position.entry_premium) * 100
            
            # Determine which SL to use
            is_trial_sl_enabled = position.trial_sl_enabled
            
            # ============================================================
            # PHASE 1 & 2: Check if we should activate TRIAL SL
            # ============================================================
            # 🔧 ADAPTIVE THRESHOLD: Use market conditions to determine activation threshold
            market_detector = get_market_condition_detector()
            
            # 🔴 FIX: Fetch Nifty market data (LTP + Open) to determine market condition
            # This determines if we use 5% (weak market) or 10% (strong market) threshold
            nifty_ltp = None
            nifty_open = None
            try:
                # Try to get Nifty full market data from broker for market condition analysis
                nifty_market_data = self.broker.get_market_data("NIFTY", exchange="NSE")
                if nifty_market_data:
                    nifty_ltp = nifty_market_data.get('ltp')
                    nifty_open = nifty_market_data.get('open')
                    if nifty_ltp:
                        # Update market detector with current Nifty data
                        market_detector.update_market_data(
                            nifty_ltp=nifty_ltp,
                            nifty_open=nifty_open  # ✅ Now fetches actual open price
                        )
                        open_str = f"₹{nifty_open:.2f}" if nifty_open else "N/A"
                        logger.debug(f"TRIAL_SL_MARKET_DATA: Nifty LTP=₹{nifty_ltp:.2f} | Open={open_str}")
            except Exception as e:
                logger.debug(f"Could not fetch Nifty market data for condition: {str(e)}")
                nifty_ltp = None
            
            trial_sl_threshold, market_reason = market_detector.get_trial_sl_threshold(
                nifty_ltp=nifty_ltp,  # ✅ Now passes actual Nifty LTP
                current_time=datetime.now()
            )
            activation_threshold, trailing_gap, trail_profile = self._get_trial_sl_profile(position, trial_sl_threshold)
            trial_sl_buffer_pct = max(0.0, OptionsTradingConfig.TRAILING_BUFFER_PERCENTAGE)
            buffered_activation_pct = activation_threshold + trial_sl_buffer_pct
            safe_trigger_ceiling = max(position.entry_premium, position.current_premium - 0.10)
            
            if (
                not is_trial_sl_enabled
                and peak_gain_percent >= buffered_activation_pct
                and gain_percent >= buffered_activation_pct
            ):
                # 🚀 ACTIVATE TRIAL SL only after the live premium can support the buffered lock.
                position.trial_sl_enabled = True
                position.trial_sl_activation_time = datetime.now().isoformat()
                # 🎯 Buffer the lock level so STOPLOSS_MARKET slippage still tends to realize
                # near the intended threshold on fast pullbacks.
                desired_trial_sl = position.entry_premium * (1 + buffered_activation_pct / 100)
                position.trial_sl_price = min(desired_trial_sl, safe_trigger_ceiling)
                # 🔴 IMPORTANT: Also update legacy trailing_sl fields for backward compatibility
                position.trailing_sl_activated = True
                position.last_trailing_sl_price = position.trial_sl_price
                position.trailing_sl_activation_time = datetime.now().isoformat()
                is_trial_sl_enabled = True
                
                log_event(
                    "TRIAL_SL_ACTIVATED",
                    f"✅ TRIAL SL ACTIVATED for {symbol} ({buffered_activation_pct:.1f}% buffered gain reached)",
                    symbol=symbol,
                    peak_gain_percent=round(peak_gain_percent, 2),
                    current_gain_percent=round(gain_percent, 2),
                    entry_premium=position.entry_premium,
                    peak_premium=position.highest_premium,
                    trial_sl=round(position.trial_sl_price, 2),
                    current_premium=position.current_premium,
                    threshold_used=activation_threshold,
                    market_threshold=trial_sl_threshold,
                    buffer_used=trial_sl_buffer_pct,
                    trail_profile=trail_profile,
                    trailing_gap=trailing_gap,
                    buffered_activation_pct=round(buffered_activation_pct, 2),
                    market_condition=market_reason,
                    reason=f"{trail_profile} profile | activation {activation_threshold:.1f}% + {trial_sl_buffer_pct:.1f}% buffer: {market_reason}",
                )
                try:
                    live_tracker = get_live_data_tracker()
                    live_tracker.record_candle_context(
                        event_type='trial_sl_activated',
                        symbol=symbol,
                        underlying=position.underlying,
                        option_premium=position.current_premium,
                        trial_sl_price=position.trial_sl_price,
                        trail_profile=trail_profile,
                        trail_activation_threshold=activation_threshold,
                        trailing_gap=trailing_gap,
                        market_trend=position.market_trend,
                        trend_strength=position.trend_strength,
                        gain_percent=gain_percent,
                    )
                except Exception as e:
                    logger.debug(f"CANDLE_TELEMETRY: TRIAL_SL_ACTIVATE_FAILED | {symbol} | {str(e)}")

                logger.info(
                    f"TRIAL_SL_ACTIVATED: {symbol} | Profile: {trail_profile} | Activation: {activation_threshold:.1f}% | Buffer: {trial_sl_buffer_pct:.1f}% | "
                    f"Buffered Activation: {buffered_activation_pct:.1f}% | Peak Gain: {peak_gain_percent:.2f}% | "
                    f"Current Gain: {gain_percent:.2f}% | Peak: ₹{position.highest_premium:.2f} | TRIAL SL: ₹{position.trial_sl_price:.2f} | "
                    f"Market: {market_reason}"
                )
                
                # 🔴 CRITICAL: Save positions to disk so state persists across bot restarts
                self._save_positions()
                
                # 🔴 LIVE: Push TRIAL_SL activation price to broker SL order
                # Moves broker SL from initial -10% up to the activation milestone
                self.modify_sl_order(symbol, position.trial_sl_price, position.sl_order_id)
            
            # ============================================================
            # PHASE 3: Update TRIAL SL as price moves up (TRAILING FROM PEAK)
            # ============================================================
            if is_trial_sl_enabled:
                # 🎯 TRAILING FROM PEAK: SL = max(activation_threshold, peak - TRAILING_GAP)
                #
                # WHY: Staircase (int(peak/5)*5) had a 5%-wide dead zone — any peak between
                # 5.0% and 9.9% locked at 5%, leaving 4.9% on the table.  Smaller steps (2%)
                # solve the capture problem but create a noise problem: SL sits only 0-1%
                # below current price, firing on normal options bid-ask wiggle.
                #
                # TRAILING GAP = 2%: tighter lock than 3%, while still leaving enough
                # room for normal options noise after activation.
                #   Peak  5% → SL = max(5%, 2%)  = 5%   (activation floor holds)
                #   Peak  8% → SL = max(5%, 5%)  = 5%   (no change, floor holds)
                #   Peak  9% → SL = max(5%, 6%)  = 6%   ← better than old 5%
                #   Peak 12% → SL = max(5%, 9%)  = 9%   ← better than old 10% (stay in longer)
                #   Peak 15% → SL = max(5%, 12%) = 12%  ← better than old 10%
                #   Peak 29% → SL = max(5%, 26%) = 26%  ← better than old 25%
                #
                # The 2% gap keeps more profit than 3% while still avoiding 0-1% noise exits.
                trailing_sl_pct = max(buffered_activation_pct, peak_gain_percent - trailing_gap)
                desired_trial_sl = position.entry_premium * (1 + trailing_sl_pct / 100)
                new_trial_sl = min(desired_trial_sl, safe_trigger_ceiling)

                # Only update if new SL is meaningfully higher than current (avoid micro-updates
                # that spam broker modify_sl_order calls on every monitoring tick)
                MIN_SL_MOVE_PCT = 0.5   # at least 0.5% premium move before pushing to broker
                min_move = position.entry_premium * MIN_SL_MOVE_PCT / 100
                current_sl = position.trial_sl_price or 0.0
                is_clamped_update = desired_trial_sl > safe_trigger_ceiling

                if is_clamped_update:
                    logger.debug(
                        f"TRIAL_SL_CLAMPED_UPDATE: {symbol} | desired=₹{desired_trial_sl:.2f} | "
                        f"safe_ceiling=₹{safe_trigger_ceiling:.2f} | current=₹{position.current_premium:.2f}"
                    )

                if new_trial_sl > current_sl + min_move:
                    old_trial_sl = position.trial_sl_price
                    position.trial_sl_price = new_trial_sl
                    # 🔴 IMPORTANT: Update legacy trailing_sl tracking for analysis
                    position.last_trailing_sl_price = new_trial_sl
                    position.trailing_sl_update_count += 1
                    position.trial_sl_update_count += 1

                    if old_trial_sl is not None:
                        sl_increase = round(new_trial_sl - old_trial_sl, 2)
                        log_event("TRIAL_SL_UPDATED",
                                 f"🔺 TRIAL SL Updated for {symbol} (peak-trail: peak={peak_gain_percent:.1f}% - {trailing_gap:.0f}% → locked at {trailing_sl_pct:.1f}%)",
                                 symbol=symbol,
                                 update_count=position.trial_sl_update_count,
                                 trailing_gap=trailing_gap,
                                 trail_profile=trail_profile,
                                 trial_sl_buffer_pct=trial_sl_buffer_pct,
                                 trailing_sl_pct=round(trailing_sl_pct, 2),
                                 old_trial_sl=round(old_trial_sl, 2),
                                 new_trial_sl=round(new_trial_sl, 2),
                                 sl_increase=sl_increase,
                                 peak_premium=position.highest_premium,
                                 peak_gain=round(peak_gain_percent, 2),
                                 current_premium=position.current_premium,
                                 gain_percent=round(gain_percent, 2),
                                 clamped_to_safe_ceiling=is_clamped_update)
                        try:
                            live_tracker = get_live_data_tracker()
                            live_tracker.record_candle_context(
                                event_type='trial_sl_updated',
                                symbol=symbol,
                                underlying=position.underlying,
                                option_premium=position.current_premium,
                                trial_sl_price=new_trial_sl,
                                trail_profile=trail_profile,
                                trail_activation_threshold=activation_threshold,
                                trailing_gap=trailing_gap,
                                market_trend=position.market_trend,
                                trend_strength=position.trend_strength,
                                gain_percent=gain_percent,
                            )
                        except Exception as e:
                            logger.debug(f"CANDLE_TELEMETRY: TRIAL_SL_UPDATE_FAILED | {symbol} | {str(e)}")

                        logger.debug(f"TRIAL_SL_UPDATED: {symbol} | Profile: {trail_profile} | Peak: {peak_gain_percent:.1f}% - {trailing_gap:.1f}% = "
                                   f"Lock: {trailing_sl_pct:.1f}% | TRIAL SL: ₹{old_trial_sl:.2f} → ₹{new_trial_sl:.2f} | Δ₹{sl_increase} | "
                                   f"Clamped={is_clamped_update}")

                        # 🔴 CRITICAL: Save positions to disk so updated SL persists across bot restarts
                        self._save_positions()

                        # 🔴 LIVE: Modify broker SL order to new trailing price
                        self.modify_sl_order(symbol, new_trial_sl, position.sl_order_id)
            
            # ============================================================
            # CHECK SL HIT: Determine effective SL and check if hit
            # ============================================================
            effective_sl = position.trial_sl_price if is_trial_sl_enabled else position.hard_sl_price
            
            if position.current_premium <= effective_sl:
                # 🎯 SL HIT - Close position at SL price (not slippage price)
                # When the breakeven profit-floor raised the hard SL to ~entry, label it as
                # BREAKEVEN_FLOOR (not HARD_SL) — it's a protected give-back, not a -10% loss.
                if is_trial_sl_enabled:
                    sl_type = "TRIAL_SL"
                elif getattr(position, 'breakeven_floor_active', False):
                    sl_type = "BREAKEVEN_FLOOR"
                else:
                    sl_type = "HARD_SL"

                logger.warning(f"SL_HIT: {symbol} | Type: {sl_type} | SL: ₹{effective_sl:.2f} | "
                             f"Current: ₹{position.current_premium:.2f} | Peak: ₹{position.highest_premium:.2f}")
                
                pnl = self._reconcile_broker_stop_exit(
                    symbol,
                    effective_sl,
                    f"{sl_type}_HIT (SL: ₹{effective_sl:.2f})"
                )
                
                if pnl:
                    closed.append(pnl)
                    
                    log_event("SL_EXIT_EXECUTED",
                             f"🛑 {sl_type} exit executed for {symbol}",
                             symbol=symbol,
                             sl_type=sl_type,
                             entry_premium=position.entry_premium,
                             exit_premium=round(pnl.get('exit_premium', effective_sl), 2),
                             peak_premium=position.highest_premium,
                             sl_price=round(effective_sl, 2),
                             gain_percent=round(gain_percent, 2),
                             pnl=round(pnl.get('pnl', 0), 2),
                             trial_sl_updates=position.trial_sl_update_count if is_trial_sl_enabled else 0)
                    
                    logger.info(f"SL_EXIT: {symbol} | Entry: ₹{position.entry_premium:.2f} | "
                               f"Exit: ₹{pnl.get('exit_premium', effective_sl):.2f} | Peak: ₹{position.highest_premium:.2f} | "
                               f"PnL: ₹{pnl.get('pnl', 0):.2f} ({gain_percent:.2f}%) | "
                               f"{sl_type} Updates: {position.trial_sl_update_count}")
            else:
                # Log current SL status
                logger.debug(f"TRIAL_SL_CHECK: {symbol} | Gain: {gain_percent:.2f}% | "
                           f"Peak: ₹{position.highest_premium:.2f} | "
                           f"SL: ₹{effective_sl:.2f} | Current: ₹{position.current_premium:.2f}")
        
        return closed
    
    def check_hard_stop_loss(self) -> List[Dict[str, Any]]:
        """
        🛑 ULTIMATE SAFETY NET: Hard stop loss at -10% from entry
        
        This is the CRITICAL failsafe that guarantees:
        - No position can lose more than 10% from entry price
        - Prevents catastrophic losses (like -36% SAMMAANCAP incident)
        - Runs FIRST in monitoring loop, before any other exits
        - Executes independently from TRIAL_SL check
        
        IMPORTANCE:
        - On 2026-01-19, SAMMAANCAP lost ₹-10,823 (-36.1%) because HARD_SL check was skipped
        - Adding this standalone function ensures hard SL check ALWAYS runs
        - Critical for preserving capital and limiting downside risk
        """
        closed = []
        
        positions = self._snapshot_positions_items()
        logger.debug(f"HARD_SL_CHECK: Starting | positions={len(positions)}")
        
        for symbol, position in positions:
            
            if not position.hard_sl_price:
                logger.warning(f"HARD_SL_CHECK: {symbol} | No hard SL price set, recalculating from config")
                position.hard_sl_price = self._round_to_10_paise(
                    position.entry_premium * (1 - OptionsTradingConfig.STOP_LOSS_PERCENTAGE / 100)
                )
            
            # Check if current premium has hit hard SL
            if position.current_premium <= position.hard_sl_price:
                # HARD SL BREACHED - EXIT IMMEDIATELY
                loss_percent = ((position.current_premium - position.entry_premium) / position.entry_premium) * 100
                
                logger.error(f"HARD_SL_BREACHED: {symbol} | Entry: ₹{position.entry_premium:.2f} | "
                           f"SL: ₹{position.hard_sl_price:.2f} | Current: ₹{position.current_premium:.2f} | "
                           f"Loss: {loss_percent:.1f}%")
                
                # Distinguish a true -10% hard stop from a breakeven-floor give-back.
                _sl_label = "BREAKEVEN_FLOOR_HIT" if getattr(position, 'breakeven_floor_active', False) else "HARD_SL_HIT"
                pnl = self._reconcile_broker_stop_exit(
                    symbol,
                    position.hard_sl_price,
                    f"{_sl_label} (SL: ₹{position.hard_sl_price:.2f})"
                )

                if pnl:
                    closed.append(pnl)
                    logger.error(f"HARD_SL_EXIT_EXECUTED: {symbol} | PnL: ₹{pnl.get('pnl', 0):.2f} | "
                               f"Entry: ₹{position.entry_premium:.2f} | Exit: ₹{position.hard_sl_price:.2f}")
            else:
                # Log hard SL status
                current_loss_percent = ((position.current_premium - position.entry_premium) / position.entry_premium) * 100
                sl_distance = position.current_premium - position.hard_sl_price
                logger.debug(f"HARD_SL_OK: {symbol} | Current: ₹{position.current_premium:.2f} | "
                           f"SL: ₹{position.hard_sl_price:.2f} | Distance: ₹{sl_distance:.2f} | Loss: {current_loss_percent:.1f}%")
        
        return closed

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _build_candle_metrics(candle: Dict[str, Any]) -> Dict[str, Any]:
        open_price = float(candle.get('open', 0.0) or 0.0)
        high_price = float(candle.get('high', 0.0) or 0.0)
        low_price = float(candle.get('low', 0.0) or 0.0)
        close_price = float(candle.get('close', 0.0) or 0.0)
        candle_range = max(high_price - low_price, 0.0)
        body = close_price - open_price
        upper_wick = max(high_price - max(open_price, close_price), 0.0)
        lower_wick = max(min(open_price, close_price) - low_price, 0.0)

        return {
            'body': body,
            'range': candle_range,
            'direction': 'up' if body > 0 else 'down' if body < 0 else 'flat',
            'body_pct_of_range': (abs(body) / candle_range * 100.0) if candle_range > 0 else 0.0,
            'upper_wick_pct_of_range': (upper_wick / candle_range * 100.0) if candle_range > 0 else 0.0,
            'lower_wick_pct_of_range': (lower_wick / candle_range * 100.0) if candle_range > 0 else 0.0,
        }

    def _get_recent_underlying_candles(self, underlying: str, *, interval: str, count: int) -> List[Dict[str, Any]]:
        if not underlying or not hasattr(self.broker, 'get_historical_data'):
            return []

        try:
            candles = self.broker.get_historical_data(
                underlying,
                interval=interval,
                days_back=1,
            )
        except Exception as e:
            logger.debug(f"CANDLE_MACD_FADE: CANDLE_FETCH_FAILED | {underlying} | interval={interval} | {str(e)}")
            return []

        if not candles:
            return []

        normalized: List[Dict[str, Any]] = []
        for candle in candles[-count:]:
            normalized.append(dict(candle) if isinstance(candle, dict) else {
                'open': getattr(candle, 'open', 0.0),
                'high': getattr(candle, 'high', 0.0),
                'low': getattr(candle, 'low', 0.0),
                'close': getattr(candle, 'close', 0.0),
                'timestamp': getattr(candle, 'timestamp', None),
            })
        return normalized

    def _calculate_realtime_premium_macd(self, prices: List[float]) -> Optional[Dict[str, float]]:
        from optcode.optconfig import SentimentConfig

        fast_period = max(2, int(SentimentConfig.REALTIME_PREMIUM_FADE_MACD_FAST))
        slow_period = max(fast_period + 1, int(SentimentConfig.REALTIME_PREMIUM_FADE_MACD_SLOW))
        signal_period = max(2, int(SentimentConfig.REALTIME_PREMIUM_FADE_MACD_SIGNAL))
        min_required = slow_period + signal_period
        if len(prices) < min_required:
            return None

        def _ema_series(values: List[float], period: int) -> List[float]:
            multiplier = 2.0 / (period + 1)
            ema_values = [values[0]]
            for value in values[1:]:
                ema_values.append((value - ema_values[-1]) * multiplier + ema_values[-1])
            return ema_values

        fast_ema = _ema_series(prices, fast_period)
        slow_ema = _ema_series(prices, slow_period)
        macd_line = [fast - slow for fast, slow in zip(fast_ema, slow_ema)]
        signal_line = _ema_series(macd_line, signal_period)
        histogram = [macd - signal for macd, signal in zip(macd_line, signal_line)]

        return {
            'macd': macd_line[-1],
            'signal': signal_line[-1],
            'histogram': histogram[-1],
            'prev_histogram': histogram[-2] if len(histogram) >= 2 else histogram[-1],
            'prev2_histogram': histogram[-3] if len(histogram) >= 3 else histogram[-2] if len(histogram) >= 2 else histogram[-1],
        }

    def _get_realtime_premium_fade_snapshot(self, symbol: str, position: OptionPosition) -> Optional[Dict[str, Any]]:
        from optcode.optconfig import SentimentConfig

        decay_monitor = get_decay_monitor()
        raw_history = list(getattr(decay_monitor, 'price_history', {}).get(symbol, []))
        min_points = max(5, int(SentimentConfig.REALTIME_PREMIUM_FADE_MIN_HISTORY_POINTS))
        if len(raw_history) < min_points:
            return None

        recent_points = raw_history[-min_points:]
        recent_prices = []
        for point in recent_points:
            try:
                recent_prices.append(float(point.get('price', 0.0)))
            except (AttributeError, TypeError, ValueError):
                continue
        if len(recent_prices) < min_points:
            return None

        full_prices = []
        for point in raw_history:
            try:
                full_prices.append(float(point.get('price', 0.0)))
            except (AttributeError, TypeError, ValueError):
                continue

        latest = recent_prices[-1]
        prev = recent_prices[-2]
        earlier = recent_prices[-3]
        recent_peak = max(recent_prices)
        recent_low = min(recent_prices)
        rebound_pct = ((latest - recent_low) / recent_low * 100.0) if recent_low > 0 else 0.0
        peak_reversion_pct = ((recent_peak - latest) / recent_peak * 100.0) if recent_peak > 0 else 0.0
        macd_snapshot = self._calculate_realtime_premium_macd(full_prices)
        decay_analysis = decay_monitor.get_decay_adjusted_change(symbol, position.current_premium, position.entry_premium)
        is_decay_dip = decay_monitor.is_decay_induced_dip(symbol, position.current_premium, position.entry_premium)
        is_booking, booking_reason = decay_monitor.detect_profit_booking_dip(symbol)

        drawdown_pct = 0.0
        if position.highest_premium > 0:
            drawdown_pct = ((position.highest_premium - position.current_premium) / position.highest_premium) * 100.0

        current_pnl_pct = 0.0
        if position.entry_premium > 0 and position.quantity > 0:
            current_pnl_pct = (position.unrealized_pnl / (position.entry_premium * position.quantity)) * 100.0

        entry_context = position.entry_context or {}
        return {
            'latest': latest,
            'prev': prev,
            'earlier': earlier,
            'recent_peak': recent_peak,
            'recent_low': recent_low,
            'peak_reversion_pct': peak_reversion_pct,
            'rebound_pct': rebound_pct,
            'drawdown_pct': drawdown_pct,
            'current_pnl_pct': current_pnl_pct,
            'macd': macd_snapshot,
            'is_decay_dip': is_decay_dip,
            'is_booking': is_booking,
            'booking_reason': booking_reason,
            'decay_analysis': decay_analysis,
            'entry_adx': self._coerce_float(entry_context.get('adx')),
            'entry_volume_ratio': self._coerce_float(entry_context.get('volume_ratio')),
        }

    def _score_realtime_premium_fade(self, snapshot: Dict[str, Any]) -> Tuple[int, int, List[str]]:
        from optcode.optconfig import SentimentConfig

        required_score = int(SentimentConfig.REALTIME_PREMIUM_FADE_BASE_SCORE)
        if snapshot.get('entry_adx', 0.0) >= SentimentConfig.REALTIME_PREMIUM_FADE_STRONG_ADX:
            required_score += 1
        if snapshot.get('entry_volume_ratio', 0.0) >= SentimentConfig.REALTIME_PREMIUM_FADE_HIGH_VOLUME_RATIO:
            required_score += 1
        if snapshot.get('is_booking'):
            required_score += 1
        if snapshot.get('is_decay_dip'):
            required_score += 2

        score = 0
        reasons: List[str] = []
        latest = snapshot['latest']
        prev = snapshot['prev']
        earlier = snapshot['earlier']
        macd_snapshot = snapshot.get('macd') or {}
        decay_analysis = snapshot.get('decay_analysis') or {}

        if latest < prev < earlier:
            score += 1
            reasons.append('three_tick_fade')
        if snapshot.get('peak_reversion_pct', 0.0) >= SentimentConfig.REALTIME_PREMIUM_FADE_PEAK_REVERSION_PCT:
            score += 1
            reasons.append('peak_reversion')
        if snapshot.get('rebound_pct', 0.0) <= SentimentConfig.REALTIME_PREMIUM_FADE_REBOUND_FAILURE_PCT:
            score += 1
            reasons.append('weak_rebound')
        if decay_analysis.get('decay_adjusted_change', 0.0) < -(abs(decay_analysis.get('expected_decay', 0.0)) * SentimentConfig.REALTIME_PREMIUM_FADE_DECAY_BUFFER_MULTIPLIER):
            score += 1
            reasons.append('decay_adjusted_selloff')
        if decay_analysis.get('is_real_move'):
            score += 1
            reasons.append('real_move_confirmed')

        histogram = macd_snapshot.get('histogram')
        prev_histogram = macd_snapshot.get('prev_histogram')
        prev2_histogram = macd_snapshot.get('prev2_histogram')
        if histogram is not None and histogram <= 0:
            score += 2
            reasons.append('micro_macd_negative')
        elif macd_snapshot.get('macd') is not None and macd_snapshot.get('signal') is not None and macd_snapshot['macd'] <= macd_snapshot['signal']:
            score += 1
            reasons.append('micro_macd_below_signal')
        if histogram is not None and prev_histogram is not None and histogram < prev_histogram:
            score += 1
            reasons.append('micro_macd_decay')
        if histogram is not None and prev_histogram is not None and prev2_histogram is not None and histogram < prev_histogram < prev2_histogram:
            score += 1
            reasons.append('micro_macd_rollover')

        return score, required_score, reasons

    def _has_strong_realtime_indicator_reversal(self, snapshot: Dict[str, Any], reasons: List[str], score: int, required_score: int) -> bool:
        macd_snapshot = snapshot.get('macd') or {}
        histogram = macd_snapshot.get('histogram')
        prev_histogram = macd_snapshot.get('prev_histogram')
        prev2_histogram = macd_snapshot.get('prev2_histogram')
        macd_value = macd_snapshot.get('macd')
        signal_value = macd_snapshot.get('signal')
        decay_analysis = snapshot.get('decay_analysis') or {}

        has_macd_reversal = (
            (histogram is not None and histogram <= 0)
            or (
                macd_value is not None
                and signal_value is not None
                and macd_value <= signal_value
            )
        )
        has_macd_rollover = (
            histogram is not None
            and prev_histogram is not None
            and prev2_histogram is not None
            and histogram < prev_histogram < prev2_histogram
        )
        has_price_fade = snapshot.get('latest') < snapshot.get('prev') < snapshot.get('earlier')
        has_real_move = decay_analysis.get('is_real_move') and decay_analysis.get('decay_adjusted_change', 0.0) < 0.0

        return (
            score >= required_score
            and has_macd_reversal
            and has_macd_rollover
            and (has_price_fade or has_real_move)
        )

    def check_realtime_premium_fade_exit(self) -> List[Dict[str, Any]]:
        from optcode.optconfig import SentimentConfig

        if not SentimentConfig.ENABLE_REALTIME_PREMIUM_FADE_EXIT:
            return []

        closed: List[Dict[str, Any]] = []
        now = datetime.now()
        for symbol, position in self._snapshot_positions_items():
            if position.trial_sl_enabled:
                continue

            time_in_position = (now - position.entry_time).total_seconds()
            if time_in_position < SentimentConfig.REALTIME_PREMIUM_FADE_MIN_SECONDS:
                continue

            last_check = self._realtime_fade_last_check.get(symbol)
            if last_check and (now - last_check).total_seconds() < SentimentConfig.REALTIME_PREMIUM_FADE_CHECK_INTERVAL_SECONDS:
                continue
            self._realtime_fade_last_check[symbol] = now

            snapshot = self._get_realtime_premium_fade_snapshot(symbol, position)
            if not snapshot:
                continue

            score, required_score, reasons = self._score_realtime_premium_fade(snapshot)
            strong_indicator_reversal = self._has_strong_realtime_indicator_reversal(snapshot, reasons, score, required_score)
            logger.debug(
                f"REALTIME_PREMIUM_FADE_SCORE: {symbol} | score={score}/{required_score} | "
                f"drawdown={snapshot['drawdown_pct']:.1f}% | pnl={snapshot['current_pnl_pct']:.1f}% | "
                f"peak_reversion={snapshot['peak_reversion_pct']:.2f}% | early_reversal={strong_indicator_reversal} | "
                f"reasons={','.join(reasons) if reasons else 'none'}"
            )
            if snapshot['drawdown_pct'] < SentimentConfig.REALTIME_PREMIUM_FADE_MIN_DRAWDOWN and not strong_indicator_reversal:
                continue
            if snapshot['current_pnl_pct'] > SentimentConfig.REALTIME_PREMIUM_FADE_MAX_PROFIT_PCT and not strong_indicator_reversal:
                continue
            if score < required_score:
                continue

            exit_reason = f"REALTIME_PREMIUM_FADE ({score}/{required_score} | {','.join(reasons[:4])})"
            pnl = self.close_position(symbol, position.current_premium, exit_reason)
            if pnl:
                closed.append(pnl)

        return closed

    def _get_candle_macd_fade_snapshot(self, position: OptionPosition) -> Optional[Dict[str, Any]]:
        minute_candles = self._get_recent_underlying_candles(position.underlying, interval='ONE_MINUTE', count=4)
        if len(minute_candles) < 3:
            return None

        latest_candle = minute_candles[-1]
        prev_candle = minute_candles[-2]
        earlier_candle = minute_candles[-3]
        latest_close = self._coerce_float(latest_candle.get('close'))
        prev_close = self._coerce_float(prev_candle.get('close'))
        earlier_close = self._coerce_float(earlier_candle.get('close'))

        tech_analyzer = TechnicalAnalyzer(position.underlying, self.broker)
        macd_data = tech_analyzer.get_macd(5) or {}
        latest_metrics = self._build_candle_metrics(latest_candle)
        drawdown_pct = 0.0
        if position.highest_premium > 0:
            drawdown_pct = ((position.highest_premium - position.current_premium) / position.highest_premium) * 100.0

        current_pnl_pct = 0.0
        if position.entry_premium > 0 and position.quantity > 0:
            current_pnl_pct = (position.unrealized_pnl / (position.entry_premium * position.quantity)) * 100.0

        entry_context = position.entry_context or {}
        return {
            'latest_metrics': latest_metrics,
            'two_step_adverse': latest_close < prev_close < earlier_close,
            'two_step_supportive': latest_close > prev_close > earlier_close,
            'macd': self._coerce_float(macd_data.get('macd')) if macd_data else None,
            'signal': self._coerce_float(macd_data.get('signal')) if macd_data else None,
            'histogram': self._coerce_float(macd_data.get('histogram')) if macd_data else None,
            'entry_macd_hist': self._coerce_float(entry_context.get('macd_hist')),
            'entry_rsi': self._coerce_float(entry_context.get('rsi_value')),
            'entry_adx': self._coerce_float(entry_context.get('adx')),
            'entry_volume_ratio': self._coerce_float(entry_context.get('volume_ratio')),
            'entry_ema_spread': abs(self._coerce_float(entry_context.get('ema_spread'))),
            'entry_atr_pc': self._coerce_float(entry_context.get('atr_pc')),
            'drawdown_pct': drawdown_pct,
            'current_pnl_pct': current_pnl_pct,
        }

    def _score_candle_macd_fade(self, position: OptionPosition, snapshot: Dict[str, Any]) -> Tuple[int, int, List[str]]:
        from optcode.optconfig import SentimentConfig

        latest_metrics = snapshot['latest_metrics']
        direction = latest_metrics.get('direction')
        body_pct = latest_metrics.get('body_pct_of_range') or 0.0
        upper_wick_pct = latest_metrics.get('upper_wick_pct_of_range') or 0.0
        lower_wick_pct = latest_metrics.get('lower_wick_pct_of_range') or 0.0
        histogram = snapshot.get('histogram')
        macd_value = snapshot.get('macd')
        signal_value = snapshot.get('signal')
        entry_hist = snapshot.get('entry_macd_hist') or 0.0

        required_score = int(SentimentConfig.CANDLE_MACD_FADE_BASE_SCORE)
        if snapshot.get('entry_adx', 0.0) >= SentimentConfig.CANDLE_MACD_FADE_STRONG_ADX:
            required_score += 1
        if snapshot.get('entry_volume_ratio', 0.0) >= SentimentConfig.CANDLE_MACD_FADE_HIGH_VOLUME_RATIO:
            required_score += 1
        if snapshot.get('entry_ema_spread', 0.0) >= SentimentConfig.CANDLE_MACD_FADE_STRONG_EMA_SPREAD:
            required_score += 1
        if snapshot.get('entry_atr_pc', 0.0) >= SentimentConfig.CANDLE_MACD_FADE_HIGH_ATR_PC:
            required_score += 1

        score = 0
        reasons: List[str] = []
        is_call = position.contract_type.upper() == 'CE'

        if is_call:
            if direction == 'down' and body_pct >= 55.0:
                score += 1
                reasons.append('strong_bear_candle')
            if upper_wick_pct >= 35.0:
                score += 1
                reasons.append('upper_wick_rejection')
            if snapshot.get('two_step_adverse'):
                score += 1
                reasons.append('two_step_lower_close')
            if histogram is not None and entry_hist > 0 and histogram <= entry_hist * SentimentConfig.CANDLE_MACD_FADE_MACD_DECAY_RATIO:
                score += 1
                reasons.append('macd_hist_decay')
            if histogram is not None and histogram <= 0:
                score += 2
                reasons.append('macd_hist_negative')
            elif macd_value is not None and signal_value is not None and macd_value <= signal_value:
                score += 1
                reasons.append('macd_below_signal')
            if snapshot.get('entry_rsi', 0.0) >= SentimentConfig.CANDLE_MACD_FADE_RSI_EXHAUSTION_CE:
                required_score = max(2, required_score - 1)
        else:
            if direction == 'up' and body_pct >= 55.0:
                score += 1
                reasons.append('strong_bull_candle')
            if lower_wick_pct >= 35.0:
                score += 1
                reasons.append('lower_wick_rejection')
            if snapshot.get('two_step_supportive'):
                score += 1
                reasons.append('two_step_higher_close')
            if histogram is not None and entry_hist < 0 and abs(histogram) <= abs(entry_hist) * SentimentConfig.CANDLE_MACD_FADE_MACD_DECAY_RATIO:
                score += 1
                reasons.append('macd_hist_decay')
            if histogram is not None and histogram >= 0:
                score += 2
                reasons.append('macd_hist_positive')
            elif macd_value is not None and signal_value is not None and macd_value >= signal_value:
                score += 1
                reasons.append('macd_above_signal')
            if snapshot.get('entry_rsi', 100.0) <= SentimentConfig.CANDLE_MACD_FADE_RSI_EXHAUSTION_PE:
                required_score = max(2, required_score - 1)

        return score, required_score, reasons

    def check_candle_macd_fade_exit(self) -> List[Dict[str, Any]]:
        from optcode.optconfig import SentimentConfig

        closed: List[Dict[str, Any]] = []
        positions = self._snapshot_positions_items()
        logger.info(f"CANDLE_MACD_FADE_CHECK: Starting | enabled={SentimentConfig.ENABLE_CANDLE_MACD_FADE_EXIT} | positions={len(positions)}")

        if not SentimentConfig.ENABLE_CANDLE_MACD_FADE_EXIT:
            return closed

        for symbol, position in positions:
            if position.trial_sl_enabled:
                continue

            time_in_position = (datetime.now() - position.entry_time).total_seconds()
            if time_in_position < SentimentConfig.CANDLE_MACD_FADE_MIN_SECONDS:
                continue

            snapshot = self._get_candle_macd_fade_snapshot(position)
            if not snapshot:
                continue

            if snapshot['drawdown_pct'] < SentimentConfig.CANDLE_MACD_FADE_MIN_DRAWDOWN:
                continue
            if snapshot['current_pnl_pct'] > SentimentConfig.CANDLE_MACD_FADE_MAX_PROFIT_PCT:
                continue

            score, required_score, reasons = self._score_candle_macd_fade(position, snapshot)
            logger.debug(
                f"CANDLE_MACD_FADE_SCORE: {symbol} | score={score}/{required_score} | "
                f"drawdown={snapshot['drawdown_pct']:.1f}% | pnl={snapshot['current_pnl_pct']:.1f}% | "
                f"hist={snapshot.get('histogram')} | reasons={','.join(reasons) if reasons else 'none'}"
            )

            if score < required_score:
                continue

            reason_summary = ','.join(reasons[:3]) if reasons else 'confirmed_fade'
            pnl = self.close_position(symbol, position.current_premium, f"CANDLE_MACD_FADE ({score}/{required_score} | {reason_summary})")
            if pnl:
                closed.append(pnl)
                logger.warning(
                    f"CANDLE_MACD_FADE_EXIT: {symbol} | score={score}/{required_score} | "
                    f"drawdown={snapshot['drawdown_pct']:.1f}% | pnl={snapshot['current_pnl_pct']:.1f}% | "
                    f"hist={snapshot.get('histogram')} | reasons={reason_summary}"
                )

        return closed
    
    def check_momentum_reversal(self) -> List[Dict[str, Any]]:
        """
        ⭐ TIER 1 EARLY EXIT: Detect momentum reversal and exit early
        
        SMART CONDITIONAL LOGIC:
        - Only exit on momentum IF position is ALREADY LOSING (unrealized P&L < -1%)
        - On good market days (positions in profit), let TRIAL_SL handle exits
        - On bad market days (positions losing), catch reversals early to prevent -20% SL
        
        Logic:
        - Track highest premium since entry (peak)
        - If price drops >10% from peak AND position already losing → exit immediately
        - This saves LOSING positions from catastrophic drawdown
        - But allows WINNING positions to reach TRIAL_SL targets
        
        Impact on Good Days:
        - Winning positions: NOT exited (let them hit TRIAL_SL) ✅
        - Losing positions: Cut early to prevent -20% SL ✅
        
        Impact on Bad Days:
        - All positions already red: MOMENTUM catches free-fall ✅
        - Prevents cascading losses ✅
        """
        from optcode.optconfig import SentimentConfig
        
        closed = []
        threshold = SentimentConfig.EARLY_EXIT_MOMENTUM_THRESHOLD / 100.0  # Convert % to decimal
        momentum_loss_trigger = -0.01  # Only exit momentum if already losing 1%+
        
        positions = self._snapshot_positions_items()
        logger.info(f"MOMENTUM_REVERSAL_CHECK: Starting | enabled={SentimentConfig.ENABLE_EARLY_EXIT_MOMENTUM} | threshold={threshold*100:.1f}% | loss_trigger={momentum_loss_trigger*100:.1f}% | positions={len(positions)}")
        
        if not SentimentConfig.ENABLE_EARLY_EXIT_MOMENTUM:
            logger.warning("MOMENTUM_REVERSAL_CHECK: Feature DISABLED in config")
            return closed  # Feature disabled
        
        for symbol, position in positions:
            
            # 🔧 SKIP MOMENTUM CHECK IF TRIAL_SL IS ACTIVE
            # Once TRIAL_SL is protecting the profit, momentum checks are unnecessary
            if position.trial_sl_enabled:
                logger.debug(f"MOMENTUM_SKIP: {symbol} | TRIAL_SL active - profit protected, momentum check skipped")
                continue
            
            # Calculate unrealized P&L percentage
            unrealized_pnl_pct = position.unrealized_pnl / (position.entry_premium * position.quantity) if position.entry_premium > 0 else 0
            
            # Calculate drawdown from peak
            if position.highest_premium > 0:
                drawdown = (position.highest_premium - position.current_premium) / position.highest_premium
                
                time_in_position = (datetime.now() - position.entry_time).total_seconds()
                
                # 🔍 DEBUG: Always log momentum check for every position
                logger.debug(f"MOMENTUM_CHECK: {symbol} | Peak: ₹{position.highest_premium:.2f} | Current: ₹{position.current_premium:.2f} | Drawdown: {drawdown*100:.1f}% | UnrealPnL: {unrealized_pnl_pct*100:.2f}% | Threshold: {threshold*100:.1f}% | Time: {time_in_position:.1f}s")
                
                if time_in_position > 10:  # Give position at least 10 seconds to breathe
                    # ⚡ SMART FILTER: Only exit momentum if ALREADY LOSING
                    if drawdown > threshold and unrealized_pnl_pct < momentum_loss_trigger:
                        # Momentum reversal detected AND position already losing → exit early
                        logger.warning(f"MOMENTUM_REVERSAL_TRIGGERED: {symbol} | Drawdown {drawdown*100:.1f}% > threshold {threshold*100:.1f}% + Position Already Losing {unrealized_pnl_pct*100:.2f}%")
                        pnl = self.close_position(
                            symbol, 
                            position.current_premium, 
                            f"MOMENTUM ({drawdown*100:.1f}% from peak)"
                        )
                        if pnl:
                            closed.append(pnl)
                            logger.warning(
                                f"EARLY_EXIT_MOMENTUM: {symbol} | Peak: ₹{position.highest_premium:.2f} | "
                                f"Current: ₹{position.current_premium:.2f} | "
                                f"Drawdown: {drawdown*100:.1f}% | UnrealPnL: {unrealized_pnl_pct*100:.2f}% | "
                                f"Loss: ₹{pnl['pnl']:.2f} | "
                                f"SAVED from potential -20% SL"
                            )
                    else:
                        if unrealized_pnl_pct >= momentum_loss_trigger:
                            logger.debug(f"MOMENTUM_SKIP_WINNING: {symbol} | Position in profit {unrealized_pnl_pct*100:.2f}% - let TRIAL_SL handle")
                        else:
                            logger.debug(f"MOMENTUM_OK: {symbol} | Drawdown {drawdown*100:.1f}% <= threshold {threshold*100:.1f}%")
                else:
                    logger.debug(f"MOMENTUM_SKIP: {symbol} | Too early, only {time_in_position:.1f}s since entry")
        
        return closed
    
    def check_stale_consolidation_exits(self) -> List[Dict[str, Any]]:
        """
        ⭐ NEW EXIT: Exit STALE CONSOLIDATIONS < 10% gain (before momentum reversal)
        
        SIMPLIFIED LOGIC:
        - If peak < 10%: TRIAL_SL not activated (trial_sl_enabled = FALSE)
        - Position is stale for 15+ mins WITHOUT hitting 10% peak = trend died
        - Exit early to lock whatever gain exists (6%, 7%, 8%)
        - Don't let it reverse and hit MOMENTUM_REVERSAL at -5%
        
        PROTECTION LAYERS:
        1. TRIAL_SL: Handles peaks >= threshold% (adaptive: 5% or 10%) - exits at 5% below peak
        2. STALE_CONSOLIDATION: Handles peaks < threshold% (exits after 15min stale)
        3. MOMENTUM_REVERSAL: Catch-all (exits at 10% drawdown from ANY peak)
        4. HARD_SL: Ultimate safety (-20% emergency exit)
        
        BENEFIT: Lock +6% instead of taking -5% loss (11% swing prevented)
        Data: 85 positions peaked +6.9% but lost -5.45% with old MOMENTUM logic
        
        Returns:
            List of closed position stats
        """
        closed = []
        
        # Get dynamic threshold based on market conditions
        market_detector = get_market_condition_detector()
        
        # 🔴 FIX: Fetch Nifty market data (LTP + Open) to determine market condition
        # This determines if we use 5% (weak market) or 10% (strong market) threshold
        nifty_ltp = None
        nifty_open = None
        try:
            # Try to get Nifty full market data from broker for market condition analysis
            nifty_market_data = self.broker.get_market_data("NIFTY", exchange="NSE")
            if nifty_market_data:
                nifty_ltp = nifty_market_data.get('ltp')
                nifty_open = nifty_market_data.get('open')
                if nifty_ltp:
                    market_detector.update_market_data(
                        nifty_ltp=nifty_ltp,
                        nifty_open=nifty_open  # ✅ Now fetches actual open price
                    )
        except Exception as e:
            logger.debug(f"Could not fetch Nifty market data for stale consolidation check: {str(e)}")
            nifty_ltp = None
        
        trial_sl_threshold, market_reason = market_detector.get_trial_sl_threshold(
            nifty_ltp=nifty_ltp,  # ✅ Now passes actual Nifty LTP
            current_time=datetime.now()
        )
        
        # Threshold from data analysis
        stale_hold_time_min = 10  # 10 minutes: if TRIAL_SL not activated by 10min, trade is dead
        
        positions = self._snapshot_positions_items()
        logger.info(f"STALE_CONSOLIDATION_CHECK: Starting | positions={len(positions)} | "
                   f"stale_threshold={stale_hold_time_min}min | "
                   f"trial_sl_threshold={trial_sl_threshold:.0f}% | market={market_reason} | "
                   f"exit_condition=trial_sl_enabled=FALSE (peak < {trial_sl_threshold:.0f}%)")
        
        for symbol, position in positions:
            
            # 🔧 SKIP STALE_CONSOLIDATION IF TRIAL_SL IS ACTIVE
            # Once TRIAL_SL is protecting the profit, we don't need stale logic
            if position.trial_sl_enabled:
                logger.debug(f"STALE_CONSOL_SKIP: {symbol} | TRIAL_SL active - profit protected, stale check skipped")
                continue
            
            # Calculate time held
            hold_time_sec = (datetime.now() - position.entry_time).total_seconds()
            hold_time_min = hold_time_sec / 60
            
            # Calculate current P&L %
            current_pnl_pct = (position.unrealized_pnl / (position.entry_premium * position.quantity)) if position.entry_premium > 0 else 0
            
            # Calculate peak profit %
            peak_profit_pct = (position.highest_premium - position.entry_premium) / position.entry_premium if position.entry_premium > 0 else 0
            
            logger.debug(f"STALE_CONSOL_CHECK: {symbol} | Hold: {hold_time_min:.1f}min | "
                        f"Trial_SL_Enabled: {position.trial_sl_enabled} | Peak: +{peak_profit_pct*100:.2f}% | "
                        f"Current: {current_pnl_pct*100:.2f}%")

            # 🛡️ PROFIT FLOOR: if this trade was meaningfully green, don't let a stale/dead exit
            # book it below breakeven — raise the hard SL to the floor and let that guard the downside.
            if self._apply_profit_floor(symbol, position):
                logger.info(
                    f"PROFIT_FLOOR_HOLD: {symbol} | peaked +{peak_profit_pct*100:.1f}% | "
                    f"current {current_pnl_pct*100:.1f}% below floor — breakeven SL protects, skipping stale/dead exit"
                )
                continue

            # Check stale consolidation pattern
            # Exit if:
            # 1. Been holding for 15+ minutes (stale period)
            # 2. TRIAL_SL not activated - trade should have exited by now
            # 3. Exit regardless of peak value (consolidation after peak is also stale)
            
            # 🔧 CRITICAL FIX: Remove the peak_vs_threshold check
            # Problem: If peak >= threshold but trial_sl_enabled=FALSE, trade sits idle
            # Reason: Logic assumed "peak >= threshold" means "trial_sl already activated"
            # Reality: Price can reach threshold, consolidate, and never activate trial_sl
            # Solution: Exit after 15 mins if trial_sl not activated (PERIOD)
            
            # RULE 1 — PER-MINUTE CHECK at marks 5,6,7,8,9:
            # Sample PnL ONCE per whole minute between 5-9 min. If current PnL < 3% at that snapshot,
            # exit. Avoids killing momentary dips (2.9% that recovers to 4% within seconds).
            # TRIAL_SL activation (option running strongly) overrides this entirely.
            minute_mark = int(hold_time_min)
            last_checked_min = getattr(position, '_slide_check_min', 0)
            if (5 <= minute_mark <= 9 and
                    minute_mark > last_checked_min and
                    not position.trial_sl_enabled and
                    current_pnl_pct < 0.03):
                position._slide_check_min = minute_mark
                logger.warning(
                    f"DEAD_TRADE_5MIN: {symbol} | Hold: {hold_time_min:.1f}min (min={minute_mark}) | "
                    f"Current: {current_pnl_pct*100:.2f}% (<3% at {minute_mark}min mark) | "
                    f"Peak: +{peak_profit_pct*100:.2f}% | Exiting"
                )
                log_event(
                    'DEAD_TRADE_5MIN',
                    f"Per-min slide exit at {minute_mark}min | {symbol} | current={current_pnl_pct*100:.2f}% peak={peak_profit_pct*100:.2f}%",
                    symbol=symbol,
                    hold_time_min=round(hold_time_min, 1),
                    minute_mark=minute_mark,
                    peak_profit_pct=round(peak_profit_pct * 100, 2),
                    current_pnl_pct=round(current_pnl_pct * 100, 2),
                    entry_premium=position.entry_premium,
                    highest_premium=position.highest_premium,
                    current_premium=getattr(position, 'current_premium', None),
                    entry_time=position.entry_time.isoformat() if position.entry_time else None,
                )
                pnl = self.close_position(
                    symbol,
                    position.current_premium,
                    f"STALE_CONSOLIDATION (Current {current_pnl_pct*100:.1f}%, Peak +{peak_profit_pct*100:.1f}%, {minute_mark}min check)"
                )
                if pnl is not None:
                    # Append the full close_position dict (pnl['pnl'] is the number, plus
                    # symbol/duration/pnl_percent) — matches what the monitor loop expects.
                    # Wrapping it as {'pnl': pnl} put a dict under 'pnl' and crashed f-string formatting.
                    closed.append(pnl)
                continue
            elif 5 <= minute_mark <= 9 and minute_mark > last_checked_min:
                position._slide_check_min = minute_mark  # mark checked even if PnL >= 3%

            if (hold_time_min >= stale_hold_time_min and  # Been stale for 15+ mins
                not position.trial_sl_enabled):  # Trial SL not activated (regardless of peak)
                
                # Exit if near break-even/small profit
                should_exit = current_pnl_pct >= -0.01
                
                # OR exit if we've started losing but not too deeply yet (catch before momentum hits)
                # AND we can still salvage some capital vs waiting for -10% momentum loss
                if not should_exit and -0.10 <= current_pnl_pct < -0.01:
                    # Position lost <10% and has been stale - exit now vs waiting for momentum
                    should_exit = True
                    logger.debug(f"STALE_CONSOLIDATION: {symbol} showing loss {current_pnl_pct*100:.2f}% but peak minimal - exiting early to avoid momentum")
                
                if should_exit:
                    logger.warning(f"STALE_CONSOLIDATION_TRIGGERED: {symbol} | Hold: {hold_time_min:.1f}min | "
                                 f"Trial_SL: {position.trial_sl_enabled} | Peak: +{peak_profit_pct*100:.2f}% | "
                                 f"Current: {current_pnl_pct*100:.2f}% | "
                                 f"Action: Exit stale position (10+ min held, TRIAL_SL inactive)")
                    
                    pnl = self.close_position(
                        symbol,
                        position.current_premium,
                        f"STALE_CONSOLIDATION (Peak +{peak_profit_pct*100:.1f}%, Stale {hold_time_min:.0f}min)"
                    )
                    if pnl:
                        closed.append(pnl)
                        logger.warning(
                            f"EARLY_EXIT_STALE_CONSOL: {symbol} | Hold: {hold_time_min:.1f}min | "
                            f"Entry: ₹{position.entry_premium:.2f} | Peak: ₹{position.highest_premium:.2f} (+{peak_profit_pct*100:.1f}%) | "
                            f"Exit: ₹{position.current_premium:.2f} ({current_pnl_pct*100:+.1f}%) | "
                            f"PnL: ₹{pnl['pnl']:.2f} | "
                            f"Exited after 10min stale (TRIAL_SL not activated)"
                        )
        
        return closed
    
    def check_stale_positions(self) -> List[Dict[str, Any]]:
        """
        ⭐ TIME-BASED EXIT: Detect and exit stale (non-trending) positions
        
        STALENESS PATTERN FROM DATA ANALYSIS:
        - Symbols either TREND or STALE in first 20 minutes
        - MOMENTUM exits hit at ~19.6 min median (2.2% win rate) → mostly losses
        - TRIAL_SL hits at ~28.3 min median (100% win rate) → all profitable
        - Critical insight: 67.4% of stale positions exhaust by 15 min
        
        STRATEGY: Detect staleness BEFORE hard momentum reversal (-10%)
        - Hold time > 20 minutes = potential staleness
        - No price movement last 5 min = confirmed stale
        - Already losing 2%+ = stale consolidation
        
        ACTION: Exit early with -2% loss instead of waiting for -10% momentum hit
        BENEFIT: Save ~₹1,260 per position, free capital for fresh symbols
        
        Returns:
            List of closed position stats (PnL, symbol, reason, etc)
        """
        closed = []
        
        # Configuration thresholds (from data analysis)
        hold_time_threshold = 20 * 60  # 20 minutes in seconds
        momentum_threshold = 0.005  # 0.5% price change in lookback window
        momentum_lookback = 5 * 60   # Look at last 5 minutes
        pnl_threshold = -0.02  # -2% (already losing consolidation)
        
        positions = self._snapshot_positions_items()
        logger.info(f"STALE_TIMEOUT_CHECK: Starting | positions={len(positions)} | "
                   f"hold_threshold={hold_time_threshold/60:.0f}min | "
                   f"momentum_threshold={momentum_threshold*100:.2f}%")
        
        for symbol, position in positions:
            
            # Calculate time held
            hold_time_sec = (datetime.now() - position.entry_time).total_seconds()
            hold_time_min = hold_time_sec / 60
            
            # Calculate unrealized P&L %
            unrealized_pnl_pct = (position.unrealized_pnl / (position.entry_premium * position.quantity)) if position.entry_premium > 0 else 0
            
            # Get recent price movement (total move from entry, not absolute)
            # For staleness: check if price is barely moving despite long hold time
            price_change_pct = (position.current_premium - position.entry_premium) / position.entry_premium if position.entry_premium > 0 else 0
            abs_price_change_pct = abs(price_change_pct)
            
            # DEBUG logging
            logger.debug(f"STALE_CHECK: {symbol} | Hold: {hold_time_min:.1f}min | "
                        f"Price Change: {price_change_pct*100:.2f}% | Abs Move: {abs_price_change_pct*100:.2f}% | "
                        f"P&L: {unrealized_pnl_pct*100:.2f}%")
            
            # 🛡️ PROFIT FLOOR: previously-green trades must not be dumped at a sub-breakeven loss
            # by the stale-timeout path either — the breakeven hard SL handles their downside.
            if self._apply_profit_floor(symbol, position):
                logger.info(
                    f"PROFIT_FLOOR_HOLD: {symbol} | stale-timeout skipped — breakeven SL protecting "
                    f"previously-green trade (current {unrealized_pnl_pct*100:.1f}%)"
                )
                continue

            # Check staleness criteria ONLY if position has aged beyond threshold
            if hold_time_min >= hold_time_threshold / 60:  # 20 minutes
                is_stale = False
                stale_reason = ""

                # Check if position shows no momentum (very small price movement despite long hold)
                # This catches positions that are stuck/consolidating
                if abs_price_change_pct < momentum_threshold and unrealized_pnl_pct <= 0:
                    is_stale = True
                    stale_reason = f"Stalled with small loss: move={abs_price_change_pct*100:.2f}% < {momentum_threshold*100:.2f}% + losing {unrealized_pnl_pct*100:.2f}%"
                
                # Check if position is already losing significantly (classic stale consolidation)
                elif unrealized_pnl_pct < pnl_threshold:
                    is_stale = True
                    stale_reason = f"Losing consolidation: {unrealized_pnl_pct*100:.2f}% < {pnl_threshold*100:.2f}%"
                
                # If stale, exit the position
                if is_stale:
                    logger.warning(f"STALE_TIMEOUT_TRIGGERED: {symbol} | {stale_reason}")
                    pnl = self.close_position(
                        symbol,
                        position.current_premium,
                        f"STALE_TIMEOUT ({stale_reason})"
                    )
                    if pnl:
                        closed.append(pnl)
                        logger.warning(
                            f"EARLY_EXIT_STALE: {symbol} | Hold: {hold_time_min:.1f}min | "
                            f"Premium: ₹{position.entry_premium:.2f} → ₹{position.current_premium:.2f} | "
                            f"P&L: ₹{pnl['pnl']:.2f} ({pnl.get('pnl_percent', 0):.2f}%) | "
                            f"Reason: {stale_reason}"
                        )
                else:
                    logger.debug(f"STALE_SKIP: {symbol} | Not stale yet (move: {price_change_pct*100:.2f}%, P&L: {unrealized_pnl_pct*100:.2f}%)")
            else:
                logger.debug(f"STALE_EARLY: {symbol} | Too young ({hold_time_min:.1f}min < {hold_time_threshold/60:.0f}min), skipping stale check")
        
        return closed
    
    def _log_iv_crash_event(self, symbol: str, entry_iv: float, current_iv: float, 
                            iv_drop_pct: float, position_data: Dict) -> None:
        """
        Log comprehensive IV_CRASH event data for analysis.
        Ensures all IV-related data is captured for later review.
        """
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'event': 'IV_CRASH_EXIT',
            'symbol': symbol,
            'entry_iv': entry_iv,
            'current_iv': current_iv,
            'iv_drop_pct': iv_drop_pct,
            'entry_premium': position_data.get('entry_premium', 0),
            'current_premium': position_data.get('current_premium', 0),
            'entry_delta': position_data.get('entry_delta', 0),
            'exit_delta': position_data.get('exit_delta', 0),
            'entry_gamma': position_data.get('entry_gamma', 0),
            'exit_gamma': position_data.get('exit_gamma', 0),
            'pnl': position_data.get('pnl', 0),
            'pnl_percent': position_data.get('pnl_percent', 0),
            'duration_seconds': position_data.get('duration', 0),
        }
        
        # Log to both application logs and metrics
        logger.info(f"IV_CRASH_EVENT_LOGGED: {symbol} | IV: {entry_iv:.2f}→{current_iv:.2f} ({iv_drop_pct:.1f}%) | PnL: ₹{log_entry['pnl']:.2f}")
        
        return log_entry
    
    def _log_iv_spike_event(self, symbol: str, entry_iv: float, current_iv: float, 
                            iv_rise_pct: float, position_data: Dict) -> None:
        """
        Log comprehensive IV_SPIKE event data for analysis.
        IV spike = panic signal during market crashes.
        Ensures all IV-related data is captured for later review.
        """
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'event': 'IV_SPIKE_EXIT',
            'symbol': symbol,
            'entry_iv': entry_iv,
            'current_iv': current_iv,
            'iv_rise_pct': iv_rise_pct,
            'entry_premium': position_data.get('entry_premium', 0),
            'current_premium': position_data.get('current_premium', 0),
            'entry_delta': position_data.get('entry_delta', 0),
            'exit_delta': position_data.get('exit_delta', 0),
            'entry_gamma': position_data.get('entry_gamma', 0),
            'exit_gamma': position_data.get('exit_gamma', 0),
            'pnl': position_data.get('pnl', 0),
            'pnl_percent': position_data.get('pnl_percent', 0),
            'duration_seconds': position_data.get('duration', 0),
        }
        
        # Log to both application logs and metrics
        logger.info(f"IV_SPIKE_EVENT_LOGGED: {symbol} | IV: {entry_iv:.2f}→{current_iv:.2f} (+{iv_rise_pct:.1f}%) | PnL: ₹{log_entry['pnl']:.2f}")
        
        return log_entry
    
    def check_iv_crash(self) -> List[Dict[str, Any]]:
        """
        ⭐ TIER 1 EARLY EXIT: Detect IV crash and exit immediately
        
        Logic:
        - Track IV at entry
        - If IV drops >10% from entry, exit immediately
        - IV crash = premium is dying = no recovery potential
        
        Why this works:
        - IV crash is the ROOT CAUSE of losses (premium collapses)
        - Catching it early prevents gamma explosion at exit
        - Earlier signal than MOMENTUM_REVERSAL (IV crashes first)
        
        Impact:
        - Save ₹20-30k on choppy/reversal days
        - Prevents "worst time to exit" scenario (high gamma + low IV)
        
        Expected outcome:
        - Complements MOMENTUM_REVERSAL (different signals)
        - May catch 40-50% of would-be MOMENTUM losses earlier
        """
        from optcode.optconfig import SentimentConfig
        
        closed = []
        threshold = SentimentConfig.EARLY_EXIT_IV_CRASH_THRESHOLD / 100.0  # Convert % to decimal
        
        positions = self._snapshot_positions_items()
        logger.info(f"IV_CRASH_CHECK: Starting | enabled={SentimentConfig.ENABLE_EARLY_EXIT_IV_CRASH} | threshold={threshold*100:.1f}% | positions={len(positions)}")
        
        if not SentimentConfig.ENABLE_EARLY_EXIT_IV_CRASH:
            logger.warning("IV_CRASH_CHECK: Feature DISABLED in config")
            return closed  # Feature disabled
        
        for symbol, position in positions:
            
            # Check IV collapse
            if position.entry_iv and position.current_iv:
                iv_drop_pct = (position.entry_iv - position.current_iv) / position.entry_iv
                
                # Only check positions that have been open for at least 10 seconds
                time_in_position = (datetime.now() - position.entry_time).total_seconds()
                
                # 🔍 DEBUG: Always log IV check for every position
                logger.debug(f"IV_CRASH_CHECK: {symbol} | Entry IV: {position.entry_iv:.2f} | Current IV: {position.current_iv:.2f} | Drop: {iv_drop_pct*100:.1f}% | Threshold: {threshold*100:.1f}% | Time: {time_in_position:.1f}s")
                
                if time_in_position > 10:  # Give position at least 10 seconds to breathe
                    if iv_drop_pct > threshold:
                        # IV crash detected - exit immediately
                        logger.warning(f"IV_CRASH_TRIGGERED: {symbol} | IV drop {iv_drop_pct*100:.1f}% > threshold {threshold*100:.1f}%")
                        pnl = self.close_position(
                            symbol,
                            position.current_premium,
                            f"IV_CRASH ({iv_drop_pct*100:.1f}% from entry)"
                        )
                        if pnl:
                            closed.append(pnl)
                            
                            # Log comprehensive IV_CRASH event data
                            self._log_iv_crash_event(
                                symbol, 
                                position.entry_iv, 
                                position.current_iv,
                                iv_drop_pct,
                                {
                                    'entry_premium': position.entry_premium,
                                    'current_premium': position.current_premium,
                                    'entry_delta': pnl.get('entry_delta', 0),
                                    'exit_delta': pnl.get('exit_delta', 0),
                                    'entry_gamma': pnl.get('entry_gamma', 0),
                                    'exit_gamma': pnl.get('exit_gamma', 0),
                                    'pnl': pnl['pnl'],
                                    'pnl_percent': pnl.get('pnl_percent', 0),
                                    'duration': pnl.get('duration', 0),
                                }
                            )
                            
                            logger.warning(
                                f"EARLY_EXIT_IV_CRASH: {symbol} | Entry IV: {position.entry_iv:.2f} | "
                                f"Current IV: {position.current_iv:.2f} | "
                                f"IV Drop: {iv_drop_pct*100:.1f}% (threshold: {threshold*100:.1f}%) | "
                                f"PnL: ₹{pnl['pnl']:.2f} | "
                                f"Premium: ₹{position.entry_premium:.2f} → ₹{position.current_premium:.2f}"
                            )
                    else:
                        logger.debug(f"IV_CRASH_OK: {symbol} | IV drop {iv_drop_pct*100:.1f}% <= threshold {threshold*100:.1f}%")
                else:
                    logger.debug(f"IV_CRASH_SKIP: {symbol} | Too early, only {time_in_position:.1f}s since entry")
        
        return closed
    
    def check_iv_spike(self) -> List[Dict[str, Any]]:
        """
        Close positions if IV spikes significantly (panic/crash signal).
        IV spike = opposite of IV crash. On crash days, IV rises BEFORE price drops.
        This catches market crashes 1-2 minutes earlier than MOMENTUM_REVERSAL.
        
        Returns:
            List of closed position stats (PnL, symbol, reason, etc)
        """
        closed = []
        threshold = SentimentConfig.EARLY_EXIT_IV_SPIKE_THRESHOLD / 100.0  # Convert % to decimal
        min_time = SentimentConfig.EARLY_EXIT_IV_SPIKE_MIN_TIME
        
        positions = self._snapshot_positions_items()
        logger.info(f"IV_SPIKE_CHECK: Starting | enabled={SentimentConfig.ENABLE_EARLY_EXIT_IV_SPIKE} | threshold={threshold*100:.1f}% | min_time={min_time}s | positions={len(positions)}")
        
        if not SentimentConfig.ENABLE_EARLY_EXIT_IV_SPIKE:
            logger.warning("IV_SPIKE_CHECK: Feature DISABLED in config")
            return closed  # Feature disabled
        
        for symbol, position in positions:
            
            # Check IV spike (opposite of crash)
            if position.entry_iv and position.current_iv:
                iv_rise_pct = (position.current_iv - position.entry_iv) / position.entry_iv
                
                # Only check positions that have been open for minimum time
                time_in_position = (datetime.now() - position.entry_time).total_seconds()
                
                # 🔍 DEBUG: Always log IV spike check for every position
                logger.debug(f"IV_SPIKE_CHECK: {symbol} | Entry IV: {position.entry_iv:.2f} | Current IV: {position.current_iv:.2f} | Rise: {iv_rise_pct*100:.1f}% | Threshold: {threshold*100:.1f}% | Time: {time_in_position:.1f}s")
                
                if time_in_position > min_time:  # Give position minimum time to breathe
                    if iv_rise_pct > threshold:
                        # IV spike detected - panic signal - exit immediately
                        logger.warning(f"IV_SPIKE_TRIGGERED: {symbol} | IV rise {iv_rise_pct*100:.1f}% > threshold {threshold*100:.1f}%")
                        pnl = self.close_position(
                            symbol,
                            position.current_premium,
                            f"IV_SPIKE ({iv_rise_pct*100:.1f}% from entry)"
                        )
                        if pnl:
                            closed.append(pnl)
                            
                            # Log comprehensive IV_SPIKE event data
                            self._log_iv_spike_event(
                                symbol, 
                                position.entry_iv, 
                                position.current_iv,
                                iv_rise_pct,
                                {
                                    'entry_premium': position.entry_premium,
                                    'current_premium': position.current_premium,
                                    'entry_delta': pnl.get('entry_delta', 0),
                                    'exit_delta': pnl.get('exit_delta', 0),
                                    'entry_gamma': pnl.get('entry_gamma', 0),
                                    'exit_gamma': pnl.get('exit_gamma', 0),
                                    'pnl': pnl['pnl'],
                                    'pnl_percent': pnl.get('pnl_percent', 0),
                                    'duration': pnl.get('duration', 0),
                                }
                            )
                            
                            logger.warning(
                                f"EARLY_EXIT_IV_SPIKE: {symbol} | Entry IV: {position.entry_iv:.2f} | "
                                f"Current IV: {position.current_iv:.2f} | "
                                f"IV Rise: {iv_rise_pct*100:.1f}% (threshold: {threshold*100:.1f}%) | "
                                f"PnL: ₹{pnl['pnl']:.2f} | "
                                f"Premium: ₹{position.entry_premium:.2f} → ₹{position.current_premium:.2f}"
                            )
                    else:
                        logger.debug(f"IV_SPIKE_OK: {symbol} | IV rise {iv_rise_pct*100:.1f}% <= threshold {threshold*100:.1f}%")
                else:
                    logger.debug(f"IV_SPIKE_SKIP: {symbol} | Too early, only {time_in_position:.1f}s since entry")
        
        return closed
    
    def check_stop_losses(self) -> List[Dict[str, Any]]:
        """
        Close positions at stop loss levels with decay-aware validation.
        
        Decay-aware logic:
        - Calculate expected decay (theta) since entry
        - Only trigger SL if loss exceeds decay + threshold
        - Ignore single candle dips (require 3+ sustained candles)
        - Distinguish real selling pressure from profit booking reversals
        
        This prevents premature SL exits on natural decay or temporary dips.
        
        SL Priority (in order):
        1. Percentage-based SL (20% on premium entry) - PRIMARY
        2. MAX_LOSS_PER_TRADE (safety net for catastrophic losses)
        """
        closed = []
        sl_percent = OptionsTradingConfig.STOP_LOSS_PERCENTAGE
        max_loss = OptionsTradingConfig.MAX_LOSS_PER_TRADE  # Safety net only
        decay_monitor = get_decay_monitor()
        
        for symbol, position in self._snapshot_positions_items():
            
            if position.unrealized_pnl < 0:
                loss_percent = abs((position.unrealized_pnl / (position.entry_premium * position.quantity)) * 100)
                
                # Get decay-aware analysis
                decay_signal = decay_monitor.get_smart_monitoring_signal(
                    symbol, position.current_premium, position.entry_premium
                )
                
                # Decay-aware decision logic
                should_close = False
                close_reason = "LOSS"
                
                # Check percentage-based SL FIRST (PRIMARY exit logic)
                if loss_percent >= sl_percent:
                    # CRITICAL: Loss >= SL threshold MUST exit immediately
                    # Don't wait for decay confirmation - losses this big need to be closed
                    should_close = True
                    close_reason = f"HARD_SL_HIT (SL: ₹{position.hard_sl_price:.2f})"
                    decay_reason = decay_signal.get('reason', 'N/A') if decay_signal else 'N/A'
                    logger.warning(f"STOP_LOSS: {symbol} | Loss {loss_percent:.1f}% >= {sl_percent}% threshold | FORCED EXIT | {decay_reason}")
                
                # Check MAX_LOSS as SAFETY NET ONLY (catastrophic loss prevention)
                if not should_close and abs(position.unrealized_pnl) >= max_loss:
                    should_close = True
                    close_reason = f"MAX_LOSS_SAFETY_NET (₹{position.unrealized_pnl:.2f})"
                    logger.warning(f"STOP_LOSS: {symbol} | SAFETY NET TRIGGERED: ₹{position.unrealized_pnl:.2f} >= ₹{max_loss:.2f}")
                
                if should_close:
                    if close_reason.startswith("HARD_SL_HIT"):
                        pnl = self._reconcile_broker_stop_exit(
                            symbol,
                            position.hard_sl_price,
                            close_reason,
                        )
                    else:
                        pnl = self.close_position(symbol, position.current_premium, close_reason)
                    if pnl:
                        closed.append(pnl)
                        logger.info(f"LOSS_EXIT: {symbol} | Entry: ₹{position.entry_premium:.2f} | "
                                   f"Exit: ₹{pnl.get('exit_premium', position.current_premium):.2f} | Loss: ₹{pnl['pnl']:.2f}")
        
        return closed
    
    def check_sentiment_exit(self) -> List[Dict[str, Any]]:
        """
        Check market sentiment (PCR + OI Buildup) and exit positions if sentiment FADES.
        
        OPTIMIZED: Batch fetch all PCR + OI data ONCE, then check all positions
        This avoids making duplicate API calls for same data.
        
        Strategy (IMPROVED):
        - Track entry sentiment levels (PCR + OI)
        - Exit when sentiment DETERIORATES by threshold
        - Don't wait for absolute levels, exit on FADE
        
        Examples:
        - Entry PCR: 0.9 → Exit if rises 20% (0.9 → 1.08)
        - Entry OI: 5M → Exit if drops 40% (5M → 3M)
        - Entry PCR + OI together: Any significant fade triggers
        
        This prevents holding in dead/weakening markets.
        """
        start_time = time.time()
        from .market_sentiment import get_market_sentiment
        from .optconfig import SentimentConfig
        
        closed = []
        
        # Don't check if feature disabled
        if not SentimentConfig.ENABLE_SENTIMENT_FILTER:
            return closed
        
        try:
            sentiment_engine = get_market_sentiment()
            positions = self._snapshot_positions_items()
            
            # BATCH FETCH: Get all PCR and OI data ONCE for all positions
            # This reduces API calls dramatically (1 call per data type instead of N)
            logger.debug(f"SENTIMENT_BATCH_FETCH: Fetching data for {len(positions)} positions")
            
            current_pcr_map = sentiment_engine.fetch_pcr_ratio()  # Call ONCE
            current_buildup_map = sentiment_engine.fetch_oi_buildup('Long Built Up')  # Call ONCE
            
            logger.info(f"SENTIMENT_BATCH_FETCH: Got PCR={len(current_pcr_map)} symbols, OI={len(current_buildup_map)} symbols")
            
            # Now check all positions with cached data
            for symbol, position in positions:
                
                # Get underlying from symbol (first part before expiry)
                underlying = symbol.split('25')[0] if '25' in symbol else symbol.split('24')[0] if '24' in symbol else symbol
                
                # Use pre-fetched data (no API calls here!)
                current_pcr = current_pcr_map.get(underlying)
                current_buildup = current_buildup_map.get(underlying)
                
                # Skip if first time checking (no baseline to compare)
                if position.entry_pcr is None:
                    if current_pcr is not None:
                        position.entry_pcr = current_pcr
                        position.entry_sentiment_timestamp = datetime.now()
                    if current_buildup is not None:
                        try:
                            oi_val = current_buildup.get('oi_change', 0) if isinstance(current_buildup, dict) else 0
                            position.entry_oi_buildup = oi_val if isinstance(oi_val, (int, float)) else 0
                        except Exception:
                            position.entry_oi_buildup = 0
                    
                    pcr_str = f"PCR={current_pcr:.2f}" if current_pcr is not None else "PCR=None"
                    oi_val = getattr(position, 'entry_oi_buildup', 0) or 0
                    oi_str = f"OI={oi_val:,.0f}" if oi_val else "OI=0"
                    logger.debug(f"SENTIMENT_BASELINE: {symbol} | {pcr_str} | {oi_str}")
                    continue  # Skip exit check on first reading
                
                # Always update current PCR for exit logging (even if not exiting)
                if current_pcr is not None:
                    position.current_pcr = current_pcr
                
                # Always update current OI for exit logging (even if not exiting)
                if current_buildup is not None:
                    oi_val = current_buildup.get('oi_change', 0) if isinstance(current_buildup, dict) else 0
                    if isinstance(oi_val, (int, float)):
                        position.current_oi = oi_val
                
                # Check PCR deterioration (fade detection) - LOG ONLY, DON'T EXIT
                # PCR and OI data logged for ML training, but exits only via IV_CRUSH
                pcr_fade_detected = False
                pcr_fade_reason = None
                
                if current_pcr and position.entry_pcr:
                    pcr_change_pct = ((current_pcr - position.entry_pcr) / position.entry_pcr) * 100
                    
                    # For CE (entered when bullish, PCR < 1.0):
                    # PCR rising = bearish fade (bad for CE)
                    if position.contract_type == 'CE' and position.entry_pcr < 1.0:
                        if pcr_change_pct > SentimentConfig.EXIT_PCR_FADE_THRESHOLD:  # e.g., 20% rise
                            pcr_fade_detected = True
                            if current_pcr is not None and position.entry_pcr is not None:
                                pcr_fade_reason = f"CE entry PCR {position.entry_pcr:.2f} → {current_pcr:.2f} (+{pcr_change_pct:.1f}%)"
                            else:
                                pcr_fade_reason = "CE_PCR_FADE"
                    
                    # For PE (entered when bearish, PCR > 1.0):
                    # PCR falling = bullish fade (bad for PE)
                    elif position.contract_type == 'PE' and position.entry_pcr > 1.0:
                        if pcr_change_pct < -SentimentConfig.EXIT_PCR_FADE_THRESHOLD:  # e.g., 20% drop
                            pcr_fade_detected = True
                            if current_pcr is not None and position.entry_pcr is not None:
                                pcr_fade_reason = f"PE entry PCR {position.entry_pcr:.2f} → {current_pcr:.2f} ({pcr_change_pct:.1f}%)"
                            else:
                                pcr_fade_reason = "PE_PCR_FADE"
                    
                    # Log PCR change for ML analysis (but don't use for exit)
                    if pcr_fade_reason:
                        logger.debug(f"PCR_MONITORING: {symbol} | {pcr_fade_reason}")
                
                # Check OI buildup fading (conviction weakening) - LOG ONLY, DON'T EXIT
                oi_fade_detected = False
                oi_fade_reason = None
                
                if current_buildup and position.entry_oi_buildup:
                    current_oi_change = current_buildup.get('oi_change', 0)
                    if position.entry_oi_buildup != 0:
                        oi_change_pct = ((current_oi_change - position.entry_oi_buildup) / position.entry_oi_buildup) * 100
                    else:
                        oi_change_pct = 0
                    
                    # If OI buildup dropped significantly, conviction is weakening
                    if oi_change_pct < -SentimentConfig.EXIT_OI_FADE_THRESHOLD:  # e.g., 40% drop
                        oi_fade_detected = True
                        if current_oi_change is not None and position.entry_oi_buildup is not None:
                            oi_fade_reason = f"OI {position.entry_oi_buildup:,.0f} → {current_oi_change:,.0f} ({oi_change_pct:.1f}%)"
                        else:
                            oi_fade_reason = "OI_BUILDUP_FADE"
                    
                    # Log OI change for ML analysis (but don't use for exit)
                    if oi_fade_reason:
                        logger.debug(f"OI_MONITORING: {symbol} | {oi_fade_reason}")
                
                # DISABLED: PCR and OI exits - only IV_CRUSH triggers exits for sentiment-based exits
                # if pcr_fade_detected or oi_fade_detected:
                #     exit_reason = []
                #     if pcr_fade_detected:
                #         exit_reason.append(pcr_fade_reason if pcr_fade_reason else "PCR_FADE")
                #     if oi_fade_detected:
                #         exit_reason.append(oi_fade_reason if oi_fade_reason else "OI_FADE")
                #     
                #     combined_reason = " | ".join(exit_reason) if exit_reason else "SENTIMENT_FADE"
                #     logger.warning(f"SENTIMENT_FADE: {symbol} | {combined_reason}")
                #     
                #     # Close position at current premium
                #     pnl = self.close_position(
                #         symbol,
                #         position.current_premium,
                #         f"SENTIMENT_FADE: {combined_reason}"
                #     )
                #     
                #     if pnl:
                #         closed.append(pnl)
                #         logger.info(f"SENTIMENT_EXIT_CLOSED: {symbol} | {combined_reason} | PnL: ₹{pnl['pnl']:.2f}")

        
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            logger.error(f"SENTIMENT_EXIT_CHECK: ERROR | {str(e)}")
            print(f"\n❌ SENTIMENT_EXIT_CHECK FULL ERROR:\n{tb_str}\n", file=__import__('sys').stderr)
            # Don't block monitoring on sentiment errors
        
        duration = time.time() - start_time
        logger.debug(f"SENTIMENT_CHECK: Complete | exits={len(closed)} | duration={duration:.2f}s")
        return closed
    
    def get_position_summary(self) -> Dict[str, Any]:
        """Get summary of all open positions"""
        positions = self._snapshot_positions_values()
        total_unrealized = sum(p.unrealized_pnl for p in positions)
        total_quantity = sum(p.quantity for p in positions)
        
        # Portfolio Greeks
        portfolio_delta = sum(p.current_greeks.get('delta', 0) * p.quantity for p in positions)
        portfolio_gamma = sum(p.current_greeks.get('gamma', 0) * p.quantity for p in positions)
        portfolio_theta = sum(p.current_greeks.get('theta', 0) * p.quantity for p in positions)
        
        return {
            'open_positions': len(positions),
            'total_quantity': total_quantity,
            'total_unrealized_pnl': total_unrealized,
            'portfolio_delta': portfolio_delta,
            'portfolio_gamma': portfolio_gamma,
            'portfolio_theta': portfolio_theta,
            'positions': [p.to_dict() for p in positions]
        }
    
    def get_all_positions(self) -> list:
        """Get all open positions as dictionaries for squareoff"""
        return [p.to_dict() for p in self._snapshot_positions_values()]
    
    def get_fake_move_detection_stats(self) -> Dict[str, Any]:
        """Get fake move detection statistics"""
        fake_move_detector = get_fake_move_detector()
        return fake_move_detector.get_statistics()
    
    def refresh_position_ltps(self) -> Dict[str, Any]:
        """
        Refresh LTP and Greeks for ALL open positions from broker using active symbol pool.
        
        For paper trading (small portfolio), refresh all positions every cycle.
        This ensures position data is always current and P&L reflects market reality.
        
        Uses ActiveSymbolPool to only fetch data for currently open positions,
        preventing wasted API calls on closed/historical positions.
        
        Returns:
            Dictionary with refresh statistics
        """
        start_time = time.time()
        refresh_stats = {
            'positions_checked': 0,
            'ltps_updated': 0,
            'greeks_updated': 0,
            'failed_fetches': 0,
            'errors': [],
            'bucket_info': {},
            'active_symbol_pool': {},
            'duration': 0.0
        }
        
        if not self.broker:
            logger.warning("REFRESH_LTP: No broker available")
            return refresh_stats
        
        positions = self._snapshot_positions_items()
        if not positions:
            return refresh_stats
        
        # Get active symbols from pool (only currently open positions)
        all_symbols = self.symbol_pool.get_active_symbols()
        
        if not all_symbols:
            logger.debug("REFRESH_LTP: No active positions in pool")
            return refresh_stats
        
        logger.info(f"REFRESH_LTP: Starting | active_positions={len(all_symbols)} | broker={self.broker is not None}")
        refresh_stats['active_symbol_pool'] = self.symbol_pool.get_pool_status()
        
        # BULK FETCH: Get LTP for all active positions using TRUE bulk API
        # Broker supports max 50 symbols per call, so batch into chunks
        # For 74 positions: 2 calls (50 + 24)
        ltps = {}
        batch_size = 50  # Broker max per call
        for i in range(0, len(all_symbols), batch_size):
            batch = all_symbols[i:i+batch_size]
            try:
                batch_ltps = self.broker.get_ltp_bulk(batch, exchange="NFO")
                if batch_ltps:
                    ltps.update(batch_ltps)
                logger.debug(f"REFRESH_LTP: Batch fetch | batch {i//batch_size + 1} | symbols={len(batch)} | results={len([v for v in batch_ltps.values() if v]) if batch_ltps else 0}")
            except Exception as e:
                logger.warning(f"REFRESH_LTP: Batch fetch failed | batch {i//batch_size + 1} | {str(e)}")
        
        logger.info(f"REFRESH_LTP: Bulk fetch complete | batches={len(range(0, len(all_symbols), batch_size))} | results={len([v for v in ltps.values() if v]) if ltps else 0}/{len(all_symbols)}")

        valid_ltps = [value for value in (ltps or {}).values() if value and value > 0]
        if not valid_ltps:
            logger.error("REFRESH_LTP: No valid LTPs returned - attempting broker recovery before using cached prices")
            recovered = False
            if self.broker:
                try:
                    recovered = bool(self.broker._detect_and_fix_invalid_token())
                except Exception as e:
                    logger.warning(f"REFRESH_LTP: Token recovery check failed | {str(e)}")
                if not recovered and not self.broker.authenticated:
                    logger.error("REFRESH_LTP: Broker not authenticated - attempting re-authentication")
                    recovered = bool(self.broker.authenticate(is_retry=True))

            if recovered:
                logger.info("REFRESH_LTP: Broker recovery successful - retrying bulk LTP fetch")
                ltps = {}
                for i in range(0, len(all_symbols), batch_size):
                    batch = all_symbols[i:i+batch_size]
                    try:
                        batch_ltps = self.broker.get_ltp_bulk(batch, exchange="NFO")
                        if batch_ltps:
                            ltps.update(batch_ltps)
                    except Exception as e:
                        logger.warning(f"REFRESH_LTP: RETRY batch {i//batch_size + 1} failed | {str(e)}")
                valid_ltps = [value for value in (ltps or {}).values() if value and value > 0]
                logger.info(f"REFRESH_LTP: RETRY - Bulk fetch complete | results={len(valid_ltps)}/{len(all_symbols)}")
            else:
                logger.error("REFRESH_LTP: Broker recovery failed - exiting refresh without price updates")
                refresh_stats['errors'].append('No valid LTPs available after broker recovery')
                refresh_stats['duration'] = time.time() - start_time
                return refresh_stats
        
        # Process each symbol (only active positions)
        for symbol in all_symbols:
            try:
                position = self._get_position(symbol)
                if not position:
                    continue

                refresh_stats['positions_checked'] += 1
                
                # Get LTP from bulk fetch result
                current_ltp = ltps.get(symbol) if ltps else None
                
                if not current_ltp or current_ltp <= 0:
                    # FALLBACK: If LTP fetch failed, use last known current_premium
                    # This allows SL checks to continue even during temporary broker API failures
                    # SL checks use current_premium to trigger exits, so we need SOME value
                    if position.current_premium and position.current_premium > 0:
                        current_ltp = position.current_premium
                        logger.debug(f"REFRESH_LTP: Using fallback price | {symbol} | last_price=₹{current_ltp:.2f} | (broker LTP unavailable)")
                    else:
                        refresh_stats['failed_fetches'] += 1
                        logger.warning(f"REFRESH_LTP: Failed to fetch LTP for {symbol} and no fallback available")
                        continue
                
                # STEP 2: Monitoring path updates premium only.
                # Entry-time Greeks remain stored on the position, but we do not
                # refresh or use Greeks during live monitoring.
                current_greeks = position.current_greeks.copy() if position.current_greeks else {
                    'delta': 0.5,
                    'gamma': 0.05,
                    'theta': -0.02,
                    'vega': 0.1
                }
                current_iv = position.current_iv if position.current_iv is not None else position.entry_iv if position.entry_iv is not None else 0.25
                
                self.update_position_market_data(
                    symbol=symbol,
                    current_premium=current_ltp,
                    greeks=current_greeks,
                    iv=current_iv
                )
                
                refresh_stats['ltps_updated'] += 1
                
                logger.debug(f"REFRESH_LTP: {symbol} | ltp=₹{current_ltp:.2f} | pnl=₹{position.unrealized_pnl:.2f}")
                
            except Exception as e:
                refresh_stats['failed_fetches'] += 1
                refresh_stats['errors'].append(f"{symbol}: {str(e)}")
                logger.error(f"REFRESH_LTP: ERROR | {symbol} | {str(e)}")
        
        # Save updated positions
        if refresh_stats['ltps_updated'] > 0:
            self._save_positions()
        
        refresh_stats['duration'] = time.time() - start_time
        logger.info(f"REFRESH_LTP: Complete | updated={refresh_stats['ltps_updated']}/{refresh_stats['positions_checked']} | greeks={refresh_stats['greeks_updated']} | failed={refresh_stats['failed_fetches']} | duration={refresh_stats['duration']:.2f}s")
        return refresh_stats
    
    def refresh_position_greeks(self) -> Dict[str, Any]:
        """
        Refresh GREEKS only (expensive operation) - runs every 60 seconds
        
        Separated from LTP refresh (10s) to keep main loop fast:
        - LTP refresh: Bulk API calls only (10 seconds)
        - Greeks refresh: Chain fetches per position (60 seconds)
        
        Returns:
            Dictionary with Greeks refresh statistics
        """
        greeks_stats = {
            'positions_checked': 0,
            'greeks_updated': 0,
            'failed_fetches': 0,
            'errors': []
        }
        
        positions = self._snapshot_positions_items()
        if not self.broker or not positions:
            return greeks_stats
        
        logger.info(f"REFRESH_GREEKS: Starting | positions={len(positions)}")
        
        # Get unique underlying symbols
        underlying_symbols = set(pos.underlying for _, pos in positions)
        
        # Bulk fetch all underlying LTPs once
        underlying_ltps = {}
        if underlying_symbols:
            try:
                underlying_ltps = self.broker.get_ltp_bulk(list(underlying_symbols), exchange="NSE") or {}
            except Exception as e:
                logger.warning(f"REFRESH_GREEKS: Underlying bulk fetch failed | {str(e)}")
        
        # Fetch Greeks for each position
        for symbol, position in positions:
            try:
                greeks_stats['positions_checked'] += 1
                
                underlying_ltp = underlying_ltps.get(position.underlying)
                # Track underlying move for OTM/ITM divergence analysis.
                if underlying_ltp:
                    position.current_underlying_price = underlying_ltp
                monthly_expiry_iso = position.expiry

                # Fetch option chain (with cache)
                try:
                    option_chain = self.broker.fetch_option_chain(
                        position.underlying,
                        monthly_expiry_iso,
                        current_price=underlying_ltp
                    )
                except Exception as e:
                    logger.debug(f"REFRESH_GREEKS: Could not fetch chain | {symbol} | {str(e)}")
                    continue
                
                if not option_chain:
                    continue
                
                # Find contract matching this position
                contract = option_chain.get_contract(position.strike, position.contract_type)
                
                if contract and (contract.delta != 0.0 or contract.gamma != 0.0 or 
                                contract.theta != 0.0 or contract.vega != 0.0):
                    # Update position with real Greeks
                    real_greeks = {
                        'delta': contract.delta,
                        'gamma': contract.gamma,
                        'theta': contract.theta,
                        'vega': contract.vega
                    }
                    from .volatility_calculator import get_volatility_calculator
                    vol_calc = get_volatility_calculator()
                    real_iv = contract.iv if contract.iv > 0 else vol_calc.get_dynamic_iv(symbol, default_iv=0.25)
                    
                    position.current_greeks = real_greeks
                    position.current_iv = real_iv
                    position.bid_price = contract.bid if contract.bid > 0 else position.current_premium
                    position.ask_price = contract.ask if contract.ask > 0 else position.current_premium
                    position.volume = contract.volume
                    position.open_interest = contract.open_interest
                    
                    greeks_stats['greeks_updated'] += 1
                    logger.debug(f"REFRESH_GREEKS: {symbol} | delta={contract.delta:.3f} | gamma={contract.gamma:.4f} | theta={contract.theta:.4f}")
                    
            except Exception as e:
                greeks_stats['failed_fetches'] += 1
                greeks_stats['errors'].append(f"{symbol}: {str(e)}")
                logger.debug(f"REFRESH_GREEKS: Error | {symbol} | {str(e)}")
        
        if greeks_stats['greeks_updated'] > 0:
            self._save_positions()
        
        logger.info(f"REFRESH_GREEKS: Complete | updated={greeks_stats['greeks_updated']}/{greeks_stats['positions_checked']}")
        return greeks_stats
    
    def refresh_underlying_candles(self) -> Dict[str, Any]:
        """
        Refresh candle data for all underlying stocks/indices of active positions.
        
        Candles are used for:
        - Fake move detection (sustained vs transient moves)
        - Momentum confirmation (3+ consecutive candles)
        - Volume validation
        
        Currently implemented as premium movement tracking for fake move detector.
        Future: integrate with bulk candle fetcher for true 1-min candle data.
        
        Returns:
            Dictionary with candle refresh statistics
        """
        candle_stats = {
            'candles_fetched': 0,
            'underlyings': [],
            'errors': []
        }
        
        positions = self._snapshot_positions_items()
        if not positions:
            logger.debug("REFRESH_CANDLES: No positions to monitor")
            return candle_stats
        
        # Get unique underlyings from active positions
        underlyings = set(pos.underlying for _, pos in positions)
        candle_stats['underlyings'] = sorted(list(underlyings))
        
        logger.info(f"REFRESH_CANDLES: Starting | underlyings={len(underlyings)} | symbols={underlyings}")
        
        try:
            fake_move_detector = get_fake_move_detector()
            decay_monitor = get_decay_monitor()
            
            # Record premium movements as candles for fake move detection
            # This gives the momentum filter data about sustained moves
            for symbol, position in positions:
                try:
                    # Only process if we have valid entry premium
                    if position.entry_premium <= 0:
                        logger.debug(f"REFRESH_CANDLES: SKIPPED | {symbol} | no entry_premium")
                        continue
                    
                    # Calculate price change percentage from entry
                    price_change_pct = ((position.current_premium - position.entry_premium) / position.entry_premium) * 100
                    
                    # Record as candle direction (bullish if premium up, bearish if premium down)
                    is_bullish = position.current_premium >= position.entry_premium
                    
                    # Record this candle in the fake move detector
                    fake_move_detector.record_candle_for_symbol(
                        symbol=symbol, 
                        close_price=position.current_premium, 
                        is_bullish=is_bullish
                    )
                    
                    # Record price in decay monitor for analysis
                    decay_monitor.record_price(symbol, position.current_premium)
                    
                    candle_stats['candles_fetched'] += 1
                    logger.debug(f"REFRESH_CANDLES: Recorded | {symbol} | premium={position.current_premium:.2f} | change={price_change_pct:+.2f}% | direction={'UP' if is_bullish else 'DOWN'}")
                    
                except Exception as e:
                    candle_stats['errors'].append(f"{symbol}: {str(e)}")
                    logger.warning(f"REFRESH_CANDLES: ERROR | {symbol} | {str(e)}")
        
        except Exception as e:
            logger.error(f"REFRESH_CANDLES: CRITICAL ERROR | {str(e)}")
            candle_stats['errors'].append(str(e))
        
        logger.info(f"REFRESH_CANDLES: Complete | updated={candle_stats['candles_fetched']} | underlyings={len(underlyings)}")
        return candle_stats
    
    # =========================================================================
    # Greeks-Based Smart Exit Checks (NEW FRAMEWORK)
    # =========================================================================
    
    def check_greeks_delta_reversal(self) -> List[Dict[str, Any]]:
        """
        Exit if delta declining rapidly - indicates reversal/momentum loss.
        
        IMPROVED: Requires confirmation via:
        - 2 consecutive cycles of delta decline, OR
        - Rolling average of last 3 samples showing declining trend
        
        This reduces false positives during volatile periods (whipsaw avoidance).
        
        Threshold: delta_change < -0.05 per cycle (confirmed)
        """
        closed = []
        
        positions = self._snapshot_positions_items()
        logger.debug(f"GREEKS_CHECK: Starting check_greeks_delta_reversal | positions={len(positions)}")
        
        for symbol, position in positions:
            try:
                # Use new confirmation logic with multiple sample checking
                is_confirmed, delta_change = position.get_delta_trend_confirmed()
                
                logger.debug(f"GREEKS_EVAL: Delta reversal | {symbol} | delta_change={delta_change:.4f if delta_change else None} | confirmed={is_confirmed}")
                
                if is_confirmed and delta_change and delta_change < -0.05:
                    logger.warning(f"GREEKS_SIGNAL: EXIT | {symbol} | reason=greeks_delta_reversal | delta_change={delta_change:.4f} | premium={position.current_premium:.2f}")
                    
                    closed_pos = self.close_position(
                        symbol,
                        position.current_premium,
                        "greeks_delta_reversal"
                    )
                    if closed_pos:
                        closed.append(closed_pos)
                
            except Exception as e:
                logger.error(f"GREEKS_CHECK: Delta reversal error | {symbol} | {str(e)}")
        
        logger.debug(f"GREEKS_CHECK: check_greeks_delta_reversal complete | closed={len(closed)}")
        return closed
    
    def check_greeks_gamma_explosion(self) -> List[Dict[str, Any]]:
        """
        Exit if gamma spiked - indicates high binary risk near expiry.
        
        IMPROVED: Uses BOTH relative multiplier AND absolute cap:
        - Trigger if current_gamma > entry_gamma × 1.5, OR
        - Trigger if current_gamma > 0.04 (absolute safety limit)
        
        This protects against near-expiry binary behavior even when entry gamma is high.
        
        Thresholds:
        - Multiplier: 1.5x from entry
        - Absolute: 0.04 (configurable)
        """
        closed = []
        
        positions = self._snapshot_positions_items()
        logger.debug(f"GREEKS_CHECK: Starting check_greeks_gamma_explosion | positions={len(positions)}")
        
        for symbol, position in positions:
            try:
                # Use new status method with both checks
                is_dangerous, current_gamma, reason = position.get_gamma_status()
                
                logger.debug(f"GREEKS_EVAL: Gamma explosion | {symbol} | current={current_gamma:.4f} | dangerous={is_dangerous} | reason={reason}")
                
                if is_dangerous:
                    logger.warning(f"GREEKS_SIGNAL: EXIT | {symbol} | reason=greeks_gamma_explosion | {reason} | premium={position.current_premium:.2f}")
                    
                    closed_pos = self.close_position(
                        symbol,
                        position.current_premium,
                        "greeks_gamma_explosion"
                    )
                    if closed_pos:
                        closed.append(closed_pos)
                
            except Exception as e:
                logger.error(f"GREEKS_CHECK: Gamma explosion error | {symbol} | {str(e)}")
        
        logger.debug(f"GREEKS_CHECK: check_greeks_gamma_explosion complete | closed={len(closed)}")
        return closed
    
    def check_greeks_theta_acceleration(self) -> List[Dict[str, Any]]:
        """
        Exit if theta accelerating rapidly - time decay is eating position.
        
        IMPROVED: Only triggers if:
        - |current_theta| > |entry_theta| × 3, AND
        - (P&L <= 0 OR Delta is weakening)
        
        This avoids killing winning trades due to normal time decay (theta noise).
        Only exits when theta decay is combined with adverse conditions.
        
        Threshold: |current_theta| > |entry_theta| × 3.0 (confirmed with context check)
        """
        closed = []
        
        positions = self._snapshot_positions_items()
        logger.debug(f"GREEKS_CHECK: Starting check_greeks_theta_acceleration | positions={len(positions)}")
        
        for symbol, position in positions:
            try:
                # Use new status method with directional context
                is_dangerous, current_theta, reason, pnl_check, delta_check = position.get_theta_status()
                
                logger.debug(f"GREEKS_EVAL: Theta acceleration | {symbol} | current={current_theta:.4f} | dangerous={is_dangerous} | pnl_check={pnl_check} | delta_check={delta_check} | reason={reason}")
                
                if is_dangerous:
                    logger.warning(f"GREEKS_SIGNAL: EXIT | {symbol} | reason=greeks_theta_acceleration | {reason} | premium={position.current_premium:.2f}")
                    
                    closed_pos = self.close_position(
                        symbol,
                        position.current_premium,
                        "greeks_theta_acceleration"
                    )
                    if closed_pos:
                        closed.append(closed_pos)
                
            except Exception as e:
                logger.error(f"GREEKS_CHECK: Theta acceleration error | {symbol} | {str(e)}")
        
        logger.debug(f"GREEKS_CHECK: check_greeks_theta_acceleration complete | closed={len(closed)}")
        return closed
    
    def check_greeks_vega_crush(self) -> List[Dict[str, Any]]:
        """
        Exit if IV crushing the position - volatility collapse.
        
        IMPROVED: Uses DYNAMIC threshold based on entry IV regime:
        - Low IV regime (entry_iv < 50): threshold = 1.0% change
        - High IV regime (entry_iv >= 50): threshold = 3.0% change
        OR uses fixed threshold = 2.0% if dynamic disabled
        
        This provides context-aware volatility handling:
        - 2% IV drop is normal in low-IV regimes, requires 1% only
        - 2% IV drop is small in high-IV regimes, requires 3%
        
        Sharp IV drops hurt long options (our positions).
        """
        closed = []
        
        positions = self._snapshot_positions_items()
        logger.debug(f"GREEKS_CHECK: Starting check_greeks_vega_crush | positions={len(positions)}")
        
        for symbol, position in positions:
            try:
                # Use new status method with dynamic threshold
                is_dangerous, iv_change_pct, reason = position.get_vega_status()
                
                logger.debug(f"GREEKS_EVAL: Vega crush | {symbol} | iv_change={iv_change_pct:.2f}% | dangerous={is_dangerous} | reason={reason}")
                
                if is_dangerous:
                    logger.warning(f"GREEKS_SIGNAL: EXIT | {symbol} | reason=greeks_vega_crush | {reason} | premium={position.current_premium:.2f}")
                    
                    closed_pos = self.close_position(
                        symbol,
                        position.current_premium,
                        "greeks_vega_crush"
                    )
                    if closed_pos:
                        closed.append(closed_pos)
                
            except Exception as e:
                logger.error(f"GREEKS_CHECK: Vega crush error | {symbol} | {str(e)}")
        
        logger.debug(f"GREEKS_CHECK: check_greeks_vega_crush complete | closed={len(closed)}")
        return closed
    
    def check_greeks_health_score(self) -> List[Dict[str, Any]]:
        """
        Combined health check - exit if 2+ Greeks conditions are unhealthy.
        
        IMPROVED: Provides detailed transparency in logging:
        - Logs exact red flags: delta_bad=True, gamma_bad=False, theta_bad=True, vega_bad=False
        - Shows which specific conditions failed
        - Helps auditors understand exit decisions
        
        Uses improved individual checks:
        - Delta with 2-cycle confirmation
        - Gamma with absolute cap
        - Theta with P&L/delta context
        - Vega with dynamic threshold
        
        Trigger: 2 or more conditions are RED (unhealthy)
        """
        closed = []
        
        positions = self._snapshot_positions_items()
        logger.debug(f"GREEKS_CHECK: Starting check_greeks_health_score | positions={len(positions)}")
        
        for symbol, position in positions:
            try:
                # Use new unified status method
                is_unhealthy, conditions, formatted_str = position.get_health_status()
                
                # Log with transparency
                logger.debug(f"GREEKS_EVAL: Health score | {symbol} | {formatted_str} | unhealthy={is_unhealthy}")
                
                if is_unhealthy:
                    # Build detailed reason string
                    bad_reasons = [k for k, v in conditions.items() if v]
                    reason_str = ",".join(bad_reasons)
                    
                    logger.warning(f"GREEKS_SIGNAL: EXIT | {symbol} | reason=greeks_health_failure | {formatted_str} | failed_reasons={reason_str} | premium={position.current_premium:.2f}")
                    
                    closed_pos = self.close_position(
                        symbol,
                        position.current_premium,
                        "greeks_health_failure"
                    )
                    if closed_pos:
                        closed.append(closed_pos)
                
            except Exception as e:
                logger.error(f"GREEKS_CHECK: Health score error | {symbol} | {str(e)}")
        
        logger.debug(f"GREEKS_CHECK: check_greeks_health_score complete | closed={len(closed)}")
        return closed
    
    def check_ml_greeks_quality(self) -> List[Dict[str, Any]]:
        """
        ML-guided exit check - exit if Greeks quality degrades below threshold.
        
        Uses ML learning engine to score Greeks quality and exit early
        if the position's Greeks setup becomes poor.
        
        This prevents holding positions with bad Greeks even if other signals haven't triggered.
        
        Returns:
            List of closed positions
        """
        closed = []
        
        try:
            from .ml_integration_engine import get_ml_integration_engine
            ml_engine = get_ml_integration_engine()
        except ImportError:
            logger.debug("ML_QUALITY_CHECK: ML engine not available")
            return closed
        
        positions = self._snapshot_positions_items()
        logger.debug(f"ML_QUALITY_CHECK: Starting ML Greeks quality check | positions={len(positions)}")
        
        for symbol, position in positions:
            try:
                # Check if Greeks quality has degraded
                should_exit, reason, ml_score = ml_engine.should_exit_by_ml_quality(
                    position.current_greeks,
                    position.contract_type,
                    position.action
                )
                
                if should_exit:
                    logger.warning(f"ML_QUALITY: EXIT | {symbol} | reason={reason} | score={ml_score:.2f} | premium={position.current_premium:.2f}")
                    
                    closed_pos = self.close_position(
                        symbol,
                        position.current_premium,
                        f"ml_greeks_quality_degradation"
                    )
                    if closed_pos:
                        closed.append(closed_pos)
                        logger.info(f"ML_QUALITY: CLOSED | {symbol} | pnl=₹{closed_pos.get('pnl', 0):.2f}")
                else:
                    logger.debug(f"ML_QUALITY: PASS | {symbol} | score={ml_score:.2f}")
            
            except Exception as e:
                logger.error(f"ML_QUALITY: ERROR | {symbol} | {str(e)}")
        
        logger.debug(f"ML_QUALITY_CHECK: Complete | closed={len(closed)}")
        return closed
    
    def perform_periodic_monitoring(self) -> Dict[str, Any]:
        """
        Perform all monitoring checks with rate limit handling.
        Call this periodically (e.g., every 30 seconds) to monitor positions
        and process queued API requests.
        
        Returns:
            Dictionary with monitoring results
        """
        monitoring_result = {
            'timestamp': datetime.now().isoformat(),
            'positions_monitored': len(self._snapshot_positions_values()),
            'closed_by_expiry': [],
            'closed_by_profit': [],
            'closed_by_stoploss': [],
            'closed_by_trailing': [],
            'closed_by_momentum': [],
            'closed_by_sentiment': [],
            # Greeks-based exits (NEW)
            'closed_by_greeks_delta': [],
            'closed_by_greeks_gamma': [],
            'closed_by_greeks_theta': [],
            'closed_by_greeks_vega': [],
            'closed_by_greeks_health': [],
            'closed_by_ml_quality': [],  # ML-guided exits (NEW)
            'ltps_refreshed': 0,
            'rate_limiter_stats': {},
            'error': None
        }
        
        try:
            # Process any rate-limited requests that were queued for retry
            if self.broker:
                self.broker.process_pending_rate_limited_requests()
            
            # CRITICAL: Refresh LTP for all positions before checking exits
            positions = self._snapshot_positions_values()
            if positions:
                refresh_stats = self.refresh_position_ltps()
                monitoring_result['ltps_refreshed'] = refresh_stats['ltps_updated']
                logger.info(f"MONITORING: Refreshed LTP for {refresh_stats['ltps_updated']}/{len(positions)} positions")
                if refresh_stats['ltps_updated'] == 0:
                    monitoring_result['error'] = 'No fresh LTP data available; skipped exit checks'
                    if self.broker:
                        monitoring_result['rate_limiter_stats'] = self.broker.get_rate_limiter_stats()
                    logger.error("MONITORING: No fresh LTP data available - skipping exit checks to avoid stale-price decisions")
                    return monitoring_result
                
                # OPTIMIZATION: Refresh underlying candles for fake move detection
                candle_stats = self.refresh_underlying_candles()
                logger.debug(f"MONITORING: Candle data updated for {candle_stats['candles_fetched']} positions | underlyings={candle_stats['underlyings']}")
            
            # Check and close positions by expiry
            expired = self.check_expiry_close()
            monitoring_result['closed_by_expiry'] = [p['symbol'] for p in expired]
            
            # Check and close positions by profit targets
            profit_closes = self.check_profit_targets()
            monitoring_result['closed_by_profit'] = [p['symbol'] for p in profit_closes]
            
            # Check and close positions by trailing stop losses (20% SL, update every 10% gain)
            trailing_closes = self.check_trailing_stop_losses()
            monitoring_result['closed_by_trailing'] = [p['symbol'] for p in trailing_closes]
            
            # Track which positions were closed by TRIAL_SL to avoid duplicate momentum check
            trailing_closed_symbols = set(p['symbol'] for p in trailing_closes)
            
            # Greeks-based exits disabled during monitoring.
            greeks_delta_closes = []
            monitoring_result['closed_by_greeks_delta'] = []
            greeks_closed_symbols = set()
            
            candle_macd_fade_closes = self.check_candle_macd_fade_exit()
            candle_macd_fade_closed_symbols = set(p['symbol'] for p in candle_macd_fade_closes)
            monitoring_result['closed_by_candle_macd_fade'] = [p['symbol'] for p in candle_macd_fade_closes]

            # ⭐ PRIORITY 2: Momentum reversal (TIER 1 early exit - backup if Greeks missed it)
            # Only check positions NOT already closed by Greeks delta or TRIAL_SL
            momentum_closes = self.check_momentum_reversal()
            # Filter out any that were already closed by Greeks or TRIAL_SL
            momentum_closes = [
                p for p in momentum_closes
                if p['symbol'] not in greeks_closed_symbols
                and p['symbol'] not in trailing_closed_symbols
                and p['symbol'] not in candle_macd_fade_closed_symbols
            ]
            monitoring_result['closed_by_momentum'] = [p['symbol'] for p in momentum_closes]
            
            # ⭐ PRIORITY 2.3: Stale consolidation exit
            # Enabled to cut low-momentum positions that never activate TRIAL_SL,
            # while keeping stale timeout disabled for stronger trend days.
            stale_consol_closes = self.check_stale_consolidation_exits()
            monitoring_result['closed_by_stale_consolidation'] = [p['symbol'] for p in stale_consol_closes]

            if stale_consol_closes:
                logger.info(
                    f"MONITORING: Stale consolidation exit detected {len(stale_consol_closes)} positions | "
                    f"TRIAL_SL inactive after hold-time threshold"
                )
            
            # ⭐ NEW: Retry failed SL placement (critical - ensures all positions protected)
            # This runs in background to place SL orders for any positions still missing them
            sl_retry_stats = self.retry_failed_sl_orders()
            if sl_retry_stats['attempted'] > 0:
                monitoring_result['sl_retries_attempted'] = sl_retry_stats['attempted']
                monitoring_result['sl_retries_placed'] = sl_retry_stats['placed']
                monitoring_result['sl_retries_max_exceeded'] = sl_retry_stats['max_retries']
            
            # ⭐ PRIORITY 2.5: Time-based staleness exit (DISABLED)
            # ANALYSIS: Costs ₹162,905 in missed gains on strong trend days (MUTHOOTFIN +171%, SIEMENS +52%)
            # Let HARD_SL (-10%) and TRIAL_SL handle exits instead
            stale_closes = []  # DISABLED - was: []
            monitoring_result['closed_by_stale_timeout'] = []
            
            # if stale_closes:  # DISABLED
            #     logger.info(f"MONITORING: Stale timeout detected {len(stale_closes)} non-trending positions | Proactive exit before momentum hit")
            
            # Check and close positions by stop loss (hard SL if loss exceeds 20%)
            sl_closes = self.check_stop_losses()
            monitoring_result['closed_by_stoploss'] = [p['symbol'] for p in sl_closes]
            
            # Check and close positions by sentiment fade
            sentiment_closes = self.check_sentiment_exit()
            monitoring_result['closed_by_sentiment'] = [p['symbol'] for p in sentiment_closes]
            
            # ⭐ NEW: Check and close positions by IV crash (premium decay)
            iv_crash_closes = self.check_iv_crash()
            monitoring_result['closed_by_iv_crash'] = [p['symbol'] for p in iv_crash_closes]
            
            # ⭐ NEW: Check and close positions by IV spike (panic/crash signal)
            iv_spike_closes = self.check_iv_spike()
            monitoring_result['closed_by_iv_spike'] = [p['symbol'] for p in iv_spike_closes]
            
            # Other Greeks-based monitoring exits disabled.
            greeks_gamma_closes = []
            monitoring_result['closed_by_greeks_gamma'] = []
            greeks_theta_closes = []
            monitoring_result['closed_by_greeks_theta'] = []
            greeks_vega_closes = []
            monitoring_result['closed_by_greeks_vega'] = []
            greeks_health_closes = []
            monitoring_result['closed_by_greeks_health'] = []
            ml_quality_closes = []
            monitoring_result['closed_by_ml_quality'] = []
            
            # Get rate limiter statistics
            if self.broker:
                monitoring_result['rate_limiter_stats'] = self.broker.get_rate_limiter_stats()
            
            total_closed = len(expired) + len(profit_closes) + len(trailing_closes) + len(momentum_closes) + len(stale_closes) + len(sl_closes) + len(sentiment_closes) + len(iv_crash_closes) + len(iv_spike_closes) + len(greeks_delta_closes) + len(greeks_gamma_closes) + len(greeks_theta_closes) + len(greeks_vega_closes) + len(greeks_health_closes) + len(ml_quality_closes)
            open_positions = self._snapshot_positions_values()
            logger.info(f"MONITORING: Checked {len(open_positions)} positions | Closed {total_closed} "
                       f"(expiry={len(expired)}, profit={len(profit_closes)}, trailing={len(trailing_closes)}, "
                       f"momentum={len(momentum_closes)}, stale={len(stale_closes)}, stoploss={len(sl_closes)}, sentiment={len(sentiment_closes)}, "
                       f"iv_crash={len(iv_crash_closes)}, iv_spike={len(iv_spike_closes)}, "
                       f"greeks_delta={len(greeks_delta_closes)}, greeks_gamma={len(greeks_gamma_closes)}, "
                       f"greeks_theta={len(greeks_theta_closes)}, greeks_vega={len(greeks_vega_closes)}, "
                       f"greeks_health={len(greeks_health_closes)}, ml_quality={len(ml_quality_closes)})")
            
            # Log current position state summary
            positions = self._snapshot_positions_values()
            if positions:
                total_pnl = sum(p.unrealized_pnl for p in positions)
                portfolio_delta = sum(p.current_greeks.get('delta', 0) * p.quantity for p in positions)
                portfolio_gamma = sum(p.current_greeks.get('gamma', 0) * p.quantity for p in positions)
                logger.debug(f"POSITION_STATE: open={len(positions)} | upnl=₹{total_pnl:.2f} | "
                            f"delta={portfolio_delta:.2f} | gamma={portfolio_gamma:.4f} | interval=10s")
            
        except Exception as e:
            monitoring_result['error'] = str(e)
            logger.error(f"MONITORING: ERROR | {str(e)}")
            print(f"❌ Error during monitoring: {str(e)}")
        
        # 🔴 SAVE LIVE DATA: Scrape from existing files and save summary
        try:
            live_tracker = get_live_data_tracker()
            live_tracker.save()
        except Exception as e:
            logger.debug(f"LIVE_DATA_TRACKING: SAVE_FAILED | {str(e)}")
        
        return monitoring_result
    
    def _save_positions(self):
        """Save positions to disk"""
        try:
            if not self.positions_file.parent.exists():
                self.positions_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Filter out stuck intraday positions (prevent re-adding them after removal)
            STUCK_INTRADAY_SYMBOLS = {'SONACOMS30DEC25490CE', 'INDIANB30DEC25780CE'}
            
            positions_list = [
                pos.to_dict() 
                for pos in self._snapshot_positions_values() 
                if pos.symbol not in STUCK_INTRADAY_SYMBOLS
            ]
            positions_list.sort(key=lambda item: item.get('entry_time', ''))
            positions_data = {
                'timestamp': datetime.now().isoformat(),
                'positions': positions_list
            }

            with tempfile.NamedTemporaryFile(
                mode='w',
                dir=self.positions_file.parent,
                delete=False,
                suffix='.json',
                encoding='utf-8',
            ) as tmp:
                json.dump(positions_data, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name

            os.replace(tmp_path, self.positions_file)
        except Exception as e:
            print(f"⚠️ Error saving positions: {str(e)}")
    
    def _load_positions(self):
        """Load positions from disk"""
        try:
            if not self.positions_file.exists():
                return
            
            with open(self.positions_file, 'r') as f:
                data = json.load(f)
            
            raw_positions = data.get('positions', [])

            if isinstance(raw_positions, dict):
                positions_iter = raw_positions.items()
            else:
                positions_iter = (
                    (pos.get('symbol'), pos)
                    for pos in raw_positions
                    if pos.get('symbol')
                )

            # Skip stuck intraday positions (from SL issues in PAPER mode)
            STUCK_INTRADAY_SYMBOLS = {'SONACOMS30DEC25490CE', 'INDIANB30DEC25780CE'}
            
            for symbol, pos_data in positions_iter:
                # Skip stuck positions
                if symbol in STUCK_INTRADAY_SYMBOLS:
                    logger.info(f"POSITION_LOAD: SKIPPING stuck intraday position {symbol}")
                    continue
                entry_time_raw = pos_data.get('entry_time')
                try:
                    entry_time = datetime.fromisoformat(entry_time_raw) if entry_time_raw else datetime.now()
                except Exception:
                    entry_time = datetime.now()

                position = OptionPosition(
                    symbol=symbol,
                    underlying=pos_data['underlying'],
                    strike=pos_data['strike'],
                    expiry=pos_data['expiry'],
                    contract_type=pos_data['contract_type'],
                    action=pos_data['action'],
                    quantity=pos_data['quantity'],
                    entry_premium=pos_data['entry_premium'],
                    entry_time=entry_time,
                    order_id=pos_data.get('order_id', ''),
                    underlying_alert_price=pos_data.get('underlying_alert_price')
                )
                position.current_premium = pos_data.get('current_premium', pos_data['entry_premium'])
                position.current_greeks = pos_data.get('current_greeks', {})
                position.current_iv = pos_data.get('current_iv', 20.0)
                position.unrealized_pnl = pos_data.get('unrealized_pnl', 0.0)
                position.highest_premium = pos_data.get('highest_premium', position.highest_premium)
                position.lowest_premium = pos_data.get('lowest_premium', position.lowest_premium)
                # Restore SL order tracking
                position.sl_order_id = pos_data.get('sl_order_id')
                position.sl_order_price = pos_data.get('sl_order_price')
                # Restore trailing SL fields
                position.trailing_sl_activated = pos_data.get('trailing_sl_activated', False)
                position.last_trailing_sl_price = pos_data.get('last_trailing_sl_price')
                position.trailing_sl_activation_time = pos_data.get('trailing_sl_activation_time')
                position.trailing_sl_update_count = pos_data.get('trailing_sl_update_count', 0)
                # Restore TRIAL SL fields
                position.hard_sl_price = pos_data.get('hard_sl_price', position.entry_premium * 0.9)
                position.trial_sl_enabled = pos_data.get('trial_sl_enabled', False)
                position.trial_sl_price = pos_data.get('trial_sl_price')
                position.trial_sl_activation_time = pos_data.get('trial_sl_activation_time')
                position.trial_sl_update_count = pos_data.get('trial_sl_update_count', 0)
                
                # Restore Greeks tracking fields (NEW)
                position.entry_delta = pos_data.get('entry_delta', 0.5)
                position.entry_gamma = pos_data.get('entry_gamma', 0.05)
                position.entry_theta = pos_data.get('entry_theta', -0.02)
                position.entry_vega = pos_data.get('entry_vega', 0.1)
                position.entry_iv = pos_data.get('entry_iv', 20.0)
                
                # Restore Greeks history for trend detection
                if pos_data.get('entry_greeks'):
                    position.entry_greeks = pos_data['entry_greeks']
                
                # Log restored Greeks tracking
                logger.debug(f"POSITION_LOAD: Restored Greeks tracking | symbol={symbol} | "
                           f"delta={position.entry_delta:.3f} | gamma={position.entry_gamma:.4f} | "
                           f"theta={position.entry_theta:.4f} | vega={position.entry_vega:.4f}")
                
                last_updated_raw = pos_data.get('last_updated')
                if last_updated_raw:
                    try:
                        position.last_updated = datetime.fromisoformat(last_updated_raw)
                    except Exception:
                        pass
                with self._positions_lock:
                    self.positions[symbol] = position
                
                # 🔴 CRITICAL FIX: Add restored positions to symbol pool for LTP refreshing
                # Without this, refresh_position_ltps() finds no active symbols and skips all updates
                self.symbol_pool.add_symbol(symbol, entry_time=position.entry_time)
                
            logger.info(f"POSITION_LOAD: Loaded {len(self._snapshot_positions_values())} positions | Active symbols in pool: {len(self.symbol_pool.get_active_symbols())}")

            # Verify any restored sl_order_ids against broker on startup.
            # If the bot was down and the broker SL fired, the stored sl_order_id
            # refers to a COMPLETE order. Attempting to cancel it later blocks the exit.
            if OptionsTradingConfig.TRADING_MODE == "LIVE" and self.broker:
                for symbol, position in self._snapshot_positions_items():
                    if not position.sl_order_id:
                        continue
                    try:
                        order_status = self.broker.get_order_status(position.sl_order_id)
                        if not order_status:
                            continue
                        status = str(order_status.get('status', '')).upper()
                        if status in ('COMPLETE', 'FILLED', 'FULLY_FILLED'):
                            # SL fired while bot was down — close position at SL price
                            actual_price = order_status.get('average_price') or position.sl_order_price or position.entry_premium * 0.9
                            logger.warning(
                                f"POSITION_LOAD: SL_FILLED_DURING_DOWNTIME | {symbol} | "
                                f"order_id={position.sl_order_id} | exit=₹{actual_price}"
                            )
                            self.close_position(
                                symbol, actual_price,
                                "SL_FILLED_DURING_DOWNTIME",
                                broker_managed_exit=True,
                                skip_sl_cancel=True,
                                exit_order_id=position.sl_order_id,
                            )
                        elif status in ('REJECTED', 'CANCELLED', 'EXPIRED'):
                            # SL order is dead — clear it so retry_failed_sl_orders places a fresh one
                            logger.warning(
                                f"POSITION_LOAD: SL_ORDER_DEAD | {symbol} | "
                                f"order_id={position.sl_order_id} | status={status} | clearing for re-placement"
                            )
                            position.sl_order_id = None
                            position.sl_order_price = None
                            self._save_positions()
                    except Exception as verify_err:
                        logger.warning(f"POSITION_LOAD: SL_VERIFY_ERROR | {symbol} | {verify_err}")

        except Exception as e:
            print(f"⚠️ Error loading positions: {str(e)}")
    
    def _save_pnl_history(self, pnl_info: Dict[str, Any]):
        """Save closed position P&L to history"""
        try:
            if not self.pnl_history_file.parent.exists():
                self.pnl_history_file.parent.mkdir(parents=True, exist_ok=True)

            # Load existing history
            history = []
            if self.pnl_history_file.exists():
                with open(self.pnl_history_file, 'r') as f:
                    history = json.load(f)

            # Dedup guard: skip if same symbol + entry_time already recorded today
            # (prevents double-write when two bot processes close the same position)
            symbol = pnl_info.get('symbol', '')
            entry_time = pnl_info.get('entry_time', '')
            today = datetime.now().date().isoformat()
            for existing in history:
                if (existing.get('symbol') == symbol and
                        existing.get('entry_time') == entry_time and
                        (existing.get('closed_at', '') or '').startswith(today)):
                    logger.warning(f"PNL_HISTORY: DUPLICATE_SKIPPED | {symbol} | entry={entry_time} | already recorded today")
                    return

            # Add new entry
            pnl_info['closed_at'] = datetime.now().isoformat()
            history.append(pnl_info)

            with tempfile.NamedTemporaryFile(
                mode='w',
                dir=self.pnl_history_file.parent,
                delete=False,
                suffix='.json',
                encoding='utf-8',
            ) as tmp:
                json.dump(history, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name

            os.replace(tmp_path, self.pnl_history_file)
        except Exception as e:
            print(f"⚠️ Error saving P&L history: {str(e)}")

# =============================================================================
# Global monitor instance
# =============================================================================

_option_monitor_instance = None

def get_option_monitor(broker: Optional[AngelOneOptionsBroker] = None) -> OptionPositionMonitor:
    """Get or create option monitor instance"""
    global _option_monitor_instance
    if _option_monitor_instance is None:
        _option_monitor_instance = OptionPositionMonitor(broker)
    return _option_monitor_instance
