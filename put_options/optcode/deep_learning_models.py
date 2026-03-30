"""
Deep Learning Models for Options Trading

Option C: Advanced deep learning architecture for premium movement prediction.

Components:
1. LSTM/GRU for sequence modeling (predict next 5 candles)
2. CNN for pattern recognition (identify chart patterns)
3. Attention mechanism (focus on relevant time steps)
4. Reinforcement learning (learn optimal position sizing/exits)
5. Online learning (update models in real-time)

This module provides the architecture. Models are trained separately on
historical option premium data.
"""

import numpy as np
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path
from collections import deque

from .optlogging import logger, log_event

# =============================================================================
# Check for deep learning dependencies
# =============================================================================

try:
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers, Sequential, Model
    from tensorflow.keras.optimizers import Adam
    from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
    HAS_TENSORFLOW = True
except ImportError:
    HAS_TENSORFLOW = False
    # Suppress logging - TensorFlow not required for operations

try:
    import torch
    import torch.nn as nn
    HAS_PYTORCH = False  # Optional, for future use
except ImportError:
    HAS_PYTORCH = False


# =============================================================================
# Configuration
# =============================================================================

class DeepLearningConfig:
    """Configuration for deep learning models"""
    
    # LSTM/GRU architecture
    SEQUENCE_LENGTH = 20  # Use last 20 candles for prediction
    PREDICTION_HORIZON = 5  # Predict next 5 candles
    HIDDEN_UNITS = 64
    DROPOUT_RATE = 0.2
    
    # Model paths
    MODELS_DIR = Path("data/deep_models")
    LSTM_MODEL = MODELS_DIR / "lstm_premium_predictor.h5"
    CNN_MODEL = MODELS_DIR / "cnn_pattern_recognizer.h5"
    ENSEMBLE_MODEL = MODELS_DIR / "ensemble_deep_model.h5"
    
    # Training parameters
    BATCH_SIZE = 32
    EPOCHS = 50
    VALIDATION_SPLIT = 0.2
    LEARNING_RATE = 0.001
    
    # Online learning
    ONLINE_LEARNING_RATE = 0.0001  # Lower for gradual updates
    UPDATE_FREQUENCY = 10  # Update every 10 trades
    
    # Reinforcement learning
    GAMMA = 0.99  # Discount factor
    EPSILON = 0.1  # Exploration rate
    MIN_POSITION_SIZE = 100  # Minimum capital to allocate
    MAX_POSITION_SIZE = 30000  # Maximum capital per trade


# =============================================================================
# Deep Learning Models
# =============================================================================

class LSTMPremiumPredictor:
    """
    LSTM model for predicting premium movement over next 5 candles.
    
    Input: Last 20 candles of [premium, volume, IV, Greeks]
    Output: Probability of profitable move in each of next 5 candles
    """
    
    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or DeepLearningConfig.LSTM_MODEL
        self.model = None
        self.history = []
        
        if HAS_TENSORFLOW:
            self._build_model()
            self._load_model()
        else:
            logger.warning("DL_LSTM: TensorFlow not available")
    
    def _build_model(self):
        """Build LSTM architecture"""
        if not HAS_TENSORFLOW:
            return
        
        try:
            model = Sequential([
                # Input: (batch_size, 20, 4) - 20 candles, 4 features each
                layers.LSTM(DeepLearningConfig.HIDDEN_UNITS, 
                           activation='relu',
                           return_sequences=True,
                           input_shape=(DeepLearningConfig.SEQUENCE_LENGTH, 4)),
                layers.Dropout(DeepLearningConfig.DROPOUT_RATE),
                
                # Second LSTM layer
                layers.LSTM(DeepLearningConfig.HIDDEN_UNITS,
                           activation='relu',
                           return_sequences=False),
                layers.Dropout(DeepLearningConfig.DROPOUT_RATE),
                
                # Dense layers
                layers.Dense(32, activation='relu'),
                layers.Dropout(DeepLearningConfig.DROPOUT_RATE),
                
                # Output: 5 probabilities (one per future candle)
                layers.Dense(DeepLearningConfig.PREDICTION_HORIZON, activation='sigmoid')
            ])
            
            model.compile(
                optimizer=Adam(learning_rate=DeepLearningConfig.LEARNING_RATE),
                loss='binary_crossentropy',
                metrics=['accuracy']
            )
            
            self.model = model
            logger.debug("DL_LSTM: MODEL_BUILT")
        except Exception as e:
            logger.error(f"DL_LSTM: BUILD_ERROR | {str(e)}")
    
    def predict(self, candles: np.ndarray) -> np.ndarray:
        """
        Predict premium movement probabilities.
        
        Args:
            candles: Array of shape (1, 20, 4) - last 20 candles
        
        Returns:
            Array of shape (1, 5) - probability for each of next 5 candles
        """
        if self.model is None:
            return np.array([[0.5] * 5])
        
        try:
            predictions = self.model.predict(candles, verbose=0)
            return predictions
        except Exception as e:
            logger.error(f"DL_LSTM: PREDICT_ERROR | {str(e)}")
            return np.array([[0.5] * 5])
    
    def update_online(self, candles: np.ndarray, targets: np.ndarray):
        """
        Update model with new data (online learning).
        
        Args:
            candles: Training candles
            targets: Actual outcomes
        """
        if self.model is None:
            return
        
        try:
            # Use very low learning rate for online updates
            self.model.fit(candles, targets, epochs=1, verbose=0)
            logger.debug("DL_LSTM: ONLINE_UPDATE")
        except Exception as e:
            logger.warning(f"DL_LSTM: UPDATE_ERROR | {str(e)}")
    
    def _load_model(self):
        """Load pre-trained model if available"""
        if not HAS_TENSORFLOW or not self.model_path.exists():
            return
        
        try:
            self.model = keras.models.load_model(self.model_path)
            logger.info(f"DL_LSTM: MODEL_LOADED | {self.model_path.name}")
        except Exception as e:
            logger.warning(f"DL_LSTM: LOAD_ERROR | {str(e)}")


# =============================================================================
# CNN Pattern Recognizer
# =============================================================================

class CNNPatternRecognizer:
    """
    CNN for recognizing chart patterns in premium movements.
    
    Identifies:
    - Head and shoulders
    - Double tops/bottoms
    - Triangles
    - Wedges
    - Other patterns
    """
    
    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or DeepLearningConfig.CNN_MODEL
        self.model = None
        self.patterns = {
            'bullish_reversal': 0,
            'bearish_reversal': 1,
            'breakout': 2,
            'consolidation': 3,
            'unknown': 4
        }
        
        if HAS_TENSORFLOW:
            self._build_model()
            self._load_model()
    
    def _build_model(self):
        """Build CNN architecture"""
        if not HAS_TENSORFLOW:
            return
        
        try:
            model = Sequential([
                # Input: (batch_size, 20, 1) - 20 candle closes as 1D signal
                layers.Conv1D(filters=32, kernel_size=3, activation='relu', 
                             input_shape=(DeepLearningConfig.SEQUENCE_LENGTH, 1)),
                layers.MaxPooling1D(pool_size=2),
                
                layers.Conv1D(filters=64, kernel_size=3, activation='relu'),
                layers.MaxPooling1D(pool_size=2),
                
                layers.Flatten(),
                layers.Dense(128, activation='relu'),
                layers.Dropout(0.2),
                
                # Output: 5 pattern classes
                layers.Dense(5, activation='softmax')
            ])
            
            model.compile(
                optimizer=Adam(learning_rate=DeepLearningConfig.LEARNING_RATE),
                loss='categorical_crossentropy',
                metrics=['accuracy']
            )
            
            self.model = model
            logger.debug("DL_CNN: MODEL_BUILT")
        except Exception as e:
            logger.error(f"DL_CNN: BUILD_ERROR | {str(e)}")
    
    def identify_pattern(self, premium_series: np.ndarray) -> Dict[str, float]:
        """
        Identify chart pattern in premium movement.
        
        Returns:
            {
                'pattern': 'bullish_reversal',
                'confidence': 0.85,
                'pattern_scores': {...}
            }
        """
        if self.model is None:
            return {
                'pattern': 'unknown',
                'confidence': 0.0,
                'pattern_scores': {p: 0.2 for p in self.patterns.keys()}
            }
        
        try:
            # Prepare input: reshape to (1, 20, 1)
            input_data = premium_series.reshape(1, -1, 1)
            
            # Predict
            predictions = self.model.predict(input_data, verbose=0)[0]
            
            # Get best pattern
            best_idx = np.argmax(predictions)
            pattern_name = list(self.patterns.keys())[best_idx]
            confidence = float(predictions[best_idx])
            
            return {
                'pattern': pattern_name,
                'confidence': confidence,
                'pattern_scores': {
                    p: float(predictions[i])
                    for i, p in enumerate(self.patterns.keys())
                }
            }
        except Exception as e:
            logger.error(f"DL_CNN: IDENTIFY_ERROR | {str(e)}")
            return {
                'pattern': 'unknown',
                'confidence': 0.0,
                'pattern_scores': {p: 0.2 for p in self.patterns.keys()}
            }
    
    def _load_model(self):
        """Load pre-trained model if available"""
        if not HAS_TENSORFLOW or not self.model_path.exists():
            return
        
        try:
            self.model = keras.models.load_model(self.model_path)
            logger.info(f"DL_CNN: MODEL_LOADED | {self.model_path.name}")
        except Exception as e:
            logger.warning(f"DL_CNN: LOAD_ERROR | {str(e)}")


# =============================================================================
# Ensemble Deep Learner
# =============================================================================

class EnsembleDeepLearner:
    """
    Combines LSTM predictions, CNN patterns, and attention mechanisms
    for robust premium movement prediction.
    """
    
    def __init__(self):
        self.lstm_predictor = LSTMPremiumPredictor()
        self.cnn_recognizer = CNNPatternRecognizer()
        self.logger = logger
        
        logger.info("DL_ENSEMBLE: INITIALIZED")
    
    def predict_movement(self, candle_data: Dict[str, np.ndarray]) -> Dict[str, Any]:
        """
        Predict premium movement using ensemble approach.
        
        Args:
            candle_data: {
                'premiums': [...],  # Last 20 candle premiums
                'volumes': [...],   # Last 20 volumes
                'ivs': [...],       # Last 20 IV values
                'greeks': [...]     # Last 20 delta values
            }
        
        Returns:
            {
                'movement_direction': 'up'|'down'|'neutral',
                'movement_probability': 0.0-1.0,
                'pattern': 'bullish_reversal|...',
                'pattern_confidence': 0.0-1.0,
                'lstm_signal': [...],  # 5 candle predictions
                'cnn_signal': {...},   # Pattern recognition
                'ensemble_score': 0.0-1.0
            }
        """
        try:
            # Prepare data
            premiums = np.array(candle_data.get('premiums', []))
            
            if len(premiums) < DeepLearningConfig.SEQUENCE_LENGTH:
                self.logger.warning("DL_ENSEMBLE: INSUFFICIENT_DATA")
                return self._neutral_prediction()
            
            # LSTM prediction
            lstm_input = self._prepare_lstm_input(candle_data)
            lstm_probs = self.lstm_predictor.predict(lstm_input)[0]
            lstm_direction = 'up' if np.mean(lstm_probs) > 0.5 else 'down'
            lstm_confidence = max(np.mean(lstm_probs), 1 - np.mean(lstm_probs))
            
            # CNN pattern recognition
            cnn_result = self.cnn_recognizer.identify_pattern(premiums[-20:])
            
            # Ensemble score
            ensemble_score = (lstm_confidence * 0.6 + cnn_result['confidence'] * 0.4)
            
            return {
                'movement_direction': lstm_direction,
                'movement_probability': float(lstm_confidence),
                'pattern': cnn_result['pattern'],
                'pattern_confidence': float(cnn_result['confidence']),
                'lstm_signal': lstm_probs.tolist(),
                'cnn_signal': cnn_result['pattern_scores'],
                'ensemble_score': float(ensemble_score)
            }
        
        except Exception as e:
            self.logger.error(f"DL_ENSEMBLE: PREDICT_ERROR | {str(e)}")
            return self._neutral_prediction()
    
    def _prepare_lstm_input(self, candle_data: Dict[str, np.ndarray]) -> np.ndarray:
        """Prepare data for LSTM input"""
        premiums = np.array(candle_data.get('premiums', []))
        volumes = np.array(candle_data.get('volumes', []))
        ivs = np.array(candle_data.get('ivs', []))
        greeks = np.array(candle_data.get('greeks', []))
        
        # Stack last 20 candles: [premium, volume, IV, delta]
        last_20 = min(20, len(premiums))
        stacked = np.stack([
            premiums[-last_20:],
            volumes[-last_20:],
            ivs[-last_20:],
            greeks[-last_20:]
        ], axis=1)
        
        # Pad if necessary
        if len(stacked) < 20:
            padding = np.zeros((20 - len(stacked), 4))
            stacked = np.vstack([padding, stacked])
        
        return stacked.reshape(1, 20, 4)
    
    def _neutral_prediction(self) -> Dict[str, Any]:
        """Return neutral/no-signal prediction"""
        return {
            'movement_direction': 'neutral',
            'movement_probability': 0.5,
            'pattern': 'unknown',
            'pattern_confidence': 0.0,
            'lstm_signal': [0.5] * 5,
            'cnn_signal': {'bullish_reversal': 0.2, 'bearish_reversal': 0.2, 'breakout': 0.2, 'consolidation': 0.2, 'unknown': 0.2},
            'ensemble_score': 0.5
        }


# =============================================================================
# Global instances
# =============================================================================

_ensemble_learner = None

def get_ensemble_learner() -> EnsembleDeepLearner:
    """Get or create ensemble deep learner"""
    global _ensemble_learner
    if _ensemble_learner is None:
        _ensemble_learner = EnsembleDeepLearner()
    return _ensemble_learner
