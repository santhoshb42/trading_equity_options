"""
Missed Trade Logger and Paper Trading Simulator

Logs alerts that were rejected/missed (rate limit, capital unavailable, etc.)
At EOD, fetches current LTP and simulates what would have happened if the trade executed.
Uses this as paper trading data for learning purposes.

Flow:
1. Alert rejected → log_missed_alert() stores it
2. 3:30 PM EOD → fetch LTP for all missed trades
3. Calculate P&L (entry_price vs LTP)
4. Ingest as paper trades for learning (marked is_paper=True)
"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import defaultdict


class MissedTradeLogger:
    """
    Logs missed trading opportunities for paper trading simulation
    """
    
    def __init__(self, data_dir: str = "data"):
        """
        Initialize missed trade logger
        
        Args:
            data_dir: Directory to store missed trades log
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.missed_trades_file = self.data_dir / "missed_trades.jsonl"
        
        # In-memory cache for today's missed trades
        self.today_missed = []
    
    def log_missed_alert(self, symbol: str, action: str, entry_price: float,
                        reason: str, timestamp: Optional[datetime] = None,
                        quantity: int = 1, alert_data: Optional[Dict] = None) -> None:
        """
        Log a missed trading opportunity
        
        Args:
            symbol: Stock symbol (e.g., 'HDFC-EQ')
            action: 'BUY' or 'SELL'
            entry_price: Price at which trade would have executed
            reason: Why it was missed (e.g., 'RATE_LIMITED', 'CAPITAL_UNAVAILABLE', 'SLOT_FULL')
            timestamp: When the alert arrived (default: now)
            quantity: Quantity that would have been traded
            alert_data: Original alert data for reference
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        missed_trade = {
            'symbol': symbol.strip(),
            'action': action.strip(),
            'entry_price': float(entry_price),
            'quantity': int(quantity),
            'reason': reason.strip(),
            'timestamp': timestamp.isoformat(),
            'date': timestamp.date().isoformat(),
            'alert_data': alert_data or {}
        }
        
        # Store in memory for today
        self.today_missed.append(missed_trade)
        
        # Also append to log file (for historical record)
        try:
            with open(self.missed_trades_file, 'a') as f:
                f.write(json.dumps(missed_trade) + '\n')
        except Exception as e:
            print(f"Warning: Could not write missed trade to log: {e}")
    
    def get_today_missed_trades(self) -> List[Dict[str, Any]]:
        """Get all missed trades from today"""
        return self.today_missed.copy()
    
    def get_missed_trades_by_symbol(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get today's missed trades grouped by symbol"""
        trades_by_symbol = defaultdict(list)
        
        for trade in self.today_missed:
            symbol = trade['symbol']
            trades_by_symbol[symbol].append(trade)
        
        return dict(trades_by_symbol)
    
    def get_missed_trade_count(self) -> int:
        """Get count of missed trades today"""
        return len(self.today_missed)
    
    def clear_today_missed(self) -> None:
        """Clear today's missed trades (usually called at EOD after ingestion)"""
        self.today_missed.clear()
    
    def get_historical_missed_trades(self, lookback_days: int = 7) -> List[Dict[str, Any]]:
        """Get historical missed trades (from JSONL log file)"""
        all_trades = []
        
        try:
            if self.missed_trades_file.exists():
                with open(self.missed_trades_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            trade = json.loads(line)
                            all_trades.append(trade)
        except Exception as e:
            print(f"Warning: Could not read historical missed trades: {e}")
        
        return all_trades


class MissedTradePaperTrader:
    """
    Simulates outcomes of missed trades using LTP at EOD
    """
    
    def __init__(self, broker=None):
        """
        Initialize paper trader
        
        Args:
            broker: AngelOne broker instance for fetching LTP
        """
        self.broker = broker
        self.ltp_cache = {}  # Cache to avoid multiple API calls
    
    def get_symbol_ltp(self, symbol: str) -> Optional[float]:
        """
        Fetch current LTP for a symbol
        
        Args:
            symbol: Stock symbol (e.g., 'HDFC-EQ')
        
        Returns:
            Last traded price or None if unavailable
        """
        # Check cache first
        if symbol in self.ltp_cache:
            return self.ltp_cache[symbol]
        
        if not self.broker:
            print(f"Warning: Broker not available, cannot fetch LTP for {symbol}")
            return None
        
        try:
            # Try to fetch LTP from broker using get_quote
            # AngelOne broker method: get_quote(symbol) returns quote dict with 'ltp' key
            quote = self.broker.get_quote(symbol)
            
            if quote and isinstance(quote, dict):
                # Handle different possible response formats
                ltp = quote.get('ltp') or quote.get('last') or quote.get('price')
                
                if ltp:
                    ltp = float(ltp)
                    self.ltp_cache[symbol] = ltp
                    return ltp
        
        except AttributeError:
            # If broker doesn't have get_quote, try alternative methods
            try:
                # Alternative: direct LTP fetch method
                if hasattr(self.broker, 'fetch_ltp'):
                    ltp = self.broker.fetch_ltp(symbol)
                    if ltp:
                        ltp = float(ltp)
                        self.ltp_cache[symbol] = ltp
                        return ltp
            except Exception:
                pass
        
        except Exception as e:
            print(f"Warning: Could not fetch LTP for {symbol}: {e}")
        
        return None
    
    def simulate_paper_trade(self, missed_trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Simulate outcome of a missed trade using EOD LTP
        
        Args:
            missed_trade: Missed trade record with symbol and entry_price
        
        Returns:
            Paper trade record with simulated outcome or None if LTP unavailable
        """
        symbol = missed_trade['symbol']
        entry_price = missed_trade['entry_price']
        action = missed_trade['action']
        quantity = missed_trade.get('quantity', 1)
        
        # Get LTP
        ltp = self.get_symbol_ltp(symbol)
        if ltp is None:
            return None
        
        # Calculate P&L
        if action == 'BUY':
            # For BUY: profit if LTP > entry_price
            pnl = (ltp - entry_price) * quantity
        elif action == 'SELL':
            # For SELL: profit if entry_price > LTP
            pnl = (entry_price - ltp) * quantity
        else:
            return None
        
        # Create paper trade record
        paper_trade = {
            'symbol': symbol,
            'entry_price': entry_price,
            'exit_price': ltp,
            'quantity': quantity,
            'action': action,
            'pnl': pnl,
            'won': pnl > 0,
            'reason': missed_trade['reason'],
            'original_alert_time': missed_trade['timestamp'],
            'eod_check_time': datetime.now().isoformat(),
            'is_paper': True,  # Mark as paper trade
            'timestamp': datetime.now().isoformat()
        }
        
        return paper_trade
    
    def simulate_batch_paper_trades(self, missed_trades: List[Dict[str, Any]]) -> tuple:
        """
        Simulate outcomes for multiple missed trades
        
        Args:
            missed_trades: List of missed trade records
        
        Returns:
            Tuple of (successful_simulations, failed_simulations)
        """
        successful = []
        failed = []
        
        for missed_trade in missed_trades:
            paper_trade = self.simulate_paper_trade(missed_trade)
            
            if paper_trade:
                successful.append(paper_trade)
            else:
                failed.append(missed_trade)
        
        return successful, failed
    
    def get_paper_trade_statistics(self, paper_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate statistics from simulated paper trades
        
        Args:
            paper_trades: List of simulated paper trades
        
        Returns:
            Statistics dictionary
        """
        if not paper_trades:
            return {
                'total': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0,
                'by_symbol': {}
            }
        
        wins = [t for t in paper_trades if t['won']]
        losses = [t for t in paper_trades if not t['won']]
        total_pnl = sum(t['pnl'] for t in paper_trades)
        
        # By symbol
        by_symbol = {}
        for trade in paper_trades:
            symbol = trade['symbol']
            if symbol not in by_symbol:
                by_symbol[symbol] = {'count': 0, 'wins': 0, 'pnl': 0}
            
            by_symbol[symbol]['count'] += 1
            by_symbol[symbol]['wins'] += 1 if trade['won'] else 0
            by_symbol[symbol]['pnl'] += trade['pnl']
        
        return {
            'total': len(paper_trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': len(wins) / len(paper_trades) * 100 if paper_trades else 0,
            'total_pnl': total_pnl,
            'avg_pnl': total_pnl / len(paper_trades) if paper_trades else 0,
            'by_symbol': by_symbol
        }


# Global instances
_missed_logger: Optional[MissedTradeLogger] = None
_paper_trader: Optional[MissedTradePaperTrader] = None


def get_missed_trade_logger(data_dir: str = "data") -> MissedTradeLogger:
    """Get or create missed trade logger instance"""
    global _missed_logger
    
    if _missed_logger is None:
        _missed_logger = MissedTradeLogger(data_dir)
    
    return _missed_logger


def get_paper_trader(broker=None) -> MissedTradePaperTrader:
    """Get or create paper trader instance"""
    global _paper_trader
    
    if _paper_trader is None:
        _paper_trader = MissedTradePaperTrader(broker)
    
    return _paper_trader


def log_missed_alert(symbol: str, action: str, entry_price: float,
                    reason: str, **kwargs) -> None:
    """Convenience function to log missed alert"""
    logger = get_missed_trade_logger()
    logger.log_missed_alert(symbol, action, entry_price, reason, **kwargs)
