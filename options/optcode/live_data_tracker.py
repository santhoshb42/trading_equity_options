"""
Real-Time Live Trading Data Tracker

Maintains live_data.json with:
1. Summary statistics (budget, trades, PNL, etc.)
2. Individual trade details with all current metrics
3. Auto-updated every monitoring cycle

Output: /root/santhosh/trading/options/data/live_data.json
"""

import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path

from .optconfig import BASE_DIR, OptionsCapitalConfig, OptionsTradingConfig
from .optlogging import logger

# =============================================================================
# Live Data Tracker
# =============================================================================

class LiveDataTracker:
    """
    Real-time trading statistics tracker
    
    Maintains live_data.json with:
    - Daily trading summary (budget, trades, PNL)
    - Individual trade details (entry, current, exit info)
    - Updated every monitoring cycle
    """
    
    def __init__(self):
        # BASE_DIR is typically /root/santhosh/trading, options/data is inside it
        self.data_dir = Path(BASE_DIR).parent / 'santhosh' / 'trading' / 'options' / 'data' \
                        if 'optcode' in str(BASE_DIR) \
                        else Path(BASE_DIR) / 'options' / 'data'
        # Simpler approach: just use the known structure
        self.data_dir = Path('/root/santhosh/trading/options/data')
        self.live_data_file = self.data_dir / 'live_data.json'
        self.trading_mode = getattr(OptionsTradingConfig, 'TRADING_MODE', 'PAPER')
        self.max_daily_budget = getattr(OptionsCapitalConfig, 'MAX_DAILY_BUDGET', 100000)
        self.max_positions = getattr(OptionsTradingConfig, 'MAX_POSITIONS', 3)
        
        # Initialize live data structure
        self.live_data = {
            'timestamp': datetime.now().isoformat(),
            'trading_mode': self.trading_mode,
            'market_status': 'OPEN',  # OPEN, CLOSED
            'summary': {
                'total_budget': 0,
                'budget_used': 0,
                'budget_remaining': 0,
                'budget_used_percent': 0.0,
                'max_positions_allowed': self.max_positions,
                'total_trades_today': 0,
                'ongoing_trades': 0,
                'closed_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate_percent': 0.0,
                'total_pnl': 0.0,
                'total_pnl_percent': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'largest_win': 0.0,
                'largest_loss': 0.0,
                'best_trade_symbol': '',
                'worst_trade_symbol': '',
            },
            'trades': []  # List of individual trade details
        }
        
        # Load existing data from file if it exists
        if self.live_data_file.exists():
            try:
                with open(self.live_data_file, 'r') as f:
                    existing_data = json.load(f)
                    # Preserve existing trades
                    if 'trades' in existing_data:
                        self.live_data['trades'] = existing_data['trades']
                    # Preserve summary if available
                    if 'summary' in existing_data:
                        self.live_data['summary'] = existing_data['summary']
                    logger.info(f"LIVE_DATA_TRACKER: Loaded {len(self.live_data['trades'])} existing trades from file")
            except Exception as e:
                logger.warning(f"LIVE_DATA_TRACKER: Failed to load existing data | {str(e)}")
        
        logger.info("LIVE_DATA_TRACKER: INITIALIZED")
    
    def update_summary(self,
                      total_budget: float,
                      budget_used: float,
                      max_positions: int,
                      total_trades: int,
                      ongoing_count: int,
                      closed_count: int,
                      winning_count: int,
                      losing_count: int,
                      total_pnl: float,
                      avg_win: float = 0.0,
                      avg_loss: float = 0.0,
                      largest_win: float = 0.0,
                      largest_loss: float = 0.0,
                      market_status: str = 'OPEN') -> None:
        """
        Update summary statistics
        
        Args:
            total_budget: Total daily budget allocated
            budget_used: Budget consumed by ongoing trades
            max_positions: Max positions allowed
            total_trades: Total trades executed today
            ongoing_count: Currently open positions
            closed_count: Closed positions today
            winning_count: Winning trades count
            losing_count: Losing trades count
            total_pnl: Total P&L across all trades
            avg_win: Average win amount
            avg_loss: Average loss amount
            largest_win: Largest winning trade
            largest_loss: Largest losing trade
            market_status: OPEN or CLOSED
        """
        summary = self.live_data['summary']
        
        # Budget tracking
        summary['total_budget'] = total_budget
        summary['budget_used'] = budget_used
        summary['budget_remaining'] = max(0, total_budget - budget_used)
        summary['budget_used_percent'] = (budget_used / total_budget * 100) if total_budget > 0 else 0.0
        
        # Position tracking
        summary['max_positions_allowed'] = max_positions
        summary['total_trades_today'] = total_trades
        summary['ongoing_trades'] = ongoing_count
        summary['closed_trades'] = closed_count
        
        # Win/loss tracking
        summary['winning_trades'] = winning_count
        summary['losing_trades'] = losing_count
        win_rate = (winning_count / total_trades * 100) if total_trades > 0 else 0.0
        summary['win_rate_percent'] = round(win_rate, 2)
        
        # PNL tracking
        summary['total_pnl'] = round(total_pnl, 2)
        pnl_percent = (total_pnl / total_budget * 100) if total_budget > 0 else 0.0
        summary['total_pnl_percent'] = round(pnl_percent, 2)
        summary['avg_win'] = round(avg_win, 2)
        summary['avg_loss'] = round(avg_loss, 2)
        summary['largest_win'] = round(largest_win, 2)
        summary['largest_loss'] = round(largest_loss, 2)
        
        # Market status
        self.live_data['market_status'] = market_status
        self.live_data['timestamp'] = datetime.now().isoformat()
    
    def add_trade(self,
                 symbol: str,
                 underlying: str,
                 strike: float,
                 contract_type: str,  # CE or PE
                 action: str,  # BUY or SELL
                 quantity: int,
                 entry_time: str,
                 entry_premium: float,
                 entry_greeks: Optional[Dict[str, float]] = None,
                 entry_iv: float = 0.0,
                 underlying_alert_price: Optional[float] = None,
                 trade_id: Optional[str] = None) -> None:
        """
        Add or update a trade in live data
        
        Args:
            symbol: Full symbol (e.g., BANKNIFTY25DEC1900CE)
            underlying: Underlying asset (e.g., BANKNIFTY)
            strike: Strike price
            contract_type: CE or PE
            action: BUY or SELL
            quantity: Lot size
            entry_time: Entry time (ISO format)
            entry_premium: Premium paid at entry
            entry_greeks: Greeks dict {delta, gamma, theta, vega}
            entry_iv: IV at entry
            underlying_alert_price: Alert price that triggered trade
            trade_id: Unique trade identifier
        """
        trade_record = {
            'trade_id': trade_id or f"{symbol}_{entry_time}",
            'symbol': symbol,
            'underlying': underlying,
            'strike': round(strike, 2),
            'contract_type': contract_type,
            'action': action,
            'quantity': quantity,
            'entry_time': entry_time,
            'entry_premium': round(entry_premium, 2),
            'entry_value': round(entry_premium * quantity, 2),
            'entry_greeks': entry_greeks or {},
            'entry_iv': round(entry_iv, 2),
            'underlying_alert_price': round(underlying_alert_price, 2) if underlying_alert_price else None,
            'current_premium': entry_premium,  # Will be updated
            'current_value': round(entry_premium * quantity, 2),
            'current_greeks': entry_greeks or {},
            'current_iv': entry_iv,
            'highest_premium': entry_premium,  # For trailing exit tracking
            'unrealized_pnl': 0.0,
            'unrealized_pnl_percent': 0.0,
            'exit_time': None,
            'exit_premium': None,
            'exit_value': None,
            'exit_reason': None,  # PROFIT, LOSS, TIME, MANUAL, EXPIRY
            'exit_greeks': None,
            'exit_iv': None,
            'realized_pnl': None,
            'realized_pnl_percent': None,
            'duration_seconds': 0,
            'duration_formatted': '',
            'status': 'OPEN'  # OPEN, CLOSED
        }
        
        self.live_data['trades'].append(trade_record)
    
    def update_trade(self,
                    symbol: str,
                    current_premium: float,
                    current_greeks: Optional[Dict[str, float]] = None,
                    current_iv: float = 0.0,
                    highest_premium: Optional[float] = None,
                    quantity: int = 1) -> None:
        """
        Update current market data for an open trade
        
        Args:
            symbol: Full symbol of the position
            current_premium: Current market premium
            current_greeks: Current Greeks {delta, gamma, theta, vega}
            current_iv: Current IV
            highest_premium: Highest premium seen (for trailing SL)
            quantity: Quantity (for unrealized PNL)
        """
        # Find the trade
        for trade in self.live_data['trades']:
            if trade['symbol'] == symbol and trade['status'] == 'OPEN':
                # Update current market data
                trade['current_premium'] = round(current_premium, 2)
                trade['current_value'] = round(current_premium * quantity, 2)
                trade['current_greeks'] = current_greeks or trade['current_greeks']
                trade['current_iv'] = round(current_iv, 2)
                
                if highest_premium:
                    trade['highest_premium'] = max(trade['highest_premium'], round(highest_premium, 2))
                
                # Calculate unrealized PNL
                premium_diff = current_premium - trade['entry_premium']
                
                # For BUY: profit if current > entry
                # For SELL: profit if current < entry
                if trade['action'] == 'BUY':
                    unrealized_pnl = premium_diff * quantity
                else:  # SELL
                    unrealized_pnl = -premium_diff * quantity
                
                trade['unrealized_pnl'] = round(unrealized_pnl, 2)
                
                # Calculate unrealized PNL percent
                entry_value = trade['entry_value']
                if entry_value != 0:
                    pnl_percent = (unrealized_pnl / entry_value) * 100
                    trade['unrealized_pnl_percent'] = round(pnl_percent, 2)
                
                break
    
    def close_trade(self,
                   symbol: str,
                   exit_time: str,
                   exit_premium: float,
                   exit_reason: str,
                   exit_greeks: Optional[Dict[str, float]] = None,
                   exit_iv: float = 0.0,
                   quantity: int = 1,
                   entry_premium: float = 0.0,
                   entry_time: str = '') -> None:
        """
        Close a trade and record exit details
        
        Args:
            symbol: Full symbol of the position
            exit_time: Exit time (ISO format)
            exit_premium: Exit premium
            exit_reason: Why the trade exited (PROFIT, LOSS, TIME, MANUAL, EXPIRY)
            exit_greeks: Greeks at exit
            exit_iv: IV at exit
            quantity: Quantity for PNL calculation
            entry_premium: Entry premium for PNL calculation
            entry_time: Entry time for duration calculation
        """
        # Find the trade
        for trade in self.live_data['trades']:
            if trade['symbol'] == symbol and trade['status'] == 'OPEN':
                # Record exit details
                trade['exit_time'] = exit_time
                trade['exit_premium'] = round(exit_premium, 2)
                trade['exit_value'] = round(exit_premium * quantity, 2)
                trade['exit_reason'] = exit_reason
                trade['exit_greeks'] = exit_greeks or {}
                trade['exit_iv'] = round(exit_iv, 2)
                trade['status'] = 'CLOSED'
                
                # Calculate realized PNL
                premium_diff = exit_premium - entry_premium
                if trade['action'] == 'BUY':
                    realized_pnl = premium_diff * quantity
                else:  # SELL
                    realized_pnl = -premium_diff * quantity
                
                trade['realized_pnl'] = round(realized_pnl, 2)
                
                # Calculate realized PNL percent
                entry_value = trade['entry_value']
                if entry_value != 0:
                    pnl_percent = (realized_pnl / entry_value) * 100
                    trade['realized_pnl_percent'] = round(pnl_percent, 2)
                
                # Calculate duration
                if entry_time:
                    try:
                        entry_dt = datetime.fromisoformat(entry_time)
                        exit_dt = datetime.fromisoformat(exit_time)
                        duration = (exit_dt - entry_dt).total_seconds()
                        trade['duration_seconds'] = int(duration)
                        
                        # Format duration nicely
                        mins = int(duration // 60)
                        secs = int(duration % 60)
                        trade['duration_formatted'] = f"{mins}m {secs}s"
                    except Exception:
                        pass
                
                break
    
    def save(self) -> bool:
        """
        Generate live_data.json by scraping option_positions.json and option_pnl_history.json
        
        Returns:
            True if saved successfully
        """
        try:
            # Read option_positions.json for open trades
            positions_file = self.data_dir / 'option_positions.json'
            open_positions = []
            if positions_file.exists():
                with open(positions_file, 'r') as f:
                    pos_data = json.load(f)
                    positions_raw = pos_data.get('positions', [])
                    # Handle both dict and list formats
                    if isinstance(positions_raw, dict):
                        open_positions = list(positions_raw.values())
                    else:
                        open_positions = positions_raw
            
            # Read option_pnl_history.json for closed trades
            pnl_file = self.data_dir / 'option_pnl_history.json'
            closed_trades = []
            if pnl_file.exists():
                with open(pnl_file, 'r') as f:
                    pnl_data = json.load(f)
                    # Handle both list and dict with 'trades' key
                    if isinstance(pnl_data, list):
                        closed_trades = pnl_data
                    else:
                        closed_trades = pnl_data.get('trades', [])
            
            # Calculate summary
            ongoing_count = len(open_positions)
            ongoing_budget = sum(p.get('entry_premium', 0) * p.get('quantity', 0) for p in open_positions)
            total_unrealized_pnl = sum(p.get('unrealized_pnl', 0) for p in open_positions)
            
            # Closed trades stats (today only)
            today = datetime.now().date().isoformat()
            # Check both 'closed_at' and 'exit_time' fields
            today_closed = [t for t in closed_trades if (t.get('closed_at', '') or t.get('exit_time', '')).startswith(today)]
            closed_count = len(today_closed)
            winning_trades = len([t for t in today_closed if t.get('pnl', 0) > 0])
            losing_trades = len([t for t in today_closed if t.get('pnl', 0) < 0])
            total_realized_pnl = sum(t.get('pnl', 0) for t in today_closed)
            
            # Budget used: ongoing positions + closed trades (today)
            closed_budget = sum(t.get('entry_premium_total', t.get('entry_premium', 0) * t.get('quantity', 0)) for t in today_closed)
            budget_used = ongoing_budget + closed_budget
            
            # Win rate
            win_rate = (winning_trades / closed_count * 100) if closed_count > 0 else 0.0
            
            # Create summary
            output_data = {
                'timestamp': datetime.now().isoformat(),
                'trading_mode': self.trading_mode,
                'market_status': 'OPEN',
                'summary': {
                    'total_budget': OptionsCapitalConfig.MAX_CAPITAL,
                    'budget_used': round(budget_used, 2),
                    'budget_remaining': round(OptionsCapitalConfig.MAX_CAPITAL - budget_used, 2),
                    'budget_used_percent': round((budget_used / OptionsCapitalConfig.MAX_CAPITAL * 100) if OptionsCapitalConfig.MAX_CAPITAL > 0 else 0, 2),
                    'max_positions_allowed': OptionsCapitalConfig.MAX_SLOTS,
                    'total_trades_today': ongoing_count + closed_count,
                    'ongoing_trades': ongoing_count,
                    'closed_trades': closed_count,
                    'winning_trades': winning_trades,
                    'losing_trades': losing_trades,
                    'win_rate_percent': round(win_rate, 2),
                    'total_pnl': round(total_unrealized_pnl + total_realized_pnl, 2),
                    'unrealized_pnl': round(total_unrealized_pnl, 2),
                    'realized_pnl': round(total_realized_pnl, 2),
                }
            }
            
            logger.debug(f"LIVE_DATA_TRACKER: Scraped | ongoing={ongoing_count} | closed={closed_count} | pnl=₹{output_data['summary']['total_pnl']:.2f}")
            
            # Write JSON to file
            with open(self.live_data_file, 'w') as f:
                json.dump(output_data, f, indent=2)
            
            return True
            
        except Exception as e:
            logger.error(f"LIVE_DATA_TRACKER: SAVE_ERROR | {str(e)}")
            return False
    
    def get_live_data(self) -> Dict[str, Any]:
        """Get current live data snapshot"""
        return self.live_data.copy()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics only"""
        return self.live_data['summary'].copy()
    
    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all open trades"""
        return [t for t in self.live_data['trades'] if t['status'] == 'OPEN']
    
    def get_closed_trades(self) -> List[Dict[str, Any]]:
        """Get all closed trades"""
        return [t for t in self.live_data['trades'] if t['status'] == 'CLOSED']
    
    def clear_daily_data(self) -> None:
        """Clear daily data for new trading day"""
        self.live_data['trades'] = []
        self.live_data['summary'] = {
            'total_budget': 0,
            'budget_used': 0,
            'budget_remaining': 0,
            'budget_used_percent': 0.0,
            'max_positions_allowed': self.max_positions,
            'total_trades_today': 0,
            'ongoing_trades': 0,
            'closed_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate_percent': 0.0,
            'total_pnl': 0.0,
            'total_pnl_percent': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'largest_win': 0.0,
            'largest_loss': 0.0,
            'best_trade_symbol': '',
            'worst_trade_symbol': '',
        }
        logger.info("LIVE_DATA_TRACKER: DAILY_DATA_CLEARED")


# Global instance
_live_data_tracker = None

def get_live_data_tracker() -> LiveDataTracker:
    """Get or create global live data tracker instance"""
    global _live_data_tracker
    if _live_data_tracker is None:
        _live_data_tracker = LiveDataTracker()
    return _live_data_tracker
