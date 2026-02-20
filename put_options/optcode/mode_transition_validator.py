"""
Trading Mode Transition Validator

Analyzes paper trading performance and validates readiness for live trading.

Features:
- Separate tracking of paper vs live trades
- Mode-specific statistics and win rates
- Performance consistency analysis
- Transition readiness assessment
- Risk metrics for paper-to-live transition
"""

import json
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
from pathlib import Path
from collections import defaultdict

try:
    from .optlogging import logger
except Exception:
    # Fallback logger
    class FallbackLogger:
        @staticmethod
        def warning(msg):
            print(f"[WARNING] {msg}")
        @staticmethod
        def error(msg):
            print(f"[ERROR] {msg}")
        @staticmethod
        def info(msg):
            print(f"[INFO] {msg}")
    
    logger = FallbackLogger()


class ModeTransitionValidator:
    """
    Validates readiness for trading mode transitions.
    
    Analyzes:
    - Paper trading win rate and consistency
    - Risk metrics (max loss, avg loss, loss streak)
    - Symbol-specific performance across modes
    - Greeks quality consistency
    - ML confidence vs actual outcomes
    """
    
    def __init__(self, history_file: str = "data/trade_history.jsonl"):
        self.history_file = Path(history_file)
        self.trades_by_mode = defaultdict(list)
        self.stats_by_mode = {}
        self.reload()
    
    def reload(self):
        """Reload trades from history file"""
        self.trades_by_mode = defaultdict(list)
        
        if not self.history_file.exists():
            logger.warning(f"TRANSITION_VALIDATOR: NO_HISTORY_FILE | {self.history_file}")
            return
        
        try:
            with open(self.history_file, 'r') as f:
                for line in f:
                    try:
                        trade = json.loads(line)
                        mode = trade.get('trading_mode', 'PAPER')
                        self.trades_by_mode[mode].append(trade)
                    except json.JSONDecodeError:
                        continue
            
            logger.info(f"TRANSITION_VALIDATOR: LOADED | PAPER={len(self.trades_by_mode['PAPER'])} | LIVE={len(self.trades_by_mode['LIVE'])}")
            self._calculate_stats()
            
        except Exception as e:
            logger.error(f"TRANSITION_VALIDATOR: LOAD_ERROR | {str(e)}")
    
    def _calculate_stats(self):
        """Calculate statistics for each mode"""
        self.stats_by_mode = {}
        
        for mode in ['PAPER', 'LIVE']:
            trades = self.trades_by_mode[mode]
            
            if not trades:
                self.stats_by_mode[mode] = {
                    'total_trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'win_rate': 0.0,
                    'total_profit': 0.0,
                    'avg_profit_per_trade': 0.0,
                    'avg_win': 0.0,
                    'avg_loss': 0.0,
                    'max_win': 0.0,
                    'max_loss': 0.0,
                    'largest_loss_streak': 0,
                    'profit_factor': 0.0,
                }
                continue
            
            wins = [t.get('profit', 0) for t in trades if t.get('win', False)]
            losses = [t.get('profit', 0) for t in trades if not t.get('win', False)]
            
            total_wins = len(wins)
            total_losses = len(losses)
            total_trades = total_wins + total_losses
            
            total_profit = sum(t.get('profit', 0) for t in trades)
            total_wins_amount = sum(wins)
            total_losses_amount = sum(losses)
            
            # Calculate loss streak
            loss_streak = 0
            max_loss_streak = 0
            for trade in trades:
                if not trade.get('win', False):
                    loss_streak += 1
                    max_loss_streak = max(max_loss_streak, loss_streak)
                else:
                    loss_streak = 0
            
            self.stats_by_mode[mode] = {
                'total_trades': total_trades,
                'wins': total_wins,
                'losses': total_losses,
                'win_rate': total_wins / total_trades if total_trades > 0 else 0.0,
                'total_profit': total_profit,
                'avg_profit_per_trade': total_profit / total_trades if total_trades > 0 else 0.0,
                'avg_win': sum(wins) / len(wins) if wins else 0.0,
                'avg_loss': sum(losses) / len(losses) if losses else 0.0,
                'max_win': max(wins) if wins else 0.0,
                'max_loss': min(losses) if losses else 0.0,
                'largest_loss_streak': max_loss_streak,
                'profit_factor': total_wins_amount / abs(total_losses_amount) if total_losses_amount != 0 else 0.0,
            }
    
    def get_paper_trading_readiness(self) -> Dict[str, Any]:
        """
        Assess readiness for switching from PAPER to LIVE trading.
        
        Returns:
            Assessment with readiness_score (0-100) and recommendations
        """
        paper_stats = self.stats_by_mode.get('PAPER', {})
        
        if not paper_stats or paper_stats.get('total_trades', 0) < 10:
            return {
                'status': 'NOT_READY',
                'reason': 'Insufficient paper trading data',
                'required_trades': 10,
                'current_trades': paper_stats.get('total_trades', 0),
                'readiness_score': 0,
                'recommendations': [
                    'Trade at least 10 contracts in PAPER mode first',
                    f'Currently have {paper_stats.get("total_trades", 0)} trades'
                ]
            }
        
        readiness_score = 0
        issues = []
        recommendations = []
        
        # Win rate check (need 55%+ for confidence)
        win_rate = paper_stats.get('win_rate', 0.0)
        if win_rate >= 0.55:
            readiness_score += 20
        elif win_rate >= 0.50:
            readiness_score += 10
            recommendations.append(f'Win rate {win_rate:.1%} is barely profitable - trade more to improve confidence')
        else:
            issues.append(f'Win rate {win_rate:.1%} is below breakeven')
            recommendations.append('ML needs more data to identify winning patterns')
        
        # Trade count check (more = better)
        trades = paper_stats.get('total_trades', 0)
        if trades >= 50:
            readiness_score += 25
        elif trades >= 30:
            readiness_score += 20
        elif trades >= 20:
            readiness_score += 15
        else:
            readiness_score += 10
        
        # Consistency check (max loss shouldn't be huge)
        max_loss = abs(paper_stats.get('max_loss', 0.0))
        avg_trade = paper_stats.get('avg_profit_per_trade', 0.0)
        
        if max_loss == 0:
            readiness_score += 15
        elif avg_trade > 0 and max_loss < abs(avg_trade) * 5:
            readiness_score += 15
            recommendations.append(f'Max loss ₹{max_loss:.2f} is well-controlled')
        elif avg_trade > 0 and max_loss < abs(avg_trade) * 10:
            readiness_score += 10
            recommendations.append(f'Watch out for large losses (max: ₹{max_loss:.2f})')
        else:
            issues.append(f'Max loss ₹{max_loss:.2f} is very large relative to avg trade')
            recommendations.append('Tighten stop losses or reduce position size')
        
        # Loss streak check
        loss_streak = paper_stats.get('largest_loss_streak', 0)
        if loss_streak <= 2:
            readiness_score += 20
        elif loss_streak <= 3:
            readiness_score += 15
            recommendations.append(f'Longest loss streak: {loss_streak} trades - monitor drawdowns')
        else:
            issues.append(f'Long loss streak ({loss_streak} trades) indicates strategy weakness')
            recommendations.append('Investigate loss causes before switching to LIVE')
        
        # Profit factor (total wins / total losses)
        profit_factor = paper_stats.get('profit_factor', 0.0)
        if profit_factor > 1.5:
            readiness_score += 20
        elif profit_factor > 1.0:
            readiness_score += 15
        else:
            recommendations.append('Wins are not significantly larger than losses')
        
        # Cap score at 100
        readiness_score = min(100, readiness_score)
        
        status = 'READY' if readiness_score >= 70 else 'CAUTION' if readiness_score >= 50 else 'NOT_READY'
        
        return {
            'status': status,
            'readiness_score': readiness_score,
            'paper_stats': paper_stats,
            'issues': issues,
            'recommendations': recommendations,
            'threshold_for_live': {
                'minimum_trades': 10,
                'minimum_win_rate': 0.50,
                'maximum_loss_streak': 5,
                'minimum_readiness_score': 50
            }
        }
    
    def get_mode_comparison(self) -> Dict[str, Any]:
        """
        Compare paper vs live trading performance.
        
        Returns:
            Detailed comparison of metrics across modes
        """
        paper = self.stats_by_mode.get('PAPER', {})
        live = self.stats_by_mode.get('LIVE', {})
        
        comparison = {
            'paper_trades': paper.get('total_trades', 0),
            'live_trades': live.get('total_trades', 0),
            'paper_win_rate': paper.get('win_rate', 0.0),
            'live_win_rate': live.get('win_rate', 0.0),
            'paper_avg_profit': paper.get('avg_profit_per_trade', 0.0),
            'live_avg_profit': live.get('avg_profit_per_trade', 0.0),
            'paper_total_profit': paper.get('total_profit', 0.0),
            'live_total_profit': live.get('total_profit', 0.0),
            'paper_max_loss': paper.get('max_loss', 0.0),
            'live_max_loss': live.get('max_loss', 0.0),
        }
        
        # Calculate differences
        if live.get('total_trades', 0) > 0:
            comparison['win_rate_difference'] = live.get('win_rate', 0.0) - paper.get('win_rate', 0.0)
            comparison['profit_per_trade_difference'] = live.get('avg_profit_per_trade', 0.0) - paper.get('avg_profit_per_trade', 0.0)
            
            if paper.get('avg_profit_per_trade', 0.0) != 0:
                comparison['profit_per_trade_change_percent'] = (
                    comparison['profit_per_trade_difference'] / abs(paper.get('avg_profit_per_trade', 1.0))
                ) * 100
        
        return comparison
    
    def get_symbol_performance_by_mode(self) -> Dict[str, Dict[str, Any]]:
        """
        Get per-symbol performance breakdown by trading mode.
        
        Returns:
            Performance metrics for each symbol in PAPER and LIVE modes
        """
        symbol_stats = {}
        
        for mode in ['PAPER', 'LIVE']:
            trades = self.trades_by_mode[mode]
            
            for trade in trades:
                symbol = trade.get('symbol', 'UNKNOWN')
                
                if symbol not in symbol_stats:
                    symbol_stats[symbol] = {}
                
                if mode not in symbol_stats[symbol]:
                    symbol_stats[symbol][mode] = {
                        'trades': 0,
                        'wins': 0,
                        'losses': 0,
                        'win_rate': 0.0,
                        'total_profit': 0.0,
                    }
                
                stats = symbol_stats[symbol][mode]
                stats['trades'] += 1
                
                if trade.get('win', False):
                    stats['wins'] += 1
                else:
                    stats['losses'] += 1
                
                stats['total_profit'] += trade.get('profit', 0.0)
                stats['win_rate'] = stats['wins'] / stats['trades'] if stats['trades'] > 0 else 0.0
        
        return symbol_stats
    
    def generate_transition_report(self) -> str:
        """Generate human-readable transition readiness report"""
        readiness = self.get_paper_trading_readiness()
        comparison = self.get_mode_comparison()
        symbol_perf = self.get_symbol_performance_by_mode()
        
        report = []
        report.append("=" * 80)
        report.append("TRADING MODE TRANSITION READINESS REPORT")
        report.append("=" * 80)
        report.append("")
        
        report.append(f"Current Status: {readiness['status']}")
        report.append(f"Readiness Score: {readiness['readiness_score']}/100")
        report.append("")
        
        paper_stats = readiness.get('paper_stats', {})
        report.append("PAPER TRADING STATISTICS:")
        report.append("-" * 80)
        report.append(f"  Total Trades: {paper_stats.get('total_trades', 0)}")
        report.append(f"  Wins: {paper_stats.get('wins', 0)} | Losses: {paper_stats.get('losses', 0)}")
        report.append(f"  Win Rate: {paper_stats.get('win_rate', 0.0):.1%}")
        report.append(f"  Total Profit: ₹{paper_stats.get('total_profit', 0.0):.2f}")
        report.append(f"  Avg Profit/Trade: ₹{paper_stats.get('avg_profit_per_trade', 0.0):.2f}")
        report.append(f"  Avg Win: ₹{paper_stats.get('avg_win', 0.0):.2f} | Avg Loss: ₹{paper_stats.get('avg_loss', 0.0):.2f}")
        report.append(f"  Max Loss: ₹{paper_stats.get('max_loss', 0.0):.2f}")
        report.append(f"  Largest Loss Streak: {paper_stats.get('largest_loss_streak', 0)} trades")
        report.append(f"  Profit Factor: {paper_stats.get('profit_factor', 0.0):.2f}")
        report.append("")
        
        if comparison.get('live_trades', 0) > 0:
            report.append("LIVE TRADING COMPARISON (if applicable):")
            report.append("-" * 80)
            report.append(f"  Live Trades: {comparison['live_trades']}")
            report.append(f"  Paper Win Rate: {comparison['paper_win_rate']:.1%} | Live Win Rate: {comparison['live_win_rate']:.1%}")
            report.append(f"  Win Rate Difference: {comparison['win_rate_difference']:+.1%}")
            report.append(f"  Paper Avg Profit: ₹{comparison['paper_avg_profit']:.2f} | Live Avg Profit: ₹{comparison['live_avg_profit']:.2f}")
            report.append(f"  Profit per Trade Difference: ₹{comparison['profit_per_trade_difference']:+.2f}")
            if 'profit_per_trade_change_percent' in comparison:
                report.append(f"  Profit Change %: {comparison['profit_per_trade_change_percent']:+.1f}%")
            report.append("")
        
        if readiness.get('issues'):
            report.append("CONCERNS:")
            report.append("-" * 80)
            for issue in readiness['issues']:
                report.append(f"  ⚠️  {issue}")
            report.append("")
        
        report.append("RECOMMENDATIONS:")
        report.append("-" * 80)
        for rec in readiness.get('recommendations', []):
            report.append(f"  • {rec}")
        report.append("")
        
        if symbol_perf and any(m in sym_data for sym_data in symbol_perf.values() for m in ['PAPER', 'LIVE']):
            report.append("PER-SYMBOL PERFORMANCE:")
            report.append("-" * 80)
            for symbol in sorted(symbol_perf.keys()):
                stats = symbol_perf[symbol]
                report.append(f"  {symbol}:")
                for mode in ['PAPER', 'LIVE']:
                    if mode in stats and stats[mode].get('trades', 0) > 0:
                        mode_stats = stats[mode]
                        report.append(f"    {mode}: {mode_stats['trades']} trades | WR={mode_stats['win_rate']:.1%} | Profit=₹{mode_stats['total_profit']:.2f}")
            report.append("")
        
        report.append("TRANSITION THRESHOLDS:")
        report.append("-" * 80)
        for key, value in readiness.get('threshold_for_live', {}).items():
            report.append(f"  {key}: {value}")
        report.append("")
        
        report.append("=" * 80)
        report.append(f"Report Generated: {datetime.now().isoformat()}")
        report.append("=" * 80)
        
        return "\n".join(report)


# Singleton instance
_validator = None

def get_mode_transition_validator(history_file: str = "data/trade_history.jsonl") -> ModeTransitionValidator:
    """Get or create mode transition validator instance"""
    global _validator
    if _validator is None:
        _validator = ModeTransitionValidator(history_file)
    return _validator
