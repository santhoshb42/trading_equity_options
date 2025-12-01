"""
Fake Move Detector

Detects false LTP falls due to profit booking by other traders.
Prevents entry on transient moves without real directional conviction.

Filters:
1. Volume Spike Filter - Confirm price move with matching volume
2. Time-Weighted Momentum - Require sustained move (3+ candles)
3. Premium Reversion Check - Monitor for quick reversions post-entry
4. ML Confidence Booster (Optional) - Use ML to adjust confidence
5. Deep Learning Insights (Optional) - Use LSTM/CNN for pattern recognition

ML Integration:
- If ML models available: runs ML scoring after rule-based filters pass
- Adjusts confidence based on model predictions
- Tracks all predictions for learning
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from collections import deque

from .optlogging import logger, log_alert, log_signal_validation, log_event

# ML imports (optional)
try:
    from .ml_signal_scorer import get_ml_scorer
    from .options_learning_engine import get_symbol_tracker, get_trade_recorder
    from .deep_learning_models import get_ensemble_learner
    HAS_ML = True
except ImportError:
    HAS_ML = False
    logger.warning("FAKE_MOVE_DETECTOR: ML modules not available")

# Trade logging
try:
    from .trade_logger import get_trade_logger
    HAS_TRADE_LOGGER = True
except ImportError:
    HAS_TRADE_LOGGER = False
    logger.warning("FAKE_MOVE_DETECTOR: Trade logger not available")

# =============================================================================
# Configuration
# =============================================================================

class FakeMoveConfig:
    """Configuration for fake move detection"""
    
    # Volume Spike Filter
    VOLUME_THRESHOLD_MULTIPLIER = 1.5  # Need 1.5x average volume for move to be valid
    MIN_VOLUME_MULTIPLIER = 1.2  # Minimum threshold (very low volume moves are fake)
    
    # Time-Weighted Momentum
    REQUIRED_CONSECUTIVE_CANDLES = 3  # Need 3 consecutive candles in same direction
    CANDLE_WINDOW = 5  # Check last 5 candles
    MIN_MOMENTUM_STRENGTH = 0.7  # % of candles should match direction
    
    # Premium Reversion Check
    REVERSION_CHECK_WINDOW = 30  # seconds after entry
    REVERSION_THRESHOLD = 0.5  # 50% reversion = fake move
    QUICK_REVERSION_EXIT = True  # Automatically exit on false move
    
    # Order Book Depth
    CHECK_SPREAD = True  # Check bid-ask spread
    MAX_SPREAD_MULTIPLIER = 1.5  # Max acceptable spread vs average
    MIN_ORDER_DEPTH = 5  # Minimum order book depth required
    
    # PCR Ratio (for BANKNIFTY/NIFTY)
    CHECK_PCR_RATIO = True  # Check put-call ratio
    MIN_PCR_FOR_SELL_SIGNAL = 0.8  # If PCR < 0.8, reduce confidence for SELL signals
    
    # Price Action
    CHECK_SUPPORT_LEVELS = True  # Check if price near support
    SUPPORT_PROXIMITY = 0.3  # % away from support


# =============================================================================
# Volume Spike Filter
# =============================================================================

class VolumeSpikeFilter:
    """
    Detects if price move has matching volume.
    Real moves have high volume, fake moves have low volume.
    """
    
    def __init__(self):
        self.volume_history = deque(maxlen=20)  # Track last 20 candles
        self.logger = logger
    
    def record_volume(self, volume: float, price_change_percent: float):
        """Record volume and price change"""
        self.volume_history.append({
            'volume': volume,
            'price_change': price_change_percent,
            'timestamp': datetime.now()
        })
    
    def get_average_volume(self, lookback: int = 10) -> float:
        """Get average volume from history"""
        if len(self.volume_history) < lookback:
            return sum(v['volume'] for v in self.volume_history) / len(self.volume_history) if self.volume_history else 0
        
        recent = list(self.volume_history)[-lookback:]
        return sum(v['volume'] for v in recent) / len(recent)
    
    def is_volume_sufficient(self, current_volume: float, price_change_percent: float) -> Tuple[bool, str]:
        """
        Check if current volume matches price move.
        
        Returns: (is_valid, message)
        """
        avg_volume = self.get_average_volume()
        
        if avg_volume == 0:
            return True, "No volume history yet - accepting signal"
        
        # For significant price moves (>1%), need matching volume
        if price_change_percent >= 1.0:
            if current_volume < avg_volume * FakeMoveConfig.MIN_VOLUME_MULTIPLIER:
                message = f"Low volume spike: {current_volume:.0f} < {avg_volume * FakeMoveConfig.MIN_VOLUME_MULTIPLIER:.0f} (avg={avg_volume:.0f})"
                logger.warning(f"VOLUME_FILTER: REJECTED | {message}")
                return False, message
            
            # Even better: require higher multiplier for larger moves
            if price_change_percent >= 2.0:
                if current_volume < avg_volume * FakeMoveConfig.VOLUME_THRESHOLD_MULTIPLIER:
                    message = f"Insufficient volume for {price_change_percent:.1f}% move: {current_volume:.0f} < {avg_volume * FakeMoveConfig.VOLUME_THRESHOLD_MULTIPLIER:.0f}"
                    logger.warning(f"VOLUME_FILTER: REJECTED | {message}")
                    return False, message
        
        logger.debug(f"VOLUME_FILTER: PASSED | volume={current_volume:.0f} | avg={avg_volume:.0f} | price_change={price_change_percent:.2f}%")
        return True, "Volume sufficient for price move"


# =============================================================================
# Time-Weighted Momentum Filter
# =============================================================================

class TimeWeightedMomentumFilter:
    """
    Ensures price move is sustained over multiple candles.
    Detects profit booking (single candle spike) vs real momentum (3+ candles).
    """
    
    def __init__(self):
        self.candle_history = deque(maxlen=20)  # Track last 20 candles
        self.logger = logger
    
    def record_candle(self, close_price: float, is_bullish: bool):
        """Record candle direction (bullish=up, bearish=down)"""
        self.candle_history.append({
            'price': close_price,
            'direction': 'UP' if is_bullish else 'DOWN',
            'timestamp': datetime.now()
        })
    
    def check_momentum(self, expected_direction: str) -> Tuple[bool, str]:
        """
        Check if recent candles show sustained momentum.
        
        expected_direction: 'UP' for BUY signal, 'DOWN' for SELL signal
        Returns: (is_valid, message)
        """
        if len(self.candle_history) < FakeMoveConfig.REQUIRED_CONSECUTIVE_CANDLES:
            return True, f"Not enough history ({len(self.candle_history)} candles)"
        
        # Check last N candles
        recent_candles = list(self.candle_history)[-FakeMoveConfig.CANDLE_WINDOW:]
        matching = sum(1 for c in recent_candles if c['direction'] == expected_direction)
        matching_percent = matching / len(recent_candles)
        
        # Need at least REQUIRED_CONSECUTIVE_CANDLES in same direction
        consecutive = 0
        max_consecutive = 0
        for candle in recent_candles:
            if candle['direction'] == expected_direction:
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 0
        
        logger.debug(f"MOMENTUM_FILTER: CHECK | direction={expected_direction} | matching={matching}/{len(recent_candles)} ({matching_percent*100:.1f}%) | max_consecutive={max_consecutive}")
        
        if max_consecutive < FakeMoveConfig.REQUIRED_CONSECUTIVE_CANDLES:
            message = f"Insufficient momentum: only {max_consecutive} consecutive {expected_direction} candles (need {FakeMoveConfig.REQUIRED_CONSECUTIVE_CANDLES})"
            logger.warning(f"MOMENTUM_FILTER: REJECTED | {message}")
            return False, message
        
        if matching_percent < FakeMoveConfig.MIN_MOMENTUM_STRENGTH:
            message = f"Weak momentum: only {matching_percent*100:.1f}% candles match direction (need {FakeMoveConfig.MIN_MOMENTUM_STRENGTH*100:.1f}%)"
            logger.warning(f"MOMENTUM_FILTER: REJECTED | {message}")
            return False, message
        
        logger.debug(f"MOMENTUM_FILTER: PASSED | {max_consecutive} consecutive candles | {matching_percent*100:.1f}% matching")
        return True, f"Strong momentum: {max_consecutive} consecutive {expected_direction} candles"


# =============================================================================
# Premium Reversion Check (Post-Entry Monitor)
# =============================================================================

class PremiumReversionMonitor:
    """
    Monitors position post-entry for quick reversions.
    Detects false moves that reverse quickly (indicating profit booking exit).
    """
    
    def __init__(self):
        self.entries = {}  # {symbol: {'entry_premium': ..., 'entry_time': ..., 'entry_iv': ...}}
        self.reversion_events = []
        self.logger = logger
    
    def record_entry(self, symbol: str, entry_premium: float, entry_iv: float):
        """Record entry point"""
        self.entries[symbol] = {
            'entry_premium': entry_premium,
            'entry_time': datetime.now(),
            'entry_iv': entry_iv,
            'min_premium': entry_premium,  # Track lowest point
            'max_premium': entry_premium,  # Track highest point
            'checked_reversion': False
        }
        logger.debug(f"REVERSION_MONITOR: ENTRY_RECORDED | {symbol} | premium={entry_premium:.2f} | iv={entry_iv:.1f}")
    
    def check_reversion(self, symbol: str, current_premium: float, current_iv: float) -> Tuple[bool, Optional[str]]:
        """
        Check if position has reverted (false move detected).
        
        Returns: (is_false_move, reversion_reason)
        """
        if symbol not in self.entries:
            return False, None
        
        entry_data = self.entries[symbol]
        time_since_entry = (datetime.now() - entry_data['entry_time']).total_seconds()
        
        # Only check within window
        if time_since_entry > FakeMoveConfig.REVERSION_CHECK_WINDOW:
            entry_data['checked_reversion'] = True
            return False, None
        
        # Already checked
        if entry_data['checked_reversion']:
            return False, None
        
        entry_premium = entry_data['entry_premium']
        reversion_percent = abs(current_premium - entry_premium) / entry_premium if entry_premium > 0 else 0
        
        logger.debug(f"REVERSION_MONITOR: CHECK | {symbol} | elapsed={time_since_entry:.1f}s | reversion={reversion_percent*100:.2f}%")
        
        # Check for significant reversion
        if reversion_percent >= FakeMoveConfig.REVERSION_THRESHOLD:
            # Mark as checked to avoid repeated exits
            entry_data['checked_reversion'] = True
            
            reason = f"Premium reverted {reversion_percent*100:.1f}% in {time_since_entry:.1f}s (fake move)"
            logger.warning(f"REVERSION_MONITOR: FALSE_MOVE_DETECTED | {symbol} | {reason}")
            
            self.reversion_events.append({
                'symbol': symbol,
                'entry_premium': entry_premium,
                'exit_premium': current_premium,
                'reversion_percent': reversion_percent,
                'time_since_entry': time_since_entry,
                'timestamp': datetime.now().isoformat()
            })
            
            return True, reason
        
        return False, None
    
    def clear_entry(self, symbol: str):
        """Clear entry record after position closed"""
        if symbol in self.entries:
            del self.entries[symbol]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get reversion monitoring statistics"""
        return {
            'active_entries': len(self.entries),
            'reversion_events': len(self.reversion_events),
            'total_reversions': len(self.reversion_events),
            'recent_reversions': self.reversion_events[-10:] if self.reversion_events else []
        }


# =============================================================================
# Order Book Depth Check
# =============================================================================

class OrderBookDepthChecker:
    """
    Validates order book liquidity.
    Thin order books indicate potential for slippage or false moves.
    """
    
    @staticmethod
    def check_spread(bid_price: float, ask_price: float, avg_spread: float) -> Tuple[bool, str]:
        """
        Check if bid-ask spread is acceptable.
        
        Returns: (is_acceptable, message)
        """
        current_spread = ask_price - bid_price
        spread_ratio = current_spread / avg_spread if avg_spread > 0 else 1.0
        
        if spread_ratio > FakeMoveConfig.MAX_SPREAD_MULTIPLIER:
            message = f"Wide spread: {current_spread:.2f} ({spread_ratio:.2f}x avg)"
            logger.warning(f"ORDER_BOOK_CHECK: REJECTED | {message}")
            return False, message
        
        logger.debug(f"ORDER_BOOK_CHECK: SPREAD_OK | {current_spread:.2f} ({spread_ratio:.2f}x avg)")
        return True, "Spread acceptable"
    
    @staticmethod
    def check_depth(bid_volume: float, ask_volume: float) -> Tuple[bool, str]:
        """
        Check if order book has sufficient depth.
        
        Returns: (is_acceptable, message)
        """
        min_depth = min(bid_volume, ask_volume)
        
        if min_depth < FakeMoveConfig.MIN_ORDER_DEPTH:
            message = f"Thin order book: depth={min_depth:.0f} (need {FakeMoveConfig.MIN_ORDER_DEPTH})"
            logger.warning(f"ORDER_BOOK_CHECK: REJECTED | {message}")
            return False, message
        
        logger.debug(f"ORDER_BOOK_CHECK: DEPTH_OK | bid={bid_volume:.0f} | ask={ask_volume:.0f}")
        return True, "Order book depth sufficient"


# =============================================================================
# PCR Ratio Filter (For Index Options)
# =============================================================================

class PCRRatioFilter:
    """
    Checks Put-Call Ratio for index options (BANKNIFTY, NIFTY, FINNIFTY).
    Low PCR = puts are cheap = likely recovery coming (don't SELL).
    High PCR = calls are cheap = likely recovery coming (don't BUY).
    """
    
    @staticmethod
    def adjust_confidence(action: str, pcr_ratio: float, current_confidence: float) -> Tuple[float, Optional[str]]:
        """
        Adjust signal confidence based on PCR ratio.
        
        Returns: (adjusted_confidence, warning_message)
        """
        if not FakeMoveConfig.CHECK_PCR_RATIO:
            return current_confidence, None
        
        # For SELL signals (take PE), check if puts are already cheap
        if action == "SELL":
            if pcr_ratio < FakeMoveConfig.MIN_PCR_FOR_SELL_SIGNAL:
                warning = f"Low PCR ratio ({pcr_ratio:.2f}) - puts already cheap, recovery likely"
                logger.warning(f"PCR_FILTER: CONFIDENCE_REDUCED | {warning}")
                # Reduce confidence by 15%
                adjusted = current_confidence * 0.85
                return adjusted, warning
        
        # For BUY signals (take CE), check if calls are already cheap
        elif action == "BUY":
            if pcr_ratio > (1.0 / FakeMoveConfig.MIN_PCR_FOR_SELL_SIGNAL):  # Inverse for calls
                warning = f"High PCR ratio ({pcr_ratio:.2f}) - calls already cheap, recovery likely"
                logger.warning(f"PCR_FILTER: CONFIDENCE_REDUCED | {warning}")
                # Reduce confidence by 15%
                adjusted = current_confidence * 0.85
                return adjusted, warning
        
        logger.debug(f"PCR_FILTER: OK | action={action} | pcr={pcr_ratio:.2f}")
        return current_confidence, None


# =============================================================================
# Fake Move Detector (Main Class)
# =============================================================================

class FakeMoveDetector:
    """
    Combined fake move detection using all filters.
    """
    
    def __init__(self):
        self.volume_filter = VolumeSpikeFilter()
        self.momentum_filter = TimeWeightedMomentumFilter()
        self.reversion_monitor = PremiumReversionMonitor()
        self.order_book_checker = OrderBookDepthChecker()
        self.pcr_filter = PCRRatioFilter()
        
        # ML components (optional)
        self.ml_scorer = None
        self.symbol_tracker = None
        self.trade_recorder = None
        self.ensemble_learner = None
        
        if HAS_ML:
            try:
                self.ml_scorer = get_ml_scorer()
                self.symbol_tracker = get_symbol_tracker()
                self.trade_recorder = get_trade_recorder()
                self.ensemble_learner = get_ensemble_learner()
                logger.info("FAKE_MOVE_DETECTOR: ML_COMPONENTS_LOADED")
            except Exception as e:
                logger.warning(f"FAKE_MOVE_DETECTOR: ML_INIT_ERROR | {str(e)}")
        
        # Trade logger (optional)
        self.trade_logger = None
        if HAS_TRADE_LOGGER:
            try:
                self.trade_logger = get_trade_logger()
                logger.info("FAKE_MOVE_DETECTOR: TRADE_LOGGER_LOADED")
            except Exception as e:
                logger.warning(f"FAKE_MOVE_DETECTOR: TRADE_LOGGER_INIT_ERROR | {str(e)}")
        
        self.detection_stats = {
            'total_signals_checked': 0,
            'rejected_by_volume': 0,
            'rejected_by_momentum': 0,
            'rejected_by_spread': 0,
            'rejected_by_depth': 0,
            'rejected_by_pcr': 0,
            'false_moves_detected': 0,
            'ml_scores_generated': 0,
            'ml_confidence_boosted': 0,
            'ml_confidence_reduced': 0
        }
        
        logger.debug("FAKE_MOVE_DETECTOR: INITIALIZED")
    
    def validate_entry_signal(self, 
                             symbol: str,
                             action: str,
                             confidence: float,
                             volume: float,
                             price_change_percent: float,
                             candle_direction: str,
                             bid_price: float = None,
                             ask_price: float = None,
                             avg_spread: float = None,
                             bid_volume: float = None,
                             ask_volume: float = None,
                             pcr_ratio: float = None) -> Tuple[bool, float, List[str]]:
        """
        Validate entry signal through all fake move filters.
        
        Returns: (is_valid_signal, adjusted_confidence, rejection_reasons)
        """
        self.detection_stats['total_signals_checked'] += 1
        rejection_reasons = []
        adjusted_confidence = confidence
        
        logger.info(f"FAKE_MOVE_DETECTOR: VALIDATE_START | {symbol} | {action} | conf={confidence:.1f}% | change={price_change_percent:.2f}%")
        
        # Filter 1: Volume Spike
        volume_valid, volume_msg = self.volume_filter.is_volume_sufficient(volume, price_change_percent)
        if not volume_valid:
            rejection_reasons.append(volume_msg)
            self.detection_stats['rejected_by_volume'] += 1
            logger.warning(f"FAKE_MOVE_DETECTOR: REJECTED | {symbol} | {volume_msg}")
            return False, adjusted_confidence, rejection_reasons
        
        # Record volume for future checks
        self.volume_filter.record_volume(volume, price_change_percent)
        
        # Filter 2: Time-Weighted Momentum
        # Convert action to direction
        expected_direction = 'UP' if action == 'BUY' else 'DOWN'
        self.momentum_filter.record_candle(0, action == 'BUY')  # Record before check
        
        momentum_valid, momentum_msg = self.momentum_filter.check_momentum(expected_direction)
        if not momentum_valid:
            rejection_reasons.append(momentum_msg)
            self.detection_stats['rejected_by_momentum'] += 1
            logger.warning(f"FAKE_MOVE_DETECTOR: REJECTED | {symbol} | {momentum_msg}")
            return False, adjusted_confidence, rejection_reasons
        
        # Filter 3: Order Book (if provided)
        if FakeMoveConfig.CHECK_SPREAD and bid_price and ask_price and avg_spread:
            spread_valid, spread_msg = self.order_book_checker.check_spread(bid_price, ask_price, avg_spread)
            if not spread_valid:
                rejection_reasons.append(spread_msg)
                self.detection_stats['rejected_by_spread'] += 1
                logger.warning(f"FAKE_MOVE_DETECTOR: REJECTED | {symbol} | {spread_msg}")
                return False, adjusted_confidence, rejection_reasons
        
        # Filter 4: Order Book Depth (if provided)
        if FakeMoveConfig.CHECK_SPREAD and bid_volume and ask_volume:
            depth_valid, depth_msg = self.order_book_checker.check_depth(bid_volume, ask_volume)
            if not depth_valid:
                rejection_reasons.append(depth_msg)
                self.detection_stats['rejected_by_depth'] += 1
                logger.warning(f"FAKE_MOVE_DETECTOR: REJECTED | {symbol} | {depth_msg}")
                return False, adjusted_confidence, rejection_reasons
        
        # Filter 5: PCR Ratio (if provided)
        if FakeMoveConfig.CHECK_PCR_RATIO and pcr_ratio:
            new_confidence, pcr_msg = self.pcr_filter.adjust_confidence(action, pcr_ratio, adjusted_confidence)
            if pcr_msg:
                rejection_reasons.append(pcr_msg)
                if new_confidence < adjusted_confidence:
                    adjusted_confidence = new_confidence
                    self.detection_stats['rejected_by_pcr'] += 1
        
        logger.info(f"FAKE_MOVE_DETECTOR: VALIDATED | {symbol} | all filters passed | confidence={adjusted_confidence:.1f}%")
        return True, adjusted_confidence, []
    
    def apply_ml_scoring(self, symbol: str, alert: Dict[str, Any], 
                        current_confidence: float,
                        market_data: Optional[Dict[str, Any]] = None) -> Tuple[float, Optional[Dict[str, Any]]]:
        """
        Apply ML scoring to boost/reduce confidence.
        
        Returns: (final_confidence, ml_result)
        """
        if not self.ml_scorer:
            return current_confidence, None
        
        try:
            # Get ML prediction
            ml_result = self.ml_scorer.score_signal(alert, symbol, market_data, current_confidence)
            
            self.detection_stats['ml_scores_generated'] += 1
            
            final_confidence = ml_result['confidence_adjusted']
            
            if final_confidence > current_confidence:
                self.detection_stats['ml_confidence_boosted'] += 1
                logger.info(f"ML_SCORER: CONFIDENCE_BOOSTED | {symbol} | {current_confidence:.0f}% → {final_confidence:.0f}%")
            elif final_confidence < current_confidence:
                self.detection_stats['ml_confidence_reduced'] += 1
                logger.info(f"ML_SCORER: CONFIDENCE_REDUCED | {symbol} | {current_confidence:.0f}% → {final_confidence:.0f}%")
            
            return final_confidence, ml_result
        except Exception as e:
            logger.error(f"FAKE_MOVE_DETECTOR: ML_ERROR | {str(e)}")
            return current_confidence, None
    
    def record_trade_entry(self, symbol: str, action: str, confidence: float,
                          entry_premium: float, order_id: str,
                          alert: Dict[str, Any],
                          ml_result: Optional[Dict[str, Any]] = None):
        """Record trade entry for learning"""
        if not self.trade_recorder:
            return
        
        try:
            ml_prediction = None
            if ml_result:
                ml_prediction = {
                    'win_probability': ml_result.get('win_probability', 0.5),
                    'confidence_multiplier': ml_result.get('confidence_multiplier', 1.0),
                    'model_used': ml_result.get('model_used', 'unknown')
                }
            
            features = ml_result.get('feature_values', {}) if ml_result else {}
            
            self.trade_recorder.record_entry(
                symbol=symbol,
                action=action,
                confidence=confidence,
                entry_premium=entry_premium,
                order_id=order_id,
                ml_prediction=ml_prediction,
                features=features
            )
            
            logger.debug(f"FAKE_MOVE_DETECTOR: TRADE_ENTRY_RECORDED | {symbol}")
        except Exception as e:
            logger.error(f"FAKE_MOVE_DETECTOR: RECORD_ERROR | {str(e)}")
    
    def record_trade_exit(self, symbol: str, exit_premium: float, exit_reason: str):
        """Record trade exit and update learning"""
        if not self.trade_recorder or not self.symbol_tracker:
            return
        
        try:
            trade_data = self.trade_recorder.record_exit(symbol, exit_premium, exit_reason)
            
            if trade_data:
                # Update symbol stats
                self.symbol_tracker.record_trade(
                    symbol=symbol,
                    won=trade_data['win'],
                    profit=trade_data['profit'],
                    predicted_prob=trade_data.get('ml_prediction', {}).get('win_probability', 0.5)
                )
                
                logger.debug(f"FAKE_MOVE_DETECTOR: TRADE_EXIT_RECORDED | {symbol} | profit=₹{trade_data['profit']:.2f}")
        except Exception as e:
            logger.error(f"FAKE_MOVE_DETECTOR: RECORD_ERROR | {str(e)}")
    
    def monitor_position(self, symbol: str, entry_premium: float, entry_iv: float):
        """Start monitoring position for false reversions"""
        self.reversion_monitor.record_entry(symbol, entry_premium, entry_iv)
    
    def check_false_move_exit(self, symbol: str, current_premium: float, current_iv: float) -> Tuple[bool, Optional[str]]:
        """Check if position should be closed due to false move detection"""
        is_false_move, reason = self.reversion_monitor.check_reversion(symbol, current_premium, current_iv)
        
        if is_false_move:
            self.detection_stats['false_moves_detected'] += 1
            logger.warning(f"FAKE_MOVE_DETECTOR: FALSE_MOVE_EXIT | {symbol} | {reason}")
        
        return is_false_move, reason
    
    def close_position_monitoring(self, symbol: str):
        """Clear monitoring for closed position"""
        self.reversion_monitor.clear_entry(symbol)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get detection statistics"""
        return {
            **self.detection_stats,
            'reversion_stats': self.reversion_monitor.get_stats(),
            'pass_rate': (self.detection_stats['total_signals_checked'] - 
                         sum([self.detection_stats[k] for k in self.detection_stats if k.startswith('rejected_')])) 
                        / self.detection_stats['total_signals_checked'] * 100 
                        if self.detection_stats['total_signals_checked'] > 0 else 0
        }


# =============================================================================
# Global detector instance
# =============================================================================

_fake_move_detector = None

def get_fake_move_detector() -> FakeMoveDetector:
    """Get or create fake move detector"""
    global _fake_move_detector
    if _fake_move_detector is None:
        _fake_move_detector = FakeMoveDetector()
    return _fake_move_detector
