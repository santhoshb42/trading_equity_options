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
        
        # Extract strike from symbol for all contracts
        # Symbol format: SYMBOL+DDMMMYY+STRIKE+CE/PE (e.g., AMBER30DEC257100CE)
        def extract_strike_from_symbol(contract):
            """Extract numeric strike from symbol using pattern matching
            
            Format: {SYMBOL}{DD}{MMM}{YY}{STRIKE}{CE|PE}
            Example: AMBER30DEC257100CE → strike 7100
            Pattern: ([A-Z]+)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)
            """
            symbol = contract.symbol
            
            import re
            # Match pattern: SYMBOL + DD + MMM + YY + STRIKE + CE/PE
            pattern = r'^([A-Z]+)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$'
            match = re.match(pattern, symbol)
            
            if match:
                strike_str = match.group(5)  # Group 5 is the STRIKE
                try:
                    return int(strike_str)
                except:
                    return 0
            return 0
        
        # Build lists of (contract, strike) tuples
        ce_with_strikes = [(c, extract_strike_from_symbol(c)) for c in ce_contracts]
        pe_with_strikes = [(c, extract_strike_from_symbol(c)) for c in pe_contracts]
        
        if not ce_with_strikes or not pe_with_strikes:
            logger.warning(f"ATM: Could not extract strikes from symbols")
            return None
        
        # Find available strikes
        available_strikes = sorted(set(s for _, s in ce_with_strikes))
        if not available_strikes:
            return None
        
        # Find the NEXT HIGHER strike (strike >= current_spot)
        higher_strikes = [s for s in available_strikes if s >= current_spot]
        if higher_strikes:
            atm_strike = higher_strikes[0]  # First strike >= price
        else:
            atm_strike = available_strikes[-1]  # If price is above all strikes, use highest
        
        logger.debug(f"ATM: LTP={current_spot} | next_higher_strike={atm_strike} | available={available_strikes[:5]}...")
        
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
    
    def _check_session_valid(self) -> bool:
        """Check if current session is still valid (max 24 hours)"""
        if not self.authenticated or not self.last_auth_time:
            return False
        
        session_age = (datetime.now() - self.last_auth_time).total_seconds() / 3600
        if session_age > 23:  # Refresh before 24 hour limit
            logger.warning(f"BROKER_SESSION: EXPIRED | age={session_age:.1f}h")
            return False
        
        return True
    
    def authenticate(self) -> bool:
        """Authenticate with AngelOne API"""
        logger.debug("BROKER_AUTHENTICATE: Attempting broker authentication")
        
        if not SmartConnect:
            logger.warning("BROKER_AUTHENTICATE: SmartAPI not available - running in demo mode")
            print("⚠️ SmartAPI not available - running in demo mode")
            self.authenticated = True
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
                logger.info(f"BROKER_AUTHENTICATE: SUCCESS | client={self.client_code} | session_valid_until={self.last_auth_time + timedelta(hours=24)}")
                print(f"✅ Options broker authenticated: {self.client_code}")
                return True
            else:
                logger.error(f"BROKER_AUTHENTICATE: FAILED | message={data.get('message')}")
                print(f"❌ Options broker auth failed: {data.get('message')}")
                return False
        except Exception as e:
            logger.error(f"BROKER_AUTHENTICATE: ERROR | {str(e)}")
            print(f"❌ Options broker auth error: {str(e)}")
            return False
    
    def ensure_authenticated(self) -> bool:
        """Check and refresh authentication if needed - called before critical operations"""
        if not self._check_session_valid():
            logger.info("BROKER_SESSION: REFRESHING | current session expired or invalid")
            print("🔄 Refreshing broker session...")
            return self.authenticate()
        return True
    
    def fetch_option_chain(self, underlying: str, expiry: str, current_price: Optional[float] = None) -> Optional[OptionChain]:
        """
        Fetch complete option chain for underlying and expiry.
        With rate limiting to prevent AngelOne API throttling.
        
        Underlying: BANKNIFTY, NIFTY, FINNIFTY
        Expiry: YYYY-MM-DD
        Current_price: Optional current spot price to center strikes around (for PAPER mode)
        """
        # Get rate limiter instance
        rate_limiter = get_options_rate_limiter()
        
        logger.debug(f"CHAIN_FETCH: {underlying} | expiry={expiry} | current_price={current_price}")
        
        try:
            key = (underlying, expiry)
            
            # In LIVE mode: cache chains for 30 minutes (broker data is static)
            # In PAPER mode with dynamic prices: don't cache (each alert price needs fresh chain)
            should_use_cache = OptionsTradingConfig.TRADING_MODE == "LIVE" or current_price is None
            
            # Check cache (valid for 30 minutes in LIVE mode)
            if should_use_cache and key in self.option_chains:
                last_update = self.chain_last_updated.get(key)
                if last_update and (datetime.now() - last_update).total_seconds() < 1800:
                    logger.debug(f"CHAIN_FETCH: Using cached chain | {underlying} {expiry}")
                    return self.option_chains[key]
            
            logger.info(f"CHAIN_FETCH: Fetching chain | {underlying} {expiry} | cache_enabled={should_use_cache}")
            
            # Wait for rate limit permission (with timeout)
            if not rate_limiter.wait_for_call_permission(timeout=30.0):
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
            chain = self._fetch_from_angel(underlying, expiry)
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
    
    def _fetch_from_angel(self, underlying: str, expiry: str) -> Optional[OptionChain]:
        """Fetch from AngelOne API OR instrument.json for real contracts"""
        # CRITICAL: Use instrument.json to build real option chain with actual contracts
        # This provides real strikes, symbols, tokens - essential for trading
        # Then we fetch LTP, Greeks, IV from broker for each contract
        
        # Load real contracts from instrument.json
        extractor = InstrumentCEExtractor()
        contracts_data = extractor.build_real_option_chain(underlying, expiry, center_price=None)
        
        if not contracts_data:
            logger.warning(f"CHAIN_FETCH: {underlying} NOT in F&O - no real contracts available")
            return None
        
        # Build OptionChain with real contracts
        chain = OptionChain(underlying, expiry)
        
        for contract_data in contracts_data:
            # Extract strike from symbol (e.g., TECHM30DEC251600CE -> 1600)
            symbol = contract_data['symbol']
            underlying_prefix = symbol[:len(contract_data['underlying'])]
            strike_portion = symbol[len(underlying_prefix):-2]  # Remove CE/PE
            
            if len(strike_portion) > 7:
                strike_str = strike_portion[7:]  # Remove DDMMMYY (7 chars)
                try:
                    strike = float(strike_str)
                except ValueError:
                    strike = 0
            else:
                strike = 0
            
            contract = OptionContract(
                underlying=contract_data['underlying'],
                strike=strike,
                expiry=contract_data['expiry'],
                contract_type=contract_data['contract_type'],
                symbol=contract_data['symbol']
            )
            
            # Get token from instrument file
            contract.token = contract_data.get('token', '')
            
            # Fetch REAL LTP and Greeks from broker if authenticated
            if self.authenticated:
                try:
                    ltp = self.get_ltp(symbol, exchange="NFO")
                    if ltp:
                        contract.ltp = ltp
                        contract.bid = ltp * 0.98  # Approximate bid
                        contract.ask = ltp * 1.02  # Approximate ask
                except Exception as e:
                    logger.debug(f"CHAIN_FETCH: Could not fetch LTP for {symbol}: {e}")
            
            chain.add_contract(contract)
        
        logger.info(f"CHAIN_FETCH: Built real chain | {underlying} | contracts={len(chain.contracts)} | expiry={expiry}")
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
            contract.iv = 20
            contract.delta = 0.5
            contract.gamma = 0.05
            contract.theta = -0.02
            contract.vega = 0.1
            contract.open_interest = 10000
            contract.volume = 1000
            contract.bid = base_premium * 0.98
            contract.ask = base_premium * 1.02
            contract.last_updated = datetime.now().isoformat()
            
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
    
    def get_next_expiry(self, underlying: str) -> str:
        """Get current month's expiry (last Tuesday of month)
        
        Monthly expiry = last Tuesday of current month.
        This matches what's available in instrument.json from broker.
        """
        today = datetime.now().date()
        
        # Find LAST Tuesday of current month
        # Start from last day of month and work backwards
        import calendar
        last_day = calendar.monthrange(today.year, today.month)[1]
        last_date = today.replace(day=last_day)
        
        # Find last Tuesday
        # Tuesday = 1 in Python's weekday (0=Monday, 1=Tuesday, ... 6=Sunday)
        days_back = (last_date.weekday() - 1) % 7
        last_tuesday = last_date - timedelta(days=days_back)
        
        # If we've passed this month's expiry, use next month's
        if today > last_tuesday:
            # Move to next month
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=1)
            else:
                next_month = today.replace(month=today.month + 1, day=1)
            
            last_day = calendar.monthrange(next_month.year, next_month.month)[1]
            last_date = next_month.replace(day=last_day)
            days_back = (last_date.weekday() - 1) % 7  # Tuesday = 1
            last_tuesday = last_date - timedelta(days=days_back)
        
        return last_tuesday.strftime("%Y-%m-%d")
    
    def place_options_order(self,
                           symbol: str,  # BANKNIFTY25XXX1900CE
                           action: str,  # BUY or SELL
                           quantity: int,  # Lot size
                           price: float = 0,
                           order_type: str = "MARKET",
                           product_type: str = "INTRADAY") -> Optional[str]:
        """
        Place options order (CE or PE contract)
        With rate limiting to prevent AngelOne API throttling
        
        Returns: order_id on success, None on failure, or queued marker if rate limited
        """
        # Get rate limiter instance
        rate_limiter = get_options_rate_limiter()
        
        logger.debug(f"ORDER_PLACE: {symbol} | action={action} qty={quantity} price={price:.2f}")
        
        if not self.authenticated and OptionsTradingConfig.TRADING_MODE != "PAPER":
            logger.error(f"ORDER_PLACE: Not authenticated | symbol={symbol}")
            print(f"❌ Not authenticated - cannot place options order")
            return None
        
        try:
            # Wait for rate limit permission (with timeout)
            if not rate_limiter.wait_for_call_permission(timeout=30.0):
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
                return order_id
            
            # LIVE mode: Place actual order to broker
            try:
                order_params = {
                    "variety": "NORMAL",
                    "tradingsymbol": symbol,
                    "symboltoken": self.get_instrument_token(symbol, "NFO"),
                    "transactiontype": action,
                    "exchange": "NFO",
                    "ordertype": order_type,
                    "producttype": product_type,
                    "duration": "DAY",
                    "quantity": str(quantity)
                }
                
                if order_type == "LIMIT" and price > 0:
                    order_params["price"] = str(price)
                
                response = self.smart_api.placeOrder(order_params)
                
                if response and response.get('status'):
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
                    return order_id
                else:
                    logger.error(f"ORDER_PLACE: LIVE FAILED | {symbol} | response={response}")
                    print(f"❌ [LIVE] Order placement failed: {response.get('message')}")
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
            if not rate_limiter.wait_for_call_permission(timeout=30.0):
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
    
    def get_instrument_token(self, symbol: str, exchange: str = "NFO") -> Optional[str]:
        """Get instrument token from CE extractor's instrument.json"""
        try:
            # Use CE extractor to lookup token
            instruments = self.ce_extractor.instruments
            if symbol in instruments:
                return instruments[symbol].get('token', '')
            
            # Try with -EQ suffix for equity
            if exchange == "NSE":
                eq_symbol = f"{symbol}-EQ"
                if eq_symbol in instruments:
                    return instruments[eq_symbol].get('token', '')
            
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
            
            # Fetch LTP from AngelOne
            rate_limiter.record_call("ltp_fetch", True)
            ltp_data = self.smart_api.ltpData(exchange, symbol, token)
            
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
    
    def get_ltp_bulk(self, symbols: List[str], exchange: str = "NFO") -> Dict[str, Optional[float]]:
        """
        Get LTP for multiple option symbols in fewer API calls.
        
        OPTIMIZATION: Instead of calling get_market_data() N times (N API calls),
        this fetches multiple symbols efficiently, reducing API usage and rate limiting.
        
        Args:
            symbols: List of option symbols (e.g., ["BANKNIFTY25JAN19800CE", "NIFTY25JAN18000CE"])
            exchange: NFO for options
        
        Returns:
            Dictionary mapping symbol -> LTP value (None if not available)
        
        Example:
            ltps = broker.get_ltp_bulk(["BANKNIFTY25JAN19800CE", "NIFTY25JAN18000CE"])
            # Returns: {"BANKNIFTY25JAN19800CE": 250.5, "NIFTY25JAN18000CE": 180.3}
        """
        if not symbols:
            return {}
        
        # CRITICAL: Always fetch LIVE data from broker, never use mock/fallback
        # Paper trading means simulated order placement, NOT simulated prices
        # Users need real market data to make real trading decisions
        logger.info(f"BULK_MARKET_DATA: STARTING | symbols={len(symbols)} | authenticated={self.authenticated}")
        
        if not self.authenticated:
            logger.warning(f"BULK_MARKET_DATA: Not authenticated, cannot fetch live data for {len(symbols)} symbols")
            return {sym: None for sym in symbols}
        
        result = {sym: None for sym in symbols}
        
        try:
            # Get rate limiter
            rate_limiter = get_options_rate_limiter()
            
            # Fetch each symbol - SmartAPI doesn't support true bulk endpoint for options
            # but we batch the rate limit waits to be more efficient
            fetched_count = 0
            failed_symbols = []
            
            for symbol in symbols:
                # Wait for rate limit permission with shorter timeout
                if not rate_limiter.wait_for_call_permission(timeout=2.0):
                    logger.warning(f"BULK_MARKET_DATA: RATE_LIMITED | {symbol}")
                    failed_symbols.append(symbol)
                    continue
                
                try:
                    # Get instrument token
                    token = self.get_instrument_token(symbol, exchange)
                    if not token:
                        logger.warning(f"BULK_MARKET_DATA: No token found | {symbol}")
                        failed_symbols.append(symbol)
                        continue
                    
                    # Fetch market data from AngelOne
                    rate_limiter.record_call("bulk_market_data", True)
                    logger.debug(f"BULK_MARKET_DATA: Calling ltpData for {symbol} | token={token}")
                    ltp_data = self.smart_api.ltpData(exchange, symbol, token)
                    
                    if ltp_data and ltp_data.get('status'):
                        data = ltp_data['data']
                        ltp = float(data.get('ltp', 0))
                        if ltp > 0:
                            result[symbol] = ltp
                            fetched_count += 1
                            logger.debug(f"BULK_MARKET_DATA: SUCCESS | {symbol} | ltp=₹{ltp:.2f}")
                        else:
                            logger.debug(f"BULK_MARKET_DATA: Invalid LTP | {symbol}")
                            failed_symbols.append(symbol)
                    else:
                        logger.debug(f"BULK_MARKET_DATA: API call failed | {symbol}")
                        rate_limiter.record_call("bulk_market_data", False)
                        failed_symbols.append(symbol)
                
                except Exception as e:
                    logger.debug(f"BULK_MARKET_DATA: ERROR | {symbol} | {str(e)}")
                    rate_limiter.record_call("bulk_market_data", False)
                    failed_symbols.append(symbol)
            
            log_event("BULK_MARKET_DATA", 
                     f"Fetched LTP for {fetched_count}/{len(symbols)} symbols",
                     total=len(symbols),
                     success=fetched_count,
                     failed=len(failed_symbols))
            
            if failed_symbols:
                logger.warning(f"BULK_MARKET_DATA: Failed for symbols: {failed_symbols}")
            
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
            'iv': 20.0,  # Mock IV of 20%
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
            historical_data = self.get_historical_data(symbol, interval="FIVE_MINUTE", days_back=2)
            
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
                           days_back: int = 2) -> Optional[List[Dict[str, Any]]]:
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
            
            token = self.get_instrument_token(symbol, exchange="NSE")
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
                "exchange": "NSE",
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
# Global broker instance (singleton)
# =============================================================================

_options_broker_instance = None

def get_options_broker() -> AngelOneOptionsBroker:
    """Get or create options broker instance"""
    global _options_broker_instance
    if _options_broker_instance is None:
        _options_broker_instance = AngelOneOptionsBroker()
    return _options_broker_instance
