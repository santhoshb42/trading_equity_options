"""
Options Learning Engine

Tracks symbol performance and learns from live trading results.
Enables the ML models to make better decisions over time.

Components:
1. SymbolPerformanceTracker - per-symbol win rates, recent form, reliability
2. TradeResultRecorder - logs every trade outcome for learning
3. ModelPerformanceAnalyzer - measures model accuracy vs reality
4. Daily Learning Engine - retrains models with new data
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict, deque
from pathlib import Path
import numpy as np

from .optlogging import logger, log_event

# =============================================================================
# Configuration
# =============================================================================

class LearningConfig:
    """Configuration for learning engine"""
    
    # Performance tracking
    SYMBOLS = ['BANKNIFTY', 'NIFTY', 'FINNIFTY']
    LOOKBACK_TRADES = 50  # Remember last 50 trades per symbol
    DECAY_DAYS = 30  # Time decay for older trades
    
    # Form detection
    HOT_THRESHOLD = 0.65  # 65%+ win rate = hot
    COLD_THRESHOLD = 0.35  # 35%- win rate = cold
    FORM_LOOKBACK = 10  # Last 10 trades for recent form
    
    # Reliability scoring
    MIN_TRADES_FOR_STATS = 5  # Need 5+ trades for reliability
    MAX_CONFIDENCE_BOOST = 1.3  # Max 30% confidence boost
    MIN_CONFIDENCE_REDUCTION = 0.7  # Min 30% confidence reduction
    
    # Data persistence - use learning directory as canonical location
    SYMBOL_STATS_FILE = "data/learning/symbol_stats.json"  # Canonical location: learning directory
    TRADE_HISTORY_FILE = "data/trade_history.jsonl"
    MODEL_PERFORMANCE_FILE = "data/model_performance.json"


# =============================================================================
# Symbol Performance Tracker
# =============================================================================

class SymbolPerformanceTracker:
    """
    Tracks per-symbol trading performance.
    
    Calculates:
    - Win rates (overall, last 10, recent form)
    - Reliability scores (how consistent is this symbol?)
    - Form detection (is symbol hot/cold?)
    - Confidence adjustments (should we trade it more/less?)
    """
    
    def __init__(self, symbol_stats_file: Optional[Path] = None):
        self.symbol_stats_file = Path(symbol_stats_file) if symbol_stats_file else Path(LearningConfig.SYMBOL_STATS_FILE)
        
        # Initialize with predefined symbols, but allow dynamic addition
        self.symbol_stats = {symbol: {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0.0,
            'avg_profit_per_trade': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'trade_history': deque(maxlen=LearningConfig.LOOKBACK_TRADES),
            'recent_form': 'neutral',  # 'hot', 'neutral', 'cold'
            'win_rate': 0.0,
            'win_rate_last_10': 0.0,
            'reliability_score': 0.5,
            'confidence_multiplier': 1.0,
            'last_updated': datetime.now().isoformat()
        } for symbol in LearningConfig.SYMBOLS}
        
        self._load_stats()
        logger.debug("SYMBOL_TRACKER: INITIALIZED")
    
    def record_trade(self, symbol: str, won: bool, profit: float, 
                     predicted_prob: float = 0.5, trading_mode: str = "PAPER") -> None:
        """
        Record trade result.
        
        Args:
            symbol: Stock symbol
            won: Did position win?
            profit: P&L in rupees
            predicted_prob: ML model's predicted win probability
            trading_mode: 'PAPER' or 'LIVE' - mode in which trade was executed
        """
        # Dynamically create symbol entry if it doesn't exist (for options contracts)
        if symbol not in self.symbol_stats:
            logger.info(f"SYMBOL_TRACKER: NEW_SYMBOL_ADDED | {symbol}")
            self.symbol_stats[symbol] = {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'total_profit': 0.0,
                'avg_profit_per_trade': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'trade_history': deque(maxlen=LearningConfig.LOOKBACK_TRADES),
                'recent_form': 'neutral',
                'win_rate': 0.0,
                'win_rate_last_10': 0.0,
                'reliability_score': 0.5,
                'confidence_multiplier': 1.0,
                'last_updated': datetime.now().isoformat()
            }
        
        stats = self.symbol_stats[symbol]
        
        # Update counters
        stats['total_trades'] += 1
        stats['wins'] += 1 if won else 0
        stats['losses'] += 0 if won else 1
        stats['total_profit'] += profit
        
        # Add to history
        trade_record = {
            'date': datetime.now().isoformat(),
            'won': won,
            'profit': profit,
            'predicted_prob': predicted_prob,
            'profit_range': self._categorize_profit(profit),
            'trading_mode': trading_mode  # PAPER or LIVE
        }
        stats['trade_history'].append(trade_record)
        stats['last_updated'] = datetime.now().isoformat()
        
        # Recalculate metrics
        self._update_metrics(symbol)
        
        # Save updated stats
        self._save_stats()
        
        logger.info(f"SYMBOL_TRACKER: TRADE_RECORDED | {symbol} | mode={trading_mode} | won={won} | profit=₹{profit:.2f} | total={stats['total_trades']} | wr={stats['win_rate']:.1%}")
    
    def _update_metrics(self, symbol: str) -> None:
        """Recalculate all metrics for a symbol"""
        stats = self.symbol_stats[symbol]
        history = list(stats['trade_history'])
        
        if not history:
            return
        
        # Overall statistics
        stats['win_rate'] = stats['wins'] / stats['total_trades'] if stats['total_trades'] > 0 else 0.0
        stats['avg_profit_per_trade'] = stats['total_profit'] / stats['total_trades'] if stats['total_trades'] > 0 else 0.0
        
        # Win and loss averages
        wins = [t['profit'] for t in history if t['won']]
        losses = [t['profit'] for t in history if not t['won']]
        stats['avg_win'] = np.mean(wins) if wins else 0.0
        stats['avg_loss'] = np.mean(losses) if losses else 0.0
        
        # Recent form (last 10 trades)
        recent = history[-LearningConfig.FORM_LOOKBACK:]
        if recent:
            recent_wins = sum(1 for t in recent if t['won'])
            stats['win_rate_last_10'] = recent_wins / len(recent)
            
            # Determine form: hot/neutral/cold
            if stats['win_rate_last_10'] >= LearningConfig.HOT_THRESHOLD:
                stats['recent_form'] = 'hot'
            elif stats['win_rate_last_10'] <= LearningConfig.COLD_THRESHOLD:
                stats['recent_form'] = 'cold'
            else:
                stats['recent_form'] = 'neutral'
        
        # Reliability score: how consistent?
        if stats['total_trades'] >= LearningConfig.MIN_TRADES_FOR_STATS:
            # High consistency (low variance) = high reliability
            profits = [t['profit'] for t in history]
            profit_variance = np.var(profits) if profits else 1000
            # Normalize: lower variance = higher score
            stats['reliability_score'] = 1.0 / (1.0 + profit_variance / 1000)
        else:
            # Not enough data
            stats['reliability_score'] = 0.3
        
        # Confidence multiplier based on performance
        if stats['total_trades'] >= LearningConfig.MIN_TRADES_FOR_STATS:
            # Hot symbols: boost confidence
            if stats['recent_form'] == 'hot':
                stats['confidence_multiplier'] = min(1.2, 1.0 + (stats['win_rate_last_10'] - 0.5) * 0.4)
            # Cold symbols: reduce confidence
            elif stats['recent_form'] == 'cold':
                stats['confidence_multiplier'] = max(0.7, 1.0 - (0.5 - stats['win_rate_last_10']) * 0.4)
            # Neutral: no adjustment
            else:
                stats['confidence_multiplier'] = 1.0
        
        logger.debug(f"SYMBOL_TRACKER: METRICS_UPDATED | {symbol} | wr={stats['win_rate']:.1%} | form={stats['recent_form']} | mult={stats['confidence_multiplier']:.2f}")
    
    def get_symbol_stats(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current stats for a symbol"""
        return self.symbol_stats.get(symbol)
    
    def get_confidence_multiplier(self, symbol: str) -> float:
        """
        Get confidence multiplier for a symbol.
        
        Returns:
            Multiplier: 0.7 (cold) to 1.3 (hot)
        """
        if symbol not in self.symbol_stats:
            return 1.0
        
        return self.symbol_stats[symbol]['confidence_multiplier']
    
    def get_form(self, symbol: str) -> str:
        """Get recent form: 'hot', 'neutral', or 'cold'"""
        if symbol not in self.symbol_stats:
            return 'neutral'
        
        return self.symbol_stats[symbol]['recent_form']
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get all symbol statistics"""
        return {
            sym: {k: v for k, v in stats.items() if k != 'trade_history'}
            for sym, stats in self.symbol_stats.items()
        }
    
    @staticmethod
    def _categorize_profit(profit: float) -> str:
        """Categorize profit magnitude"""
        if profit > 500:
            return 'huge_win'
        elif profit > 100:
            return 'big_win'
        elif profit > 0:
            return 'small_win'
        elif profit > -100:
            return 'small_loss'
        else:
            return 'big_loss'
    
    def _save_stats(self):
        """Save stats to disk"""
        try:
            stats_data = {}
            for symbol, stats in self.symbol_stats.items():
                stats_copy = {k: v for k, v in stats.items() if k != 'trade_history'}
                stats_copy['trade_history'] = list(stats['trade_history'])
                stats_data[symbol] = stats_copy
            
            self.symbol_stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.symbol_stats_file, 'w') as f:
                json.dump(stats_data, f, indent=2)
        except Exception as e:
            logger.error(f"SYMBOL_TRACKER: SAVE_ERROR | {str(e)}")
    
    def _load_stats(self):
        """Load stats from disk if available"""
        try:
            if not self.symbol_stats_file.exists():
                return
            
            with open(self.symbol_stats_file, 'r') as f:
                stats_data = json.load(f)
            
            for symbol, data in stats_data.items():
                if symbol in self.symbol_stats:
                    for key, value in data.items():
                        if key == 'trade_history':
                            self.symbol_stats[symbol]['trade_history'] = deque(value, maxlen=LearningConfig.LOOKBACK_TRADES)
                        else:
                            self.symbol_stats[symbol][key] = value
            
            logger.info(f"SYMBOL_TRACKER: STATS_LOADED | {len(stats_data)} symbols")
        except Exception as e:
            logger.error(f"SYMBOL_TRACKER: LOAD_ERROR | {str(e)}")


# =============================================================================
# Trade Result Recorder
# =============================================================================

class TradeResultRecorder:
    """
    Records detailed information about every trade for learning.
    
    Stores:
    - Entry time, symbol, action, confidence, entry premium
    - ML prediction at entry time
    - Exit time, exit premium, P&L, exit reason
    - Feature values at entry
    - Model accuracy metrics
    """
    
    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = Path(history_file) if history_file else Path(LearningConfig.TRADE_HISTORY_FILE)
        self.open_trades = {}  # {symbol: trade_data}
        
        logger.debug("TRADE_RECORDER: INITIALIZED")
    
    def record_entry(self, symbol: str, action: str, confidence: float, 
                    entry_premium: float, order_id: str, 
                    ml_prediction: Optional[Dict[str, float]] = None,
                    features: Optional[Dict[str, float]] = None,
                    trading_mode: str = "PAPER") -> None:
        """Record position entry"""
        trade_data = {
            'order_id': order_id,
            'symbol': symbol,
            'action': action,
            'entry_time': datetime.now().isoformat(),
            'entry_confidence': confidence,
            'entry_premium': entry_premium,
            'ml_prediction': ml_prediction or {},
            'entry_features': features or {},
            'trading_mode': trading_mode,  # PAPER or LIVE
        }
        
        self.open_trades[symbol] = trade_data
        logger.debug(f"TRADE_RECORDER: ENTRY_RECORDED | {symbol} | mode={trading_mode} | conf={confidence:.1f}% | premium=₹{entry_premium:.2f}")
    
    def record_exit(self, symbol: str, exit_premium: float, 
                   exit_reason: str) -> Optional[Dict[str, Any]]:
        """
        Record position exit and calculate metrics.
        
        Returns: Complete trade record or None if no matching entry
        """
        if symbol not in self.open_trades:
            logger.warning(f"TRADE_RECORDER: NO_ENTRY_FOUND | {symbol}")
            return None
        
        trade_data = self.open_trades[symbol]
        
        # Calculate P&L
        premium_change = exit_premium - trade_data['entry_premium']
        if trade_data['action'] == 'BUY':
            profit = premium_change  # Long position profits from premium increase
        else:  # SELL
            profit = -premium_change  # Short position profits from premium decrease
        
        # Complete trade record
        trade_data['exit_time'] = datetime.now().isoformat()
        trade_data['exit_premium'] = exit_premium
        trade_data['exit_reason'] = exit_reason
        trade_data['profit'] = profit
        trade_data['win'] = profit > 0
        trade_data['duration_seconds'] = (datetime.fromisoformat(trade_data['exit_time']) - 
                                         datetime.fromisoformat(trade_data['entry_time'])).total_seconds()
        
        # Calculate model accuracy
        if trade_data['ml_prediction']:
            ml_prob = trade_data['ml_prediction'].get('win_probability', 0.5)
            trade_data['ml_accuracy'] = ml_prob if trade_data['win'] else (1 - ml_prob)
        
        # Save to history
        self._append_to_history(trade_data)
        
        # Remove from open trades
        del self.open_trades[symbol]
        
        logger.info(f"TRADE_RECORDER: EXIT_RECORDED | {symbol} | mode={trade_data.get('trading_mode', 'PAPER')} | profit=₹{profit:.2f} | reason={exit_reason}")
        
        return trade_data
    
    def _append_to_history(self, trade_data: Dict[str, Any]):
        """Append trade to history file"""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, 'a') as f:
                f.write(json.dumps(trade_data) + '\n')
        except Exception as e:
            logger.error(f"TRADE_RECORDER: APPEND_ERROR | {str(e)}")
    
    def get_recent_trades(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent completed trades"""
        trades = []
        try:
            if not self.history_file.exists():
                return trades
            
            with open(self.history_file, 'r') as f:
                for line in f:
                    trade = json.loads(line)
                    if symbol is None or trade['symbol'] == symbol:
                        trades.append(trade)
            
            # Return last N trades
            return trades[-limit:]
        except Exception as e:
            logger.error(f"TRADE_RECORDER: READ_ERROR | {str(e)}")
            return trades


# =============================================================================
# Global instances
# =============================================================================

_symbol_tracker = None
_trade_recorder = None

def get_symbol_tracker() -> SymbolPerformanceTracker:
    """Get or create symbol tracker"""
    global _symbol_tracker
    if _symbol_tracker is None:
        _symbol_tracker = SymbolPerformanceTracker()
    return _symbol_tracker

def get_trade_recorder() -> TradeResultRecorder:
    """Get or create trade recorder"""
    global _trade_recorder
    if _trade_recorder is None:
        _trade_recorder = TradeResultRecorder()
    return _trade_recorder
