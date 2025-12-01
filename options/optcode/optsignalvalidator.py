"""
Options Signal Validation

Validates TradingView alerts for options trading:
- Strike selection (ATM vs OTM)
- IV conditions (percentile thresholds)
- Greeks constraints
- Expiry window validation
- Directional signal mapping (BUY→Long CE, SELL→Long PE)
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

from .optconfig import OptionsTradingConfig
from .optlogging import logger, log_event, log_signal_validation
from .fake_move_detector import get_fake_move_detector

# =============================================================================
# Options Signal Validator
# =============================================================================

class OptionsSignalValidator:
    """Validates and processes TradingView alerts for options trading"""
    
    @staticmethod
    def validate_options_signal(alert: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Validate options trading signal from TradingView alert.
        
        Returns: (is_valid, message, processed_signal)
        """
        try:
            # Extract basic fields
            symbol = alert.get('symbol', '').upper().strip()
            action = alert.get('action', '').upper()
            score = float(alert.get('score', 0))
            confidence = float(alert.get('confidence', 0))
            verdict = alert.get('verdict', 0)
            
            logger.debug(f"SIGNAL_VALIDATE: START | symbol={symbol} | action={action} | conf={confidence}% | score={score}")
            
            # Validation 1: Symbol and action
            if not symbol or action not in ['BUY', 'SELL']:
                message = "Invalid symbol or action"
                logger.warning(f"SIGNAL_VALIDATE: REJECTED | {message} | symbol={symbol} | action={action}")
                log_signal_validation(symbol, False, message, action=action)
                return False, message, None
            
            # Validation 2: Signal quality thresholds - confidence
            if confidence < OptionsTradingConfig.MIN_CONFIDENCE:
                message = f"Low confidence: {confidence}% < {OptionsTradingConfig.MIN_CONFIDENCE}%"
                logger.warning(f"SIGNAL_VALIDATE: REJECTED | {message} | symbol={symbol}")
                log_signal_validation(symbol, False, "Low confidence", confidence=confidence, min_conf=OptionsTradingConfig.MIN_CONFIDENCE)
                return False, message, None
            
            # Validation 3: Signal quality thresholds - score
            if score < OptionsTradingConfig.MIN_SIGNAL_QUALITY:
                message = f"Low signal quality: {score} < {OptionsTradingConfig.MIN_SIGNAL_QUALITY}"
                logger.warning(f"SIGNAL_VALIDATE: REJECTED | {message} | symbol={symbol}")
                log_signal_validation(symbol, False, "Low signal quality", score=score, min_score=OptionsTradingConfig.MIN_SIGNAL_QUALITY)
                return False, message, None
            
            logger.debug(f"SIGNAL_VALIDATE: QUALITY_OK | symbol={symbol} | conf={confidence}% | score={score}")
            
            # Validation 4: Underlying availability
            underlying = OptionsSignalValidator._derive_underlying(symbol)
            if underlying not in OptionsTradingConfig.UNDERLYING_INDEXES:
                message = f"Underlying {underlying} not supported"
                logger.warning(f"SIGNAL_VALIDATE: REJECTED | {message} | symbol={symbol}")
                log_signal_validation(symbol, False, "Unsupported underlying", underlying=underlying)
                return False, message, None
            
            logger.debug(f"SIGNAL_VALIDATE: UNDERLYING_OK | symbol={symbol} | underlying={underlying}")
            
            # Validation 5: IV conditions (mock check - in production would fetch live IV)
            iv_check, iv_message = OptionsSignalValidator._check_iv_conditions(underlying)
            if not iv_check:
                logger.warning(f"SIGNAL_VALIDATE: REJECTED | {iv_message} | symbol={symbol} | underlying={underlying}")
                log_signal_validation(symbol, False, "IV conditions failed", underlying=underlying, reason=iv_message)
                return False, iv_message, None
            
            logger.debug(f"SIGNAL_VALIDATE: IV_OK | symbol={symbol} | {iv_message}")
            
            # Validation 6: Fake move detection (optional - only if required fields provided)
            volume = float(alert.get('volume', 0))
            price_change = float(alert.get('price_change_percent', 0))
            
            if volume > 0 and price_change != 0:
                fake_move_detector = get_fake_move_detector()
                
                # Extract optional fields for deeper fake move checking
                bid_price = float(alert.get('bid_price', 0)) if 'bid_price' in alert else None
                ask_price = float(alert.get('ask_price', 0)) if 'ask_price' in alert else None
                avg_spread = float(alert.get('avg_spread', 0)) if 'avg_spread' in alert else None
                bid_volume = float(alert.get('bid_volume', 0)) if 'bid_volume' in alert else None
                ask_volume = float(alert.get('ask_volume', 0)) if 'ask_volume' in alert else None
                pcr_ratio = float(alert.get('pcr_ratio', 0)) if 'pcr_ratio' in alert else None
                
                is_valid, adjusted_confidence, rejection_reasons = fake_move_detector.validate_entry_signal(
                    symbol=symbol,
                    action=action,
                    confidence=confidence,
                    volume=volume,
                    price_change_percent=price_change,
                    candle_direction='UP' if action == 'BUY' else 'DOWN',
                    bid_price=bid_price,
                    ask_price=ask_price,
                    avg_spread=avg_spread,
                    bid_volume=bid_volume,
                    ask_volume=ask_volume,
                    pcr_ratio=pcr_ratio
                )
                
                if not is_valid:
                    message = f"Fake move detected: {'; '.join(rejection_reasons)}"
                    logger.warning(f"SIGNAL_VALIDATE: REJECTED | {message} | symbol={symbol}")
                    log_signal_validation(symbol, False, "Fake move detected", 
                                        reasons=rejection_reasons, 
                                        confidence=confidence,
                                        volume=volume,
                                        price_change=price_change)
                    return False, message, None
                
                # Update confidence if adjusted
                if adjusted_confidence != confidence:
                    logger.info(f"SIGNAL_VALIDATE: CONFIDENCE_ADJUSTED | {symbol} | {confidence:.1f}% → {adjusted_confidence:.1f}%")
                    confidence = adjusted_confidence
            
            # Build processed signal for options
            processed_signal = {
                'underlying': underlying,
                'action': action,  # BUY = Long CE, SELL = Long PE
                'symbol': symbol,  # Original symbol from alert
                'confidence': confidence,
                'score': score,
                'verdict': verdict,
                'timestamp': datetime.now().isoformat(),
                'strike_offset': OptionsTradingConfig.STRIKE_OFFSET,  # Use config offset
                'iv_percentile': OptionsSignalValidator._get_iv_percentile(underlying),
                'recommended_contract': OptionsSignalValidator._get_contract_type(action)
            }
            
            logger.info(f"SIGNAL_VALIDATE: PASSED | symbol={symbol} | action={action} | contract={processed_signal['recommended_contract']} | iv={processed_signal['iv_percentile']:.1f}%")
            log_signal_validation(
                symbol, True, "Signal validation passed",
                action=action,
                underlying=underlying,
                confidence=confidence,
                score=score,
                contract_type=processed_signal['recommended_contract']
            )
            
            return True, "Options signal valid", processed_signal
        
        except Exception as e:
            message = f"Validation error: {str(e)}"
            logger.error(f"SIGNAL_VALIDATE: ERROR | {message}", exc_info=True)
            log_signal_validation(alert.get('symbol', 'UNKNOWN'), False, "Exception in validation", error=str(e))
            return False, message, None
    
    @staticmethod
    def _derive_underlying(symbol: str) -> str:
        """Derive underlying from symbol"""
        # BANKNIFTY, NIFTY, FINNIFTY symbols should be derivable from context
        # For now, try to match against known underlyings
        symbol_upper = symbol.upper()
        
        for underlying in OptionsTradingConfig.UNDERLYING_INDEXES:
            if underlying in symbol_upper:
                return underlying
        
        # Default to BANKNIFTY if derivation fails
        return "BANKNIFTY"
    
    @staticmethod
    def _check_iv_conditions(underlying: str) -> Tuple[bool, str]:
        """
        Check if IV percentile is within acceptable range.
        In production: fetch live IV percentile from broker/options chain.
        """
        # Mock IV check - in production would fetch from angelone_options.py
        # For now: assume IV is always acceptable
        return True, "IV conditions acceptable"
    
    @staticmethod
    def _get_iv_percentile(underlying: str) -> float:
        """
        Get IV percentile for underlying.
        Mock implementation - returns middle of range.
        """
        min_iv = OptionsTradingConfig.IV_PERCENTILE_MIN
        max_iv = OptionsTradingConfig.IV_PERCENTILE_MAX
        return (min_iv + max_iv) / 2
    
    @staticmethod
    def _get_contract_type(action: str) -> str:
        """Map action to contract type"""
        if action == "BUY":
            return "CE"  # Call (bullish)
        else:  # SELL
            return "PE"  # Put (bearish)
    
    @staticmethod
    def check_greeks_constraints(greeks: Dict[str, float]) -> Tuple[bool, str]:
        """Check if greeks are within acceptable constraints"""
        delta = abs(greeks.get('delta', 0.5))
        gamma = abs(greeks.get('gamma', 0.05))
        
        logger.debug(f"GREEKS_CHECK: START | delta={delta:.3f} | gamma={gamma:.4f}")
        
        if delta > OptionsTradingConfig.MAX_DELTA:
            message = f"Delta {delta} exceeds max {OptionsTradingConfig.MAX_DELTA}"
            logger.warning(f"GREEKS_CHECK: REJECTED | {message}")
            return False, message
        
        logger.debug(f"GREEKS_CHECK: DELTA_OK | delta={delta:.3f} <= {OptionsTradingConfig.MAX_DELTA}")
        
        if gamma > OptionsTradingConfig.MAX_GAMMA:
            message = f"Gamma {gamma} exceeds max {OptionsTradingConfig.MAX_GAMMA}"
            logger.warning(f"GREEKS_CHECK: REJECTED | {message}")
            return False, message
        
        logger.debug(f"GREEKS_CHECK: GAMMA_OK | gamma={gamma:.4f} <= {OptionsTradingConfig.MAX_GAMMA}")
        logger.info(f"GREEKS_CHECK: PASSED | delta={delta:.3f} | gamma={gamma:.4f}")
        
        return True, "Greeks acceptable"
    
    @staticmethod
    def check_expiry_validity(days_to_expiry: int) -> Tuple[bool, str]:
        """Check if expiry is valid for trading"""
        logger.debug(f"EXPIRY_CHECK: START | days_to_expiry={days_to_expiry}")
        
        # Avoid very short-term options (last day)
        if days_to_expiry < 1:
            message = "Option expires today - too short"
            logger.warning(f"EXPIRY_CHECK: REJECTED | {message} | days={days_to_expiry}")
            return False, message
        
        logger.debug(f"EXPIRY_CHECK: DURATION_OK | days={days_to_expiry} >= 1")
        
        # Also avoid very long-term (monthly if we prefer weekly)
        if OptionsTradingConfig.PREFER_WEEKLY and days_to_expiry > 14:
            message = "Expiry too far - prefer weekly contracts"
            logger.warning(f"EXPIRY_CHECK: REJECTED | {message} | days={days_to_expiry}")
            return False, message
        
        logger.info(f"EXPIRY_CHECK: PASSED | days_to_expiry={days_to_expiry}")
        
        return True, "Expiry valid"

# =============================================================================
# Signal Quality Filter for Options
# =============================================================================

class OptionsSignalQualityFilter:
    """Tracks and reports on options signal validation quality"""
    
    def __init__(self):
        self.total_signals = 0
        self.passed = 0
        self.failed_by_reason = {}
        self.processed_signals = []
        logger.debug(f"SIGNAL_FILTER: INITIALIZED | filter ready")
    
    def validate(self, alert: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Validate alert and track statistics"""
        self.total_signals += 1
        
        logger.debug(f"SIGNAL_FILTER: VALIDATE | total={self.total_signals} | symbol={alert.get('symbol', 'UNKNOWN')}")
        
        is_valid, message, processed_signal = OptionsSignalValidator.validate_options_signal(alert)
        
        if is_valid:
            self.passed += 1
            self.processed_signals.append(processed_signal)
            logger.debug(f"SIGNAL_FILTER: PASSED | passed={self.passed}/{self.total_signals} | pass_rate={(self.passed/self.total_signals*100):.1f}%")
            return True, processed_signal
        else:
            reason = message.split(':')[0] if ':' in message else message
            self.failed_by_reason[reason] = self.failed_by_reason.get(reason, 0) + 1
            logger.debug(f"SIGNAL_FILTER: REJECTED | failed={self.total_signals - self.passed} | reason={reason}")
            return False, None
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get validation statistics"""
        stats = {
            'total_signals': self.total_signals,
            'passed': self.passed,
            'failed': self.total_signals - self.passed,
            'pass_rate': (self.passed / self.total_signals * 100) if self.total_signals > 0 else 0,
            'failed_by_reason': self.failed_by_reason
        }
        logger.info(f"SIGNAL_FILTER: STATS | total={stats['total_signals']} | passed={stats['passed']} | failed={stats['failed']} | pass_rate={stats['pass_rate']:.1f}%")
        return stats

# =============================================================================
# Global validator instance
# =============================================================================

_options_signal_filter = None

def get_options_signal_filter() -> OptionsSignalQualityFilter:
    """Get or create signal filter instance"""
    global _options_signal_filter
    if _options_signal_filter is None:
        _options_signal_filter = OptionsSignalQualityFilter()
    return _options_signal_filter
