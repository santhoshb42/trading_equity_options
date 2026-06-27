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
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path
from .optlogging import logger, log_event
from .optconfig import SentimentConfig, MLConfig, DATA_DIR

# =============================================================================
# VALIDATOR 0: PREMIUM FILTER (Minimum Entry Premium Check)
# =============================================================================

class PremiumValidator:
    """Validates minimum premium to avoid low-liquidity gap risk trades"""
    
    def __init__(self):
        self.name = "PremiumValidator"
        self.min_premium = float(os.getenv("ENTRY_FILTER_MIN_PREMIUM", "2.0"))
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
        self.pcr_min_bullish = float(os.getenv("ENTRY_FILTER_PCR_MIN_BULLISH", "0.20"))
        self.pcr_max_bullish = float(os.getenv("ENTRY_FILTER_PCR_MAX_BULLISH", "0.85"))
        self.pcr_min_bearish = float(os.getenv("ENTRY_FILTER_PCR_MIN_BEARISH", "1.20"))  # For PE entries (tightened)
        self.pcr_max_bearish = float(os.getenv("ENTRY_FILTER_PCR_MAX_BEARISH", "2.50"))  # For PE entries
        
        # OI buildup thresholds
        self.oi_buildup_min = float(os.getenv("ENTRY_FILTER_OI_BUILDUP_MIN", "750000"))
        self.require_oi_buildup = os.getenv("ENTRY_FILTER_REQUIRE_OI_BUILDUP", "True").lower() == "true"
        
        # PCR adaptive thresholds
        self.pcr_min_normal = float(os.getenv("ENTRY_FILTER_PCR_MIN_NORMAL", "0.20"))
        self.pcr_max_normal = float(os.getenv("ENTRY_FILTER_PCR_MAX_NORMAL", "1.15"))
        self.pcr_max_expiry = float(os.getenv("ENTRY_FILTER_PCR_MAX_EXPIRY", "1.05"))
        self.pre_breakout_pcr_min_normal = float(os.getenv("ENTRY_FILTER_PRE_BREAKOUT_PCR_MIN_NORMAL", "0.05"))
        self.pre_breakout_pcr_max_normal = float(os.getenv("ENTRY_FILTER_PRE_BREAKOUT_PCR_MAX_NORMAL", "1.15"))
        self.pullback_pcr_min_normal = float(os.getenv("ENTRY_FILTER_PULLBACK_PCR_MIN_NORMAL", "0.10"))
        self.pullback_pcr_max_normal = float(os.getenv("ENTRY_FILTER_PULLBACK_PCR_MAX_NORMAL", "1.15"))
        self.momentum_pcr_min_normal = float(os.getenv("ENTRY_FILTER_MOMENTUM_PCR_MIN_NORMAL", "0.20"))
        self.momentum_pcr_max_normal = float(os.getenv("ENTRY_FILTER_MOMENTUM_PCR_MAX_NORMAL", "1.05"))
        # MACD Reversal PCR thresholds (wider than momentum, tighter than normal)
        self.macd_reversal_pcr_min_normal = float(os.getenv("ENTRY_FILTER_MACD_REVERSAL_PCR_MIN_NORMAL", "0.15"))
        self.macd_reversal_pcr_max_normal = float(os.getenv("ENTRY_FILTER_MACD_REVERSAL_PCR_MAX_NORMAL", "1.10"))
        # Deep MACD Reversal PCR thresholds (slightly wider to catch extreme reversals)
        self.deep_macd_reversal_pcr_min_normal = float(os.getenv("ENTRY_FILTER_DEEP_MACD_REVERSAL_PCR_MIN_NORMAL", "0.10"))
        self.deep_macd_reversal_pcr_max_normal = float(os.getenv("ENTRY_FILTER_DEEP_MACD_REVERSAL_PCR_MAX_NORMAL", "1.10"))
        # Momentum Acceleration PCR thresholds (normal-like, already in uptrend)
        self.momentum_accel_pcr_min_normal = float(os.getenv("ENTRY_FILTER_MOMENTUM_ACCELERATION_PCR_MIN_NORMAL", "0.15"))
        self.momentum_accel_pcr_max_normal = float(os.getenv("ENTRY_FILTER_MOMENTUM_ACCELERATION_PCR_MAX_NORMAL", "1.10"))
        self.pre_breakout_oi_buildup_min = float(os.getenv("ENTRY_FILTER_PRE_BREAKOUT_OI_BUILDUP_MIN", "0"))
        self.pullback_oi_buildup_min = float(os.getenv("ENTRY_FILTER_PULLBACK_OI_BUILDUP_MIN", "0"))
        self.momentum_oi_buildup_min = float(os.getenv("ENTRY_FILTER_MOMENTUM_OI_BUILDUP_MIN", "50000"))
        self.macd_reversal_oi_buildup_min = float(os.getenv("ENTRY_FILTER_MACD_REVERSAL_OI_BUILDUP_MIN", "30000"))
        # Deep MACD Reversal OI requirement (lower than regular reversal)
        self.deep_macd_reversal_oi_buildup_min = float(os.getenv("ENTRY_FILTER_DEEP_MACD_REVERSAL_OI_BUILDUP_MIN", "20000"))
        # Momentum Acceleration OI requirement (standard buildup validation)
        self.momentum_accel_oi_buildup_min = float(os.getenv("ENTRY_FILTER_MOMENTUM_ACCELERATION_OI_BUILDUP_MIN", "25000"))
        self.trend_strength_threshold = float(os.getenv("ENTRY_FILTER_TREND_STRENGTH", "0.40"))
        
        logger.info(f"{self.name}: Initialized | PCR ranges: Normal({self.pcr_min_normal}-{self.pcr_max_normal}), Expiry(0.0-{self.pcr_max_expiry}), MACD_REVERSAL({self.macd_reversal_pcr_min_normal}-{self.macd_reversal_pcr_max_normal}), DEEP_MACD_REVERSAL({self.deep_macd_reversal_pcr_min_normal}-{self.deep_macd_reversal_pcr_max_normal}), MOMENTUM_ACCELERATION({self.momentum_accel_pcr_min_normal}-{self.momentum_accel_pcr_max_normal})")
    
    def _is_pcr_acceptable(
        self,
        pcr: float,
        dte: int,
        trend_strength: float = 0.5,
        volume_spike: bool = False,
        entry_type: str = '',
    ) -> Tuple[bool, str]:
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
            if entry_type == 'PRE_BREAKOUT':
                min_pcr = self.pre_breakout_pcr_min_normal
                max_pcr = self.pre_breakout_pcr_max_normal
            elif entry_type == 'PULLBACK':
                min_pcr = self.pullback_pcr_min_normal
                max_pcr = self.pullback_pcr_max_normal
            elif entry_type == 'MOMENTUM':
                min_pcr = self.momentum_pcr_min_normal
                max_pcr = self.momentum_pcr_max_normal
            elif entry_type == 'MACD_REVERSAL':
                min_pcr = self.macd_reversal_pcr_min_normal
                max_pcr = self.macd_reversal_pcr_max_normal
            elif entry_type == 'DEEP_MACD_REVERSAL':
                min_pcr = self.deep_macd_reversal_pcr_min_normal
                max_pcr = self.deep_macd_reversal_pcr_max_normal
            elif entry_type == 'MOMENTUM_ACCELERATION':
                min_pcr = self.momentum_accel_pcr_min_normal
                max_pcr = self.momentum_accel_pcr_max_normal
            else:
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
        entry_type = str(signal.get('entry_type', '') or '').upper()
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
        pcr_acceptable, pcr_reason = self._is_pcr_acceptable(
            pcr,
            dte,
            trend_strength,
            volume_spike,
            entry_type,
        )
        
        if not pcr_acceptable:
            return False, pcr_reason
        
        # Check OI buildup if required and available
        if self.require_oi_buildup and oi_buildup:
            if entry_type == 'PRE_BREAKOUT':
                oi_threshold = self.pre_breakout_oi_buildup_min
            elif entry_type == 'PULLBACK':
                oi_threshold = self.pullback_oi_buildup_min
            elif entry_type == 'MOMENTUM':
                oi_threshold = self.momentum_oi_buildup_min
            elif entry_type == 'MACD_REVERSAL':
                oi_threshold = self.macd_reversal_oi_buildup_min
            elif entry_type == 'DEEP_MACD_REVERSAL':
                oi_threshold = self.deep_macd_reversal_oi_buildup_min
            elif entry_type == 'MOMENTUM_ACCELERATION':
                oi_threshold = self.momentum_accel_oi_buildup_min
            else:
                oi_threshold = self.oi_buildup_min
            if oi_buildup < oi_threshold:
                return False, f"OI buildup {oi_buildup:,.0f} < minimum {oi_threshold:,.0f}"
        
        return True, f"Market structure OK | PCR {pcr:.2f} | DTE {dte} days"


# =============================================================================
# VALIDATOR 2: MOMENTUM (RSI + MACD)
# =============================================================================

class MomentumValidator:
    """Validates momentum using RSI and MACD on 15-minute timeframe"""
    
    def __init__(self):
        self.name = "MomentumValidator"
        # RSI thresholds (15-minute timeframe)
        self.rsi_oversold = float(os.getenv("ENTRY_FILTER_RSI_OVERSOLD", "30"))
        self.rsi_overbought = float(os.getenv("ENTRY_FILTER_RSI_OVERBOUGHT", "70"))
        
        # MACD thresholds
        self.macd_require = os.getenv("ENTRY_FILTER_MACD_REQUIRE", "True").lower() == "true"
        self.macd_confirmation = os.getenv("ENTRY_FILTER_MACD_CONFIRMATION", "True").lower() == "true"
        self.low_premium_threshold = float(os.getenv("ENTRY_FILTER_LOW_PREMIUM_THRESHOLD", "5.0"))
        self.low_premium_rsi_min_call = float(os.getenv("ENTRY_FILTER_LOW_PREMIUM_RSI_MIN_CALL", "62"))
        self.default_rsi_min_call = float(os.getenv("ENTRY_FILTER_RSI_MIN_CALL", "54"))
        self.regained_rsi_min_call = float(os.getenv("ENTRY_FILTER_REGAIN_RSI_MIN_CALL", "50"))
        self.pre_breakout_rsi_min_call = float(os.getenv("ENTRY_FILTER_PRE_BREAKOUT_RSI_MIN_CALL", "50"))
        self.pullback_rsi_min_call = float(os.getenv("ENTRY_FILTER_PULLBACK_RSI_MIN_CALL", "50"))
        # MACD Reversal: RSI > 45, but live RSI just needs to confirm > 42 (recovery mode)
        self.macd_reversal_rsi_min_call = float(os.getenv("ENTRY_FILTER_MACD_REVERSAL_RSI_MIN_CALL", "42"))
        # Deep MACD Reversal: RSI recovery from deeper oversold, lower thresholds
        self.deep_macd_reversal_rsi_min_call = float(os.getenv("ENTRY_FILTER_DEEP_MACD_REVERSAL_RSI_MIN_CALL", "35"))
        # Momentum Acceleration: RSI mid-to-high range (already moving, not oversold recovery)
        self.momentum_accel_rsi_min_call = float(os.getenv("ENTRY_FILTER_MOMENTUM_ACCELERATION_RSI_MIN_CALL", "45"))
        self.regained_momentum_score_min = float(os.getenv("ENTRY_FILTER_REGAIN_MOMENTUM_SCORE_MIN", "0.30"))
        self.alert_rsi_expansion_min = float(os.getenv("ENTRY_FILTER_ALERT_RSI_EXPANSION_MIN", "1.0"))
        self.alert_pre_breakout_rsi_floor = float(os.getenv("ENTRY_FILTER_ALERT_PRE_BREAKOUT_RSI_FLOOR", "55"))
        self.alert_pullback_rsi_floor = float(os.getenv("ENTRY_FILTER_ALERT_PULLBACK_RSI_FLOOR", "48"))
        self.alert_momentum_rsi_floor = float(os.getenv("ENTRY_FILTER_ALERT_MOMENTUM_RSI_FLOOR", "48"))
        self.alert_macd_reversal_rsi_floor = float(os.getenv("ENTRY_FILTER_ALERT_MACD_REVERSAL_RSI_FLOOR", "43"))
        self.alert_deep_macd_reversal_rsi_floor = float(os.getenv("ENTRY_FILTER_ALERT_DEEP_MACD_REVERSAL_RSI_FLOOR", "36"))
        self.alert_momentum_accel_rsi_floor = float(os.getenv("ENTRY_FILTER_ALERT_MOMENTUM_ACCELERATION_RSI_FLOOR", "44"))
        
        logger.info(f"{self.name}: Initialized | RSI thresholds: Oversold={self.rsi_oversold}, Overbought={self.rsi_overbought} | MOMENTUM: RSI_min={self.regained_rsi_min_call}, expansion_min={self.alert_rsi_expansion_min} | MACD_REVERSAL: RSI_min_live={self.macd_reversal_rsi_min_call}, rsi_floor={self.alert_macd_reversal_rsi_floor} | DEEP_MACD_REVERSAL: RSI_min_live={self.deep_macd_reversal_rsi_min_call}, rsi_floor={self.alert_deep_macd_reversal_rsi_floor} | MOMENTUM_ACCELERATION: RSI_min_live={self.momentum_accel_rsi_min_call}, rsi_floor={self.alert_momentum_accel_rsi_floor}")
    
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
        
        # Defensive: Convert dict rsi_15m to None (should not happen but fixing bug)
        if isinstance(rsi_15m, dict):
            logger.warning(f"MomentumValidator: rsi_15m is dict (error object), treating as None | {rsi_15m}")
            rsi_15m = None
        
        if rsi_15m is None:
            # RSI missing but continue if not required
            logger.debug(f"MomentumValidator: RSI not available for validation")
            return True, "RSI data not available - skipping validation"
        
        entry_type = str(signal.get('entry_type', '') or '').upper()
        entry_premium = candle_data.get('entry_premium') or 0
        raw_momentum_score = signal.get('momentum_score', candle_data.get('momentum_score', 0))
        raw_alert_rsi_value = signal.get('rsi_value', 0)
        raw_alert_rsi_expansion = signal.get('rsi_expansion', 0)
        try:
            momentum_score = float(raw_momentum_score or 0)
        except (TypeError, ValueError):
            momentum_score = 0.0
        if momentum_score > 1.0:
            momentum_score = momentum_score / 100.0
        try:
            alert_rsi_value = float(raw_alert_rsi_value or 0)
        except (TypeError, ValueError):
            alert_rsi_value = 0.0
        try:
            alert_rsi_expansion = float(raw_alert_rsi_expansion or 0)
        except (TypeError, ValueError):
            alert_rsi_expansion = 0.0

        # For BUY signal (CE/CALL entry) - need strong positive momentum (RSI >= 55)
        if action == 'BUY':
            # GUARD 1: Absolute RSI ceiling — overbought stock has high reversal risk.
            # Configurable via ENTRY_FILTER_RSI_MAX_CALL (default 82).
            # MACD_REVERSAL / DEEP_MACD_REVERSAL are exempt (catching oversold recovery, RSI can be low).
            rsi_max_call = float(os.getenv("ENTRY_FILTER_RSI_MAX_CALL", "82"))
            if entry_type not in {'MACD_REVERSAL', 'DEEP_MACD_REVERSAL'} and rsi_15m > rsi_max_call:
                return False, (
                    f"CE entry: RSI {rsi_15m:.1f} > overbought ceiling {rsi_max_call} "
                    f"(high reversal risk — skip)"
                )

            # GUARD 2: Absolute RSI floor — live RSI below 50 means bearish territory for CE.
            # Alert RSI expansion alone cannot override this for non-reversal entry types.
            if entry_type not in {'MACD_REVERSAL', 'DEEP_MACD_REVERSAL'} and rsi_15m < 50:
                return False, (
                    f"CE entry: RSI {rsi_15m:.1f} below 50 — bearish territory, call entry blocked "
                    f"(entry_type={entry_type})"
                )

            if entry_type == 'MOMENTUM':
                rsi_min_call = self.regained_rsi_min_call
                alert_rsi_floor = self.alert_momentum_rsi_floor
            elif entry_type == 'PRE_BREAKOUT':
                rsi_min_call = self.pre_breakout_rsi_min_call
                alert_rsi_floor = self.alert_pre_breakout_rsi_floor
            elif entry_type == 'PULLBACK':
                rsi_min_call = self.pullback_rsi_min_call
                alert_rsi_floor = self.alert_pullback_rsi_floor
            elif entry_type == 'MACD_REVERSAL':
                rsi_min_call = self.macd_reversal_rsi_min_call
                alert_rsi_floor = self.alert_macd_reversal_rsi_floor
            elif entry_type == 'DEEP_MACD_REVERSAL':
                rsi_min_call = self.deep_macd_reversal_rsi_min_call
                alert_rsi_floor = self.alert_deep_macd_reversal_rsi_floor
            elif entry_type == 'MOMENTUM_ACCELERATION':
                rsi_min_call = self.momentum_accel_rsi_min_call
                alert_rsi_floor = self.alert_momentum_accel_rsi_floor
            else:
                rsi_min_call = self.default_rsi_min_call
                alert_rsi_floor = self.default_rsi_min_call

            if entry_premium > 0 and entry_premium < self.low_premium_threshold:
                rsi_min_call = max(rsi_min_call, self.low_premium_rsi_min_call)

            if entry_type == 'MOMENTUM' and momentum_score < self.regained_momentum_score_min:
                return False, (
                    f"CE momentum regain weak: momentum score {momentum_score:.2f} < "
                    f"minimum {self.regained_momentum_score_min:.2f}"
                )

            if entry_type in {'PULLBACK', 'PRE_BREAKOUT'}:
                alert_rsi_expanding = alert_rsi_value >= alert_rsi_floor and alert_rsi_expansion > 0
            elif entry_type in {'MACD_REVERSAL', 'DEEP_MACD_REVERSAL', 'MOMENTUM_ACCELERATION'}:
                # For MACD reversals and momentum acceleration, we're catching recovery/acceleration mode - just need RSI above alert_rsi_floor
                alert_rsi_expanding = alert_rsi_value >= alert_rsi_floor
            else:
                alert_rsi_expanding = alert_rsi_value >= alert_rsi_floor and alert_rsi_expansion >= self.alert_rsi_expansion_min

            if rsi_15m < rsi_min_call and not alert_rsi_expanding:
                if alert_rsi_value > 0 or alert_rsi_expansion > 0:
                    return False, (
                        f"CE entry: RSI {rsi_15m:.1f} < threshold {rsi_min_call} and alert RSI expansion "
                        f"{alert_rsi_expansion:.2f} is too weak"
                    )
                return False, f"CE entry: RSI {rsi_15m:.1f} < momentum threshold {rsi_min_call} (need strong uptrend)"
        
        # For SELL signal (PE/PUT entry) — bearish momentum required
        elif action == 'SELL':
            # GUARD 1: Absolute RSI oversold floor — extremely oversold stock has high bounce-back risk.
            # Configurable via ENTRY_FILTER_RSI_MIN_PUT (default 18).
            # MACD_BREAKDOWN, DEEP_MACD_BREAKDOWN, TREND_CONTINUATION are exempt:
            # — breakdown/continuation types enter into existing downtrends where RSI can stay deeply
            #   oversold for many sessions (waterfall moves). Blocking them at RSI<18 kills winners.
            rsi_min_put = float(os.getenv("ENTRY_FILTER_RSI_MIN_PUT", "18"))
            oversold_exempt = {'MACD_BREAKDOWN', 'DEEP_MACD_BREAKDOWN', 'TREND_CONTINUATION'}
            if entry_type not in oversold_exempt and rsi_15m < rsi_min_put:
                return False, (
                    f"PE entry: RSI {rsi_15m:.1f} < oversold floor {rsi_min_put} "
                    f"(high reversal-up risk — skip put entry)"
                )

            # GUARD 2: Bullish territory block — RSI above 60 means stock has upward momentum, bad for puts.
            # Breakdown/continuation types catch stocks rolling over from mid-RSI but threshold raised to
            # 60 (from 55) because DEEP_MACD_BREAKDOWN at RSI 56-59 can still be valid put entries.
            breakdown_types = {'MACD_BREAKDOWN', 'DEEP_MACD_BREAKDOWN', 'TREND_CONTINUATION',
                               'PRE_FALL', 'MOMENTUM_BREAKDOWN', 'MOMENTUM', 'PULLBACK', 'PUT_BUY'}
            if entry_type in breakdown_types:
                rsi_bullish_ceil = float(os.getenv("ENTRY_FILTER_RSI_BULLISH_CEIL_PUT", "70"))
                if rsi_15m > rsi_bullish_ceil:
                    return False, (
                        f"PE entry: RSI {rsi_15m:.1f} > {rsi_bullish_ceil} — stock in bullish zone, "
                        f"put entry blocked (entry_type={entry_type})"
                    )
            else:
                # For non-breakdown types (e.g., reversal puts): require overbought RSI
                if rsi_15m < self.rsi_overbought:
                    return False, f"PE entry: RSI {rsi_15m:.1f} < overbought threshold {self.rsi_overbought}"
        
        # MACD confirmation (if available and enabled)
        # The Pine scripts already encode improving MACD histogram in the alert.
        # Do not hard-reject MOMENTUM entries solely because the live MACD level is still below zero.
        if self.macd_confirmation and isinstance(macd_15m, dict) and 'macd' in macd_15m:
            macd_value = macd_15m.get('macd', 0)
            if action == 'BUY' and entry_type != 'MOMENTUM' and macd_value < 0:
                return False, f"CE entry: MACD {macd_value:.4f} should be positive (uptrend)"
            elif action == 'SELL' and entry_type != 'MOMENTUM' and macd_value < 0:
                return False, f"PE entry: MACD {macd_value:.4f} should be positive"
        
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
        self.pullback_momentum_min = float(os.getenv("ENTRY_FILTER_PULLBACK_MOMENTUM_MIN", "0.55"))
        
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
        entry_type = str(signal.get('entry_type', '') or '').upper()
        ma_short = hourly_data.get('ma_short')
        ma_long = hourly_data.get('ma_long')
        slope = hourly_data.get('slope', 0)

        raw_trend_strength = signal.get('trend_strength', hourly_data.get('trend_strength', 0))
        raw_momentum_score = signal.get('momentum_score', hourly_data.get('momentum_score', 0))
        try:
            trend_strength = float(raw_trend_strength or 0)
        except (TypeError, ValueError):
            trend_strength = 0.0
        if not self.ma_require:
            return True, "Trend validation disabled"
        
        # GRACEFUL FALLBACK: If MA data unavailable, skip validation and allow entry (don't reject)
        if ma_short is None or ma_long is None:
            logger.debug(f"{self.name}: MA data not available (ma_short={ma_short}, ma_long={ma_long}), allowing entry")
            return True, "MA data unavailable - skipping trend check (allowing entry)"
        
        # For BUY signal (CALL entry / uptrend) - short MA should be above long MA
        if action == 'BUY':
            if entry_type in {'MOMENTUM', 'PULLBACK', 'PRE_BREAKOUT'}:
                ma_bearish = ma_short < ma_long
                slope_bearish = slope is not None and slope < 0
                if ma_bearish and slope_bearish:
                    return False, (
                        f"CE entry: MA+slope still bearish (MA {ma_short:.2f} < {ma_long:.2f}, "
                        f"slope {slope:.4f})"
                    )
                # Flat slope means momentum exhausted — no directional bias
                slope_min = float(os.getenv("ENTRY_FILTER_MOMENTUM_SLOPE_MIN", "0.05"))
                if slope is not None and abs(slope) < slope_min:
                    return False, (
                        f"CE MOMENTUM: slope {slope:.4f} too flat (< {slope_min}) — "
                        f"no directional momentum, likely exhausted move"
                    )
                return True, "Trend sanity check OK for setup-driven scalp"

            if ma_short < ma_long:
                return False, f"CE entry: Short MA {ma_short:.2f} < Long MA {ma_long:.2f} (should be uptrend for CALL entry)"
            if slope is None or slope < self.slope_threshold:
                slope_value = 0.0 if slope is None else slope
                return False, f"CE entry: slope {slope_value:.4f} < threshold {self.slope_threshold:.4f}"
        
        # For SELL signal (downtrend) - short MA should be below long MA
        elif action == 'SELL':
            if ma_short > ma_long:
                return False, f"PE entry: Short MA {ma_short:.2f} > Long MA {ma_long:.2f} (should be downtrend)"
            if slope is None or slope > -self.slope_threshold:
                slope_value = 0.0 if slope is None else slope
                return False, f"PE entry: slope {slope_value:.4f} > -{self.slope_threshold:.4f}"
        
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
        self.iv_percentile_min = float(os.getenv("ENTRY_FILTER_IV_PERCENTILE_MIN", "25"))  # Skip if IV < 25th
        self.pre_breakout_iv_percentile_min = float(os.getenv("ENTRY_FILTER_PRE_BREAKOUT_IV_PERCENTILE_MIN", "10"))
        self.pullback_iv_percentile_min = float(os.getenv("ENTRY_FILTER_PULLBACK_IV_PERCENTILE_MIN", "18"))
        self.momentum_iv_percentile_min = float(os.getenv("ENTRY_FILTER_MOMENTUM_IV_PERCENTILE_MIN", "25"))
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

        entry_type = str(signal.get('entry_type', '') or '').upper()
        
        iv_percentile = market_data.get('iv_percentile')
        
        # GRACEFUL FALLBACK: If IV data unavailable, skip validation and allow entry (don't reject)
        if iv_percentile is None:
            logger.debug(f"{self.name}: IV percentile data not available, allowing entry")
            return True, "IV percentile data unavailable - skipping IV check (allowing entry)"
        
        if iv_percentile > self.iv_percentile_max:
            return False, f"IV percentile {iv_percentile:.1f} > maximum {self.iv_percentile_max}"
        
        if entry_type == 'PRE_BREAKOUT':
            iv_min = self.pre_breakout_iv_percentile_min
        elif entry_type == 'PULLBACK':
            iv_min = self.pullback_iv_percentile_min
        elif entry_type == 'MOMENTUM':
            iv_min = self.momentum_iv_percentile_min
        else:
            iv_min = self.iv_percentile_min

        if iv_percentile < iv_min:
            return False, f"IV percentile {iv_percentile:.1f} < minimum {iv_min}"
        
        return True, f"IV percentile {iv_percentile:.1f}% valid"


# =============================================================================
# VALIDATOR 5: MARKET HOURS
# =============================================================================

class MarketHoursValidator:
    """Validates trading hours using the alert market trend."""
    
    def __init__(self):
        self.name = "MarketHoursValidator"
        self.market_open = int(os.getenv("ENTRY_FILTER_MARKET_OPEN", "930"))      # 9:30 AM
        self.market_close_good = int(os.getenv("ENTRY_FILTER_MARKET_CLOSE_GOOD", os.getenv("ENTRY_FILTER_MARKET_CLOSE", "1500")))
        self.market_close_neutral = int(os.getenv("ENTRY_FILTER_MARKET_CLOSE_NEUTRAL", "1230"))
        self.market_close_bad = int(os.getenv("ENTRY_FILTER_MARKET_CLOSE_BAD", "1230"))
        self.require_market_hours = os.getenv("ENTRY_FILTER_REQUIRE_MARKET_HOURS", "True").lower() == "true"
        self.timezone = ZoneInfo("Asia/Kolkata")
        
        logger.info(
            f"{self.name}: Initialized | GOOD={self.market_open:04d}-{self.market_close_good:04d} "
            f"| NEUTRAL={self.market_open:04d}-{self.market_close_neutral:04d} "
            f"| BAD={self.market_open:04d}-{self.market_close_bad:04d}"
        )

    def _resolve_market_close(self, market_trend: str) -> int:
        trend = str(market_trend or "NEUTRAL").strip().upper()
        if trend == "GOOD":
            return self.market_close_good
        if trend == "BAD":
            return self.market_close_bad
        return self.market_close_neutral
    
    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any] = None) -> Tuple[bool, str]:
        """
        Validate market hours
        
        Returns:
            (is_valid, reason_message)
        """
        if not self.require_market_hours:
            return True, "Market hours validation disabled"
        
        market_trend = str(signal.get('market_trend', 'NEUTRAL') or 'NEUTRAL').upper()
        market_close = self._resolve_market_close(market_trend)
        now = datetime.now(self.timezone)
        current_time = now.hour * 100 + now.minute
        
        if not (self.market_open <= current_time <= market_close):
            return False, (
                f"Outside market hours for {market_trend} "
                f"({current_time:04d} not in {self.market_open:04d}-{market_close:04d})"
            )
        
        return True, f"Within {market_trend} market hours ({current_time:04d} in {self.market_open:04d}-{market_close:04d})"


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
# VALIDATOR 7: SETUP QUALITY (Strong Day + Controlled Retrade)
# =============================================================================

class SetupQualityValidator:
    """Validates that CALL re-trades happen only on strong intraday leaders."""

    def __init__(self):
        self.name = "SetupQualityValidator"
        self.reentry_allowed = os.getenv("CALL_OPTIONS_REENTRY_ALLOWED", "false").lower() == "true"
        self.reentry_min_alert_step_pct = float(os.getenv("CALL_OPTIONS_REENTRY_MIN_ALERT_STEP_PCT", "1.0"))
        self.require_top_gainer_for_reentry = os.getenv("CALL_OPTIONS_REENTRY_REQUIRE_TOP_GAINER", "true").lower() == "true"
        self.top_gainer_max_rank = int(os.getenv("CALL_OPTIONS_REENTRY_TOP_GAINERS_MAX_RANK", "15"))
        self.min_day_change_call = float(os.getenv("ENTRY_FILTER_MIN_DAY_CHANGE_CALL", "1.20"))
        self.min_retrade_day_change_call = float(os.getenv("ENTRY_FILTER_MIN_RETRADE_DAY_CHANGE_CALL", "2.00"))
        self.pullback_setup_min = int(os.getenv("ENTRY_FILTER_PULLBACK_SETUP_MIN", "2"))
        self.pullback_setup_max = int(os.getenv("ENTRY_FILTER_PULLBACK_SETUP_MAX", "3"))
        self.pre_breakout_setup_min = int(os.getenv("ENTRY_FILTER_PRE_BREAKOUT_SETUP_MIN", "2"))
        self.pre_breakout_setup_max = int(os.getenv("ENTRY_FILTER_PRE_BREAKOUT_SETUP_MAX", "3"))
        logger.info(
            f"{self.name}: Initialized | reentry_allowed={self.reentry_allowed} | "
            f"min_day_change={self.min_day_change_call:.2f}% | retrade_day_change={self.min_retrade_day_change_call:.2f}%"
        )

    @staticmethod
    def _read_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _read_int(value: Any) -> Optional[int]:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str]:
        action = signal.get('action', 'UNKNOWN')
        entry_type = str(signal.get('entry_type', '') or '').upper()

        if action != 'BUY':
            return True, 'Setup-quality gate applies to CALL entries only'

        day_change = self._read_float(signal.get('day_change'))
        setup_sequence = self._read_int(signal.get('setup_sequence'))
        is_reentry_setup = bool(signal.get('is_reentry_setup'))
        is_top_gainer = market_data.get('is_top_gainer')
        top_gainer_rank = self._read_int(market_data.get('top_gainer_rank'))

        if is_reentry_setup and not self.reentry_allowed:
            return False, 'CALL re-entry disabled by CALL_OPTIONS_REENTRY_ALLOWED'

        if day_change is None:
            return True, 'Alert day-change unavailable - skipping setup-quality gate'

        if entry_type == 'MOMENTUM':
            if day_change < self.min_day_change_call:
                return False, (
                    f"CALL momentum: day change {day_change:.2f}% < minimum "
                    f"{self.min_day_change_call:.2f}%"
                )
            return True, f"Strong momentum day confirmed ({day_change:.2f}% vs prev close)"

        if entry_type == 'PULLBACK':
            if setup_sequence is None:
                return True, 'Pullback setup sequence unavailable - skipping setup-quality gate'
            if not (self.pullback_setup_min <= setup_sequence <= self.pullback_setup_max):
                return False, (
                    f"CALL pullback: setup sequence {setup_sequence} outside preferred "
                    f"window {self.pullback_setup_min}-{self.pullback_setup_max}"
                )
            if day_change < self.min_retrade_day_change_call:
                return False, (
                    f"CALL pullback: day change {day_change:.2f}% < retrade minimum "
                    f"{self.min_retrade_day_change_call:.2f}%"
                )
            if self.require_top_gainer_for_reentry and is_top_gainer is not None and not is_top_gainer:
                return False, f"CALL pullback: {signal.get('symbol')} not in top {self.top_gainer_max_rank} gainers"
            if self.require_top_gainer_for_reentry and top_gainer_rank is not None and top_gainer_rank > self.top_gainer_max_rank:
                return False, f"CALL pullback: top gainer rank {top_gainer_rank} > {self.top_gainer_max_rank}"
            return True, f"Pullback retrade OK | seq={setup_sequence} | day_change={day_change:.2f}%"

        if entry_type == 'PRE_BREAKOUT':
            if day_change < self.min_retrade_day_change_call:
                return False, (
                    f"CALL pre-breakout: day change {day_change:.2f}% < retrade minimum "
                    f"{self.min_retrade_day_change_call:.2f}%"
                )
            if is_reentry_setup:
                if setup_sequence is None:
                    return True, 'Pre-breakout re-entry sequence unavailable - skipping setup-quality gate'
                if not (self.pre_breakout_setup_min <= setup_sequence <= self.pre_breakout_setup_max):
                    return False, (
                        f"CALL pre-breakout re-entry: setup sequence {setup_sequence} outside preferred "
                        f"window {self.pre_breakout_setup_min}-{self.pre_breakout_setup_max}"
                    )
            if self.require_top_gainer_for_reentry and is_top_gainer is not None and not is_top_gainer:
                return False, f"CALL pre-breakout: {signal.get('symbol')} not in top {self.top_gainer_max_rank} gainers"
            if self.require_top_gainer_for_reentry and top_gainer_rank is not None and top_gainer_rank > self.top_gainer_max_rank:
                return False, f"CALL pre-breakout: top gainer rank {top_gainer_rank} > {self.top_gainer_max_rank}"
            return True, f"Pre-breakout OK | seq={setup_sequence} | day_change={day_change:.2f}% | reentry={is_reentry_setup}"

        return True, 'Setup-quality gate not applicable for this entry type'


# =============================================================================
    # VALIDATOR 8: SYMBOL REPUTATION (Probation Ladder)
# =============================================================================

class SymbolReputationValidator:
    """
    Hard-gate filter using the probation ladder from symbol_stats.json.

    States:
      ACTIVE  → trade normally
      BLOCKED → hard reject UNLESS the probe window has opened
                  probe window: 1 trade allowed every N days to check for recovery
                  3 consecutive probe wins → back to ACTIVE
                  probe loss → backoff doubles (7 → 14 → 28 → 56 days)

    Data is NEVER erased — probation state accumulates alongside win/loss history.
    The EOD aggregator (eod_learning_aggregator.py) writes probation state nightly.
    """

    # Block threshold: symbols with conf_mult <= this get probation
    BLOCK_CONF_THRESHOLD  = 0.7
    MIN_TRADES_TO_BLOCK   = 5    # need at least 5 trades before blocking
    STATS_CACHE_TTL_SEC   = 300  # reload file at most every 5 minutes

    def __init__(self, stats_path: str = None):
        self.name = "SymbolReputationValidator"
        if stats_path:
            self._stats_file = Path(stats_path)
        else:
            self._stats_file = DATA_DIR / "learning" / "symbol_stats.json"
        self._cache: Dict = {}
        self._cache_ts: Optional[datetime] = None
        logger.info(f"{self.name}: Initialized | stats={self._stats_file}")

    # ------------------------------------------------------------------
    def _load(self) -> Dict:
        """Return cached symbol_stats dict, refreshing every 5 minutes."""
        now = datetime.now()
        if (
            self._cache
            and self._cache_ts
            and (now - self._cache_ts).total_seconds() < self.STATS_CACHE_TTL_SEC
        ):
            return self._cache
        try:
            if self._stats_file.exists():
                with open(self._stats_file) as f:
                    self._cache = json.load(f)
                self._cache_ts = now
                logger.debug(f"{self.name}: Stats reloaded ({len(self._cache)} symbols)")
        except Exception as exc:
            logger.warning(f"{self.name}: LOAD_ERROR | {exc}")
        return self._cache

    def get_symbol_snapshot(self, symbol: str) -> Dict[str, Any]:
        """Return the exact probation/reputation state used for trade-time decisions."""
        stats = self._load()
        sym = stats.get(symbol, {})
        today_str = datetime.now().date().isoformat()
        next_probe = sym.get('probation_next_probe')

        return {
            'symbol': symbol,
            'stats_last_updated': sym.get('last_updated'),
            'total_trades': sym.get('total_trades', 0),
            'confidence_multiplier': sym.get('confidence_multiplier', 1.0),
            'recent_form': sym.get('recent_form', 'neutral'),
            'probation_status': sym.get('probation_status', 'UNKNOWN'),
            'probation_blocked_since': sym.get('probation_blocked_since'),
            'probation_next_probe': next_probe,
            'probation_streak': sym.get('probation_streak', 0),
            'probation_backoff_days': sym.get('probation_backoff_days', 7),
            'probation_probes_attempted': sym.get('probation_probes_attempted', 0),
            'probation_probes_won': sym.get('probation_probes_won', 0),
            'probation_last_probe_date': sym.get('probation_last_probe_date'),
            'probe_window_open': bool(next_probe and today_str >= next_probe),
            'evaluated_at': datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    def validate(self, signal: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str]:
        symbol = signal.get('symbol', '')
        if not symbol:
            return True, "No symbol — skipping reputation check"

        stats = self._load()
        sym = stats.get(symbol)

        if not sym:
            return True, f"{symbol} not in learning data — allowing (new symbol)"

        total_trades = sym.get('total_trades', 0)
        if total_trades < self.MIN_TRADES_TO_BLOCK:
            return True, f"{symbol} only {total_trades} trades — insufficient history, allowing"

        status      = sym.get('probation_status', 'ACTIVE')
        conf        = sym.get('confidence_multiplier', 1.0)
        form        = sym.get('recent_form', 'neutral')
        next_probe  = sym.get('probation_next_probe')
        streak      = sym.get('probation_streak', 0)
        backoff     = sym.get('probation_backoff_days', 7)
        attempts    = sym.get('probation_probes_attempted', 0)
        today_str   = datetime.now().date().isoformat()

        if status == 'ACTIVE':
            return True, (f"{symbol} ACTIVE | conf={conf:.1f} | form={form} | "
                          f"trades={total_trades}")

        if status == 'BLOCKED':
            if next_probe and today_str >= next_probe:
                # Probe window is open — allow exactly 1 trade today
                return True, (f"{symbol} PROBE_ALLOWED | streak={streak}/3 | "
                              f"window_opened={next_probe} | total_probes={attempts}")
            else:
                days_left = (
                    (datetime.fromisoformat(next_probe).date()
                     - datetime.now().date()).days
                    if next_probe else '?'
                )
                return False, (
                    f"{symbol} BLOCKED (probation) | "
                    f"next_probe={next_probe} ({days_left}d away) | "
                    f"streak={streak}/3 | backoff={backoff}d | attempts={attempts}"
                )

        # Unknown status → allow and log
        return True, f"{symbol} status={status} — unknown, allowing"


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

        # HARD GATE: runs before all other validators (not part of N/M voting)
        self.reputation_validator = SymbolReputationValidator()

        # Initialize all validators
        self.validators = {
            'premium': PremiumValidator(),  # NEW: Check minimum premium first
            'market_structure': MarketStructureValidator(),
            'momentum': MomentumValidator(),
            'trend': TrendValidator(),
            'iv': IVValidator(),
            'market_hours': MarketHoursValidator(),
            'expiry': ExpiryValidator(),
            'setup_quality': SetupQualityValidator(),
        }
        
        # Statistics tracking
        self.total_alerts = 0
        self.passed = 0
        self.rejected_by_reason = {}
        
        # Configuration
        self.require_all_filters = os.getenv("ENTRY_FILTER_REQUIRE_ALL", "False").lower() == "true"
        self.min_filters_pass = int(os.getenv("ENTRY_FILTER_MIN_PASS", "6"))
        self.pre_breakout_min_pass = int(os.getenv("ENTRY_FILTER_PRE_BREAKOUT_MIN_PASS", "4"))
        self.pullback_min_pass = int(os.getenv("ENTRY_FILTER_PULLBACK_MIN_PASS", "5"))
        self.momentum_min_pass = int(os.getenv("ENTRY_FILTER_MOMENTUM_MIN_PASS", "6"))
        
        logger.info(f"{self.name}: Initialized | Mode: {'ALL_REQUIRED' if self.require_all_filters else f'MIN_{self.min_filters_pass}_PASS'}")
        logger.info(f"{self.name}: Active validators: {list(self.validators.keys())}")

    @staticmethod
    def _is_counted_pass(is_valid: bool, reason: str) -> bool:
        """Only count validators that passed on actual data-backed checks."""
        if not is_valid:
            return False

        normalized_reason = str(reason or '').lower()
        skip_markers = (
            'skipping validation',
            'skipping trend check',
            'allowing entry',
            'validation disabled',
            'not available',
            'unavailable',
            'insufficient history',
            'no symbol',
        )
        return not any(marker in normalized_reason for marker in skip_markers)

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        try:
            if value is None or value == '':
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _evaluate_neutral_entry_sanity(
        self,
        signal: Dict[str, Any],
        validation_results: Dict[str, Any],
        market_trend: str,
        entry_type: str,
    ) -> Tuple[bool, str]:
        """Apply narrow neutral-market guards to avoid low-quality CE entries."""
        if market_trend != 'NEUTRAL':
            return True, f"Neutral sanity gate skipped for market trend {market_trend}"

        trend_valid = (validation_results.get('trend') or {}).get('valid')
        ema_spread = self._coerce_float(signal.get('ema_spread'))
        vwap_distance = self._coerce_float(signal.get('vwap_distance'))

        if (
            entry_type == 'MOMENTUM_ACCELERATION'
            and trend_valid is False
            and ema_spread is not None
            and vwap_distance is not None
            and ema_spread <= 0
            and vwap_distance <= 0
        ):
            return False, "Neutral momentum acceleration rejected: trend validator failed with weak VWAP/EMA structure"

        return True, "Neutral sanity gate passed"
    
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
        entry_type = str(signal.get('entry_type', '') or '').upper()

        logger.info(f"{self.name}: VALIDATE | #{self.total_alerts} | {symbol} | {action}")

        # ---------------------------------------------------------------
        # HARD GATE 0: Probation / Reputation check
        # Runs before all other validators — a BLOCKED symbol is rejected
        # outright regardless of how many other filters pass.
        # A symbol in its probe window is allowed through (1 trade/window).
        # ---------------------------------------------------------------
        rep_snapshot = self.reputation_validator.get_symbol_snapshot(symbol)
        rep_ok, rep_reason = self.reputation_validator.validate(signal, market_data)
        if not rep_ok:
            reason_key = 'SymbolBlocked_Probation'
            self.rejected_by_reason[reason_key] = self.rejected_by_reason.get(reason_key, 0) + 1
            logger.warning(f"{self.name}: ❌ HARD_GATE_REJECTED | {symbol} | {rep_reason}")
            self._log_rejection(signal, market_data, rep_reason, filter_name='SYMBOL_PROBATION')
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            return False, rep_reason, {
                'symbol_reputation': {
                    'valid': False,
                    'reason': rep_reason,
                    'snapshot': rep_snapshot,
                }
            }

        if 'PROBE_ALLOWED' in rep_reason:
            logger.info(f"{self.name}: 🔬 PROBE_TRADE | {symbol} | {rep_reason}")

        # ---------------------------------------------------------------
        # OPTIONAL GATE: Market Trend Filter (GOOD/NEUTRAL/BAD)
        # Skip BAD market days if configured; trade on GOOD/NEUTRAL always
        # ---------------------------------------------------------------
        market_trend = str(signal.get('market_trend', 'NEUTRAL') or 'NEUTRAL').upper()
        skip_bad_market = os.getenv("ENTRY_FILTER_SKIP_BAD_MARKET", "False").lower() == "true"
        
        if skip_bad_market and market_trend == 'BAD':
            reason_key = 'MarketTrend_BAD'
            self.rejected_by_reason[reason_key] = self.rejected_by_reason.get(reason_key, 0) + 1
            logger.warning(f"{self.name}: ❌ MARKET_TREND_GATE | {symbol} | Market trend BAD, skipping entry")
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            return False, f"Market trend {market_trend} - skipping BAD market", {
                'market_trend': market_trend,
                'skip_bad_market': skip_bad_market,
            }
        
        logger.info(f"{self.name}: Market trend: {market_trend} | Skip BAD: {skip_bad_market}")

        validation_results = {
            'symbol_reputation': {
                'valid': True,
                'reason': rep_reason,
                'snapshot': rep_snapshot,
            },
            'market_trend': {
                'valid': True,
                'reason': f"Market trend {market_trend} is acceptable",
                'market_trend_label': market_trend,
            }
        }
        passed_count = 0
        counted_validators = 0
        skipped_validators = []

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
                
                counted_pass = self._is_counted_pass(is_valid, reason)
                validation_results[validator_name]['counted'] = counted_pass

                if counted_pass:
                    passed_count += 1
                    counted_validators += 1
                    logger.debug(f"{self.name}: ✅ {validator_name.upper()} | {reason}")
                elif is_valid:
                    skipped_validators.append(validator_name)
                    logger.debug(f"{self.name}: ⏭️ {validator_name.upper()} | {reason}")
                else:
                    logger.debug(f"{self.name}: ❌ {validator_name.upper()} | {reason}")
            
            except Exception as e:
                logger.error(f"{self.name}: ERROR in {validator_name} | {str(e)}")
                validation_results[validator_name] = {
                    'valid': False,
                    'reason': f"Error: {str(e)}"
                }

        neutral_gate_valid, neutral_gate_reason = self._evaluate_neutral_entry_sanity(
            signal,
            validation_results,
            market_trend,
            entry_type,
        )
        validation_results['neutral_sanity'] = {
            'valid': neutral_gate_valid,
            'reason': neutral_gate_reason,
            'counted': False,
        }

        if not neutral_gate_valid:
            reason_key = neutral_gate_reason.split(':')[0] if ':' in neutral_gate_reason else neutral_gate_reason
            self.rejected_by_reason[reason_key] = self.rejected_by_reason.get(reason_key, 0) + 1
            validation_results['_summary'] = {
                'counted_passes': passed_count,
                'skipped_validators': skipped_validators,
                'required_passes': self.min_filters_pass,
            }
            logger.warning(f"{self.name}: ❌ NEUTRAL_SANITY_REJECTED | {symbol} {action} | Reason: {neutral_gate_reason}")
            self._log_rejection(signal, market_data, neutral_gate_reason, filter_name='NEUTRAL_SANITY')
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            return False, neutral_gate_reason, validation_results

        market_hours_result = validation_results.get('market_hours')
        if isinstance(market_hours_result, dict) and not market_hours_result.get('valid', True):
            failure_reason = market_hours_result.get('reason', 'Outside allowed market hours')
            reason_key = failure_reason.split(':')[0] if ':' in failure_reason else failure_reason
            self.rejected_by_reason[reason_key] = self.rejected_by_reason.get(reason_key, 0) + 1
            validation_results['_summary'] = {
                'counted_passes': passed_count,
                'skipped_validators': skipped_validators,
                'required_passes': self.min_filters_pass,
            }
            logger.warning(f"{self.name}: ❌ MARKET_HOURS_HARD_REJECTED | {symbol} {action} | Reason: {failure_reason}")
            self._log_rejection(signal, market_data, failure_reason, filter_name='MARKET_HOURS')
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            return False, failure_reason, validation_results

        # Determine overall result
        if entry_type == 'PRE_BREAKOUT':
            required_passes = self.pre_breakout_min_pass
        elif entry_type == 'PULLBACK':
            required_passes = self.pullback_min_pass
        elif entry_type == 'MOMENTUM':
            required_passes = self.momentum_min_pass
        else:
            required_passes = self.min_filters_pass

        if self.require_all_filters:
            is_valid = all(v['valid'] for v in validation_results.values())
            decision = "ALL filters required"
        else:
            is_valid = passed_count >= required_passes
            decision = f"Need {required_passes}/{len(self.validators)} passed"
        
        if is_valid:
            self.passed += 1
            logger.info(f"{self.name}: ✅ PASSED | {symbol} {action} | {passed_count}/{len(self.validators)} validators | {decision}")
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            validation_results['_summary'] = {
                'counted_passes': passed_count,
                'skipped_validators': skipped_validators,
                'required_passes': required_passes,
            }
            return True, f"Entry valid ({passed_count}/{len(self.validators)} counted validators)", validation_results
        else:
            # Find first failing validator for rejection reason
            failure_reason = next((v['reason'] for v in validation_results.values() if isinstance(v, dict) and not v.get('valid')), None)
            if failure_reason is None and skipped_validators:
                failure_reason = (
                    f"Only {passed_count}/{len(self.validators)} counted validators passed; "
                    f"missing or skipped data in: {', '.join(skipped_validators)}"
                )
            if failure_reason is None:
                failure_reason = "Unknown"
            reason_key = failure_reason.split(':')[0] if ':' in failure_reason else failure_reason
            self.rejected_by_reason[reason_key] = self.rejected_by_reason.get(reason_key, 0) + 1

            validation_results['_summary'] = {
                'counted_passes': passed_count,
                'skipped_validators': skipped_validators,
                'required_passes': required_passes,
            }
            
            logger.warning(f"{self.name}: ❌ REJECTED | {symbol} {action} | {passed_count}/{len(self.validators)} validators | Reason: {failure_reason}")
            self._log_rejection(signal, market_data, failure_reason)
            pass_rate = (self.passed / self.total_alerts * 100) if self.total_alerts > 0 else 0
            logger.info(f"{self.name}: STATS | Total: {self.total_alerts} | Passed: {self.passed} | Rate: {pass_rate:.1f}%")
            return False, failure_reason, validation_results
    
    # -------------------------------------------------------------------------
    # Rejection logging helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _classify_filter_name(reason: str) -> str:
        """Map a failure reason string to a short filter label for grouping."""
        r = (reason or '').upper()
        if 'OVERBOUGHT CEILING' in r:    return 'RSI_CEILING'        # CE: RSI > 82
        if 'OVERSOLD FLOOR' in r or 'REVERSAL-UP RISK' in r: return 'RSI_FLOOR_PUT'  # PE: RSI < 18
        if 'BELOW 50' in r or 'BEARISH TERRITORY' in r: return 'RSI_FLOOR'           # CE: RSI < 50
        if 'BULLISH ZONE' in r:          return 'RSI_CEIL_PUT'                        # PE: RSI > 55
        if 'TOO FLAT' in r or 'MOMENTUM EXHAUSTED' in r: return 'SLOPE_MIN'
        if 'PROBATION' in r or 'HARD_GATE' in r:        return 'SYMBOL_PROBATION'
        if 'MARKET HOURS' in r or 'MARKET_HOURS' in r:  return 'MARKET_HOURS'
        if 'NEUTRAL' in r and 'SANITY' in r:            return 'NEUTRAL_SANITY'
        if 'PCR' in r:           return 'PCR'
        if 'OI BUILDUP' in r or 'OI_BUILDUP' in r:     return 'OI_BUILDUP'
        if 'MACD' in r:          return 'MACD'
        if 'RSI' in r:           return 'RSI_FLOOR'
        if 'SLOPE' in r:         return 'SLOPE'
        if 'IV PERCENTILE' in r: return 'IV_PERCENTILE'
        if 'PREMIUM' in r:       return 'PREMIUM'
        if 'DTE' in r or 'EXPIRY' in r: return 'DTE'
        if 'MOMENTUM SCORE' in r: return 'MOMENTUM_SCORE'
        return 'OTHER'

    def _log_rejection(
        self,
        signal: Dict[str, Any],
        market_data: Dict[str, Any],
        failure_reason: str,
        filter_name: Optional[str] = None,
    ) -> None:
        """Write one structured rejection record to events.jsonl AND live_data_rejections.csv."""
        try:
            symbol      = signal.get('symbol', 'UNKNOWN')
            action      = signal.get('action', 'UNKNOWN')
            entry_type  = str(signal.get('entry_type', '') or '').upper()
            alert_px    = signal.get('price', 0)
            confidence  = signal.get('confidence', 0)
            score       = signal.get('score', 0)
            market_trend = signal.get('market_trend', 'UNKNOWN')
            rsi_alert   = signal.get('rsi_value', None)

            rsi_15m     = market_data.get('rsi_15m')
            slope       = market_data.get('slope')
            pcr         = market_data.get('pcr')
            oi_buildup  = market_data.get('oi_buildup')
            dte         = market_data.get('days_to_expiry')
            iv_pct      = market_data.get('iv_percentile')
            entry_prem  = market_data.get('entry_premium')

            fname = filter_name or self._classify_filter_name(failure_reason)
            now   = datetime.now()
            ts    = now.isoformat()
            hhmm  = now.strftime('%H:%M')

            # ── 1. Structured JSON event (goes to events.jsonl via log_event) ──
            log_event(
                'ENTRY_FILTER_REJECTED',
                f"REJECTED | {symbol} | {action} | filter={fname}",
                symbol=symbol,
                action=action,
                entry_type=entry_type,
                alert_px=alert_px,
                confidence=confidence,
                score=score,
                market_trend=market_trend,
                filter_name=fname,
                reason=failure_reason,
                rsi_15m=rsi_15m,
                rsi_alert=rsi_alert,
                slope=slope,
                pcr=pcr,
                oi_buildup=oi_buildup,
                dte=dte,
                iv_percentile=iv_pct,
                entry_premium=entry_prem,
            )

            # ── 2. CSV row (goes to live_data_rejections.csv in DATA_DIR) ──
            csv_path = DATA_DIR / 'live_data_rejections.csv'
            header   = (
                'Timestamp|Time|Symbol|Action|EntryType|AlertPx|Confidence|Score|'
                'MarketTrend|FilterName|RSI_15m|RSI_Alert|Slope|PCR|OI_Buildup|'
                'DTE|IV_Pct|EntryPremium|Reason\n'
            )
            row = (
                f"{ts}|{hhmm}|{symbol}|{action}|{entry_type}|{alert_px}|"
                f"{confidence}|{score}|{market_trend}|{fname}|"
                f"{rsi_15m}|{rsi_alert}|{slope}|{pcr}|{oi_buildup}|"
                f"{dte}|{iv_pct}|{entry_prem}|{failure_reason}\n"
            )
            if not csv_path.exists() or csv_path.stat().st_size == 0:
                csv_path.write_text(header + row)
            else:
                with open(csv_path, 'a') as f:
                    f.write(row)

        except Exception as exc:
            logger.debug(f"_log_rejection: failed to write rejection record: {exc}")

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
