"""
Advanced Strike Derivation System

Derives appropriate option strikes based on:
1. Alert price (current market price)
2. Expiry date (weekly/monthly)
3. Nearest ATM (At-The-Money) calculation
4. Strike steps for different symbol categories
5. Moneyness (ITM/ATM/OTM) classification
"""

from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from enum import Enum
import math

# Import strike validator
from .strike_validator import StrikeValidator

# =============================================================================
# Constants & Enums
# =============================================================================

class StrikeStep(Enum):
    """Strike interval for different symbol categories"""
    INDEX_50 = 50        # NIFTY, BANKNIFTY (50 paise steps)
    INDEX_100 = 100      # FINNIFTY (100 rupee steps)
    STOCK_100 = 100      # Most stocks (100 rupee steps)
    LIQUID_100 = 100     # Highly liquid stocks
    ILLIQUID_200 = 200   # Less liquid stocks

class ContractType(Enum):
    """Option contract types"""
    CE = "CE"  # Call option
    PE = "PE"  # Put option

class Moneyness(Enum):
    """Option moneyness classification"""
    ITM = "ITM"      # In-The-Money
    ATM = "ATM"      # At-The-Money
    OTM = "OTM"      # Out-Of-The-Money

# =============================================================================
# Strike Derivation System
# =============================================================================

class StrikeDeriver:
    """
    Advanced strike derivation based on alert price and expiry date
    
    Example:
        deriver = StrikeDeriver()
        strikes = deriver.derive_strikes_for_alert(
            symbol="BAJAJFINSV",
            alert_price=2045.10,
            expiry_date="2025-12-11",
            target_contracts=3
        )
        # Returns: {'ATM': {...}, 'CE_1': {...}, 'PE_1': {...}, ...}
    """
    
    # Symbol-to-strike-step mapping
    STRIKE_STEPS = {
        # Index options
        "NIFTY": StrikeStep.INDEX_50,
        "BANKNIFTY": StrikeStep.INDEX_50,
        "FINNIFTY": StrikeStep.INDEX_100,
        
        # Highly liquid stocks (100 rupee step)
        "RELIANCE": StrikeStep.STOCK_100,
        "INFY": StrikeStep.STOCK_100,
        "TCS": StrikeStep.STOCK_100,
        "HDFCBANK": StrikeStep.STOCK_100,
        "ICICIBANK": StrikeStep.STOCK_100,
        "BAJAJFINSV": StrikeStep.STOCK_100,
        "AXISBANK": StrikeStep.STOCK_100,
        "SBIN": StrikeStep.STOCK_100,
        
        # Default for unlisted symbols
        None: StrikeStep.STOCK_100
    }
    
    @staticmethod
    def get_strike_step(symbol: str) -> StrikeStep:
        """Get appropriate strike step for a symbol"""
        # Check exact match first
        if symbol in StrikeDeriver.STRIKE_STEPS:
            return StrikeDeriver.STRIKE_STEPS[symbol]
        
        # Default to 100 rupee step for stocks
        return StrikeDeriver.STRIKE_STEPS[None]
    
    @staticmethod
    def get_atm_strike(alert_price: float, symbol: str) -> float:
        """
        Calculate nearest strike to alert price (closest strike, not ATM)
        
        This finds the strike step boundary that the alert price falls between,
        then returns the closest one. If alert price is exactly at a strike,
        that strike is returned.
        
        Args:
            alert_price: Current market price of underlying
            symbol: Stock/index symbol
        
        Returns:
            Nearest strike price (closest to alert price)
        """
        strike_step = StrikeDeriver.get_strike_step(symbol)
        step_value = strike_step.value
        
        # Find which two strikes the alert price falls between
        lower_strike = (int(alert_price / step_value)) * step_value
        upper_strike = lower_strike + step_value
        
        # Return the closest one
        if alert_price - lower_strike <= upper_strike - alert_price:
            return float(lower_strike)
        else:
            return float(upper_strike)
    
    @staticmethod
    def derive_strikes_for_alert(
        symbol: str,
        alert_price: float,
        expiry_date: Optional[str] = None,
        target_contracts: int = 3,
        include_otm_only: bool = True
    ) -> Dict[str, Dict[str, Any]]:
        """
        Derive complete strike configuration for an alert - CALLS (CE) ONLY
        
        Selects the strike CLOSEST to alert price as primary, then adds
        strikes both above and below it for Call options only.
        
        Args:
            symbol: Stock/index symbol (e.g., "BAJAJFINSV", "NIFTY")
            alert_price: Current price at which alert triggered
            expiry_date: Target expiry date (YYYY-MM-DD). If None, uses next weekly
            target_contracts: Number of contracts to suggest (default 3)
            include_otm_only: If True, only suggest OTM contracts
        
        Returns:
            Dict with strikes for CE only with metadata
            Example:
            {
                'underlying': 'BAJAJFINSV',
                'nearest_strike': 2050,
                'alert_price': 2045.10,
                'expiry': '2025-12-11',
                'strike_step': 100,
                'days_to_expiry': 7,
                'calls': [
                    {'strike': 2050, 'type': 'NEAREST', 'moneyness': 'ATM', 'distance': 4.9},
                    {'strike': 2150, 'type': 'OTM', 'moneyness': 'OTM', 'distance': 104.9},
                    ...
                ]
            }
        """
        # Get expiry date
        if expiry_date is None:
            expiry_date = StrikeDeriver.get_monthly_expiry()
        
        # Calculate NEAREST strike to alert price
        nearest_strike = StrikeDeriver.get_atm_strike(alert_price, symbol)
        
        # Get strike step
        strike_step = StrikeDeriver.get_strike_step(symbol)
        step_value = strike_step.value
        
        # Calculate days to expiry
        expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        days_to_expiry = (expiry_dt.date() - datetime.now().date()).days
        
        # Derive CALL strikes only (higher strikes are OTM calls)
        calls = []
        
        # Start from nearest strike and add strikes above and below
        num_above = (target_contracts - 1) // 2  # Strikes above nearest
        num_below = (target_contracts - 1) - num_above  # Strikes below nearest
        
        # Add strikes below nearest for calls (lower strikes but still calls, less OTM)
        for i in range(num_below, 0, -1):
            ce_strike = nearest_strike - (i * step_value)
            calls.append({
                'strike': float(ce_strike),
                'type': 'OTM' if i > 0 else 'NEAREST',
                'moneyness': Moneyness.OTM.value,
                'distance_from_alert': float(ce_strike - alert_price),
                'distance_from_nearest': float(-i * step_value),
            })
        
        # Add nearest strike
        calls.append({
            'strike': float(nearest_strike),
            'type': 'NEAREST',
            'moneyness': Moneyness.ATM.value,
            'distance_from_alert': float(nearest_strike - alert_price),
            'distance_from_nearest': 0.0,
        })
        
        # Add strikes above nearest for calls (higher strikes, more OTM)
        for i in range(1, num_above + 1):
            ce_strike = nearest_strike + (i * step_value)
            calls.append({
                'strike': float(ce_strike),
                'type': 'OTM',
                'moneyness': Moneyness.OTM.value,
                'distance_from_alert': float(ce_strike - alert_price),
                'distance_from_nearest': float(i * step_value),
            })
        
        return {
            'underlying': symbol,
            'nearest_strike': float(nearest_strike),
            'alert_price': alert_price,
            'expiry': expiry_date,
            'strike_step': step_value,
            'days_to_expiry': days_to_expiry,
            'calls': calls,
            'timestamp': datetime.now().isoformat()
        }
    
    @staticmethod
    def get_next_weekly_expiry() -> str:
        """Get next weekly expiry date (typically Tuesday for NSE)"""
        today = datetime.now().date()
        # Find next Tuesday
        days_ahead = 1 - today.weekday()  # Tuesday = 1
        if days_ahead <= 0:
            days_ahead += 7
        next_tuesday = today + timedelta(days=days_ahead)
        return next_tuesday.strftime("%Y-%m-%d")
    
    @staticmethod
    @staticmethod
    def get_monthly_expiry() -> str:
        """Get F&O monthly expiry (last Tuesday of current/next month)"""
        today = datetime.now().date()
        
        # Get last day of current month
        if today.month == 12:
            last_day = datetime(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(today.year, today.month + 1, 1) - timedelta(days=1)
        
        last_day = last_day.date()
        
        # Find last Tuesday of current month
        while last_day.weekday() != 1:  # Tuesday = 1
            last_day -= timedelta(days=1)
        
        # If this month's last Tuesday hasn't passed yet, use it
        if last_day >= today:
            return last_day.strftime("%Y-%m-%d")
        
        # Otherwise get last Tuesday of next month
        if today.month == 12:
            last_day = datetime(today.year + 2, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(today.year, today.month + 2, 1) - timedelta(days=1)
        
        last_day = last_day.date()
        
        # Find last Tuesday of next month
        while last_day.weekday() != 1:
            last_day -= timedelta(days=1)
        
        return last_day.strftime("%Y-%m-%d")
    
    @staticmethod
    def classify_moneyness(
        underlying_price: float,
        strike: float,
        contract_type: str
    ) -> Moneyness:
        """
        Classify if option is ITM, ATM, or OTM
        
        Args:
            underlying_price: Current price of underlying
            strike: Strike price
            contract_type: 'CE' or 'PE'
        
        Returns:
            Moneyness classification
        """
        diff = underlying_price - strike
        
        # Consider within 1% as ATM
        atm_threshold = underlying_price * 0.01
        
        if contract_type == "CE":
            if diff > atm_threshold:
                return Moneyness.ITM
            elif diff < -atm_threshold:
                return Moneyness.OTM
            else:
                return Moneyness.ATM
        else:  # PE
            if diff < -atm_threshold:
                return Moneyness.ITM
            elif diff > atm_threshold:
                return Moneyness.OTM
            else:
                return Moneyness.ATM
    
    @staticmethod
    def build_option_symbol(
        underlying: str,
        strike: float,
        expiry_date: str,
        contract_type: str
    ) -> str:
        """
        Build NSE option symbol from components
        
        Format: SYMBOL + DDMMMYY + STRIKE + TYPE
        Example: RELIANCE24FEB261540CE, TCS27JAN263140CE
        
        Args:
            underlying: Stock/index symbol
            strike: Strike price
            expiry_date: Date in YYYY-MM-DD format
            contract_type: 'CE' or 'PE'
        
        Returns:
            NSE-formatted option symbol (e.g., RELIANCE24FEB261540CE)
        """
        # Parse expiry date
        expiry_obj = datetime.strptime(expiry_date, "%Y-%m-%d")
        
        # Format: SYMBOL + DDMMMYY + STRIKE + CE/PE
        # Example: RELIANCE24FEB261540CE (24=day, FEB=month, 26=year)
        date_str = expiry_obj.strftime("%d%b%y").upper()  # e.g., 24FEB26
        strike_str = int(strike)
        return f"{underlying}{date_str}{strike_str}{contract_type}"


# =============================================================================
# Integration with Alert Processing
# =============================================================================

class AlertStrikeMapper:
    """Maps trading alerts to appropriate option strikes with validation"""
    
    def __init__(self, validate_strikes: bool = True):
        """
        Initialize AlertStrikeMapper
        
        Args:
            validate_strikes: If True, validate strikes against instrument.json
        """
        self.deriver = StrikeDeriver()
        self.validator = StrikeValidator() if validate_strikes else None
        self.validate_strikes = validate_strikes
    
    def process_alert(
        self,
        symbol: str,
        price: float,
        signal: str = "BUY",  # BUY, SELL, HOLD
        expiry: Optional[str] = None,
        target_contracts: int = 3
    ) -> Dict[str, Any]:
        """
        Process a TradingView alert and derive CALL option strikes (CE only)
        
        **NOTE: Only Call options (CE) are traded. Put options (PE) are never used.**
        
        Validates that all derived strikes exist in instrument.json before returning.
        
        Args:
            symbol: Stock symbol from alert
            price: Price at which alert triggered
            signal: Trading signal (BUY/SELL/HOLD) - all signals trade CE
            expiry: Target expiry date (YYYY-MM-DD)
            target_contracts: Number of strikes to suggest
        
        Returns:
            Complete alert processing with derived CALL strikes only
            Includes validation status and diagnostics if validation failed
        """
        # All signals trade CALLS (CE) only - never PE
        option_type = "CE"
        
        # Derive strikes (calls only)
        strikes = self.deriver.derive_strikes_for_alert(
            symbol=symbol,
            alert_price=price,
            expiry_date=expiry,
            target_contracts=target_contracts
        )
        
        # Use expiry from strikes (in case it was auto-generated)
        actual_expiry = strikes['expiry']
        
        # Build option symbols for all CE strikes
        option_symbols = []
        validation_results = {}  # strike -> (is_valid, token, details)
        
        for call_strike in strikes['calls']:
            opt_symbol = self.deriver.build_option_symbol(
                symbol,
                call_strike['strike'],
                actual_expiry,
                'CE'
            )
            
            # Validate strike if validator is enabled
            is_valid = True
            token = None
            validation_details = None
            
            if self.validate_strikes and self.validator:
                is_valid, token, validation_details = self.validator.validate_strike(
                    symbol=symbol,
                    expiry=actual_expiry,
                    strike=call_strike['strike'],
                    contract_type='CE'
                )
                validation_results[call_strike['strike']] = (is_valid, token, validation_details)
            
            option_symbols.append({
                'symbol': opt_symbol,
                'strike': call_strike['strike'],
                'type': 'CE',
                'moneyness': call_strike['moneyness'],
                'position_type': call_strike['type'],  # NEAREST or OTM
                'valid': is_valid,  # Strike validation status
                'token': token,  # Broker token (if validated)
                'validation': validation_details if self.validate_strikes else None
            })
        
        # Count valid vs invalid strikes
        valid_count = sum(1 for opt in option_symbols if opt['valid'])
        invalid_count = len(option_symbols) - valid_count
        
        return {
            'alert': {
                'symbol': symbol,
                'price': price,
                'signal': signal,
                'timestamp': datetime.now().isoformat()
            },
            'strikes': strikes,
            'option_symbols': option_symbols,
            'validation': {
                'enabled': self.validate_strikes,
                'valid_count': valid_count,
                'invalid_count': invalid_count,
                'all_valid': invalid_count == 0,
                'results': validation_results
            },
            'recommended': {
                'primary': option_symbols[0] if option_symbols else None,
                'all_options': option_symbols,
                'valid_options': [opt for opt in option_symbols if opt['valid']]
            }
        }

