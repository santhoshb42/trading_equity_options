"""
Hybrid Learning Engine for Options Trading

Specialized machine learning for options-specific trading:
1. Greeks-aware profit prediction (Delta, Gamma, Theta, Vega)
2. Volatility regime learning (high/medium/low IV environments)
3. Time decay impact modeling (Theta degradation)
4. Strike selection optimization (ATM vs OTM selection)
5. Contract type preference learning (CE vs PE historical performance)
6. Expiry cycle learning (current month vs next month)

Architecture:
├─ Options Greeks Analyzer (Delta hedge, Gamma risk, Theta decay)
├─ Volatility Regime Detector (IV percentile, IV rank learning)
├─ Strike Selection Engine (ATM optimality, OTM risk/reward)
├─ Contract Type Performance Tracker (CE vs PE historical win rates)
└─ EOD Learning System (updates all models with daily Greeks changes)

Goal: 3-5% daily profit with controlled Greeks exposure
"""

import json
import pickle
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict, deque
import numpy as np
from statistics import mean, stdev

# Import ML config
try:
    from optconfig import MLConfig
except ImportError:
    # Fallback if optconfig not available
    MLConfig = None


class OptionsGreeksAnalyzer:
    """
    Tracks Greeks impact on trade outcomes
    Learns which Greeks combinations lead to wins
    """
    
    def __init__(self):
        self.greek_stats = defaultdict(lambda: {
            'trades': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0.0,
            'avg_delta': 0.0,
            'avg_gamma': 0.0,
            'avg_theta': 0.0,
            'avg_vega': 0.0,
            'trades_history': deque(maxlen=MLConfig.TRADE_HISTORY_SIZE if MLConfig else 100),
        })
        
        # Load optimal Greeks from config (can be updated by learning)
        if MLConfig:
            self.optimal_greeks = MLConfig.OPTIMAL_GREEKS.copy()
            self.greeks_weights = MLConfig.GREEKS_WEIGHTS.copy()
        else:
            # Fallback hardcoded values
            self.optimal_greeks = {
                'ce_buy': {'delta': 0.65, 'gamma': 0.015, 'theta': -0.05, 'vega': 0.8},
                'ce_sell': {'delta': -0.35, 'gamma': -0.015, 'theta': 0.05, 'vega': -0.8},
                'pe_buy': {'delta': -0.65, 'gamma': 0.015, 'theta': -0.05, 'vega': 0.8},
                'pe_sell': {'delta': 0.35, 'gamma': -0.015, 'theta': 0.05, 'vega': -0.8},
            }
            self.greeks_weights = {
                'delta': 0.35,
                'gamma': 0.20,
                'theta': 0.25,
                'vega': 0.20,
            }
    
    def record_greek_trade(self, contract_type: str, action: str, 
                          entry_greeks: Dict[str, float],
                          exit_greeks: Dict[str, float],
                          profit: float, won: bool) -> None:
        """
        Record a trade with its Greeks at entry and exit
        
        Args:
            contract_type: 'CE' or 'PE'
            action: 'BUY' or 'SELL'
            entry_greeks: dict with delta, gamma, theta, vega at entry
            exit_greeks: dict with delta, gamma, theta, vega at exit
            profit: profit/loss in rupees
            won: did it win?
        """
        key = f"{contract_type}_{action}".upper()
        stats = self.greek_stats[key]
        
        stats['trades'] += 1
        if won:
            stats['wins'] += 1
        else:
            stats['losses'] += 1
        
        stats['total_profit'] += profit
        
        # Calculate Greeks changes
        for greek in ['delta', 'gamma', 'theta', 'vega']:
            entry_val = entry_greeks.get(greek, 0.0)
            exit_val = exit_greeks.get(greek, 0.0)
            change = exit_val - entry_val
            
            # Update running average
            n = stats['trades']
            old_avg = stats.get(f'avg_{greek}', 0.0)
            stats[f'avg_{greek}'] = (old_avg * (n-1) + change) / n
        
        # Add to history
        trade_record = {
            'date': datetime.now(),
            'won': won,
            'profit': profit,
            'entry_greeks': entry_greeks.copy(),
            'exit_greeks': exit_greeks.copy(),
        }
        stats['trades_history'].append(trade_record)
    
    def score_greeks_quality(self, contract_type: str, action: str,
                            current_greeks: Dict[str, float]) -> float:
        """
        Score how good the Greeks setup is (0.0 to 1.0)
        
        Considers distance from optimal Greeks for the strategy
        """
        key = f"{contract_type}_{action}".upper()
        optimal = self.optimal_greeks.get(key, {})
        
        if not optimal:
            return 0.5  # Unknown combination
        
        # Calculate weighted distance from optimal
        total_score = 0.0
        total_weight = 0.0
        
        for greek, weight in self.greeks_weights.items():
            optimal_val = optimal.get(greek, 0.0)
            current_val = current_greeks.get(greek, 0.0)
            
            # Distance from optimal (normalized)
            distance = abs(current_val - optimal_val)
            max_distance = max(abs(optimal_val), 1.0)
            distance_ratio = min(1.0, distance / max_distance)
            
            # Convert distance to score (0 distance = 1.0 score)
            greek_score = 1.0 - distance_ratio
            
            total_score += greek_score * weight
            total_weight += weight
        
        return total_score / total_weight if total_weight > 0 else 0.5
    
    def get_greeks_stats(self, contract_type: str, action: str) -> Dict[str, Any]:
        """Get statistics for a specific Greeks combination"""
        key = f"{contract_type}_{action}".upper()
        stats = self.greek_stats[key]
        
        if stats['trades'] == 0:
            return {'trades': 0, 'win_rate': 0.0}
        
        return {
            'trades': stats['trades'],
            'wins': stats['wins'],
            'losses': stats['losses'],
            'win_rate': stats['wins'] / stats['trades'],
            'avg_profit': stats['total_profit'] / stats['trades'],
            'avg_delta': stats['avg_delta'],
            'avg_gamma': stats['avg_gamma'],
            'avg_theta': stats['avg_theta'],
            'avg_vega': stats['avg_vega'],
        }


class VolatilityRegimeDetector:
    """
    Detects and adapts to different volatility regimes
    Different strategies work better in different IV environments
    """
    
    def __init__(self, lookback_days=20):
        self.lookback_days = lookback_days
        self.iv_history = deque(maxlen=lookback_days)
        
        # Performance by regime
        self.regime_stats = {
            'high_iv': {'trades': 0, 'wins': 0, 'avg_profit': 0.0},
            'medium_iv': {'trades': 0, 'wins': 0, 'avg_profit': 0.0},
            'low_iv': {'trades': 0, 'wins': 0, 'avg_profit': 0.0},
        }
        
        # Strategy recommendations by regime
        self.regime_strategies = {
            'high_iv': {
                'preferred_action': 'SELL',  # Sell premium when IV is high
                'strike_bias': 'OTM',  # Sell further OTM for better premium
                'risk_multiplier': 0.7,  # Lower capital per trade
            },
            'medium_iv': {
                'preferred_action': 'BUY',  # Flexible, buy opportunities
                'strike_bias': 'ATM',  # ATM has best risk/reward
                'risk_multiplier': 1.0,  # Normal capital allocation
            },
            'low_iv': {
                'preferred_action': 'BUY',  # Buy when IV is low
                'strike_bias': 'ATM',  # ATM gives good delta movement
                'risk_multiplier': 1.2,  # Can be more aggressive
            },
        }
    
    def add_iv_data(self, iv_value: float) -> None:
        """Record IV value for regime detection"""
        self.iv_history.append(iv_value)
    
    def detect_regime(self) -> Tuple[str, Dict[str, Any]]:
        """
        Detect current volatility regime
        
        Returns:
            (regime_name, regime_stats)
        """
        if len(self.iv_history) < 3:
            return 'medium_iv', {}
        
        iv_array = list(self.iv_history)
        current_iv = iv_array[-1]
        iv_percentile = np.percentile(iv_array, 50)  # Median
        
        # Calculate IV rank (current IV / (min IV to max IV range))
        iv_min = min(iv_array)
        iv_max = max(iv_array)
        iv_range = iv_max - iv_min
        
        if iv_range == 0:
            iv_rank = 0.5
        else:
            iv_rank = (current_iv - iv_min) / iv_range
        
        # Detect regime
        if current_iv > np.percentile(iv_array, 75):
            regime = 'high_iv'
        elif current_iv < np.percentile(iv_array, 25):
            regime = 'low_iv'
        else:
            regime = 'medium_iv'
        
        stats = {
            'current_iv': current_iv,
            'iv_percentile': iv_percentile,
            'iv_rank': iv_rank,
            'iv_min': iv_min,
            'iv_max': iv_max,
        }
        
        return regime, stats
    
    def get_regime_strategy(self, regime: str) -> Dict[str, Any]:
        """Get recommended strategy for current regime"""
        return self.regime_strategies.get(regime, self.regime_strategies['medium_iv'])
    
    def record_regime_trade(self, regime: str, won: bool, profit: float) -> None:
        """Record a trade in a specific regime for learning"""
        stats = self.regime_stats[regime]
        stats['trades'] += 1
        if won:
            stats['wins'] += 1
        
        n = stats['trades']
        old_avg = stats['avg_profit']
        stats['avg_profit'] = (old_avg * (n-1) + profit) / n


class StrikeSelectionOptimizer:
    """
    Learns optimal strike selection for different symbols and conditions
    ATM vs ITM vs OTM preferences
    """
    
    def __init__(self):
        self.strike_performance = defaultdict(lambda: {
            'atm': {'trades': 0, 'wins': 0, 'avg_profit': 0.0},
            'otm_1': {'trades': 0, 'wins': 0, 'avg_profit': 0.0},  # 1 strike OTM
            'otm_2': {'trades': 0, 'wins': 0, 'avg_profit': 0.0},  # 2 strikes OTM
            'itm_1': {'trades': 0, 'wins': 0, 'avg_profit': 0.0},
        })
        
        # General preferences
        self.strike_preferences = {
            'BUY': 'ATM',  # Buy ATM for good delta movement
            'SELL': 'OTM_1',  # Sell OTM for better probability
        }
    
    def record_strike_trade(self, symbol: str, strike_type: str,
                           action: str, won: bool, profit: float) -> None:
        """
        Record a trade by strike type
        
        Args:
            symbol: Underlying symbol
            strike_type: 'atm', 'otm_1', 'otm_2', or 'itm_1'
            action: 'BUY' or 'SELL'
            won: Trade outcome
            profit: P&L
        """
        stats = self.strike_performance[symbol][strike_type]
        stats['trades'] += 1
        
        if won:
            stats['wins'] += 1
        
        n = stats['trades']
        old_avg = stats['avg_profit']
        stats['avg_profit'] = (old_avg * (n-1) + profit) / n
    
    def get_optimal_strike(self, symbol: str, action: str,
                          available_strikes: List[str]) -> str:
        """
        Get optimal strike selection based on historical performance
        
        Prefers strikes that have historically performed well for this symbol
        """
        perf = self.strike_performance[symbol]
        
        best_strike = None
        best_score = -float('inf')
        
        for strike_type in perf.keys():
            if strike_type not in ['atm', 'otm_1', 'otm_2', 'itm_1']:
                continue
            
            stats = perf[strike_type]
            if stats['trades'] < 3:
                continue
            
            # Score: win rate + profit
            win_rate = stats['wins'] / stats['trades']
            profit_score = max(0, stats['avg_profit']) / 100  # Normalize
            
            score = (win_rate * 0.7) + (profit_score * 0.3)
            
            if score > best_score:
                best_score = score
                best_strike = strike_type
        
        # Fallback to preference
        if best_strike is None:
            best_strike = self.strike_preferences.get(action, 'ATM').lower()
        
        return best_strike


class ContractTypePerformanceTracker:
    """
    Tracks CE vs PE performance for learning preferences
    Different symbols may favor CE or PE
    """
    
    def __init__(self):
        self.contract_performance = defaultdict(lambda: {
            'CE': {'trades': 0, 'wins': 0, 'avg_profit': 0.0, 'win_rate': 0.0},
            'PE': {'trades': 0, 'wins': 0, 'avg_profit': 0.0, 'win_rate': 0.0},
        })
    
    def record_contract_trade(self, underlying: str, contract_type: str,
                             action: str, won: bool, profit: float) -> None:
        """Record a trade for a specific contract type"""
        stats = self.contract_performance[underlying][contract_type]
        stats['trades'] += 1
        
        if won:
            stats['wins'] += 1
        
        n = stats['trades']
        old_avg = stats['avg_profit']
        stats['avg_profit'] = (old_avg * (n-1) + profit) / n
        stats['win_rate'] = stats['wins'] / n if n > 0 else 0.0
    
    def get_preferred_contract_type(self, underlying: str) -> str:
        """
        Get historically better performing contract type
        Default to 'CE' if no history
        """
        perf = self.contract_performance[underlying]
        
        ce_stats = perf['CE']
        pe_stats = perf['PE']
        
        # Need minimum trades
        if ce_stats['trades'] < 3 and pe_stats['trades'] < 3:
            return 'CE'  # Default
        
        ce_score = ce_stats['win_rate'] + (ce_stats['avg_profit'] / 100)
        pe_score = pe_stats['win_rate'] + (pe_stats['avg_profit'] / 100)
        
        return 'CE' if ce_score >= pe_score else 'PE'
    
    def get_contract_stats(self, underlying: str) -> Dict[str, Any]:
        """Get CE vs PE statistics"""
        return dict(self.contract_performance[underlying])


class OptionsHybridLearningEngine:
    """
    Master learning engine that combines all options-specific learning
    Updates daily based on realized Greeks changes and P&L
    """
    
    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path('/root/santhosh/trading/put_options/data/learning')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Sub-engines
        self.greeks_analyzer = OptionsGreeksAnalyzer()
        self.volatility_detector = VolatilityRegimeDetector()
        self.strike_optimizer = StrikeSelectionOptimizer()
        self.contract_tracker = ContractTypePerformanceTracker()
        
        # Alert ranking history
        self.alert_ranking_history = deque(maxlen=1000)
        
        # Load existing model if available
        self._load_models()
    
    def _load_models(self) -> None:
        """Load saved learning data from disk"""
        try:
            greeks_file = self.data_dir / 'greeks_stats.json'
            if greeks_file.exists():
                with open(greeks_file) as f:
                    # Could deserialize here
                    pass
        except Exception as e:
            print(f"Note: Could not load saved models: {e}")
    
    def save_models(self) -> None:
        """Save learning data to disk"""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            
            # Save Greeks stats
            greeks_file = self.data_dir / 'greeks_stats.json'
            greeks_data = {
                key: {
                    'trades': stats['trades'],
                    'wins': stats['wins'],
                    'losses': stats['losses'],
                    'total_profit': stats['total_profit'],
                    'win_rate': stats['wins'] / stats['trades'] if stats['trades'] > 0 else 0,
                }
                for key, stats in self.greeks_analyzer.greek_stats.items()
            }
            with open(greeks_file, 'w') as f:
                json.dump(greeks_data, f, indent=2)
            
            # Save contract preferences
            contract_file = self.data_dir / 'contract_stats.json'
            contract_data = dict(self.contract_tracker.contract_performance)
            # Convert defaultdicts for JSON serialization
            for underlying in contract_data:
                for ct in contract_data[underlying]:
                    contract_data[underlying][ct] = dict(contract_data[underlying][ct])
            
            with open(contract_file, 'w') as f:
                json.dump(contract_data, f, indent=2, default=str)
        
        except Exception as e:
            print(f"Warning: Could not save models: {e}")
    
    def rank_and_select_alerts(self, alerts: List[Dict[str, Any]],
                              max_trades: int = 3) -> List[Dict[str, Any]]:
        """
        Rank alerts by predicted success and select top N
        
        Considers:
        - Greeks quality
        - Volatility regime fit
        - Strike selection optimality
        - Contract type historical performance
        - Recent symbol performance
        """
        scored_alerts = []
        
        for alert in alerts:
            symbol = alert.get('symbol', '')
            action = alert.get('action', 'BUY').upper()
            contract_type = alert.get('contract_type', 'CE')
            
            # Get Greeks score
            entry_greeks = alert.get('greeks', {})
            greeks_score = self.greeks_analyzer.score_greeks_quality(
                contract_type, action, entry_greeks
            )
            
            # Get volatility regime score
            regime, regime_stats = self.volatility_detector.detect_regime()
            regime_strategy = self.volatility_detector.get_regime_strategy(regime)
            regime_score = 0.8 if regime_strategy.get('preferred_action') == action else 0.5
            
            # Get contract type score
            preferred_ct = self.contract_tracker.get_preferred_contract_type(symbol)
            contract_score = 0.9 if contract_type == preferred_ct else 0.6
            
            # Combined score
            total_score = (
                (greeks_score * 0.40) +
                (regime_score * 0.30) +
                (contract_score * 0.30)
            )
            
            scored_alerts.append({
                'alert': alert,
                'score': total_score,
                'greeks_score': greeks_score,
                'regime_score': regime_score,
                'contract_score': contract_score,
                'regime': regime,
            })
        
        # Sort by score
        scored_alerts.sort(key=lambda x: x['score'], reverse=True)
        
        # Return top N
        selected = [sa['alert'] for sa in scored_alerts[:max_trades]]
        
        # Record ranking for analysis
        self.alert_ranking_history.append({
            'timestamp': datetime.now(),
            'total_alerts': len(alerts),
            'selected': len(selected),
            'top_scores': [sa['score'] for sa in scored_alerts[:max_trades]],
        })
        
        return selected
    
    def eod_learning_update(self, daily_trades: List[Dict[str, Any]]) -> None:
        """
        Update learning models with today's trade results
        Called at end of day
        
        CRITICAL: All trades indexed by underlying (not full symbol) for data persistence
        across contract expirations. After 30 days when contract expires, underlying
        remains the same so learning persists.
        """
        for trade in daily_trades:
            # Record Greeks impact
            if 'entry_greeks' in trade and 'exit_greeks' in trade:
                self.greeks_analyzer.record_greek_trade(
                    contract_type=trade.get('contract_type', 'CE'),
                    action=trade.get('action', 'BUY'),
                    entry_greeks=trade.get('entry_greeks', {}),
                    exit_greeks=trade.get('exit_greeks', {}),
                    profit=trade.get('profit', 0.0),
                    won=trade.get('profit', 0.0) > 0,
                )
            
            # Record volatility regime performance
            if 'regime' in trade:
                self.volatility_detector.record_regime_trade(
                    regime=trade['regime'],
                    won=trade.get('profit', 0.0) > 0,
                    profit=trade.get('profit', 0.0),
                )
            
            # Record contract type performance (uses underlying - CORRECT)
            if 'contract_type' in trade:
                self.contract_tracker.record_contract_trade(
                    underlying=trade.get('underlying', ''),
                    contract_type=trade['contract_type'],
                    action=trade.get('action', 'BUY'),
                    won=trade.get('profit', 0.0) > 0,
                    profit=trade.get('profit', 0.0),
                )
            
            # Record strike performance (use underlying, NOT full symbol - CRITICAL FIX)
            if 'strike_type' in trade:
                self.strike_optimizer.record_strike_trade(
                    symbol=trade.get('underlying', ''),  # Use underlying, not full symbol!
                    strike_type=trade['strike_type'],
                    action=trade.get('action', 'BUY'),
                    won=trade.get('profit', 0.0) > 0,
                    profit=trade.get('profit', 0.0),
                )
        
        # Save models to disk
        self.save_models()


# Global instance
_learning_engine = None

def get_learning_engine() -> OptionsHybridLearningEngine:
    """Get or create global learning engine instance"""
    global _learning_engine
    if _learning_engine is None:
        _learning_engine = OptionsHybridLearningEngine()
    return _learning_engine
