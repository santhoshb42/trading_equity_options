"""
Options Position Monitor

Tracks open option contracts (CE/PE positions) with Greeks, IV, premium tracking,
and P&L calculation specific to derivatives trading.
"""

import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path

from .optconfig import OptionsCapitalConfig, OptionsTradingConfig, BASE_DIR
from .angelone_options import AngelOneOptionsBroker, get_options_broker
from .optlogging import logger, log_position, log_pnl, log_event
from .fake_move_detector import get_fake_move_detector
from .trade_logger import get_trade_logger

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
                 underlying_alert_price: Optional[float] = None):
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
        
        # Current state
        self.current_premium = entry_premium
        self.highest_premium = entry_premium  # Track highest premium for trailing exit
        self.current_greeks = {
            'delta': 0.5,
            'gamma': 0.05,
            'theta': -0.02,
            'vega': 0.1
        }
        self.current_iv = 20.0
        self.entry_iv = None  # IV at entry (for IV crush detection)
        self.last_updated = entry_time
        
        # Market data tracking for liquidity
        self.bid_price = entry_premium
        self.ask_price = entry_premium
        self.volume = 0
        self.open_interest = 0
        
        # Exit tracking
        self.exit_premium = None
        self.exit_time = None
        self.exit_order_id = None
        self.exit_reason = None  # PROFIT, LOSS, TIME, MANUAL, EXPIRY
        
        # P&L tracking
        self.unrealized_pnl = 0.0
        self.realized_pnl = None
    
    def update_market_data(self, current_premium: float, greeks: Dict[str, float], iv: float):
        """Update position with current market data"""
        self.current_premium = current_premium
        
        # Track highest premium reached (for trailing exit)
        if current_premium > self.highest_premium:
            self.highest_premium = current_premium
        
        self.current_greeks = greeks
        self.current_iv = iv
        self.last_updated = datetime.now()
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
    
    def close_position(self, exit_premium: float, exit_reason: str) -> Dict[str, Any]:
        """Close option position and calculate final P&L"""
        self.exit_premium = exit_premium
        self.exit_time = datetime.now()
        self.exit_reason = exit_reason
        
        # Calculate realized P&L
        premium_difference = exit_premium - self.entry_premium
        if self.action == "BUY":
            self.realized_pnl = premium_difference * self.quantity
        else:  # SELL
            self.realized_pnl = -premium_difference * self.quantity
        
        return {
            'symbol': self.symbol,
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
            'entry_time': self.entry_time.isoformat() if isinstance(self.entry_time, datetime) else self.entry_time
        }
    
    def is_expired(self) -> bool:
        """Check if option position has expired"""
        expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d").date()
        return datetime.now().date() > expiry_date
    
    def days_to_expiry(self) -> int:
        """Get days remaining to expiry"""
        expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d").date()
        return (expiry_date - datetime.now().date()).days
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert position to dictionary"""
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
            'order_id': self.order_id,
            'current_premium': self.current_premium,
            'current_premium_total': self.current_premium * self.quantity,
            'current_greeks': self.current_greeks,
            'current_iv': self.current_iv,
            'unrealized_pnl': self.unrealized_pnl,
            'highest_premium': self.highest_premium,
            'days_to_expiry': self.days_to_expiry(),
            'last_updated': self.last_updated.isoformat() if isinstance(self.last_updated, datetime) else self.last_updated,
            'underlying_alert_price': self.underlying_alert_price
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
        self.positions_file = BASE_DIR / "data" / "option_positions.json"
        self.pnl_history_file = BASE_DIR / "data" / "option_pnl_history.json"
        
        # Bucket manager for bulk LTP fetching (optimization)
        self.ltp_bucket_manager = LTPBucketManager(bucket_size=5)
        
        # Load existing positions
        self._load_positions()
    
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
                    underlying_alert_price: Optional[float] = None) -> bool:
        """Add new option position"""
        try:
            logger.debug(f"POSITION_ADD: {symbol} | qty={quantity} | premium={entry_premium:.2f}")
            
            if symbol in self.positions:
                logger.warning(f"POSITION_ADD: DUPLICATE | {symbol}")
                print(f"⚠️ Position {symbol} already exists")
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
                underlying_alert_price=underlying_alert_price
            )
            
            self.positions[symbol] = position
            self._save_positions()
            
            # Register for fake move monitoring
            fake_move_detector = get_fake_move_detector()
            fake_move_detector.monitor_position(symbol, entry_premium, entry_iv=20.0)
            
            # Log trade entry to CSV
            try:
                trade_logger = get_trade_logger()
                position.trade_id = trade_logger.log_trade_entry(
                    symbol=underlying,  # Log with underlying (BANKNIFTY not BANKNIFTY25XXX1900CE)
                    action=action,
                    entry_premium=entry_premium,
                    confidence=85,  # Default confidence
                    score=85,
                    features=[85, 85, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]  # Placeholder features
                )
                logger.debug(f"POSITION_ADD: TRADE_LOGGED | {symbol} | trade_id={position.trade_id}")
            except Exception as e:
                logger.warning(f"POSITION_ADD: TRADE_LOG_FAILED | {symbol} | {str(e)}")
            
            logger.info(f"POSITION_ADD: SUCCESS | {symbol} | {contract_type} | {action} | qty={quantity} | premium=₹{entry_premium:.2f}")
            print(f"✅ Added option position: {symbol}")
            
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
            if symbol not in self.positions:
                return False
            
            self.positions[symbol].update_market_data(current_premium, greeks, iv)
            
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
    
    def close_position(self, symbol: str, exit_premium: float, exit_reason: str) -> Optional[Dict[str, Any]]:
        """Close option position"""
        try:
            logger.debug(f"POSITION_CLOSE: {symbol} | reason={exit_reason} | premium={exit_premium:.2f}")
            
            if symbol not in self.positions:
                logger.warning(f"POSITION_CLOSE: NOT_FOUND | {symbol}")
                print(f"⚠️ Position {symbol} not found")
                return None
            
            position = self.positions[symbol]
            pnl_info = position.close_position(exit_premium, exit_reason)
            
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
            
            # Move to history
            self.closed_positions.append(position)
            del self.positions[symbol]
            
            # Clear fake move monitoring
            fake_move_detector = get_fake_move_detector()
            fake_move_detector.close_position_monitoring(symbol)
            
            self._save_positions()
            self._save_pnl_history(pnl_info)
            
            logger.info(f"POSITION_CLOSE: SUCCESS | {symbol} | {exit_reason} | PnL=₹{pnl_info['pnl']:.2f} ({pnl_info['pnl_percent']:.2f}%)")
            print(f"✅ Closed position {symbol}: {exit_reason} | PnL: ₹{pnl_info['pnl']:.2f}")
            
            log_position("closed", pnl_info)
            log_pnl(symbol, pnl_info['pnl'], pnl_info['pnl_percent'], exit_reason)
            
            return pnl_info
        except Exception as e:
            logger.error(f"POSITION_CLOSE: ERROR | {symbol} | {str(e)}")
            print(f"❌ Error closing position {symbol}: {str(e)}")
            return None
    
    def check_expiry_close(self) -> List[Dict[str, Any]]:
        """Check and close positions near expiry (configured days)"""
        closed = []
        days_to_close = OptionsTradingConfig.EXPIRY_DAYS_TO_CLOSE
        
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            
            if position.days_to_expiry() <= days_to_close:
                # Close at current market price (current premium)
                pnl = self.close_position(symbol, position.current_premium, "EXPIRY")
                if pnl:
                    closed.append(pnl)
        
        return closed
    
    def check_profit_targets(self) -> List[Dict[str, Any]]:
        """Close positions with intelligent trailing exit strategy
        
        Strategy:
        1. Lock in initial profit target (5%)
        2. Trail by configured buffer (2%) from peak
        3. Exit when price pulls back from peak
        
        This lets winners run while protecting against reversals.
        """
        closed = []
        profit_target = OptionsTradingConfig.PROFIT_TARGET_PERCENTAGE
        enable_trailing = OptionsTradingConfig.ENABLE_TRAILING_EXIT
        trailing_buffer = OptionsTradingConfig.TRAILING_BUFFER_PERCENTAGE
        
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            
            if position.unrealized_pnl > 0:
                # Calculate current and peak profit percentages
                current_profit_pct = (position.current_premium - position.entry_premium) / position.entry_premium * 100
                peak_profit_pct = (position.highest_premium - position.entry_premium) / position.entry_premium * 100
                
                should_exit = False
                exit_reason = "PROFIT"
                
                if enable_trailing and peak_profit_pct >= profit_target:
                    # Trailing exit: exit when price pulls back from peak
                    # Current price is more than trailing_buffer% below peak
                    if current_profit_pct <= (peak_profit_pct - trailing_buffer):
                        should_exit = True
                        exit_reason = f"TRAILING_EXIT (peak={peak_profit_pct:.1f}%, current={current_profit_pct:.1f}%, buffer={trailing_buffer:.1f}%)"
                        logger.info(f"TRAILING_EXIT: {symbol} | Peak profit: {peak_profit_pct:.1f}% → Current: {current_profit_pct:.1f}% | Exiting at: ₹{position.current_premium:.2f}")
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
    
    def check_stop_losses(self) -> List[Dict[str, Any]]:
        """Close positions at stop loss levels"""
        closed = []
        sl_percent = OptionsTradingConfig.STOP_LOSS_PERCENTAGE
        max_loss = OptionsTradingConfig.MAX_LOSS_PER_TRADE
        
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            
            if position.unrealized_pnl < 0:
                loss_percent = abs((position.unrealized_pnl / (position.entry_premium * position.quantity)) * 100)
                
                if loss_percent >= sl_percent or abs(position.unrealized_pnl) >= max_loss:
                    pnl = self.close_position(symbol, position.current_premium, "LOSS")
                    if pnl:
                        closed.append(pnl)
        
        return closed
    
    def get_position_summary(self) -> Dict[str, Any]:
        """Get summary of all open positions"""
        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        total_quantity = sum(p.quantity for p in self.positions.values())
        
        # Portfolio Greeks
        portfolio_delta = sum(p.current_greeks.get('delta', 0) * p.quantity for p in self.positions.values())
        portfolio_gamma = sum(p.current_greeks.get('gamma', 0) * p.quantity for p in self.positions.values())
        portfolio_theta = sum(p.current_greeks.get('theta', 0) * p.quantity for p in self.positions.values())
        
        return {
            'open_positions': len(self.positions),
            'total_quantity': total_quantity,
            'total_unrealized_pnl': total_unrealized,
            'portfolio_delta': portfolio_delta,
            'portfolio_gamma': portfolio_gamma,
            'portfolio_theta': portfolio_theta,
            'positions': [p.to_dict() for p in self.positions.values()]
        }
    
    def get_fake_move_detection_stats(self) -> Dict[str, Any]:
        """Get fake move detection statistics"""
        fake_move_detector = get_fake_move_detector()
        return fake_move_detector.get_statistics()
    
    def refresh_position_ltps(self) -> Dict[str, Any]:
        """
        Refresh LTP and Greeks for ALL open positions from broker.
        
        For paper trading (small portfolio), refresh all positions every cycle.
        This ensures position data is always current and P&L reflects market reality.
        
        Returns:
            Dictionary with refresh statistics
        """
        refresh_stats = {
            'positions_checked': 0,
            'ltps_updated': 0,
            'greeks_updated': 0,
            'failed_fetches': 0,
            'errors': [],
            'bucket_info': {}
        }
        
        if not self.broker:
            logger.warning("REFRESH_LTP: No broker available")
            return refresh_stats
        
        if not self.positions:
            return refresh_stats
        
        # Get all position symbols
        all_symbols = list(self.positions.keys())
        logger.info(f"REFRESH_LTP: Starting | positions={len(all_symbols)} | broker={self.broker is not None}")
        
        # BULK FETCH: Get LTP for all positions at once
        ltps = self.broker.get_ltp_bulk(all_symbols, exchange="NFO")
        logger.info(f"REFRESH_LTP: Bulk fetch complete | results={len([v for v in ltps.values() if v]) if ltps else 0}/{len(all_symbols)}")
        
        # Process each symbol (all positions)
        for symbol in all_symbols:
            try:
                if symbol not in self.positions:
                    continue
                
                position = self.positions[symbol]
                refresh_stats['positions_checked'] += 1
                
                # Get LTP from bulk fetch result
                current_ltp = ltps.get(symbol)
                
                if not current_ltp or current_ltp <= 0:
                    refresh_stats['failed_fetches'] += 1
                    logger.warning(f"REFRESH_LTP: Failed to fetch LTP for {symbol}")
                    continue
                
                # STEP 2: Fetch real Greeks and IV from option chain
                # Use fallback defaults
                real_greeks = {
                    'delta': 0.5,
                    'gamma': 0.05,
                    'theta': -0.02,
                    'vega': 0.1
                }
                real_iv = 20.0
                
                try:
                    # CRITICAL: Use the last TUESDAY of the month (NSE monthly expiry)
                    from datetime import datetime as dt, timedelta
                    exp_date = dt.strptime(position.expiry, "%Y-%m-%d")
                    
                    # Find last TUESDAY of the same month
                    # Start from last day of month and walk backwards
                    if exp_date.month == 12:
                        last_day = dt(exp_date.year, 12, 31)
                    else:
                        last_day = dt(exp_date.year, exp_date.month + 1, 1) - timedelta(days=1)
                    
                    # Walk backwards to find Tuesday (weekday=1)
                    while last_day.weekday() != 1:
                        last_day -= timedelta(days=1)
                    
                    monthly_expiry_iso = last_day.strftime("%Y-%m-%d")
                    # Also convert to broker format for matching (DDMMMYYYY, e.g., 30DEC2025)
                    monthly_expiry_broker = last_day.strftime("%d%b%Y").upper()
                    
                    # If exact monthly expiry not available, find closest available expiry
                    # (instruments.json might not have the exact last Tuesday)
                    from .ce_extractor import InstrumentCEExtractor
                    ce_extractor = InstrumentCEExtractor()
                    available_expiries = set(item['expiry'] for item in ce_extractor.all_instruments 
                                            if item['name'] == position.underlying)
                    
                    # Check if monthly expiry exists (in broker format: DDMMMYYYY)
                    if available_expiries and monthly_expiry_broker not in available_expiries:
                        # Find closest available expiry to our monthly expiry
                        # Convert broker format back to ISO for comparison
                        def broker_to_iso(broker_date_str):
                            """Convert DDMMMYYYY to YYYY-MM-DD"""
                            from datetime import datetime as dt_convert
                            return dt_convert.strptime(broker_date_str, "%d%b%Y").strftime("%Y-%m-%d")
                        
                        closest = min(available_expiries, 
                                    key=lambda x: abs((dt.strptime(broker_to_iso(x), "%Y-%m-%d") - dt.strptime(monthly_expiry_iso, "%Y-%m-%d")).days))
                        logger.debug(f"REFRESH_LTP: Monthly expiry {monthly_expiry_broker} not available, using {closest}")
                        monthly_expiry_iso = broker_to_iso(closest)
                    
                    # Fetch option chain using the corrected monthly expiry (ISO format)
                    option_chain = self.broker.fetch_option_chain(
                        position.underlying,
                        monthly_expiry_iso
                    )
                    
                    if option_chain:
                        # Find contract matching this position's strike and type
                        contract = option_chain.get_contract(position.strike, position.contract_type)
                        
                        logger.debug(f"REFRESH_LTP: Chain search | {symbol} | looking for strike={position.strike} ({type(position.strike).__name__}), type={position.contract_type} | chain has {len(option_chain.contracts)} contracts")
                        
                        if contract:
                            # Use REAL Greeks from broker if available, otherwise use fallbacks
                            # Check if Greeks were actually fetched (non-zero values)
                            if (contract.delta != 0.0 or contract.gamma != 0.0 or 
                                contract.theta != 0.0 or contract.vega != 0.0):
                                # Contract has real Greeks data
                                real_greeks = {
                                    'delta': contract.delta,
                                    'gamma': contract.gamma,
                                    'theta': contract.theta,
                                    'vega': contract.vega
                                }
                                real_iv = contract.iv if contract.iv > 0 else 20.0
                                refresh_stats['greeks_updated'] += 1
                                logger.debug(f"REFRESH_LTP: GREEKS_REAL | {symbol} | delta={contract.delta:.3f} | gamma={contract.gamma:.4f} | theta={contract.theta:.4f} | vega={contract.vega:.3f} | iv={real_iv:.2f}")
                            else:
                                # Contract found but no Greeks data (AngelOne API limitation)
                                # Keep fallback Greeks already set above
                                logger.debug(f"REFRESH_LTP: GREEKS_FALLBACK | {symbol} | No real Greeks available from broker")
                            
                            # Track market liquidity data
                            position.bid_price = contract.bid if contract.bid > 0 else current_ltp
                            position.ask_price = contract.ask if contract.ask > 0 else current_ltp
                            position.volume = contract.volume
                            position.open_interest = contract.open_interest
                            
                            # Track entry IV for IV crush detection
                            if position.entry_iv is None:
                                position.entry_iv = real_iv
                        else:
                            logger.debug(f"REFRESH_LTP: Contract not found | {symbol} | strike={position.strike} | type={position.contract_type} (using fallback Greeks)")
                    else:
                        logger.debug(f"REFRESH_LTP: Option chain not available | {symbol} | expiry={monthly_expiry_iso} (using fallback Greeks)")
                        
                except Exception as chain_error:
                    logger.debug(f"REFRESH_LTP: Could not fetch real Greeks | {symbol} | {str(chain_error)} (using fallback)")
                    # Continue with fallback greeks
                
                # STEP 3: Update position with market data
                self.update_position_market_data(
                    symbol=symbol,
                    current_premium=current_ltp,
                    greeks=real_greeks,
                    iv=real_iv
                )
                
                refresh_stats['ltps_updated'] += 1
                
                logger.debug(f"REFRESH_LTP: {symbol} | ltp=₹{current_ltp:.2f} | pnl=₹{position.unrealized_pnl:.2f} | delta={real_greeks['delta']:.3f} | oi={position.open_interest}")
                
            except Exception as e:
                refresh_stats['failed_fetches'] += 1
                refresh_stats['errors'].append(f"{symbol}: {str(e)}")
                logger.error(f"REFRESH_LTP: ERROR | {symbol} | {str(e)}")
        
        # Save updated positions
        if refresh_stats['ltps_updated'] > 0:
            self._save_positions()
        
        logger.info(f"REFRESH_LTP: Complete | updated={refresh_stats['ltps_updated']}/{refresh_stats['positions_checked']} | greeks={refresh_stats['greeks_updated']} | failed={refresh_stats['failed_fetches']}")
        return refresh_stats
    
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
            'positions_monitored': len(self.positions),
            'closed_by_expiry': [],
            'closed_by_profit': [],
            'closed_by_stoploss': [],
            'ltps_refreshed': 0,
            'rate_limiter_stats': {},
            'error': None
        }
        
        try:
            # Process any rate-limited requests that were queued for retry
            if self.broker:
                self.broker.process_pending_rate_limited_requests()
            
            # CRITICAL: Refresh LTP for all positions before checking exits
            if len(self.positions) > 0:
                refresh_stats = self.refresh_position_ltps()
                monitoring_result['ltps_refreshed'] = refresh_stats['ltps_updated']
                logger.info(f"MONITORING: Refreshed LTP for {refresh_stats['ltps_updated']}/{len(self.positions)} positions")
            
            # Check and close positions by expiry
            expired = self.check_expiry_close()
            monitoring_result['closed_by_expiry'] = [p['symbol'] for p in expired]
            
            # Check and close positions by profit targets
            profit_closes = self.check_profit_targets()
            monitoring_result['closed_by_profit'] = [p['symbol'] for p in profit_closes]
            
            # Check and close positions by stop loss
            sl_closes = self.check_stop_losses()
            monitoring_result['closed_by_stoploss'] = [p['symbol'] for p in sl_closes]
            
            # Get rate limiter statistics
            if self.broker:
                monitoring_result['rate_limiter_stats'] = self.broker.get_rate_limiter_stats()
            
            total_closed = len(expired) + len(profit_closes) + len(sl_closes)
            logger.info(f"MONITORING: Checked {len(self.positions)} positions | Closed {total_closed} "
                       f"(expiry={len(expired)}, profit={len(profit_closes)}, sl={len(sl_closes)})")
            
        except Exception as e:
            monitoring_result['error'] = str(e)
            logger.error(f"MONITORING: ERROR | {str(e)}")
            print(f"❌ Error during monitoring: {str(e)}")
        
        return monitoring_result
    
    def _save_positions(self):
        """Save positions to disk"""
        try:
            if not self.positions_file.parent.exists():
                self.positions_file.parent.mkdir(parents=True, exist_ok=True)
            
            positions_list = [pos.to_dict() for pos in self.positions.values()]
            positions_list.sort(key=lambda item: item.get('entry_time', ''))
            positions_data = {
                'timestamp': datetime.now().isoformat(),
                'positions': positions_list
            }
            
            with open(self.positions_file, 'w') as f:
                json.dump(positions_data, f, indent=2)
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

            for symbol, pos_data in positions_iter:
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
                last_updated_raw = pos_data.get('last_updated')
                if last_updated_raw:
                    try:
                        position.last_updated = datetime.fromisoformat(last_updated_raw)
                    except Exception:
                        pass
                self.positions[symbol] = position
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
            
            # Add new entry
            pnl_info['closed_at'] = datetime.now().isoformat()
            history.append(pnl_info)
            
            # Save
            with open(self.pnl_history_file, 'w') as f:
                json.dump(history, f, indent=2)
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
