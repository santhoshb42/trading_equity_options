"""
Trade logging and statistics tracking for options bot.
Logs all trades to CSV with entry/exit/PNL data for analysis.
"""

import json
import csv
import os
from datetime import datetime
from pathlib import Path
import threading
from typing import Dict, List, Optional

class TradeLogger:
    """Logs all trades to CSV and maintains statistics."""
    
    def __init__(self, base_path: str = "/root/santhosh/trading/options"):
        self.base_path = base_path
        self.data_dir = Path(base_path) / "data"
        self.logs_dir = Path(base_path) / "logs"
        
        # Create directories if not exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        
        # File paths
        self.trades_csv = self.data_dir / "trades.csv"
        self.daily_stats_log = self.logs_dir / "stats.log"
        self.trade_analysis_json = self.data_dir / "trade_analysis.json"
        
        # Column headers
        self.csv_headers = [
            'timestamp',
            'trade_id',
            'symbol',
            'action',
            'entry_time',
            'entry_premium',
            'entry_confidence',
            'entry_score',
            'entry_features',
            'sector',
            'sector_rsi',
            'sector_performance',
            'sector_participation',
            'sector_bullish',
            'exit_time',
            'exit_premium',
            'pnl',
            'pnl_percent',
            'is_win',
            'exit_reason',
            'duration_minutes',
            'max_profit',
            'max_loss',
            'status',
            'ml_prediction',
            'actual_outcome',
            'ml_accuracy'
        ]
        
        # Initialize CSV if not exists
        self._init_csv()
        
        # Lock for thread-safe operations
        self.lock = threading.Lock()
        
        # In-memory statistics
        self.session_stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'total_pnl_percent': 0.0,
            'win_rate': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'max_win': 0.0,
            'max_loss': 0.0,
            'consecutive_wins': 0,
            'consecutive_losses': 0,
            'session_start': datetime.now().isoformat()
        }
    
    def _init_csv(self):
        """Initialize CSV file with headers if not exists."""
        if not self.trades_csv.exists():
            with open(self.trades_csv, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.csv_headers)
                writer.writeheader()
            print(f"✅ Trade logger initialized: {self.trades_csv}")
    
    def log_trade_entry(self, 
                       symbol: str,
                       action: str,
                       entry_premium: float,
                       confidence: float,
                       score: float,
                       features: List[float],
                       ml_prediction: Optional[Dict] = None,
                       trade_id: Optional[str] = None,
                       sector_data: Optional[Dict] = None) -> str:
        """
        Log trade entry.
        
        Args:
            symbol: BANKNIFTY, NIFTY, FINNIFTY
            action: BUY or SELL
            entry_premium: Entry premium price
            confidence: Signal confidence (0-100)
            score: Signal score (0-100)
            features: List of 15 extracted ML features
            ml_prediction: ML model prediction dict
            trade_id: Custom trade ID or auto-generate
            sector_data: Sector strength data at entry
        
        Returns:
            trade_id: Unique identifier for this trade
        """
        with self.lock:
            if not trade_id:
                trade_id = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # Default sector data
            sector_info = sector_data or {
                'sector': 'UNKNOWN',
                'sector_rsi': None,
                'sector_performance': None,
                'sector_participation': None,
                'sector_bullish': None
            }
            
            trade_data = {
                'timestamp': datetime.now().isoformat(),
                'trade_id': trade_id,
                'symbol': symbol,
                'action': action,
                'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'entry_premium': entry_premium,
                'entry_confidence': confidence,
                'entry_score': score,
                'entry_features': json.dumps(features[:15]) if features else '[]',
                'sector': sector_info.get('sector', 'UNKNOWN'),
                'sector_rsi': sector_info.get('sector_rsi'),
                'sector_performance': sector_info.get('sector_performance'),
                'sector_participation': sector_info.get('sector_participation'),
                'sector_bullish': sector_info.get('sector_bullish'),
                'exit_time': '',
                'exit_premium': '',
                'pnl': '',
                'pnl_percent': '',
                'is_win': '',
                'exit_reason': '',
                'duration_minutes': '',
                'max_profit': '',
                'max_loss': '',
                'status': 'OPEN',
                'ml_prediction': json.dumps(ml_prediction) if ml_prediction else '',
                'actual_outcome': '',
                'ml_accuracy': ''
            }
            
            # Append to CSV
            with open(self.trades_csv, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.csv_headers)
                writer.writerow(trade_data)
            
            print(f"[TRADE_ENTRY] {trade_id} | {symbol} {action} @ {entry_premium} | Conf: {confidence}% | Sector: {sector_info.get('sector')}")
            return trade_id
    
    def log_trade_exit(self,
                      trade_id: str,
                      exit_premium: float,
                      pnl: float,
                      exit_reason: str = 'TARGET',
                      max_profit: Optional[float] = None,
                      max_loss: Optional[float] = None,
                      ml_accuracy: Optional[float] = None) -> Dict:
        """
        Log trade exit and update statistics.
        
        Args:
            trade_id: Trade ID from entry
            exit_premium: Exit premium price
            pnl: Profit/Loss in rupees
            exit_reason: TARGET, SL, REVERSION, MANUAL, TIMEOUT
            max_profit: Maximum profit during trade
            max_loss: Maximum loss during trade
            ml_accuracy: If ML prediction was correct
        
        Returns:
            Updated trade data
        """
        with self.lock:
            # Read all trades
            trades = []
            with open(self.trades_csv, 'r') as f:
                reader = csv.DictReader(f)
                trades = list(reader)
            
            # Find and update the trade
            trade_found = False
            for trade in trades:
                if trade['trade_id'] == trade_id:
                    trade_found = True
                    exit_time = datetime.now()
                    entry_time = datetime.strptime(trade['entry_time'], '%Y-%m-%d %H:%M:%S')
                    duration = (exit_time - entry_time).total_seconds() / 60
                    
                    pnl_percent = (pnl / float(trade['entry_premium'])) * 100 if float(trade['entry_premium']) > 0 else 0
                    is_win = pnl > 0
                    
                    trade.update({
                        'exit_time': exit_time.strftime('%Y-%m-%d %H:%M:%S'),
                        'exit_premium': exit_premium,
                        'pnl': pnl,
                        'pnl_percent': round(pnl_percent, 2),
                        'is_win': 'YES' if is_win else 'NO',
                        'exit_reason': exit_reason,
                        'duration_minutes': round(duration, 2),
                        'max_profit': max_profit if max_profit else '',
                        'max_loss': max_loss if max_loss else '',
                        'status': 'CLOSED',
                        'ml_accuracy': 'YES' if ml_accuracy else ('NO' if ml_accuracy is False else '')
                    })
                    
                    # Update statistics
                    self._update_stats(pnl, is_win)
                    break
            
            if not trade_found:
                print(f"⚠️  Trade {trade_id} not found in CSV")
                return {}
            
            # Write updated trades
            with open(self.trades_csv, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.csv_headers)
                writer.writeheader()
                writer.writerows(trades)
            
            print(f"[TRADE_EXIT] {trade_id} | PNL: {pnl:+.0f} ({pnl_percent:+.2f}%) | Reason: {exit_reason}")
            return trade
    
    def _update_stats(self, pnl: float, is_win: bool):
        """Update in-memory statistics."""
        self.session_stats['total_trades'] += 1
        self.session_stats['total_pnl'] += pnl
        
        if is_win:
            self.session_stats['winning_trades'] += 1
            self.session_stats['consecutive_wins'] += 1
            self.session_stats['consecutive_losses'] = 0
            if pnl > self.session_stats['max_win']:
                self.session_stats['max_win'] = pnl
            if self.session_stats['avg_win'] == 0:
                self.session_stats['avg_win'] = pnl
            else:
                self.session_stats['avg_win'] = (self.session_stats['avg_win'] + pnl) / 2
        else:
            self.session_stats['losing_trades'] += 1
            self.session_stats['consecutive_losses'] += 1
            self.session_stats['consecutive_wins'] = 0
            if pnl < self.session_stats['max_loss']:
                self.session_stats['max_loss'] = pnl
            if self.session_stats['avg_loss'] == 0:
                self.session_stats['avg_loss'] = pnl
            else:
                self.session_stats['avg_loss'] = (self.session_stats['avg_loss'] + pnl) / 2
        
        # Calculate win rate
        if self.session_stats['total_trades'] > 0:
            self.session_stats['win_rate'] = (self.session_stats['winning_trades'] / 
                                             self.session_stats['total_trades']) * 100
    
    def get_session_stats(self) -> Dict:
        """Get current session statistics."""
        with self.lock:
            return self.session_stats.copy()
    
    def get_symbol_stats(self, symbol: str) -> Dict:
        """Get statistics for specific symbol."""
        with self.lock:
            trades = self._read_trades()
            symbol_trades = [t for t in trades if t['symbol'] == symbol and t['status'] == 'CLOSED']
            
            if not symbol_trades:
                return {'symbol': symbol, 'total_trades': 0}
            
            wins = sum(1 for t in symbol_trades if t['is_win'] == 'YES')
            losses = len(symbol_trades) - wins
            total_pnl = sum(float(t['pnl']) for t in symbol_trades if t['pnl'])
            
            return {
                'symbol': symbol,
                'total_trades': len(symbol_trades),
                'wins': wins,
                'losses': losses,
                'win_rate': (wins / len(symbol_trades)) * 100 if symbol_trades else 0,
                'total_pnl': total_pnl,
                'avg_pnl': total_pnl / len(symbol_trades) if symbol_trades else 0,
                'max_win': max([float(t['pnl']) for t in symbol_trades if t['pnl']]),
                'max_loss': min([float(t['pnl']) for t in symbol_trades if t['pnl']])
            }
    
    def _read_trades(self) -> List[Dict]:
        """Read all trades from CSV."""
        if not self.trades_csv.exists():
            return []
        
        trades = []
        with open(self.trades_csv, 'r') as f:
            reader = csv.DictReader(f)
            trades = list(reader)
        return trades
    
    def get_trades(self, 
                  symbol: Optional[str] = None,
                  status: Optional[str] = None,
                  limit: int = 100) -> List[Dict]:
        """
        Get trades with optional filtering.
        
        Args:
            symbol: Filter by symbol
            status: Filter by status (OPEN, CLOSED)
            limit: Maximum trades to return
        
        Returns:
            List of trades
        """
        with self.lock:
            trades = self._read_trades()
            
            if symbol:
                trades = [t for t in trades if t['symbol'] == symbol]
            if status:
                trades = [t for t in trades if t['status'] == status]
            
            return trades[-limit:]
    
    def write_stats_log(self):
        """Write current statistics to stats.log."""
        with self.lock:
            stats = self.get_session_stats()
            
            log_entry = f"""
════════════════════════════════════════════════════════════════
  TRADING SESSION STATISTICS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
════════════════════════════════════════════════════════════════

OVERALL PERFORMANCE:
  Total Trades: {stats['total_trades']}
  Winning Trades: {stats['winning_trades']}
  Losing Trades: {stats['losing_trades']}
  Win Rate: {stats['win_rate']:.2f}%
  
PROFIT & LOSS:
  Total P&L: {stats['total_pnl']:+.0f}
  Total P&L %: {stats['total_pnl_percent']:+.2f}%
  Average Win: {stats['avg_win']:+.0f}
  Average Loss: {stats['avg_loss']:+.0f}
  Max Win: {stats['max_win']:+.0f}
  Max Loss: {stats['max_loss']:+.0f}

STREAKS:
  Consecutive Wins: {stats['consecutive_wins']}
  Consecutive Losses: {stats['consecutive_losses']}

Session Start: {stats['session_start']}

════════════════════════════════════════════════════════════════
"""
            
            with open(self.daily_stats_log, 'a') as f:
                f.write(log_entry)
            
            print(f"✅ Stats written to {self.daily_stats_log}")
    
    def export_analysis(self):
        """Export detailed trade analysis to JSON."""
        with self.lock:
            trades = self._read_trades()
            symbols = list(set(t['symbol'] for t in trades))
            
            analysis = {
                'export_timestamp': datetime.now().isoformat(),
                'session_stats': self.get_session_stats(),
                'symbol_stats': {sym: self.get_symbol_stats(sym) for sym in symbols},
                'total_trades': len(trades),
                'closed_trades': len([t for t in trades if t['status'] == 'CLOSED']),
                'open_trades': len([t for t in trades if t['status'] == 'OPEN']),
                'trades_sample': trades[-50:]  # Last 50 trades
            }
            
            with open(self.trade_analysis_json, 'w') as f:
                json.dump(analysis, f, indent=2)
            
            print(f"✅ Analysis exported to {self.trade_analysis_json}")
            return analysis


# Global instance
_trade_logger = None

def get_trade_logger(base_path: str = "/root/santhosh/trading/put_options") -> TradeLogger:
    """Get or create trade logger instance for PUT options bot."""
    global _trade_logger
    if _trade_logger is None:
        _trade_logger = TradeLogger(base_path)
    return _trade_logger

