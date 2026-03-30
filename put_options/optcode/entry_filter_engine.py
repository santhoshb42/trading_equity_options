"""
ENTRY FILTER ENGINE - Comprehensive Signal Validation for PE (PUT Options)

Implements Signal Validation for Put Options (PE) Trading
Target: Increase win rate from 34.5% to 55%
Method: Multi-layer validation (PCR + Momentum + Trend + IV + Market Hours + DTE)

KEY DIFFERENCES FROM CE BOT:
- Entry Action: SELL (not BUY) - PE profits from downside
- Momentum Check: RSI for downtrend (RSI < 45 for SELL), not uptrend
- Trend Check: Short MA BELOW long MA (downtrend), not above
- Signal Direction: SELL action means go long on PUT option
- Greeks: For PE, losses occur when underlying RISES

Architecture:
├─ MarketStructureValidator (PCR, OI buildup) - BEARISH thresholds
├─ MomentumValidator (RSI, MACD) - DOWNTREND confirmation
├─ TrendValidator (Moving averages, slope) - DOWNTREND check  
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
# VALIDATOR 0: PREMIUM FILTER (Minimum Entry Premium Check)
# =============================================================================

class PremiumValidator:
    """Validates minimum premium to avoid low-liquidity gap risk trades"""
    
    def __init__(self):
        self.name = "PremiumValidator"
        self.min_premium = float(os.getenv("ENTRY_FILTER_MIN_PREMIUM", "5.0"))  # Min ₹5 premium
        logger.info(f"{self.name}: Initialized | Min premium: ₹{self.min_premium}")
    
    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate that entry premium is above minimum threshold.
        Low premiums (< ₹5) have high gap risk and slippage.
        
        Args:
            signal: Alert signal
            market_data: Market data with 'entry_premium'
        
        Returns:
            (is_valid, reason)
        """
        entry_premium = market_data.get('entry_premium', 0)
        
        if entry_premium >= self.min_premium:
            return True, f"Premium ₹{entry_premium:.2f} >= ₹{self.min_premium} (sufficient liquidity)"
        else:
            return False, f"Premium ₹{entry_premium:.2f} < ₹{self.min_premium} (low liquidity, high gap risk)"

# =============================================================================
# VALIDATOR 1: MARKET STRUCTURE (PCR + OI Buildup)
# =============================================================================

class MarketStructureValidator:
    """Validates PCR and OI buildup for entry signal confirmation"""
    
    def __init__(self):
        self.name = "MarketStructureValidator"
        # PCR thresholds (configurable)
        self.pcr_min_bullish = float(os.getenv("ENTRY_FILTER_PCR_MIN_BULLISH", "0.15"))  # For CE entries - lowered to 0.15 to catch momentum rally
        self.pcr_max_bullish = float(os.getenv("ENTRY_FILTER_PCR_MAX_BULLISH", "0.90"))  # For CE entries
        self.pcr_min_bearish = float(os.getenv("ENTRY_FILTER_PCR_MIN_BEARISH", "1.20"))  # For PE entries (tightened)
        self.pcr_max_bearish = float(os.getenv("ENTRY_FILTER_PCR_MAX_BEARISH", "2.50"))  # For PE entries
        
        # OI buildup thresholds
        self.oi_buildup_min = float(os.getenv("ENTRY_FILTER_OI_BUILDUP_MIN", "500000"))  # Min OI for confirmation
        self.require_oi_buildup = os.getenv("ENTRY_FILTER_REQUIRE_OI_BUILDUP", "True").lower() == "true"
        
        # PCR adaptive thresholds
        self.pcr_min_normal = float(os.getenv("ENTRY_FILTER_PCR_MIN_NORMAL", "0.15"))  # DTE > 1 day
        self.pcr_max_normal = float(os.getenv("ENTRY_FILTER_PCR_MAX_NORMAL", "1.30"))  # DTE > 1 day
        self.pcr_max_expiry = float(os.getenv("ENTRY_FILTER_PCR_MAX_EXPIRY", "1.20"))  # DTE = 0, ignore min
        self.trend_strength_threshold = float(os.getenv("ENTRY_FILTER_TREND_STRENGTH", "0.70"))  # For PCR override
        
        logger.info(f"{self.name}: Initialized | PCR ranges: Normal({self.pcr_min_normal}-{self.pcr_max_normal}), Expiry(0.0-{self.pcr_max_expiry})")
    
    def _is_pcr_acceptable(self, pcr: float, dte: int, trend_strength: float = 0.5, volume_spike: bool = False) -> Tuple[bool, str]:
        """
        Adaptive PCR validation based on DTE and trend.
        
        Args:
            pcr: Current put-call ratio
            dte: Days to expiry
            trend_strength: Normalized trend indicator 0-1 (e.g., from EMA+VWAP alignment)
            volume_spike: Whether current volume > 1.2x average
        
        Returns:
            (is_acceptable, reason)
        """
        # Step 1: DTE-based min/max thresholds
        if dte > 1:
            min_pcr = self.pcr_min_normal  # Relax for normal trades
            max_pcr = self.pcr_max_normal
        else:  # DTE = 0 or 1 (expiry/near-expiry)
            min_pcr = 0.0  # Ignore minimum (ignore low PCR on expiry)
            max_pcr = self.pcr_max_expiry  # Still reject extremely bearish
        
        # Step 2: Trend/momentum override
        # If market is strongly bullish (trend + volume), ignore low PCR completely
        strong_uptrend = trend_strength > self.trend_strength_threshold and volume_spike
        if strong_uptrend:
            min_pcr = 0.0  # Allow any PCR if trend is strong
            logger.debug(f"PCR_ADAPTIVE: Strong uptrend detected (trend={trend_strength:.2f}, volume_spike={volume_spike}) - PCR filter relaxed")
        
        # Step 3: Final check
        if min_pcr <= pcr <= max_pcr:
            return True, f"PCR {pcr:.2f} acceptable (DTE={dte}, trend={trend_strength:.2f})"
        else:
            reason = f"PCR {pcr:.2f} out of range ({min_pcr:.2f}-{max_pcr:.2f}) | DTE={dte} days"
            return False, reason

    
    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate market structure (PCR + OI) with adaptive logic.
        
        Args:
            signal: Alert signal with 'action' (BUY/SELL)
            market_data: Market data with 'pcr', 'oi_buildup', 'days_to_expiry', 'trend_strength', 'volume_spike'
        
        Returns:
            (is_valid, reason_message)
        """
        action = signal.get('action', 'UNKNOWN')
        pcr = market_data.get('pcr')
        oi_buildup = market_data.get('oi_buildup', 0)
        dte = market_data.get('days_to_expiry', 7)
        trend_strength = market_data.get('trend_strength', 0.5)  # Default 0.5 (neutral)
        volume_spike = market_data.get('volume_spike', False)
        
        if pcr is None:
            # PCR is critical - but if unavailable, don't block (log and continue)
            logger.debug(f"MarketStructureValidator: PCR not available for validation")
            return True, "PCR data not available - skipping validation"
        
        # Use adaptive PCR validation (applies to CE/BUY trades since we're CE-only)
        pcr_acceptable, pcr_reason = self._is_pcr_acceptable(pcr, dte, trend_strength, volume_spike)
        
        if not pcr_acceptable:
            return False, pcr_reason
        
        # Check OI buildup if required and available
        if self.require_oi_buildup and oi_buildup and oi_buildup < self.oi_buildup_min:
            return False, f"OI buildup {oi_buildup:,.0f} < minimum {self.oi_buildup_min:,.0f}"
        
        return True, f"Market structure OK | PCR {pcr:.2f} | DTE {dte} days"


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
        Validate momentum confirmation for PE (PUT) options
        
        For PE (Put Options):
        - SELL action = Entry into PE (profit from downside)
        - Need DOWNTREND confirmation: RSI < 45 (strong downtrend momentum)
        - MACD should be negative (downtrend)
        
        Args:
            signal: Alert signal with 'action' (BUY=ignored for PE bot, SELL=PE entry)
            candle_data: 15-minute candle data with 'rsi' and 'macd'
        
        Returns:
            (is_valid, reason_message)
        """
        action = signal.get('action', 'UNKNOWN')
        rsi_15m = candle_data.get('rsi_15m')
        macd_15m = candle_data.get('macd_15m')
        
        # Defensive: Convert dict rsi_15m to None (should not happen but fixing bug)
        if isinstance(rsi_15m, dict):
            logger.warning(f"MomentumValidator: rsi_15m is dict (error object), treating as None | {rsi_15m}")
            rsi_15m = None
        
        if rsi_15m is None:
            # RSI missing but continue if not required
            logger.debug(f"MomentumValidator: RSI not available for validation")
            return True, "RSI data not available - skipping validation"
        
        # For SELL signal (PE/PUT entry) - need strong NEGATIVE momentum (RSI <= 45)
        if action == 'SELL':
            rsi_max_put = float(os.getenv("ENTRY_FILTER_RSI_MAX_PUT", "45"))  # Maximum RSI for PUT entry (downtrend)
            if rsi_15m > rsi_max_put:
                return False, f"PE entry: RSI {rsi_15m:.1f} > momentum threshold {rsi_max_put} (need strong downtrend)"
        
        # For BUY signal (should not happen in PE bot, log warning) - allow through
        elif action == 'BUY':
            logger.warning(f"MomentumValidator: BUY action received in PE bot (unusual), allowing through")
        
        # MACD confirmation (if available and enabled)
        # Defensive: MACD should be dict with keys, not a bare value
        if self.macd_confirmation and isinstance(macd_15m, dict) and 'macd' in macd_15m:
            macd_value = macd_15m.get('macd', 0)
            if action == 'SELL' and macd_value > 0:  # Should be NEGATIVE for DOWNTREND (PE entry)
                return False, f"PE entry: MACD {macd_value:.4f} should be negative (downtrend)"
        
        return True, f"Momentum confirmed for PE (RSI {rsi_15m:.1f})"


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
        
        # For BUY signal (CALL entry / uptrend) - short MA should be above long MA
        if action == 'BUY':
            if ma_short < ma_long:
                return False, f"CE entry: Short MA {ma_short:.2f} < Long MA {ma_long:.2f} (should be uptrend for CALL entry)"
        
        # For SELL signal (downtrend) - short MA should be below long MA
        elif action == 'SELL':
            if ma_short > ma_long:
                return False, f"PE entry: Short MA {ma_short:.2f} > Long MA {ma_long:.2f} (should be downtrend)"
        
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
    """Validates expiry distance - allows expiry day trading for NSE weekly options"""
    
    def __init__(self):
        self.name = "ExpiryValidator"
        # NSE expiry Thursdays have peak liquidity at 0-1 DTE, so we allow trading through expiry
        self.dte_min = int(os.getenv("ENTRY_FILTER_DTE_MIN", "0"))    # Allow 0 DTE (expiry day itself)
        self.dte_max = int(os.getenv("ENTRY_FILTER_DTE_MAX", "30"))   # Max 30 days (monthly contracts, next month acceptable)
        self.require_dte_check = os.getenv("ENTRY_FILTER_REQUIRE_DTE_CHECK", "True").lower() == "true"
        
        logger.info(f"{self.name}: Initialized | DTE range: {self.dte_min}-{self.dte_max} days | NSE expiry day trading enabled")
    
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
        
        # Allow 0 DTE (expiry day) for NSE expiry Thursday scalping with peak liquidity
        if dte < self.dte_min and self.dte_min > 0:
            return False, f"DTE {dte} days < minimum {self.dte_min} days (too close to expiry)"
        
        if dte > self.dte_max:
            return False, f"DTE {dte} days > maximum {self.dte_max} days (prefer weekly)"
        
        # Special handling for expiry day and near-expiry trading
        if dte == 0:
            return True, "DTE 0 - EXPIRY DAY (peak liquidity for scalping)"
        elif dte <= 3:
            return True, f"DTE {dte} days - close to expiry (high theta decay)"
        else:
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
            'premium': PremiumValidator(),  # NEW: Check minimum premium first
            'market_structure': MarketStructureValidator(),
            'momentum': MomentumValidator(),
            'trend': TrendValidator(),
            'iv': IVValidator(),
            'market_hours': MarketHoursValidator(),
            'expiry': ExpiryValidator(),
        }
        
        # Blacklist of worst-performing symbols from analysis
        # These symbols have consistently lost money (0-30% win rate or consistent losses)
        self.blacklist_symbols = {
            # Always Losers (0% win rate): -₹56,572
            'ETERNAL', 'JIOFIN', 'PIDILITIND', 'ANGELONE',
            # Frequent Losers (<30% win rate): -₹212,226
            'IRFC', 'CONCOR', 'DELHIVERY', 'WAAREEENER', 'PFC', 'GAIL', 
            'HUDCO', 'ADANIGREEN', 'DIXON', 'BHARATFORG', 'TATATECH', 
            'COLPAL', 'MCX', 'VBL', 'INFY', 'HDFCLIFE', 'LT', 'SOLARINDS', 
            'EXIDEIND', 'CANBK',
            # Recently Added Consistent Losers (Feb 5): -₹21,955 cumulative
            'SAMMAANCAP'  # 6 trades: 3W/3L, net -₹21,955, avg loss -₹10,291
        }
        
        # Statistics tracking
        self.total_alerts = 0
        self.passed = 0
        self.rejected_by_reason = {}
        
        # Configuration
        self.require_all_filters = os.getenv("ENTRY_FILTER_REQUIRE_ALL", "False").lower() == "true"
        self.min_filters_pass = int(os.getenv("ENTRY_FILTER_MIN_PASS", "5"))  # Need at least 5/7 validators (was 4/6, now 5/7 with premium filter)
        self.enable_blacklist = os.getenv("ENTRY_FILTER_ENABLE_BLACKLIST", "True").lower() == "true"
        
        logger.info(f"{self.name}: Initialized | Mode: {'ALL_REQUIRED' if self.require_all_filters else f'MIN_{self.min_filters_pass}_PASS'}")
        logger.info(f"{self.name}: Blacklist enabled: {self.enable_blacklist} | {len(self.blacklist_symbols)} symbols blacklisted")
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
        
        # Check blacklist first (fast rejection)
        if self.enable_blacklist and symbol in self.blacklist_symbols:
            logger.warning(f"{self.name}: ❌ BLACKLISTED | {symbol} is on worst-performers list (skip)")
            return False, f"Symbol {symbol} is blacklisted (consistent underperformance)", {}
        
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

        premium_result = validation_results.get('premium')
        if premium_result and not premium_result['valid']:
            failure_reason = premium_result['reason']
            reason_key = failure_reason.split(':')[0] if ':' in failure_reason else failure_reason
            self.rejected_by_reason[reason_key] = self.rejected_by_reason.get(reason_key, 0) + 1
            logger.warning(
                f"{self.name}: ❌ REJECTED | {symbol} {action} | premium hard gate failed | Reason: {failure_reason}"
            )
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            return False, failure_reason, validation_results
        
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
