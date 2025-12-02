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
    from SmartApi import SmartConnect, SmartWebSocketV2
except ImportError:
    try:
        from smartapi import SmartConnect, SmartWebSocketV2
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
        self.contracts: Dict[Tuple[float, str], OptionContract] = {}  # {(strike, type): contract}
        self.atm_strike = None
        self.last_updated = None
    
    def add_contract(self, contract: OptionContract):
        """Add contract to chain"""
        key = (contract.strike, contract.contract_type)
        self.contracts[key] = contract
    
    def get_contract(self, strike: float, contract_type: str) -> Optional[OptionContract]:
        """Get contract for specific strike and type"""
        return self.contracts.get((strike, contract_type))
    
    def get_atm_contracts(self, current_spot: float, offset: int = 0) -> Optional[Tuple[OptionContract, OptionContract]]:
        """Get ATM or offset contracts (CE, PE) based on current spot price"""
        # Find nearest strike
        available_strikes = sorted(set(s for s, _ in self.contracts.keys()))
        if not available_strikes:
            return None
        
        # Find ATM strike (nearest to spot)
        atm_strike = min(available_strikes, key=lambda x: abs(x - current_spot))
        
        # Apply offset if requested
        strike_index = available_strikes.index(atm_strike)
        if offset > 0 and strike_index + offset < len(available_strikes):
            selected_strike = available_strikes[strike_index + offset]
        elif offset < 0 and strike_index + offset >= 0:
            selected_strike = available_strikes[strike_index + offset]
        else:
            selected_strike = atm_strike
        
        ce = self.get_contract(selected_strike, 'CE')
        pe = self.get_contract(selected_strike, 'PE')
        return (ce, pe) if ce and pe else None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert chain to dictionary for storage"""
        return {
            'underlying': self.underlying,
            'expiry': self.expiry,
            'contracts': {
                f"{strike}_{type}": contract.to_dict()
                for (strike, type), contract in self.contracts.items()
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
            'BANKNIFTY': 47000,
            'NIFTY': 23500,
            'FINNIFTY': 22000,
        }
        
        # Positions tracking
        self.option_positions: Dict[str, Dict[str, Any]] = {}  # {symbol: position_data}
    
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
                logger.info(f"BROKER_AUTHENTICATE: SUCCESS | client={self.client_code}")
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
            
            # PAPER mode: generate mock chain
            if OptionsTradingConfig.TRADING_MODE == "PAPER":
                chain = self._create_mock_option_chain(underlying, expiry, current_price)
                logger.debug(f"CHAIN_FETCH: Generated mock chain | contracts={len(chain.contracts)}")
            else:
                chain = self._fetch_from_angel(underlying, expiry)
                logger.debug(f"CHAIN_FETCH: Fetched from Angel One | contracts={len(chain.contracts) if chain else 0}")
            
            if chain:
                # In LIVE mode: cache chain to memory for faster retrieval
                # In PAPER mode with dynamic prices: only keep in memory for this request
                if should_use_cache:
                    self.option_chains[key] = chain
                    self.chain_last_updated[key] = datetime.now()
                    self._cache_chain(chain)
                logger.info(f"CHAIN_FETCH: SUCCESS | {underlying} | strikes={len(set(s for s, _ in chain.contracts.keys()))} | cached={should_use_cache}")
            else:
                logger.warning(f"CHAIN_FETCH: FAILED | {underlying} {expiry}")
                rate_limiter.record_call("fetch_chain", False)
            
            return chain
        except Exception as e:
            logger.error(f"CHAIN_FETCH: ERROR | {underlying} | {str(e)}", underlying=underlying, expiry=expiry)
            rate_limiter.record_call("fetch_chain", False)
            return None
    
    def _fetch_from_angel(self, underlying: str, expiry: str) -> Optional[OptionChain]:
        """Fetch from AngelOne API (production implementation)"""
        # This would call SmartAPI to fetch live option chain
        # For now, return None (would be implemented with real API)
        return None
    
    def _create_mock_option_chain(self, underlying: str, expiry: str, current_price: Optional[float] = None) -> OptionChain:
        """Create mock option chain for PAPER mode testing using CE extractor
        
        Args:
            underlying: Stock or index symbol
            expiry: Expiry date YYYY-MM-DD
            current_price: Current market price to center strikes around (if None, uses configured spot)
        """
        chain = OptionChain(underlying, expiry)
        
        # Use CE extractor to generate realistic contracts
        # Generate wider range of strikes (31 strikes) to support various alert prices
        # In real trading, broker APIs return full chains; this simulates that
        generator = OptionChainGenerator()
        contracts_data = generator.generate_chain(underlying, expiry, num_strikes=31, center_price=current_price)
        
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
            contract = OptionContract(
                underlying=contract_data['underlying'],
                strike=contract_data['strike'],
                expiry=contract_data['expiry'],
                contract_type=contract_data['contract_type'],
                symbol=contract_data['symbol']
            )
            
            # Get token from instrument file if available
            token = self.ce_extractor.get_token_for_symbol(contract_data['symbol'])
            if token:
                contract.token = token
            
            # Mock market data
            itm_offset = abs((contract_data['strike'] - spot) / 100)
            base_premium = spot * 0.02
            premium = base_premium / (1 + itm_offset * 0.3)
            
            contract.ltp = premium
            contract.iv = 20 + (itm_offset * 2)
            contract.delta = 0.5 + ((contract_data['strike'] - spot) / 1000) if contract_data['contract_type'] == 'CE' else 0.5 - ((contract_data['strike'] - spot) / 1000)
            contract.delta = max(0, min(1, contract.delta))
            contract.gamma = 0.05 - min(itm_offset * 0.005, 0.04)
            contract.theta = -0.02 - (itm_offset * 0.005)
            contract.vega = 0.1
            contract.open_interest = 10000 + int(itm_offset ** 2 * 1000)
            contract.volume = 1000 + int(itm_offset ** 2 * 100)
            contract.bid = premium * 0.98
            contract.ask = premium * 1.02
            contract.last_updated = datetime.now().isoformat()
            
            chain.add_contract(contract)
        
        chain.atm_strike = spot
        chain.last_updated = datetime.now().isoformat()
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
        """Get next available expiry date (weekly preferred)"""
        today = datetime.now().date()
        
        # Find next Thursday (weekly expiry)
        days_ahead = 3 - today.weekday()  # 3 = Thursday
        if days_ahead <= 0:  # Already passed this week
            days_ahead += 7
        
        next_expiry = today + timedelta(days=days_ahead)
        return next_expiry.strftime("%Y-%m-%d")
    
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
        Fetches LIVE data in both PAPER and LIVE modes.
        
        Args:
            symbol: Symbol name
            exchange: NFO for options, NSE for stocks
        
        Returns:
            Dict with market data or None
        """
        # In PAPER mode, still fetch LIVE market data from broker
        # Only order placement is simulated
        
        if not self.authenticated:
            logger.warning(f"MARKET_DATA: Not authenticated | {symbol}")
            return self._get_mock_market_data(symbol)  # Fallback to mock if not authenticated
        
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
        Requires historical data fetching (not implemented yet - placeholder).
        Returns mock values until historical data API is implemented.
        
        Returns:
            Dict with RSI, ATR values or None
        """
        logger.info(f"INDICATORS: Calculation requested | {symbol} | RSI period={period_rsi} | ATR period={period_atr}")
        
        # TODO: Implement historical data fetching and indicator calculation
        # For now, return mock values (both PAPER and LIVE need historical API)
        return {
            'rsi': 55.0,  # Mock RSI - needs historical data API
            'atr': 50.0,  # Mock ATR - needs historical data API
            'calculated_at': datetime.now().isoformat()
        }

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
