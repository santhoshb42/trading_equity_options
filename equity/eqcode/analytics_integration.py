"""
Analytics Integration

Integrates the analytics system with the main trading bot.
Automatically tracks trades and provides real-time insights.
"""

import json
from datetime import datetime
from typing import Dict, Optional

from .analytics.target_analytics import TargetAnalytics
from .bot_logging import log_event


class AnalyticsIntegration:
    """
    Integrates analytics tracking with the trading system
    """
    
    def __init__(self):
        self.analytics = TargetAnalytics()
        self.enabled = True
    
    def track_order_execution(self, order_data: Dict) -> Optional[Dict]:
        """
        Track when an order gets executed (entry or exit)
        
        Args:
            order_data: {
                'symbol': str,
                'action': 'BUY'|'SELL',
                'price': float,
                'quantity': int,
                'order_type': 'MARKET'|'LIMIT',
                'timestamp': str,
                'order_id': str
            }
        
        Returns:
            Analysis if this was a trade completion, None if just an entry
        """
        if not self.enabled:
            return None
        
        try:
            # Check if this is a trade completion (sell order)
            if order_data.get('action') == 'SELL':
                # Look for matching entry order
                entry_data = self._find_matching_entry(order_data['symbol'], order_data['timestamp'])
                
                if entry_data:
                    # This is a trade completion
                    trade_data = {
                        'symbol': order_data['symbol'],
                        'entry_price': entry_data['price'],
                        'exit_price': order_data['price'],
                        'quantity': order_data['quantity'],
                        'margin_used': entry_data['price'] * order_data['quantity'],  # Simplified
                        'entry_time': entry_data['timestamp'],
                        'exit_time': order_data['timestamp'],
                        'exit_reason': 'MANUAL_EXIT'  # Could be enhanced with stop-loss/target info
                    }
                    
                    analysis = self.analytics.track_trade_completion(trade_data)
                    
                    log_event("ANALYTICS", f"Trade tracked: {order_data['symbol']}", **analysis)
                    
                    return analysis
            
            # For buy orders, just store for future matching
            self._store_entry_order(order_data)
            
            return None
            
        except Exception as e:
            log_event("ERROR", f"Error in analytics integration: {str(e)}")
            return None
    
    def _find_matching_entry(self, symbol: str, exit_time: str) -> Optional[Dict]:
        """Find the matching entry order for this exit"""
        try:
            # In a real implementation, this would query a database or maintain state
            # For now, we'll use a simple file-based approach
            
            from pathlib import Path
            entry_file = Path("data/pending_entries.json")
            
            if not entry_file.exists():
                return None
            
            with open(entry_file, 'r') as f:
                pending_entries = json.load(f)
            
            # Find matching entry for this symbol
            for entry_id, entry_data in pending_entries.items():
                if entry_data['symbol'] == symbol:
                    # Remove from pending and return
                    del pending_entries[entry_id]
                    
                    with open(entry_file, 'w') as f:
                        json.dump(pending_entries, f, indent=2)
                    
                    return entry_data
            
            return None
            
        except Exception:
            return None
    
    def _store_entry_order(self, order_data: Dict):
        """Store entry order for future matching with database fallback (FIX GAP-003)"""
        if order_data.get('action') != 'BUY':
            return
        
        from pathlib import Path
        from .bot_logging import log_error
        
        entry_id = f"{order_data['symbol']}_{order_data['timestamp']}"
        json_saved = False
        db_saved = False
        
        # Try JSON storage first
        try:
            entry_file = Path("data/pending_entries.json")
            entry_file.parent.mkdir(exist_ok=True)
            
            # Load existing entries
            pending_entries = {}
            if entry_file.exists():
                try:
                    with open(entry_file, 'r') as f:
                        pending_entries = json.load(f)
                except json.JSONDecodeError:
                    log_event("WARNING", "Corrupted pending_entries.json, starting fresh")
                    pending_entries = {}
            
            # Add this entry
            pending_entries[entry_id] = order_data
            
            # Save back
            with open(entry_file, 'w') as f:
                json.dump(pending_entries, f, indent=2)
            
            json_saved = True
            log_event("ENTRY_STORED", f"BUY entry stored for {order_data['symbol']}", 
                     entry_id=entry_id, storage="json")
            
        except Exception as json_error:
            log_event("WARNING", f"Failed to store entry in JSON for {order_data['symbol']}: {json_error}")
            json_saved = False
        
        # Always try database backup (FIX GAP-003)
        try:
            from .state_recovery import state_manager
            
            state_manager.save_position({
                'symbol': order_data['symbol'],
                'action': 'BUY',
                'entry_price': order_data.get('price', 0),
                'quantity': order_data.get('quantity', 0),
                'status': 'PENDING_ENTRY',
                'timestamp': order_data.get('timestamp', datetime.now().isoformat()),
                'order_id': order_data.get('order_id', entry_id)
            })
            
            db_saved = True
            log_event("ENTRY_STORED", f"BUY entry stored in database for {order_data['symbol']}", 
                     entry_id=entry_id, storage="database")
            
        except Exception as db_error:
            log_event("WARNING", f"Failed to store entry in database for {order_data['symbol']}: {db_error}")
            db_saved = False
        
        # Critical: Log if both storages failed
        if not json_saved and not db_saved:
            log_error("CRITICAL_ACTION_LOSS", 
                     f"Failed to store BUY entry for {order_data['symbol']} - action will be lost on crash",
                     Exception("Both JSON and database storage failed"),
                     recovery_action="Action tracking compromised",
                     context={
                        "symbol": order_data['symbol'],
                        "order_id": order_data.get('order_id'),
                        "timestamp": order_data.get('timestamp')
                     })
    
    def update_portfolio_state(self, positions: list, margin_utilized: float):
        """Update real-time portfolio utilization"""
        if not self.enabled:
            return
        
        try:
            import sqlite3
            
            active_positions = len(positions)
            max_positions = self.analytics.max_positions
            available_slots = max_positions - active_positions
            margin_efficiency = (margin_utilized / (self.analytics.capital_per_trade * active_positions)) * 100 if active_positions > 0 else 0
            
            # Calculate target progress for today
            dashboard = self.analytics.get_daily_dashboard()
            target_progress = dashboard['performance']['achievement_percent']
            
            with sqlite3.connect(self.analytics.db_file) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO portfolio_utilization 
                    (timestamp, active_positions, margin_utilized, margin_efficiency, 
                     available_slots, target_progress)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(),
                    active_positions,
                    margin_utilized,
                    margin_efficiency,
                    available_slots,
                    target_progress
                ))
                
        except Exception as e:
            log_event("ERROR", f"Error updating portfolio state: {str(e)}")
    
    def get_trading_recommendations(self) -> Dict:
        """Get AI recommendations for current trading situation"""
        if not self.enabled:
            return {"recommendations": []}
        
        try:
            return self.analytics.get_recommendations()
        except Exception as e:
            log_event("ERROR", f"Error getting recommendations: {str(e)}")
            return {"error": str(e)}
    
    def get_symbol_priority(self, symbols: list) -> list:
        """
        Rank symbols by performance priority for trading
        
        Args:
            symbols: List of symbols to rank
            
        Returns:
            List of symbols ranked by priority (highest first)
        """
        if not self.enabled:
            return symbols
        
        try:
            import sqlite3
            
            symbol_scores = {}
            
            with sqlite3.connect(self.analytics.db_file) as conn:
                for symbol in symbols:
                    cursor = conn.execute("""
                        SELECT target_achievement_rate, avg_profit_percent, total_trades
                        FROM symbol_performance 
                        WHERE symbol = ?
                    """, (symbol,))
                    
                    result = cursor.fetchone()
                    if result:
                        achievement_rate, avg_profit, total_trades = result
                        
                        # Adjust scoring based on statistical significance for intraday trading
                        if total_trades < 10:
                            # Insufficient data - use neutral score with slight penalty
                            score = 45  # Slightly below neutral
                        elif total_trades < 20:
                            # Early data - use cautious scoring
                            score = (achievement_rate * 0.4) + (avg_profit * 20 * 0.4) + (total_trades * 0.2)
                        elif total_trades < 30:
                            # Medium confidence - standard scoring
                            score = (achievement_rate * 0.5) + (avg_profit * 25 * 0.4) + (min(total_trades, 30) * 0.1)
                        else:
                            # High confidence - full weight scoring
                            score = (achievement_rate * 0.6) + (avg_profit * 40 * 0.3) + (min(total_trades, 50) * 0.1)
                    else:
                        # New symbol gets neutral score
                        score = 50
                    
                    symbol_scores[symbol] = score
            
            # Sort by score (highest first)
            ranked_symbols = sorted(symbols, key=lambda s: symbol_scores.get(s, 50), reverse=True)
            
            # Add debug info about ranking rationale
            log_event("ANALYTICS", f"Symbol ranking completed", 
                     ranked_order=[f"{s}({symbol_scores.get(s, 50):.1f})" for s in ranked_symbols[:3]])
            
            return ranked_symbols
            
        except Exception as e:
            log_event("ERROR", f"Error ranking symbols: {str(e)}")
            return symbols
    
    def should_take_trade(self, symbol: str, expected_profit_percent: float) -> tuple[bool, str]:
        """
        Decide if a trade should be taken based on analytics
        
        Args:
            symbol: Symbol to trade
            expected_profit_percent: Expected profit percentage
            
        Returns:
            (should_take, reason)
        """
        if not self.enabled:
            return True, "Analytics disabled"
        
        try:
            import sqlite3
            
            with sqlite3.connect(self.analytics.db_file) as conn:
                # Get symbol performance
                cursor = conn.execute("""
                    SELECT target_achievement_rate, avg_profit_percent, total_trades
                    FROM symbol_performance 
                    WHERE symbol = ?
                """, (symbol,))
                
                result = cursor.fetchone()
                
                if result:
                    achievement_rate, avg_profit, total_trades = result
                    
                    # For intraday trading, be very cautious about early judgments
                    if total_trades < 5:
                        # Very early data - allow trading but with neutral stance
                        return True, f"Early data ({total_trades} trades) - neutral recommendation"
                    
                    elif total_trades < 15:
                        # Some data available - use conservative thresholds
                        if achievement_rate < 20 and avg_profit < -0.5:
                            return False, f"Early negative pattern: {achievement_rate:.1f}% success, {avg_profit:.2f}% avg"
                        else:
                            return True, f"Early data acceptable: {achievement_rate:.1f}% success over {total_trades} trades"
                    
                    else:
                        # Sufficient data for stronger recommendations (15+ trades)
                        if achievement_rate < 30 and total_trades >= 20:
                            return False, f"Poor performance: {achievement_rate:.1f}% success rate over {total_trades} trades"
                        
                        if avg_profit < -0.3 and total_trades >= 15:
                            return False, f"Negative returns: {avg_profit:.2f}% average over {total_trades} trades"
                
                # Check daily progress - be more selective if behind target
                dashboard = self.analytics.get_daily_dashboard()
                achievement_percent = dashboard['performance']['achievement_percent']
                
                if achievement_percent < 50 and datetime.now().hour >= 14:  # After 2 PM
                    # Be more selective when behind schedule
                    if expected_profit_percent < 1.5:
                        return False, "Behind target - need higher profit potential"
                
                return True, "Analytics approved"
                
        except Exception as e:
            log_event("ERROR", f"Error in trade decision: {str(e)}")
            return True, "Analytics error - defaulting to allow"


# Global analytics integration instance
analytics_integration = AnalyticsIntegration()