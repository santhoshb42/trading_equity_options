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
                 quantity: int,  # Lot size
                 entry_premium: float,  # Entry premium paid
                 entry_time: datetime,
                 order_id: str = ""):
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
        
        # Current state
        self.current_premium = entry_premium
        self.current_greeks = {
            'delta': 0.5,
            'gamma': 0.05,
            'theta': -0.02,
            'vega': 0.1
        }
        self.current_iv = 20.0
        self.last_updated = entry_time
        
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
            'exit_premium': exit_premium,
            'quantity': self.quantity,
            'pnl': self.realized_pnl,
            'pnl_percent': (premium_difference / self.entry_premium * 100) if self.entry_premium else 0,
            'duration': (self.exit_time - self.entry_time).total_seconds(),
            'exit_reason': exit_reason
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
            'entry_time': self.entry_time.isoformat() if isinstance(self.entry_time, datetime) else self.entry_time,
            'order_id': self.order_id,
            'current_premium': self.current_premium,
            'current_greeks': self.current_greeks,
            'current_iv': self.current_iv,
            'unrealized_pnl': self.unrealized_pnl,
            'days_to_expiry': self.days_to_expiry(),
            'last_updated': self.last_updated.isoformat() if isinstance(self.last_updated, datetime) else self.last_updated
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
                    order_id: str) -> bool:
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
                order_id=order_id
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
                'order_id': order_id
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
        """Close positions at profit targets"""
        closed = []
        profit_target = OptionsTradingConfig.PROFIT_TARGET_PERCENTAGE
        
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            
            if position.unrealized_pnl > 0:
                profit_percent = (position.unrealized_pnl / (position.entry_premium * position.quantity)) * 100
                
                if profit_percent >= profit_target:
                    pnl = self.close_position(symbol, position.current_premium, "PROFIT")
                    if pnl:
                        closed.append(pnl)
        
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
            'rate_limiter_stats': {},
            'error': None
        }
        
        try:
            # Process any rate-limited requests that were queued for retry
            if self.broker:
                self.broker.process_pending_rate_limited_requests()
            
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
            
            positions_data = {
                'timestamp': datetime.now().isoformat(),
                'positions': {sym: pos.to_dict() for sym, pos in self.positions.items()}
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
            
            for symbol, pos_data in data.get('positions', {}).items():
                position = OptionPosition(
                    symbol=symbol,
                    underlying=pos_data['underlying'],
                    strike=pos_data['strike'],
                    expiry=pos_data['expiry'],
                    contract_type=pos_data['contract_type'],
                    action=pos_data['action'],
                    quantity=pos_data['quantity'],
                    entry_premium=pos_data['entry_premium'],
                    entry_time=datetime.fromisoformat(pos_data['entry_time']),
                    order_id=pos_data.get('order_id', '')
                )
                position.current_premium = pos_data.get('current_premium', pos_data['entry_premium'])
                position.current_greeks = pos_data.get('current_greeks', {})
                position.current_iv = pos_data.get('current_iv', 20.0)
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
