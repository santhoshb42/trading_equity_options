"""
Options-Specific ML Signal Filter

Advanced machine learning for options signal validation:
- Greeks-aware signal quality (Delta, Gamma, Theta, Vega alignment)
- Volatility regime optimization (high/low IV signal scoring)
- Implied Volatility percentile checking
- Strike moneyness analysis (ATM vs ITM vs OTM)
- Time decay impact (Theta benefit for sellers, cost for buyers)
- Probability of Profit (PoP) scoring
"""

import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from collections import deque
import os

# Import config
try:
    from .optconfig import MLConfig
except ImportError:
    MLConfig = None


class GreeksQualityValidator:
    """Validates Greeks setup quality"""
    
    def __init__(self):
        self.filter_stats = {
            'total_signals': 0,
            'passed': 0,
            'failed_delta': 0,
            'failed_gamma': 0,
            'failed_theta': 0,
            'failed_vega': 0,
            'failed_iv_percentile': 0,
        }
    
    def validate_greeks_alignment(self, greeks: Dict[str, float],
                                  contract_type: str,
                                  action: str,
                                  market_data: Dict[str, Any] = None) -> Tuple[bool, str]:
        """
        Validate Greeks alignment with strategy using configurable ranges
        
        For CE BUY:
        - Positive Delta (configurable range, default 0.2-0.8)
        - Positive Gamma (acceleration upward)
        - Negative Theta (cost of time)
        - Positive Vega (benefit from IV increase)
        
        For CE SELL:
        - Negative Delta (configurable range, default -0.8 to -0.2)
        - Negative Gamma (acceleration downward, good for seller)
        - Positive Theta (profit from time decay)
        - Negative Vega (benefit from IV decrease)
        
        All thresholds loaded from MLConfig for easy tuning
        """
        self.filter_stats['total_signals'] += 1
        
        delta = greeks.get('delta', 0.0)
        gamma = greeks.get('gamma', 0.0)
        theta = greeks.get('theta', 0.0)
        vega = greeks.get('vega', 0.0)
        
        action_upper = action.upper()
        ct_upper = contract_type.upper()
        
        # Get validation ranges from config
        key = f"{ct_upper.lower()}_{action_upper.lower()}"
        ranges = MLConfig.VALIDATION_RANGES.get(key, {}) if MLConfig else {}
        
        delta_min = ranges.get('delta_min', 0.2) if ct_upper == 'CE' and action_upper == 'BUY' else ranges.get('delta_min', -0.8)
        delta_max = ranges.get('delta_max', 0.8) if ct_upper == 'CE' and action_upper == 'BUY' else ranges.get('delta_max', -0.2)
        
        # Validate Delta using config ranges
        if not (delta_min < delta < delta_max):
            self.filter_stats['failed_delta'] += 1
            return False, f"{ct_upper} {action_upper}: Delta {delta:.2f} not in range ({delta_min}-{delta_max})"
        
        # Validate Theta (time decay direction) - DTE-ADAPTIVE
        # Theta is only "bad" if DTE is long. As DTE shrinks, acceptable theta loss increases.
        # BUT: Must be coupled with gamma validation (theta can't be extreme without gamma compensation)
        
        if action_upper == 'BUY':
            # Get DTE from market data if available, default to 7 days if not
            if market_data is None:
                market_data = {}
            dte = market_data.get('days_to_expiry', 7)
            
            # DTE-ADAPTIVE THETA THRESHOLDS (CE/PE BUY)
            if dte >= 7:
                # Swing/positional: theta loss must be minimal
                theta_threshold = -0.08
            elif dte >= 3:
                # Normal directional: moderate theta acceptable
                theta_threshold = -0.15
            elif dte >= 1:
                # Short-term/gamma-sensitive: higher theta acceptable if gamma compensates
                theta_threshold = -0.25
                # BUT: require gamma >= 0.08 if theta is aggressive
                if theta < -0.15 and gamma < 0.08:
                    self.filter_stats['failed_theta'] += 1
                    return False, f"{ct_upper} BUY: DTE {dte} days | Theta {theta:.3f} too aggressive without gamma compensation (gamma={gamma:.4f} < 0.08)"
            else:  # dte == 0 (expiry day)
                # Expiry day: pure gamma scalp only
                # Ignore theta, require high gamma (>=0.15)
                if gamma >= 0.15:
                    return True, f"DTE 0 - Pure gamma scalp (gamma={gamma:.4f})"
                else:
                    self.filter_stats['failed_gamma'] += 1
                    return False, f"{ct_upper} BUY: Expiry day DTE 0 requires gamma >= 0.15 (got {gamma:.4f})"
            
            # HARD SAFETY REJECT: Theta < -1.0 is always bad (extreme decay)
            if theta < -1.0:
                self.filter_stats['failed_theta'] += 1
                return False, f"{ct_upper} BUY: Theta {theta:.3f} too extreme (> -1.0) - pure bleed, no recovery possible"
            
            # Apply DTE-appropriate theta threshold
            if theta < theta_threshold:
                self.filter_stats['failed_theta'] += 1
                return False, f"{ct_upper} BUY: DTE {dte} days | Theta {theta:.3f} < threshold {theta_threshold} (unacceptable decay for this timeframe)"
        
        else:  # SELL
            # Sellers benefit from time decay
            theta_threshold = float(os.getenv("ML_SELL_THETA_MIN", "0.02"))  # Default: need > 0.02 theta benefit
            if theta < theta_threshold:
                self.filter_stats['failed_theta'] += 1
                return False, f"{ct_upper} SELL: Theta {theta:.3f} insufficient (need > {theta_threshold})"
        
        # Validate Gamma (acceleration)
        # Generally prefer non-extreme gamma
        if abs(gamma) > 0.05:
            self.filter_stats['failed_gamma'] += 1
            return False, f"Gamma {gamma:.4f} too high - unstable Greeks"
        
        self.filter_stats['passed'] += 1
        return True, "Greeks validated"
    
    def get_filter_stats(self) -> Dict[str, int]:
        """Get filter statistics"""
        return dict(self.filter_stats)


class VolatilityPercentileValidator:
    """
    Validates signal based on IV percentile
    Different strategies work better at different IV levels
    """
    
    def __init__(self, lookback_days: int = 30):
        self.lookback_days = lookback_days
        self.iv_history = deque(maxlen=lookback_days)
    
    def add_iv_data(self, iv: float) -> None:
        """Record IV value"""
        self.iv_history.append(iv)
    
    def calculate_iv_percentile(self, current_iv: float) -> float:
        """
        Calculate IV percentile (0-100)
        0 = current IV at minimum of period
        100 = current IV at maximum of period
        """
        if len(self.iv_history) < 3:
            return 50.0  # Default to middle if no history
        
        iv_array = list(self.iv_history)
        iv_min = min(iv_array)
        iv_max = max(iv_array)
        
        if iv_max == iv_min:
            return 50.0
        
        percentile = ((current_iv - iv_min) / (iv_max - iv_min)) * 100
        return min(100, max(0, percentile))
    
    def validate_iv_for_action(self, action: str, iv_percentile: float) -> Tuple[bool, str]:
        """
        Validate IV level for the action
        
        BUY (long): Prefer lower IV (buy cheap premiums)
        SELL (short): Prefer higher IV (sell expensive premiums)
        """
        action_upper = action.upper()
        
        if action_upper == 'BUY':
            # Buyers prefer low IV
            if iv_percentile > 75:
                return False, f"IV too high ({iv_percentile:.0f}%) for BUY - premiums expensive"
            elif iv_percentile < 25:
                return True, "Excellent: Low IV ({:.0f}%) optimal for buying cheap premiums".format(iv_percentile)
            else:
                return True, f"IV percentile {iv_percentile:.0f}% acceptable for BUY"
        
        elif action_upper == 'SELL':
            # Sellers prefer high IV
            if iv_percentile < 25:
                return False, f"IV too low ({iv_percentile:.0f}%) for SELL - premiums cheap"
            elif iv_percentile > 75:
                return True, "Excellent: High IV ({:.0f}%) optimal for selling premium".format(iv_percentile)
            else:
                return True, f"IV percentile {iv_percentile:.0f}% acceptable for SELL"
        
        return True, "IV validation passed"


class MoneyMoneyAnalyzer:
    """
    Analyzes strike moneyness (ATM, ITM, OTM)
    Different strategies suit different moneyness levels
    """
    
    @staticmethod
    def calculate_moneyness(underlying_price: float, strike: float,
                           contract_type: str) -> Tuple[str, float]:
        """
        Calculate moneyness type and distance
        
        Returns:
            (moneyness_type, distance_percentage)
            moneyness_type: 'ITM', 'ATM', 'OTM'
        """
        if contract_type.upper() == 'CE':
            # Call: ITM if underlying > strike
            if underlying_price > strike:
                moneyness = 'ITM'
                distance = ((underlying_price - strike) / strike) * 100
            elif underlying_price < strike:
                moneyness = 'OTM'
                distance = ((strike - underlying_price) / strike) * 100
            else:
                moneyness = 'ATM'
                distance = 0.0
        else:  # PE
            # Put: ITM if underlying < strike
            if underlying_price < strike:
                moneyness = 'ITM'
                distance = ((strike - underlying_price) / strike) * 100
            elif underlying_price > strike:
                moneyness = 'OTM'
                distance = ((underlying_price - strike) / strike) * 100
            else:
                moneyness = 'ATM'
                distance = 0.0
        
        return moneyness, distance
    
    @staticmethod
    def validate_moneyness_for_strategy(moneyness: str, action: str) -> Tuple[bool, str]:
        """
        Validate if moneyness is suitable for the strategy
        
        BUY ATM: Good delta, good for directional moves
        BUY OTM: Cheaper, but requires bigger moves
        BUY ITM: Expensive, more defensive
        
        SELL ATM: Good PoP, good for range-bound markets
        SELL OTM: Higher PoP, but less premium
        SELL ITM: High risk, avoid
        """
        action_upper = action.upper()
        
        if action_upper == 'BUY':
            if moneyness == 'ATM':
                return True, "ATM: Optimal for directional moves"
            elif moneyness == 'OTM':
                return True, "OTM: Cheaper but requires bigger moves"
            else:  # ITM
                return True, "ITM: Expensive, more defensive"
        
        elif action_upper == 'SELL':
            if moneyness == 'OTM':
                return True, "OTM: High PoP, good for selling"
            elif moneyness == 'ATM':
                return True, "ATM: Good PoP and premium balance"
            else:  # ITM
                return False, "ITM: Too risky to sell ITM - avoid"
        
        return True, "Moneyness check passed"


class ProbabilityOfProfitCalculator:
    """
    Calculates and validates Probability of Profit (PoP)
    Higher PoP = more likely to be profitable
    """
    
    @staticmethod
    def calculate_pop(entry_price: float, strike: float,
                     contract_type: str, action: str) -> float:
        """
        Simplified PoP calculation
        
        For SELL: PoP = probability price doesn't move beyond strike
        For BUY: PoP = probability price moves significantly
        
        Uses simple model: PoP relates to moneyness distance
        """
        moneyness, distance = MoneyMoneyAnalyzer.calculate_moneyness(
            entry_price, strike, contract_type
        )
        
        action_upper = action.upper()
        
        if action_upper == 'SELL':
            # Sellers profit if stock stays outside strike
            # More OTM = higher PoP
            if moneyness == 'OTM':
                # Higher OTM distance = higher PoP
                pop = min(95, 65 + (distance * 0.3))
            elif moneyness == 'ATM':
                pop = 50  # 50% chance moves beyond ATM
            else:  # ITM
                pop = max(10, 30 - (distance * 0.5))
        
        else:  # BUY
            # Buyers profit if price moves significantly
            # More OTM = lower PoP (needs bigger move)
            if moneyness == 'ATM':
                pop = 50  # 50% chance moves beyond current ATM
            elif moneyness == 'OTM':
                # Lower OTM = higher PoP
                pop = max(15, 40 - (distance * 0.3))
            else:  # ITM
                # ITM has better PoP (in-the-money already)
                pop = min(95, 60 + (distance * 0.5))
        
        return min(100, max(0, pop))
    
    @staticmethod
    def validate_pop(pop: float, action: str, min_pop: float = 40.0) -> Tuple[bool, str]:
        """
        Validate if PoP is acceptable
        
        For SELL: Want PoP > 50% (profitable more than half the time)
        For BUY: Want PoP > 40% (reasonable odds)
        """
        action_upper = action.upper()
        
        if action_upper == 'SELL':
            threshold = 50.0
            if pop < threshold:
                return False, f"PoP {pop:.1f}% < 50% threshold for SELL"
            else:
                return True, f"Good PoP {pop:.1f}% for SELL"
        else:  # BUY
            threshold = min_pop
            if pop < threshold:
                return False, f"PoP {pop:.1f}% < {threshold}% threshold for BUY"
            else:
                return True, f"Good PoP {pop:.1f}% for BUY"


class OptionsSignalQualityFilter:
    """
    Master options signal filter combining all validators
    """
    
    def __init__(self):
        self.greeks_validator = GreeksQualityValidator()
        self.iv_validator = VolatilityPercentileValidator()
        self.pop_calculator = ProbabilityOfProfitCalculator()
        
        # Overall stats
        self.stats = {
            'total_signals': 0,
            'passed': 0,
            'failed_greeks': 0,
            'failed_iv': 0,
            'failed_moneyness': 0,
            'failed_pop': 0,
        }
    
    def validate_signal(self, signal: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Comprehensive options signal validation
        
        Args:
            signal: dict with symbol, action, contract_type, greeks, etc.
        
        Returns:
            (passed, reason, analysis_details)
        """
        self.stats['total_signals'] += 1
        
        contract_type = signal.get('contract_type', 'CE')
        action = signal.get('action', 'BUY').upper()
        greeks = signal.get('greeks', {})
        underlying_price = signal.get('underlying_price', 0.0)
        strike = signal.get('strike', 0.0)
        current_iv = signal.get('iv', 0.0)
        market_data = signal.get('market_data', {})  # Include DTE and other market info
        
        details = {
            'greeks_valid': False,
            'iv_valid': False,
            'moneyness_valid': False,
            'pop_valid': False,
            'greeks_message': '',
            'iv_message': '',
            'moneyness_message': '',
            'pop_message': '',
            'final_pop': 0.0,
        }
        
        # Check 1: Greeks alignment (with market_data for DTE-adaptive theta)
        greeks_valid, greeks_msg = self.greeks_validator.validate_greeks_alignment(
            greeks, contract_type, action, market_data
        )
        details['greeks_valid'] = greeks_valid
        details['greeks_message'] = greeks_msg
        
        if not greeks_valid:
            self.stats['failed_greeks'] += 1
            return False, f"Greeks validation failed: {greeks_msg}", details
        
        # Check 2: IV Percentile
        if current_iv > 0:
            self.iv_validator.add_iv_data(current_iv)
            iv_percentile = self.iv_validator.calculate_iv_percentile(current_iv)
            iv_valid, iv_msg = self.iv_validator.validate_iv_for_action(action, iv_percentile)
            details['iv_valid'] = iv_valid
            details['iv_message'] = iv_msg
            
            if not iv_valid:
                self.stats['failed_iv'] += 1
                return False, f"IV validation failed: {iv_msg}", details
        
        # Check 3: Moneyness
        if underlying_price > 0 and strike > 0:
            moneyness, distance = MoneyMoneyAnalyzer.calculate_moneyness(
                underlying_price, strike, contract_type
            )
            moneyness_valid, moneyness_msg = MoneyMoneyAnalyzer.validate_moneyness_for_strategy(
                moneyness, action
            )
            details['moneyness_valid'] = moneyness_valid
            details['moneyness_message'] = moneyness_msg
            
            if not moneyness_valid:
                self.stats['failed_moneyness'] += 1
                return False, f"Moneyness validation failed: {moneyness_msg}", details
        
        # Check 4: Probability of Profit
        if underlying_price > 0 and strike > 0:
            pop = self.pop_calculator.calculate_pop(
                underlying_price, strike, contract_type, action
            )
            pop_valid, pop_msg = self.pop_calculator.validate_pop(pop, action)
            details['pop_valid'] = pop_valid
            details['pop_message'] = pop_msg
            details['final_pop'] = pop
            
            if not pop_valid:
                self.stats['failed_pop'] += 1
                return False, f"PoP validation failed: {pop_msg}", details
        
        self.stats['passed'] += 1
        return True, "Signal passed all validations", details
    
    def get_filter_stats(self) -> Dict[str, Any]:
        """Get comprehensive filter statistics"""
        stats = dict(self.stats)
        stats['pass_rate'] = (
            self.stats['passed'] / self.stats['total_signals'] * 100
            if self.stats['total_signals'] > 0 else 0
        )
        stats['greeks_filter_stats'] = self.greeks_validator.get_filter_stats()
        return stats


# Global instance
_signal_filter = None

def get_options_signal_filter() -> OptionsSignalQualityFilter:
    """Get or create global signal filter instance"""
    global _signal_filter
    if _signal_filter is None:
        _signal_filter = OptionsSignalQualityFilter()
    return _signal_filter
