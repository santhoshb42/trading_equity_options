"""
Options Contract (CE/PE) Symbol Extractor

Dynamically generates CE/PE contract symbols for:
- BANKNIFTY
- NIFTY  
- FINNIFTY

Uses instrument.json as reference for token mapping when available.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .optconfig import BASE_DIR

# =============================================================================
# CE/PE Symbol Format Reference
# =============================================================================

class OptionSymbolFormat:
    """
    Angel One NFO option symbol format:
    BANKNIFTY25DEC1900CE
    - BANKNIFTY: Underlying (or NIFTY, FINNIFTY)
    - 25: Year (2025 → 25)
    - DEC: Month (JAN, FEB, ... DEC)
    - 1900: Strike price
    - CE/PE: Contract type (Call or Put)
    
    Lot size is fixed per underlying:
    - BANKNIFTY: 1 lot = 40 contracts
    - NIFTY: 1 lot = 100 contracts  
    - FINNIFTY: 1 lot = 40 contracts
    """
    
    # Lot sizes by underlying
    LOT_SIZES = {
        'BANKNIFTY': 40,
        'NIFTY': 100,
        'FINNIFTY': 40
    }
    
    # Month mapping
    MONTHS = {
        1: 'JAN', 2: 'FEB', 3: 'MAR', 4: 'APR',
        5: 'MAY', 6: 'JUN', 7: 'JUL', 8: 'AUG',
        9: 'SEP', 10: 'OCT', 11: 'NOV', 12: 'DEC'
    }
    
    @staticmethod
    def generate_symbol(underlying: str, strike: int, expiry: str, contract_type: str) -> str:
        """
        Generate full option symbol from components.
        
        Args:
            underlying: BANKNIFTY, NIFTY, or FINNIFTY
            strike: Strike price (e.g., 1900)
            expiry: Expiry date YYYY-MM-DD
            contract_type: CE or PE
        
        Returns: Full symbol (e.g., BANKNIFTY25DEC1900CE)
        """
        try:
            # Parse expiry
            exp_date = datetime.strptime(expiry, "%Y-%m-%d")
            year = str(exp_date.year)[-2:]  # 2025 → 25
            month = OptionSymbolFormat.MONTHS[exp_date.month]
            
            # Build symbol
            symbol = f"{underlying}{year}{month}{int(strike)}{contract_type}"
            return symbol
        except Exception as e:
            print(f"❌ Error generating symbol: {str(e)}")
            return ""
    
    @staticmethod
    def parse_symbol(symbol: str) -> Optional[Dict[str, str]]:
        """
        Parse full option symbol back to components.
        
        Args:
            symbol: Full symbol (e.g., BANKNIFTY25DEC1900CE)
        
        Returns: Dict with underlying, year, month, strike, contract_type or None
        """
        try:
            # Pattern: UNDERLYING + YY + MONTH + STRIKE + (CE|PE)
            pattern = r'^([A-Z]+?)(\d{2})([A-Z]{3})(\d+)(CE|PE)$'
            match = re.match(pattern, symbol)
            
            if not match:
                return None
            
            underlying, year, month, strike, contract_type = match.groups()
            
            return {
                'underlying': underlying,
                'year': year,
                'month': month,
                'strike': int(strike),
                'contract_type': contract_type,
                'full_symbol': symbol
            }
        except Exception as e:
            print(f"❌ Error parsing symbol {symbol}: {str(e)}")
            return None

# =============================================================================
# Option Chain Generator
# =============================================================================

class OptionChainGenerator:
    """Generates realistic option chains for indexes"""
    
    def __init__(self):
        self.spot_prices = {
            'BANKNIFTY': 47000,
            'NIFTY': 23500,
            'FINNIFTY': 22000
        }
        self.strike_intervals = {
            'BANKNIFTY': 100,
            'NIFTY': 50,
            'FINNIFTY': 100
        }
    
    def generate_chain(self, 
                      underlying: str, 
                      expiry: str,
                      num_strikes: int = 15) -> List[Dict[str, str]]:
        """
        Generate option chain for underlying and expiry.
        
        Args:
            underlying: BANKNIFTY, NIFTY, or FINNIFTY
            expiry: Expiry date YYYY-MM-DD
            num_strikes: Number of strikes to generate (centered on ATM)
        
        Returns: List of option contract symbols (CE and PE)
        """
        spot = self.spot_prices.get(underlying, 20000)
        interval = self.strike_intervals.get(underlying, 100)
        
        # Generate strikes around ATM
        atm_strike = (int(spot) // interval) * interval
        half_strikes = num_strikes // 2
        
        contracts = []
        
        for offset in range(-half_strikes, half_strikes + 1):
            strike = atm_strike + (offset * interval)
            
            # Generate both CE and PE
            for contract_type in ['CE', 'PE']:
                symbol = OptionSymbolFormat.generate_symbol(
                    underlying=underlying,
                    strike=strike,
                    expiry=expiry,
                    contract_type=contract_type
                )
                
                if symbol:
                    contracts.append({
                        'symbol': symbol,
                        'underlying': underlying,
                        'strike': strike,
                        'expiry': expiry,
                        'contract_type': contract_type,
                        'atm': (offset == 0)
                    })
        
        return contracts

# =============================================================================
# Instrument File CE Extractor
# =============================================================================

class InstrumentCEExtractor:
    """Extracts and maps CE/PE symbols from instrument.json"""
    
    def __init__(self, instrument_file: Optional[Path] = None):
        self.instrument_file = instrument_file or (BASE_DIR / "tools" / "instrument.json")
        self.instruments = {}
        self.options_map = {}  # Map of symbol -> option details
        self._load_instruments()
    
    def _load_instruments(self):
        """Load instrument.json if available, otherwise use pure symbol generation"""
        try:
            if self.instrument_file.exists():
                with open(self.instrument_file, 'r') as f:
                    data = json.load(f)
                
                # Build quick lookup by symbol
                for item in data:
                    self.instruments[item['symbol']] = item
                    
                    # Store any NFO instruments separately
                    if item.get('exch_seg') == 'NFO':
                        self.options_map[item['symbol']] = item
                
                print(f"✅ Loaded {len(self.instruments)} instruments from {self.instrument_file}")
                if self.options_map:
                    print(f"   Found {len(self.options_map)} NFO options contracts")
            else:
                print(f"⚠️ Instrument file not found: {self.instrument_file}")
                print("   ℹ️ Using pure algorithmic symbol generation (no token mapping)")
                print("   💡 To enable token mapping, run: python3 tools/download_options_instruments.py")
        
        except Exception as e:
            print(f"❌ Error loading instruments: {str(e)}")
            print("   ℹ️ Falling back to pure algorithmic symbol generation")
    
    def get_token_for_symbol(self, symbol: str) -> Optional[str]:
        """Get token for a symbol from instrument.json"""
        if symbol in self.instruments:
            return self.instruments[symbol].get('token')
        return None
    
    def build_ce_pe_map(self, underlyings: List[str] = None) -> Dict[str, List[Dict]]:
        """
        Build CE/PE symbol map for specified underlyings.
        
        Args:
            underlyings: List of underlyings (BANKNIFTY, NIFTY, FINNIFTY)
        
        Returns: Dict mapping underlying to list of CE/PE contracts
        """
        if underlyings is None:
            underlyings = ['BANKNIFTY', 'NIFTY', 'FINNIFTY']
        
        ce_pe_map = {}
        
        for underlying in underlyings:
            # Generate next weekly expiry
            expiry = self._get_next_weekly_expiry()
            
            # Generate chain
            generator = OptionChainGenerator()
            contracts = generator.generate_chain(underlying, expiry)
            
            # Try to get tokens from instrument file
            for contract in contracts:
                token = self.get_token_for_symbol(contract['symbol'])
                if token:
                    contract['token'] = token
                else:
                    # Generate mock token if not found
                    contract['token'] = self._generate_mock_token(contract['symbol'])
            
            ce_pe_map[underlying] = contracts
        
        return ce_pe_map
    
    def _get_next_weekly_expiry(self) -> str:
        """Get next Wednesday (weekly option expiry)"""
        today = datetime.now().date()
        
        # Find next Wednesday (2 = Wednesday in Python's weekday)
        # But NSE weekly options expire on Wednesday, so we need next Thursday actually
        # Standard: BANKNIFTY/NIFTY weekly expires on Thursday (3 = Thursday)
        days_ahead = 3 - today.weekday()
        if days_ahead <= 0:  # Already passed this week
            days_ahead += 7
        
        next_expiry = today + timedelta(days=days_ahead)
        return next_expiry.strftime("%Y-%m-%d")
    
    def _generate_mock_token(self, symbol: str) -> str:
        """
        Generate deterministic mock token for symbol if not found in instrument.json
        
        Used in PAPER mode when real tokens aren't available.
        Generates consistent tokens for same symbol across sessions.
        """
        # Use a consistent hashing approach
        # Take first 6 digits of hash to create a token-like number
        import hashlib
        hash_obj = hashlib.md5(symbol.encode())
        hash_int = int(hash_obj.hexdigest()[:6], 16)
        token = str(hash_int % 1000000).zfill(6)
        return token
    
    def get_atm_contracts(self, underlying: str, expiry: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Get ATM CE and PE symbols for underlying.
        
        Returns: (ce_symbol, pe_symbol) or (None, None)
        """
        generator = OptionChainGenerator()
        contracts = generator.generate_chain(underlying, expiry, num_strikes=1)
        
        ce_symbol = None
        pe_symbol = None
        
        for contract in contracts:
            if contract['atm']:
                if contract['contract_type'] == 'CE':
                    ce_symbol = contract['symbol']
                else:
                    pe_symbol = contract['symbol']
        
        return (ce_symbol, pe_symbol)
    
    def get_contract_details(self, symbol: str) -> Optional[Dict]:
        """Get details for a specific CE/PE symbol"""
        parsed = OptionSymbolFormat.parse_symbol(symbol)
        
        if not parsed:
            return None
        
        # Add token if available
        token = self.get_token_for_symbol(symbol)
        parsed['token'] = token
        
        return parsed
    
    def export_ce_pe_cache(self, output_file: Optional[Path] = None) -> bool:
        """Export CE/PE map to cache file"""
        try:
            output_file = output_file or (BASE_DIR / "data" / "ce_pe_map.json")
            
            ce_pe_map = self.build_ce_pe_map()
            
            if not output_file.parent.exists():
                output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w') as f:
                json.dump(ce_pe_map, f, indent=2)
            
            print(f"✅ CE/PE map exported to {output_file}")
            return True
        except Exception as e:
            print(f"❌ Error exporting CE/PE map: {str(e)}")
            return False

# =============================================================================
# Global instance
# =============================================================================

_ce_extractor = None

def get_ce_extractor() -> InstrumentCEExtractor:
    """Get or create CE extractor instance"""
    global _ce_extractor
    if _ce_extractor is None:
        _ce_extractor = InstrumentCEExtractor()
    return _ce_extractor
