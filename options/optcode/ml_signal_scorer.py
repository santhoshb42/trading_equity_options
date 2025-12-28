"""
ML Signal Scorer for Options

Extracts features from signals and predicts win probability using trained models.

Features extracted:
1. Signal confidence (TradingView alert confidence)
2. Symbol reputation (recent win rate for symbol)
3. Market conditions (IV, volume, spread)
4. Time factors (time of day, day of week)
5. Options-specific (Greeks, theta decay, moneyness)

Models used:
- RandomForest (primary - fast, interpretable)
- GradientBoosting (secondary - ensemble)
- SVM (tertiary - decision boundary)
"""

import json
import pickle
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
import numpy as np

from .optlogging import logger, log_event
from .options_learning_engine import get_symbol_tracker, LearningConfig

try:
    from .optconfig import MLConfig
except ImportError:
    MLConfig = None

try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    logger.warning("SKLEARN_IMPORT: Failed to import scikit-learn")

# =============================================================================
# Configuration
# =============================================================================

class MLScorerConfig:
    """Configuration for ML signal scoring"""
    
    # Model paths
    MODELS_DIR = Path("data/ml_models")
    RANDOM_FOREST_MODEL = MODELS_DIR / "random_forest_options.pkl"
    GRADIENT_BOOSTING_MODEL = MODELS_DIR / "gradient_boosting_options.pkl"
    SCALER_MODEL = MODELS_DIR / "feature_scaler.pkl"
    
    # Feature names (must match training)
    FEATURE_NAMES = [
        'confidence',           # TradingView confidence (0-100)
        'score',               # Alert score (0-100)
        'symbol_reputation',   # Symbol win rate (-1 to 1)
        'time_of_day',         # Hours since market open (0-6)
        'day_of_week',         # Day of week (0-4 for Mon-Fri)
        'iv_percentile',       # IV percentile (0-100)
        'iv_extreme',          # Is IV extreme? (0-1)
        'volume_zscore',       # Volume z-score (-3 to 3)
        'spread_quality',      # Tight spread = 1, wide = 0
        'pcr_ratio',           # Put-call ratio (0-2)
        'recent_volatility',   # 20-candle volatility (0-5)
        'symbol_form_hot',     # Is symbol hot? (0-1)
        'symbol_form_cold',    # Is symbol cold? (0-1)
        'premium_momentum',     # Premium trending up/down (-1 to 1)
        'days_to_expiry',      # Days remaining (0-30)
    ]
    
    # Model weights for ensemble voting (loaded from config)
    RANDOM_FOREST_WEIGHT = MLConfig.MODEL_WEIGHTS['random_forest'] if MLConfig else 0.5
    GRADIENT_BOOSTING_WEIGHT = MLConfig.MODEL_WEIGHTS['gradient_boosting'] if MLConfig else 0.3
    SVM_WEIGHT = MLConfig.MODEL_WEIGHTS['svm'] if MLConfig else 0.2
    
    # Confidence adjustment (loaded from config)
    ML_SCORE_MIN = MLConfig.ML_SCORE_MIN if MLConfig else 0.3  # 30% floor
    ML_SCORE_MAX = MLConfig.ML_SCORE_MAX if MLConfig else 0.85  # 85% ceiling (never predict too high)
    
    # Feature defaults (loaded from config)
    DEFAULT_IV_PERCENTILE = MLConfig.DEFAULT_IV_PERCENTILE if MLConfig else 50
    DEFAULT_VOLATILITY = MLConfig.DEFAULT_VOLATILITY if MLConfig else 1.0


# =============================================================================
# Feature Extractor
# =============================================================================

class OptionsFeatureExtractor:
    """
    Extract ML features from signals and market data.
    """
    
    def __init__(self):
        self.logger = logger
    
    @staticmethod
    def extract_features(alert: Dict[str, Any], 
                        symbol: str,
                        live_market_data: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        """
        Extract features from alert and market data.
        
        Args:
            alert: TradingView alert data
            symbol: Stock symbol (BANKNIFTY, NIFTY, FINNIFTY)
            live_market_data: Current market data (IV, volume, Greeks, etc.)
        
        Returns:
            Dictionary of feature values
        """
        tracker = get_symbol_tracker()
        
        features = {}
        
        # 1. Signal features
        features['confidence'] = float(alert.get('confidence', 50))
        features['score'] = float(alert.get('score', 50))
        
        # 2. Symbol reputation (learn from historical trades)
        symbol_stats = tracker.get_symbol_stats(symbol)
        if symbol_stats and symbol_stats['total_trades'] > 0:
            # Normalize win rate to -1..1 (0.5 wr = 0, 1.0 wr = 1, 0 wr = -1)
            features['symbol_reputation'] = (symbol_stats['win_rate'] - 0.5) * 2
        else:
            features['symbol_reputation'] = 0.0
        
        # 3. Time of day features
        now = datetime.now()
        hours_since_open = (now.hour - 9) + (now.minute / 60)  # Market opens at 9:15
        features['time_of_day'] = max(0, min(6, hours_since_open))  # 0-6 hours
        features['day_of_week'] = now.weekday()  # 0=Monday, 4=Friday
        
        # 4. Market data features (from live_market_data or defaults)
        market_data = live_market_data or {}
        
        features['iv_percentile'] = float(market_data.get('iv_percentile', MLScorerConfig.DEFAULT_IV_PERCENTILE))
        # IV extreme: if < 20 or > 80, it's extreme
        features['iv_extreme'] = 1.0 if features['iv_percentile'] < 20 or features['iv_percentile'] > 80 else 0.0
        
        # Volume z-score (if available)
        volume = float(market_data.get('volume', 0))
        avg_volume = float(market_data.get('avg_volume', 1))
        if avg_volume > 0:
            volume_zscore = (volume - avg_volume) / max(1, avg_volume * 0.5)
        else:
            volume_zscore = 0.0
        features['volume_zscore'] = np.clip(volume_zscore, -3, 3)
        
        # Spread quality: 1 if tight, 0 if wide
        bid_ask_spread = float(market_data.get('bid_ask_spread', 0))
        avg_spread = float(market_data.get('avg_spread', 1))
        if avg_spread > 0:
            spread_ratio = bid_ask_spread / avg_spread
            features['spread_quality'] = 1.0 / (1.0 + spread_ratio)  # Logistic: tight=1, wide=0
        else:
            features['spread_quality'] = 0.5
        
        # PCR ratio
        features['pcr_ratio'] = float(market_data.get('pcr_ratio', 1.0))
        
        # Recent volatility
        features['recent_volatility'] = float(market_data.get('recent_volatility', MLScorerConfig.DEFAULT_VOLATILITY))
        features['recent_volatility'] = np.clip(features['recent_volatility'], 0, 5)
        
        # 5. Symbol form (from learning engine)
        form = tracker.get_form(symbol)
        features['symbol_form_hot'] = 1.0 if form == 'hot' else 0.0
        features['symbol_form_cold'] = 1.0 if form == 'cold' else 0.0
        
        # 6. Premium momentum (if available)
        features['premium_momentum'] = float(market_data.get('premium_momentum', 0.0))
        features['premium_momentum'] = np.clip(features['premium_momentum'], -1, 1)
        
        # 7. Days to expiry
        expiry = market_data.get('expiry')
        if expiry:
            try:
                expiry_date = datetime.fromisoformat(expiry)
                days_left = (expiry_date - now).days
                features['days_to_expiry'] = min(30, max(0, days_left))
            except:
                features['days_to_expiry'] = 7  # Default
        else:
            features['days_to_expiry'] = 7
        
        return features
    
    @staticmethod
    def normalize_features(features: Dict[str, float], 
                          scaler: Optional[Any] = None) -> np.ndarray:
        """
        Convert feature dict to normalized numpy array.
        
        Args:
            features: Feature dictionary
            scaler: StandardScaler fitted on training data
        
        Returns:
            Normalized feature array
        """
        # Extract in order
        feature_values = [features.get(name, 0.0) for name in MLScorerConfig.FEATURE_NAMES]
        feature_array = np.array(feature_values).reshape(1, -1)
        
        # Normalize if scaler provided
        if scaler is not None:
            try:
                feature_array = scaler.transform(feature_array)
            except Exception as e:
                logger.warning(f"FEATURE_NORM: SCALER_ERROR | {str(e)}")
        
        return feature_array


# =============================================================================
# ML Signal Scorer
# =============================================================================

class MLSignalScorer:
    """
    Scores signals using trained ML models.
    Provides win probability prediction.
    """
    
    def __init__(self):
        self.feature_extractor = OptionsFeatureExtractor()
        self.logger = logger
        
        # Load models if available
        self.random_forest = self._load_model(MLScorerConfig.RANDOM_FOREST_MODEL)
        self.gradient_boosting = self._load_model(MLScorerConfig.GRADIENT_BOOSTING_MODEL)
        self.scaler = self._load_model(MLScorerConfig.SCALER_MODEL)
        
        self.models_available = bool(self.random_forest or self.gradient_boosting)
        
        if self.models_available:
            logger.info("ML_SCORER: MODELS_LOADED | ready for predictions")
        else:
            logger.warning("ML_SCORER: NO_MODELS | using fallback scoring")
    
    def score_signal(self, alert: Dict[str, Any],
                    symbol: str,
                    live_market_data: Optional[Dict[str, Any]] = None,
                    current_confidence: float = 90.0) -> Dict[str, Any]:
        """
        Score a signal and return prediction.
        
        Returns:
            {
                'win_probability': 0.0-1.0,
                'confidence_adjusted': 0-100,
                'model_used': 'ensemble'|'fallback',
                'feature_importance': {...},
                'reasoning': 'explanation'
            }
        """
        try:
            # Extract features
            features = self.feature_extractor.extract_features(alert, symbol, live_market_data)
            
            logger.debug(f"ML_SCORER: FEATURES_EXTRACTED | {symbol} | confidence={features['confidence']:.0f}% | score={features['score']:.0f}%")
            
            # Get prediction
            if self.models_available:
                win_prob = self._ensemble_predict(features)
                model_used = 'ensemble'
            else:
                win_prob = self._fallback_score(features)
                model_used = 'fallback'
            
            # Clamp to safe range
            win_prob = np.clip(win_prob, MLScorerConfig.ML_SCORE_MIN, MLScorerConfig.ML_SCORE_MAX)
            
            # Adjust original confidence
            # Formula: new_conf = orig_conf * (ml_prob / 0.5)
            # If ML predicts 75% win, boost by 50%
            # If ML predicts 25% win, reduce by 50%
            confidence_multiplier = win_prob / 0.5
            confidence_adjusted = current_confidence * confidence_multiplier
            confidence_adjusted = np.clip(confidence_adjusted, 0, 100)
            
            logger.info(f"ML_SCORER: PREDICTION | {symbol} | win_prob={win_prob:.1%} | conf_adjusted={confidence_adjusted:.0f}%")
            
            return {
                'win_probability': float(win_prob),
                'confidence_adjusted': float(confidence_adjusted),
                'model_used': model_used,
                'feature_values': features,
                'confidence_multiplier': float(confidence_multiplier)
            }
        
        except Exception as e:
            logger.error(f"ML_SCORER: ERROR | {str(e)}", exc_info=True)
            # Fallback: return original confidence
            return {
                'win_probability': 0.5,
                'confidence_adjusted': current_confidence,
                'model_used': 'error',
                'error': str(e)
            }
    
    def _ensemble_predict(self, features: Dict[str, float]) -> float:
        """
        Predict using ensemble of models.
        Combines RandomForest + GradientBoosting predictions.
        """
        predictions = []
        weights = []
        
        # Normalize features
        feature_array = self.feature_extractor.normalize_features(features, self.scaler)
        
        # Random Forest prediction
        if self.random_forest is not None:
            try:
                rf_prob = self.random_forest.predict_proba(feature_array)[0][1]
                predictions.append(rf_prob)
                weights.append(MLScorerConfig.RANDOM_FOREST_WEIGHT)
                logger.debug(f"ML_SCORER: RF_PROB | {rf_prob:.1%}")
            except Exception as e:
                logger.warning(f"ML_SCORER: RF_ERROR | {str(e)}")
        
        # Gradient Boosting prediction
        if self.gradient_boosting is not None:
            try:
                gb_prob = self.gradient_boosting.predict_proba(feature_array)[0][1]
                predictions.append(gb_prob)
                weights.append(MLScorerConfig.GRADIENT_BOOSTING_WEIGHT)
                logger.debug(f"ML_SCORER: GB_PROB | {gb_prob:.1%}")
            except Exception as e:
                logger.warning(f"ML_SCORER: GB_ERROR | {str(e)}")
        
        # Weighted average
        if predictions:
            ensemble_prob = np.average(predictions, weights=weights[:len(predictions)])
            return float(ensemble_prob)
        else:
            return 0.5  # Neutral if no models work
    
    def _fallback_score(self, features: Dict[str, float]) -> float:
        """
        Fallback scoring when models not available.
        Uses simple heuristics based on features.
        """
        score = 0.5  # Start at neutral
        
        # Weight signal quality
        confidence_factor = (features['confidence'] - 50) / 100  # -0.5 to 0.5
        score += confidence_factor * 0.2
        
        # Weight symbol reputation
        symbol_factor = features['symbol_reputation'] * 0.15
        score += symbol_factor
        
        # Weight recent form
        if features['symbol_form_hot']:
            score += 0.1
        elif features['symbol_form_cold']:
            score -= 0.1
        
        # Penalize extreme IV
        if features['iv_extreme']:
            score -= 0.05
        
        # Reward good volume
        if features['volume_zscore'] > 1:
            score += 0.1
        
        # Clamp
        return np.clip(score, 0.1, 0.9)
    
    @staticmethod
    def _load_model(model_path: Path) -> Optional[Any]:
        """Load pickled model from disk"""
        try:
            if not model_path.exists():
                return None
            
            with open(model_path, 'rb') as f:
                model = pickle.load(f)
            
            logger.debug(f"ML_SCORER: MODEL_LOADED | {model_path.name}")
            return model
        except Exception as e:
            logger.warning(f"ML_SCORER: LOAD_ERROR | {model_path.name} | {str(e)}")
            return None


# =============================================================================
# Global scorer instance
# =============================================================================

_ml_scorer = None

def get_ml_scorer() -> MLSignalScorer:
    """Get or create ML scorer"""
    global _ml_scorer
    if _ml_scorer is None:
        _ml_scorer = MLSignalScorer()
    return _ml_scorer
