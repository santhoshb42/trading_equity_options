"""
Real Trade Data Parser

Extracts closed trades from CSV trade logs and feeds them to the learning engine.
This bridges the gap between trade execution logs and ML learning.

Usage:
    from eqcode.real_trade_parser import parse_daily_trades, get_today_trades
    
    # Get today's closed trades
    trades = get_today_trades()  # Returns list of closed trades
    
    # Or parse specific date
    trades = parse_daily_trades('2025-11-20')
"""

import csv
import json
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional


class RealTradeParser:
    """Parse real trades from CSV logs"""
    
    def __init__(self, logs_dir: str = "logs"):
        """
        Initialize parser
        
        Args:
            logs_dir: Path to logs directory (default: logs/)
        """
        self.logs_dir = Path(logs_dir)
    
    def parse_trades_csv(self, csv_path: str) -> List[Dict[str, Any]]:
        """
        Parse trades from a CSV file
        
        Expected columns:
        - date, time, symbol, action, quantity, entry_price, exit_price, 
          capital_used, sl_price, pnl, status
        
        Args:
            csv_path: Path to trades.csv file
        
        Returns:
            List of trade dictionaries (closed trades only)
        """
        trades = []
        
        try:
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    # Only include CLOSED trades
                    if row.get('status', '').strip() != 'CLOSED':
                        continue
                    
                    try:
                        trade = {
                            'date': row.get('date', '').strip(),
                            'time': row.get('time', '').strip(),
                            'symbol': row.get('symbol', '').strip(),
                            'action': row.get('action', '').strip(),
                            'quantity': int(row.get('quantity', 0)),
                            'entry_price': float(row.get('entry_price', 0)),
                            'exit_price': float(row.get('exit_price', 0)),
                            'pnl': float(row.get('pnl', 0)),
                            'status': row.get('status', '').strip(),
                            'capital_used': float(row.get('capital_used', 0)),
                            'sl_price': float(row.get('sl_price', 0))
                        }
                        
                        # Build timestamp
                        try:
                            timestamp = datetime.strptime(
                                f"{trade['date']} {trade['time']}", 
                                "%Y-%m-%d %H:%M:%S"
                            )
                            trade['timestamp'] = timestamp.isoformat()
                        except ValueError:
                            trade['timestamp'] = datetime.now().isoformat()
                        
                        # Validate essential fields
                        if trade['symbol'] and trade['pnl'] is not None:
                            trades.append(trade)
                    
                    except (ValueError, KeyError) as e:
                        print(f"Warning: Could not parse trade row: {row}, error: {e}")
                        continue
        
        except FileNotFoundError:
            print(f"Trade log not found: {csv_path}")
            return []
        
        except Exception as e:
            print(f"Error parsing trade CSV {csv_path}: {e}")
            return []
        
        return trades
    
    def get_daily_trades(self, target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all closed trades for a specific date
        
        Args:
            target_date: Date string in format YYYY-MM-DD (default: today)
        
        Returns:
            List of closed trades for that date
        """
        if target_date is None:
            target_date = date.today().strftime('%d-%m-%Y')
        
        # Parse date components
        parts = target_date.split('-')
        if len(parts) == 3:
            if parts[0].isdigit() and len(parts[0]) == 4:
                # YYYY-MM-DD format
                year, month, day = parts[0], parts[1], parts[2]
            else:
                # DD-MM-YYYY format
                day, month, year = parts[0], parts[1], parts[2]
        else:
            print(f"Invalid date format: {target_date}")
            return []
        
        # Use standardized ISO format (YYYY-MM-DD) for all log directories
        ymd_format = f"{year}-{month}-{day}"
        
        csv_path = self.logs_dir / ymd_format / "trades.csv"
        
        if csv_path.exists():
            return self.parse_trades_csv(str(csv_path))
        
        print(f"No trade logs found for {target_date} (looked in {ymd_format})")
        return []
    
    def get_today_trades(self) -> List[Dict[str, Any]]:
        """Get all closed trades for today"""
        # Use consistent YYYY-MM-DD format across all modules
        today_iso = date.today().strftime('%Y-%m-%d')
        
        trades = []
        
        # Check ISO format (standardized across all modules)
        iso_path = self.logs_dir / today_iso / "trades.csv"
        if iso_path.exists():
            trades.extend(self.parse_trades_csv(str(iso_path)))
        
        return trades
    
    def get_all_recent_trades(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get all closed trades from the last N days
        
        Args:
            days: Number of days to look back (default: 7)
        
        Returns:
            List of all closed trades from last N days
        """
        all_trades = []
        
        for i in range(days):
            target_date = date.today() - __import__('datetime').timedelta(days=i)
            # Use consistent YYYY-MM-DD format
            target_str = target_date.strftime('%Y-%m-%d')
            
            trades = self.get_daily_trades(target_str)
            all_trades.extend(trades)
        
        return all_trades
    
    def get_statistics(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate statistics from trades
        
        Args:
            trades: List of trade dictionaries
        
        Returns:
            Statistics dictionary
        """
        if not trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0,
                'max_profit': 0.0,
                'max_loss': 0.0
            }
        
        winning = [t for t in trades if t['pnl'] > 0]
        losing = [t for t in trades if t['pnl'] <= 0]
        
        total_pnl = sum(t['pnl'] for t in trades)
        
        return {
            'total_trades': len(trades),
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': len(winning) / len(trades) * 100 if trades else 0,
            'total_pnl': total_pnl,
            'avg_pnl': total_pnl / len(trades) if trades else 0,
            'max_profit': max([t['pnl'] for t in winning], default=0),
            'max_loss': min([t['pnl'] for t in losing], default=0),
            'by_symbol': self._aggregate_by_symbol(trades)
        }
    
    def _aggregate_by_symbol(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Aggregate statistics by symbol"""
        symbols = {}
        
        for trade in trades:
            symbol = trade['symbol']
            
            if symbol not in symbols:
                symbols[symbol] = {
                    'trades': [],
                    'total_pnl': 0,
                    'count': 0,
                    'wins': 0,
                    'losses': 0
                }
            
            symbols[symbol]['trades'].append(trade)
            symbols[symbol]['total_pnl'] += trade['pnl']
            symbols[symbol]['count'] += 1
            
            if trade['pnl'] > 0:
                symbols[symbol]['wins'] += 1
            else:
                symbols[symbol]['losses'] += 1
        
        # Calculate win rates
        for symbol, data in symbols.items():
            data['win_rate'] = data['wins'] / data['count'] * 100 if data['count'] > 0 else 0
            data['avg_pnl'] = data['total_pnl'] / data['count'] if data['count'] > 0 else 0
        
        return symbols


# Global parser instance
_parser_instance: Optional[RealTradeParser] = None


def get_parser(logs_dir: str = "logs") -> RealTradeParser:
    """Get or create parser instance"""
    global _parser_instance
    
    if _parser_instance is None:
        _parser_instance = RealTradeParser(logs_dir)
    
    return _parser_instance


def get_today_trades() -> List[Dict[str, Any]]:
    """Get all closed trades for today"""
    parser = get_parser()
    return parser.get_today_trades()


def parse_daily_trades(date_str: str) -> List[Dict[str, Any]]:
    """Parse trades for a specific date (YYYY-MM-DD or DD-MM-YYYY)"""
    parser = get_parser()
    return parser.get_daily_trades(date_str)


def get_trade_statistics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Get statistics from trades"""
    parser = get_parser()
    return parser.get_statistics(trades)
