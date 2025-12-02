"""
DummyTradeTracker - Paper Trading for ML Learning

When signals are rejected due to insufficient capital/slots,
this module tracks hypothetical trades to generate more training data for ML models.

Key Features:
├─ Track rejected signals as dummy trades
├─ Monitor LTP every 2 seconds (same as real trades)
├─ Close on SL/target hit or market close
├─ Log outcomes to CSV for ML training
└─ No capital allocation, no broker orders

Usage:
    tracker = DummyTradeTracker()
    dummy_id = tracker.create_dummy_entry(alert, entry_price, ml_score, rejection_reason)
    # Later in position_monitor loop:
    tracker.update_dummy_price(dummy_id, current_ltp)
    # At end of day:
    dummy_trades = tracker.get_closed_trades()
"""

import csv
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
import json


class DummyTradeTracker:
    """Track hypothetical trades for ML learning from rejected signals"""
    
    def __init__(self, csv_file: str = "data/dummy_trades.csv"):
        """
        Initialize the dummy trade tracker
        
        Args:
            csv_file: Path to CSV file for logging dummy trades
        """
        self.dummy_trades: Dict[str, Dict[str, Any]] = {}
        self.csv_file = csv_file
        self.closed_trades: List[Dict[str, Any]] = []
        
        # Create CSV file if it doesn't exist
        self._initialize_csv()
        
        # Load existing dummy trades from CSV
        self._load_existing_trades()
    
    def _initialize_csv(self):
        """Initialize CSV file with headers if it doesn't exist"""
        if not os.path.exists(self.csv_file):
            # Create parent directory if needed
            Path(self.csv_file).parent.mkdir(parents=True, exist_ok=True)
            
            # Create CSV with headers
            headers = [
                'trade_id', 'symbol', 'action', 'entry_price', 'entry_time',
                'quantity', 'capital_would_use', 'rejection_reason',
                'ml_score', 'tv_score', 'exit_price', 'exit_time', 'exit_reason',
                'gross_profit', 'charges_estimate', 'net_profit', 'profit_percent',
                'outcome', 'sl_price', 'trail_sl_price', 'was_hit_by_sl',
                'was_real_trade', 'used_for_ml_training', 'ml_learned'
            ]
            
            with open(self.csv_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
    
    def _load_existing_trades(self):
        """Load existing dummy trades from CSV"""
        if os.path.exists(self.csv_file):
            try:
                with open(self.csv_file, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get('ml_learned') != 'True':  # Only load trades not yet used for ML
                            self.closed_trades.append(row)
            except Exception as e:
                print(f"Warning: Failed to load existing dummy trades: {e}")
    
    def create_dummy_entry(
        self,
        alert: Dict[str, Any],
        entry_price: float,
        ml_score: float,
        rejection_reason: str,
        quantity: int = 4
    ) -> str:
        """
        Create a dummy entry when a signal is rejected
        
        Args:
            alert: Alert data (symbol, score, etc.)
            entry_price: Current LTP at rejection time
            ml_score: ML ensemble confidence score (0-1)
            rejection_reason: Why the signal was rejected
            quantity: Shares to track (from config)
            
        Returns:
            dummy_id: Unique ID for this dummy trade
        """
        symbol = alert.get('symbol', 'UNKNOWN')
        timestamp = datetime.now()
        
        # Create unique ID
        dummy_id = f"DUMMY_{symbol}_{timestamp.strftime('%Y%m%d_%H%M%S')}"
        
        # Calculate SL and target prices
        sl_price = entry_price * 0.99  # 1% SL
        
        # Initialize dummy trade
        self.dummy_trades[dummy_id] = {
            'trade_id': dummy_id,
            'symbol': symbol,
            'action': 'BUY',
            'entry_price': float(entry_price),
            'entry_time': timestamp.isoformat(),
            'quantity': int(quantity),
            'capital_would_use': float(entry_price * quantity),
            'rejection_reason': str(rejection_reason),
            'ml_score': float(ml_score),
            'tv_score': float(alert.get('score', 0)),
            'exit_price': None,
            'exit_time': None,
            'exit_reason': None,
            'sl_price': float(sl_price),
            'trail_sl_price': None,
            'trail_activated': False,
            'peak_price': float(entry_price),
            'outcome': None,
            'gross_profit': None,
            'charges_estimate': 30,  # Estimated brokerage
            'net_profit': None,
            'profit_percent': None,
            'was_hit_by_sl': False,
            'was_real_trade': False,
            'used_for_ml_training': True,
            'ml_learned': False,
            'status': 'ACTIVE'
        }
        
        return dummy_id
    
    def update_dummy_price(self, dummy_id: str, current_price: float) -> Optional[str]:
        """
        Update dummy trade with current price (called every 2 seconds)
        
        Args:
            dummy_id: ID of dummy trade to update
            current_price: Current LTP
            
        Returns:
            exit_reason if trade should close, None otherwise
        """
        if dummy_id not in self.dummy_trades:
            return None
        
        dummy = self.dummy_trades[dummy_id]
        
        if dummy['status'] != 'ACTIVE':
            return None
        
        entry_price = dummy['entry_price']
        profit_pct = (current_price - entry_price) / entry_price
        
        # Track peak price for trailing SL
        if current_price > dummy['peak_price']:
            dummy['peak_price'] = current_price
        
        # Use epsilon for floating point comparison
        EPSILON = 1e-8
        
        # Check if target hit (+0.5% profit trigger)
        # Using >= with small epsilon to handle floating point precision
        if profit_pct >= (0.005 - EPSILON):
            return self.close_dummy_trade(dummy_id, current_price, 'Target hit (+0.5%)')
        
        # Check if SL hit (-1% hard stop)
        if current_price <= dummy['sl_price']:
            dummy['was_hit_by_sl'] = True
            return self.close_dummy_trade(dummy_id, current_price, 'SL hit (-1%)')
        
        # Activate trailing SL once profit reaches +0.5%
        if not dummy['trail_activated'] and profit_pct >= (0.005 - EPSILON):
            dummy['trail_activated'] = True
            dummy['trail_sl_price'] = current_price * 0.995  # 0.5% below peak
        
        # Check if trailing SL hit
        if dummy['trail_activated'] and current_price <= dummy['trail_sl_price']:
            return self.close_dummy_trade(dummy_id, current_price, 'Trailing SL hit (-0.5%)')
        
        return None
    
    def close_dummy_trade(
        self,
        dummy_id: str,
        exit_price: float,
        exit_reason: str
    ) -> str:
        """
        Close a dummy trade and calculate outcome
        
        Args:
            dummy_id: ID of trade to close
            exit_price: Exit price (LTP)
            exit_reason: Why trade is closing
            
        Returns:
            outcome: 'WIN' or 'LOSS'
        """
        if dummy_id not in self.dummy_trades:
            return None
        
        dummy = self.dummy_trades[dummy_id]
        
        if dummy['status'] != 'ACTIVE':
            return None
        
        # Calculate P&L
        entry_price = dummy['entry_price']
        quantity = dummy['quantity']
        
        gross_profit = (exit_price - entry_price) * quantity
        charges = dummy['charges_estimate']
        net_profit = gross_profit - charges
        profit_pct = (net_profit / (entry_price * quantity)) * 100 if entry_price > 0 else 0
        
        # Determine outcome
        outcome = 'WIN' if net_profit > 0 else 'LOSS'
        
        # Update trade
        dummy['exit_price'] = float(exit_price)
        dummy['exit_time'] = datetime.now().isoformat()
        dummy['exit_reason'] = str(exit_reason)
        dummy['gross_profit'] = float(gross_profit)
        dummy['net_profit'] = float(net_profit)
        dummy['profit_percent'] = float(profit_pct)
        dummy['outcome'] = outcome
        dummy['status'] = 'CLOSED'
        
        # Log to CSV
        self._log_to_csv(dummy)
        
        # Move to closed trades
        self.closed_trades.append(dummy.copy())
        
        return outcome
    
    def _log_to_csv(self, dummy: Dict[str, Any]):
        """Write dummy trade to CSV file"""
        try:
            with open(self.csv_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'trade_id', 'symbol', 'action', 'entry_price', 'entry_time',
                    'quantity', 'capital_would_use', 'rejection_reason',
                    'ml_score', 'tv_score', 'exit_price', 'exit_time', 'exit_reason',
                    'gross_profit', 'charges_estimate', 'net_profit', 'profit_percent',
                    'outcome', 'sl_price', 'trail_sl_price', 'was_hit_by_sl',
                    'was_real_trade', 'used_for_ml_training', 'ml_learned'
                ])
                
                # Prepare row
                row = {
                    'trade_id': dummy.get('trade_id'),
                    'symbol': dummy.get('symbol'),
                    'action': dummy.get('action'),
                    'entry_price': dummy.get('entry_price'),
                    'entry_time': dummy.get('entry_time'),
                    'quantity': dummy.get('quantity'),
                    'capital_would_use': dummy.get('capital_would_use'),
                    'rejection_reason': dummy.get('rejection_reason'),
                    'ml_score': dummy.get('ml_score'),
                    'tv_score': dummy.get('tv_score'),
                    'exit_price': dummy.get('exit_price'),
                    'exit_time': dummy.get('exit_time'),
                    'exit_reason': dummy.get('exit_reason'),
                    'gross_profit': dummy.get('gross_profit'),
                    'charges_estimate': dummy.get('charges_estimate'),
                    'net_profit': dummy.get('net_profit'),
                    'profit_percent': dummy.get('profit_percent'),
                    'outcome': dummy.get('outcome'),
                    'sl_price': dummy.get('sl_price'),
                    'trail_sl_price': dummy.get('trail_sl_price'),
                    'was_hit_by_sl': dummy.get('was_hit_by_sl', False),
                    'was_real_trade': dummy.get('was_real_trade', False),
                    'used_for_ml_training': dummy.get('used_for_ml_training', True),
                    'ml_learned': 'False'
                }
                
                writer.writerow(row)
        except Exception as e:
            print(f"Error logging dummy trade: {e}")
    
    def get_active_dummies(self) -> List[str]:
        """Get list of active dummy trade IDs"""
        return [
            dummy_id for dummy_id, dummy in self.dummy_trades.items()
            if dummy['status'] == 'ACTIVE'
        ]
    
    def get_closed_trades(self) -> List[Dict[str, Any]]:
        """Get all closed dummy trades (not yet used for ML)"""
        return [
            trade for trade in self.closed_trades
            if not trade.get('ml_learned', False)
        ]
    
    def mark_trades_as_learned(self, trade_ids: List[str]):
        """Mark dummy trades as used for ML training"""
        for trade_id in trade_ids:
            # Update in-memory
            if trade_id in self.dummy_trades:
                self.dummy_trades[trade_id]['ml_learned'] = True
            
            # Update in closed trades
            for trade in self.closed_trades:
                if trade.get('trade_id') == trade_id:
                    trade['ml_learned'] = True
            
            # Update in CSV file
            self._update_csv_trade_learned(trade_id)
    
    def _update_csv_trade_learned(self, trade_id: str):
        """Update CSV to mark trade as ML-learned"""
        try:
            # Read all trades
            trades = []
            with open(self.csv_file, 'r') as f:
                reader = csv.DictReader(f)
                trades = list(reader)
            
            # Update the trade
            for trade in trades:
                if trade.get('trade_id') == trade_id:
                    trade['ml_learned'] = 'True'
            
            # Write back
            with open(self.csv_file, 'w', newline='') as f:
                if trades:
                    fieldnames = trades[0].keys()
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(trades)
        except Exception as e:
            print(f"Error updating CSV: {e}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics on dummy trades"""
        if not self.closed_trades:
            return {
                'total_trades': 0,
                'win_trades': 0,
                'loss_trades': 0,
                'win_rate': 0,
                'total_profit': 0,
                'avg_profit_per_trade': 0,
                'avg_win_size': 0,
                'avg_loss_size': 0
            }
        
        wins = [t for t in self.closed_trades if t.get('outcome') == 'WIN']
        losses = [t for t in self.closed_trades if t.get('outcome') == 'LOSS']
        
        total_profit = sum(float(t.get('net_profit', 0)) for t in self.closed_trades)
        avg_profit_per_trade = total_profit / len(self.closed_trades) if self.closed_trades else 0
        
        avg_win_size = sum(float(t.get('net_profit', 0)) for t in wins) / len(wins) if wins else 0
        avg_loss_size = sum(float(t.get('net_profit', 0)) for t in losses) / len(losses) if losses else 0
        
        return {
            'total_trades': len(self.closed_trades),
            'win_trades': len(wins),
            'loss_trades': len(losses),
            'win_rate': (len(wins) / len(self.closed_trades) * 100) if self.closed_trades else 0,
            'total_profit': total_profit,
            'avg_profit_per_trade': avg_profit_per_trade,
            'avg_win_size': avg_win_size,
            'avg_loss_size': avg_loss_size
        }
    
    def get_dummy_trade(self, dummy_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific dummy trade"""
        return self.dummy_trades.get(dummy_id)
    
    def close_at_market_close(self, market_close_price_dict: Dict[str, float]):
        """
        Close all remaining dummy trades at market close
        
        Args:
            market_close_price_dict: Dict of {symbol: close_price}
        """
        for dummy_id, dummy in list(self.dummy_trades.items()):
            if dummy['status'] == 'ACTIVE':
                symbol = dummy['symbol']
                if symbol in market_close_price_dict:
                    close_price = market_close_price_dict[symbol]
                    self.close_dummy_trade(dummy_id, close_price, 'Market close')


# Global instance
_dummy_tracker: Optional[DummyTradeTracker] = None


def get_dummy_tracker() -> DummyTradeTracker:
    """Get or create the global dummy tracker instance"""
    global _dummy_tracker
    if _dummy_tracker is None:
        _dummy_tracker = DummyTradeTracker()
    return _dummy_tracker


def reset_dummy_tracker():
    """Reset the global dummy tracker instance"""
    global _dummy_tracker
    _dummy_tracker = None
