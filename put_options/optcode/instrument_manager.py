"""
Instrument Master Manager

Handles daily downloads of instrument.json from AngelOne broker.
Provides strike lookup by symbol for options trading.

Features:
- Automatic daily download at 9:00 AM (can be customized)
- In-memory caching for fast lookups
- Symbol-based strike selection
- Token mapping for order placement
"""

import json
import time
import threading
import subprocess
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from .optlogging import logger, log_event

# =============================================================================
# Instrument Manager
# =============================================================================

class InstrumentManager:
    """Manages F&O instrument master data from broker"""
    
    def __init__(self, instrument_file: Path, download_script: Path, download_hour=9, download_minute=0):
        """
        Initialize instrument manager
        
        Args:
            instrument_file: Path to instrument.json
            download_script: Path to download script (inst.py)
            download_hour: Hour to download (0-23, default 9 = 9:00 AM)
            download_minute: Minute to download (0-59, default 0)
        """
        self.instrument_file = Path(instrument_file)
        self.download_script = Path(download_script)
        self.download_hour = download_hour
        self.download_minute = download_minute
        
        # Cache
        self.instruments = []
        self.symbol_index = {}  # symbol -> list of contracts
        self.token_index = {}   # token -> contract
        
        # State
        self.is_loaded = False
        self.last_updated = None
        self.download_thread = None
        self.running = False
        
        logger.info("INSTRUMENT_MGR: Initialized")
    
    def load_from_file(self) -> bool:
        """Load instruments from local file"""
        try:
            if not self.instrument_file.exists():
                logger.error(f"INSTRUMENT_MGR: FILE_NOT_FOUND | {self.instrument_file}")
                return False
            
            with open(self.instrument_file, 'r') as f:
                self.instruments = json.load(f)
            
            self._build_indices()
            self.is_loaded = True
            self.last_updated = datetime.now()
            
            logger.info(f"INSTRUMENT_MGR: LOADED | {len(self.instruments)} instruments")
            log_event("INSTRUMENTS", f"Loaded {len(self.instruments)} instruments from file")
            
            return True
        
        except Exception as e:
            logger.error(f"INSTRUMENT_MGR: LOAD_ERROR | {str(e)}")
            return False
    
    def _build_indices(self):
        """Build fast lookup indices"""
        self.symbol_index = {}
        self.token_index = {}
        
        for inst in self.instruments:
            # Index by symbol
            symbol = inst.get('symbol')
            if symbol:
                if symbol not in self.symbol_index:
                    self.symbol_index[symbol] = []
                self.symbol_index[symbol].append(inst)
            
            # Index by token
            token = inst.get('token')
            if token:
                self.token_index[token] = inst
        
        logger.debug(f"INSTRUMENT_MGR: INDICES_BUILT | {len(self.symbol_index)} symbols, {len(self.token_index)} tokens")
    
    def get_strike_by_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Get contract by symbol
        
        Args:
            symbol: Full symbol (e.g., "RELIANCE30DEC251600CE")
        
        Returns:
            Contract dict or None
        """
        if symbol not in self.symbol_index:
            return None
        
        contracts = self.symbol_index[symbol]
        return contracts[0] if contracts else None
    
    def get_lot_size(self, symbol: str) -> int:
        """
        Get lot size for a symbol
        
        Args:
            symbol: Full symbol (e.g., "RELIANCE30DEC251600CE")
        
        Returns:
            Lot size as integer, or 1 if not found
        """
        contract = self.get_strike_by_symbol(symbol)
        if contract:
            try:
                lotsize = contract.get('lotsize')
                if lotsize:
                    return int(lotsize)
            except (ValueError, TypeError):
                logger.warning(f"INSTRUMENT_MGR: LOT_SIZE_INVALID | symbol={symbol} | lotsize={lotsize}")
        
        logger.debug(f"INSTRUMENT_MGR: LOT_SIZE_NOT_FOUND | symbol={symbol} | using default=1")
        return 1
    
    def get_strikes_for_underlying(self, underlying: str, exch_seg="NFO", 
                                   instrument_types=("OPTSTK", "FUTSTK")) -> List[Dict]:
        """
        Get all strikes for an underlying stock
        
        Args:
            underlying: Stock name (e.g., "RELIANCE")
            exch_seg: Exchange segment (default "NFO")
            instrument_types: Types to include (default options and futures)
        
        Returns:
            List of contracts
        """
        strikes = []
        
        for inst in self.instruments:
            if (inst.get('name') == underlying and 
                inst.get('exch_seg') == exch_seg and 
                inst.get('instrumenttype') in instrument_types):
                strikes.append(inst)
        
        return strikes
    
    def get_strikes_for_underlying_and_expiry(self, underlying: str, expiry: str,
                                              exch_seg="NFO", 
                                              instrument_types=("OPTSTK", "FUTSTK")) -> List[Dict]:
        """
        Get strikes for a specific underlying and expiry
        
        Args:
            underlying: Stock name (e.g., "RELIANCE")
            expiry: Expiry date (e.g., "30DEC2025")
            exch_seg: Exchange segment
            instrument_types: Types to include
        
        Returns:
            List of contracts
        """
        strikes = []
        
        for inst in self.instruments:
            if (inst.get('name') == underlying and 
                inst.get('expiry') == expiry and
                inst.get('exch_seg') == exch_seg and 
                inst.get('instrumenttype') in instrument_types):
                strikes.append(inst)
        
        return strikes
    
    def find_nearest_strike(self, underlying: str, strike_price: float, 
                           contract_type: str, expiry: str) -> Optional[Dict]:
        """
        Find nearest available strike to a price
        
        Args:
            underlying: Stock name
            strike_price: Target strike price
            contract_type: "CE" or "PE"
            expiry: Expiry date
        
        Returns:
            Nearest strike contract or None
        """
        strikes = self.get_strikes_for_underlying_and_expiry(underlying, expiry)
        
        # Filter by contract type
        ce_pe_strikes = [s for s in strikes if contract_type.upper() in s.get('symbol', '')]
        
        if not ce_pe_strikes:
            return None
        
        # Find immediate next strike ABOVE the alert price (never below)
        # For CE: always pick strike >= alert price (next higher)
        # For PE: always pick strike >= alert price (next higher)
        strikes_above = [s for s in ce_pe_strikes if float(s.get('strike', 0)) >= strike_price]
        
        if strikes_above:
            # Pick the lowest strike that is >= alert price (immediate next)
            nearest = min(strikes_above, key=lambda s: float(s.get('strike', 0)))
        else:
            # Fallback: if no strikes above, pick the highest available
            nearest = max(ce_pe_strikes, key=lambda s: float(s.get('strike', 0)))
        
        return nearest
    
    def download_instruments(self) -> bool:
        """Download fresh instruments from broker"""
        try:
            logger.info("INSTRUMENT_MGR: DOWNLOAD_START")
            
            if not self.download_script.exists():
                logger.error(f"INSTRUMENT_MGR: DOWNLOAD_SCRIPT_NOT_FOUND | {self.download_script}")
                return False
            
            # Run download script
            result = subprocess.run(
                ["python3", str(self.download_script)],
                cwd=self.download_script.parent,
                capture_output=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"INSTRUMENT_MGR: DOWNLOAD_FAILED | {result.stderr.decode()}")
                return False
            
            # Reload from file
            if self.load_from_file():
                logger.info("INSTRUMENT_MGR: DOWNLOAD_SUCCESS")
                log_event("INSTRUMENTS", "Downloaded and loaded fresh instrument master")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"INSTRUMENT_MGR: DOWNLOAD_ERROR | {str(e)}")
            return False
    
    def _should_download_now(self) -> bool:
        """Check if current time matches download schedule"""
        now = datetime.now().time()
        scheduled_time = dt_time(self.download_hour, self.download_minute)
        
        # Download if within 1 minute window
        return abs((now.hour * 60 + now.minute) - (scheduled_time.hour * 60 + scheduled_time.minute)) < 1

    def should_refresh_on_startup(self) -> bool:
        """Return True when startup should force a fresh download of instrument.json."""
        try:
            if not self.instrument_file.exists():
                return True

            if self.instrument_file.stat().st_size == 0:
                return True

            modified_at = datetime.fromtimestamp(self.instrument_file.stat().st_mtime)
            now = datetime.now()
            scheduled_time = dt_time(self.download_hour, self.download_minute)

            if modified_at.date() == now.date():
                return False

            return now.time() >= scheduled_time
        except Exception as e:
            logger.warning(f"INSTRUMENT_MGR: STARTUP_REFRESH_CHECK_FAILED | {str(e)}")
            return False
    
    def start_scheduler(self):
        """Start background download scheduler"""
        self.running = True
        self.download_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.download_thread.start()
        logger.info(f"INSTRUMENT_MGR: SCHEDULER_STARTED | Download at {self.download_hour:02d}:{self.download_minute:02d}")
    
    def stop_scheduler(self):
        """Stop background scheduler"""
        self.running = False
        if self.download_thread:
            self.download_thread.join(timeout=5)
        logger.info("INSTRUMENT_MGR: SCHEDULER_STOPPED")
    
    def _scheduler_loop(self):
        """Background scheduler loop"""
        last_download_date = None
        
        while self.running:
            try:
                now = datetime.now()
                
                # Check if it's a new day and time to download
                if (last_download_date != now.date() and self._should_download_now()):
                    logger.info(f"INSTRUMENT_MGR: SCHEDULED_DOWNLOAD_TRIGGERED")
                    
                    if self.download_instruments():
                        last_download_date = now.date()
                        log_event("INSTRUMENTS", "Scheduled daily download completed")
                    else:
                        logger.warning("INSTRUMENT_MGR: SCHEDULED_DOWNLOAD_FAILED")
                
                # Sleep for 30 seconds before next check
                time.sleep(30)
            
            except Exception as e:
                logger.error(f"INSTRUMENT_MGR: SCHEDULER_ERROR | {str(e)}")
                time.sleep(60)
    
    def get_stats(self) -> Dict:
        """Get manager statistics"""
        # Count F&O stocks
        fo_stocks = set()
        for inst in self.instruments:
            if inst.get('exch_seg') == 'NFO' and inst.get('instrumenttype') in ('OPTSTK', 'FUTSTK'):
                name = inst.get('name')
                if name:
                    fo_stocks.add(name)
        
        return {
            'total_instruments': len(self.instruments),
            'fo_stocks': len(fo_stocks),
            'symbols_indexed': len(self.symbol_index),
            'tokens_indexed': len(self.token_index),
            'is_loaded': self.is_loaded,
            'last_updated': self.last_updated.isoformat() if self.last_updated else None,
            'file_exists': self.instrument_file.exists(),
        }


# =============================================================================
# Global Manager Instance
# =============================================================================

_instrument_manager = None

def get_instrument_manager(instrument_file: Optional[Path] = None,
                          download_script: Optional[Path] = None) -> InstrumentManager:
    """Get or create global instrument manager"""
    global _instrument_manager
    
    if _instrument_manager is None:
        if instrument_file is None:
            instrument_file = Path(__file__).parent.parent / "tools" / "instrument.json"
        
        if download_script is None:
            download_script = Path(__file__).parent.parent / "tools" / "inst.py"
        
        _instrument_manager = InstrumentManager(instrument_file, download_script)
        loaded = _instrument_manager.load_from_file()
        if not loaded or _instrument_manager.should_refresh_on_startup():
            logger.info("INSTRUMENT_MGR: STARTUP_REFRESH_TRIGGERED")
            _instrument_manager.download_instruments()
        _instrument_manager.start_scheduler()
    
    return _instrument_manager
