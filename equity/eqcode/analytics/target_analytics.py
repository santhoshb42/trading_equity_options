"""
Target Analytics - 5% Daily Growth Tracker

Specialized analytics for achieving 5% daily growth on total capital
using 20% margin leverage across 5 simultaneous positions.

Strategy:
- Total Capital: ₹20,000
- Margin per trade: 20% (₹4,000)
- Max positions: 5 simultaneous
- Target per trade: 1% minimum profit
- Daily goal: 5% total capital growth (₹1,000 profit)
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from ..config import CapitalConfig, BASE_DIR
from ..bot_logging import log_event


class TargetAnalytics:
    """
    Analytics engine focused on achieving 5% daily growth target
    """
    
    def __init__(self):
        self.analytics_dir = BASE_DIR / "data" / "analytics"
        self.analytics_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_file = self.analytics_dir / "target_analytics.db"
        self.daily_file = self.analytics_dir / "daily_targets.json"
        
        self._init_database()
        
        # Target configuration (5% strategy with margin leverage)
        self.total_capital = 20000  # ₹20,000 total capital
        self.margin_per_trade = 0.20  # 20% margin per trade
        self.capital_per_trade = self.total_capital * self.margin_per_trade  # ₹4,000 per position
        self.max_positions = 5  # 5 simultaneous trades
        self.target_profit_per_trade = 0.01  # 1% minimum profit per trade
        self.daily_target_percent = 0.05  # 5% of total capital
        self.daily_target_amount = self.total_capital * self.daily_target_percent  # ₹1,000 profit
    
    def _init_database(self):
        """Initialize analytics database"""
        with sqlite3.connect(self.db_file) as conn:
            # Daily performance table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_performance (
                    date TEXT PRIMARY KEY,
                    target_amount REAL,
                    achieved_amount REAL,
                    achievement_percent REAL,
                    trades_completed INTEGER,
                    trades_profitable INTEGER,
                    avg_profit_per_trade REAL,
                    margin_efficiency REAL,
                    best_symbol TEXT,
                    worst_symbol TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Trade efficiency table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_efficiency (
                    trade_id TEXT PRIMARY KEY,
                    date TEXT,
                    symbol TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    quantity INTEGER,
                    margin_used REAL,
                    profit_amount REAL,
                    profit_percent REAL,
                    target_achieved BOOLEAN,
                    hold_duration_minutes INTEGER,
                    entry_time TEXT,
                    exit_time TEXT,
                    exit_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Portfolio utilization table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_utilization (
                    timestamp TEXT PRIMARY KEY,
                    active_positions INTEGER,
                    margin_utilized REAL,
                    margin_efficiency REAL,
                    available_slots INTEGER,
                    target_progress REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Symbol performance table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbol_performance (
                    symbol TEXT PRIMARY KEY,
                    total_trades INTEGER,
                    profitable_trades INTEGER,
                    win_rate REAL,
                    avg_profit_percent REAL,
                    avg_hold_duration INTEGER,
                    best_profit_percent REAL,
                    worst_profit_percent REAL,
                    target_achievement_rate REAL,
                    last_traded TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    
    def track_trade_completion(self, trade_data: Dict) -> Dict:
        """
        Track a completed trade and analyze against targets
        
        Args:
            trade_data: {
                'symbol': str,
                'entry_price': float,
                'exit_price': float,
                'quantity': int,
                'margin_used': float,
                'entry_time': str,
                'exit_time': str,
                'exit_reason': str
            }
        
        Returns:
            Analysis results
        """
        try:
            # Calculate trade metrics
            profit_amount = (trade_data['exit_price'] - trade_data['entry_price']) * trade_data['quantity']
            profit_percent = (profit_amount / (trade_data['entry_price'] * trade_data['quantity'])) * 100
            target_achieved = profit_percent >= (self.target_profit_per_trade * 100)
            
            # Calculate hold duration
            entry_time = datetime.fromisoformat(trade_data['entry_time'])
            exit_time = datetime.fromisoformat(trade_data['exit_time'])
            hold_duration = int((exit_time - entry_time).total_seconds() / 60)
            
            # Store trade data
            trade_id = f"{trade_data['symbol']}_{entry_time.strftime('%Y%m%d_%H%M%S')}"
            
            with sqlite3.connect(self.db_file) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO trade_efficiency 
                    (trade_id, date, symbol, entry_price, exit_price, quantity, 
                     margin_used, profit_amount, profit_percent, target_achieved,
                     hold_duration_minutes, entry_time, exit_time, exit_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade_id,
                    entry_time.strftime('%Y-%m-%d'),  # Use DD-MM-YYYY format consistently
                    trade_data['symbol'],
                    trade_data['entry_price'],
                    trade_data['exit_price'],
                    trade_data['quantity'],
                    trade_data['margin_used'],
                    profit_amount,
                    profit_percent,
                    target_achieved,
                    hold_duration,
                    trade_data['entry_time'],
                    trade_data['exit_time'],
                    trade_data['exit_reason']
                ))
            
            # Update symbol performance
            self._update_symbol_performance(trade_data['symbol'])
            
            # Update daily progress
            self._update_daily_progress()
            
            analysis = {
                'trade_id': trade_id,
                'profit_amount': profit_amount,
                'profit_percent': round(profit_percent, 2),
                'target_achieved': target_achieved,
                'target_progress': f"{profit_percent:.2f}% / {self.target_profit_per_trade * 100}%",
                'hold_duration_minutes': hold_duration,
                'margin_efficiency': round((profit_amount / trade_data['margin_used']) * 100, 2),
                'contribution_to_daily_target': round((profit_amount / self.daily_target_amount) * 100, 1)
            }
            
            log_event("TRADE_ANALYZED", f"Trade completed for {trade_data['symbol']}", **analysis)
            
            return analysis
            
        except Exception as e:
            log_event("ERROR", f"Error analyzing trade: {str(e)}")
            return {"error": str(e)}
    
    def _update_symbol_performance(self, symbol: str):
        """Update symbol performance statistics"""
        with sqlite3.connect(self.db_file) as conn:
            # Get symbol stats from trade_efficiency
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    COUNT(CASE WHEN target_achieved = 1 THEN 1 END) as profitable_trades,
                    AVG(profit_percent) as avg_profit_percent,
                    AVG(hold_duration_minutes) as avg_hold_duration,
                    MAX(profit_percent) as best_profit_percent,
                    MIN(profit_percent) as worst_profit_percent,
                    COUNT(CASE WHEN target_achieved = 1 THEN 1 END) * 100.0 / COUNT(*) as target_achievement_rate
                FROM trade_efficiency 
                WHERE symbol = ?
            """, (symbol,))
            
            stats = cursor.fetchone()
            
            if stats and stats[0] > 0:  # If we have trades for this symbol
                conn.execute("""
                    INSERT OR REPLACE INTO symbol_performance 
                    (symbol, total_trades, profitable_trades, win_rate, avg_profit_percent,
                     avg_hold_duration, best_profit_percent, worst_profit_percent, 
                     target_achievement_rate, last_traded)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    stats[0],  # total_trades
                    stats[1],  # profitable_trades
                    round((stats[1] / stats[0]) * 100, 2),  # win_rate
                    round(stats[2], 2),  # avg_profit_percent
                    int(stats[3]),  # avg_hold_duration
                    round(stats[4], 2),  # best_profit_percent
                    round(stats[5], 2),  # worst_profit_percent
                    round(stats[6], 2),  # target_achievement_rate
                    datetime.now().strftime('%Y-%m-%d')  # Use DD-MM-YYYY format consistently
                ))
    
    def _update_daily_progress(self):
        """Update daily progress toward 5% target"""
        today = datetime.now().strftime('%Y-%m-%d')  # Use DD-MM-YYYY format consistently
        
        with sqlite3.connect(self.db_file) as conn:
            # Get today's performance
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as trades_completed,
                    COUNT(CASE WHEN target_achieved = 1 THEN 1 END) as trades_profitable,
                    SUM(profit_amount) as total_profit,
                    AVG(profit_percent) as avg_profit_per_trade,
                    AVG(margin_used) as avg_margin_used
                FROM trade_efficiency 
                WHERE date = ?
            """, (today,))
            
            stats = cursor.fetchone()
            
            if stats and stats[0] > 0:  # If we have trades today
                total_profit = stats[2] or 0
                achievement_percent = (total_profit / self.daily_target_amount) * 100
                margin_efficiency = (total_profit / (stats[4] * stats[0])) * 100 if stats[4] else 0
                
                # Get best and worst performing symbols today
                cursor = conn.execute("""
                    SELECT symbol, profit_percent 
                    FROM trade_efficiency 
                    WHERE date = ? 
                    ORDER BY profit_percent DESC 
                    LIMIT 1
                """, (today,))
                best_symbol = cursor.fetchone()
                
                cursor = conn.execute("""
                    SELECT symbol, profit_percent 
                    FROM trade_efficiency 
                    WHERE date = ? 
                    ORDER BY profit_percent ASC 
                    LIMIT 1
                """, (today,))
                worst_symbol = cursor.fetchone()
                
                conn.execute("""
                    INSERT OR REPLACE INTO daily_performance 
                    (date, target_amount, achieved_amount, achievement_percent, 
                     trades_completed, trades_profitable, avg_profit_per_trade,
                     margin_efficiency, best_symbol, worst_symbol)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    today,
                    self.daily_target_amount,
                    total_profit,
                    round(achievement_percent, 2),
                    stats[0],
                    stats[1],
                    round(stats[3], 2),
                    round(margin_efficiency, 2),
                    best_symbol[0] if best_symbol else None,
                    worst_symbol[0] if worst_symbol else None
                ))
    
    def get_daily_dashboard(self) -> Dict:
        """Get comprehensive daily dashboard for 5% target tracking"""
        today = datetime.now().strftime('%Y-%m-%d')  # Use DD-MM-YYYY format consistently
        
        with sqlite3.connect(self.db_file) as conn:
            # Daily performance
            cursor = conn.execute("""
                SELECT * FROM daily_performance WHERE date = ?
            """, (today,))
            daily_perf = cursor.fetchone()
            
            # Current portfolio utilization
            cursor = conn.execute("""
                SELECT * FROM portfolio_utilization 
                ORDER BY timestamp DESC LIMIT 1
            """)
            portfolio = cursor.fetchone()
            
            # Top performing symbols
            cursor = conn.execute("""
                SELECT symbol, target_achievement_rate, avg_profit_percent, total_trades
                FROM symbol_performance 
                ORDER BY target_achievement_rate DESC, avg_profit_percent DESC
                LIMIT 5
            """)
            top_symbols = cursor.fetchall()
            
            # Recent trades
            cursor = conn.execute("""
                SELECT symbol, profit_percent, target_achieved, hold_duration_minutes
                FROM trade_efficiency 
                WHERE date = ?
                ORDER BY exit_time DESC
                LIMIT 10
            """, (today,))
            recent_trades = cursor.fetchall()
        
        dashboard = {
            'date': today,
            'target': {
                'daily_target_amount': self.daily_target_amount,
                'daily_target_percent': self.daily_target_percent * 100,
                'target_per_trade': self.target_profit_per_trade * 100,
                'max_positions': self.max_positions,
                'capital_per_trade': self.capital_per_trade
            },
            'performance': {
                'achieved_amount': daily_perf[2] if daily_perf else 0,
                'achievement_percent': daily_perf[3] if daily_perf else 0,
                'trades_completed': daily_perf[4] if daily_perf else 0,
                'trades_profitable': daily_perf[5] if daily_perf else 0,
                'avg_profit_per_trade': daily_perf[6] if daily_perf else 0,
                'margin_efficiency': daily_perf[7] if daily_perf else 0
            },
            'portfolio': {
                'active_positions': portfolio[1] if portfolio else 0,
                'margin_utilized': portfolio[2] if portfolio else 0,
                'available_slots': portfolio[4] if portfolio else self.max_positions,
                'utilization_percent': (portfolio[1] / self.max_positions * 100) if portfolio else 0
            },
            'top_symbols': [
                {
                    'symbol': row[0],
                    'target_achievement_rate': row[1],
                    'avg_profit_percent': row[2],
                    'total_trades': row[3]
                }
                for row in top_symbols
            ],
            'recent_trades': [
                {
                    'symbol': row[0],
                    'profit_percent': row[1],
                    'target_achieved': bool(row[2]),
                    'hold_duration_minutes': row[3]
                }
                for row in recent_trades
            ]
        }
        
        return dashboard
    
    def get_recommendations(self) -> Dict:
        """Get AI-driven recommendations for achieving 5% target"""
        today = datetime.now().strftime('%Y-%m-%d')  # Use DD-MM-YYYY format consistently
        
        with sqlite3.connect(self.db_file) as conn:
            # Current progress
            cursor = conn.execute("""
                SELECT achieved_amount, trades_completed 
                FROM daily_performance WHERE date = ?
            """, (today,))
            progress = cursor.fetchone()
            
            achieved_amount = progress[0] if progress else 0
            trades_completed = progress[1] if progress else 0
            
            remaining_target = self.daily_target_amount - achieved_amount
            remaining_trades = self.max_positions - trades_completed
            
            # Best performing symbols
            cursor = conn.execute("""
                SELECT symbol, target_achievement_rate, avg_profit_percent
                FROM symbol_performance 
                WHERE total_trades >= 3 AND target_achievement_rate > 60
                ORDER BY target_achievement_rate DESC, avg_profit_percent DESC
                LIMIT 3
            """)
            recommended_symbols = cursor.fetchall()
        
        recommendations = {
            'target_status': {
                'achieved': achieved_amount,
                'remaining': remaining_target,
                'progress_percent': (achieved_amount / self.daily_target_amount) * 100,
                'on_track': achieved_amount >= (self.daily_target_amount * 0.8)  # 80% of target
            },
            'trade_recommendations': {
                'trades_completed': trades_completed,
                'remaining_trades': remaining_trades,
                'required_profit_per_remaining_trade': remaining_target / max(remaining_trades, 1) if remaining_trades > 0 else 0,
                'recommended_symbols': [
                    {
                        'symbol': row[0],
                        'success_rate': f"{row[1]:.1f}%",
                        'avg_profit': f"{row[2]:.2f}%"
                    }
                    for row in recommended_symbols
                ]
            },
            'optimization_suggestions': []
        }
        
        # Add specific recommendations
        if achieved_amount < self.daily_target_amount * 0.5:
            recommendations['optimization_suggestions'].append(
                "Consider focusing on high-probability symbols with >70% target achievement rate"
            )
        
        if remaining_trades > 0:
            required_profit = (remaining_target / remaining_trades) / self.capital_per_trade * 100
            if required_profit > 2:
                recommendations['optimization_suggestions'].append(
                    f"Need {required_profit:.1f}% profit per remaining trade - consider higher momentum stocks"
                )
        
        return recommendations


if __name__ == "__main__":
    # Test the analytics system
    analytics = TargetAnalytics()
    print("Target Analytics initialized successfully!")
    print(f"Daily target: ₹{analytics.daily_target_amount}")
    print(f"Capital per trade: ₹{analytics.capital_per_trade}")