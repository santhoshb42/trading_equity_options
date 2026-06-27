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
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .optconfig import BASE_DIR, OptionsTradingConfig
from .optlogging import logger

# =============================================================================
# Process-wide instrument cache
# instrument.json is ~36MB. Parsing it per-alert (per InstrumentCEExtractor())
# spiked CPU under the GIL and memory (→ OOM) during bursts. Parse ONCE per
# process, keyed on file mtime; all instances share the same read-only structures.
# Reloaded automatically when instrument.json changes on disk (daily refresh).
# =============================================================================
_INSTRUMENT_CACHE = {'mtime': None, 'all_instruments': None, 'instruments': None, 'options_map': None}
_INSTRUMENT_CACHE_LOCK = threading.Lock()

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
        'FINNIFTY': 40,
        'MIDCPNIFTY': 120,
        'NIFTYNXT50': 25,
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
        
        Supports two formats:
        1. BANKNIFTY25DEC1900CE (broker format with month name)
        2. BANKNIFTY251900CE (compact format with YYMMDD, where MM is 01-12)
        3. POWERINDIA251222500CE (Angel One format with strike directly after date)
        
        Args:
            symbol: Full symbol (e.g., BANKNIFTY25DEC1900CE or POWERINDIA251222500CE)
        
        Returns: Dict with underlying, year, month, strike, contract_type or None
        """
        try:
            # Try format 1: UNDERLYING + YY + MONTH(3 chars) + STRIKE + (CE|PE)
            pattern1 = r'^([A-Z]+?)(\d{2})([A-Z]{3})(\d+)(CE|PE)$'
            match = re.match(pattern1, symbol)
            
            if match:
                underlying, year, month, strike, contract_type = match.groups()
                return {
                    'underlying': underlying,
                    'year': year,
                    'month': month,
                    'strike': int(strike),
                    'contract_type': contract_type,
                    'full_symbol': symbol
                }
            
            # Try format 2: UNDERLYING + YYMMDD + STRIKE + (CE|PE)
            # This catches Angel One format like POWERINDIA251222500CE
            # YYMMDD = 6 digits (251222 = Dec 22, 2025)
            pattern2 = r'^([A-Z]+?)(\d{6})(\d+)(CE|PE)$'
            match = re.match(pattern2, symbol)
            
            if match:
                underlying, yymmdd, strike, contract_type = match.groups()
                
                # Parse YYMMDD
                yy = yymmdd[:2]
                mm = yymmdd[2:4]
                
                # Convert month number to month name
                month_names = {
                    '01': 'JAN', '02': 'FEB', '03': 'MAR', '04': 'APR',
                    '05': 'MAY', '06': 'JUN', '07': 'JUL', '08': 'AUG',
                    '09': 'SEP', '10': 'OCT', '11': 'NOV', '12': 'DEC'
                }
                month = month_names.get(mm, 'UNK')
                
                return {
                    'underlying': underlying,
                    'year': yy,
                    'month': month,
                    'strike': int(strike),
                    'contract_type': contract_type,
                    'full_symbol': symbol
                }
            
            return None
        except Exception as e:
            print(f"❌ Error parsing symbol {symbol}: {str(e)}")
            return None


def extract_underlying_from_symbol(symbol: str) -> str:
    """Return the underlying from a full option contract symbol."""
    if not symbol:
        return ""

    parsed = OptionSymbolFormat.parse_symbol(str(symbol).strip().upper())
    if parsed and parsed.get('underlying'):
        return str(parsed['underlying'])

    match = re.match(r'^([A-Z][A-Z0-9!&_-]*?)(\d{2}[A-Z]{3}|\d{6})\d+(CE|PE)$', str(symbol).strip().upper())
    if match:
        return match.group(1)

    return str(symbol).strip().upper()

# =============================================================================
# Option Chain Generator
# =============================================================================

class OptionChainGenerator:
    """Generates realistic option chains for indexes and equity stocks"""
    
    def __init__(self):
        # Default spot prices - will be overridden by LTP when available
        self.spot_prices = {
            # Index underlyings
            'BANKNIFTY': 47000,
            'NIFTY': 23500,
            'FINNIFTY': 22000,
            'MIDCPNIFTY': 12000,
            'NIFTYNXT50': 56000,
        }
        
        # Dynamic strike intervals based on price range
        # Will be auto-calculated if not specified
        self.strike_intervals = {
            # Index underlyings
            'BANKNIFTY': 100,
            'NIFTY': 50,
            'FINNIFTY': 100,
            'MIDCPNIFTY': 25,
            'NIFTYNXT50': 100,
        }
    
    def _get_strike_interval(self, underlying: str, price: float) -> int:
        """
        Calculate appropriate strike interval based on symbol and price.
        
        Rules:
        - Indexes: Fixed (NIFTY=50, BANKNIFTY=100, FINNIFTY=100)
        - Stocks:
          - Price < 100: interval = 5
          - Price < 500: interval = 10
          - Price < 2000: interval = 20
          - Price >= 2000: interval = 50
        """
        # Check if it's a known index
        if underlying in self.strike_intervals:
            return self.strike_intervals[underlying]
        
        # For stocks, calculate based on price
        if price < 100:
            return 5
        elif price < 500:
            return 10
        elif price < 2000:
            return 20
        else:
            return 50
    
    def generate_chain(self, 
                      underlying: str, 
                      expiry: str,
                      num_strikes: int = 15,
                      center_price: float = None) -> List[Dict[str, str]]:
        """
        Generate option chain for underlying and expiry.
        Dynamically supports ANY F&O symbol by calculating strikes based on LTP.
        
        Args:
            underlying: Any F&O symbol (ASIANPAINT, BANKNIFTY, RELIANCE, etc.)
            expiry: Expiry date YYYY-MM-DD
            num_strikes: Number of strikes to generate (centered on ATM)
            center_price: LTP to center strikes around (REQUIRED for non-index symbols)
        
        Returns: List of option contract symbols (CE and PE)
        """
        # Use provided center_price (LTP from alert) or fall back to configured spot
        if center_price and center_price > 0:
            spot = center_price
        else:
            spot = self.spot_prices.get(underlying, 1000)  # Default fallback
        
        # Calculate strike interval dynamically based on price
        interval = self._get_strike_interval(underlying, spot)
        
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
        self.instruments = {}  # symbol -> item (for single lookup)
        self.all_instruments = []  # Keep ALL items to handle duplicates with different expiry
        self.options_map = {}  # Map of symbol -> option details
        self._last_loaded_mtime = None
        self._load_instruments()
    
    def _load_instruments(self):
        """Load instrument.json once per process (cached by mtime), shared across instances."""
        try:
            if not self.instrument_file.exists():
                print(f"⚠️ Instrument file not found: {self.instrument_file}")
                print("   ℹ️ Using pure algorithmic symbol generation (no token mapping)")
                print("   💡 To enable token mapping, run: python3 tools/download_options_instruments.py")
                return

            current_mtime = self.instrument_file.stat().st_mtime

            with _INSTRUMENT_CACHE_LOCK:
                # Reuse process-wide parse if file unchanged (the common, hot path)
                if (_INSTRUMENT_CACHE['mtime'] == current_mtime
                        and _INSTRUMENT_CACHE['all_instruments'] is not None):
                    self.all_instruments = _INSTRUMENT_CACHE['all_instruments']
                    self.instruments = _INSTRUMENT_CACHE['instruments']
                    self.options_map = _INSTRUMENT_CACHE['options_map']
                    self._last_loaded_mtime = current_mtime
                    return

                # Cold parse (first instance in process, or file changed on disk)
                with open(self.instrument_file, 'r') as f:
                    data = json.load(f)

                instruments = {}
                options_map = {}
                for item in data:
                    instruments[item['symbol']] = item
                    if item.get('exch_seg') == 'NFO':
                        options_map[item['symbol']] = item

                # Publish to the process-wide cache; all instances share these (read-only)
                _INSTRUMENT_CACHE['mtime'] = current_mtime
                _INSTRUMENT_CACHE['all_instruments'] = data
                _INSTRUMENT_CACHE['instruments'] = instruments
                _INSTRUMENT_CACHE['options_map'] = options_map

                self.all_instruments = data
                self.instruments = instruments
                self.options_map = options_map
                self._last_loaded_mtime = current_mtime

                print(f"✅ Loaded {len(data)} instruments from {self.instrument_file} (cached process-wide)")
                print(f"   {len(instruments)} unique symbols, {len(options_map)} NFO options")

        except Exception as e:
            print(f"❌ Error loading instruments: {str(e)}")
            print("   ℹ️ Falling back to pure algorithmic symbol generation")

    def reload_if_stale(self) -> bool:
        """Reload instrument cache when instrument.json changed on disk."""
        try:
            if not self.instrument_file.exists():
                return False

            current_mtime = self.instrument_file.stat().st_mtime
            if self._last_loaded_mtime is None or current_mtime > self._last_loaded_mtime:
                logger.info(f"INSTRUMENT_CACHE: RELOADING | file={self.instrument_file}")
                self._load_instruments()
                return True
        except Exception as e:
            logger.warning(f"INSTRUMENT_CACHE: RELOAD_FAILED | {str(e)}")
        return False
    
    def get_token_for_symbol(self, symbol: str) -> Optional[str]:
        """Get token for a symbol from instrument.json"""
        if symbol in self.instruments:
            return self.instruments[symbol].get('token')
        return None
    
    def build_real_option_chain(self, underlying: str, expiry: str, center_price: Optional[float] = None) -> Optional[List[Dict]]:
        """
        Build option chain from REAL instrument.json data - SUPER SIMPLE grep approach.
        
        Strategy: grep for "SYMBOL+EXACT_EXPIRY" pattern (e.g., "AMBER30DEC25")
        This will return only 3-4 strikes from broker, then pick nearest to LTP.
        
        Args:
            underlying: Stock symbol (POWERINDIA, AMBER, INFY, etc.)
            expiry: Expiry date in YYYY-MM-DD format (e.g., 2025-12-30)
            center_price: Current LTP (unused here, used by get_atm_contracts())
        
        Returns: List of matching contracts (typically 3-4 strikes) OR None if not found
        """
        contracts = []

        # Strip trailing digits (e.g., POWERINDIA3 -> POWERINDIA)
        underlying_clean = underlying.rstrip('0123456789')

        from datetime import datetime
        expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
        expiry_pattern = expiry_date.strftime('%d%b%Y').upper()

        logger.debug(f"Searching instrument master | underlying={underlying_clean} | expiry={expiry_pattern}")

        for instrument in self.all_instruments:
            symbol = str(instrument.get('symbol', ''))
            if not symbol.endswith(('CE', 'PE')):
                continue
            if str(instrument.get('name', '')).upper() != underlying_clean.upper():
                continue
            if str(instrument.get('expiry', '')).strip().upper() != expiry_pattern:
                continue
            instrument_type = str(instrument.get('instrumenttype', ''))
            if not instrument_type.startswith('OPT'):
                continue

            contract_type = symbol[-2:]
            strike = None
            try:
                strike_value = float(instrument.get('strike', 0))
                if strike_value > 0:
                    strike = strike_value / 100.0
                    if strike.is_integer():
                        strike = int(strike)
            except (TypeError, ValueError):
                strike = None

            contract = {
                'symbol': symbol,
                'underlying': underlying_clean,
                'expiry': expiry,
                'contract_type': contract_type,
                'strike': strike,
                'atm': False,
                'token': instrument.get('token'),
                'exch_seg': instrument.get('exch_seg', 'NFO')
            }
            contracts.append(contract)

        contracts = sorted(contracts, key=lambda x: (x['strike'] is None, x['strike'], x['symbol']))

        if contracts:
            logger.info(f"✅ Found {len(contracts)} contracts for {underlying_clean} {expiry_pattern}")
            logger.debug(f"Contracts: {[c['symbol'] for c in contracts[:10]]}")
            return contracts
        else:
            logger.warning(f"⚠️ NO contracts found for {underlying_clean} {expiry_pattern}")
            return None
    
    def build_ce_pe_map(self, underlyings: List[str] = None) -> Dict[str, List[Dict]]:
        """
        Build CE/PE symbol map for specified underlyings.
        
        Args:
            underlyings: List of underlyings (BANKNIFTY, NIFTY, FINNIFTY)
        
        Returns: Dict mapping underlying to list of CE/PE contracts
        """
        if underlyings is None:
            underlyings = list(OptionsTradingConfig.UNDERLYING_INDEXES)
        
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
        """Get next Thursday weekly expiry."""
        today = datetime.now().date()

        # Supported weekly index expiries in this bot are treated as Thursday expiries.
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
    
    def get_pe_contracts_from_instruments(self, underlying: str, expiry: str) -> List[Dict]:
        """
        Get ONLY PE (PUT) contracts from instruments.json for a specific underlying and expiry.
        
        Args:
            underlying: Stock symbol (POWERINDIA, AMBER, INFY, etc.)
            expiry: Expiry date in YYYY-MM-DD format
        
        Returns: List of PE contracts sorted by strike price
        """
        contracts = []
        
        # Strip trailing digits
        underlying_clean = underlying.rstrip('0123456789')
        
        # Convert expiry to broker format: DDMMMYY
        from datetime import datetime
        expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
        expiry_pattern = expiry_date.strftime('%d%b%y').upper()
        
        # Build grep pattern
        search_pattern = f"{underlying_clean}{expiry_pattern}"
        
        logger.debug(f"Searching PE contracts for: {search_pattern}")
        
        # Search all_instruments for matching PE symbols ONLY
        for instrument in self.all_instruments:
            symbol = instrument.get('symbol', '')
            
            # Look for symbols that:
            # 1. Contain the exact date pattern
            # 2. END with 'PE' (not CE)
            # 3. Are NFO segment
            if search_pattern in symbol and symbol.endswith('PE') and instrument.get('exch_seg') == 'NFO':
                contract = {
                    'symbol': symbol,
                    'underlying': underlying_clean,
                    'expiry': expiry,
                    'contract_type': 'PE',
                    'token': instrument.get('token'),
                    'exch_seg': instrument.get('exch_seg', 'NFO'),
                    'full_name': instrument.get('full_name', '')
                }
                contracts.append(contract)
        
        # Sort by symbol to maintain consistent ordering
        contracts = sorted(contracts, key=lambda x: x['symbol'])
        
        if contracts:
            logger.info(f"✅ Found {len(contracts)} PE contracts for {underlying_clean} expiry {expiry}")
            logger.debug(f"PE Contracts: {[c['symbol'] for c in contracts]}")
            return contracts
        else:
            logger.warning(f"⚠️ NO PE contracts found for {search_pattern}")
            return []
    
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

_pe_extractor = None

def get_pe_extractor() -> InstrumentCEExtractor:
    """Get or create CE extractor instance"""
    global _pe_extractor
    if _pe_extractor is None:
        _pe_extractor = InstrumentCEExtractor()
    return _pe_extractor
