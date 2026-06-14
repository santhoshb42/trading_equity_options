"""
ML-Based Signal Filtering Module

Advanced machine learning ensemble for signal quality filtering:
- Pattern recognition from historical trades
- Feature extraction and normalization
- Multiple model voting (Random Forest, Gradient Boosting, SVM)
- Adaptive thresholds based on performance
- Real-time signal scoring
"""

import json
import pickle
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from collections import deque
import numpy as np
from scipy import stats

# ML libraries
try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


def log_event(*args, **kwargs):
    """Stub for logging integration"""
    pass


class FeatureExtractor:
    """Extract trading features from historical data"""
    
    def __init__(self, lookback_periods: int = 50):
        self.lookback_periods = lookback_periods
        self.price_history = {}
        self.volume_history = {}
        self.rsi_history = {}
        self.trend_history = {}
    
    def add_data_point(self, symbol: str, price: float, volume: int, 
                       rsi: float = None, trend: str = "neutral") -> None:
        """Record price data for feature calculation"""
        if symbol not in self.price_history:
            self.price_history[symbol] = deque(maxlen=self.lookback_periods)
            self.volume_history[symbol] = deque(maxlen=self.lookback_periods)
            self.rsi_history[symbol] = deque(maxlen=self.lookback_periods)
            self.trend_history[symbol] = deque(maxlen=self.lookback_periods)
        
        self.price_history[symbol].append(price)
        self.volume_history[symbol].append(volume)
        if rsi:
            self.rsi_history[symbol].append(rsi)
        self.trend_history[symbol].append(trend)
    
    def extract_features(self, symbol: str, entry_price: float, 
                        alert_confidence: float = 0.5) -> Dict[str, float]:
        """
        Extract 15+ features for ML model
        
        Price Features:
        - Momentum: recent price trend strength
        - Volatility: standard deviation of returns
        - Mean reversion: distance from average
        
        Volume Features:
        - Volume trend: increasing/decreasing
        - Volume spike: abnormal volume
        
        Technical Features:
        - RSI extremes
        - Trend consistency
        - Support/resistance proximity
        """
        features = {}
        
        if symbol not in self.price_history or len(self.price_history[symbol]) < 5:
            return self._get_neutral_features()
        
        prices = list(self.price_history[symbol])
        volumes = list(self.volume_history[symbol])
        
        # 1. Price Momentum (3-period and 5-period)
        if len(prices) >= 3:
            momentum_3 = (prices[-1] - prices[-3]) / prices[-3]
            features['momentum_3'] = momentum_3
        
        if len(prices) >= 5:
            momentum_5 = (prices[-1] - prices[-5]) / prices[-5]
            features['momentum_5'] = momentum_5
        
        # 2. Volatility (rolling std)
        if len(prices) >= 5:
            returns = np.diff(prices) / prices[:-1]
            features['volatility'] = np.std(returns) * 100
        
        # 3. Mean Reversion (distance from SMA)
        sma_20 = np.mean(prices[-min(20, len(prices)):])
        features['mean_reversion'] = (prices[-1] - sma_20) / sma_20 * 100
        
        # 4. Volume Trend
        if len(volumes) >= 3:
            vol_sma = np.mean(volumes[-3:])
            features['volume_trend'] = volumes[-1] / vol_sma if vol_sma > 0 else 1.0
        
        # 5. Volume Spike Detection
        if len(volumes) >= 10:
            vol_mean = np.mean(volumes[-10:-1])
            features['volume_spike'] = (volumes[-1] - vol_mean) / vol_mean if vol_mean > 0 else 0.0
        
        # 6. RSI Features (if available)
        if symbol in self.rsi_history and len(self.rsi_history[symbol]) > 0:
            rsi = list(self.rsi_history[symbol])[-1]
            features['rsi'] = rsi
            features['rsi_extreme'] = 1.0 if rsi < 30 or rsi > 70 else 0.0
        else:
            features['rsi'] = 50.0
            features['rsi_extreme'] = 0.0
        
        # 7. Trend Consistency
        if symbol in self.trend_history:
            trends = list(self.trend_history[symbol])[-5:]
            up_count = sum(1 for t in trends if t == "uptrend")
            features['trend_consistency'] = up_count / len(trends) if trends else 0.5
        
        # 8. Entry Price Quality (proximity to support)
        highest_price = max(prices[-min(20, len(prices)):])
        features['price_from_high'] = (highest_price - prices[-1]) / highest_price * 100
        
        # 9. Alert Confidence
        features['alert_confidence'] = alert_confidence
        
        # 10. Time-based features
        features['market_hour'] = datetime.now().hour
        features['is_market_open'] = 1.0 if 9 <= datetime.now().hour <= 15 else 0.0
        
        return features
    
    def _get_neutral_features(self) -> Dict[str, float]:
        """Return neutral feature set when insufficient data"""
        return {
            'momentum_3': 0.0,
            'momentum_5': 0.0,
            'volatility': 1.0,
            'mean_reversion': 0.0,
            'volume_trend': 1.0,
            'volume_spike': 0.0,
            'rsi': 50.0,
            'rsi_extreme': 0.0,
            'trend_consistency': 0.5,
            'price_from_high': 10.0,
            'alert_confidence': 0.5,
            'market_hour': 12,
            'is_market_open': 1.0
        }


class EnsembleSignalModel:
    """Ensemble ML model combining multiple classifiers"""
    
    def __init__(self, model_dir: str = "/tmp/ml_models"):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(exist_ok=True)
        
        self.rf_model = None
        self.gb_model = None
        self.svm_model = None
        self.scaler = StandardScaler()
        
        self.is_trained = False
        self.feature_names = [
            'momentum_3', 'momentum_5', 'volatility', 'mean_reversion',
            'volume_trend', 'volume_spike', 'rsi', 'rsi_extreme',
            'trend_consistency', 'price_from_high', 'alert_confidence',
            'market_hour', 'is_market_open'
        ]
        
        self.training_data = []
        self.training_labels = []
        
        self._load_models()
    
    def add_training_sample(self, features: Dict[str, float], label: int) -> None:
        """
        Add labeled sample to training set
        label: 1 = winning trade, 0 = losing trade
        """
        feature_values = [features.get(f, 0.0) for f in self.feature_names]
        self.training_data.append(feature_values)
        self.training_labels.append(label)
        
        # Auto-train when sufficient data
        if len(self.training_data) >= 30 and len(self.training_data) % 10 == 0:
            self.train_models()
    
    def train_models(self) -> bool:
        """Train ensemble with collected data"""
        if len(self.training_data) < 10:
            log_event("ML", "Insufficient training data", {"count": len(self.training_data)})
            return False
        
        try:
            X = np.array(self.training_data)
            y = np.array(self.training_labels)
            
            # Normalize features
            X_scaled = self.scaler.fit_transform(X)
            
            # Train Random Forest
            self.rf_model = RandomForestClassifier(n_estimators=50, max_depth=10)
            self.rf_model.fit(X_scaled, y)
            
            # Train Gradient Boosting
            self.gb_model = GradientBoostingClassifier(n_estimators=50, max_depth=5)
            self.gb_model.fit(X_scaled, y)
            
            # Train SVM
            self.svm_model = SVC(kernel='rbf', probability=True)
            self.svm_model.fit(X_scaled, y)
            
            self.is_trained = True
            self._save_models()
            
            log_event("ML", "Models trained", {
                "samples": len(self.training_data),
                "win_rate": np.mean(y)
            })
            
            return True
            
        except Exception as e:
            log_event("ML", f"Training failed: {e}")
            return False
    
    def predict_signal_quality(self, features: Dict[str, float]) -> Tuple[float, Dict[str, Any]]:
        """
        Predict signal quality (0.0 - 1.0)
        
        Returns:
            - quality_score: 0.0 (bad) to 1.0 (excellent)
            - details: Model voting breakdown
        """
        if not self.is_trained:
            return self._baseline_scoring(features)
        
        try:
            feature_values = np.array([features.get(f, 0.0) for f in self.feature_names])
            X_scaled = self.scaler.transform([feature_values])
            
            # Get predictions from all models
            rf_prob = self.rf_model.predict_proba(X_scaled)[0][1]
            gb_prob = self.gb_model.predict_proba(X_scaled)[0][1]
            svm_prob = self.svm_model.predict_proba(X_scaled)[0][1]
            
            # Ensemble voting (weighted average)
            ensemble_score = (rf_prob * 0.4 + gb_prob * 0.4 + svm_prob * 0.2)
            
            # Adjust by confidence and RSI
            confidence_boost = features.get('alert_confidence', 0.5) * 0.1
            rsi_extreme_boost = features.get('rsi_extreme', 0.0) * 0.1
            
            final_score = min(1.0, ensemble_score + confidence_boost + rsi_extreme_boost)
            
            return final_score, {
                'rf_score': float(rf_prob),
                'gb_score': float(gb_prob),
                'svm_score': float(svm_prob),
                'ensemble_score': float(ensemble_score),
                'final_score': float(final_score),
                'model_agreement': self._calculate_model_agreement(rf_prob, gb_prob, svm_prob)
            }
            
        except Exception as e:
            log_event("ML", f"Prediction failed: {e}")
            return self._baseline_scoring(features)
    
    def _baseline_scoring(self, features: Dict[str, float]) -> Tuple[float, Dict[str, Any]]:
        """
        Fallback scoring when models not trained
        
        CRITICAL: Default to ACCEPT (0.65+) unless strong negative signals
        This prevents rejecting good new symbols due to lack of data
        """
        # Start optimistic - assume signal is good unless proven bad
        score = 0.65  # Above threshold by default
        
        # PENALTIES (reduce score for negative signals)
        
        # Strong negative momentum penalty
        momentum = features.get('momentum_3', 0.0)
        if momentum < -0.02:  # -2% or worse
            score -= 0.15
        
        # Very low alert confidence penalty
        alert_conf = features.get('alert_confidence', 0.5)
        if alert_conf < 0.3:
            score -= 0.10
        
        # BONUSES (increase score for positive signals)
        
        # Strong positive momentum
        if momentum > 0.01:  # +1% or better
            score += 0.05
        
        # Volume confirmation
        vol_trend = features.get('volume_trend', 1.0)
        if vol_trend > 1.3:  # 30%+ volume increase
            score += 0.05
        
        # RSI extreme (oversold = buying opportunity)
        if features.get('rsi_extreme', 0.0) > 0:
            score += 0.05
        
        # High alert confidence
        if alert_conf > 0.7:
            score += 0.05
        
        final_score = max(0.0, min(1.0, score))
        
        return final_score, {
            'method': 'baseline_optimistic',
            'final_score': final_score,
            'note': 'Defaults to ACCEPT unless negative signals present'
        }
    
    def _calculate_model_agreement(self, rf: float, gb: float, svm: float) -> float:
        """Measure agreement between models (0.0 = disagreement, 1.0 = full agreement)"""
        scores = np.array([rf, gb, svm])
        # Use coefficient of variation (lower = more agreement)
        mean = np.mean(scores)
        std = np.std(scores)
        cv = std / mean if mean > 0 else 0
        return max(0.0, 1.0 - cv)
    
    def _save_models(self) -> None:
        """Save trained models to disk"""
        try:
            if self.rf_model:
                pickle.dump(self.rf_model, open(self.model_dir / 'rf_model.pkl', 'wb'))
            if self.gb_model:
                pickle.dump(self.gb_model, open(self.model_dir / 'gb_model.pkl', 'wb'))
            if self.svm_model:
                pickle.dump(self.svm_model, open(self.model_dir / 'svm_model.pkl', 'wb'))
            pickle.dump(self.scaler, open(self.model_dir / 'scaler.pkl', 'wb'))
            
            log_event("ML", "Models saved")
        except Exception as e:
            log_event("ML", f"Save failed: {e}")
    
    def _load_models(self) -> None:
        """Load trained models from disk"""
        try:
            rf_path = self.model_dir / 'rf_model.pkl'
            gb_path = self.model_dir / 'gb_model.pkl'
            svm_path = self.model_dir / 'svm_model.pkl'
            scaler_path = self.model_dir / 'scaler.pkl'
            
            if all([rf_path.exists(), gb_path.exists(), svm_path.exists(), scaler_path.exists()]):
                self.rf_model = pickle.load(open(rf_path, 'rb'))
                self.gb_model = pickle.load(open(gb_path, 'rb'))
                self.svm_model = pickle.load(open(svm_path, 'rb'))
                self.scaler = pickle.load(open(scaler_path, 'rb'))
                self.is_trained = True
                log_event("ML", "Models loaded")
        except Exception as e:
            log_event("ML", f"Load failed: {e}")


class MLSignalFilter:
    """
    Advanced ML-based signal filtering
    Combines ensemble learning with feature engineering
    """
    
    def __init__(self, min_score_threshold: float = 0.6):
        self.extractor = FeatureExtractor() if HAS_SKLEARN else None
        self.model = EnsembleSignalModel() if HAS_SKLEARN else None
        self.min_score_threshold = min_score_threshold
        
        self.signal_history = {}
        self.accuracy_by_symbol = {}
        self.model_stats = {
            'total_signals': 0,
            'accepted_signals': 0,
            'rejected_signals': 0,
            'accuracy': 0.0
        }
    
    def validate_signal_with_ml(self, symbol: str, alert_data: Dict[str, Any],
                               entry_price: float) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Validate signal using ML ensemble
        
        PHILOSOPHY: 
        - If model is trained and has data → use ML prediction
        - If no data/untrained → DEFAULT TO ACCEPT (learn from new trades)
        - Only reject if ML has strong evidence of poor quality
        
        Returns:
            - is_valid: True if signal passes ML filters
            - confidence: ML model confidence score (0.0-1.0)
            - details: Scoring breakdown
        """
        if not HAS_SKLEARN or not self.model:
            return True, 0.65, {'status': 'sklearn_not_available', 'default': 'accept'}
        
        # Extract features
        alert_confidence = alert_data.get('confidence', 0.5)
        rsi = alert_data.get('technical', {}).get('rsi', 50.0)
        trend = alert_data.get('trend', 'neutral')
        
        # Check if we have historical data for this symbol
        has_history = (symbol in self.extractor.price_history and 
                      len(self.extractor.price_history[symbol]) >= 5)
        
        self.extractor.add_data_point(symbol, entry_price, 
                                     alert_data.get('volume', 1000),
                                     rsi, trend)
        
        features = self.extractor.extract_features(symbol, entry_price, alert_confidence)
        
        # Get ML prediction
        ml_score, details = self.model.predict_signal_quality(features)
        
        # Enhanced validation logic
        if not has_history and not self.model.is_trained:
            # NEW SYMBOL + UNTRAINED MODEL → Accept and learn
            is_valid = True
            ml_score = 0.65  # Override to passing score
            details['override_reason'] = 'new_symbol_learning_mode'
        else:
            # Have data or trained model → trust ML score
            is_valid = ml_score >= self.min_score_threshold
        
        # Track statistics
        self.model_stats['total_signals'] += 1
        if is_valid:
            self.model_stats['accepted_signals'] += 1
        else:
            self.model_stats['rejected_signals'] += 1
        
        # Prepare comprehensive details with individual model scores
        return_details = {
            'status': 'ml_validation_complete',
            'ml_score': ml_score,
            'threshold': self.min_score_threshold,
            'is_valid': is_valid,
            'model_trained': self.model.is_trained,
            'training_samples': len(self.model.training_data) if hasattr(self.model, 'training_data') else 0,
            'features': features,
            'rf_score': details.get('rf_score'),
            'gb_score': details.get('gb_score'),
            'svm_score': details.get('svm_score'),
            'ensemble_score': details.get('ensemble_score'),
            'model_agreement': details.get('model_agreement'),
            'reason': 'New symbol - learning mode' if details.get('override_reason') == 'new_symbol_learning_mode' else (
                'Signal quality below threshold' if not is_valid else 'Signal passed ML validation'
            )
        }
        
        return is_valid, ml_score, return_details
    
    def record_trade_outcome(self, symbol: str, won: bool) -> None:
        """Record trade result for model retraining"""
        if not self.model or not self.extractor:
            return
        
        # Get last known features for this symbol
        if symbol in self.extractor.price_history:
            features = self.extractor.extract_features(symbol, 0.0)
            label = 1 if won else 0
            self.model.add_training_sample(features, label)
            
            # Update symbol accuracy
            if symbol not in self.accuracy_by_symbol:
                self.accuracy_by_symbol[symbol] = {'wins': 0, 'losses': 0}
            
            if won:
                self.accuracy_by_symbol[symbol]['wins'] += 1
            else:
                self.accuracy_by_symbol[symbol]['losses'] += 1
    
    def get_ml_stats(self) -> Dict[str, Any]:
        """Get ML module statistics"""
        total = self.model_stats['total_signals']
        
        return {
            'total_signals_evaluated': total,
            'signals_accepted': self.model_stats['accepted_signals'],
            'signals_rejected': self.model_stats['rejected_signals'],
            'acceptance_rate': self.model_stats['accepted_signals'] / total if total > 0 else 0.0,
            'model_trained': self.model.is_trained if self.model else False,
            'training_samples': len(self.model.training_data) if self.model else 0,
            'min_threshold': self.min_score_threshold,
            'symbol_accuracy': self.accuracy_by_symbol
        }
    
    def set_threshold(self, threshold: float) -> None:
        """Dynamically adjust acceptance threshold"""
        self.min_score_threshold = max(0.0, min(1.0, threshold))
    
    def reset_statistics(self) -> None:
        """Reset all statistics"""
        self.model_stats = {
            'total_signals': 0,
            'accepted_signals': 0,
            'rejected_signals': 0,
            'accuracy': 0.0
        }
        self.accuracy_by_symbol = {}
    
    def export_training_data(self, filepath: str = None) -> Dict[str, Any]:
        """
        Export training data to JSON for offline analysis
        
        Args:
            filepath: Optional path to save JSON file. If None, data is returned only.
            
        Returns:
            Dictionary containing training data and metadata
        """
        try:
            if not self.model or not hasattr(self.model, 'training_data'):
                return {'status': 'error', 'message': 'No training data available'}
            
            # Prepare training data for export
            training_records = []
            for i, (features, label) in enumerate(zip(
                self.model.training_data, 
                self.model.training_labels if hasattr(self.model, 'training_labels') else []
            )):
                record = {
                    'sample_id': i + 1,
                    'features': features if isinstance(features, dict) else {
                        self.model.feature_names[j]: float(features[j]) 
                        for j in range(len(features)) if j < len(self.model.feature_names)
                    },
                    'label': 'WIN' if label == 1 else 'LOSS',
                    'label_numeric': int(label)
                }
                training_records.append(record)
            
            # Prepare export data
            export_data = {
                'export_timestamp': datetime.now().isoformat(),
                'model_version': 'ensemble_v1',
                'total_samples': len(training_records),
                'model_trained': self.model.is_trained if self.model else False,
                'model_stats': self.model_stats.copy(),
                'symbol_accuracy': self.accuracy_by_symbol.copy(),
                'training_records': training_records,
                'metadata': {
                    'min_score_threshold': self.min_score_threshold,
                    'ensemble_weights': {
                        'random_forest': 0.4,
                        'gradient_boosting': 0.4,
                        'svm': 0.2
                    },
                    'feature_names': self.model.feature_names if self.model else []
                }
            }
            
            # Save to file if filepath provided
            if filepath:
                try:
                    with open(filepath, 'w') as f:
                        json.dump(export_data, f, indent=2, default=str)
                    
                    log_event(
                        "ML_DATA_EXPORTED",
                        f"Training data exported to {filepath}",
                        filepath=filepath,
                        total_samples=len(training_records),
                        file_size_bytes=Path(filepath).stat().st_size
                    )
                    
                    export_data['export_status'] = 'success'
                    export_data['filepath'] = filepath
                except Exception as e:
                    log_event("ML_EXPORT_ERROR", f"Failed to save training data: {e}")
                    export_data['export_status'] = 'error'
                    export_data['export_error'] = str(e)
            
            return export_data
            
        except Exception as e:
            log_event("ML_EXPORT_EXCEPTION", f"Exception while exporting training data: {e}")
            return {'status': 'error', 'message': str(e)}


# Global instance
_ml_filter = None


def get_ml_filter() -> MLSignalFilter:
    """Get or create ML filter instance"""
    global _ml_filter
    if _ml_filter is None:
        _ml_filter = MLSignalFilter(min_score_threshold=0.6)
    return _ml_filter


def validate_with_ml(symbol: str, alert_data: Dict[str, Any], 
                     entry_price: float) -> Tuple[bool, float, Dict]:
    """
    Convenience function for ML validation
    
    Usage:
        is_valid, confidence, details = validate_with_ml('HDFC', alert, 1500)
    """
    ml_filter = get_ml_filter()
    return ml_filter.validate_signal_with_ml(symbol, alert_data, entry_price)


def record_ml_trade_outcome(symbol: str, won: bool) -> None:
    """
    Record trade outcome for model training and retraining
    
    This is called when a position closes, allowing the model to learn
    from real trade results and improve predictions over time.
    """
    try:
        ml_filter = get_ml_filter()
        ml_filter.record_trade_outcome(symbol, won)
        
        # Log the training data accumulation
        try:
            log_event(
                "ML_TRADE_OUTCOME_RECORDED",
                f"Trade outcome recorded for {symbol}: {'WIN' if won else 'LOSS'}",
                symbol=symbol,
                outcome="WIN" if won else "LOSS",
                training_samples=len(ml_filter.model.training_data) if hasattr(ml_filter.model, 'training_data') else 0,
                model_trained=ml_filter.model.is_trained if ml_filter.model else False
            )
            
            # Check if model should retrain (every 10 new samples)
            if ml_filter.model and hasattr(ml_filter.model, 'training_data'):
                num_samples = len(ml_filter.model.training_data)
                if num_samples % 10 == 0 and num_samples > 0 and not ml_filter.model.is_trained:
                    log_event(
                        "ML_AUTO_RETRAIN_TRIGGER",
                        f"Auto-retraining ML model with {num_samples} samples",
                        symbol=symbol,
                        total_samples=num_samples,
                        action="Model retraining initiated"
                    )
                    
        except Exception as e:
            # Non-fatal logging error
            pass
            
    except Exception as e:
        log_event("ML_EXIT_ERROR", f"Failed to record exit for ML: {e}")




def get_ml_statistics() -> Dict[str, Any]:
    """Get ML module statistics"""
    ml_filter = get_ml_filter()
    return ml_filter.get_ml_stats()


def set_ml_threshold(threshold: float) -> None:
    """Adjust ML acceptance threshold"""
    ml_filter = get_ml_filter()
    ml_filter.set_threshold(threshold)


def reset_ml_stats() -> None:
    """Reset ML statistics"""
    ml_filter = get_ml_filter()
    ml_filter.reset_statistics()


def export_ml_training_data(filepath: str = None) -> Dict[str, Any]:
    """
    Export ML training data to JSON file for offline analysis
    
    Usage:
        data = export_ml_training_data('/root/santhosh/trading/equity/data/ml_training_data.json')
    """
    ml_filter = get_ml_filter()
    return ml_filter.export_training_data(filepath)

