"""
ML Integration for Options Bot API

Integrates the ML learning engine and advanced signal filtering
into the options trading bot API.

Features:
- Greeks-aware alert ranking and selection
- Volatility regime adaptive strategy
- Real-time PoP (Probability of Profit) calculation
- EOD learning updates
- Continuous model improvement
"""

import json
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional

try:
    from .opt_hybrid_learning_engine import (
        get_learning_engine,
        OptionsHybridLearningEngine
    )
    from .opt_ml_signal_filter import (
        get_options_signal_filter,
        OptionsSignalQualityFilter,
        ProbabilityOfProfitCalculator,
        VolatilityPercentileValidator,
    )
    from .ce_extractor import extract_underlying_from_symbol
    from .optconfig import MLConfig
    HAS_ML = True
except ImportError as e:
    print(f"Warning: Could not import ML modules: {e}")
    HAS_ML = False
    get_learning_engine = None
    get_options_signal_filter = None

try:
    from .optlogging import logger, log_event
except Exception:
    def logger(*args, **kwargs):
        print(*args, **kwargs)
    def log_event(*args, **kwargs):
        pass

try:
    from .optconfig import OptionsTradingConfig
except ImportError:
    class OptionsTradingConfig:
        TRADING_MODE = "PAPER"


class MLIntegration:
    """
    Bridges ML components with options bot API
    
    Tracks trading mode (PAPER/LIVE) for all learning and model updates.
    Ensures seamless transition from paper to live trading.
    """
    
    def __init__(self):
        self.learning_engine = get_learning_engine() if HAS_ML else None
        self.signal_filter = get_options_signal_filter() if HAS_ML else None
        self.pop_calculator = ProbabilityOfProfitCalculator()
        self.iv_validator = VolatilityPercentileValidator()
        
        self.daily_trades = []
        self.trading_mode = getattr(OptionsTradingConfig, 'TRADING_MODE', 'PAPER')
        
        logger.info(f"ML_INTEGRATION: INITIALIZED | mode={self.trading_mode}")
    
    def enrich_alert_with_ml(self, alert: Dict[str, Any],
                            greeks: Dict[str, float] = None,
                            underlying_price: float = 0.0,
                            current_iv: float = 0.0) -> Dict[str, Any]:
        """
        Enrich alert with ML analysis
        
        CRITICAL: Extracts underlying from symbol to preserve learning across
        contract expirations. All ML data indexed by underlying, not full symbol.
        
        Adds:
        - Greeks quality score
        - Volatility regime and suitability
        - Probability of Profit
        - Strike optimization recommendation
        - ML confidence score
        """
        if not HAS_ML:
            return alert
        
        enriched = alert.copy()
        
        try:
            # Extract underlying from symbol for data persistence
            symbol = alert.get('symbol', '')
            underlying = extract_underlying_from_symbol(symbol) if symbol else ''
            enriched['underlying'] = underlying  # CRITICAL: Add underlying to alert
            
            # Calculate Greeks quality score
            if greeks:
                contract_type = alert.get('contract_type', 'CE')
                action = alert.get('action', 'BUY')
                greeks_score = self.learning_engine.greeks_analyzer.score_greeks_quality(
                    contract_type, action, greeks
                )
                enriched['ml_greeks_score'] = greeks_score
            
            # Get volatility regime
            regime, regime_stats = self.learning_engine.volatility_detector.detect_regime()
            enriched['ml_regime'] = regime
            enriched['ml_regime_stats'] = regime_stats
            
            # Validate IV for action
            if current_iv > 0:
                iv_percentile = self.iv_validator.calculate_iv_percentile(current_iv)
                enriched['ml_iv_percentile'] = iv_percentile
                
                iv_valid, iv_msg = self.iv_validator.validate_iv_for_action(
                    alert.get('action', 'BUY'), iv_percentile
                )
                enriched['ml_iv_suitable'] = iv_valid
            
            # Calculate PoP
            if underlying_price > 0 and alert.get('strike', 0) > 0:
                pop = self.pop_calculator.calculate_pop(
                    underlying_price,
                    alert['strike'],
                    contract_type,
                    action
                )
                enriched['ml_pop'] = pop
            
            # Get strike recommendation (use underlying, not full symbol)
            if underlying:
                preferred_strike = self.learning_engine.strike_optimizer.get_optimal_strike(
                    underlying, action, []
                )
                enriched['ml_preferred_strike'] = preferred_strike
            
            # Get contract type preference (already uses underlying)
            if underlying:
                preferred_ct = self.learning_engine.contract_tracker.get_preferred_contract_type(
                    underlying
                )
                enriched['ml_preferred_contract'] = preferred_ct
            
            # Calculate combined ML confidence
            ml_confidence = self._calculate_ml_confidence(enriched)
            enriched['ml_confidence'] = ml_confidence
            
            return enriched
        
        except Exception as e:
            logger.error(f"ML_ENRICHMENT_ERROR: {str(e)}")
            return alert
    
    def _calculate_ml_confidence(self, enriched_alert: Dict[str, Any]) -> float:
        """
        Calculate overall ML confidence in the alert (0.0 to 1.0)
        
        Combines multiple factors using configurable weights:
        - Greeks quality (default 35%)
        - Volatility regime fit (default 25%)
        - PoP (default 25%)
        - Contract type alignment (default 15%)
        
        All weights loaded from MLConfig for easy tuning
        """
        confidence = 0.0
        total_weight = 0.0
        
        # Load weights from config
        greeks_weight = MLConfig.CONFIDENCE_WEIGHTS.get('greeks_quality', 0.35)
        regime_weight = MLConfig.CONFIDENCE_WEIGHTS.get('volatility_regime', 0.25)
        pop_weight = MLConfig.CONFIDENCE_WEIGHTS.get('probability_of_profit', 0.25)
        contract_weight = MLConfig.CONFIDENCE_WEIGHTS.get('contract_type_alignment', 0.15)
        
        # Greeks quality
        if 'ml_greeks_score' in enriched_alert:
            confidence += enriched_alert['ml_greeks_score'] * greeks_weight
            total_weight += greeks_weight
        
        # Regime fit
        if 'ml_regime' in enriched_alert and enriched_alert.get('ml_iv_suitable'):
            confidence += MLConfig.HIGH_CONFIDENCE_FALLBACK * regime_weight  # Good fit if IV suitable
            total_weight += regime_weight
        elif 'ml_regime' in enriched_alert:
            confidence += MLConfig.MEDIUM_CONFIDENCE_FALLBACK * regime_weight  # Medium fit otherwise
            total_weight += regime_weight
        
        # PoP
        if 'ml_pop' in enriched_alert:
            pop = enriched_alert['ml_pop']
            # Normalize PoP to 0-1 (50% PoP = 0.5 confidence)
            pop_confidence = pop / 100.0
            confidence += pop_confidence * pop_weight
            total_weight += pop_weight
        
        # Contract type alignment
        if 'ml_preferred_contract' in enriched_alert:
            actual_ct = enriched_alert.get('contract_type', '')
            preferred_ct = enriched_alert['ml_preferred_contract']
            match = 1.0 if actual_ct == preferred_ct else 0.5
            confidence += match * contract_weight
            total_weight += contract_weight
        
        return confidence / total_weight if total_weight > 0 else MLConfig.DEFAULT_CONFIDENCE
    
    def rank_alerts_by_ml(self, alerts: List[Dict[str, Any]],
                         max_trades: int = 3) -> List[Dict[str, Any]]:
        """
        Rank and select alerts using ML engine
        
        Returns top N alerts sorted by ML confidence
        Uses MLConfig.MAX_TRADES_PER_ML_CHECK if not specified
        """
        if not HAS_ML or not self.learning_engine:
            # Fallback to simple sorting
            return alerts[:max_trades]
        
        # Use config max if not overridden
        if max_trades <= 0:
            max_trades = MLConfig.MAX_TRADES_PER_ML_CHECK
        
        try:
            # Enrich all alerts
            enriched_alerts = [
                self.enrich_alert_with_ml(alert)
                for alert in alerts
            ]
            
            # Sort by ML confidence
            enriched_alerts.sort(
                key=lambda a: a.get('ml_confidence', MLConfig.DEFAULT_CONFIDENCE),
                reverse=True
            )
            
            # Log ranking
            logger.info(f"ML_RANKING: Total={len(alerts)} | "
                       f"Selected={min(max_trades, len(alerts))} | "
                       f"Top_Confidences={[a.get('ml_confidence', 0) for a in enriched_alerts[:max_trades]]}")
            
            return enriched_alerts[:max_trades]
        
        except Exception as e:
            logger.error(f"ML_RANKING_ERROR: {str(e)}")
            return alerts[:max_trades]
    
    def validate_with_ml_filter(self, alert: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validate alert using ML signal filter
        
        Returns:
            (is_valid, reason, details)
        """
        if not HAS_ML or not self.signal_filter:
            return True, "ML filter disabled", {}
        
        try:
            is_valid, reason, details = self.signal_filter.validate_signal(alert)
            
            if is_valid:
                logger.debug(f"ML_FILTER_PASSED: symbol={alert.get('symbol')} | "
                            f"pop={details.get('final_pop', 0):.1f}%")
            else:
                logger.warning(f"ML_FILTER_REJECTED: symbol={alert.get('symbol')} | "
                              f"reason={reason}")
            
            return is_valid, reason, details
        
        except Exception as e:
            logger.error(f"ML_FILTER_ERROR: {str(e)}")
            return True, "ML filter error", {}
    
    def record_daily_trade(self, trade: Dict[str, Any]) -> None:
        """
        Record a trade for daily learning update
        
        Args:
            trade: dict with symbol, action, profit, greeks_entry, greeks_exit, etc.
        
        Automatically adds:
        - trading_mode: Current trading mode (PAPER/LIVE)
        - recorded_at: Timestamp when trade was recorded
        """
        # Add mode and timestamp
        trade['trading_mode'] = self.trading_mode
        trade['recorded_at'] = datetime.now().isoformat()
        
        self.daily_trades.append(trade)
        
        logger.debug(f"TRADE_RECORDED: {trade.get('symbol', 'UNKNOWN')} | mode={self.trading_mode} | pnl=₹{trade.get('profit', 0):.2f}")
    
    def run_eod_learning_update(self) -> Dict[str, Any]:
        """
        Run end-of-day learning update
        Called after market close to update all learning models
        
        Returns:
            summary of updates made
        """
        if not HAS_ML or not self.learning_engine:
            return {'status': 'disabled'}
        
        try:
            summary = {
                'trades_processed': len(self.daily_trades),
                'models_updated': [],
            }
            
            if len(self.daily_trades) > 0:
                # Update learning engine
                self.learning_engine.eod_learning_update(self.daily_trades)
                summary['models_updated'] = [
                    'greeks_analyzer',
                    'volatility_detector',
                    'strike_optimizer',
                    'contract_tracker',
                ]
                
                logger.info(f"EOD_LEARNING: Updated {len(self.daily_trades)} trades | "
                           f"Models: {summary['models_updated']}")
            
            # Reset daily trades
            self.daily_trades = []
            
            return summary
        
        except Exception as e:
            logger.error(f"EOD_LEARNING_ERROR: {str(e)}")
            return {'status': 'error', 'message': str(e)}
    
    def get_ml_stats(self) -> Dict[str, Any]:
        """Get comprehensive ML statistics"""
        if not HAS_ML:
            return {'status': 'disabled'}
        
        stats = {
            'signal_filter_stats': self.signal_filter.get_filter_stats() if self.signal_filter else {},
            'daily_trades_pending': len(self.daily_trades),
        }
        
        # Add learning engine stats
        if self.learning_engine:
            stats['greeks_stats'] = {
                key: self.learning_engine.greeks_analyzer.get_greeks_stats(
                    key.split('_')[0], key.split('_')[1]
                )
                for key in self.learning_engine.greeks_analyzer.greek_stats.keys()
            }
            
            stats['contract_performance'] = {
                underlying: self.learning_engine.contract_tracker.get_contract_stats(underlying)
                for underlying in list(self.learning_engine.contract_tracker.contract_performance.keys())[:10]
            }
        
        return stats


# Global instance
_ml_integration = None

def get_ml_integration() -> MLIntegration:
    """Get or create global ML integration instance"""
    global _ml_integration
    if _ml_integration is None:
        _ml_integration = MLIntegration()
    return _ml_integration
