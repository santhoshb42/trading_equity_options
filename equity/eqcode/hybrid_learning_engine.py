"""
Hybrid Learning Engine - Real + Paper Trade Intelligence

Unified learning system that:
1. Tracks real trades (10 slots × 2k capital) for actual P&L
2. Tracks paper trades (unlimited) for pattern learning
3. Learns feature importance: what features predict winners?
4. Adapts daily: symbol performance + feature quality scoring
5. Ranks alerts by predicted win probability + profit potential

Architecture:
├─ Symbol Performance Tracker (win rates, recent form, reliability)
├─ Feature Importance Calculator (what separates winners from losers)
├─ Smart Alert Ranker (scores and selects top N trades)
└─ EOD Learning Engine (updates all models with today's results)

Goal: 5% minimum daily profit across all trades (real + paper learning)
"""

import json
import pickle
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict, deque
import numpy as np
from statistics import mean, stdev


class SymbolPerformanceTracker:
    """
    Tracks per-symbol win rates, recent performance, and reliability
    Uses time-decay to weight recent trades heavier
    """
    
    def __init__(self, decay_days=30):
        self.symbol_stats = defaultdict(lambda: {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0.0,
            'trade_history': deque(maxlen=50),  # Last 50 trades
            'recent_form': 'neutral',  # 'hot', 'neutral', 'cold'
            'win_rate': 0.0,
            'win_rate_last_10': 0.0,
            'avg_profit': 0.0,
            'reliability_score': 0.5,  # 0.0 = unreliable, 1.0 = very reliable
            'last_updated': None,
        })
        self.decay_days = decay_days
    
    def record_trade(self, symbol: str, won: bool, profit: float, 
                     is_paper: bool = False) -> None:
        """
        Record a trade result
        
        Args:
            symbol: Stock symbol
            won: Did it win?
            profit: P&L in rupees
            is_paper: Was this a paper trade?
        """
        stats = self.symbol_stats[symbol]
        
        # Update counters
        stats['total_trades'] += 1
        stats['wins'] += 1 if won else 0
        stats['losses'] += 0 if won else 1
        stats['total_profit'] += profit
        
        # Add to history
        trade_record = {
            'date': datetime.now(),
            'won': won,
            'profit': profit,
            'is_paper': is_paper,
        }
        stats['trade_history'].append(trade_record)
        stats['last_updated'] = datetime.now()
        
        # Recalculate metrics
        self._update_metrics(symbol)
    
    def _update_metrics(self, symbol: str) -> None:
        """Recalculate all metrics for a symbol"""
        stats = self.symbol_stats[symbol]
        history = list(stats['trade_history'])
        
        if not history:
            return
        
        # Overall win rate
        stats['win_rate'] = stats['wins'] / stats['total_trades'] if stats['total_trades'] > 0 else 0.0
        
        # Recent win rate (last 10 trades, time-weighted)
        recent = history[-10:]
        if recent:
            recent_wins = sum(1 for t in recent if t['won'])
            stats['win_rate_last_10'] = recent_wins / len(recent)
            
            # Determine recent form: 3+ wins in last 5 = hot, 2+ losses = cold
            last_5 = history[-5:]
            wins_last_5 = sum(1 for t in last_5 if t['won'])
            if wins_last_5 >= 3:
                stats['recent_form'] = 'hot'
            elif wins_last_5 <= 1:
                stats['recent_form'] = 'cold'
            else:
                stats['recent_form'] = 'neutral'
        
        # Average profit per trade
        stats['avg_profit'] = stats['total_profit'] / stats['total_trades'] if stats['total_trades'] > 0 else 0.0
        
        # Reliability: consistency of performance
        if len(history) >= 5:
            profits = [t['profit'] for t in history]
            try:
                std_dev = stdev(profits)
                mean_profit = mean(profits)
                
                # Lower volatility = more reliable
                # Positive mean = more reliable
                if mean_profit > 0:
                    reliability = 1.0 / (1.0 + std_dev / max(abs(mean_profit), 1.0))
                else:
                    reliability = 0.0
                
                stats['reliability_score'] = max(0.0, min(1.0, reliability))
            except:
                stats['reliability_score'] = 0.5
    
    def get_symbol_form_bonus(self, symbol: str) -> float:
        """
        Get recent form bonus/penalty (-0.2 to +0.2)
        
        hot = +0.20 (doing well, trust it)
        neutral = 0.0
        cold = -0.10 (struggling, lower weight)
        """
        form = self.symbol_stats[symbol]['recent_form']
        
        if form == 'hot':
            return 0.20
        elif form == 'cold':
            return -0.10
        else:
            return 0.0
    
    def get_symbol_stats(self, symbol: str) -> Dict[str, Any]:
        """Get all stats for a symbol"""
        return dict(self.symbol_stats[symbol])
    
    def get_top_symbols(self, n: int = 10) -> List[Tuple[str, float]]:
        """Get top N symbols by combined score (win rate + recent form)"""
        scores = []
        for symbol, stats in self.symbol_stats.items():
            if stats['total_trades'] < 3:  # Need minimum trades to judge
                continue
            
            # Combined score: win rate + recent form
            score = (stats['win_rate'] * 0.7) + (stats['reliability_score'] * 0.3)
            scores.append((symbol, score))
        
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:n]


class FeatureImportanceCalculator:
    """
    Learns which features best predict winning trades
    
    Features tracked:
    - momentum_3: 3-period price momentum
    - momentum_5: 5-period price momentum
    - volatility: Price volatility (std dev)
    - volume_trend: Current volume vs average
    - rsi: Relative strength index
    - rsi_extreme: Is RSI in extreme zone (< 30 or > 70)?
    - trend_consistency: Recent trend strength
    - alert_confidence: How confident is the alert source?
    """
    
    def __init__(self):
        self.feature_wins = defaultdict(int)  # Count wins per feature
        self.feature_losses = defaultdict(int)  # Count losses per feature
        self.feature_profit = defaultdict(float)  # Total profit per feature
        self.feature_trades = defaultdict(int)  # Total trades per feature
        
        # Importance weights (updated based on learning)
        self.feature_importance = {
            'momentum_3': 0.15,
            'momentum_5': 0.15,
            'volatility': 0.08,
            'volume_trend': 0.12,
            'rsi': 0.12,
            'rsi_extreme': 0.18,  # Extreme RSI is strong predictor
            'trend_consistency': 0.10,
            'alert_confidence': 0.10,
        }
        
        # Thresholds for good feature values
        self.feature_thresholds = {
            'momentum_3': {'good': [0.01, float('inf')], 'weight': 1.0},  # Positive momentum good
            'rsi_extreme': {'good': [0.5, float('inf')], 'weight': 1.5},  # Extreme RSI very good
            'volume_trend': {'good': [1.05, float('inf')], 'weight': 1.2},  # Volume spike good
            'trend_consistency': {'good': [0.6, float('inf')], 'weight': 1.0},  # Consistent trend good
        }
    
    def record_trade_features(self, features: Dict[str, float], won: bool, 
                             profit: float) -> None:
        """
        Record features of a trade and its outcome
        Helps calculate which features correlate with wins
        """
        for feature, value in features.items():
            if feature not in self.feature_importance:
                continue
            
            self.feature_trades[feature] += 1
            
            if won:
                self.feature_wins[feature] += 1
                self.feature_profit[feature] += profit
            else:
                self.feature_losses[feature] += 1
                self.feature_profit[feature] -= abs(profit)
    
    def calculate_feature_quality_score(self, features: Dict[str, float]) -> float:
        """
        Score how good the feature set is (0.0 to 1.0)
        Considers both feature values AND their importance
        """
        score = 0.0
        total_weight = 0.0
        
        for feature, value in features.items():
            if feature not in self.feature_importance:
                continue
            
            importance = self.feature_importance[feature]
            
            # Check if this feature value is "good"
            feature_score = self._score_feature_value(feature, value)
            
            # Weight by importance
            score += feature_score * importance
            total_weight += importance
        
        return min(1.0, score / total_weight) if total_weight > 0 else 0.5
    
    def _score_feature_value(self, feature: str, value: float) -> float:
        """Score a single feature value (0.0 to 1.0)"""
        
        if feature == 'momentum_3':
            # Positive momentum is good
            if value > 0.02:
                return 0.9
            elif value > 0.01:
                return 0.7
            else:
                return 0.3
        
        elif feature == 'momentum_5':
            if value > 0.03:
                return 0.9
            elif value > 0.01:
                return 0.7
            else:
                return 0.3
        
        elif feature == 'rsi_extreme':
            # Extreme RSI (value = 1) is very good
            return value  # 0.0 or 1.0
        
        elif feature == 'volume_trend':
            # Spike in volume is good (>1.1)
            if value > 1.2:
                return 0.95
            elif value > 1.1:
                return 0.8
            elif value > 0.9:
                return 0.5
            else:
                return 0.2
        
        elif feature == 'trend_consistency':
            # Consistent trend is good
            if value > 0.7:
                return 0.9
            elif value > 0.5:
                return 0.7
            else:
                return 0.4
        
        elif feature == 'alert_confidence':
            # Direct mapping
            return value
        
        elif feature == 'volatility':
            # Moderate volatility is good (1-3%)
            if 1.0 <= value <= 3.0:
                return 0.85
            elif 0.5 <= value <= 4.0:
                return 0.7
            else:
                return 0.4
        
        return 0.5
    
    def update_feature_importance(self) -> None:
        """
        Recalculate feature importance based on predictive power
        """
        for feature in self.feature_importance.keys():
            if self.feature_trades[feature] < 10:  # Need min trades to judge
                continue
            
            # Win rate for this feature
            total = self.feature_trades[feature]
            wins = self.feature_wins[feature]
            win_rate = wins / total if total > 0 else 0.5
            
            # Profit for this feature
            avg_profit = self.feature_profit[feature] / total if total > 0 else 0.0
            
            # Importance = (win rate + profit normalization)
            # Features that lead to wins AND profits rank higher
            profit_score = min(1.0, max(0.0, avg_profit / 100.0))  # Normalize to 0-1
            combined = (win_rate * 0.6) + (profit_score * 0.4)
            
            # Smooth update: 70% old importance, 30% new learning
            self.feature_importance[feature] = (
                self.feature_importance[feature] * 0.7 +
                combined * 0.3
            )
        
        # Normalize so sum = 1.0
        total = sum(self.feature_importance.values())
        if total > 0:
            for feature in self.feature_importance:
                self.feature_importance[feature] /= total
    
    def get_feature_importance(self) -> Dict[str, float]:
        """Get current feature importance scores"""
        return dict(self.feature_importance)


class AdaptiveAlertRanker:
    """
    Ranks alerts by predicted win probability + profit potential
    
    Scoring formula:
    final_score = (
        ml_confidence * 0.40 +      # What ML model predicts
        symbol_form * 0.20 +        # Recent symbol performance
        feature_quality * 0.25 +    # How good are the features
        reliability * 0.15          # Symbol reliability
    )
    """
    
    def __init__(self, perf_tracker: SymbolPerformanceTracker,
                 feature_calc: FeatureImportanceCalculator):
        self.perf_tracker = perf_tracker
        self.feature_calc = feature_calc
    
    def rank_alerts(self, alerts: List[Dict[str, Any]]) -> List[Tuple[Dict, float]]:
        """
        Rank all alerts by expected win probability
        
        Returns:
            List of (alert, score) tuples sorted by score descending
        """
        scored_alerts = []
        
        for alert in alerts:
            score = self.score_alert(alert)
            scored_alerts.append((alert, score))
        
        # Sort by score descending
        scored_alerts.sort(key=lambda x: x[1], reverse=True)
        return scored_alerts
    
    def score_alert(self, alert: Dict[str, Any]) -> float:
        """
        Calculate final score for an alert
        
        Args:
            alert: Alert dict with symbol, features, ml_score, etc.
        
        Returns:
            Score from 0.0 (worst) to 1.0 (best)
        """
        symbol = alert.get('symbol', 'UNKNOWN')
        ml_score = alert.get('ml_score', 0.5)  # From ML model
        features = alert.get('features', {})  # Feature dict
        
        # 1. Symbol recent form bonus/penalty
        form_bonus = self.perf_tracker.get_symbol_form_bonus(symbol)
        
        # 2. Feature quality score
        feature_quality = self.feature_calc.calculate_feature_quality_score(features)
        
        # 3. Symbol reliability
        stats = self.perf_tracker.get_symbol_stats(symbol)
        reliability = stats['reliability_score']
        
        # 4. Composite score
        # Ensure form_bonus doesn't overshoot (it's a -0.2 to +0.2 adjustment)
        adjusted_ml = max(0.0, min(1.0, ml_score + form_bonus))
        
        final_score = (
            adjusted_ml * 0.40 +
            form_bonus * 0.20 +  # Form bonus as direct component too
            feature_quality * 0.25 +
            reliability * 0.15
        )
        
        return max(0.0, min(1.0, final_score))
    
    def select_top_trades(self, alerts: List[Dict[str, Any]], 
                         real_slots: int = 10, 
                         paper_unlimited: bool = True) -> Dict[str, Any]:
        """
        Select top N alerts for real trading
        Rest go to paper trading for learning
        
        Returns:
            {
                'real_trades': [(alert, score), ...],  # Top 10
                'paper_trades': [(alert, score), ...], # Rest
                'summary': {...}
            }
        """
        ranked = self.rank_alerts(alerts)
        
        real_trades = ranked[:real_slots]
        paper_trades = ranked[real_slots:]
        
        return {
            'real_trades': real_trades,
            'paper_trades': paper_trades,
            'total_alerts': len(ranked),
            'real_selected': len(real_trades),
            'paper_selected': len(paper_trades),
            'summary': {
                'top_real_score': real_trades[0][1] if real_trades else 0.0,
                'avg_real_score': mean([s for _, s in real_trades]) if real_trades else 0.0,
                'avg_paper_score': mean([s for _, s in paper_trades]) if paper_trades else 0.0,
            }
        }


class HybridLearningEngine:
    """
    Main orchestrator combining all components
    """
    
    def __init__(self, storage_dir: str = "data/learning"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        self.perf_tracker = SymbolPerformanceTracker()
        self.feature_calc = FeatureImportanceCalculator()
        self.ranker = AdaptiveAlertRanker(self.perf_tracker, self.feature_calc)
        
        self._load_from_disk()
    
    def rank_and_select(self, alerts: List[Dict[str, Any]], 
                       real_slots: int = 10) -> Dict[str, Any]:
        """
        Main entry point: Rank alerts and select top N for real trading
        """
        selection = self.ranker.select_top_trades(alerts, real_slots)
        
        # Add learning status
        selection['learning_status'] = {
            'feature_importance': self.feature_calc.get_feature_importance(),
            'top_symbols': self.perf_tracker.get_top_symbols(10),
        }
        
        return selection
    
    def record_real_trade_result(self, symbol: str, won: bool, profit: float,
                                 features: Dict[str, float]) -> None:
        """Record result of a real trade"""
        self.perf_tracker.record_trade(symbol, won, profit, is_paper=False)
        self.feature_calc.record_trade_features(features, won, profit)
    
    def record_paper_trade_result(self, symbol: str, won: bool, profit: float,
                                  features: Dict[str, float]) -> None:
        """Record result of a paper trade"""
        self.perf_tracker.record_trade(symbol, won, profit, is_paper=True)
        self.feature_calc.record_trade_features(features, won, profit)
    
    def record_closed_trade_from_csv(self, trade_data: Dict[str, Any]) -> None:
        """
        Record a closed trade from CSV log data
        
        Args:
            trade_data: Dictionary with keys:
                - symbol: Stock symbol
                - pnl: Profit/Loss amount
                - entry_price: Entry price
                - exit_price: Exit price
                - quantity: Quantity traded
                - timestamp: ISO timestamp string
        """
        symbol = trade_data.get('symbol', '')
        pnl = trade_data.get('pnl', 0)
        
        if not symbol or pnl is None:
            return
        
        # Determine if trade won (pnl > 0)
        won = pnl > 0
        
        # Record to performance tracker
        self.perf_tracker.record_trade(symbol, won, pnl, is_paper=False)
    
    def ingest_real_trades(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Ingest a batch of real closed trades
        
        Args:
            trades: List of trade dictionaries from CSV parser
        
        Returns:
            Summary of ingestion
        """
        ingested = 0
        failed = 0
        
        for trade in trades:
            try:
                self.record_closed_trade_from_csv(trade)
                ingested += 1
            except Exception as e:
                print(f"Failed to ingest trade {trade.get('symbol')}: {e}")
                failed += 1
        
        return {
            'ingested': ingested,
            'failed': failed,
            'total': len(trades),
            'timestamp': datetime.now().isoformat()
        }
    
    def eod_learning_update(self) -> Dict[str, Any]:
        """
        Called at EOD (3:30 PM)
        Updates all learning based on today's results
        """
        # Recalculate feature importance
        old_importance = dict(self.feature_calc.get_feature_importance())
        self.feature_calc.update_feature_importance()
        new_importance = dict(self.feature_calc.get_feature_importance())
        
        # Save to disk
        self._save_to_disk()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'feature_importance_updated': {
                'old': old_importance,
                'new': new_importance,
            },
            'top_symbols': self.perf_tracker.get_top_symbols(10),
        }
    
    def get_learning_stats(self) -> Dict[str, Any]:
        """Get current learning statistics"""
        return {
            'timestamp': datetime.now().isoformat(),
            'feature_importance': self.feature_calc.get_feature_importance(),
            'top_symbols': self.perf_tracker.get_top_symbols(10),
            'symbol_stats': dict(self.perf_tracker.symbol_stats),
        }
    
    def _save_to_disk(self) -> None:
        """Persist learning to disk"""
        try:
            # Save feature importance
            features_file = self.storage_dir / 'feature_importance.json'
            with open(features_file, 'w') as f:
                json.dump(self.feature_calc.get_feature_importance(), f, indent=2)
            
            # Save symbol stats (convert deques to lists for JSON)
            stats_file = self.storage_dir / 'symbol_stats.json'
            stats_to_save = {}
            for symbol, stats in self.perf_tracker.symbol_stats.items():
                stats_copy = dict(stats)
                # Convert deque to list for JSON serialization
                stats_copy['trade_history'] = [
                    {
                        'date': t['date'].isoformat(),
                        'won': t['won'],
                        'profit': t['profit'],
                        'is_paper': t['is_paper'],
                    }
                    for t in stats['trade_history']
                ]
                stats_to_save[symbol] = stats_copy
            
            with open(stats_file, 'w') as f:
                json.dump(stats_to_save, f, indent=2, default=str)
        
        except Exception as e:
            print(f"Warning: Failed to save learning data: {e}")
    
    def _load_from_disk(self) -> None:
        """Load learning from disk"""
        try:
            # Load feature importance
            features_file = self.storage_dir / 'feature_importance.json'
            if features_file.exists():
                with open(features_file, 'r') as f:
                    self.feature_calc.feature_importance = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load learning data: {e}")


# Global instance
_hybrid_engine = None


def get_hybrid_engine() -> HybridLearningEngine:
    """Get or create hybrid engine instance"""
    global _hybrid_engine
    if _hybrid_engine is None:
        _hybrid_engine = HybridLearningEngine()
    return _hybrid_engine


def rank_and_select_alerts(alerts: List[Dict[str, Any]], 
                          real_slots: int = 10) -> Dict[str, Any]:
    """
    Main function: Rank alerts and select top N for real trading
    
    Usage:
        selection = rank_and_select_alerts(all_alerts, real_slots=10)
        for alert, score in selection['real_trades']:
            # Execute real trade
            pass
        for alert, score in selection['paper_trades']:
            # Execute paper trade
            pass
    """
    engine = get_hybrid_engine()
    return engine.rank_and_select(alerts, real_slots)


def record_trade_result(symbol: str, won: bool, profit: float,
                       features: Dict[str, float], is_paper: bool = False) -> None:
    """
    Record trade outcome for learning
    """
    engine = get_hybrid_engine()
    
    if is_paper:
        engine.record_paper_trade_result(symbol, won, profit, features)
    else:
        engine.record_real_trade_result(symbol, won, profit, features)


def eod_learning_update() -> Dict[str, Any]:
    """Called at end of day to update learning"""
    engine = get_hybrid_engine()
    return engine.eod_learning_update()


def get_learning_stats() -> Dict[str, Any]:
    """Get current learning statistics"""
    engine = get_hybrid_engine()
    return engine.get_learning_stats()
