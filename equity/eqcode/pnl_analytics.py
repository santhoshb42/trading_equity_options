"""
PNL Analytics Module - Equity Trading Bot

Tracks and analyzes:
1. Actual trading performance and PNL
2. Missed opportunity analysis 
3. Trading statistics and performance metrics
4. Win/loss ratios and success rates

Features:
- Real-time PNL tracking
- Historical performance analysis
- Missed signal tracking
- Performance dashboards
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import threading
import os
from pathlib import Path


class PNLAnalytics:
    """PNL and trading performance analytics"""
    
    def __init__(self, db_path: str = "data/pnl_analytics.db"):
        """Initialize PNL analytics
        
        Args:
            db_path: Path to SQLite database for PNL tracking
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.lock = threading.Lock()
        self._init_database()
    
    def _init_database(self):
        """Initialize SQLite database with PNL tracking tables"""
        with sqlite3.connect(self.db_path) as conn:
            # Executed trades table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS executed_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    quantity INTEGER NOT NULL,
                    entry_time TIMESTAMP NOT NULL,
                    exit_time TIMESTAMP,
                    pnl REAL DEFAULT 0,
                    charges REAL DEFAULT 0,
                    net_pnl REAL DEFAULT 0,
                    status TEXT DEFAULT 'OPEN',
                    alert_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Missed signals table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS missed_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    signal_price REAL NOT NULL,
                    signal_time TIMESTAMP NOT NULL,
                    reason TEXT,
                    current_price REAL,
                    potential_pnl REAL DEFAULT 0,
                    alert_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Daily PNL summary table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS daily_pnl (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL UNIQUE,
                    gross_pnl REAL DEFAULT 0,
                    charges REAL DEFAULT 0,
                    net_pnl REAL DEFAULT 0,
                    trades_count INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    max_drawdown REAL DEFAULT 0,
                    capital_used REAL DEFAULT 0,
                    roi_percentage REAL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_symbol ON executed_trades(symbol)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_date ON executed_trades(entry_time)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_missed_symbol ON missed_signals(symbol)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_missed_date ON missed_signals(signal_time)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_pnl(date)')
            
            conn.commit()
    
    def log_executed_trade(self, symbol: str, action: str, entry_price: float, 
                          quantity: int, alert_data: Dict = None) -> int:
        """Log a new executed trade
        
        Args:
            symbol: Stock symbol
            action: BUY or SELL
            entry_price: Entry price
            quantity: Number of shares
            alert_data: Original alert data
            
        Returns:
            Trade ID
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.execute('''
                        INSERT INTO executed_trades 
                        (symbol, action, entry_price, quantity, entry_time, alert_data)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (symbol, action, entry_price, quantity, 
                         datetime.now(), json.dumps(alert_data) if alert_data else None))
                    
                    trade_id = cursor.lastrowid
                    conn.commit()
                    
                    return trade_id
                    
        except Exception as e:
            print(f"Error logging executed trade: {e}")
            return 0
    
    def update_trade_exit(self, trade_id: int, exit_price: float, charges: float = 0) -> bool:
        """Update trade with exit details and calculate PNL
        
        Args:
            trade_id: Trade ID to update
            exit_price: Exit price
            charges: Trading charges/fees
            
        Returns:
            Success status
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    # Get trade details
                    cursor = conn.execute('''
                        SELECT action, entry_price, quantity FROM executed_trades 
                        WHERE id = ?
                    ''', (trade_id,))
                    
                    row = cursor.fetchone()
                    if not row:
                        return False
                    
                    action, entry_price, quantity = row
                    
                    # Calculate PNL
                    if action == 'BUY':
                        pnl = (exit_price - entry_price) * quantity
                    else:  # SELL
                        pnl = (entry_price - exit_price) * quantity
                    
                    net_pnl = pnl - charges
                    
                    # Update trade
                    conn.execute('''
                        UPDATE executed_trades 
                        SET exit_price = ?, exit_time = ?, pnl = ?, charges = ?, 
                            net_pnl = ?, status = 'CLOSED'
                        WHERE id = ?
                    ''', (exit_price, datetime.now(), pnl, charges, net_pnl, trade_id))
                    
                    conn.commit()
                    
                    # Update daily summary
                    self._update_daily_summary(datetime.now().date())
                    
                    return True
                    
        except Exception as e:
            print(f"Error updating trade exit: {e}")
            return False
    
    def log_missed_signal(self, symbol: str, action: str, signal_price: float,
                         reason: str, alert_data: Dict = None) -> bool:
        """Log a missed trading signal
        
        Args:
            symbol: Stock symbol
            action: BUY or SELL
            signal_price: Price at signal time
            reason: Why signal was missed
            alert_data: Original alert data
            
        Returns:
            Success status
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute('''
                        INSERT INTO missed_signals 
                        (symbol, action, signal_price, signal_time, reason, alert_data)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (symbol, action, signal_price, datetime.now(), reason,
                         json.dumps(alert_data) if alert_data else None))
                    
                    conn.commit()
                    return True
                    
        except Exception as e:
            print(f"Error logging missed signal: {e}")
            return False
    
    def calculate_missed_opportunity_pnl(self, days_back: int = 7) -> Dict[str, Any]:
        """Calculate potential PNL from missed opportunities
        
        Args:
            days_back: Days to look back for missed signals
            
        Returns:
            Missed opportunity analysis
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days_back)
            
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    # Get missed signals
                    cursor = conn.execute('''
                        SELECT symbol, action, signal_price, signal_time, reason 
                        FROM missed_signals 
                        WHERE signal_time > ?
                        ORDER BY signal_time DESC
                    ''', (cutoff_date,))
                    
                    missed_signals = cursor.fetchall()
                    
                    total_missed_opportunities = len(missed_signals)
                    missed_by_reason = {}
                    missed_by_symbol = {}
                    
                    for symbol, action, signal_price, signal_time, reason in missed_signals:
                        # Count by reason
                        missed_by_reason[reason] = missed_by_reason.get(reason, 0) + 1
                        
                        # Count by symbol
                        missed_by_symbol[symbol] = missed_by_symbol.get(symbol, 0) + 1
                    
                    # Get actual trade count for comparison
                    cursor = conn.execute('''
                        SELECT COUNT(*) FROM executed_trades 
                        WHERE entry_time > ?
                    ''', (cutoff_date,))
                    
                    actual_trades = cursor.fetchone()[0]
                    
                    return {
                        'period_days': days_back,
                        'total_missed_opportunities': total_missed_opportunities,
                        'actual_trades_executed': actual_trades,
                        'missed_vs_executed_ratio': round(total_missed_opportunities / max(actual_trades, 1), 2),
                        'missed_by_reason': missed_by_reason,
                        'missed_by_symbol': missed_by_symbol,
                        'analysis_date': datetime.now().isoformat()
                    }
                    
        except Exception as e:
            print(f"Error calculating missed opportunity PNL: {e}")
            return {}
    
    def get_pnl_summary(self, days_back: int = 30) -> Dict[str, Any]:
        """Get comprehensive PNL summary
        
        Args:
            days_back: Days to include in summary
            
        Returns:
            PNL summary data
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days_back)
            
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    # Get closed trades
                    cursor = conn.execute('''
                        SELECT symbol, action, entry_price, exit_price, quantity, 
                               pnl, charges, net_pnl, entry_time, exit_time
                        FROM executed_trades 
                        WHERE status = 'CLOSED' AND entry_time > ?
                        ORDER BY entry_time DESC
                    ''', (cutoff_date,))
                    
                    closed_trades = cursor.fetchall()
                    
                    # Calculate summary statistics
                    total_trades = len(closed_trades)
                    if total_trades == 0:
                        return {
                            'period_days': days_back,
                            'total_trades': 0,
                            'total_pnl': 0,
                            'analysis_date': datetime.now().isoformat()
                        }
                    
                    total_pnl = sum(trade[7] for trade in closed_trades)  # net_pnl
                    winning_trades = [trade for trade in closed_trades if trade[7] > 0]
                    losing_trades = [trade for trade in closed_trades if trade[7] < 0]
                    
                    win_rate = len(winning_trades) / total_trades * 100
                    
                    # PNL by symbol
                    pnl_by_symbol = {}
                    for trade in closed_trades:
                        symbol = trade[0]
                        net_pnl = trade[7]
                        if symbol not in pnl_by_symbol:
                            pnl_by_symbol[symbol] = {'trades': 0, 'pnl': 0}
                        pnl_by_symbol[symbol]['trades'] += 1
                        pnl_by_symbol[symbol]['pnl'] += net_pnl
                    
                    # Best and worst trades
                    best_trade = max(closed_trades, key=lambda x: x[7]) if closed_trades else None
                    worst_trade = min(closed_trades, key=lambda x: x[7]) if closed_trades else None
                    
                    return {
                        'period_days': days_back,
                        'total_trades': total_trades,
                        'winning_trades': len(winning_trades),
                        'losing_trades': len(losing_trades),
                        'win_rate': round(win_rate, 2),
                        'total_pnl': round(total_pnl, 2),
                        'avg_pnl_per_trade': round(total_pnl / total_trades, 2),
                        'best_trade': {
                            'symbol': best_trade[0],
                            'pnl': round(best_trade[7], 2),
                            'date': best_trade[8]
                        } if best_trade else None,
                        'worst_trade': {
                            'symbol': worst_trade[0],
                            'pnl': round(worst_trade[7], 2),
                            'date': worst_trade[8]
                        } if worst_trade else None,
                        'pnl_by_symbol': {k: {'trades': v['trades'], 'pnl': round(v['pnl'], 2)} 
                                        for k, v in pnl_by_symbol.items()},
                        'analysis_date': datetime.now().isoformat()
                    }
                    
        except Exception as e:
            print(f"Error getting PNL summary: {e}")
            return {}
    
    def _update_daily_summary(self, trade_date):
        """Update daily PNL summary for a given date"""
        try:
            date_str = trade_date.strftime('%Y-%m-%d')
            
            with sqlite3.connect(self.db_path) as conn:
                # Calculate daily stats
                cursor = conn.execute('''
                    SELECT 
                        COUNT(*) as trades_count,
                        SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                        SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                        SUM(pnl) as gross_pnl,
                        SUM(charges) as charges,
                        SUM(net_pnl) as net_pnl
                    FROM executed_trades 
                    WHERE DATE(entry_time) = ? AND status = 'CLOSED'
                ''', (date_str,))
                
                row = cursor.fetchone()
                if row and row[0] > 0:  # trades_count > 0
                    trades_count, winning_trades, losing_trades, gross_pnl, charges, net_pnl = row
                    win_rate = (winning_trades / trades_count) * 100 if trades_count > 0 else 0
                    
                    # Insert or update daily summary
                    conn.execute('''
                        INSERT OR REPLACE INTO daily_pnl 
                        (date, gross_pnl, charges, net_pnl, trades_count, 
                         winning_trades, losing_trades, win_rate, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (date_str, gross_pnl or 0, charges or 0, net_pnl or 0,
                         trades_count, winning_trades, losing_trades, win_rate,
                         datetime.now()))
                
                conn.commit()
                
        except Exception as e:
            print(f"Error updating daily summary: {e}")
    
    def get_daily_performance(self, days_back: int = 30) -> List[Dict]:
        """Get daily performance data
        
        Args:
            days_back: Number of days to retrieve
            
        Returns:
            List of daily performance records
        """
        try:
            cutoff_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
            
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.execute('''
                        SELECT date, gross_pnl, charges, net_pnl, trades_count,
                               winning_trades, losing_trades, win_rate
                        FROM daily_pnl 
                        WHERE date > ?
                        ORDER BY date DESC
                    ''', (cutoff_date,))
                    
                    rows = cursor.fetchall()
                    
                    return [
                        {
                            'date': row[0],
                            'gross_pnl': row[1],
                            'charges': row[2],
                            'net_pnl': row[3],
                            'trades_count': row[4],
                            'winning_trades': row[5],
                            'losing_trades': row[6],
                            'win_rate': row[7]
                        }
                        for row in rows
                    ]
                    
        except Exception as e:
            print(f"Error getting daily performance: {e}")
            return []


# Global analytics instance
_pnl_analytics_instance = None

def get_pnl_analytics() -> PNLAnalytics:
    """Get global PNL analytics instance"""
    global _pnl_analytics_instance
    if _pnl_analytics_instance is None:
        _pnl_analytics_instance = PNLAnalytics()
    return _pnl_analytics_instance