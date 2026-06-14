"""
Strike Validation System

Validates that derived strikes:
1. Exist in instrument.json (broker has them)
2. Match required expiry
3. Have valid tokens for order placement
4. Are legitimate NSE contracts
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .optconfig import BASE_DIR, INSTRUMENT_FILE
from .optlogging import logger


class StrikeValidator:
    """
    Validates option strikes against broker's available contracts in instrument.json
    
    Purpose:
    --------
    Before placing an order for a derived strike, verify:
    - The strike exists in broker's instrument list
    - The expiry date matches
    - We have a valid token for the symbol
    - The contract is actively tradable
    
    Example:
        validator = StrikeValidator()
        is_valid, token, details = validator.validate_strike(
            symbol="BAJAJFINSV25DEC2000CE",
            expiry="2025-12-25",
            strike=2000
        )
        if is_valid:
            print(f"✅ Valid. Token: {token}, Lot: {details['lot_size']}")
        else:
            print(f"❌ Invalid: {details['reason']}")
    """
    
    def __init__(self, instrument_file: Optional[Path] = None):
        """Initialize validator with instrument.json"""
        self.instrument_file = instrument_file or INSTRUMENT_FILE
        self.instruments = []  # All instruments
        self.symbol_map = {}   # symbol -> list of matching instruments (for fast lookup)
        self.underlying_map = {}  # underlying -> list of options
        self._load_instruments()
    
    def _load_instruments(self):
        """Load and index instrument.json for fast lookups"""
        try:
            if not self.instrument_file.exists():
                logger.warning(f"VALIDATOR: Instrument file not found: {self.instrument_file}")
                logger.warning("   Strike validation will be disabled")
                return
            
            with open(self.instrument_file, 'r') as f:
                self.instruments = json.load(f)
            
            # Index by symbol (fast lookup)
            for item in self.instruments:
                symbol = item.get('symbol')
                if symbol:
                    if symbol not in self.symbol_map:
                        self.symbol_map[symbol] = []
                    self.symbol_map[symbol].append(item)
            
            # Index by underlying (for derivation checks)
            for item in self.instruments:
                symbol = item.get('symbol', '')
                exch_seg = item.get('exch_seg')
                
                # Only process NFO (options)
                if exch_seg != 'NFO':
                    continue
                
                # Extract underlying (first part before expiry)
                # E.g., "BAJAJFINSV" from "BAJAJFINSV25DEC2000CE"
                underlying = self._extract_underlying(symbol)
                
                if underlying:
                    if underlying not in self.underlying_map:
                        self.underlying_map[underlying] = []
                    self.underlying_map[underlying].append(item)
            
            logger.info(f"VALIDATOR: Loaded {len(self.instruments)} instruments")
            logger.info(f"   {len(self.symbol_map)} unique symbols")
            logger.info(f"   {len(self.underlying_map)} unique underlyings")
        
        except Exception as e:
            logger.error(f"VALIDATOR: Error loading instruments: {str(e)}")
    
    def validate_strike(
        self,
        symbol: str,
        expiry: str,
        strike: float,
        contract_type: str = "CE"
    ) -> Tuple[bool, Optional[str], Dict]:
        """
        Validate a derived strike against broker's available contracts
        
        Args:
            symbol: Underlying symbol (e.g., "BAJAJFINSV")
            expiry: Expiry date (YYYY-MM-DD)
            strike: Strike price
            contract_type: "CE" or "PE"
        
        Returns:
            (is_valid, token, details_dict)
            
            Example Success:
                (True, "39877_ABC123", {
                    'found': True,
                    'symbol': 'BAJAJFINSV25DEC2000CE',
                    'lot_size': 1,
                    'tick_size': 0.05,
                    'reason': 'Valid strike found'
                })
            
            Example Failure:
                (False, None, {
                    'found': False,
                    'symbol': 'BAJAJFINSV25DEC2000CE',
                    'reason': 'Strike not found in instrument.json'
                })
        """
        if not self.instruments:
            return False, None, {
                'found': False,
                'reason': 'Instrument file not loaded - validation disabled',
                'symbol': symbol
            }
        
        # Build the expected option symbol
        expected_symbol = self._build_option_symbol(symbol, strike, expiry, contract_type)
        
        logger.debug(f"VALIDATOR: Checking {expected_symbol} (strike={strike}, expiry={expiry})")
        
        # Look up in symbol map (exact match)
        if expected_symbol in self.symbol_map:
            matches = self.symbol_map[expected_symbol]
            
            # Find best match (prefer NFO, valid expiry)
            for item in matches:
                if item.get('exch_seg') == 'NFO':
                    token = item.get('token')
                    lot_size = item.get('lotsize', 1)
                    tick_size = item.get('ticksize', 0.05)
                    
                    logger.info(f"VALIDATOR: ✅ Valid strike | {expected_symbol} | token={token} | lot={lot_size}")
                    
                    return True, token, {
                        'found': True,
                        'symbol': expected_symbol,
                        'token': token,
                        'lot_size': lot_size,
                        'tick_size': tick_size,
                        'underlying': symbol,
                        'strike': strike,
                        'expiry': expiry,
                        'contract_type': contract_type,
                        'reason': 'Strike found in instrument.json',
                        'exch_seg': 'NFO'
                    }
        
        # Not found - provide detailed diagnostics
        logger.warning(f"VALIDATOR: ❌ Strike not found | {expected_symbol}")
        
        # Try to diagnose the issue
        diagnostics = self._diagnose_missing_strike(symbol, strike, expiry, contract_type)
        
        return False, None, {
            'found': False,
            'symbol': expected_symbol,
            'underlying': symbol,
            'strike': strike,
            'expiry': expiry,
            'contract_type': contract_type,
            'reason': f"Strike not found in instrument.json",
            **diagnostics
        }
    
    def validate_multiple_strikes(
        self,
        symbol: str,
        expiry: str,
        strikes: List[float],
        contract_type: str = "CE"
    ) -> Dict[float, Tuple[bool, Optional[str], Dict]]:
        """
        Validate multiple strikes for the same symbol/expiry
        
        Returns: Dict[strike -> (is_valid, token, details)]
        """
        results = {}
        
        for strike in strikes:
            results[strike] = self.validate_strike(symbol, expiry, strike, contract_type)
        
        return results
    
    def get_available_strikes(
        self,
        symbol: str,
        expiry: Optional[str] = None,
        contract_type: str = "CE"
    ) -> List[Dict]:
        """
        Get all available strikes for a symbol/expiry from instrument.json
        
        Useful for:
        - Validating that derived strike exists in available set
        - Showing user which strikes are actually available
        - Debugging "strike not found" issues
        
        Args:
            symbol: Underlying (e.g., "BAJAJFINSV")
            expiry: Target expiry (YYYY-MM-DD). If None, returns all.
            contract_type: "CE" or "PE"
        
        Returns:
            List of available contracts:
            [
                {
                    'symbol': 'BAJAJFINSV25DEC1900CE',
                    'strike': 1900,
                    'expiry': '2025-12-25',
                    'token': '39877_ABC123',
                    'lot_size': 1,
                    'tick_size': 0.05
                },
                ...
            ]
        """
        available = []
        
        # Get all options for this underlying
        if symbol not in self.underlying_map:
            logger.warning(f"VALIDATOR: No options found for {symbol}")
            return []
        
        for item in self.underlying_map[symbol]:
            opt_symbol = item.get('symbol', '')
            
            # Filter by contract type if specified
            if not opt_symbol.endswith(contract_type):
                continue
            
            # Extract strike and expiry from symbol
            strike = self._extract_strike(opt_symbol)
            item_expiry = self._extract_expiry_from_symbol(opt_symbol)
            
            # Filter by expiry if specified
            if expiry and item_expiry != expiry:
                continue
            
            available.append({
                'symbol': opt_symbol,
                'strike': strike,
                'expiry': item_expiry,
                'contract_type': opt_symbol[-2:],  # CE or PE
                'token': item.get('token'),
                'lot_size': item.get('lotsize', 1),
                'tick_size': item.get('ticksize', 0.05)
            })
        
        # Sort by strike
        available.sort(key=lambda x: x['strike'] if x['strike'] else 0)
        
        return available
    
    def get_strike_range(
        self,
        symbol: str,
        expiry: str,
        contract_type: str = "CE"
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Get min and max available strikes for symbol/expiry
        
        Useful for validating if a derived strike is within available range
        """
        available = self.get_available_strikes(symbol, expiry, contract_type)
        
        if not available:
            return None, None
        
        strikes = [c['strike'] for c in available if c['strike'] is not None]
        
        if not strikes:
            return None, None
        
        return min(strikes), max(strikes)
    
    def is_strike_within_range(
        self,
        symbol: str,
        expiry: str,
        strike: float,
        contract_type: str = "CE"
    ) -> bool:
        """Check if strike is within available range for symbol/expiry"""
        min_strike, max_strike = self.get_strike_range(symbol, expiry, contract_type)
        
        if min_strike is None or max_strike is None:
            return False
        
        return min_strike <= strike <= max_strike
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _build_option_symbol(
        self,
        underlying: str,
        strike: float,
        expiry: str,
        contract_type: str
    ) -> str:
        """Build NSE option symbol from components"""
        try:
            # Parse expiry
            expiry_obj = datetime.strptime(expiry, "%Y-%m-%d")
            
            # Format depends on underlying
            if underlying in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
                # Index format: SYMBOL + YYMMDD + STRIKE + TYPE
                date_str = expiry_obj.strftime("%y%m%d")
                symbol = f"{underlying}{date_str}{int(strike)}{contract_type}"
            else:
                # Stock format: SYMBOL + YYMON + STRIKE + TYPE
                date_str = expiry_obj.strftime("%y%b").upper()
                symbol = f"{underlying}{date_str}{int(strike)}{contract_type}"
            
            return symbol
        except Exception as e:
            logger.error(f"VALIDATOR: Error building symbol: {str(e)}")
            return ""
    
    def _extract_underlying(self, symbol: str) -> Optional[str]:
        """Extract underlying from option symbol"""
        try:
            # Remove contract type (last 2 chars: CE or PE)
            if symbol.endswith(('CE', 'PE')):
                symbol_without_type = symbol[:-2]
            else:
                symbol_without_type = symbol
            
            # Find where the date starts (first digit)
            for i, char in enumerate(symbol_without_type):
                if char.isdigit():
                    return symbol_without_type[:i]
            
            return None
        except Exception as e:
            logger.debug(f"VALIDATOR: Error extracting underlying from {symbol}: {str(e)}")
            return None
    
    def _extract_strike(self, symbol: str) -> Optional[float]:
        """Extract strike price from option symbol"""
        try:
            # Format: UNDERLYING + DATE + STRIKE + TYPE
            # Remove type (last 2 chars)
            symbol_no_type = symbol[:-2] if len(symbol) > 2 else symbol
            
            # Find where strike starts (after date)
            # Try format 1: SYMBOL + YYMON + STRIKE (e.g., BAJAJFINSV25DEC2000)
            import re
            pattern = r'^([A-Z]+?)(\d{2})([A-Z]{3})(\d+)$'
            match = re.match(pattern, symbol_no_type)
            
            if match:
                return float(match.group(4))
            
            # Try format 2: SYMBOL + YYMMDD + STRIKE (e.g., NIFTY251225100)
            pattern2 = r'^([A-Z]+?)(\d{6})(\d+)$'
            match2 = re.match(pattern2, symbol_no_type)
            
            if match2:
                return float(match2.group(3))
            
            return None
        except Exception as e:
            logger.debug(f"VALIDATOR: Error extracting strike from {symbol}: {str(e)}")
            return None
    
    def _extract_expiry_from_symbol(self, symbol: str) -> Optional[str]:
        """Extract expiry date from option symbol as YYYY-MM-DD"""
        try:
            # Remove type (last 2 chars)
            symbol_no_type = symbol[:-2]
            
            # Try format 1: SYMBOL + YYMON + STRIKE (e.g., BAJAJFINSV25DEC2000)
            import re
            pattern = r'^([A-Z]+?)(\d{2})([A-Z]{3})(\d+)$'
            match = re.match(pattern, symbol_no_type)
            
            if match:
                yy = match.group(2)
                mon = match.group(3)
                
                # Convert to datetime
                year = int("20" + yy)
                month_map = {
                    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
                    'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
                    'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
                }
                
                # Get last Thursday of month
                if mon not in month_map:
                    return None
                
                # This is simplified - should get last Thursday
                # For now, use end of month as approximation
                month = month_map[mon]
                
                # Find last Thursday of this month
                import calendar
                from datetime import date, timedelta
                
                # Get last day of month
                last_day = calendar.monthrange(year, month)[1]
                last_date = date(year, month, last_day)
                
                # Back up to Thursday (3 = Thursday)
                while last_date.weekday() != 3:
                    last_date -= timedelta(days=1)
                
                return last_date.strftime("%Y-%m-%d")
            
            # Try format 2: SYMBOL + YYMMDD + STRIKE (e.g., NIFTY251225100)
            pattern2 = r'^([A-Z]+?)(\d{6})(\d+)$'
            match2 = re.match(pattern2, symbol_no_type)
            
            if match2:
                yymmdd = match2.group(2)
                yy = int(yymmdd[:2])
                mm = int(yymmdd[2:4])
                dd = int(yymmdd[4:6])
                
                year = 2000 + yy
                expiry_date = datetime(year, mm, dd).date()
                
                return expiry_date.strftime("%Y-%m-%d")
            
            return None
        except Exception as e:
            logger.debug(f"VALIDATOR: Error extracting expiry from {symbol}: {str(e)}")
            return None
    
    def _diagnose_missing_strike(
        self,
        symbol: str,
        strike: float,
        expiry: str,
        contract_type: str
    ) -> Dict:
        """
        Provide diagnostic info when a strike is not found
        
        Returns helpful debugging info to user
        """
        diagnostics = {
            'available_strikes': []
        }
        
        # Get available strikes for this symbol
        available = self.get_available_strikes(symbol, expiry, contract_type)
        
        if available:
            diagnostics['available_strikes'] = [
                {
                    'symbol': c['symbol'],
                    'strike': c['strike']
                }
                for c in available[:10]  # Show first 10
            ]
            diagnostics['total_available'] = len(available)
            
            # Find nearest strike
            nearest = min(available, key=lambda x: abs((x['strike'] or 0) - strike))
            diagnostics['nearest_available_strike'] = {
                'symbol': nearest['symbol'],
                'strike': nearest['strike'],
                'distance': abs((nearest['strike'] or 0) - strike)
            }
            
            # Check if strike is within range
            min_strike = min([c['strike'] for c in available if c['strike']])
            max_strike = max([c['strike'] for c in available if c['strike']])
            
            diagnostics['strike_range'] = {
                'min': min_strike,
                'max': max_strike,
                'requested': strike,
                'in_range': min_strike <= strike <= max_strike
            }
        else:
            diagnostics['available_strikes'] = []
            diagnostics['reason'] = f"No {contract_type} options found for {symbol} expiry {expiry}"
        
        return diagnostics
