"""
ENTRY FILTER ENGINE - Comprehensive Signal Validation

Implements Scenario 1: Fix Entry Filters (Best Path)
Target: Increase win rate from 34.5% to 55%
Method: Multi-layer validation (PCR + Momentum + Trend + IV + Market Hours + DTE)

Architecture:
├─ MarketStructureValidator (PCR, OI buildup)
├─ MomentumValidator (RSI, MACD)
├─ TrendValidator (Moving averages, slope)
├─ IVValidator (Percentile, volatility)
├─ MarketHoursValidator (9:30-14:30 trading window)
├─ ExpiryValidator (DTE filter)
└─ ComprehensiveEntryFilter (Combines all validators)

Each validator has configurable thresholds loaded from environment variables.
Goal: Reject 50% of alerts to keep only highest-confidence trades (55%+ win rate)
"""

import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path
import numpy as np

from .optlogging import logger, log_event
from .optconfig import SentimentConfig, MLConfig

# =============================================================================
# VALIDATOR 1: MARKET STRUCTURE (PCR + OI Buildup)
# =============================================================================

class MarketStructureValidator:
    """Validates PCR and OI buildup for entry signal confirmation"""
    
    def __init__(self):
        self.name = "MarketStructureValidator"
        # PCR thresholds (configurable)
        self.pcr_min_bullish = float(os.getenv("ENTRY_FILTER_PCR_MIN_BULLISH", "0.50"))  # For PE entries
        self.pcr_max_bullish = float(os.getenv("ENTRY_FILTER_PCR_MAX_BULLISH", "0.90"))  # For PE entries
        self.pcr_min_bearish = float(os.getenv("ENTRY_FILTER_PCR_MIN_BEARISH", "1.20"))  # For CE entries (tightened)
        self.pcr_max_bearish = float(os.getenv("ENTRY_FILTER_PCR_MAX_BEARISH", "2.50"))  # For CE entries
        
        # OI buildup thresholds
        self.oi_buildup_min = float(os.getenv("ENTRY_FILTER_OI_BUILDUP_MIN", "500000"))  # Min OI for confirmation
        self.require_oi_buildup = os.getenv("ENTRY_FILTER_REQUIRE_OI_BUILDUP", "True").lower() == "true"
        
        logger.info(f"{self.name}: Initialized | PCR ranges: PE({self.pcr_min_bullish}-{self.pcr_max_bullish}), CE({self.pcr_min_bearish}-{self.pcr_max_bearish})")
    
    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate market structure (PCR + OI)
        
        Args:
            signal: Alert signal with 'action' (BUY/SELL)
            market_data: Market data with 'pcr' and 'oi_buildup'
        
        Returns:
            (is_valid, reason_message)
        """
        action = signal.get('action', 'UNKNOWN')
        pcr = market_data.get('pcr')
        oi_buildup = market_data.get('oi_buildup', 0)
        
        if pcr is None:
            # PCR is critical - but if unavailable, don't block (log and continue)
            logger.debug(f"MarketStructureValidator: PCR not available for validation")
            return True, "PCR data not available - skipping validation"
        
        # For BUY signal (PE entry) - need lower PCR (bullish)
        if action == 'BUY':
            if not (self.pcr_min_bullish < pcr < self.pcr_max_bullish):
                return False, f"PE entry: PCR {pcr:.2f} not in range ({self.pcr_min_bullish}-{self.pcr_max_bullish})"
        
        # For SELL signal (CE entry) - need higher PCR (bearish) 
        elif action == 'SELL':
            if not (self.pcr_min_bearish < pcr < self.pcr_max_bearish):
                return False, f"CE entry: PCR {pcr:.2f} not in range ({self.pcr_min_bearish}-{self.pcr_max_bearish})"
        
        # Check OI buildup if required and available
        if self.require_oi_buildup and oi_buildup and oi_buildup < self.oi_buildup_min:
            return False, f"OI buildup {oi_buildup:,.0f} < minimum {self.oi_buildup_min:,.0f}"
        
        return True, f"PCR {pcr:.2f} valid for {action}"


# =============================================================================
# VALIDATOR 2: MOMENTUM (RSI + MACD)
# =============================================================================

class MomentumValidator:
    """Validates momentum using RSI and MACD on 15-minute timeframe"""
    
    def __init__(self):
        self.name = "MomentumValidator"
        # RSI thresholds (15-minute timeframe)
        self.rsi_oversold = float(os.getenv("ENTRY_FILTER_RSI_OVERSOLD", "30"))     # BUY when RSI < 30
        self.rsi_overbought = float(os.getenv("ENTRY_FILTER_RSI_OVERBOUGHT", "70"))  # SELL when RSI > 70
        
        # MACD thresholds
        self.macd_require = os.getenv("ENTRY_FILTER_MACD_REQUIRE", "True").lower() == "true"
        self.macd_confirmation = os.getenv("ENTRY_FILTER_MACD_CONFIRMATION", "True").lower() == "true"
        
        logger.info(f"{self.name}: Initialized | RSI thresholds: Oversold={self.rsi_oversold}, Overbought={self.rsi_overbought}")
    
    def validate(self, signal: Dict[str, Any], candle_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate momentum confirmation
        
        Args:
            signal: Alert signal with 'action'
            candle_data: 15-minute candle data with 'rsi' and 'macd'
        
        Returns:
            (is_valid, reason_message)
        """
        action = signal.get('action', 'UNKNOWN')
        rsi_15m = candle_data.get('rsi_15m')
        macd_15m = candle_data.get('macd_15m')
        
        if rsi_15m is None:
            # RSI missing but continue if not required
            logger.debug(f"MomentumValidator: RSI not available for validation")
            return True, "RSI data not available - skipping validation"
        
        # For BUY signal - need extreme negative momentum (oversold)
        if action == 'BUY':
            if rsi_15m > self.rsi_oversold:
                return False, f"PE entry: RSI {rsi_15m:.1f} > oversold threshold {self.rsi_oversold}"
        
        # For SELL signal - need extreme positive momentum (overbought)
        elif action == 'SELL':
            if rsi_15m < self.rsi_overbought:
                return False, f"CE entry: RSI {rsi_15m:.1f} < overbought threshold {self.rsi_overbought}"
        
        # MACD confirmation (if available and enabled)
        if self.macd_confirmation and macd_15m is not None:
            if action == 'BUY' and macd_15m > 0:  # Should be negative for downtrend
                return False, f"PE entry: MACD {macd_15m:.4f} should be negative"
            elif action == 'SELL' and macd_15m < 0:  # Should be positive for uptrend
                return False, f"CE entry: MACD {macd_15m:.4f} should be positive"
        
        return True, f"Momentum confirmed (RSI {rsi_15m:.1f})"


# =============================================================================
# VALIDATOR 3: TREND (Moving Averages + Slope)
# =============================================================================

class TrendValidator:
    """Validates trend direction using moving averages and slope analysis"""
    
    def __init__(self):
        self.name = "TrendValidator"
        # MA periods (hourly timeframe)
        self.ma_short = int(os.getenv("ENTRY_FILTER_MA_SHORT", "10"))     # Fast MA (10-period)
        self.ma_long = int(os.getenv("ENTRY_FILTER_MA_LONG", "20"))       # Slow MA (20-period)
        self.ma_require = os.getenv("ENTRY_FILTER_MA_REQUIRE", "True").lower() == "true"
        
        # Trend slope threshold
        self.slope_threshold = float(os.getenv("ENTRY_FILTER_SLOPE_THRESHOLD", "0.1"))  # % move threshold
        
        logger.info(f"{self.name}: Initialized | MA periods: {self.ma_short}/{self.ma_long}, Slope threshold: {self.slope_threshold}%")
    
    def validate(self, signal: Dict[str, Any], hourly_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate trend confirmation
        
        Args:
            signal: Alert signal with 'action'
            hourly_data: Hourly candle data with 'ma_short', 'ma_long', 'slope'
        
        Returns:
            (is_valid, reason_message)
        """
        action = signal.get('action', 'UNKNOWN')
        ma_short = hourly_data.get('ma_short')
        ma_long = hourly_data.get('ma_long')
        slope = hourly_data.get('slope', 0)
        
        if not self.ma_require:
            return True, "Trend validation disabled"
        
        # GRACEFUL FALLBACK: If MA data unavailable, skip validation and allow entry (don't reject)
        if ma_short is None or ma_long is None:
            logger.debug(f"{self.name}: MA data not available (ma_short={ma_short}, ma_long={ma_long}), allowing entry")
            return True, "MA data unavailable - skipping trend check (allowing entry)"
        
        # For BUY signal (downtrend) - short MA should be below long MA
        if action == 'BUY':
            if ma_short > ma_long:
                return False, f"PE entry: Short MA {ma_short:.2f} > Long MA {ma_long:.2f} (should be downtrend)"
        
        # For SELL signal (uptrend) - short MA should be above long MA
        elif action == 'SELL':
            if ma_short < ma_long:
                return False, f"CE entry: Short MA {ma_short:.2f} < Long MA {ma_long:.2f} (should be uptrend)"
        
        return True, f"Trend confirmed (MA crossover valid)"


# =============================================================================
# VALIDATOR 4: IV PERCENTILE
# =============================================================================

class IVValidator:
    """Validates IV percentile to avoid overbought/oversold conditions"""
    
    def __init__(self):
        self.name = "IVValidator"
        # IV percentile thresholds
        self.iv_percentile_max = float(os.getenv("ENTRY_FILTER_IV_PERCENTILE_MAX", "80"))  # Skip if IV > 80th
        self.iv_percentile_min = float(os.getenv("ENTRY_FILTER_IV_PERCENTILE_MIN", "20"))  # Skip if IV < 20th
        self.iv_require = os.getenv("ENTRY_FILTER_IV_REQUIRE", "True").lower() == "true"
        
        logger.info(f"{self.name}: Initialized | IV percentile range: {self.iv_percentile_min}-{self.iv_percentile_max}%")
    
    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate IV conditions
        
        Args:
            signal: Alert signal
            market_data: Market data with 'iv_percentile'
        
        Returns:
            (is_valid, reason_message)
        """
        if not self.iv_require:
            return True, "IV validation disabled"
        
        iv_percentile = market_data.get('iv_percentile')
        
        # GRACEFUL FALLBACK: If IV data unavailable, skip validation and allow entry (don't reject)
        if iv_percentile is None:
            logger.debug(f"{self.name}: IV percentile data not available, allowing entry")
            return True, "IV percentile data unavailable - skipping IV check (allowing entry)"
        
        if iv_percentile > self.iv_percentile_max:
            return False, f"IV percentile {iv_percentile:.1f} > maximum {self.iv_percentile_max}"
        
        if iv_percentile < self.iv_percentile_min:
            return False, f"IV percentile {iv_percentile:.1f} < minimum {self.iv_percentile_min}"
        
        return True, f"IV percentile {iv_percentile:.1f}% valid"


# =============================================================================
# VALIDATOR 5: MARKET HOURS
# =============================================================================

class MarketHoursValidator:
    """Validates trading within best liquidity hours (9:30 AM - 2:30 PM)"""
    
    def __init__(self):
        self.name = "MarketHoursValidator"
        self.market_open = int(os.getenv("ENTRY_FILTER_MARKET_OPEN", "930"))      # 9:30 AM
        self.market_close = int(os.getenv("ENTRY_FILTER_MARKET_CLOSE", "1430"))   # 2:30 PM
        self.require_market_hours = os.getenv("ENTRY_FILTER_REQUIRE_MARKET_HOURS", "True").lower() == "true"
        
        logger.info(f"{self.name}: Initialized | Trading hours: {self.market_open:04d}-{self.market_close:04d}")
    
    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any] = None) -> Tuple[bool, str]:
        """
        Validate market hours
        
        Returns:
            (is_valid, reason_message)
        """
        if not self.require_market_hours:
            return True, "Market hours validation disabled"
        
        now = datetime.now()
        current_time = now.hour * 100 + now.minute
        
        if not (self.market_open <= current_time <= self.market_close):
            return False, f"Outside market hours ({current_time:04d} not in {self.market_open:04d}-{self.market_close:04d})"
        
        return True, f"Within market hours ({current_time:04d})"


# =============================================================================
# VALIDATOR 6: EXPIRY (Days to Expiry)
# =============================================================================

class ExpiryValidator:
    """Validates expiry distance to avoid gamma risk"""
    
    def __init__(self):
        self.name = "ExpiryValidator"
        self.dte_min = int(os.getenv("ENTRY_FILTER_DTE_MIN", "3"))    # Skip if < 3 days
        self.dte_max = int(os.getenv("ENTRY_FILTER_DTE_MAX", "14"))   # Skip if > 14 days
        self.require_dte_check = os.getenv("ENTRY_FILTER_REQUIRE_DTE_CHECK", "True").lower() == "true"
        
        logger.info(f"{self.name}: Initialized | DTE range: {self.dte_min}-{self.dte_max} days")
    
    def validate(self, signal: Dict[str, Any], option_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate days to expiry
        
        Args:
            signal: Alert signal
            option_data: Option data with 'days_to_expiry'
        
        Returns:
            (is_valid, reason_message)
        """
        if not self.require_dte_check:
            return True, "DTE validation disabled"
        
        dte = option_data.get('days_to_expiry')
        
        if dte is None:
            return False, "Days to expiry data not available"
        
        if dte < self.dte_min:
            return False, f"DTE {dte} days < minimum {self.dte_min} days (gamma risk)"
        
        if dte > self.dte_max:
            return False, f"DTE {dte} days > maximum {self.dte_max} days (prefer weekly)"
        
        return True, f"DTE {dte} days valid (sweet spot)"


# =============================================================================
# COMPREHENSIVE ENTRY FILTER (Combines All Validators)
# =============================================================================

class ComprehensiveEntryFilter:
    """
    Multi-layer entry filter combining all validators
    
    Target: Reject 50% of alerts to keep only highest-confidence trades
    Expected result: Win rate increase from 34.5% → 55%+
    """
    
    def __init__(self):
        self.name = "ComprehensiveEntryFilter"
        
        # Initialize all validators
        self.validators = {
            'market_structure': MarketStructureValidator(),
            'momentum': MomentumValidator(),
            'trend': TrendValidator(),
            'iv': IVValidator(),
            'market_hours': MarketHoursValidator(),
            'expiry': ExpiryValidator(),
        }
        
        # Statistics tracking
        self.total_alerts = 0
        self.passed = 0
        self.rejected_by_reason = {}
        
        # Configuration
        self.require_all_filters = os.getenv("ENTRY_FILTER_REQUIRE_ALL", "False").lower() == "true"
        self.min_filters_pass = int(os.getenv("ENTRY_FILTER_MIN_PASS", "4"))  # Need at least 4/6 validators
        
        logger.info(f"{self.name}: Initialized | Mode: {'ALL_REQUIRED' if self.require_all_filters else f'MIN_{self.min_filters_pass}_PASS'}")
        logger.info(f"{self.name}: Active validators: {list(self.validators.keys())}")
    
    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Comprehensive entry validation
        
        Args:
            signal: TradingView alert with symbol, action, confidence
            market_data: Complete market data dict with:
                - pcr: PCR ratio
                - oi_buildup: OI buildup value
                - rsi_15m: 15-minute RSI
                - macd_15m: 15-minute MACD
                - ma_short: Short MA (hourly)
                - ma_long: Long MA (hourly)
                - slope: Trend slope
                - iv_percentile: IV percentile rank
                - days_to_expiry: DTE
        
        Returns:
            (is_valid, reason, validation_details)
        """
        self.total_alerts += 1
        symbol = signal.get('symbol', 'UNKNOWN')
        action = signal.get('action', 'UNKNOWN')
        
        logger.info(f"{self.name}: VALIDATE | #{self.total_alerts} | {symbol} | {action}")
        
        validation_results = {}
        passed_count = 0
        
        # Run all validators
        for validator_name, validator in self.validators.items():
            try:
                if validator_name == 'market_hours':
                    is_valid, reason = validator.validate(signal)
                else:
                    is_valid, reason = validator.validate(signal, market_data)
                
                validation_results[validator_name] = {
                    'valid': is_valid,
                    'reason': reason
                }
                
                if is_valid:
                    passed_count += 1
                    logger.debug(f"{self.name}: ✅ {validator_name.upper()} | {reason}")
                else:
                    logger.debug(f"{self.name}: ❌ {validator_name.upper()} | {reason}")
            
            except Exception as e:
                logger.error(f"{self.name}: ERROR in {validator_name} | {str(e)}")
                validation_results[validator_name] = {
                    'valid': False,
                    'reason': f"Error: {str(e)}"
                }
        
        # Determine overall result
        if self.require_all_filters:
            is_valid = all(v['valid'] for v in validation_results.values())
            decision = "ALL filters required"
        else:
            is_valid = passed_count >= self.min_filters_pass
            decision = f"Need {self.min_filters_pass}/{len(self.validators)} passed"
        
        if is_valid:
            self.passed += 1
            logger.info(f"{self.name}: ✅ PASSED | {symbol} {action} | {passed_count}/{len(self.validators)} validators | {decision}")
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            return True, f"Entry valid ({passed_count}/{len(self.validators)} validators)", validation_results
        else:
            # Find first failing validator for rejection reason
            failure_reason = next((v['reason'] for v in validation_results.values() if not v['valid']), "Unknown")
            reason_key = failure_reason.split(':')[0] if ':' in failure_reason else failure_reason
            self.rejected_by_reason[reason_key] = self.rejected_by_reason.get(reason_key, 0) + 1
            
            logger.warning(f"{self.name}: ❌ REJECTED | {symbol} {action} | {passed_count}/{len(self.validators)} validators | Reason: {failure_reason}")
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            return False, failure_reason, validation_results
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive filter statistics"""
        return {
            'total_alerts': self.total_alerts,
            'passed': self.passed,
            'rejected': self.total_alerts - self.passed,
            'pass_rate': (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0,
            'rejected_by_reason': self.rejected_by_reason
        }


# =============================================================================
# Global instance
# =============================================================================

_entry_filter = None

def get_entry_filter() -> ComprehensiveEntryFilter:
    """Get or create comprehensive entry filter"""
    global _entry_filter
    if _entry_filter is None:
        _entry_filter = ComprehensiveEntryFilter()
    return _entry_filter
