"""
EOD Learning Aggregator for Options Bot
Runs at 3:15 PM daily to parse all trading data and update symbol_stats.json with ML training data

Data sources:
- option_positions.json: Current open positions with entry/current/exit Greeks
- option_pnl_history.json: Closed trades with PnL and entry Greeks
- live_data.json: Summary statistics
- live_data_trades.csv: Detailed trade log

ML Features captured per symbol:
- Greeks snapshots: entry delta/gamma/theta/vega, current delta/gamma/theta/vega
- Premium movements: highest premium, premium changes over time
- IV tracking: entry IV, current IV changes
- Theta decay: theta at entry vs current
- Moneyness: distance from strike
- Expiry distance: days to expiration
- PCR data: put-call ratio analysis (if available)
- OI tracking: open interest changes
- Trade outcomes: win/loss ratio, average PnL
- Recent form: cold/neutral/hot based on last 10 trades
- Reliability score: win rate based learning
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict
import statistics

logger = logging.getLogger(__name__)


class EODLearningAggregator:
    """Aggregates all trading data for ML learning at end of day"""
    
    def __init__(self, base_path: str = "/root/santhosh/trading/options/data"):
        self.base_path = Path(base_path)
        self.positions_file = self.base_path / "option_positions.json"
        self.pnl_history_file = self.base_path / "option_pnl_history.json"
        self.live_data_file = self.base_path / "live_data.json"
        self.learning_dir = self.base_path / "learning"
        self.symbol_stats_file = self.learning_dir / "symbol_stats.json"
        self.learning_dir.mkdir(exist_ok=True)
        
        logger.info(f"EOD_LEARNING_AGGREGATOR: Initialized | Path: {self.base_path}")
    
    def load_json_file(self, filepath: Path) -> Any:
        """Safely load JSON file"""
        try:
            if filepath.exists():
                with open(filepath, 'r') as f:
                    return json.load(f)
            return None
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Failed to load {filepath} | {str(e)}")
            return None
    
    def save_json_file(self, filepath: Path, data: Any) -> bool:
        """Safely save JSON file"""
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Failed to save {filepath} | {str(e)}")
            return False
    
    def extract_base_symbol(self, option_symbol: str) -> str:
        """Extract base symbol from option contract (e.g., INFY27JAN261640CE -> INFY)"""
        # Format: INFY27JAN261640CE
        # Remove expiry date and strike/type
        for i, char in enumerate(option_symbol):
            if char.isdigit() and i > 0:
                return option_symbol[:i]
        return option_symbol
    
    def analyze_closed_trades(self) -> Dict[str, Any]:
        """Analyze closed trades from TODAY ONLY from option_pnl_history.json"""
        from datetime import datetime, timedelta
        
        pnl_history = self.load_json_file(self.pnl_history_file) or []
        
        # Only count trades from today (since market open)
        today = datetime.now().date()
        trades_by_symbol = defaultdict(list)
        today_trade_count = 0
        
        for trade in pnl_history:
            symbol = trade.get('symbol', '')
            base_symbol = self.extract_base_symbol(symbol)
            closed_at_str = trade.get('closed_at', '')
            
            # Filter to only today's trades
            try:
                closed_at = datetime.fromisoformat(closed_at_str).date()
                if closed_at != today:
                    continue  # Skip trades from previous days
                today_trade_count += 1
            except Exception:
                # If we can't parse date, skip this trade
                logger.debug(f"EOD_LEARNING_AGGREGATOR: Could not parse date for trade: {closed_at_str}")
                continue
            
            trade_data = {
                'symbol': symbol,
                'entry_premium': trade.get('entry_premium', 0),
                'exit_premium': trade.get('exit_premium', 0),
                'pnl': trade.get('pnl', 0),
                'pnl_percent': trade.get('pnl_percent', 0),
                'duration_seconds': trade.get('duration', 0),
                'exit_reason': trade.get('exit_reason', 'UNKNOWN'),
                'closed_at': trade.get('closed_at', ''),
                'quantity': trade.get('quantity', 1),
                'entry_greeks': trade.get('entry_greeks', {}),  # If available from new implementation
            }
            
            trades_by_symbol[base_symbol].append(trade_data)
        
        logger.info(f"EOD_LEARNING_AGGREGATOR: Analyzed closed trades | "
                   f"total_history={len(pnl_history)} | today_only={today_trade_count} | "
                   f"unique_symbols_today={len(trades_by_symbol)}")
        
        return dict(trades_by_symbol)
    
    def analyze_open_positions(self) -> Dict[str, Any]:
        """Analyze all open positions from option_positions.json"""
        positions_data = self.load_json_file(self.positions_file) or {}
        positions = positions_data.get('positions', [])
        
        positions_by_symbol = defaultdict(list)
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            underlying = pos.get('underlying', '')
            
            position_data = {
                'symbol': symbol,
                'underlying': underlying,
                'strike': pos.get('strike', 0),
                'expiry': pos.get('expiry', ''),
                'contract_type': pos.get('contract_type', ''),
                'quantity': pos.get('quantity', 0),
                'entry_premium': pos.get('entry_premium', 0),
                'current_premium': pos.get('current_premium', 0),
                'highest_premium': pos.get('highest_premium', 0),
                'unrealized_pnl': pos.get('unrealized_pnl', 0),
                'days_to_expiry': pos.get('days_to_expiry', 0),
                'entry_time': pos.get('entry_time', ''),
                'last_updated': pos.get('last_updated', ''),
                'entry_greeks': pos.get('entry_greeks', {}),
                'current_greeks': pos.get('current_greeks', {}),
                'entry_iv': pos.get('entry_iv', 0),
                'current_iv': pos.get('current_iv', 0),
                'sl_order_price': pos.get('sl_order_price', 0),
                'underlying_alert_price': pos.get('underlying_alert_price', 0),
            }
            
            positions_by_symbol[underlying].append(position_data)
        
        logger.info(f"EOD_LEARNING_AGGREGATOR: Analyzed {len(positions)} open positions | "
                   f"{len(positions_by_symbol)} unique underlying symbols")
        
        return dict(positions_by_symbol)
    
    def calculate_greeks_stats(self, greeks_list: List[Dict[str, float]]) -> Dict[str, float]:
        """Calculate aggregate statistics from a list of Greeks dictionaries"""
        if not greeks_list:
            return {}
        
        stats = {}
        greek_keys = ['delta', 'gamma', 'theta', 'vega', 'iv']
        
        for key in greek_keys:
            values = [g.get(key, 0) for g in greeks_list if key in g]
            if values:
                stats[f'{key}_avg'] = statistics.mean(values)
                stats[f'{key}_min'] = min(values)
                stats[f'{key}_max'] = max(values)
                if len(values) > 1:
                    stats[f'{key}_stddev'] = statistics.stdev(values)
        
        return stats
    
    def build_symbol_stats(self, closed_trades: Dict[str, List[Any]], 
                          open_positions: Dict[str, List[Any]]) -> Dict[str, Any]:
        """Build comprehensive symbol statistics for ML training"""
        from optcode.optconfig import OptionsTradingConfig
        
        # Get all symbols from FO_UNIVERSE (217 symbols)
        # Ensure every symbol is present, even if no trades today
        all_fo_symbols = set(OptionsTradingConfig.FO_UNIVERSE)
        
        # Also include any open positions or trades (shouldn't add new symbols, but be safe)
        all_symbols = all_fo_symbols | set(closed_trades.keys()) | set(open_positions.keys())
        
        symbol_stats = {}
        
        for symbol in all_symbols:
            trades = closed_trades.get(symbol, [])
            positions = open_positions.get(symbol, [])
            
            # Calculate trade statistics
            wins = [t for t in trades if t['pnl'] > 0]
            losses = [t for t in trades if t['pnl'] < 0]
            breakevens = [t for t in trades if t['pnl'] == 0]
            
            total_trades = len(trades)
            total_profit = sum(t['pnl'] for t in trades)
            
            # Recent form: analyze last 10 trades
            recent_trades = trades[-10:]
            recent_wins = len([t for t in recent_trades if t['pnl'] > 0])
            recent_form = 'hot' if recent_wins >= 7 else ('cold' if recent_wins <= 3 else 'neutral')
            
            # Greeks analysis from closed trades
            entry_greeks_list = [t['entry_greeks'] for t in trades if t.get('entry_greeks')]
            entry_greeks_stats = self.calculate_greeks_stats(entry_greeks_list)
            
            # Greeks analysis from open positions
            current_greeks_list = [p['current_greeks'] for p in positions if p.get('current_greeks')]
            current_greeks_stats = self.calculate_greeks_stats(current_greeks_list)
            
            # Premium movement analysis
            premium_changes = [
                (t['exit_premium'] - t['entry_premium']) / t['entry_premium'] * 100
                for t in trades if t['entry_premium'] > 0
            ]
            
            # IV analysis
            entry_ivs = [t['entry_greeks'].get('iv', 0) for t in trades if t.get('entry_greeks')]
            current_ivs = [p['current_iv'] for p in positions]
            
            # Expiry distance distribution
            expiry_distances = [p['days_to_expiry'] for p in positions]
            
            # Moneyness analysis (strike vs underlying alert price)
            moneyness_values = []
            for p in positions:
                if p['underlying_alert_price'] > 0:
                    moneyness = (p['strike'] - p['underlying_alert_price']) / p['underlying_alert_price']
                    moneyness_values.append(moneyness)
            
            # Duration analysis
            durations = [t['duration_seconds'] for t in trades if t['duration_seconds'] > 0]
            
            # Reliability score: based on win rate and consistency
            if total_trades > 0:
                win_rate = len(wins) / total_trades
                if win_rate >= 0.6:
                    reliability_score = 0.8
                elif win_rate >= 0.5:
                    reliability_score = 0.6
                elif win_rate >= 0.4:
                    reliability_score = 0.4
                else:
                    reliability_score = 0.2
            else:
                reliability_score = 0.5  # Default for new symbols
            
            # Build symbol record
            symbol_record = {
                'symbol': symbol,
                'last_updated': datetime.now().isoformat(),
                
                # Trade statistics
                'total_trades': total_trades,
                'wins': len(wins),
                'losses': len(losses),
                'breakevens': len(breakevens),
                'total_profit': total_profit,
                'avg_profit_per_trade': total_profit / total_trades if total_trades > 0 else 0,
                'avg_win': statistics.mean([t['pnl'] for t in wins]) if wins else 0,
                'avg_loss': statistics.mean([t['pnl'] for t in losses]) if losses else 0,
                'win_rate': len(wins) / total_trades if total_trades > 0 else 0,
                'win_rate_last_10': recent_wins / len(recent_trades) if recent_trades else 0,
                'recent_form': recent_form,
                'reliability_score': reliability_score,
                'confidence_multiplier': 1.0 + (reliability_score - 0.5),  # 0.5 to 1.5 range
                
                # Greeks analysis
                'entry_greeks_stats': entry_greeks_stats,
                'current_greeks_stats': current_greeks_stats,
                
                # Premium movement analysis
                'premium_changes_percent': {
                    'avg': statistics.mean(premium_changes) if premium_changes else 0,
                    'min': min(premium_changes) if premium_changes else 0,
                    'max': max(premium_changes) if premium_changes else 0,
                },
                
                # IV analysis
                'iv_analysis': {
                    'entry_iv_avg': statistics.mean(entry_ivs) if entry_ivs else 0,
                    'current_iv_avg': statistics.mean(current_ivs) if current_ivs else 0,
                    'iv_change': statistics.mean(current_ivs) - statistics.mean(entry_ivs) 
                                if (entry_ivs and current_ivs) else 0,
                },
                
                # Expiry analysis
                'expiry_analysis': {
                    'avg_days_to_expiry': statistics.mean(expiry_distances) if expiry_distances else 0,
                    'min_days': min(expiry_distances) if expiry_distances else 0,
                    'max_days': max(expiry_distances) if expiry_distances else 0,
                    'open_positions': len(positions),
                },
                
                # Moneyness analysis
                'moneyness_analysis': {
                    'avg_moneyness': statistics.mean(moneyness_values) if moneyness_values else 0,
                    'otm_ratio': len([m for m in moneyness_values if m < 0]) / len(moneyness_values) 
                                if moneyness_values else 0,
                },
                
                # Duration analysis
                'duration_analysis': {
                    'avg_duration_seconds': statistics.mean(durations) if durations else 0,
                    'avg_duration_minutes': statistics.mean(durations) / 60 if durations else 0,
                    'min_duration_seconds': min(durations) if durations else 0,
                    'max_duration_seconds': max(durations) if durations else 0,
                },
                
                # Exit reasons distribution
                'exit_reasons': self._count_exit_reasons(trades),
                
                # Trade history for training data
                'trade_history': [
                    {
                        'date': t['closed_at'],
                        'won': t['pnl'] > 0,
                        'pnl': t['pnl'],
                        'pnl_percent': t['pnl_percent'],
                        'entry_premium': t['entry_premium'],
                        'exit_premium': t['exit_premium'],
                        'highest_premium': t.get('highest_premium', t['exit_premium']),
                        'exit_reason': t['exit_reason'],
                        'duration_seconds': t['duration_seconds'],
                        'entry_greeks': t.get('entry_greeks', {}),
                        'quantity': t['quantity'],
                    }
                    for t in trades[-100:]  # Keep last 100 trades for training
                ],
            }
            
            symbol_stats[symbol] = symbol_record
        
        logger.info(f"EOD_LEARNING_AGGREGATOR: Built stats for {len(symbol_stats)} symbols")
        return symbol_stats
    
    def _count_exit_reasons(self, trades: List[Any]) -> Dict[str, int]:
        """Count exit reasons distribution"""
        reasons = defaultdict(int)
        for trade in trades:
            reason = trade.get('exit_reason', 'UNKNOWN')
            reasons[reason] += 1
        return dict(reasons)
    
    def archive_learning_files(self) -> bool:
        """Archive learning source files to prevent duplicate learning
        
        Moves option_pnl_history.json and option_positions.json to data/archive/
        with date-stamped filenames to preserve history and prevent re-learning
        
        Returns:
            True if archival successful, False otherwise
        """
        import shutil
        
        archive_dir = self.base_path / "archive"
        archive_dir.mkdir(exist_ok=True)
        
        today = datetime.now().date().isoformat()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        try:
            # Archive option_pnl_history.json
            if self.pnl_history_file.exists():
                archive_pnl = archive_dir / f"option_pnl_history_{today}_{timestamp}.json"
                shutil.copy2(self.pnl_history_file, archive_pnl)
                logger.info(f"EOD_LEARNING_AGGREGATOR: Archived pnl_history to {archive_pnl.name}")
            
            # Archive option_positions.json
            if self.positions_file.exists():
                archive_pos = archive_dir / f"option_positions_{today}_{timestamp}.json"
                shutil.copy2(self.positions_file, archive_pos)
                logger.info(f"EOD_LEARNING_AGGREGATOR: Archived positions to {archive_pos.name}")
            
            # Clear the source files for next trading day (reset to empty state)
            # option_pnl_history.json stays as is (it's cumulative)
            # option_positions.json gets cleared for new day
            empty_positions = {
                "timestamp": datetime.now().isoformat(),
                "positions": []
            }
            with open(self.positions_file, 'w') as f:
                json.dump(empty_positions, f, indent=2)
            logger.info("EOD_LEARNING_AGGREGATOR: Cleared option_positions.json for next trading day")
            
            return True
            
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Failed to archive learning files | {str(e)}")
            return False
    
    def reset_live_data_files(self) -> bool:
        """Reset live_data files to clean state for next trading day
        
        Clears:
        - live_data.json (resets to empty summary)
        - live_data_trades.csv (clears trade table)
        - live_data_tables.md (clears markdown table)
        - option_positions.json (already done in archive_learning_files, but ensure clean)
        
        Returns:
            True if reset successful, False otherwise
        """
        try:
            # Reset live_data.json to empty summary
            empty_live_data = {
                'timestamp': datetime.now().isoformat(),
                'trading_mode': getattr(self, 'trading_mode', 'PAPER'),
                'market_status': 'OPEN',
                'summary': {
                    'total_budget': 0.0,
                    'budget_used': 0.0,
                    'budget_remaining': 0.0,
                    'budget_used_percent': 0.0,
                    'max_positions_allowed': 100,
                    'total_trades_today': 0,
                    'ongoing_trades': 0,
                    'closed_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'win_rate_percent': 0.0,
                    'total_pnl': 0.0,
                    'unrealized_pnl': 0.0,
                    'realized_pnl': 0.0,
                }
            }
            
            live_data_file = self.base_path / 'live_data.json'
            with open(live_data_file, 'w') as f:
                json.dump(empty_live_data, f, indent=2)
            logger.info("EOD_LEARNING_AGGREGATOR: Reset live_data.json for next trading day")
            
            # Reset live_data_trades.csv
            csv_file = self.base_path / 'live_data_trades.csv'
            empty_csv = "Status,Timestamp\nRESET,{}".format(datetime.now().isoformat())
            with open(csv_file, 'w') as f:
                f.write(empty_csv)
            logger.info("EOD_LEARNING_AGGREGATOR: Reset live_data_trades.csv for next trading day")
            
            # Reset live_data_tables.md
            md_file = self.base_path / 'live_data_tables.md'
            empty_md = """# Live Trading Data

**Last Updated**: {}
**Status**: Reset for next trading day

No trades yet.
""".format(datetime.now().isoformat())
            with open(md_file, 'w') as f:
                f.write(empty_md)
            logger.info("EOD_LEARNING_AGGREGATOR: Reset live_data_tables.md for next trading day")
            
            return True
            
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Failed to reset live_data files | {str(e)}")
            return False
    
    def aggregate(self) -> Dict[str, Any]:
        """Main aggregation function - runs the complete EOD learning update"""
        logger.info("EOD_LEARNING_AGGREGATOR: Starting end-of-day aggregation")
        
        try:
            # Step 1: Analyze closed trades
            closed_trades = self.analyze_closed_trades()
            
            # Step 2: Analyze open positions
            open_positions = self.analyze_open_positions()
            
            # Step 3: Build symbol statistics
            symbol_stats = self.build_symbol_stats(closed_trades, open_positions)
            
            # Step 4: Save to symbol_stats.json
            success = self.save_json_file(self.symbol_stats_file, symbol_stats)
            
            if success:
                logger.info(f"EOD_LEARNING_AGGREGATOR: Successfully saved {len(symbol_stats)} "
                           f"symbol stats to {self.symbol_stats_file}")
                
                # Step 5: Archive learning source files to prevent duplicate learning
                self.archive_learning_files()
                
                # Step 6: Reset live_data files for next trading day
                self.reset_live_data_files()
                
                return {
                    'status': 'success',
                    'symbols_processed': len(symbol_stats),
                    'closed_trades_analyzed': sum(len(trades) for trades in closed_trades.values()),
                    'open_positions_analyzed': sum(len(positions) for positions in open_positions.values()),
                    'timestamp': datetime.now().isoformat(),
                    'files_archived': True,
                    'live_data_reset': True,
                }
            else:
                logger.error("EOD_LEARNING_AGGREGATOR: Failed to save symbol stats")
                return {
                    'status': 'failed',
                    'error': 'Failed to save symbol stats',
                }
        
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Aggregation failed | {str(e)}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e),
            }


def run_eod_learning():
    """Execute end-of-day learning aggregation"""
    aggregator = EODLearningAggregator()
    return aggregator.aggregate()


if __name__ == "__main__":
    # For testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    result = run_eod_learning()
    print(json.dumps(result, indent=2))
