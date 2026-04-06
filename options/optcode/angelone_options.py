"""
Options Broker Integration (AngelOne SmartAPI)

Wraps AngelOne SmartAPI for options trading:
- Options chain data fetching (BANKNIFTY, NIFTY, FINNIFTY)
- Greeks calculation and retrieval
- Premium tracking and IV data
- Options-specific order placement (CE/PE contracts)
- Position monitoring for derivatives
"""

import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

try:
    from SmartApi import SmartConnect
    try:
        from SmartApi import SmartWebSocketV2
    except ImportError:
        SmartWebSocketV2 = None  # Not all versions have WebSocket
except ImportError:
    try:
        from smartapi import SmartConnect
        try:
            from smartapi import SmartWebSocketV2
        except ImportError:
            SmartWebSocketV2 = None
    except ImportError:
        SmartConnect = None
        SmartWebSocketV2 = None

from .optconfig import AngelOneConfig, OptionsTradingConfig, BASE_DIR, DevConfig
from .ce_extractor import (
    OptionSymbolFormat, 
    OptionChainGenerator,
    InstrumentCEExtractor,
    get_ce_extractor
)
from .optlogging import logger, log_broker_action, log_event
from .options_rate_limiter import get_options_rate_limiter
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# =============================================================================
# Utility: Timeout wrapper for broker API calls
# =============================================================================

def call_with_timeout(func, timeout_seconds: float, *args, **kwargs):
    """
    Execute a function with a timeout.
    
    Args:
        func: Function to call
        timeout_seconds: Max seconds to wait
        *args, **kwargs: Arguments to pass to func
    
    Returns:
        Function result or None if timeout
    
    Raises:
        Returns None if timeout occurs
    """
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, *args, **kwargs)
            result = future.result(timeout=timeout_seconds)
            return result
    except FuturesTimeoutError:
        logger.warning(f"TIMEOUT: {func.__name__} exceeded {timeout_seconds}s")
        return None
    except Exception as e:
        logger.error(f"ERROR in {func.__name__}: {str(e)}")
        return None

# =============================================================================
# Global Rate Limiter for Authentication - PREVENTS CONCURRENT AUTH CALLS
# =============================================================================
# Angel One API has strict rate limits (~8 req/sec per session)
# When multiple threads try to authenticate simultaneously, it triggers rate limiting
# Solution: Track auth attempt time and enforce minimum gap between attempts
_GLOBAL_AUTH_LOCK = threading.Lock()  # Simple lock to serialize auth attempts
_LAST_AUTH_ATTEMPT_TIME = {}  # Dict to track last auth time per broker instance
_AUTH_MIN_INTERVAL_SECONDS = 2  # Minimum gap between auth attempts (2 seconds)

# =============================================================================
# Greeks Estimation (Fallback when broker doesn't provide)
# =============================================================================

def estimate_greeks(underlying: str, strike: float, spot: float, contract_type: str, 
                   time_to_expiry_days: float, iv: float = 0.25, risk_free_rate: float = 0.06) -> Dict[str, float]:
    """
    Estimate Greeks using simplified Black-Scholes model
    
    Args:
        underlying: Stock symbol (for volatility lookup)
        strike: Strike price
        spot: Current spot price
        contract_type: 'CE' or 'PE'
        time_to_expiry_days: Days to expiry
        iv: Implied volatility (default 25%)
        risk_free_rate: Risk-free rate (default 6%)
    
    Returns:
        Dictionary with estimated delta, gamma, theta, vega
    """
    try:
        import math
        
        if time_to_expiry_days <= 0 or spot <= 0 or strike <= 0:
            return {'delta': 0.5 if contract_type == 'CE' else -0.5, 'gamma': 0.01, 'theta': -0.01, 'vega': 0.1}
        
        # Convert to years
        T = time_to_expiry_days / 365.0
        r = risk_free_rate
        sigma = max(iv, 0.1)  # Minimum 10% volatility
        S = spot
        K = strike
        
        # Prevent division by zero
        if S <= 0 or sigma <= 0 or T <= 0:
            return {'delta': 0.5 if contract_type == 'CE' else -0.5, 'gamma': 0.01, 'theta': -0.01, 'vega': 0.1}
        
        # d1 and d2 calculation
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        # Standard normal distributions
        from scipy.stats import norm
        N_d1 = norm.cdf(d1)
        N_d2 = norm.cdf(d2)
        n_d1 = norm.pdf(d1)
        
        if contract_type == 'CE':
            # Call Greeks
            delta = N_d1
            theta = (-S * n_d1 * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * N_d2) / 365
            vega = S * n_d1 * math.sqrt(T) / 100
        else:
            # Put Greeks
            delta = N_d1 - 1
            theta = (-S * n_d1 * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * (1 - N_d2)) / 365
            vega = S * n_d1 * math.sqrt(T) / 100
        
        # Gamma (same for calls and puts)
        gamma = n_d1 / (S * sigma * math.sqrt(T))
        
        return {
            'delta': max(-1, min(1, delta)),  # Clamp to [-1, 1]
            'gamma': max(0, gamma),
            'theta': theta,
            'vega': max(0, vega)
        }
    
    except Exception as e:
        logger.debug(f"GREEKS_ESTIMATION: ERROR | {str(e)} | using defaults")
        # Fallback to reasonable defaults
        if contract_type == 'CE':
            return {'delta': 0.5, 'gamma': 0.05, 'theta': -0.02, 'vega': 0.1}
        else:
            return {'delta': -0.5, 'gamma': 0.05, 'theta': -0.02, 'vega': 0.1}

# =============================================================================
# Options Chain Data Model
# =============================================================================

class OptionContract:
    """Represents a single option contract (CE or PE)"""
    
    def __init__(self, 
                 underlying: str,  # BANKNIFTY, NIFTY, FINNIFTY
                 strike: float,
                 expiry: str,  # YYYY-MM-DD
                 contract_type: str,  # CE or PE
                 symbol: str,  # Full symbol: BANKNIFTY25XXX1900CE
                 token: str = ""):
        self.underlying = underlying
        self.strike = strike
        self.expiry = expiry
        self.contract_type = contract_type
        self.symbol = symbol
        self.token = token
        
        # Market data
        self.ltp = 0.0  # Last traded price (premium)
        self.iv = 0.0  # Implied volatility
        self.delta = 0.0
        self.gamma = 0.0
        self.theta = 0.0
        self.vega = 0.0
        self.open_interest = 0
        self.volume = 0
        self.bid = 0.0
        self.ask = 0.0
        self.last_updated = None
    
    def update_greeks(self, greeks_data: Dict[str, float]):
        """Update Greeks from market data"""
        self.iv = greeks_data.get('iv', self.iv)
        self.delta = greeks_data.get('delta', self.delta)
        self.gamma = greeks_data.get('gamma', self.gamma)
        self.theta = greeks_data.get('theta', self.theta)
        self.vega = greeks_data.get('vega', self.vega)
        self.last_updated = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            'underlying': self.underlying,
            'strike': self.strike,
            'expiry': self.expiry,
            'contract_type': self.contract_type,
            'symbol': self.symbol,
            'token': self.token,
            'ltp': self.ltp,
            'iv': self.iv,
            'greeks': {
                'delta': self.delta,
                'gamma': self.gamma,
                'theta': self.theta,
                'vega': self.vega
            },
            'open_interest': self.open_interest,
            'volume': self.volume,
            'bid': self.bid,
            'ask': self.ask,
            'last_updated': self.last_updated
        }

# =============================================================================
# Options Chain Manager
# =============================================================================

class OptionChain:
    """Manages options chain for a specific underlying and expiry"""
    
    def __init__(self, underlying: str, expiry: str):
        self.underlying = underlying
        self.expiry = expiry
        self.contracts: Dict[str, OptionContract] = {}  # {symbol: contract} - use symbol as key, not strike
        self.atm_strike = None
        self.last_updated = None
    
    def add_contract(self, contract: OptionContract):
        """Add contract to chain"""
        # Use symbol as key since strike might not be pre-parsed
        key = contract.symbol
        self.contracts[key] = contract
    
    def get_contract(self, strike: float, contract_type: str) -> Optional[OptionContract]:
        """Get contract for specific strike and type (legacy method)"""
        # Find contract with matching strike and type (by iterating contracts)
        for contract in self.contracts.values():
            if contract.strike == strike and contract.contract_type == contract_type:
                return contract
        return None
    
    def get_atm_contracts(self, current_spot: float, offset: int = 0) -> Optional[Tuple[OptionContract, OptionContract]]:
        """
        Get ATM or offset contracts (CE, PE) based on current spot price.
        
        Strategy: Find ALL available CE and PE symbols, extract their strikes from symbol strings,
        then pick the pair (CE, PE) with strike NEAREST to current spot price.
        
        Args:
            current_spot: Current LTP/spot price
            offset: Strike offset (0=ATM, 1=next OTM, -1=next ITM)
        """
        # Get all CE and PE symbols separately
        ce_contracts = [c for c in self.contracts.values() if c.contract_type == 'CE']
        pe_contracts = [c for c in self.contracts.values() if c.contract_type == 'PE']
        
        if not ce_contracts or not pe_contracts:
            logger.warning(f"ATM: Insufficient contracts CE={len(ce_contracts)} PE={len(pe_contracts)}")
            return None
        
        # Use contract.strike directly — already set correctly for both NSE and BFO formats.
        # Avoids symbol-regex parsing which fails for BFO format (e.g. SENSEX2640973000CE).
        def get_strike(contract):
            s = getattr(contract, 'strike', None)
            if s and s > 0:
                return float(s)
            return 0.0
        
        # Build lists of (contract, strike) tuples
        ce_with_strikes = [(c, get_strike(c)) for c in ce_contracts]
        pe_with_strikes = [(c, get_strike(c)) for c in pe_contracts]
        
        # Filter out any with zero strike (shouldn't happen but defensive)
        ce_with_strikes = [(c, s) for c, s in ce_with_strikes if s > 0]
        pe_with_strikes = [(c, s) for c, s in pe_with_strikes if s > 0]
        
        if not ce_with_strikes or not pe_with_strikes:
            logger.warning(f"ATM: Could not resolve strikes from contracts")
            return None
        
        # Find available strikes
        available_strikes = sorted(set(s for _, s in ce_with_strikes))
        if not available_strikes:
            return None
        
        # RULE: Always pick the IMMEDIATE NEXT HIGHEST strike
        # For alert_price=407.35 with strikes=[400,405,410,415]
        # Pick 410 (first strike > alert_price) - this is OTM, better risk/reward
        higher_strikes = [s for s in available_strikes if s > current_spot]
        if higher_strikes:
            atm_strike = higher_strikes[0]  # First strike > price (next higher)
        else:
            atm_strike = available_strikes[-1]  # If price is above all strikes, use highest
        
        logger.debug(f"ATM: LTP={current_spot} | next_higher_strike={atm_strike} (immediate next highest) | available={available_strikes[:5]}...")
        
        # Apply offset if requested
        strike_index = available_strikes.index(atm_strike)
        if offset > 0 and strike_index + offset < len(available_strikes):
            selected_strike = available_strikes[strike_index + offset]
        elif offset < 0 and strike_index + offset >= 0:
            selected_strike = available_strikes[strike_index + offset]
        else:
            selected_strike = atm_strike
        
        # Find CE and PE for selected strike
        ce = next((c for c, s in ce_with_strikes if s == selected_strike), None)
        pe = next((c for c, s in pe_with_strikes if s == selected_strike), None)
        
        if ce and pe:
            logger.info(f"ATM: Selected {ce.symbol} and {pe.symbol} for {current_spot}")
            return (ce, pe)
        else:
            logger.warning(f"ATM: Could not find CE or PE for strike {selected_strike}")
            return None
    
    def get_iv_percentile(self) -> Optional[float]:
        """
        Calculate IV percentile (0-100) for the option chain.
        Percentile: (contracts with IV < current_avg) / total_contracts * 100
        """
        if not self.contracts:
            return None
        
        try:
            # Collect all IV values from all contracts
            iv_values = []
            for contract in self.contracts.values():
                if hasattr(contract, 'iv') and contract.iv is not None:
                    iv_values.append(contract.iv)
            
            if not iv_values or len(iv_values) < 2:
                return None
            
            # Calculate average IV
            avg_iv = sum(iv_values) / len(iv_values)
            
            # Count contracts with IV below average
            below_avg = sum(1 for iv in iv_values if iv < avg_iv)
            
            # Calculate percentile
            iv_percentile = (below_avg / len(iv_values)) * 100
            
            logger.debug(f"IV_PERCENTILE: {iv_percentile:.1f}% | avg_iv={avg_iv:.2f}% | contracts={len(iv_values)}")
            return iv_percentile
        except Exception as e:
            logger.error(f"IV_PERCENTILE: ERROR | {str(e)}")
            return None
    
    def get_days_to_expiry(self) -> Optional[int]:
        """
        Calculate days to expiry from the expiry string.
        Format: 'YYYY-MM-DD' or 'DDMMMYY'
        """
        if not self.expiry:
            return None
        
        try:
            from datetime import datetime
            
            # Try parsing YYYY-MM-DD format first
            if '-' in self.expiry:
                expiry_date = datetime.strptime(self.expiry, '%Y-%m-%d').date()
            # Try parsing DDMMMYY format (e.g., '30DEC25')
            elif len(self.expiry) == 7:
                # Handle abbreviated month names
                expiry_date = datetime.strptime(self.expiry, '%d%b%y').date()
            else:
                # Try default parsing
                expiry_date = datetime.strptime(self.expiry, '%Y-%m-%d').date()
            
            today = datetime.now().date()
            dte = (expiry_date - today).days
            
            logger.debug(f"DTE: {dte} days | expiry={self.expiry} | date={expiry_date}")
            return max(0, dte)  # Return 0 if expired
        except Exception as e:
            logger.error(f"DTE: ERROR | {str(e)} | expiry={self.expiry}")
            return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert chain to dictionary for storage"""
        return {
            'underlying': self.underlying,
            'expiry': self.expiry,
            'contracts': {
                symbol: contract.to_dict()
                for symbol, contract in self.contracts.items()
            },
            'atm_strike': self.atm_strike,
            'last_updated': self.last_updated
        }

# =============================================================================
# LTP Cache with Pre-fetching
# =============================================================================

class LTPCache:
    """
    High-speed LTP cache that pre-fetches data for active symbols.
    Maintained by background thread to always have hot data ready.
    """
    
    def __init__(self):
        self.cache = {}  # {symbol: {'ltp': float, 'timestamp': datetime}}
        self.lock = __import__('threading').Lock()
        self.last_fetch_time = {}
    
    def get(self, symbol: str, max_age_seconds: float = 2.0) -> Optional[float]:
        """Get LTP from cache if fresh (< max_age_seconds old)"""
        with self.lock:
            if symbol not in self.cache:
                return None
            
            cached = self.cache[symbol]
            age = (datetime.now() - cached['timestamp']).total_seconds()
            
            # Return cached value only if fresh
            if age < max_age_seconds:
                return cached['ltp']
            
            return None
    
    def set(self, symbol: str, ltp: float):
        """Update cache with new LTP"""
        with self.lock:
            self.cache[symbol] = {
                'ltp': ltp,
                'timestamp': datetime.now()
            }
            self.last_fetch_time[symbol] = datetime.now()
    
    def get_all_symbols(self) -> List[str]:
        """Get list of all cached symbols"""
        with self.lock:
            return list(self.cache.keys())
    
    def clear_stale(self, max_age_seconds: float = 60):
        """Remove symbols not updated in last max_age_seconds"""
        with self.lock:
            now = datetime.now()
            stale = []
            for symbol, data in self.cache.items():
                age = (now - data['timestamp']).total_seconds()
                if age > max_age_seconds:
                    stale.append(symbol)
            
            for symbol in stale:
                del self.cache[symbol]
                if symbol in self.last_fetch_time:
                    del self.last_fetch_time[symbol]

# =============================================================================
# Options Broker Wrapper
# =============================================================================

class AngelOneOptionsBroker:
    """
    AngelOne SmartAPI wrapper for options trading.
    Handles options-specific operations: chain fetching, Greeks, premium tracking.
    Independent from equity broker.
    """
    
    def __init__(self):
        self.api_key = AngelOneConfig.API_KEY
        self.client_code = AngelOneConfig.CLIENT_CODE
        self.password = AngelOneConfig.PASSWORD
        self.totp_key = AngelOneConfig.TOTP_KEY
        
        self.session_token = None
        self.refresh_token = None
        self.smart_api = None
        self.authenticated = False
        self.last_auth_time = None
        
        # Options chains cache
        self.option_chains: Dict[Tuple[str, str], OptionChain] = {}  # {(underlying, expiry): chain}
        self.chain_cache_file = BASE_DIR / "data" / "option_chain_cache.json"
        self.chain_last_updated = {}
        
        # LTP cache for burst alert handling
        self.ltp_cache = LTPCache()
        
        # CE/PE extractor for symbol generation
        self.ce_extractor = get_ce_extractor()
        
        # Mock spot prices for paper trading
        self.spot_prices = {
            # Index underlyings
            'BANKNIFTY': 47000,
            'NIFTY': 23500,
            'FINNIFTY': 22000,
            # Stock underlyings with realistic spot prices
            'SAMMAANCAP': 150,      # ~6x strike (145)
            'PAYTM': 1300,          # ~1.02x strike (1280)
            'ANGELONE': 2550,       # At strike level
            'MUTHOOTFIN': 3800,     # At strike level
            'LTF': 310,             # At strike level
            'HEROMOTOCO': 6200,     # ~1.03x strike (6000)
            'KFINTECH': 1075,       # ~1.01x strike (1060)
            'SHRIRAMFIN': 875,      # ~1.03x strike (850)
            'HINDZINC': 540,        # ~1.03x strike (525)
            'ABB': 5300,            # ~0.95x strike (5250)
        }
        
        # Positions tracking
        self.option_positions: Dict[str, Dict[str, Any]] = {}  # {symbol: position_data}
        
        # Authentication retry tracking (for rate limit handling)
        self.auth_retry_count = 0
        self.auth_last_retry_time = None
        self.auth_rate_limit_detected = False
        
        # Auto-authenticate on init with retry logic to handle rate limits
        if self.api_key and self.client_code and self.password:
            self.authenticate()
        else:
            logger.warning("BROKER_INIT: Credentials missing - broker will run in demo mode")
    
    def _check_session_valid(self) -> bool:
        """Check if current session is still valid (max 24 hours)"""
        if not self.authenticated or not self.last_auth_time:
            return False
        
        session_age = (datetime.now() - self.last_auth_time).total_seconds() / 3600
        if session_age > 23:  # Refresh before 24 hour limit
            logger.warning(f"BROKER_SESSION: EXPIRED | age={session_age:.1f}h")
            return False
        
        return True
    
    def _handle_invalid_token_error(self) -> bool:
        """
        Handle Invalid Token (AG8001) errors by forcing re-authentication.
        Returns True if re-authentication was successful.
        """
        logger.warning("BROKER_SESSION: Invalid Token detected - forcing re-authentication")
        self.authenticated = False  # Mark session as invalid
        self.auth_retry_count = 0   # Reset retry counter for new authentication
        
        # CRITICAL: Reset the last auth time to force bypass the 2-second check
        # This ensures we immediately attempt auth instead of skipping due to rate limit
        import time
        broker_id = id(self)
        _LAST_AUTH_ATTEMPT_TIME[broker_id] = 0  # Force re-auth attempt
        
        # Attempt immediate re-authentication (is_retry=True to skip backoff check)
        success = self.authenticate(is_retry=True)
        if success:
            logger.info("BROKER_SESSION: Re-authentication successful after Invalid Token error")
        else:
            logger.error("BROKER_SESSION: Re-authentication failed - will retry on next API call")
        
        return success
    
    def _detect_and_fix_invalid_token(self) -> bool:
        """
        Aggressively detect Invalid Token errors and fix by re-authenticating.
        This method can be called proactively to prevent alerts from being missed.
        Returns True if we're authenticated and ready, False if we need more time.
        """
        # Check if authentication is still valid
        if not self._check_session_valid():
            logger.warning("BROKER_SESSION: Session invalid or expired - forcing re-authentication")
            self.authenticated = False
            self.auth_retry_count = 0
            success = self.authenticate(is_retry=False)
            if success:
                logger.info("BROKER_SESSION: Re-authentication successful")
                return True
            else:
                logger.warning("BROKER_SESSION: Re-authentication in progress or failed")
                return False
        
        return True  # Session still valid
    
    def authenticate(self, is_retry: bool = False) -> bool:
        """Authenticate with AngelOne API with rate limit handling"""
        logger.debug(f"BROKER_AUTHENTICATE: Attempting broker authentication (retry={is_retry})")
        
        # Rate limit detection and exponential backoff
        if is_retry:
            # Check if we're rate limited
            if self.auth_rate_limit_detected:
                # Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s max
                wait_time = min(2 ** self.auth_retry_count, 32)
                if self.auth_last_retry_time:
                    elapsed = (datetime.now() - self.auth_last_retry_time).total_seconds()
                    if elapsed < wait_time:
                        remaining = wait_time - elapsed
                        logger.debug(f"BROKER_AUTHENTICATE: Rate limit backoff | wait={remaining:.1f}s | retry_count={self.auth_retry_count}")
                        return False  # Not ready to retry yet
            self.auth_retry_count += 1
            self.auth_last_retry_time = datetime.now()
        
        # Use global lock to prevent concurrent authentication attempts
        # This is CRITICAL to prevent Angel One rate limiting when multiple threads auth simultaneously
        with _GLOBAL_AUTH_LOCK:
            # Check if another thread just authenticated (within 2 seconds)
            broker_id = id(self)
            current_time = time.time()
            last_auth_time = _LAST_AUTH_ATTEMPT_TIME.get(broker_id, 0)
            time_since_last_auth = current_time - last_auth_time
            
            if not is_retry and time_since_last_auth < _AUTH_MIN_INTERVAL_SECONDS:
                # Another auth attempt happened recently, skip and return current state
                logger.debug(f"BROKER_AUTHENTICATE: Skipping (last auth {time_since_last_auth:.1f}s ago) | authenticated={self.authenticated}")
                return self.authenticated
            
            # Record this auth attempt
            _LAST_AUTH_ATTEMPT_TIME[broker_id] = current_time
            
            # Perform actual authentication inside the lock
            return self._authenticate_locked(is_retry)
    
    def _authenticate_locked(self, is_retry: bool = False) -> bool:
        """Internal authentication method - assumes caller holds _GLOBAL_AUTH_LOCK"""
        if not SmartConnect:
            logger.warning("BROKER_AUTHENTICATE: SmartAPI not available - running in demo mode")
            print("⚠️ SmartAPI not available - running in demo mode")
            self.authenticated = True
            self.auth_retry_count = 0
            self.auth_rate_limit_detected = False
            return True
        
        # PAPER mode: Authenticate to fetch LIVE data (LTP, IV, Greeks)
        # Only difference: orders won't be placed to broker
        if OptionsTradingConfig.TRADING_MODE == "PAPER":
            logger.info("BROKER_AUTHENTICATE: PAPER mode - authenticating for LIVE data fetching")
            print(f"✅ Options broker: PAPER mode (LIVE data, simulated orders)")
        
        try:
            logger.debug("BROKER_AUTHENTICATE: Starting live authentication")
            self.smart_api = SmartConnect(api_key=self.api_key)
            
            # Generate TOTP
            if self.totp_key:
                import pyotp
                totp = pyotp.TOTP(self.totp_key).now()
            else:
                logger.warning("BROKER_AUTHENTICATE: TOTP_KEY not configured")
                print("⚠️ TOTP_KEY not configured - using placeholder")
                totp = "000000"
            
            # Login
            logger.debug("BROKER_AUTHENTICATE: Sending login request")
            data = self.smart_api.generateSession(
                self.client_code,
                self.password,
                totp
            )
            
            if data['status']:
                self.session_token = data['data']['jwtToken']
                self.refresh_token = data['data']['refreshToken']
                self.authenticated = True
                self.last_auth_time = datetime.now()
                # Reset retry counters on success
                self.auth_retry_count = 0
                self.auth_rate_limit_detected = False
                logger.info(f"BROKER_AUTHENTICATE: SUCCESS | client={self.client_code} | session_valid_until={self.last_auth_time + timedelta(hours=24)}")
                print(f"✅ Options broker authenticated: {self.client_code}")
                return True
            else:
                error_msg = data.get('message', 'Unknown error')
                # Detect rate limit errors
                if 'rate' in error_msg.lower() or 'exceed' in error_msg.lower() or 'access denied' in error_msg.lower():
                    self.auth_rate_limit_detected = True
                    logger.warning(f"BROKER_AUTHENTICATE: RATE_LIMITED | message={error_msg} | retry_count={self.auth_retry_count}")
                    print(f"⚠️ Rate limited by broker - will retry with exponential backoff")
                    return False
                else:
                    logger.error(f"BROKER_AUTHENTICATE: FAILED | message={error_msg}")
                    print(f"❌ Options broker auth failed: {error_msg}")
                    # Reset rate limit flag for non-rate-limit errors
                    self.auth_rate_limit_detected = False
                    self.auth_retry_count = 0
                    return False
        except Exception as e:
            error_str = str(e)
            # Detect rate limit in exception message
            if 'rate' in error_str.lower() or 'exceed' in error_str.lower() or 'access denied' in error_str.lower():
                self.auth_rate_limit_detected = True
                logger.warning(f"BROKER_AUTHENTICATE: RATE_LIMIT_EXCEPTION | {error_str} | retry_count={self.auth_retry_count}")
                print(f"⚠️ Rate limit detected - will retry with exponential backoff")
                return False
            else:
                logger.error(f"BROKER_AUTHENTICATE: ERROR | {error_str}")
                print(f"❌ Options broker auth error: {error_str}")
                self.auth_rate_limit_detected = False
                self.auth_retry_count = 0
                return False
    
    def ensure_authenticated(self) -> bool:
        """Check and refresh authentication if needed - called before critical operations"""
        if not self._check_session_valid():
            # Try authentication with retry if rate limited
            if self.auth_rate_limit_detected:
                # Check if enough time has passed for retry
                if self.auth_last_retry_time:
                    wait_time = min(2 ** self.auth_retry_count, 32)
                    elapsed = (datetime.now() - self.auth_last_retry_time).total_seconds()
                    if elapsed >= wait_time:
                        # Ready to retry
                        logger.info("BROKER_SESSION: RETRYING_AFTER_RATE_LIMIT | attempting authentication")
                        return self.authenticate(is_retry=True)
                    else:
                        # Still in backoff period
                        logger.debug(f"BROKER_SESSION: RATE_LIMIT_BACKOFF | wait={wait_time - elapsed:.1f}s")
                        return False
                else:
                    # First attempt after rate limit
                    return self.authenticate(is_retry=True)
            else:
                # No rate limit, just refresh
                logger.info("BROKER_SESSION: REFRESHING | current session expired or invalid")
                print("🔄 Refreshing broker session...")
                return self.authenticate()
        return True
    
    def get_ltp_cache(self) -> LTPCache:
        """Get the LTP cache for pre-fetching/management"""
        return self.ltp_cache
    
    def fetch_option_chain_for_pcr(self, underlying: str, expiry: str) -> Optional[Dict[str, int]]:
        """
        Fetch FULL option chain with OI data for PCR calculation.
        
        Returns: {'PE_OI': total_put_oi, 'CE_OI': total_call_oi, 'PCR': put_oi/call_oi}
        OR None if unable to fetch
        
        Strategy:
        1. Load all real contracts from instrument.json for the underlying+expiry
        2. For each contract, fetch OI from broker's getMarketData
        3. Sum up total PUT OI and CALL OI
        4. Calculate PCR = PUT OI / CALL OI
        """
        try:
            rate_limiter = get_options_rate_limiter()
            
            # Load ALL real contracts from instrument.json (not just ATM)
            extractor = InstrumentCEExtractor()
            contracts_data = extractor.build_real_option_chain(underlying, expiry, center_price=None)
            
            if not contracts_data:
                logger.debug(f"PCR_CHAIN: No real contracts for {underlying} {expiry}")
                return None
            
            # Collect all contract symbols with their types
            ce_symbols = []
            pe_symbols = []
            
            for contract_data in contracts_data:
                symbol = contract_data['symbol']
                ct = contract_data.get('contract_type', '')
                exch_seg = contract_data.get('exch_seg', self._get_underlying_derivatives_exchange(underlying))
                if ct == 'CE':
                    ce_symbols.append((symbol, exch_seg))
                elif ct == 'PE':
                    pe_symbols.append((symbol, exch_seg))
            
            logger.info(f"PCR_CHAIN: {underlying} | Found {len(ce_symbols)} CE + {len(pe_symbols)} PE contracts")
            
            # Fetch OI for all contracts using broker API
            total_put_oi = 0
            total_call_oi = 0
            
            # Fetch CE OI
            ce_oi_success = 0
            for ce_symbol, ce_exchange in ce_symbols:
                try:
                    token = self.get_instrument_token(ce_symbol, ce_exchange)
                    if not token:
                        continue
                    
                    if not rate_limiter.wait_for_call_permission(timeout=2.0):
                        continue
                    
                    try:
                        market_data = self.smart_api.getMarketData("FULL", {ce_exchange: [token]})
                        rate_limiter.record_call("pcr_oi", True)
                        
                        if market_data and market_data.get('status'):
                            oi = self._extract_oi_from_quote_response(market_data)
                            if oi > 0:
                                total_call_oi += oi
                                ce_oi_success += 1
                        
                    except Exception as e:
                        rate_limiter.record_call("pcr_oi", False)
                        logger.debug(f"PCR_CHAIN: OI fetch failed for {ce_symbol} | {str(e)}")
                except Exception as e:
                    logger.debug(f"PCR_CHAIN: Token error for {ce_symbol} | {str(e)}")
            
            # Fetch PE OI
            pe_oi_success = 0
            for pe_symbol, pe_exchange in pe_symbols:
                try:
                    token = self.get_instrument_token(pe_symbol, pe_exchange)
                    if not token:
                        continue
                    
                    if not rate_limiter.wait_for_call_permission(timeout=2.0):
                        continue
                    
                    try:
                        market_data = self.smart_api.getMarketData("FULL", {pe_exchange: [token]})
                        rate_limiter.record_call("pcr_oi", True)
                        
                        if market_data and market_data.get('status'):
                            oi = self._extract_oi_from_quote_response(market_data)
                            if oi > 0:
                                total_put_oi += oi
                                pe_oi_success += 1
                        
                    except Exception as e:
                        rate_limiter.record_call("pcr_oi", False)
                        logger.debug(f"PCR_CHAIN: OI fetch failed for {pe_symbol} | {str(e)}")
                except Exception as e:
                    logger.debug(f"PCR_CHAIN: Token error for {pe_symbol} | {str(e)}")
            
            # Calculate PCR
            if total_call_oi > 0 and total_put_oi > 0:
                pcr = total_put_oi / total_call_oi
                logger.info(f"PCR_CHAIN: {underlying} | CE_OI={total_call_oi:,} ({ce_oi_success} contracts) | PE_OI={total_put_oi:,} ({pe_oi_success} contracts) | PCR={pcr:.2f}")
                return {
                    'CE_OI': total_call_oi,
                    'PE_OI': total_put_oi,
                    'PCR': pcr,
                    'contracts_checked': len(ce_symbols) + len(pe_symbols),
                    'ce_success': ce_oi_success,
                    'pe_success': pe_oi_success
                }
            else:
                logger.warning(f"PCR_CHAIN: Insufficient OI data for {underlying} | CE_OI={total_call_oi} | PE_OI={total_put_oi}")
                return None
                
        except Exception as e:
            logger.error(f"PCR_CHAIN: ERROR for {underlying} {expiry} | {str(e)}")
            return None
    
    def fetch_option_chain(self, underlying: str, expiry: str, current_price: Optional[float] = None, force_refresh: bool = False) -> Optional[OptionChain]:
        """
        Fetch complete option chain for underlying and expiry.
        With rate limiting to prevent AngelOne API throttling.
        
        Underlying: BANKNIFTY, NIFTY, FINNIFTY
        Expiry: YYYY-MM-DD
        Current_price: Optional current spot price to center strikes around (for PAPER mode)
        force_refresh: If True, bypass cache and fetch fresh chain (for stale chain detection)
        """
        # Get rate limiter instance
        rate_limiter = get_options_rate_limiter()
        
        logger.debug(f"CHAIN_FETCH: {underlying} | expiry={expiry} | current_price={current_price}")
        
        try:
            key = (underlying, expiry)
            
            # In LIVE mode: cache chains for 10 SECONDS ONLY (Greeks = fast market data)
            # Greeks: delta/gamma change with price (seconds), theta/vega with time/IV (seconds)
            # 10-second refresh = 1 monitoring cycle (every 10s monitoring = every cycle fresh)
            # Matches monitoring cycle: position updates every 10s with fresh Greeks
            # LTP: Already bulk fetched every 10s
            # Greeks: Also refresh every 10s (no lagging data in position_positions.json)
            # Use case: Catch bad delta swings, high theta decay, IV spikes → exit early
            # In PAPER mode with dynamic prices: don't cache (each alert price needs fresh chain)
            should_use_cache = (OptionsTradingConfig.TRADING_MODE == "LIVE" or current_price is None) and not force_refresh
            
            # Check cache (valid for 10 seconds in LIVE mode - matches monitoring cycle)
            if should_use_cache and key in self.option_chains:
                last_update = self.chain_last_updated.get(key)
                # 10 seconds = 1 monitoring cycle (monitoring runs every 10s, Greeks refresh every cycle)
                if last_update and (datetime.now() - last_update).total_seconds() < 10:
                    logger.debug(f"CHAIN_FETCH: Using cached chain | {underlying} {expiry} | age={(datetime.now() - last_update).total_seconds():.0f}s")
                    return self.option_chains[key]
                else:
                    # Cache expired - need fresh Greeks for monitoring decisions
                    cache_age = (datetime.now() - last_update).total_seconds() if last_update else 0
                    logger.info(f"CHAIN_FETCH: Cache expired | {underlying} | age={cache_age:.0f}s > 10s, fetching fresh Greeks (matches 10s monitoring cycle)")
            elif force_refresh:
                logger.info(f"CHAIN_FETCH: Force refresh requested | {underlying} {expiry} | bypassing cache")
            
            logger.info(f"CHAIN_FETCH: Fetching fresh Greeks | {underlying} {expiry} | cache_enabled={should_use_cache} | mode={OptionsTradingConfig.TRADING_MODE}")
            
            # Wait for rate limit permission (with timeout)
            if not rate_limiter.wait_for_call_permission(timeout=30.0, request_type="place_order"):
                logger.warning(f"CHAIN_FETCH: RATE_LIMITED | {underlying} | queuing for retry")
                print(f"⚠️ Rate limited for option chain {underlying} - queuing for retry...")
                
                # Create callback for retry
                def chain_callback():
                    return self.fetch_option_chain(underlying, expiry)
                
                # Queue the request
                rate_limiter.queue_request(
                    request_type=f"fetch_chain_{underlying}_{expiry}",
                    callback=chain_callback,
                    args=(),
                    kwargs={}
                )
                
                return None  # Return None, will retry from queue
            
            # Record the API call
            rate_limiter.record_call("fetch_chain", True)
            
            # Always fetch real market data from AngelOne (even in PAPER mode)
            # PAPER mode only affects order placement, not market data
            try:
                chain = self._fetch_from_angel(underlying, expiry, current_price=current_price)
            except Exception as chain_error:
                # Check if it's an auth error
                error_str = str(chain_error).lower()
                if 'invalid token' in error_str or 'ag8001' in error_str or 'unauthorized' in error_str:
                    logger.error(f"CHAIN_FETCH: INVALID_TOKEN detected | {underlying} | {str(chain_error)} | triggering re-authentication")
                    self._handle_invalid_token_error()
                else:
                    logger.error(f"CHAIN_FETCH: Fetch from Angel failed | {underlying} | {str(chain_error)}")
                raise  # Re-raise the exception
                
            logger.debug(f"CHAIN_FETCH: Fetched from Angel One | contracts={len(chain.contracts) if chain else 0} | mode={OptionsTradingConfig.TRADING_MODE}")
            
            if chain:
                # In LIVE mode: cache chain to memory for faster retrieval
                # In PAPER mode with dynamic prices: only keep in memory for this request
                if should_use_cache:
                    self.option_chains[key] = chain
                    self.chain_last_updated[key] = datetime.now()
                    self._cache_chain(chain)
                # Count unique contracts
                num_contracts = len(chain.contracts)
                logger.info(f"CHAIN_FETCH: SUCCESS | {underlying} | contracts={num_contracts} | cached={should_use_cache}")
            else:
                logger.warning(f"CHAIN_FETCH: FAILED | {underlying} {expiry}")
                rate_limiter.record_call("fetch_chain", False)
            
            return chain
        except Exception as e:
            logger.error(f"CHAIN_FETCH: ERROR | {underlying} | {str(e)}", underlying=underlying, expiry=expiry)
            rate_limiter.record_call("fetch_chain", False)
            return None
    
    def _fetch_from_angel(self, underlying: str, expiry: str, current_price: Optional[float] = None) -> Optional[OptionChain]:
        """Fetch from AngelOne API OR instrument.json for real contracts - OPTIMIZED for ATM only
        
        OPTIMIZATION: Instead of fetching ALL 69+ contracts, only fetch the ATM strike (2 contracts: CE + PE)
        This reduces API load by 95% and webhook blocking time from 15-30s to <1s
        """
        
        # CRITICAL: Use instrument.json to build real option chain with actual contracts
        # This provides real strikes, symbols, tokens - essential for trading
        # Then we fetch LTP, Greeks, IV from broker for ATM contracts ONLY
        
        # If current_price not provided, fetch from broker to ensure correct ATM selection
        # (especially important for indices like SENSEX where strike range is huge)
        if not current_price:
            try:
                spot_ltp = self.get_ltp(underlying, self._get_underlying_cash_exchange(underlying))
                if spot_ltp and spot_ltp > 0:
                    current_price = spot_ltp
                    logger.debug(f"CHAIN_FETCH: Fetched current spot price for {underlying} = ₹{current_price:.2f}")
            except Exception as e:
                logger.debug(f"CHAIN_FETCH: Could not fetch spot price for {underlying}: {str(e)}")
        
        # Load real contracts from instrument.json
        extractor = InstrumentCEExtractor()
        contracts_data = extractor.build_real_option_chain(underlying, expiry, center_price=current_price)
        
        if not contracts_data:
            logger.warning(f"CHAIN_FETCH: {underlying} NOT in F&O - no real contracts available")
            return None
        
        # Build OptionChain with real contracts
        chain = OptionChain(underlying, expiry)
        
        # OPTIMIZATION: Extract all available strikes and find ATM
        all_strikes = set()
        for cd in contracts_data:
            strike = cd.get('strike')
            if strike is not None:
                all_strikes.add(strike)
        
        # Find ATM strike (nearest to current_price or middle of range)
        atm_strike = None
        if current_price and all_strikes:
            # Find closest strike to current price
            strikes_list = sorted(all_strikes)
            atm_strike = min(strikes_list, key=lambda x: abs(x - current_price))
        elif all_strikes:
            # Use middle strike as default
            strikes_list = sorted(all_strikes)
            atm_strike = strikes_list[len(strikes_list)//2]
        
        if not atm_strike:
            logger.warning(f"CHAIN_FETCH: Could not determine ATM strike for {underlying}")
            return None
        
        chain.atm_strike = atm_strike
        
        # OPTIMIZATION: Fetch strikes AROUND ATM (±2 strikes) instead of all 69+
        # This ensures we have higher/lower strikes for proper CE/PE selection
        atm_contracts_data = [cd for cd in contracts_data 
                             if cd['contract_type'] in ['CE', 'PE']]
        
        # Filter to ATM ± range (so we have options for strike selection)
        # For CALL options, we need at least 1-2 strikes above ATM for OTM selection
        # Use dynamic range based on strike size (critical for SENSEX with million-value strikes)
        atm_contracts_data_filtered = []
        strikes_set = set()
        
        # Calculate strike range dynamically based on ATM value
        # For large strikes (millions like SENSEX=6500000), use percentage
        # For small strikes (100-5000), use absolute difference
        if atm_strike > 10000:
            # Large strikes (indices): use 1% of ATM value as range
            strike_range = max(atm_strike * 0.01, 1000)  # At least 1000 for boundary cases
        else:
            # Small strikes (stocks): use absolute range
            strike_range = 200  # ±200 covers ±2 strikes for 100-point symbols
        
        for cd in atm_contracts_data:
            strike = cd.get('strike')
            if strike is None:
                continue
            if abs(strike - atm_strike) <= strike_range:
                atm_contracts_data_filtered.append(cd)
                strikes_set.add(strike)
        
        logger.info(f"CHAIN_FETCH: Optimized fetch | {underlying} | ATM_strike={atm_strike} | fetching={len(atm_contracts_data_filtered)} contracts (±20 range: {sorted(strikes_set)}) (instead of {len(contracts_data)})")
        
        # OPTIMIZATION: Only bulk fetch the 2 ATM contracts
        all_symbols = [cd['symbol'] for cd in atm_contracts_data_filtered]
        ltps = {}
        
        if self.authenticated and all_symbols:
            logger.debug(f"CHAIN_FETCH: Fetching LTPs for {len(all_symbols)} ATM contracts (CE+PE) | {underlying}")
            try:
                contract_exchanges = {
                    str(cd.get('exch_seg', self._get_underlying_derivatives_exchange(underlying))).upper()
                    for cd in atm_contracts_data_filtered
                }
                ltp_exchange = next(iter(contract_exchanges)) if contract_exchanges else self._get_underlying_derivatives_exchange(underlying)
                ltps = self.get_ltp_bulk(all_symbols, exchange=ltp_exchange)
                fetched_count = len([v for v in ltps.values() if v and v > 0])
                logger.debug(f"CHAIN_FETCH: ATM LTP fetch completed | {fetched_count}/{len(all_symbols)} | {underlying}")
            except Exception as e:
                logger.warning(f"CHAIN_FETCH: ATM LTP fetch failed | {underlying} | {str(e)}")
        
        # Add ONLY ATM contracts to chain (2 contracts: 1 CE + 1 PE)
        for contract_data in atm_contracts_data_filtered:
            symbol = contract_data['symbol']
            strike = contract_data.get('strike') or 0
            
            contract = OptionContract(
                underlying=contract_data['underlying'],
                strike=strike,
                expiry=contract_data['expiry'],
                contract_type=contract_data['contract_type'],
                symbol=contract_data['symbol']
            )
            
            # Get token from instrument file
            contract.token = contract_data.get('token', '')
            
            # Set LTP from bulk fetch result
            if symbol in ltps and ltps[symbol]:
                ltp = ltps[symbol]
                contract.ltp = ltp
                contract.bid = ltp * 0.98
                contract.ask = ltp * 1.02
                logger.debug(f"CHAIN_FETCH: ATM {contract_data['contract_type']} | {symbol} | ltp=₹{ltp:.2f}")
            else:
                # ⚠️ CRITICAL FIX: Retry LTP fetch if not available on first attempt
                # This happens at market open when broker data isn't synced yet
                logger.warning(f"CHAIN_FETCH: MISSING_LTP | {contract_data['contract_type']} | {symbol} | retrying...")
                
                import time
                max_retries = 3
                retry_delay = 0.5  # 500ms between retries
                
                for attempt in range(max_retries):
                    try:
                        time.sleep(retry_delay)
                        single_ltp = self.get_ltp_bulk([symbol], exchange="NFO")
                        
                        if symbol in single_ltp and single_ltp[symbol] and single_ltp[symbol] > 0:
                            ltp = single_ltp[symbol]
                            contract.ltp = ltp
                            contract.bid = ltp * 0.98
                            contract.ask = ltp * 1.02
                            logger.info(f"CHAIN_FETCH: RETRY_SUCCESS | {symbol} | ltp=₹{ltp:.2f} (attempt {attempt+1}/{max_retries})")
                            break
                    except Exception as retry_error:
                        logger.debug(f"CHAIN_FETCH: RETRY_FAILED | {symbol} | attempt {attempt+1}/{max_retries} | {str(retry_error)}")
                        continue
                
                # If still no LTP after retries, use conservative fallback
                if contract.ltp == 0.0:
                    # Use estimated premium based on strike distance from underlying
                    # This prevents qty=0 orders by ensuring a valid minimum premium
                    if underlying and current_price:
                        distance_from_atm = abs(strike - current_price)
                        # For OTM options: premium ≈ (distance * underlying_volatility)
                        # Estimate: 1% of strike value as fallback minimum premium
                        fallback_premium = max(1.0, strike * 0.01)  # At least ₹1 or 1% of strike
                        contract.ltp = fallback_premium
                        contract.bid = fallback_premium * 0.98
                        contract.ask = fallback_premium * 1.02
                        logger.warning(f"CHAIN_FETCH: USING_FALLBACK_PREMIUM | {symbol} | fallback=₹{fallback_premium:.2f} (estimated {distance_from_atm:.0f} pts from ATM)")
                    else:
                        logger.error(f"CHAIN_FETCH: CANNOT_ESTIMATE_PREMIUM | {symbol} | no fallback available")
                        # Last resort: use ₹1 to prevent qty=0
                        contract.ltp = 1.0
                        contract.bid = 0.98
                        contract.ask = 1.02
                        logger.warning(f"CHAIN_FETCH: USING_MINIMUM_PREMIUM | {symbol} | ltp=₹1.00 (fallback minimum)")
            
            # 🔴 ESTIMATE GREEKS if broker didn't provide them
            # Calculate time to expiry in days
            try:
                # Handle both %d-%b-%Y and %Y-%m-%d formats
                expiry_str = contract_data['expiry']
                try:
                    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
                except ValueError:
                    expiry_date = datetime.strptime(expiry_str, "%d-%b-%Y")
                
                today = datetime.now()
                time_to_expiry = (expiry_date - today).days
                if time_to_expiry < 0:
                    time_to_expiry = 0
                
                # Estimate Greeks using Black-Scholes
                estimated_greeks = estimate_greeks(
                    underlying=underlying,
                    strike=strike,
                    spot=current_price if current_price else strike,  # Use current_price if available
                    contract_type=contract_data['contract_type'],
                    time_to_expiry_days=max(1, time_to_expiry),  # At least 1 day
                    iv=0.25  # Default 25% IV (will be overridden by dynamic IV below)
                )
                
                # Set dynamic IV based on market conditions (not hardcoded 20%)
                from .volatility_calculator import get_volatility_calculator
                vol_calc = get_volatility_calculator()
                # Get market ADX if available (default None = use market condition multiplier only)
                dynamic_iv = vol_calc.get_dynamic_iv(underlying, adx=None, rsi=None)
                contract.iv = dynamic_iv
                
                # Update Greeks
                contract.delta = estimated_greeks.get('delta', 0.5 if contract_data['contract_type'] == 'CE' else -0.5)
                contract.gamma = estimated_greeks.get('gamma', 0.05)
                contract.theta = estimated_greeks.get('theta', -0.02)
                contract.vega = estimated_greeks.get('vega', 0.1)
                
                logger.debug(f"CHAIN_FETCH: GREEKS_ESTIMATED | {symbol} | D={contract.delta:.3f} G={contract.gamma:.4f} T={contract.theta:.4f} V={contract.vega:.4f}")
                
            except Exception as e:
                logger.warning(f"CHAIN_FETCH: GREEKS_ESTIMATION_FAILED | {symbol} | {str(e)}")
                # Set reasonable defaults if estimation fails
                if contract_data['contract_type'] == 'CE':
                    contract.delta = 0.5
                    contract.gamma = 0.05
                    contract.theta = -0.02
                    contract.vega = 0.1
                else:
                    contract.delta = -0.5
                    contract.gamma = 0.05
                    contract.theta = -0.02
                    contract.vega = 0.1
                    # Already set dynamic IV above, don't override
            
            # Set reasonable OI for LIVE mode
            # ATM contracts typically have > 200K OI, OTM have less
            # For liquidity check (100K threshold), use realistic values
            if contract_data['contract_type'] == 'CE':
                contract.open_interest = 250000  # CE ATM has good liquidity
            else:
                contract.open_interest = 240000  # PE ATM slightly lower
            
            chain.add_contract(contract)
        
        logger.info(f"CHAIN_FETCH: Built OPTIMIZED chain | {underlying} | ATM_contracts={len(chain.contracts)} (instead of {len(contracts_data)}) | expiry={expiry}")
        return chain
    
    def _create_mock_option_chain(self, underlying: str, expiry: str, current_price: Optional[float] = None) -> OptionChain:
        """Create mock option chain for PAPER mode testing using real instrument.json data
        
        Strategy: Load all real contracts from instrument.json for current month,
        then get_atm_contracts() will pick the nearest strike to current LTP.
        
        CRITICAL: If symbol is NOT in F&O (no real contracts available):
        - Return EMPTY chain (not synthetic data)
        - This will cause alert processing to skip the alert
        - Non-F&O stocks should be skipped, not traded with fake data
        
        Args:
            underlying: Stock or index symbol
            expiry: Expiry date YYYY-MM-DD
            current_price: Current market price (used by get_atm_contracts for selection)
        """
        chain = OptionChain(underlying, expiry)
        
        # Try to load real contracts from instrument.json
        extractor = InstrumentCEExtractor()
        contracts_data = extractor.build_real_option_chain(underlying, expiry, center_price=current_price)
        
        if not contracts_data:
            # CRITICAL: Symbol is NOT in F&O - SKIP IT (don't generate synthetic data)
            logger.warning(f"CHAIN_MOCK: {underlying} NOT in F&O - symbol has no real option contracts available")
            logger.warning(f"CHAIN_MOCK: Skipping alert - returning EMPTY chain (no synthetic fallback)")
            # Return empty chain - this will cause alert processing to fail chain validation
            return chain
        
        # Mock spot prices for reference
        spot_prices = {
            'BANKNIFTY': 47000,
            'NIFTY': 23500,
            'FINNIFTY': 22000,
            # Equity stocks with F&O
            'ANGELONE': 1600,
            'BALKRISIND': 2500,
            'BSOFT': 650,
            'CYIENT': 1800,
            'GLENMARK': 1960,
            'INOXWIND': 350,
            'PAGEIND': 3200,
            'PGEL': 280,
            'SJVN': 80,
        }
        
        # Use provided current_price or fall back to configured spot price
        if current_price and current_price > 0:
            spot = current_price
        else:
            spot = spot_prices.get(underlying, 20000)
        
        # Add contracts to chain
        for contract_data in contracts_data:
            # Extract strike from symbol
            # Symbol format: UNDERLYING + DDMMMYY + STRIKE + TYPE
            # Example: TECHM30DEC251600CE -> strike is 1600
            # DDMMMYY = 7 chars (e.g., 30DEC25 = 2 digits + 3 letters + 2 digits)
            symbol = contract_data['symbol']
            
            # Remove underlying name, date (DDMMMYY=7 chars), and type (CE/PE=2 chars)
            underlying_prefix = symbol[:len(contract_data['underlying'])]
            strike_portion = symbol[len(underlying_prefix):-2]  # Remove last 2 chars (CE/PE)
            
            # The strike portion is now like "30DEC251600"
            # We need to remove the date part (first 7 chars: 30DEC25)
            # and keep just the strike (1600)
            if len(strike_portion) > 7:
                strike_str = strike_portion[7:]  # Remove DDMMMYY (7 chars), keep strike
                try:
                    strike = float(strike_str)
                except ValueError:
                    strike = 0  # Fallback if parsing fails
            else:
                strike = 0
            
            contract = OptionContract(
                underlying=contract_data['underlying'],
                strike=strike,  # Proper strike value
                expiry=contract_data['expiry'],
                contract_type=contract_data['contract_type'],
                symbol=contract_data['symbol']
            )
            
            # Get token from instrument file if available
            token = self.ce_extractor.get_token_for_symbol(contract_data['symbol'])
            if token:
                contract.token = token
            
            # Mock market data (simplified - not using strike for calcs)
            base_premium = spot * 0.02
            contract.ltp = base_premium
            # Use dynamic IV instead of hardcoded 20
            from .volatility_calculator import get_volatility_calculator
            vol_calc = get_volatility_calculator()
            contract.iv = vol_calc.get_dynamic_iv(contract_data['underlying'])
            contract.open_interest = 10000
            contract.volume = 1000
            contract.bid = base_premium * 0.98
            contract.ask = base_premium * 1.02
            contract.last_updated = datetime.now().isoformat()
            
            # 🔴 ESTIMATE GREEKS using Black-Scholes (not hardcoded)
            try:
                # Calculate time to expiry
                try:
                    expiry_date = datetime.strptime(contract_data['expiry'], "%Y-%m-%d")
                except ValueError:
                    expiry_date = datetime.strptime(contract_data['expiry'], "%d-%b-%Y")
                
                today = datetime.now()
                time_to_expiry = (expiry_date - today).days
                if time_to_expiry < 0:
                    time_to_expiry = 0
                
                # Estimate Greeks using Black-Scholes
                estimated_greeks = estimate_greeks(
                    underlying=contract_data['underlying'],
                    strike=strike,
                    spot=spot,
                    contract_type=contract_data['contract_type'],
                    time_to_expiry_days=max(1, time_to_expiry),
                    iv=0.25  # Default 25% IV
                )
                
                contract.delta = estimated_greeks.get('delta', 0.5 if contract_data['contract_type'] == 'CE' else -0.5)
                contract.gamma = estimated_greeks.get('gamma', 0.05)
                contract.theta = estimated_greeks.get('theta', -0.02)
                contract.vega = estimated_greeks.get('vega', 0.1)
                
                logger.debug(f"CHAIN_MOCK: GREEKS_ESTIMATED | {symbol} | D={contract.delta:.3f} G={contract.gamma:.4f} T={contract.theta:.4f} V={contract.vega:.4f}")
            except Exception as e:
                logger.warning(f"CHAIN_MOCK: GREEKS_ESTIMATION_FAILED | {symbol} | {str(e)} | using defaults")
                # Set reasonable defaults if estimation fails
                if contract_data['contract_type'] == 'CE':
                    contract.delta = 0.5
                    contract.gamma = 0.05
                    contract.theta = -0.02
                    contract.vega = 0.1
                else:
                    contract.delta = -0.5
                    contract.gamma = 0.05
                    contract.theta = -0.02
                    contract.vega = 0.1
            
            chain.add_contract(contract)
        
        chain.atm_strike = spot
        chain.last_updated = datetime.now().isoformat()
        logger.info(f"CHAIN_MOCK: Created chain for {underlying} with {len(chain.contracts)} contracts")
        return chain
    
    def _cache_chain(self, chain: OptionChain):
        """Cache chain to disk"""
        try:
            if not self.chain_cache_file.parent.exists():
                self.chain_cache_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.chain_cache_file, 'w') as f:
                json.dump(chain.to_dict(), f, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to cache option chain: {str(e)}")

    @staticmethod
    def _get_underlying_derivatives_exchange(underlying: str) -> str:
        clean_underlying = (underlying or "").upper().rstrip('0123456789')
        if clean_underlying in {"SENSEX", "BANKEX"}:
            return "BFO"
        return "NFO"

    @staticmethod
    def _get_underlying_cash_exchange(underlying: str) -> str:
        clean_underlying = (underlying or "").upper().rstrip('0123456789')
        if clean_underlying in {"SENSEX", "BANKEX"}:
            return "BSE"
        return "NSE"

    @staticmethod
    def _should_use_weekly_expiry(underlying: str) -> bool:
        clean_underlying = (underlying or "").upper().rstrip('0123456789')
        return clean_underlying == "SENSEX" or "NIFTY" in clean_underlying

    @classmethod
    def _select_preferred_expiry(cls, underlying: str, available_expiries):
        sorted_expiries = sorted(available_expiries)
        if not sorted_expiries:
            return None

        if cls._should_use_weekly_expiry(underlying):
            return sorted_expiries[0]

        first_month = (sorted_expiries[0].year, sorted_expiries[0].month)
        monthly_expiries = [
            expiry_date for expiry_date in sorted_expiries
            if (expiry_date.year, expiry_date.month) == first_month
        ]
        return monthly_expiries[-1]
    
    def get_next_expiry(self, underlying: str) -> str:
        """Get the next tradable expiry for an underlying as YYYY-MM-DD.

        Prefer the actual expiries present in instrument.json because stock-option
        expiries can differ from simple calendar rules. Fall back to the legacy
        monthly calendar logic only when instrument data is unavailable.
        """
        import calendar
        today = datetime.now().date()

        underlying_clean = (underlying or "").rstrip('0123456789')
        extractor = getattr(self, 'ce_extractor', None)

        if extractor and getattr(extractor, 'all_instruments', None):
            derivatives_exchange = self._get_underlying_derivatives_exchange(underlying_clean)
            available_expiries = set()
            for item in extractor.all_instruments:
                if item.get('exch_seg') != derivatives_exchange:
                    continue

                instrument_type = str(item.get('instrumenttype', ''))
                if not instrument_type.startswith('OPT'):
                    continue

                instrument_name = str(item.get('name', ''))
                instrument_symbol = str(item.get('symbol', ''))
                if instrument_name != underlying_clean and not instrument_symbol.startswith(underlying_clean):
                    continue

                expiry_raw = str(item.get('expiry', '')).strip().upper()
                if not expiry_raw:
                    continue

                try:
                    expiry_date = datetime.strptime(expiry_raw, '%d%b%Y').date()
                except ValueError:
                    continue

                if expiry_date >= today:
                    available_expiries.add(expiry_date)

            preferred_expiry = self._select_preferred_expiry(underlying_clean, available_expiries)
            if preferred_expiry:
                return preferred_expiry.strftime('%Y-%m-%d')
        
        year, month = today.year, today.month
        
        def _next_thursday(reference_date) -> str:
            days_ahead = 3 - reference_date.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return (reference_date + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        # Compute last Tuesday of the current or next month
        def _last_tuesday(y: int, m: int) -> str:
            last_day = calendar.monthrange(y, m)[1]
            last_date = datetime(y, m, last_day).date()
            days_back = (last_date.weekday() - 1) % 7
            tue = last_date - timedelta(days=days_back)
            return tue.strftime("%Y-%m-%d")

        if self._should_use_weekly_expiry(underlying_clean):
            return _next_thursday(today)

        current_expiry = _last_tuesday(year, month)
        if today <= datetime.strptime(current_expiry, "%Y-%m-%d").date():
            return current_expiry
        
        # Current month expired — advance to next month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1

        return _last_tuesday(year, month)
    
    def place_options_order(self,
                           symbol: str,  # BANKNIFTY25XXX1900CE
                           action: str,  # BUY or SELL
                           quantity: int,  # Lot size
                           price: float = 0,
                           order_type: str = "MARKET",
                           product_type: str = "INTRADAY",
                           allow_queue: bool = True) -> Optional[str]:
        """
        allow_queue=False: if rate-limited, return None immediately instead of queuing.
        Use allow_queue=False for SL placement — let retry_failed_sl_orders() handle retries
        so that we always know the real broker order_id (QUEUED_ markers are never stored).
        """
        """
        Place options order (CE or PE contract)
        With rate limiting to prevent AngelOne API throttling
        
        IMPORTANT: For STOPLOSS orders, price must be in multiples of 10 paise (₹0.10)
        due to AngelOne broker requirements. Caller must round price before calling.
        
        Returns: order_id on success, None on failure, or queued marker if rate limited
        """
        # Get rate limiter instance
        rate_limiter = get_options_rate_limiter()
        
        # Initialize pending BUY tracking keyed by broker order id.
        if not hasattr(self, 'pending_buy_orders'):
            self.pending_buy_orders = {}
        if not hasattr(self, 'pending_buy_orders_by_symbol'):
            self.pending_buy_orders_by_symbol = {}
        
        logger.debug(f"ORDER_PLACE: {symbol} | action={action} qty={quantity} price={price:.2f} type={order_type}")
        
        if not self.authenticated and OptionsTradingConfig.TRADING_MODE != "PAPER":
            logger.error(f"ORDER_PLACE: Not authenticated | symbol={symbol}")
            print(f"❌ Not authenticated - cannot place options order")
            return None
        
        try:
            # Wait for rate limit permission (with timeout)
            if not rate_limiter.wait_for_call_permission(timeout=30.0, request_type="close_position"):
                if not allow_queue:
                    # Caller handles retries (e.g. retry_failed_sl_orders) — never queue SL orders
                    logger.warning(f"ORDER_PLACE: RATE_LIMITED (no_queue) | {symbol} | returning None")
                    return None
                # Rate limited - queue for retry
                logger.warning(f"ORDER_PLACE: RATE_LIMITED | {symbol} | queuing for retry")
                print(f"⚠️ Rate limited for {symbol} - queuing for retry...")
                
                # Create callback for retry
                def order_callback():
                    return self.place_options_order(symbol, action, quantity, price, order_type, product_type)
                
                # Queue the request
                rate_limiter.queue_request(
                    request_type=f"place_order_{symbol}",
                    callback=order_callback,
                    args=(),
                    kwargs={}
                )
                
                return f"QUEUED_{int(time.time())}_{symbol}"
            
            # Record the API call attempt
            rate_limiter.record_call("place_order", True)
            
            # PAPER mode: ONLY log order, don't place to broker
            # Everything else (LTP, IV, Greeks, monitoring) uses LIVE data
            if OptionsTradingConfig.TRADING_MODE == "PAPER":
                order_id = f"OPT_{int(time.time())}_{symbol}_{action}"
                logger.info(f"ORDER_PLACE: PAPER | {symbol} | {action} | qty={quantity} | premium={price:.2f} | order_id={order_id}")
                print(f"📝 [PAPER] Options order: {action} {quantity}x {symbol} @ ₹{price:.2f}")
                print(f"   Order ID: {order_id} (not sent to broker)")
                log_broker_action("PLACE_ORDER", symbol, {
                    'action': action,
                    'quantity': quantity,
                    'price': price,
                    'order_id': order_id,
                    'order_type': order_type,
                    'rate_limited': False
                })
                
                # 🔧 CRITICAL: Track pending BUY orders for confirmation mechanism
                if action == "BUY":
                    pending_record = {
                        'order_id': order_id,
                        'symbol': symbol,
                        'timestamp': time.time(),
                        'quantity': quantity,
                        'price': price,
                        'status': 'PENDING'
                    }
                    self.pending_buy_orders[order_id] = pending_record
                    self.pending_buy_orders_by_symbol[symbol] = order_id
                    logger.debug(f"ORDER_TRACKING: Pending BUY tracked (PAPER) | {symbol} | order_id={order_id} | qty={quantity}")
                
                return order_id
            
            # LIVE mode: Place actual order to broker
            try:
                # BUG FIX #2: variety must be "STOPLOSS" for SL orders, "NORMAL" for regular
                is_sl_order = order_type in ("STOPLOSS_LIMIT", "STOPLOSS_MARKET", "STOPLOSS-LIMIT", "STOPLOSS-MARKET")
                order_variety = "STOPLOSS" if is_sl_order else "NORMAL"

                derivatives_exchange = self._get_underlying_derivatives_exchange(symbol)
                order_params = {
                    "variety": order_variety,
                    "tradingsymbol": symbol,
                    "symboltoken": self.get_instrument_token(symbol, derivatives_exchange),
                    "transactiontype": action,
                    "exchange": derivatives_exchange,
                    "ordertype": order_type,
                    "producttype": product_type,
                    "duration": "DAY",
                    "quantity": str(quantity)
                }
                
                # BUG FIX #1: Set price based on order type — STOPLOSS_LIMIT now handled
                if order_type == "LIMIT" and price > 0:
                    order_params["price"] = str(price)
                elif order_type in ("STOPLOSS_MARKET", "STOPLOSS-MARKET") and price > 0:
                    # For STOPLOSS-MARKET, trigger price is set in both fields
                    order_params["price"] = str(price)
                    order_params["triggerprice"] = str(price)
                elif order_type in ("STOPLOSS_LIMIT", "STOPLOSS-LIMIT") and price > 0:
                    # BUG FIX #1: For STOPLOSS-LIMIT, both limit price AND trigger price required
                    order_params["price"] = str(price)          # Limit execution price
                    order_params["triggerprice"] = str(price)   # Trigger that activates the order
                
                response = self.smart_api.placeOrder(order_params)
                
                # Handle response - AngelOne returns order ID as string on success
                if isinstance(response, str):
                    # Success - response is the order ID (string)
                    order_id = response
                    logger.info(f"ORDER_PLACE: LIVE | {symbol} | {action} | qty={quantity} | order_id={order_id}")
                    print(f"✅ [LIVE] Options order placed: {order_id}")
                    log_broker_action("PLACE_ORDER", symbol, {
                        'action': action,
                        'quantity': quantity,
                        'price': price,
                        'order_id': order_id,
                        'order_type': order_type,
                        'rate_limited': False
                    })
                    
                    # 🔧 CRITICAL: Track pending BUY orders for confirmation mechanism (LIVE mode)
                    if action == "BUY":
                        pending_record = {
                            'order_id': order_id,
                            'symbol': symbol,
                            'timestamp': time.time(),
                            'quantity': quantity,
                            'price': price,
                            'status': 'PENDING'
                        }
                        self.pending_buy_orders[order_id] = pending_record
                        self.pending_buy_orders_by_symbol[symbol] = order_id
                        logger.debug(f"ORDER_TRACKING: Pending BUY tracked (LIVE) | {symbol} | order_id={order_id} | qty={quantity}")
                    
                    return order_id
                elif isinstance(response, dict) and response.get('status'):
                    # Old format with dict and status=True
                    order_id = response['data']['orderid']
                    logger.info(f"ORDER_PLACE: LIVE | {symbol} | {action} | qty={quantity} | order_id={order_id}")
                    print(f"✅ [LIVE] Options order placed: {order_id}")
                    log_broker_action("PLACE_ORDER", symbol, {
                        'action': action,
                        'quantity': quantity,
                        'price': price,
                        'order_id': order_id,
                        'order_type': order_type,
                        'rate_limited': False
                    })
                    
                    # 🔧 CRITICAL: Track pending BUY orders for confirmation mechanism (LIVE mode - dict response)
                    if action == "BUY":
                        pending_record = {
                            'order_id': order_id,
                            'symbol': symbol,
                            'timestamp': time.time(),
                            'quantity': quantity,
                            'price': price,
                            'status': 'PENDING'
                        }
                        self.pending_buy_orders[order_id] = pending_record
                        self.pending_buy_orders_by_symbol[symbol] = order_id
                        logger.debug(f"ORDER_TRACKING: Pending BUY tracked (LIVE dict) | {symbol} | order_id={order_id} | qty={quantity}")
                    
                    return order_id
                else:
                    error_msg = str(response) if response is not None else "No response from broker"
                    logger.error(f"ORDER_PLACE: LIVE FAILED | {symbol} | response={error_msg}")
                    print(f"❌ [LIVE] Order placement failed: {error_msg}")
                    return None
            except Exception as live_err:
                logger.error(f"ORDER_PLACE: LIVE ERROR | {symbol} | {str(live_err)}")
                print(f"❌ [LIVE] Error placing order: {str(live_err)}")
                return None
        
        except Exception as e:
            logger.error(f"ORDER_PLACE: ERROR | {symbol} | {str(e)}", symbol=symbol, action=action)
            rate_limiter.record_call("place_order", False)
            print(f"❌ Error placing options order: {str(e)}")
            return None
    
    def process_pending_rate_limited_requests(self):
        """
        Process any rate-limited requests that were queued for retry.
        Call this periodically to retry failed API calls.
        """
        rate_limiter = get_options_rate_limiter()
        
        queued_count = len(rate_limiter.request_queue.queue)
        if queued_count > 0:
            logger.info(f"RATE_LIMITER: Processing {queued_count} queued requests")
            print(f"🔄 Processing {queued_count} queued API requests...")
            rate_limiter.process_pending_requests()
            
            remaining = len(rate_limiter.request_queue.queue)
            if remaining == 0:
                logger.info(f"RATE_LIMITER: All queued requests processed successfully")
                print(f"✅ All queued requests processed successfully")
            else:
                logger.warning(f"RATE_LIMITER: {remaining} requests still queued after processing")
                print(f"⚠️ {remaining} requests still queued for next retry")
        
        return queued_count
    
    def get_rate_limiter_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        rate_limiter = get_options_rate_limiter()
        return rate_limiter.get_statistics()
    
    def close_option_position(self, symbol: str, quantity: int, price: float = 0) -> bool:
        """Close options position (reverse of entry) with rate limiting"""
        try:
            # Get rate limiter instance
            rate_limiter = get_options_rate_limiter()
            
            # Wait for rate limit permission
            if not rate_limiter.wait_for_call_permission(timeout=30.0, request_type="modify_order"):
                logger.warning(f"CLOSE_POSITION: RATE_LIMITED | {symbol} | queuing for retry")
                print(f"⚠️ Rate limited for closing {symbol} - queuing for retry...")
                
                # Create callback for retry
                def close_callback():
                    return self.close_option_position(symbol, quantity, price)
                
                # Queue the request
                rate_limiter.queue_request(
                    request_type=f"close_position_{symbol}",
                    callback=close_callback,
                    args=(),
                    kwargs={}
                )
                
                return False  # Will retry from queue
            
            # Record the API call
            rate_limiter.record_call("close_position", True)
            
            # Determine action: if we bought CE, we SELL CE to close
            # The order history will show the actual action
            response = self.place_options_order(
                symbol=symbol,
                action="SELL",
                quantity=quantity,
                price=price,
                order_type="MARKET" if price == 0 else "LIMIT"
            )
            return response is not None
        except Exception as e:
            rate_limiter = get_options_rate_limiter()
            rate_limiter.record_call("close_position", False)
            logger.error(f"CLOSE_POSITION: ERROR | {symbol} | {str(e)}")
            print(f"❌ Error closing options position: {str(e)}")
            return False
    
    def modify_order(self, 
                    order_id: str, 
                    symbol: str, 
                    new_price: float,
                    quantity: int = 0,
                    order_type: str = "STOPLOSS_MARKET") -> Optional[str]:
        """
        Modify an options order (change price)
        With rate limiting to prevent AngelOne API throttling
        
        Returns: order_id on success, None on failure
        """
        rate_limiter = get_options_rate_limiter()
        
        logger.debug(f"MODIFY_ORDER: {symbol} | order_id={order_id} | new_price={new_price:.2f}")
        
        if not self.authenticated and OptionsTradingConfig.TRADING_MODE != "PAPER":
            logger.error(f"MODIFY_ORDER: Not authenticated | {symbol}")
            return None
        
        try:
            # Wait for rate limit permission
            if not rate_limiter.wait_for_call_permission(timeout=30.0):
                logger.warning(f"MODIFY_ORDER: RATE_LIMITED | {symbol} | queuing for retry")
                
                # Create callback for retry
                def modify_callback():
                    return self.modify_order(order_id, symbol, new_price, quantity)
                
                rate_limiter.queue_request(
                    request_type=f"modify_order_{order_id}",
                    callback=modify_callback,
                    args=(),
                    kwargs={}
                )
                return f"QUEUED_{int(time.time())}_{order_id}"
            
            # Record the API call
            rate_limiter.record_call("modify_order", True)
            
            # PAPER mode: Just log
            if OptionsTradingConfig.TRADING_MODE == "PAPER":
                logger.info(f"MODIFY_ORDER: PAPER | {symbol} | {order_id} | price={new_price:.2f}")
                print(f"📝 [PAPER] Modify order: {order_id} @ ₹{new_price:.2f}")
                return order_id
            
            # LIVE mode: Call broker API
            try:
                # Get instrument token
                derivatives_exchange = self._get_underlying_derivatives_exchange(symbol)
                token = self.get_instrument_token(symbol, derivatives_exchange)
                if not token:
                    logger.error(f"MODIFY_ORDER: No token found | {symbol}")
                    return None
                
                # Prepare modify parameters
                # BUG FIX #3: AngelOne modifyOrder requires ALL original order fields
                # Missing these causes "missing required parameter" rejection
                # IMPORTANT: Price must be in multiples of 10 paise (₹0.10)
                modify_params = {
                    "variety": "STOPLOSS",          # Required: match original order's variety
                    "orderid": order_id,
                    "ordertype": order_type,          # Match the original SL order type
                    "tradingsymbol": symbol,         # Required: symbol identifier
                    "symboltoken": token,            # Required: instrument token
                    "exchange": derivatives_exchange,   # Required: exchange
                    "producttype": "INTRADAY",       # Required: product type
                    "duration": "DAY",               # Required: order validity
                    "price": str(new_price),
                    "triggerprice": str(new_price),  # Trigger price = SL price
                    "quantity": str(quantity) if quantity > 0 else "0"
                }
                
                # Call modify order API
                response = self.smart_api.modifyOrder(modify_params)
                
                # Handle response - AngelOne returns order ID as string on success
                if isinstance(response, str):
                    logger.info(f"MODIFY_ORDER: LIVE | {symbol} | order_id={response} | price={new_price:.2f}")
                    print(f"✅ [LIVE] Order modified: {response} @ ₹{new_price:.2f}")
                    log_broker_action("MODIFY_ORDER", symbol, {
                        'order_id': order_id,
                        'new_price': new_price,
                        'new_order_id': response
                    })
                    return response
                elif isinstance(response, dict) and response.get('status'):
                    # Old format with dict
                    new_order_id = response['data']['orderid']
                    logger.info(f"MODIFY_ORDER: LIVE | {symbol} | order_id={new_order_id} | price={new_price:.2f}")
                    print(f"✅ [LIVE] Order modified: {new_order_id} @ ₹{new_price:.2f}")
                    return new_order_id
                else:
                    error_msg = str(response) if response else "Unknown error"
                    logger.error(f"MODIFY_ORDER: LIVE FAILED | {symbol} | response={error_msg}")
                    print(f"❌ [LIVE] Order modification failed: {error_msg}")
                    return None
            except Exception as live_err:
                logger.error(f"MODIFY_ORDER: LIVE ERROR | {symbol} | {str(live_err)}")
                print(f"❌ [LIVE] Error modifying order: {str(live_err)}")
                return None
        
        except Exception as e:
            rate_limiter.record_call("modify_order", False)
            logger.error(f"MODIFY_ORDER: ERROR | {symbol} | {str(e)}")
            print(f"❌ Error modifying options order: {str(e)}")
            return None
    
    def cancel_order(self, order_id: str, symbol: str, order_type: str = "STOPLOSS_MARKET") -> bool:
        """
        Cancel an options order
        With rate limiting to prevent AngelOne API throttling
        
        Args:
            order_id: Broker order ID to cancel
            symbol: Option symbol (for logging)
            order_type: Original order type — CRITICAL for correct variety field.
                        Defaults to STOPLOSS_LIMIT (most common cancel scenario = canceling SL before exit)
        
        Returns: True on success, False on failure
        """
        rate_limiter = get_options_rate_limiter()
        
        logger.debug(f"CANCEL_ORDER: {symbol} | order_id={order_id}")
        
        # FIX: QUEUED_ means the SL order was never actually placed on the broker
        # (the place_options_order call was rate-limited and queued internally).
        # There is nothing to cancel on the broker side — just return True.
        if str(order_id).startswith("QUEUED_"):
            logger.info(f"CANCEL_ORDER: QUEUED_ORDER_SKIP | {symbol} | order_id={order_id} | no real broker order to cancel")
            return True
        
        if not self.authenticated and OptionsTradingConfig.TRADING_MODE != "PAPER":
            logger.error(f"CANCEL_ORDER: Not authenticated | {symbol}")
            return False
        
        try:
            # Wait for rate limit permission
            if not rate_limiter.wait_for_call_permission(timeout=30.0):
                # FIX: return False (not True) — the cancel has NOT happened yet.
                # Returning True here would let close_position() proceed with a market SELL
                # while the SL is still live on the broker, risking a double fill / short.
                logger.warning(f"CANCEL_ORDER: RATE_LIMITED | {symbol} | returning False to block premature SELL")
                return False
            
            # Record the API call
            rate_limiter.record_call("cancel_order", True)
            
            # PAPER mode: Just log
            if OptionsTradingConfig.TRADING_MODE == "PAPER":
                logger.info(f"CANCEL_ORDER: PAPER | {symbol} | {order_id}")
                print(f"📝 [PAPER] Cancel order: {order_id}")
                return True
            
            # LIVE mode: Call broker API
            try:
                # BUG FIX #4: variety must match the original order's variety
                # SL orders were placed with variety="STOPLOSS" — cancel must use same
                is_sl_order = order_type in ("STOPLOSS_LIMIT", "STOPLOSS_MARKET", "STOPLOSS-LIMIT", "STOPLOSS-MARKET")
                cancel_variety = "STOPLOSS" if is_sl_order else "NORMAL"

                try:
                    response = self.smart_api.cancelOrder(order_id, cancel_variety)
                except TypeError:
                    response = self.smart_api.cancelOrder({
                        "orderid": order_id,
                        "variety": cancel_variety
                    })
                
                # Handle response
                if isinstance(response, str):
                    # Success - response is the order ID
                    logger.info(f"CANCEL_ORDER: LIVE | {symbol} | order_id={response}")
                    print(f"✅ [LIVE] Order cancelled: {response}")
                    log_broker_action("CANCEL_ORDER", symbol, {
                        'order_id': order_id,
                        'cancelled_order_id': response
                    })
                    return True
                elif isinstance(response, dict) and response.get('status'):
                    # Old format with dict
                    logger.info(f"CANCEL_ORDER: LIVE | {symbol} | order_id={order_id}")
                    print(f"✅ [LIVE] Order cancelled: {order_id}")
                    return True
                else:
                    error_msg = str(response) if response else "Unknown error"
                    logger.error(f"CANCEL_ORDER: LIVE FAILED | {symbol} | response={error_msg}")
                    print(f"❌ [LIVE] Order cancellation failed: {error_msg}")
                    return False
            except Exception as live_err:
                logger.error(f"CANCEL_ORDER: LIVE ERROR | {symbol} | {str(live_err)}")
                print(f"❌ [LIVE] Error cancelling order: {str(live_err)}")
                return False
        
        except Exception as e:
            rate_limiter.record_call("cancel_order", False)
            logger.error(f"CANCEL_ORDER: ERROR | {symbol} | {str(e)}")
            print(f"❌ Error cancelling options order: {str(e)}")
            return False
    
    def wait_for_buy_confirmation(self, symbol: str, timeout: int = 30, order_id: Optional[str] = None) -> bool:
        """
        🔧 CRITICAL FIX: Wait for BUY order confirmation before proceeding to SL placement.
        
        ⚡ SAFETY FIRST - REVERTED TO STABLE TIMEOUT:
        - Orders MUST be confirmed and recorded before SL placement
        - 30 seconds = safe margin for broker API response + recording
        - Prevents losing trades due to premature timeout
        - Better to wait slightly longer than lose order execution records
        
        This prevents the race condition where:
        - BUY order is sent to broker
        - Before BUY is confirmed, monitoring starts checking for exits
        - SL is placed before BUY fills
        - Duplicate SELL orders might be placed
        
        Args:
            symbol: Options contract symbol (e.g., BANKNIFTY25DEC47000CE)
            timeout: Maximum seconds to wait for confirmation (default 30s, REVERTED from 5s)
        
        Returns:
            True if BUY confirmed and filled, False if timeout/failed/not found
        """
        if not hasattr(self, 'pending_buy_orders'):
            self.pending_buy_orders = {}
        if not hasattr(self, 'pending_buy_orders_by_symbol'):
            self.pending_buy_orders_by_symbol = {}

        resolved_order_id = str(order_id) if order_id else self.pending_buy_orders_by_symbol.get(symbol)
        if not resolved_order_id:
            logger.warning(f"BUY_CONFIRM: No pending BUY found | {symbol}")
            return False

        pending_order = self.pending_buy_orders.get(resolved_order_id)
        if not pending_order:
            logger.warning(f"BUY_CONFIRM: Pending BUY record missing | {symbol} | order_id={resolved_order_id}")
            return False

        tracked_symbol = pending_order.get('symbol', symbol)
        order_id = pending_order.get('order_id')
        quantity = pending_order.get('quantity')
        
        # PAPER MODE: Auto-confirm immediately (no broker confirmation needed)
        if OptionsTradingConfig.TRADING_MODE == "PAPER":
            logger.info(f"BUY_CONFIRM: PAPER MODE | {symbol} | order_id={order_id} | qty={quantity} | AUTO_CONFIRMED")
            pending_order['status'] = 'FILLED'
            self.pending_buy_orders.pop(order_id, None)
            if self.pending_buy_orders_by_symbol.get(tracked_symbol) == order_id:
                self.pending_buy_orders_by_symbol.pop(tracked_symbol, None)
            return True
        
        # LIVE MODE: Wait for broker confirmation
        logger.info(f"BUY_CONFIRM: Waiting for BUY confirmation | {symbol} | order_id={order_id} | qty={quantity} | timeout={timeout}s")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # Get current order book to check status
                order_book = self.get_order_book()
                if not order_book:
                    time.sleep(0.5)
                    continue
                
                # Find the BUY order in order book
                for order in order_book:
                    if order.get('orderid') == order_id or order.get('order_id') == order_id:
                        order_status = order.get('orderstatus', '').upper()
                        
                        # Check for filled status
                        if order_status in ['COMPLETE', 'FILLED', 'FULLY_FILLED']:
                            logger.info(f"BUY_CONFIRM: ORDER_FILLED | {symbol} | order_id={order_id} | status={order_status} | elapsed={time.time() - start_time:.1f}s")
                            
                            pending_order['status'] = 'FILLED'
                            self.pending_buy_orders.pop(order_id, None)
                            if self.pending_buy_orders_by_symbol.get(tracked_symbol) == order_id:
                                self.pending_buy_orders_by_symbol.pop(tracked_symbol, None)
                            
                            return True
                        
                        # Check for rejected status
                        elif order_status in ['REJECTED', 'CANCELLED', 'EXPIRED']:
                            logger.error(f"BUY_CONFIRM: ORDER_{order_status} | {symbol} | order_id={order_id}")
                            self.pending_buy_orders.pop(order_id, None)
                            if self.pending_buy_orders_by_symbol.get(tracked_symbol) == order_id:
                                self.pending_buy_orders_by_symbol.pop(tracked_symbol, None)
                            return False
                        
                        # Still pending - log and wait
                        logger.debug(f"BUY_CONFIRM: WAITING | {symbol} | status={order_status} | elapsed={time.time() - start_time:.1f}s")
                        break
                
                # Check every 0.5 seconds
                time.sleep(0.5)
            
            except Exception as e:
                logger.warning(f"BUY_CONFIRM: CHECK_ERROR | {symbol} | {str(e)}")
                time.sleep(0.5)
        
        # Timeout reached
        logger.error(f"BUY_CONFIRM: TIMEOUT | {symbol} | order_id={order_id} | waited {timeout}s")
        return False
    
    def get_instrument_token(self, symbol: str, exchange: str = "NFO") -> Optional[str]:
        """Get instrument token from CE extractor's instrument.json"""
        try:
            # Use CE extractor to lookup token
            instruments = self.ce_extractor.instruments

            # For NSE equity, always try {SYMBOL}-EQ first.
            # instrument.json contains BOTH a bare "COFORGE" (BSE, wrong token)
            # AND "COFORGE-EQ" (NSE, correct token).  Hitting the bare key for
            # an NSE request would return the BSE token → AB4006 error.
            if exchange == "NSE":
                eq_symbol = f"{symbol}-EQ"
                if eq_symbol in instruments:
                    return instruments[eq_symbol].get('token', '')
                # Fall through to bare symbol only if -EQ not present
                if symbol in instruments:
                    entry = instruments[symbol]
                    if entry.get('exch_seg', '') == 'NSE':
                        return entry.get('token', '')
                logger.warning(f"TOKEN_LOOKUP: Symbol not found | {symbol} | exchange={exchange}")
                return None

            if symbol in instruments:
                return instruments[symbol].get('token', '')

            logger.warning(f"TOKEN_LOOKUP: Symbol not found | {symbol} | exchange={exchange}")
            return None
        except Exception as e:
            logger.error(f"TOKEN_LOOKUP: ERROR | {symbol} | {str(e)}")
            return None
    
    def get_ltp(self, symbol: str, exchange: str = "NFO") -> Optional[float]:
        """
        Get Last Traded Price (LTP) for any symbol with rate limiting.
        Works for both options contracts and underlying stocks.
        Fetches LIVE data in both PAPER and LIVE modes.
        
        Args:
            symbol: Symbol name (e.g., BANKNIFTY25DEC47000CE or ASIANPAINT)
            exchange: NFO for options, NSE for stocks
        
        Returns:
            LTP or None if not available
        """
        # In PAPER mode, still fetch LIVE LTP from broker
        # Only order placement is simulated
        
        if not self.authenticated:
            logger.warning(f"LTP_FETCH: Not authenticated | {symbol}")
            return self._get_mock_ltp(symbol)  # Fallback to mock if not authenticated
        
        try:
            # Get rate limiter
            rate_limiter = get_options_rate_limiter()
            
            # Wait for rate limit permission
            if not rate_limiter.wait_for_call_permission(timeout=5.0):
                logger.warning(f"LTP_FETCH: RATE_LIMITED | {symbol}")
                return None
            
            # Get instrument token
            token = self.get_instrument_token(symbol, exchange)
            if not token:
                logger.warning(f"LTP_FETCH: No token found | {symbol}")
                return None
            
            # Fetch LTP from AngelOne with TIMEOUT
            rate_limiter.record_call("ltp_fetch", True)
            ltp_data = call_with_timeout(
                lambda: self.smart_api.ltpData(exchange, symbol, token),
                timeout_seconds=4.0
            )
            
            if ltp_data and ltp_data.get('status'):
                ltp = float(ltp_data['data']['ltp'])
                logger.debug(f"LTP_FETCH: SUCCESS | {symbol} | ltp=₹{ltp:.2f}")
                return ltp
            else:
                logger.warning(f"LTP_FETCH: FAILED | {symbol} | response={ltp_data}")
                rate_limiter.record_call("ltp_fetch", False)
                return None
            
        except Exception as e:
            logger.error(f"LTP_FETCH: ERROR | {symbol} | {str(e)}")
            rate_limiter = get_options_rate_limiter()
            rate_limiter.record_call("ltp_fetch", False)
            return None
    
    def _get_mock_ltp(self, symbol: str) -> float:
        """Generate mock LTP for paper trading"""
        # For options contracts, extract strike and calculate realistic premium
        parsed = OptionSymbolFormat.parse_symbol(symbol)
        if parsed:
            # Options contract
            strike = parsed['strike']
            contract_type = parsed['contract_type']
            underlying = parsed['underlying']
            
            # Get mock spot price
            spot = self.spot_prices.get(underlying, strike)
            
            # Calculate ITM/OTM offset
            if contract_type == 'CE':
                itm_offset = max(0, spot - strike)
            else:  # PE
                itm_offset = max(0, strike - spot)
            
            # Mock premium calculation
            intrinsic = max(0, itm_offset)
            time_value = 50 * (1 - min(abs(spot - strike) / spot, 0.5))
            premium = intrinsic + time_value
            
            return premium
        else:
            # Underlying stock/index - return configured spot price
            return self.spot_prices.get(symbol, 1000.0)
    
    def get_market_data(self, symbol: str, exchange: str = "NFO") -> Optional[Dict[str, Any]]:
        """
        Get comprehensive market data including LTP, IV, OI, volume, bid/ask.
        Fetches LIVE data from broker - never uses fallback/mock data.
        
        Args:
            symbol: Symbol name
            exchange: NFO for options, NSE for stocks
        
        Returns:
            Dict with market data or None if broker call fails
        """
        # CRITICAL: Always fetch LIVE data from broker
        # Paper trading = simulated order placement, NOT simulated prices
        
        if not self.authenticated:
            logger.warning(f"MARKET_DATA: Not authenticated | {symbol}")
            return None  # Return None instead of mock data
        
        try:
            # Get rate limiter
            rate_limiter = get_options_rate_limiter()
            
            # Wait for rate limit permission
            if not rate_limiter.wait_for_call_permission(timeout=5.0):
                logger.warning(f"MARKET_DATA: RATE_LIMITED | {symbol}")
                return None
            
            # Get instrument token
            token = self.get_instrument_token(symbol, exchange)
            if not token:
                logger.warning(f"MARKET_DATA: No token found | {symbol}")
                return None
            
            # Fetch market data from AngelOne (using getMarketData or similar API)
            rate_limiter.record_call("market_data", True)
            
            # Note: SmartAPI doesn't have direct getMarketData, use ltpData as fallback
            ltp_data = self.smart_api.ltpData(exchange, symbol, token)
            
            if ltp_data and ltp_data.get('status'):
                data = ltp_data['data']
                market_data = {
                    'ltp': float(data.get('ltp', 0)),
                    'open': float(data.get('open', 0)),
                    'high': float(data.get('high', 0)),
                    'low': float(data.get('low', 0)),
                    'close': float(data.get('close', 0)),
                    'volume': int(data.get('volume', 0)),
                    'timestamp': datetime.now().isoformat()
                }
                logger.debug(f"MARKET_DATA: SUCCESS | {symbol} | ltp=₹{market_data['ltp']:.2f}")
                return market_data
            else:
                logger.warning(f"MARKET_DATA: FAILED | {symbol}")
                rate_limiter.record_call("market_data", False)
                return None
            
        except Exception as e:
            logger.error(f"MARKET_DATA: ERROR | {symbol} | {str(e)}")
            rate_limiter = get_options_rate_limiter()
            rate_limiter.record_call("market_data", False)
            return None

    def _extract_oi_from_quote_response(self, market_data: Dict[str, Any]) -> int:
        """Extract OI from SmartAPI FULL quote response."""
        fetched = market_data.get('data', {}).get('fetched', []) if market_data else []
        if not fetched:
            return 0

        item_data = fetched[0] or {}
        oi_value = (
            item_data.get('openInterest')
            or item_data.get('opnInterest')
            or item_data.get('oi')
            or 0
        )

        try:
            return int(float(oi_value))
        except (TypeError, ValueError):
            return 0
    
    def get_oi_data(self, symbols: List[str], exchange: str = "NFO") -> Dict[str, Optional[int]]:
        """
        Fetch Open Interest data for multiple symbols using getMarketData.
        
        Args:
            symbols: List of option symbols
            exchange: NFO for options
        
        Returns:
            {symbol: oi_value, ...}
        """
        oi_map = {}
        
        try:
            rate_limiter = get_options_rate_limiter()
            
            for symbol in symbols:
                try:
                    # Get token
                    token = self.get_instrument_token(symbol, exchange)
                    if not token:
                        logger.warning(f"OI_FETCH: No token | {symbol}")
                        oi_map[symbol] = 100000  # Mock default OI for liquid contracts
                        continue
                    
                    # Wait for rate limit
                    if not rate_limiter.wait_for_call_permission(timeout=5.0):
                        logger.warning(f"OI_FETCH: RATE_LIMITED | {symbol}")
                        oi_map[symbol] = 100000  # Use default on rate limit
                        continue
                    
                    # Try getMarketData for full quote with OI
                    try:
                        market_data = self.smart_api.getMarketData("FULL", {exchange: [token]})
                        rate_limiter.record_call("oi_fetch", True)
                        
                        if market_data and market_data.get('status'):
                            oi = self._extract_oi_from_quote_response(market_data)
                            oi_map[symbol] = max(oi, 50000)  # Min 50K for validity
                            logger.debug(f"OI_FETCH: SUCCESS | {symbol} | oi={oi}")
                        else:
                            logger.warning(f"OI_FETCH: NO DATA | {symbol}")
                            oi_map[symbol] = 100000
                    except Exception as e:
                        # If getMarketData fails, use sensible default based on liquidity
                        logger.debug(f"OI_FETCH: Fallback for {symbol} | {str(e)}")
                        oi_map[symbol] = 100000  # Default liquid OI
                        rate_limiter.record_call("oi_fetch", False)
                
                except Exception as symbol_err:
                    logger.warning(f"OI_FETCH: ERROR for {symbol} | {str(symbol_err)}")
                    oi_map[symbol] = 100000  # Default fallback
            
            return oi_map
        
        except Exception as e:
            logger.error(f"OI_FETCH: BATCH ERROR | {str(e)}")
            # Return reasonable defaults for all symbols
            return {symbol: 100000 for symbol in symbols}
    
    def get_ltp_bulk(self, symbols: List[str], exchange: str = "NFO") -> Dict[str, Optional[float]]:
        """
        Get LTP for multiple option symbols with intelligent caching and smart rate limiting.
        
        OPTIMIZATION: 
        1. Check LTP cache first (60s TTL to dramatically reduce API calls)
        2. For uncached symbols: use getMarketData() in small batches (respects SmartAPI limits)
        3. With 60s cache, uncached symbols per cycle = ~10% of positions
        4. For 56 positions: ~5-6 uncached per cycle = can batch them efficiently
        
        Args:
            symbols: List of option symbols (e.g., ["BANKNIFTY25JAN19800CE", "NIFTY25JAN18000CE"])
            exchange: NFO for options
        
        Returns:
            Dictionary mapping symbol -> LTP value (None if not available)
        
        Example:
            ltps = broker.get_ltp_bulk(["BANKNIFTY25JAN19800CE", "NIFTY25JAN18000CE"])
            # Returns: {"BANKNIFTY25JAN19800CE": 250.5, "NIFTY25JAN18000CE": 180.3}
        
        Rate Limit Strategy:
        - Cached hits: 0 API calls (60s TTL means ~90% cache hit rate during monitoring)
        - Uncached symbols: getMarketData() with small batches (2 tokens/call)
        - With proper caching, monitoring cycle uses ~3 API calls for 56 positions (vs 56 without cache)
        - At 10s monitoring interval: ~3 calls × 6 cycles/min = ~18 API calls/min (vs 336 without cache)
        """
        if not symbols:
            return {}
        
        # CRITICAL: Always fetch LIVE data from broker, never use mock/fallback
        # Paper trading means simulated order placement, NOT simulated prices
        # Users need real market data to make real trading decisions
        logger.info(f"BULK_MARKET_DATA: STARTING | symbols={len(symbols)} | authenticated={self.authenticated}")
        
        if not self.authenticated:
            logger.warning(f"BULK_MARKET_DATA: Not authenticated, attempting re-authentication before fetching {len(symbols)} symbols")
            # Try to re-authenticate if session lost
            success = self.authenticate(is_retry=True)
            if not success:
                logger.error(f"BULK_MARKET_DATA: Re-authentication FAILED - cannot fetch live data for {len(symbols)} symbols")
                return {sym: None for sym in symbols}
            logger.info(f"BULK_MARKET_DATA: Re-authentication successful, retrying LTP fetch")
        
        result = {sym: None for sym in symbols}
        
        try:
            # Get rate limiter
            rate_limiter = get_options_rate_limiter()
            
            # OPTIMIZATION: Batch into two groups: cached vs uncached
            # This way we do zero API calls if all are cached
            cached_symbols = []
            uncached_symbols = []
            
            # PHASE 1: Fetch all symbols live (no caching)
            # With batching (50 tokens per request), even 100 positions = 2 API calls
            # Cost: ~20-30 requests/minute with 2-3s monitoring = well within 180 RPM limit
            # Benefit: Live Greeks and real-time price updates for decision making
            uncached_symbols = symbols  # Fetch all live
            
            logger.info(f"BULK_MARKET_DATA: Fetching all {len(symbols)} symbols LIVE (no cache)")
            
            # PHASE 2: Fetch uncached symbols using getMarketData() with small batches
            # Use 2 tokens per batch to avoid overwhelming the API
            # This balances efficiency with API stability
            fetched_count = 0
            failed_symbols = []
            
            if uncached_symbols:
                # Use SmartAPI /quote bulk endpoint (50 tokens per request, 1 RPS)
                logger.info(f"BULK_MARKET_DATA: Fetching {len(uncached_symbols)} symbols using /quote bulk endpoint")
                
                # Build symbol -> token mapping
                symbol_to_token = {}
                for symbol in uncached_symbols:
                    token = self.get_instrument_token(symbol, exchange)
                    if token:
                        symbol_to_token[symbol] = str(token)
                    else:
                        logger.debug(f"BULK_MARKET_DATA: No token | {symbol}")
                        failed_symbols.append(symbol)
                
                if symbol_to_token:
                    # Batch tokens into groups of 50
                    batch_size = 50
                    symbols_list = list(symbol_to_token.keys())
                    
                    for batch_idx in range(0, len(symbols_list), batch_size):
                        batch_symbols = symbols_list[batch_idx:batch_idx + batch_size]
                        batch_tokens = [symbol_to_token[sym] for sym in batch_symbols]
                        
                        # Wait for rate limiter (1 RPS for /quote endpoint)
                        if not rate_limiter.wait_for_call_permission(timeout=3.0, request_type="bulk_ltp_quote"):
                            logger.warning(f"BULK_MARKET_DATA: Rate limited on batch {batch_idx//batch_size}")
                            failed_symbols.extend(batch_symbols)
                            continue
                        
                        try:
                            # Build request
                            import requests
                            request_data = {
                                "mode": "LTP",
                                "exchangeTokens": {exchange.upper(): batch_tokens}
                            }
                            
                            headers = {
                                'Authorization': self.session_token,  # Already has "Bearer " prefix
                                'Content-Type': 'application/json',
                                'Accept': 'application/json',
                                'X-UserType': 'USER',
                                'X-SourceID': 'WEB',
                                'X-ClientLocalIP': getattr(self.smart_api, 'localIP', '127.0.0.1'),
                                'X-ClientPublicIP': getattr(self.smart_api, 'publicIP', ''),
                                'X-MACAddress': getattr(self.smart_api, 'macAddress', ''),
                                'X-PrivateKey': self.api_key
                            }
                            
                            response = requests.post(
                                'https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/',
                                json=request_data,
                                headers=headers,
                                timeout=5.0
                            )
                            
                            rate_limiter.record_call("bulk_ltp_quote", True)
                            
                            if response.status_code == 200:
                                data = response.json()
                                # Response format: {"status": true, "message": "SUCCESS", "data": {"fetched": [...]}}}
                                if data.get('status') or data.get('success'):
                                    fetched = data.get('data', {}).get('fetched', [])
                                    for item in fetched:
                                        token = item.get('symbolToken')
                                        ltp = float(item.get('ltp', 0))
                                        # Find symbol by token
                                        for sym, tok in symbol_to_token.items():
                                            if tok == token and ltp > 0:
                                                result[sym] = ltp
                                                self.ltp_cache.set(sym, ltp)
                                                fetched_count += 1
                                                break
                                    logger.debug(f"BULK_MARKET_DATA: Batch {batch_idx//batch_size} | fetched={len(fetched)}/{len(batch_tokens)}")
                                else:
                                    error_msg = data.get('message', 'Unknown error')
                                    error_code = data.get('errorcode', '')
                                    # Check for Invalid Token error
                                    if 'Invalid Token' in error_msg or error_code == 'AG8001':
                                        logger.error(f"BULK_MARKET_DATA: INVALID_TOKEN detected | {error_msg} | triggering re-authentication and retry")
                                        auth_success = self._handle_invalid_token_error()  # Re-authenticate
                                        if auth_success:
                                            # Auth succeeded - retry this batch
                                            logger.info(f"BULK_MARKET_DATA: Re-authentication successful - RETRYING batch {batch_idx//batch_size}")
                                            try:
                                                # Update auth header with new session token
                                                headers['Authorization'] = self.session_token
                                                
                                                # Retry the request with new token
                                                response_retry = requests.post(
                                                    'https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/',
                                                    json=request_data,
                                                    headers=headers,
                                                    timeout=5.0
                                                )
                                                
                                                if response_retry.status_code == 200:
                                                    data_retry = response_retry.json()
                                                    if data_retry.get('status') or data_retry.get('success'):
                                                        fetched_retry = data_retry.get('data', {}).get('fetched', [])
                                                        for item in fetched_retry:
                                                            token = item.get('symbolToken')
                                                            ltp = float(item.get('ltp', 0))
                                                            for sym, tok in symbol_to_token.items():
                                                                if tok == token and ltp > 0:
                                                                    result[sym] = ltp
                                                                    self.ltp_cache.set(sym, ltp)
                                                                    fetched_count += 1
                                                                    if sym in failed_symbols:
                                                                        failed_symbols.remove(sym)  # Mark as recovered
                                                                    break
                                                        logger.info(f"BULK_MARKET_DATA: RETRY batch {batch_idx//batch_size} SUCCESS | fetched={len(fetched_retry)}/{len(batch_tokens)}")
                                                    else:
                                                        logger.warning(f"BULK_MARKET_DATA: RETRY failed with error | {data_retry.get('message')}")
                                                        failed_symbols.extend([s for s in batch_symbols if s not in result or result[s] is None])
                                                else:
                                                    logger.warning(f"BULK_MARKET_DATA: RETRY HTTP {response_retry.status_code}")
                                                    failed_symbols.extend([s for s in batch_symbols if s not in result or result[s] is None])
                                            except Exception as retry_error:
                                                logger.warning(f"BULK_MARKET_DATA: RETRY exception | {type(retry_error).__name__}: {str(retry_error)}")
                                                failed_symbols.extend([s for s in batch_symbols if s not in result or result[s] is None])
                                        else:
                                            logger.error(f"BULK_MARKET_DATA: Re-authentication FAILED - cannot retry batch")
                                            failed_symbols.extend(batch_symbols)
                                    logger.warning(f"BULK_MARKET_DATA: API error | {error_msg} | {error_code}")
                                    if error_code != 'AG8001':  # Don't double-log for Invalid Token
                                        failed_symbols.extend(batch_symbols)
                                    rate_limiter.record_call("bulk_ltp_quote", False)
                            else:
                                response_text = response.text[:100] if response.text else "No response"
                                logger.warning(f"BULK_MARKET_DATA: HTTP {response.status_code} | {response_text}")
                                # Check if response contains Invalid Token error
                                if 'Invalid Token' in response.text or response.status_code == 401:
                                    logger.error(f"BULK_MARKET_DATA: INVALID_TOKEN (HTTP {response.status_code}) | triggering re-authentication and retry")
                                    auth_success = self._handle_invalid_token_error()  # Re-authenticate
                                    if auth_success:
                                        # Auth succeeded - retry this batch
                                        logger.info(f"BULK_MARKET_DATA: Re-authentication successful - RETRYING batch {batch_idx//batch_size}")
                                        try:
                                            # Update auth header with new session token
                                            headers['Authorization'] = self.session_token
                                            
                                            # Retry the request with new token
                                            response_retry = requests.post(
                                                'https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/',
                                                json=request_data,
                                                headers=headers,
                                                timeout=5.0
                                            )
                                            
                                            if response_retry.status_code == 200:
                                                data_retry = response_retry.json()
                                                if data_retry.get('status') or data_retry.get('success'):
                                                    fetched_retry = data_retry.get('data', {}).get('fetched', [])
                                                    for item in fetched_retry:
                                                        token = item.get('symbolToken')
                                                        ltp = float(item.get('ltp', 0))
                                                        for sym, tok in symbol_to_token.items():
                                                            if tok == token and ltp > 0:
                                                                result[sym] = ltp
                                                                self.ltp_cache.set(sym, ltp)
                                                                fetched_count += 1
                                                                if sym in failed_symbols:
                                                                    failed_symbols.remove(sym)
                                                                break
                                                    logger.info(f"BULK_MARKET_DATA: RETRY batch {batch_idx//batch_size} SUCCESS | fetched={len(fetched_retry)}/{len(batch_tokens)}")
                                                else:
                                                    logger.warning(f"BULK_MARKET_DATA: RETRY failed with error | {data_retry.get('message')}")
                                                    failed_symbols.extend([s for s in batch_symbols if s not in result or result[s] is None])
                                            else:
                                                logger.warning(f"BULK_MARKET_DATA: RETRY HTTP {response_retry.status_code}")
                                                failed_symbols.extend([s for s in batch_symbols if s not in result or result[s] is None])
                                        except Exception as retry_error:
                                            logger.warning(f"BULK_MARKET_DATA: RETRY exception | {type(retry_error).__name__}: {str(retry_error)}")
                                            failed_symbols.extend([s for s in batch_symbols if s not in result or result[s] is None])
                                    else:
                                        logger.error(f"BULK_MARKET_DATA: Re-authentication FAILED - cannot retry batch")
                                        failed_symbols.extend(batch_symbols)
                                else:
                                    failed_symbols.extend(batch_symbols)
                                rate_limiter.record_call("bulk_ltp_quote", False)
                                
                        except Exception as e:
                            logger.warning(f"BULK_MARKET_DATA: Exception | {type(e).__name__}: {str(e)}")
                            failed_symbols.extend(batch_symbols)
                            rate_limiter.record_call("bulk_ltp_quote", False)
            
            # Log final statistics
            api_calls = (len(symbols) + 49) // 50  # Ceiling division for batch count
            logger.info(f"BULK_MARKET_DATA: Complete | fetched={fetched_count}/{len(symbols)} symbols | "
                       f"failed={len(failed_symbols)} | api_calls={api_calls}")
            
            log_event("BULK_MARKET_DATA", 
                     f"Fetched LIVE LTP (no cache) using SmartAPI bulk /quote with batching",
                     total_symbols=len(symbols),
                     api_calls_made=api_calls,
                     success=fetched_count,
                     failed=len(failed_symbols),
                     efficiency=f"{(fetched_count/len(symbols)*100):.1f}% success rate")
            
            if failed_symbols:
                logger.warning(f"BULK_MARKET_DATA: Failed for {len(failed_symbols)} symbols")
            
            return result
        
        except Exception as e:
            logger.error(f"BULK_MARKET_DATA: CRITICAL ERROR | {str(e)}")
            return result
    
    def _get_mock_market_data(self, symbol: str) -> Dict[str, Any]:
        """Generate mock market data for paper trading"""
        ltp = self._get_mock_ltp(symbol)
        
        return {
            'ltp': ltp,
            'open': ltp * 0.99,
            'high': ltp * 1.02,
            'low': ltp * 0.98,
            'close': ltp,
            'volume': 100000,
            'oi': 50000,  # Open Interest for options
            'bid': ltp * 0.995,
            'ask': ltp * 1.005,
            'iv': 0.25,  # Dynamic IV (default 25%, will be overridden by volatility calculator)
            'timestamp': datetime.now().isoformat()
        }
    
    def calculate_technical_indicators(self, symbol: str, exchange: str = "NSE", 
                                      period_rsi: int = 14, period_atr: int = 14) -> Optional[Dict[str, float]]:
        """
        Calculate RSI and ATR for underlying symbol.
        
        Args:
            symbol: Underlying symbol (e.g., 'BANKNIFTY', 'NIFTY')
            exchange: Exchange (NSE or NFO)
            period_rsi: RSI period (default: 14)
            period_atr: ATR period (default: 14)
        
        Returns:
            Dict with technical indicators or None
        """
        try:
            # Get historical data for underlying
            resolved_exchange = exchange or self._get_underlying_cash_exchange(symbol)
            if exchange == "NSE":
                resolved_exchange = self._get_underlying_cash_exchange(symbol)
            historical_data = self.get_historical_data(symbol, interval="FIVE_MINUTE", days_back=2, exchange=resolved_exchange)
            
            if not historical_data or len(historical_data) < max(period_rsi, period_atr) + 1:
                logger.warning(f"INDICATORS: Insufficient data for {symbol}")
                return self._get_mock_indicators()
            
            # Extract OHLCV data
            closes = [candle['close'] for candle in historical_data]
            highs = [candle['high'] for candle in historical_data]
            lows = [candle['low'] for candle in historical_data]
            volumes = [candle['volume'] for candle in historical_data]
            
            indicators = {}
            
            # RSI (Relative Strength Index)
            if len(closes) >= period_rsi + 1:
                indicators['rsi'] = self._calculate_rsi(closes, period_rsi)
                indicators['rsi_overbought'] = indicators['rsi'] > 70
                indicators['rsi_oversold'] = indicators['rsi'] < 30
            
            # ATR (Average True Range)
            if len(historical_data) >= period_atr + 1:
                indicators['atr'] = self._calculate_atr(highs, lows, closes, period_atr)
            
            # SMA (Simple Moving Averages)
            if len(closes) >= 20:
                indicators['sma_20'] = sum(closes[-20:]) / 20
            if len(closes) >= 50:
                indicators['sma_50'] = sum(closes[-50:]) / 50
            
            # Current price
            indicators['current_price'] = closes[-1]
            current_price = closes[-1]
            
            # Price vs SMA
            if 'sma_20' in indicators:
                indicators['price_vs_sma20'] = ((current_price - indicators['sma_20']) / indicators['sma_20']) * 100
            if 'sma_50' in indicators:
                indicators['price_vs_sma50'] = ((current_price - indicators['sma_50']) / indicators['sma_50']) * 100
            
            # ADX (Average Directional Index)
            if len(historical_data) >= 14:
                indicators['adx'] = self._calculate_adx(highs, lows, closes, 14)
            
            # Bollinger Bands
            if len(closes) >= 20:
                bb_mid, bb_upper, bb_lower = self._calculate_bollinger_bands(closes, 20, 2)
                indicators['bb_middle'] = bb_mid
                indicators['bb_upper'] = bb_upper
                indicators['bb_lower'] = bb_lower
            
            indicators['calculated_at'] = datetime.now().isoformat()
            indicators['data_points'] = len(historical_data)
            
            logger.info(f"INDICATORS: Calculated {len(indicators)} indicators for {symbol}")
            return indicators
            
        except Exception as e:
            logger.error(f"INDICATORS: ERROR calculating for {symbol} | {str(e)}")
            return self._get_mock_indicators()
    
    def get_underlying_technicals(self, underlying: str) -> Dict[str, Any]:
        """
        Get comprehensive technical analysis for underlying symbol.
        
        Args:
            underlying: Underlying symbol (BANKNIFTY, NIFTY, FINNIFTY)
            
        Returns:
            Dict with technical indicators and signals
        """
        try:
            # Get indicators
            indicators = self.calculate_technical_indicators(underlying)
            if not indicators:
                return {}
            
            # Generate trading signals
            signals = {}
            
            # RSI signals
            if 'rsi' in indicators:
                rsi = indicators['rsi']
                if rsi > 70:
                    signals['rsi_signal'] = 'OVERBOUGHT'
                elif rsi < 30:
                    signals['rsi_signal'] = 'OVERSOLD'
                else:
                    signals['rsi_signal'] = 'NEUTRAL'
            
            # Price vs MA signals
            if 'price_vs_sma20' in indicators:
                pct = indicators['price_vs_sma20']
                if pct > 2:
                    signals['sma20_signal'] = 'ABOVE'
                elif pct < -2:
                    signals['sma20_signal'] = 'BELOW'
                else:
                    signals['sma20_signal'] = 'NEUTRAL'
            
            # ADX trend strength
            if 'adx' in indicators:
                adx = indicators['adx']
                if adx > 25:
                    signals['trend_strength'] = 'STRONG'
                else:
                    signals['trend_strength'] = 'WEAK'
            
            return {
                'underlying': underlying,
                'indicators': indicators,
                'signals': signals,
                'timestamp': datetime.now().isoformat()
            }
        
        except Exception as e:
            logger.error(f"UNDERLYING_TECHNICALS: ERROR for {underlying} | {str(e)}")
            return {}
    
    def get_historical_data(self, symbol: str, interval: str = "FIVE_MINUTE", 
                           days_back: int = 2, exchange: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """
        Get historical candlestick data for a symbol.
        
        Args:
            symbol: Symbol name (e.g., 'BANKNIFTY', 'NIFTY')
            interval: Time interval ('ONE_MINUTE', 'FIVE_MINUTE', 'FIFTEEN_MINUTE', 'ONE_HOUR', 'ONE_DAY')
            days_back: Number of days of historical data to fetch
            
        Returns:
            List of OHLC candles or None if failed
        """
        try:
            # Paper trading mode
            if DevConfig.PAPER_TRADING_ENABLED:
                return self._get_mock_historical_data(symbol, days_back)
            
            if not self.authenticated:
                logger.warning(f"HISTORICAL: Not authenticated for {symbol}")
                return self._get_mock_historical_data(symbol, days_back)
            
            resolved_exchange = exchange or self._get_underlying_cash_exchange(symbol)
            token = self.get_instrument_token(symbol, exchange=resolved_exchange)
            if not token:
                logger.warning(f"HISTORICAL: Token not found for {symbol}")
                return None
            
            # Calculate date range
            from datetime import datetime as dt, timedelta
            end_date = dt.now()
            start_date = end_date - timedelta(days=days_back)
            
            from_date = start_date.strftime("%Y-%m-%d 09:15")
            to_date = end_date.strftime("%Y-%m-%d 15:30")
            
            # Fetch historical data
            rate_limiter = get_options_rate_limiter()
            if not rate_limiter.wait_for_call_permission(timeout=5.0):
                logger.warning(f"HISTORICAL: RATE_LIMITED for {symbol}")
                return self._get_mock_historical_data(symbol, days_back)
            
            historic_params = {
                "exchange": resolved_exchange,
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_date,
                "todate": to_date
            }
            
            rate_limiter.record_call("historical_data", True)
            response = self.smart_api.getCandleData(historic_params)
            
            if not response or not response.get('status'):
                logger.warning(f"HISTORICAL: API failed for {symbol}")
                rate_limiter.record_call("historical_data", False)
                return self._get_mock_historical_data(symbol, days_back)
            
            # Parse response
            candle_data = response.get('data', [])
            if not candle_data:
                logger.warning(f"HISTORICAL: No data returned for {symbol}")
                return None
            
            # Format candles
            formatted_data = []
            for candle in candle_data:
                try:
                    # Format: [timestamp, open, high, low, close, volume]
                    formatted_data.append({
                        'timestamp': candle[0],
                        'open': float(candle[1]),
                        'high': float(candle[2]),
                        'low': float(candle[3]),
                        'close': float(candle[4]),
                        'volume': int(candle[5]) if len(candle) > 5 else 0
                    })
                except (IndexError, ValueError) as e:
                    logger.debug(f"HISTORICAL: Error parsing candle for {symbol}: {e}")
                    continue
            
            logger.info(f"HISTORICAL: Fetched {len(formatted_data)} candles for {symbol}")
            return formatted_data
        
        except Exception as e:
            logger.error(f"HISTORICAL: ERROR for {symbol} | {str(e)}")
            return self._get_mock_historical_data(symbol, days_back)
    
    def _get_mock_historical_data(self, symbol: str, days_back: int = 2) -> List[Dict[str, Any]]:
        """Generate mock historical data for paper trading"""
        from datetime import datetime as dt, timedelta
        
        candles = []
        base_price = self._get_mock_ltp(symbol)
        current_time = dt.now()
        
        # Generate 5-minute candles for the specified days
        num_candles = days_back * 77  # ~77 candles per day (9:15 to 15:30)
        
        for i in range(num_candles):
            time_offset = timedelta(minutes=-5 * (num_candles - i))
            candle_time = current_time + time_offset
            
            # Simulate price movement
            import random
            price_change = (random.random() - 0.5) * base_price * 0.01  # ±0.5% per candle
            open_price = base_price + price_change * random.random()
            close_price = open_price + price_change
            high_price = max(open_price, close_price) * (1 + abs(random.random() * 0.002))
            low_price = min(open_price, close_price) * (1 - abs(random.random() * 0.002))
            volume = int(100000 + random.random() * 50000)
            
            candles.append({
                'timestamp': candle_time.strftime("%Y-%m-%d %H:%M:%S"),
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': volume
            })
            
            base_price = close_price
        
        return candles
    
    def _get_mock_indicators(self) -> Dict[str, float]:
        """Return mock technical indicators"""
        return {
            'rsi': 50.0,
            'atr': 50.0,
            'sma_20': 18000.0,
            'sma_50': 17800.0,
            'adx': 20.0,
            'calculated_at': datetime.now().isoformat()
        }
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate RSI (Relative Strength Index)"""
        if len(prices) < period + 1:
            return 50.0
        
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [delta if delta > 0 else 0 for delta in deltas]
        losses = [-delta if delta < 0 else 0 for delta in deltas]
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)
    
    def _calculate_atr(self, highs: List[float], lows: List[float], 
                      closes: List[float], period: int = 14) -> float:
        """Calculate ATR (Average True Range)"""
        if len(highs) < period + 1:
            return 0.0
        
        true_ranges = []
        for i in range(1, len(highs)):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i-1])
            low_close = abs(lows[i] - closes[i-1])
            true_range = max(high_low, high_close, low_close)
            true_ranges.append(true_range)
        
        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
        
        return round(sum(true_ranges[-period:]) / period, 2)
    
    def _calculate_adx(self, highs: List[float], lows: List[float], 
                      closes: List[float], period: int = 14) -> float:
        """Calculate ADX (Average Directional Index)"""
        if len(highs) < period + 1:
            return 0.0
        
        try:
            # Calculate +DM, -DM, TR
            plus_dm = []
            minus_dm = []
            tr = []
            
            for i in range(1, len(highs)):
                high_diff = highs[i] - highs[i-1]
                low_diff = lows[i-1] - lows[i]
                
                if high_diff > low_diff and high_diff > 0:
                    plus_dm.append(high_diff)
                else:
                    plus_dm.append(0)
                
                if low_diff > high_diff and low_diff > 0:
                    minus_dm.append(low_diff)
                else:
                    minus_dm.append(0)
                
                tr_val = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i] - closes[i-1])
                )
                tr.append(tr_val)
            
            # Calculate DI+ and DI-
            atr = sum(tr[-period:]) / period if len(tr) >= period else sum(tr) / len(tr)
            
            di_plus = (sum(plus_dm[-period:]) / period) / atr * 100 if atr > 0 else 0
            di_minus = (sum(minus_dm[-period:]) / period) / atr * 100 if atr > 0 else 0
            
            # Calculate DX
            di_sum = di_plus + di_minus
            dx = (abs(di_plus - di_minus) / di_sum * 100) if di_sum > 0 else 0
            
            # ADX is smoothed DX
            adx = sum([dx] * period) / period  # Simplified for quick calculation
            return round(adx, 2)
        
        except Exception as e:
            logger.debug(f"ADX calculation error: {str(e)}")
            return 0.0
    
    def _calculate_bollinger_bands(self, prices: List[float], period: int = 20, 
                                  std_dev: int = 2) -> Tuple[float, float, float]:
        """Calculate Bollinger Bands"""
        if len(prices) < period:
            avg = sum(prices) / len(prices)
            return avg, avg, avg
        
        # Middle band (SMA)
        sma = sum(prices[-period:]) / period
        
        # Standard deviation
        variance = sum((price - sma) ** 2 for price in prices[-period:]) / period
        std = variance ** 0.5
        
        # Bands
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        
        return round(sma, 2), round(upper, 2), round(lower, 2)
    
    # =============================================================================
    # Live Position Verification from Broker
    # =============================================================================
    
    def get_order_book(self) -> Optional[List[Dict[str, Any]]]:
        """
        Get Order Book from Angel One broker.
        
        Angel One API: GET /rest/secure/angelbroking/order/v1/getOrderBook
        
        Returns list of all orders (pending, executed, rejected) with their status.
        CRITICAL for squareoff to verify which positions are actually open.
        
        Returns:
            List of orders or None if failed
        """
        try:
            if not self.authenticated:
                logger.warning("ORDER_BOOK: Not authenticated")
                return None
            
            if not self.smart_api:
                logger.warning("ORDER_BOOK: SmartAPI not initialized")
                return None
            
            # Get rate limiter
            rate_limiter = get_options_rate_limiter()
            
            # Wait for rate limit permission
            if not rate_limiter.wait_for_call_permission(timeout=5.0):
                logger.warning("ORDER_BOOK: RATE_LIMITED")
                return None
            
            # Call getOrderBook API
            rate_limiter.record_call("get_order_book", True)
            logger.info("ORDER_BOOK: Fetching from broker...")
            
            order_book_method = getattr(self.smart_api, "getOrderBook", None) or getattr(self.smart_api, "orderBook", None)
            if not order_book_method:
                logger.error("ORDER_BOOK: SmartAPI order book method unavailable")
                rate_limiter.record_call("get_order_book", False)
                return None

            response = order_book_method()
            
            if response and response.get('status'):
                orders = response.get('data', [])
                logger.info(f"ORDER_BOOK: Retrieved {len(orders)} orders from broker")
                
                # Log for debugging
                for order in orders:
                    if order.get('orderstate') == 'COMPLETE' or order.get('orderstatus') == 'COMPLETE':
                        logger.debug(f"ORDER_BOOK: COMPLETE | {order.get('tradingsymbol')} | "
                                   f"qty={order.get('quantity')} | executed={order.get('filledshares')}")
                
                return orders
            else:
                logger.warning(f"ORDER_BOOK: API failed | response={response}")
                rate_limiter.record_call("get_order_book", False)
                return None
        
        except Exception as e:
            logger.error(f"ORDER_BOOK: ERROR | {str(e)}")
            rate_limiter = get_options_rate_limiter()
            rate_limiter.record_call("get_order_book", False)
            return None

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Lookup a single AngelOne order by id and normalize its status fields."""
        try:
            order_book = self.get_order_book()
            if not order_book:
                return None

            for order in order_book:
                current_order_id = str(order.get('orderid') or order.get('order_id') or '')
                if current_order_id != str(order_id):
                    continue

                status = str(order.get('orderstatus') or order.get('orderstate') or order.get('status') or '').upper()

                def _as_float(*keys: str) -> float:
                    for key in keys:
                        value = order.get(key)
                        if value in (None, ''):
                            continue
                        try:
                            return float(value)
                        except (TypeError, ValueError):
                            continue
                    return 0.0

                def _as_int(*keys: str) -> int:
                    for key in keys:
                        value = order.get(key)
                        if value in (None, ''):
                            continue
                        try:
                            return int(float(value))
                        except (TypeError, ValueError):
                            continue
                    return 0

                return {
                    'order_id': current_order_id,
                    'status': status,
                    'average_price': _as_float('averageprice', 'average_price', 'price'),
                    'trigger_price': _as_float('triggerprice', 'trigger_price'),
                    'filled_quantity': _as_int('filledshares', 'filledquantity', 'filled_quantity'),
                    'raw': order,
                }

            return None
        except Exception as e:
            logger.error(f"ORDER_STATUS: ERROR | order_id={order_id} | {str(e)}")
            return None

    def cancel_outstanding_orders_for_symbol(self, symbol: str, exclude_order_ids: Optional[List[str]] = None) -> List[str]:
        """Cancel any still-open broker orders for a closed symbol."""
        cancelled_order_ids: List[str] = []

        if OptionsTradingConfig.TRADING_MODE == "PAPER":
            return cancelled_order_ids

        try:
            order_book = self.get_order_book()
            if not order_book:
                logger.warning(f"ORDER_CLEANUP: NO_ORDER_BOOK | {symbol}")
                return cancelled_order_ids

            excluded = {str(order_id) for order_id in (exclude_order_ids or []) if order_id}
            terminal_statuses = {
                'COMPLETE', 'FILLED', 'FULLY_FILLED', 'REJECTED', 'CANCELLED', 'CANCELED', 'EXPIRED'
            }

            for order in order_book:
                trading_symbol = str(order.get('tradingsymbol') or order.get('symbol') or '')
                if trading_symbol != symbol:
                    continue

                order_id = str(order.get('orderid') or order.get('order_id') or '')
                if not order_id or order_id in excluded:
                    continue

                status = str(order.get('orderstatus') or order.get('orderstate') or order.get('status') or '').upper()
                if status in terminal_statuses:
                    continue

                order_type = str(order.get('ordertype') or order.get('order_type') or 'MARKET').upper()
                variety = str(order.get('variety') or '').upper()
                cancel_order_type = 'STOPLOSS_MARKET' if ('STOPLOSS' in order_type or variety == 'STOPLOSS') else 'MARKET'

                if self.cancel_order(order_id, symbol, order_type=cancel_order_type):
                    cancelled_order_ids.append(order_id)
                    logger.warning(f"ORDER_CLEANUP: CANCELLED_STALE_ORDER | {symbol} | order_id={order_id} | status={status}")
                else:
                    logger.error(f"ORDER_CLEANUP: CANCEL_FAILED | {symbol} | order_id={order_id} | status={status}")

            if cancelled_order_ids:
                logger.warning(f"ORDER_CLEANUP: COMPLETED | {symbol} | cancelled={cancelled_order_ids}")

            return cancelled_order_ids
        except Exception as e:
            logger.error(f"ORDER_CLEANUP: ERROR | {symbol} | {str(e)}")
            return cancelled_order_ids
    
    def get_trade_book(self) -> Optional[Dict[str, Any]]:
        """
        Get Trade Book from Angel One broker.
        
        Angel One API: GET /rest/secure/angelbroking/order/v1/getTradeBook
        
        Returns list of all EXECUTED trades (actual fills/executions).
        Used to verify which positions are actually open with broker.
        
        Returns:
            Dict with trades list and net positions or None if failed
        """
        try:
            if not self.authenticated:
                logger.warning("TRADE_BOOK: Not authenticated")
                return None
            
            if not self.smart_api:
                logger.warning("TRADE_BOOK: SmartAPI not initialized")
                return None
            
            # Get rate limiter
            rate_limiter = get_options_rate_limiter()
            
            # Wait for rate limit permission
            if not rate_limiter.wait_for_call_permission(timeout=5.0):
                logger.warning("TRADE_BOOK: RATE_LIMITED")
                return None
            
            # Call getTradeBook API
            rate_limiter.record_call("get_trade_book", True)
            logger.info("TRADE_BOOK: Fetching from broker...")
            
            trade_book_method = getattr(self.smart_api, "getTradeBook", None) or getattr(self.smart_api, "tradeBook", None)
            if not trade_book_method:
                logger.error("TRADE_BOOK: SmartAPI trade book method unavailable")
                rate_limiter.record_call("get_trade_book", False)
                return None

            response = trade_book_method()
            
            if response and response.get('status'):
                trades = response.get('data', [])
                logger.info(f"TRADE_BOOK: Retrieved {len(trades)} executed trades from broker")
                
                # Build trade summary (count BUY vs SELL to find net positions)
                position_net = {}  # {symbol: net_quantity}
                for trade in trades:
                    symbol = trade.get('tradingsymbol', '')
                    qty = int(trade.get('tradeqty', 0))
                    action = trade.get('ordertype', '').upper()  # BUY or SELL
                    
                    if symbol:
                        if symbol not in position_net:
                            position_net[symbol] = 0
                        
                        if action == 'BUY':
                            position_net[symbol] += qty
                        elif action == 'SELL':
                            position_net[symbol] -= qty
                        
                        logger.debug(f"TRADE_BOOK: {action:4s} | {symbol:20s} | qty={qty:6d} | net={position_net[symbol]:6d}")
                
                # Return both trades and net positions
                return {
                    'trades': trades,
                    'net_positions': {sym: qty for sym, qty in position_net.items() if qty != 0},
                    'timestamp': datetime.now().isoformat()
                }
            else:
                logger.warning(f"TRADE_BOOK: API failed | response={response}")
                rate_limiter.record_call("get_trade_book", False)
                return None
        
        except Exception as e:
            logger.error(f"TRADE_BOOK: ERROR | {str(e)}")
            rate_limiter = get_options_rate_limiter()
            rate_limiter.record_call("get_trade_book", False)
            return None
    
    def verify_positions_with_broker(self) -> Dict[str, Any]:
        """
        CRITICAL FOR SQUAREOFF:
        Verify which positions are actually open with the broker.
        
        Uses getTradeBook to fetch all executed trades, calculates net positions,
        and compares with internal position tracking.
        
        This prevents duplicate SELL orders on positions that were already closed!
        
        Returns:
            {
                'verified': bool,
                'net_positions': {symbol: quantity},
                'internal_positions': {symbol: quantity},
                'mismatches': [symbol, ...],  # Positions different between broker and internal
                'timestamp': str
            }
        """
        try:
            # Get live trade book from broker
            trade_data = self.get_trade_book()
            if not trade_data:
                logger.warning("VERIFY_POSITIONS: Could not fetch trade book from broker")
                return {
                    'verified': False,
                    'error': 'Failed to fetch trade book from broker',
                    'timestamp': datetime.now().isoformat()
                }
            
            # Extract net positions from trade book
            broker_positions = trade_data.get('net_positions', {})
            
            logger.info(f"VERIFY_POSITIONS: Broker has {len(broker_positions)} open positions")
            logger.debug(f"VERIFY_POSITIONS: Open positions: {broker_positions}")
            
            return {
                'verified': True,
                'net_positions': broker_positions,
                'timestamp': datetime.now().isoformat()
            }
        
        except Exception as e:
            logger.error(f"VERIFY_POSITIONS: ERROR | {str(e)}")
            return {
                'verified': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }


_options_broker_instance = None

def get_options_broker() -> AngelOneOptionsBroker:
    """Get or create options broker instance"""
    global _options_broker_instance
    if _options_broker_instance is None:
        _options_broker_instance = AngelOneOptionsBroker()
    return _options_broker_instance
