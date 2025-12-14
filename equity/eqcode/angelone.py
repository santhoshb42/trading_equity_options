"""
AngelOne Module - Equity Trading Bot

SmartAPI wrapper for AngelOne broker integration.
Handles:
- Login and session management
- Order placement (BUY/SELL/SL)
- Order confirmation tracking
- Session monitoring and auto-refresh
- Paper trading mode
"""

import json
import time
import hashlib
import pyotp
import threading
import traceback
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List
from pathlib import Path
import requests
from SmartApi import SmartConnect

from .config import AngelOneConfig, TradingConfig, DevConfig, BASE_DIR
from .bot_logging import log_event, log_alert
from .rate_limiter import rate_limited, get_rate_limiter


# =============================================================================
# Rate Limiting System
# =============================================================================

class TokenBucket:
    """
    Token bucket rate limiter for AngelOne API calls
    
    Implements both per-second and per-minute rate limiting to stay within
    AngelOne's strict API limits: 10 req/sec, 200 req/min
    """
    
    def __init__(self, requests_per_second: int = 6, requests_per_minute: int = 150):
        """
        Initialize rate limiter with conservative limits
        
        Args:
            requests_per_second: Max requests per second (default: 6)
            requests_per_minute: Max requests per minute (default: 150)
        """
        self.rps_limit = requests_per_second
        self.rpm_limit = requests_per_minute
        
        # Token buckets
        self.rps_tokens = requests_per_second
        self.rpm_tokens = requests_per_minute
        
        # Last refill times
        self.last_rps_refill = time.time()
        self.last_rpm_refill = time.time()
        
        # Request tracking
        self.recent_requests = []  # Store timestamps of recent requests
        self.total_requests = 0
        self.blocked_requests = 0
        
        # Thread safety
        self.lock = threading.Lock()
    
    def _refill_tokens(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        
        # Refill per-second bucket
        if now - self.last_rps_refill >= 1.0:
            self.rps_tokens = self.rps_limit
            self.last_rps_refill = now
        
        # Refill per-minute bucket (every 60 seconds)
        if now - self.last_rpm_refill >= 60.0:
            self.rpm_tokens = self.rpm_limit
            self.last_rpm_refill = now
            
            # Clean old request timestamps (keep only last minute)
            cutoff = now - 60
            self.recent_requests = [req_time for req_time in self.recent_requests if req_time > cutoff]
    
    def can_make_request(self) -> bool:
        """
        Check if a request can be made without blocking
        
        Returns:
            True if request can be made immediately, False otherwise
        """
        with self.lock:
            self._refill_tokens()
            return self.rps_tokens > 0 and self.rpm_tokens > 0
    
    def wait_for_request(self, timeout: float = 5.0) -> bool:
        """
        Wait until a request can be made or timeout occurs
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if request can be made, False if timeout
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.can_make_request():
                return True
            
            # Calculate minimum wait time
            with self.lock:
                self._refill_tokens()
                
                if self.rps_tokens <= 0:
                    wait_time = min(0.2, 1.0 - (time.time() - self.last_rps_refill))
                elif self.rpm_tokens <= 0:
                    wait_time = min(1.0, 60.0 - (time.time() - self.last_rpm_refill))
                else:
                    wait_time = 0.1
            
            time.sleep(max(0.05, wait_time))
        
        return False
    
    def consume_token(self) -> bool:
        """
        Consume a token for making a request
        
        Returns:
            True if token consumed successfully, False if rate limited
        """
        with self.lock:
            self._refill_tokens()
            
            if self.rps_tokens > 0 and self.rpm_tokens > 0:
                self.rps_tokens -= 1
                self.rpm_tokens -= 1
                self.total_requests += 1
                self.recent_requests.append(time.time())
                
                log_event("RATE_LIMIT", f"API call allowed", 
                         rps_tokens=self.rps_tokens, rpm_tokens=self.rpm_tokens,
                         total_calls=self.total_requests)
                return True
            else:
                self.blocked_requests += 1
                reason = "RPS" if self.rps_tokens <= 0 else "RPM"
                log_event("RATE_LIMIT_BLOCK", f"API call blocked - {reason} limit hit",
                         rps_tokens=self.rps_tokens, rpm_tokens=self.rpm_tokens,
                         blocked_total=self.blocked_requests)
                return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        with self.lock:
            self._refill_tokens()
            now = time.time()
            
            # Count requests in last minute
            recent_count = len([req for req in self.recent_requests if now - req <= 60])
            
            return {
                "rps_tokens_available": self.rps_tokens,
                "rpm_tokens_available": self.rpm_tokens,
                "rps_limit": self.rps_limit,
                "rpm_limit": self.rpm_limit,
                "requests_last_minute": recent_count,
                "total_requests": self.total_requests,
                "blocked_requests": self.blocked_requests,
                "block_rate": (self.blocked_requests / max(1, self.total_requests)) * 100,
                "last_rps_refill": self.last_rps_refill,
                "last_rpm_refill": self.last_rpm_refill
            }
    
    def reset_stats(self):
        """Reset statistics (for testing)"""
        with self.lock:
            self.total_requests = 0
            self.blocked_requests = 0
            self.recent_requests = []


# =============================================================================
# Order Status Tracking
# =============================================================================

class OrderStatus:
    """Order status constants"""
    PENDING = "PENDING"
    QUEUED = "QUEUED"           # 🔥 NEW: Order queued for retry due to rate limit
    CONFIRMED = "CONFIRMED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Order:
    """Order tracking class"""
    def __init__(self, order_id: str, symbol: str, action: str, quantity: int, price: float):
        self.order_id = order_id
        self.symbol = symbol
        self.action = action  # BUY, SELL, SL
        self.quantity = quantity
        self.price = price
        self.average_price = price  # 🔧 FIX: Store actual filled price (may differ from order price)
        self.status = OrderStatus.PENDING
        self.placed_at = datetime.now()
        self.confirmed_at = None
        self.filled_at = None
        self.rejection_reason = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert order to dictionary"""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "action": self.action,
            "quantity": self.quantity,
            "price": self.price,
            "status": self.status,
            "placed_at": self.placed_at.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "rejection_reason": self.rejection_reason
        }


# =============================================================================
# AngelOne Broker Class
# =============================================================================

class AngelOneBroker:
    """
    AngelOne SmartAPI wrapper with session management and order tracking
    """
    
    def __init__(self):
        self.smart_api = None
        self.session_token = None
        self.refresh_token = None
        self.session_created_at = None
        self.session_file = BASE_DIR / "data" / "session.json"
        self.pending_orders: Dict[str, Order] = {}
        self.instruments_data = None
        
        # Symbol-level locking for burst protection
        self.symbol_locks: Dict[str, threading.Lock] = {}
        self.symbol_lock_manager = threading.Lock()  # Lock for managing symbol locks
        # Per-symbol cooldown/backoff to avoid retry storms on rate-limit or repeated failures
        self.symbol_backoffs: Dict[str, int] = {}
        self.symbol_cooldowns: Dict[str, float] = {}
        self.backoff_base_seconds = 5    # initial backoff on failure
        self.backoff_max_seconds = 300   # maximum backoff (5 minutes)
        
        # 🔍 ORDER REJECTION TRACKING - For debugging "Broker returned None" issues
        self.last_order_error = None  # Store last order error message
        self.last_order_error_symbol = None  # Which symbol failed
        self.last_order_error_time = None  # When it failed
        
        # API ERROR EXTRACTION - For proper error classification
        self.last_api_error_code = None  # Broker error code (AB4036, AG8001, etc.)
        self.last_api_error_message = None  # Detailed error message from broker
        
        # 🔴 ANTI-BURST: Prevent rate limiting by AngelOne (AG8001)
        # AngelOne has strict limits - delay between placeOrder calls
        self.last_place_order_time = 0  # Track last placeOrder call
        self.min_place_order_interval = 0.25  # Minimum 250ms between placeOrder calls
        
        # 🔄 PROACTIVE SESSION REFRESH - Prevent token expiration at 20-minute mark
        # AngelOne tokens valid for ~60 minutes, but we refresh at 15 minutes to be safe
        self.session_refresh_interval = 900  # 15 minutes
        self.session_last_refresh_time = None
        self.session_refresh_thread = None
        self.session_refresh_enabled = True
        self.session_refresh_lock = threading.Lock()
        
        # Initialize PRIORITY rate limiter - ensures orders NEVER fail due to rate limits
        try:
            from .priority_rate_limiter import PriorityRateLimiter
            self.rate_limiter = PriorityRateLimiter(
                rps_limit=AngelOneConfig.REQUESTS_PER_SECOND,
                rpm_limit=AngelOneConfig.REQUESTS_PER_MINUTE
            )
            log_event("BROKER_INIT", "✅ Priority rate limiter initialized - orders have reserved capacity")
        except Exception as e:
            log_event("BROKER_INIT", f"⚠️ Failed to load priority limiter, using fallback: {e}")
            # fallback to standard rate limiter if priority limiter not available
            try:
                self.rate_limiter = get_rate_limiter()
            except Exception:
                from .rate_limiter import AngelOneRateLimiter
                self.rate_limiter = AngelOneRateLimiter()
        
        # Create data directory if not exists
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load instruments
        self.load_instruments()
        
        # Initialize bulk LTP fetcher (reduces API calls and rate limiting)
        try:
            from .bulk_ltp_fetcher import BulkLTPFetcher
            self.bulk_ltp_fetcher = BulkLTPFetcher(
                smart_api=None,  # Will set after authentication
                cache_ttl_seconds=5
            )
            log_event("BROKER_INIT", "✅ Bulk LTP fetcher initialized - will fetch up to 50 symbols per request")
        except Exception as e:
            log_event("BROKER_INIT", f"⚠️ Failed to initialize bulk LTP fetcher: {e}")
            self.bulk_ltp_fetcher = None
        
        # Initialize bulk order fetcher (prevents rate limit exhaustion during order confirmation)
        try:
            from .bulk_order_fetcher import BulkOrderFetcher
            self.bulk_order_fetcher = BulkOrderFetcher(
                self.smart_api,
                fetch_interval_seconds=5  # Fetch orderBook every 5 seconds
            )
            self.bulk_order_fetcher.start()  # Start background thread
            log_event("BROKER_INIT", "✅ Bulk order fetcher initialized - will reduce orderBook API calls")
        except Exception as e:
            log_event("BROKER_INIT", f"⚠️ Failed to initialize bulk order fetcher: {e}")
            self.bulk_order_fetcher = None
    
    def _safe_api_call_with_ag8001_retry(self, func, *args, max_retries: int = 3, initial_backoff: float = 1.0, **kwargs):
        """
        Make a rate-limited API call with AG8001 error retry logic
        
        Implements exponential backoff for AG8001 (token invalid) errors.
        Automatically refreshes session on first AG8001, then retries with exponential backoff.
        
        Args:
            func: API function to call
            *args: Function arguments
            max_retries: Maximum retry attempts (default: 3)
            initial_backoff: Initial backoff time in seconds (default: 1.0)
            **kwargs: Function keyword arguments
            
        Returns:
            API response or None if all retries exhausted
        """
        from .bot_logging import log_broker_error
        
        endpoint_name = func.__name__ if hasattr(func, '__name__') else str(func)
        retry_count = 0
        backoff_time = initial_backoff
        
        while retry_count < max_retries:
            try:
                # Make the API call
                result = self._safe_api_call(func, *args, **kwargs)
                
                # Check if request was queued due to rate limit
                if isinstance(result, dict) and result.get("__QUEUED_FOR_RETRY__"):
                    # Request was queued - return marker to caller
                    log_event("API_QUEUED_FOR_RETRY", f"API call queued for retry due to rate limit",
                             endpoint=endpoint_name)
                    return result
                
                # If successful, return result
                if result is not None:
                    return result
                
                # Check if it's AG8001 (None response likely means token invalid)
                session_age_minutes = (datetime.now() - self.session_created_at).total_seconds() / 60 if hasattr(self, 'session_created_at') and self.session_created_at else None
                
                # If session is over 15 minutes, it's likely AG8001 token expiry
                if session_age_minutes and session_age_minutes > 15:
                    log_event("AG8001_DETECTED", f"Token invalid detected (session age: {session_age_minutes:.1f}m), attempting recovery",
                             endpoint=endpoint_name, retry_count=retry_count, max_retries=max_retries)
                    
                    # Attempt session refresh
                    if self._refresh_session_if_needed():
                        log_event("AG8001_RECOVERY", "Session refreshed successfully after AG8001")
                        # Continue to retry with new session
                        retry_count += 1
                        time.sleep(backoff_time)
                        backoff_time *= 1.5  # Exponential backoff
                        continue
                    else:
                        log_event("AG8001_RECOVERY_FAILED", "Failed to refresh session after AG8001")
                        log_broker_error(
                            error_type="AG8001_RECOVERY_FAILED",
                            error_code="AG8001",
                            message=f"Session recovery failed after {retry_count} retries",
                            endpoint=endpoint_name,
                            context={
                                "retry_count": retry_count,
                                "max_retries": max_retries,
                                "session_age_minutes": session_age_minutes
                            },
                            recovery_attempted=True,
                            recovery_success=False
                        )
                        return None
                
                # Otherwise it's just a None response (not token issue), don't retry
                return None
                
            except Exception as e:
                log_event("ERROR", f"AG8001 retry loop error: {str(e)}")
                log_broker_error(
                    error_type="AG8001_RETRY_ERROR",
                    message=str(e),
                    endpoint=endpoint_name,
                    context={
                        "exception_type": type(e).__name__,
                        "retry_count": retry_count,
                        "traceback": traceback.format_exc()[:500]
                    }
                )
                return None
        
        log_event("AG8001_MAX_RETRIES", f"Max retries ({max_retries}) exhausted for {endpoint_name}")
        log_broker_error(
            error_type="AG8001_MAX_RETRIES_EXHAUSTED",
            error_code="AG8001",
            message=f"Max retries ({max_retries}) exhausted for AG8001 errors",
            endpoint=endpoint_name,
            recovery_attempted=True,
            recovery_success=False
        )
        return None
    
    def _safe_api_call(self, func, *args, timeout: float = None, **kwargs):
        """
        Make a PRIORITY rate-limited API call safely
        
        CRITICAL: Order operations (placeOrder, modifyOrder, cancelOrder) have:
        - Reserved capacity (50% of rate limit)
        - Infinite timeout (will NEVER fail due to rate limits)
        - Priority over all other API calls
        
        Non-critical operations (LTP, positions) have:
        - Shared capacity
        - Quick timeout to avoid blocking
        
        Args:
            func: API function to call
            *args: Function arguments
            timeout: Maximum time to wait (None = use priority default)
            **kwargs: Function keyword arguments
            
        Returns:
            API response or None if rate limited/failed
        """
        from .bot_logging import log_broker_error
        
        # Get endpoint name and priority
        endpoint_name = func.__name__ if hasattr(func, '__name__') else str(func)
        
        # Check if using priority rate limiter
        has_priority = hasattr(self.rate_limiter, 'acquire')
        
        if has_priority:
            # PRIORITY SYSTEM: Orders get infinite timeout, others get priority-based timeout
            log_event("PRIORITY_API_CALL", f"Requesting priority access for {endpoint_name}",
                     endpoint=endpoint_name,
                     timeout="PRIORITY_DEFAULT" if timeout is None else timeout)
            
            # Acquire with priority (orders automatically get infinite timeout + reserved capacity)
            if not self.rate_limiter.acquire(endpoint_name, timeout):
                # Should NEVER happen for CRITICAL operations (placeOrder, modifyOrder, cancelOrder)
                # But can happen for non-critical operations (LTP, positions)
                log_event("PRIORITY_RATE_LIMIT_BLOCKED", 
                         f"⛔ {endpoint_name} blocked by rate limiter after timeout",
                         endpoint=endpoint_name)
                
                log_broker_error(
                    error_type="RATE_LIMIT_EXCEEDED",
                    message=f"{endpoint_name} blocked after waiting for rate limit",
                    endpoint=endpoint_name,
                    context={
                        "timeout": timeout,
                        "priority_system": "enabled"
                    }
                )
                return None
        else:
            # LEGACY SYSTEM: Fall back to old wait_for_request method
            log_event("RATE_LIMIT_WAIT_START", f"Waiting for rate limit clearance",
                     endpoint=endpoint_name, timeout_seconds=timeout if timeout else 30.0)
            
            if not self.rate_limiter.wait_for_request(timeout if timeout else 30.0):
                log_event("RATE_LIMIT_TIMEOUT", f"⛔ {endpoint_name} timed out waiting for rate limit",
                         endpoint=endpoint_name)
                return None
        
        # Rate limit acquired - make the API call
        try:
            # DEBUG: Log before API call
            log_event("API_CALL_DEBUG", f"About to call {endpoint_name} with args={args}, kwargs={kwargs}")
            
            result = func(*args, **kwargs)
            
            # DEBUG: Log after API call
            log_event("API_CALL_DEBUG", f"{endpoint_name} returned: {result}")
            
            # Handle None response (SmartAPI v1.5.5 returns None for AG8001 errors)
            endpoint_name = func.__name__ if hasattr(func, '__name__') else str(func)
            
            # For placeOrder, log None but don't auto-recover (caller handles it)
            if result is None and endpoint_name == 'placeOrder':
                log_event("ORDER_API_NONE", "placeOrder returned None - likely token invalid")
                log_broker_error(
                    error_type="TOKEN_INVALID",
                    error_code="AG8001",
                    message="placeOrder returned None - Invalid Token suspected",
                    endpoint=endpoint_name,
                    context={
                        "session_age_minutes": (datetime.now() - self.session_created_at).total_seconds() / 60 if hasattr(self, 'session_created_at') and self.session_created_at else None
                    }
                )
                return None
            
            # For other endpoints, attempt auto-recovery
            if result is None:
                log_event("SESSION_EXPIRED", "API returned None - likely Invalid Token (AG8001)")
                
                # Log session expiry
                log_broker_error(
                    error_type="SESSION_EXPIRED",
                    error_code="AG8001",
                    message="API returned None - Invalid Token suspected",
                    endpoint=endpoint_name,
                    context={
                        "session_age_minutes": (datetime.now() - self.session_created_at).total_seconds() / 60 if hasattr(self, 'session_created_at') and self.session_created_at else None
                    },
                    recovery_attempted=True
                )
                
                # Clear session cache and force re-login
                self.session_token = None
                self.refresh_token = None
                if self.session_file.exists():
                    self.session_file.unlink()
                
                # Attempt recovery
                if self.login():
                    log_event("SESSION_RECOVERED", "Successfully re-authenticated after None response")
                    log_broker_error(
                        error_type="SESSION_EXPIRED",
                        error_code="AG8001",
                        message="Token recovery successful after None response",
                        endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                        recovery_attempted=True,
                        recovery_success=True
                    )
                    # Brief delay to let new session propagate
                    import time
                    time.sleep(0.5)
                    # Retry the original call once (directly, without rate limiter check since we already consumed token)
                    try:
                        retry_result = func(*args, **kwargs)
                        if retry_result is None:
                            log_event("SESSION_RECOVERY_RETRY_FAILED", "Retry after recovery still returned None")
                            log_broker_error(
                                error_type="SESSION_EXPIRED",
                                error_code="AG8001",
                                message="Retry failed after successful login - still getting None response",
                                endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                                recovery_attempted=True,
                                recovery_success=False
                            )
                        return retry_result
                    except Exception as retry_err:
                        log_event("SESSION_RECOVERY_RETRY_EXCEPTION", f"Retry after recovery threw exception: {str(retry_err)}")
                        log_broker_error(
                            error_type="API_ERROR",
                            message=f"Retry exception after recovery: {str(retry_err)}",
                            endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                            context={"exception_type": type(retry_err).__name__}
                        )
                        return None
                else:
                    log_event("SESSION_RECOVERY_FAILED", "Failed to re-authenticate")
                    log_broker_error(
                        error_type="SESSION_EXPIRED",
                        error_code="AG8001",
                        message="Token recovery failed after None response",
                        endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                        recovery_attempted=True,
                        recovery_success=False
                    )
                    return None
            
            # Check for invalid token error (AG8001) in dict response
            if isinstance(result, dict):
                    # Try both errorCode (capitalized) and errorcode (lowercase) for compatibility
                    error_code = result.get('errorcode') or result.get('errorCode')
                    
                    # Handle token expiry (AG8001)
                    if not result.get('status') and error_code == 'AG8001':
                        log_event("SESSION_EXPIRED", "Invalid Token detected (AG8001) - likely rate limited")
                        
                        # Log session expiry to broker errors
                        log_broker_error(
                            error_type="SESSION_EXPIRED",
                            error_code="AG8001",
                            message=result.get('message', 'Invalid Token'),
                            endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                            context={
                                "session_age_minutes": (datetime.now() - self.session_created_at).total_seconds() / 60 if hasattr(self, 'session_created_at') and self.session_created_at else None,
                                "note": "Angel One has strict rate limits (1 login/sec). If getting AG8001 repeatedly, we're likely rate-limited for the day."
                            },
                            recovery_attempted=False
                        )
                        
                        # DISABLED auto-recovery: Angel One's 1 login/sec rate limit means
                        # if we're getting AG8001, we're likely already rate-limited.
                        # Auto-recovery would just make more failed login attempts.
                        # Sessions are valid until midnight - trust cached session.
                        log_event("SESSION_RECOVERY_DISABLED", "Auto-recovery disabled to prevent rate limit exhaustion")
                        return None
                    
                    # Handle other API error codes
                    elif not result.get('status') and error_code:
                        error_message = result.get('message', 'API error')
                        log_event("API_ERROR", f"Broker API error: {error_code} - {error_message}")
                        
                        log_broker_error(
                            error_type="API_ERROR",
                            error_code=error_code,
                            message=error_message,
                            endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                        context={
                            "full_response": result,
                            "args": str(args)[:200],  # Truncate for safety
                            "kwargs": str(kwargs)[:200]
                        }
                    )
            
            return result
        except Exception as e:
                error_msg = str(e)
                exception_type = type(e).__name__
                
                # Try to extract structured error from exception
                # SmartAPI exceptions may have error details in exception attributes or string
                extracted_error_code = None
                extracted_error_message = None
                
                # Check if exception has response attribute with error details
                if hasattr(e, 'response') and isinstance(e.response, dict):
                    extracted_error_code = e.response.get('errorcode')
                    extracted_error_message = e.response.get('message')
                
                # Otherwise try to extract from string representation (fallback)
                # Example: "errorcode: 'AB4036'" in error message
                if not extracted_error_code:
                    import re
                    error_code_match = re.search(r"errorcode['\"]?\s*:\s*['\"]?([A-Z0-9]+)", error_msg)
                    if error_code_match:
                        extracted_error_code = error_code_match.group(1)
                
                # Store extracted error details for retrieval by caller
                if extracted_error_code:
                    self.last_api_error_code = extracted_error_code
                    self.last_api_error_message = extracted_error_message or error_msg
                    log_event("API_ERROR_EXTRACTED", f"Extracted broker error: {extracted_error_code} - {extracted_error_message}")
                else:
                    self.last_api_error_code = None
                    self.last_api_error_message = error_msg
                
                # Check if error message contains token expiry indicators OR empty response from broker (DataException with b'')
                is_token_error = ('Invalid Token' in error_msg or 'AG8001' in error_msg or 'token' in error_msg.lower())
                is_empty_response = (exception_type == 'DataException' and "b''" in error_msg)
                is_rate_limit = ('rate' in error_msg.lower() or 'access denied' in error_msg.lower())
                
                if is_token_error or is_empty_response:
                    if is_empty_response:
                        log_event("SESSION_EXPIRED", f"Empty response from broker (likely rate limited): {error_msg}")
                    else:
                        log_event("SESSION_EXPIRED", f"Token expiry detected in exception: {error_msg}")
                    
                    log_broker_error(
                        error_type="TOKEN_INVALID",
                        message=error_msg,
                        endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                        context={
                            "exception_type": exception_type, 
                            "is_empty_response": is_empty_response,
                            "is_rate_limit": is_rate_limit,
                            "note": "Angel One: 1 login/sec limit. Empty responses often mean rate-limited."
                        },
                        recovery_attempted=False
                    )
                    
                    # DISABLED auto-recovery: Angel One's strict rate limits mean
                    # attempting recovery will likely fail and waste login quota.
                    # Sessions valid until midnight - trust existing session.
                    log_event("SESSION_RECOVERY_DISABLED", "Auto-recovery disabled to prevent rate limit exhaustion")
                    return None
                
                log_event("API_ERROR", f"API call failed: {str(e)}")
                
                # Log generic API error
                log_broker_error(
                    error_type="API_ERROR",
                    message=error_msg,
                    endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                    context={
                        "exception_type": type(e).__name__,
                        "extracted_error_code": extracted_error_code,
                        "traceback": traceback.format_exc()[:500]  # First 500 chars of traceback
                    }
                )
                return None
        else:
            log_event("RATE_LIMIT_BLOCKED", f"API call blocked by rate limiter")
            
            log_broker_error(
                error_type="RATE_LIMIT_EXCEEDED",
                message="API call blocked - no tokens available",
                endpoint=func.__name__ if hasattr(func, '__name__') else str(func),
                context={"rate_limiter_stats": self.get_rate_limiter_stats()}
            )
            return None
    
    def get_rate_limiter_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics (compat shim)

        Returns a statistics dictionary by calling the available method on the
        configured rate_limiter instance. Supports both older `get_stats()`/
        `reset_stats()` and newer `get_statistics()`/`reset_statistics()` APIs.
        """
        if hasattr(self.rate_limiter, "get_statistics"):
            return self.rate_limiter.get_statistics()
        if hasattr(self.rate_limiter, "get_stats"):
            return self.rate_limiter.get_stats()
        # Last-resort: return minimal info
        try:
            return {"info": str(self.rate_limiter)}
        except Exception:
            return {"info": "unavailable"}
    
    def process_pending_rate_limited_requests(self):
        """Process any queued requests that were rate limited
        
        This method is called periodically from the monitoring loop to retry
        requests that were queued due to rate limiting. This ensures that
        rate-limited orders are automatically retried instead of being lost.
        
        This is critical for preventing order loss under high API traffic.
        """
        if hasattr(self.rate_limiter, "process_pending_requests"):
            self.rate_limiter.process_pending_requests()
            
            # Log queue status if there are pending requests
            stats = self.get_rate_limiter_stats()
            queued_requests = stats.get("queued_requests", 0)
            if queued_requests > 0:
                log_event("QUEUE_PROCESSING", 
                         f"Processing pending rate-limited requests",
                         queued_count=queued_requests,
                         rate_limiter_stats=stats)
    
    def load_instruments(self) -> bool:
        """Load instruments data from JSON file"""
        try:
            instruments_file = BASE_DIR / "tools" / "instrument.json"
            if instruments_file.exists():
                with open(instruments_file, 'r') as f:
                    self.instruments_data = json.load(f)
                log_event("INSTRUMENTS", f"Loaded {len(self.instruments_data)} instruments")
                return True
            else:
                log_event("ERROR", f"Instruments file not found: {instruments_file}")
                return False
        except Exception as e:
            log_event("ERROR", f"Failed to load instruments: {str(e)}")
            return False
    
    def get_instrument_token(self, symbol: str) -> Optional[str]:
        """
        Get instrument token for a symbol
        
        Args:
            symbol: Symbol with -EQ suffix (e.g., RELIANCE-EQ)
            
        Returns:
            Instrument token or None if not found
        """
        if not self.instruments_data:
            log_event("ERROR", "Instruments data not loaded")
            return None
        
        # Remove -EQ suffix for lookup
        base_symbol = symbol.replace("-EQ", "")
        
        # Search in instruments data
        for instrument in self.instruments_data:
            if instrument.get("name") == base_symbol and instrument.get("exch_seg") == "NSE":
                token = instrument.get("token")
                log_event("INSTRUMENT", f"Found token for {symbol}: {token}")
                return token
        
        log_event("ERROR", f"Instrument token not found for symbol: {symbol}")
        return None
    
    def _should_use_cached_session(self) -> bool:
        """
        Check if we should use cached session
        
        Per Angel One docs: Sessions are valid until midnight
        Rate limit: Only 1 login per second, max 1000 per day
        Best practice: Minimize logins, use refresh token when needed
        """
        log_event("SESSION_DEBUG", f"Checking cached session | file_exists={self.session_file.exists()}")
        
        if not self.session_file.exists():
            log_event("SESSION_DEBUG", "No session file found")
            return False
        
        try:
            with open(self.session_file, 'r') as f:
                session_data = json.load(f)
            
            created_at = datetime.fromisoformat(session_data['created_at'])
            now = datetime.now()
            
            # Per Angel One docs: Sessions valid until midnight
            # Check if session is from today (same calendar date)
            if created_at.date() == now.date():
                session_age_minutes = (now - created_at).total_seconds() / 60
                log_event("SESSION", f"Using cached session from today (age: {session_age_minutes:.1f} minutes)")
                return True
            else:
                log_event("SESSION", "Session from previous day - need fresh login")
                return False
                
        except Exception as e:
            log_event("ERROR", f"Error checking cached session: {str(e)}")
            return False
    
    def _load_cached_session(self) -> bool:
        """
        Load cached session data
        
        Per Angel One docs: Sessions valid until midnight
        SKIP validation test to avoid rate limits (1 login/sec limit is very strict)
        Trust that if session is from today, it's valid
        """
        try:
            with open(self.session_file, 'r') as f:
                session_data = json.load(f)
            
            self.session_token = session_data['session_token']
            self.refresh_token = session_data['refresh_token']
            self.session_created_at = datetime.fromisoformat(session_data['created_at'])
            
            # Initialize SmartConnect with cached session
            self.smart_api = SmartConnect(api_key=AngelOneConfig.API_KEY)
            
            # Set the cached tokens directly
            self.smart_api.accessToken = self.session_token
            self.smart_api.refreshToken = self.refresh_token
            
            # SKIP validation test - Angel One sessions valid until midnight
            # Testing adds unnecessary API calls and risks rate limiting
            # If session fails during actual use, error handling will catch it
            log_event("SESSION", "Cached session loaded (trusting validity until midnight)")
            return True
                
        except Exception as e:
            log_event("ERROR", f"Failed to load cached session: {str(e)}")
            return False
            
        except Exception as e:
            log_event("ERROR", f"Failed to load cached session: {str(e)}")
            return False
    
    def _save_session(self):
        """Save current session to file"""
        try:
            session_data = {
                "session_token": self.session_token,
                "refresh_token": self.refresh_token,
                "created_at": self.session_created_at.isoformat()
            }
            
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=2)
            
            log_event("SESSION", "Session saved to file")
            
        except Exception as e:
            log_event("ERROR", f"Failed to save session: {str(e)}")
    
    def _generate_totp(self) -> str:
        """Generate TOTP for 2FA"""
        if not AngelOneConfig.TOTP_SECRET:
            raise ValueError("TOTP_SECRET not configured")
        
        totp = pyotp.TOTP(AngelOneConfig.TOTP_SECRET)
        return totp.now()
    
    @rate_limited(call_type="login", timeout=60.0)
    def login(self) -> bool:
        """
        Login to AngelOne and establish session
        
        Returns:
            True if login successful, False otherwise
        
        Note: Session caching disabled because SmartConnect requires internal state
        set during generateSession() that cannot be replicated when loading from cache.
        Fresh login works perfectly, so we do one login per bot restart (once/day).
        """
        # CRITICAL: AngelOne has strict rate limits (1 login/sec max)
        # Add delay between login attempts to avoid AG8001 errors
        if hasattr(self, 'last_login_time') and self.last_login_time:
            time_since_last_login = time.time() - self.last_login_time
            if time_since_last_login < 1.5:  # Wait minimum 1.5 seconds between logins
                wait_time = 1.5 - time_since_last_login
                log_event("SESSION", f"Rate limiting login attempts, waiting {wait_time:.1f}s")
                time.sleep(wait_time)
        
        # DISABLED: Session caching doesn't work with SmartConnect's internal state
        # if self._should_use_cached_session():
        #     if self._load_cached_session():
        #         return True
        
        # Fresh login required
        from .bot_logging import log_broker_error
        
        log_event("SESSION", "Performing fresh login")
        self.last_login_time = time.time()  # Record login time for rate limiting
        
        try:
            # Initialize SmartConnect
            self.smart_api = SmartConnect(api_key=AngelOneConfig.API_KEY)
            
            # Generate TOTP
            totp = self._generate_totp()
            
            # Login
            data = self.smart_api.generateSession(
                AngelOneConfig.CLIENT_CODE,
                AngelOneConfig.PASSWORD,
                totp
            )
            
            if data['status']:
                self.session_token = data['data']['jwtToken']
                self.refresh_token = data['data']['refreshToken']
                self.session_created_at = datetime.now()
                
                # generateSession() already sets access_token and refresh_token internally
                # No need to manually set them - SmartConnect does it automatically
                
                # DEBUG: Log SmartConnect state after login
                log_event("SESSION_DEBUG", f"SmartConnect instance ID: {id(self.smart_api)}", 
                         has_access_token=hasattr(self.smart_api, 'access_token'),
                         has_refresh_token=hasattr(self.smart_api, 'refresh_token'),
                         access_token_set=bool(getattr(self.smart_api, 'access_token', None)),
                         api_key=self.smart_api.api_key if hasattr(self.smart_api, 'api_key') else None)
                
                # Save session
                self._save_session()
                
                log_event("SESSION", "Login successful", 
                         client_code=AngelOneConfig.CLIENT_CODE,
                         session_id=self.session_token[:10] + "...")
                
                # Start proactive session refresh to prevent AG8001 token expiration
                self.start_proactive_session_refresh()
                
                return True
            else:
                error_msg = data.get('message', 'Unknown error')
                error_code = data.get('errorCode', '')
                
                log_event("ERROR", f"Login failed: {error_msg}")
                
                log_broker_error(
                    error_type="LOGIN_FAILED",
                    error_code=error_code,
                    message=error_msg,
                    endpoint="generateSession",
                    context={
                        "client_code": AngelOneConfig.CLIENT_CODE,
                        "full_response": data
                    }
                )
                return False
                
        except Exception as e:
            log_event("ERROR", f"Login exception: {str(e)}")
            
            log_broker_error(
                error_type="LOGIN_FAILED",
                message=str(e),
                endpoint="generateSession",
                context={
                    "exception_type": type(e).__name__,
                    "traceback": traceback.format_exc()[:500]
                }
            )
            return False
    
    def logged_in(self) -> bool:
        """
        Check if currently logged in with valid session using smart caching
        Uses aggressive caching to avoid unnecessary API calls
        
        Returns:
            True if logged in, False otherwise
        """
        # First check: Do we have basic session components?
        if not self.smart_api or not self.session_token:
            log_event("SESSION", "No session found, attempting login")
            return self.ensure_session()
        
        # Second check: Is session recently validated? (cache for 5 minutes)
        now = datetime.now()
        if hasattr(self, '_last_session_check'):
            time_since_last_check = (now - self._last_session_check).total_seconds()
            if time_since_last_check < 300:  # 5 minutes cache
                log_event("SESSION", f"Using cached session status (checked {time_since_last_check:.0f}s ago)")
                return hasattr(self, '_last_session_valid') and self._last_session_valid
        
        # Third check: Is session age reasonable? (under 40 minutes)
        if hasattr(self, 'session_created_at') and self.session_created_at:
            session_age_minutes = (now - self.session_created_at).total_seconds() / 60
            if session_age_minutes > 40:
                log_event("SESSION", f"Session too old ({session_age_minutes:.1f} minutes), forcing refresh")
                return self._refresh_session_if_needed()
        
        # Fourth check: Try lightweight API call only if cache expired
        try:
            log_event("SESSION", "Performing lightweight session validation")
            # Use the lightest possible API call - rms (Risk Management System) limits
            result = self._safe_api_call(self.smart_api.rmsLimit, timeout=2.0)
            
            # Cache the result
            self._last_session_check = now
            
            if result and result.get('status') is True:
                log_event("SESSION", "Session valid")
                self._last_session_valid = True
                return True
            else:
                log_event("SESSION", "Session invalid, needs refresh")
                self._last_session_valid = False
                return self._refresh_session_if_needed()
                
        except Exception as e:
            log_event("SESSION", f"Session check failed: {str(e)}, attempting refresh")
            self._last_session_valid = False
            return self._refresh_session_if_needed()

    def _refresh_session_if_needed(self) -> bool:
        """
        Attempt to refresh session or perform fresh login with rate limit awareness
        
        Returns:
            True if session is valid after refresh, False otherwise
        """
        from .bot_logging import log_broker_error
        
        try:
            # Check if we recently attempted refresh (avoid spam)
            now = datetime.now()
            if hasattr(self, '_last_refresh_attempt'):
                time_since_refresh = (now - self._last_refresh_attempt).total_seconds()
                if time_since_refresh < 60:  # Don't retry refresh within 1 minute
                    log_event("SESSION", f"Recent refresh attempt ({time_since_refresh:.0f}s ago), using cached result")
                    return hasattr(self, '_last_refresh_success') and self._last_refresh_success
            
            self._last_refresh_attempt = now
            
            # First, try to refresh the session if we have a refresh token
            if self.refresh_token and self.smart_api:
                try:
                    log_event("SESSION", "Attempting session refresh with refresh token")
                    
                    # Conservative retry with rate limiting awareness
                    refresh_result = self.smart_api.generateSession(
                        clientCode=AngelOneConfig.CLIENT_CODE,
                        password=AngelOneConfig.PASSWORD,
                        totp=self._generate_totp()
                    )
                    
                    if refresh_result and refresh_result.get('status'):
                        self.session_token = refresh_result['data']['jwtToken']
                        self.refresh_token = refresh_result['data']['refreshToken']
                        self.session_created_at = datetime.now()
                        self._save_session()
                        
                        # Update cache
                        self._last_session_check = now
                        self._last_session_valid = True
                        self._last_refresh_success = True
                        
                        log_event("SESSION", "Session refreshed successfully")
                        return True
                    else:
                        # Log failed refresh attempt
                        error_msg = refresh_result.get('message', 'Unknown error') if refresh_result else 'No response'
                        error_code = refresh_result.get('errorCode', '') if refresh_result else ''
                        
                        log_broker_error(
                            error_type="SESSION_REFRESH_FAILED",
                            error_code=error_code,
                            message=error_msg,
                            endpoint="generateSession (refresh)",
                            context={
                                "has_refresh_token": bool(self.refresh_token),
                                "session_age_minutes": (now - self.session_created_at).total_seconds() / 60 if hasattr(self, 'session_created_at') and self.session_created_at else None,
                                "full_response": refresh_result
                            },
                            recovery_attempted=True
                        )
                        
                except Exception as refresh_error:
                    log_event("SESSION", f"Session refresh failed: {str(refresh_error)}")
                    
                    log_broker_error(
                        error_type="SESSION_REFRESH_FAILED",
                        message=str(refresh_error),
                        endpoint="generateSession (refresh)",
                        context={
                            "exception_type": type(refresh_error).__name__,
                            "traceback": traceback.format_exc()[:500]
                        },
                        recovery_attempted=True
                    )
            
            # If refresh fails, try fresh login (last resort)
            log_event("SESSION", "Attempting fresh login as last resort")
            self.session_token = None
            self.refresh_token = None
            self.smart_api = None
            
            # Clear cached session
            if self.session_file.exists():
                self.session_file.unlink()
                log_event("SESSION", "Cleared invalid cached session")
            
            # Perform fresh login
            login_success = self.login()
            self._last_refresh_success = login_success
            
            if not login_success:
                log_broker_error(
                    error_type="SESSION_REFRESH_FAILED",
                    message="Fresh login failed after refresh attempt",
                    endpoint="login (fallback)",
                    recovery_attempted=True,
                    recovery_success=False
                )
            
            return login_success
            
        except Exception as e:
            log_event("ERROR", f"Session refresh/login failed: {str(e)}")
            self._last_refresh_success = False
            
            log_broker_error(
                error_type="SESSION_REFRESH_FAILED",
                message=str(e),
                endpoint="_refresh_session_if_needed",
                context={
                    "exception_type": type(e).__name__,
                    "traceback": traceback.format_exc()[:500]
                },
                recovery_attempted=True,
                recovery_success=False
            )
            return False

    def ensure_session(self) -> bool:
        """
        Ensure we have a valid session with aggressive caching
        Only performs actual login if absolutely necessary
        
        Returns:
            True if session is valid, False otherwise
        """
        from .bot_logging import log_broker_error
        
        # Quick check: If we have session components and they're recent, assume valid
        if (self.smart_api and self.session_token and 
            hasattr(self, 'session_created_at') and self.session_created_at):
            
            session_age_minutes = (datetime.now() - self.session_created_at).total_seconds() / 60
            if session_age_minutes < 45:  # Trust sessions under 45 minutes (AngelOne tokens valid ~60min, avoid refresh)
                log_event("SESSION", f"Session assumed valid (age: {session_age_minutes:.1f} minutes)")
                return True
        
        # Need to validate or login
        log_event("SESSION", "Ensuring valid session")
        
        # Try only once to avoid rate limit issues
        try:
            if self.login():
                log_event("SESSION", "Session established successfully")
                return True
            else:
                log_event("SESSION", "Failed to establish session")
                
                log_broker_error(
                    error_type="SESSION_ESTABLISHMENT_FAILED",
                    message="Login returned False when ensuring session",
                    endpoint="ensure_session -> login",
                    context={
                        "has_smart_api": bool(self.smart_api),
                        "has_session_token": bool(self.session_token)
                    }
                )
                return False
                
        except Exception as e:
            log_event("ERROR", f"Session establishment exception: {str(e)}")
            
            log_broker_error(
                error_type="SESSION_ESTABLISHMENT_FAILED",
                message=str(e),
                endpoint="ensure_session",
                context={
                    "exception_type": type(e).__name__,
                    "traceback": traceback.format_exc()[:500]
                }
            )
            return False
    
    def start_proactive_session_refresh(self):
        """
        Start background thread for proactive session refresh
        Refreshes session every 15 minutes to prevent token expiration (AG8001)
        
        This prevents tokens from expiring at 20-23 minutes by proactively refreshing
        at 15-minute mark before expiration window.
        """
        from .bot_logging import log_broker_error
        
        if self.session_refresh_thread and self.session_refresh_thread.is_alive():
            log_event("SESSION", "Proactive session refresh thread already running")
            return
        
        def _refresh_loop():
            """Background thread: periodically refresh session"""
            log_event("SESSION_REFRESH", "Proactive session refresh thread started")
            self.session_last_refresh_time = datetime.now()
            
            while self.session_refresh_enabled:
                try:
                    if not hasattr(self, 'session_created_at') or not self.session_created_at:
                        # No active session, wait and retry
                        time.sleep(60)
                        continue
                    
                    # Calculate session age
                    session_age_seconds = (datetime.now() - self.session_created_at).total_seconds()
                    session_age_minutes = session_age_seconds / 60
                    
                    # Refresh if approaching 15 minutes
                    if session_age_minutes >= 14.5:  # Refresh at 14.5 minutes (well before 20-minute expiry)
                        log_event("SESSION_REFRESH", f"Proactive refresh triggered (session age: {session_age_minutes:.1f}m)")
                        
                        with self.session_refresh_lock:
                            # Double-check age hasn't changed (another thread might have refreshed)
                            if hasattr(self, 'session_created_at') and self.session_created_at:
                                current_age_minutes = (datetime.now() - self.session_created_at).total_seconds() / 60
                                if current_age_minutes >= 14.5:
                                    # Perform refresh
                                    if self._refresh_session_if_needed():
                                        log_event("SESSION_REFRESH", "✅ Proactive session refresh successful")
                                        self.session_last_refresh_time = datetime.now()
                                    else:
                                        log_event("SESSION_REFRESH", "❌ Proactive session refresh failed")
                                        log_broker_error(
                                            error_type="PROACTIVE_SESSION_REFRESH_FAILED",
                                            message="Proactive refresh failed - attempting to recover",
                                            endpoint="start_proactive_session_refresh",
                                            context={
                                                "session_age_minutes": current_age_minutes,
                                                "refresh_interval": self.session_refresh_interval
                                            },
                                            recovery_attempted=True
                                        )
                    
                    # Sleep before next check (check every minute)
                    time.sleep(60)
                    
                except Exception as e:
                    log_event("ERROR", f"Session refresh thread error: {str(e)}")
                    log_broker_error(
                        error_type="SESSION_REFRESH_THREAD_ERROR",
                        message=str(e),
                        endpoint="start_proactive_session_refresh._refresh_loop",
                        context={
                            "exception_type": type(e).__name__,
                            "traceback": traceback.format_exc()[:500]
                        }
                    )
                    # Continue running despite error
                    time.sleep(60)
        
        # Start daemon thread
        self.session_refresh_thread = threading.Thread(
            target=_refresh_loop,
            name="ProactiveSessionRefresh",
            daemon=True
        )
        self.session_refresh_thread.start()
        log_event("SESSION_REFRESH", "Proactive session refresh thread started successfully")
    
    def stop_proactive_session_refresh(self):
        """Stop the background session refresh thread"""
        self.session_refresh_enabled = False
        if self.session_refresh_thread and self.session_refresh_thread.is_alive():
            self.session_refresh_thread.join(timeout=5)
        log_event("SESSION_REFRESH", "Proactive session refresh thread stopped")
    
    def round_to_tick_size(self, price: float) -> str:
        """
        Round price to valid NSE tick size using integer paise arithmetic.
        
        NSE tick size rules:
        - ₹0 to ₹1000: tick size = ₹0.05
        - ₹1000+: tick size = ₹0.05 (Angel One enforces 0.05 for all prices)
        
        CRITICAL: Uses integer arithmetic in paise (1 rupee = 100 paise).
        Tick size of ₹0.05 = 5 paise. This completely avoids float precision issues.
        
        Args:
            price: Original price in rupees (float)
            
        Returns:
            Price rounded to tick size as string (e.g., "7036.90")
        """
        if price < 0:
            return "0.00"
        
        # Convert to paise (integer arithmetic - NO float precision issues!)
        paise = round(price * 100)
        
        # Round to nearest 5 paise (tick size = 5 paise)
        rounded_paise = round(paise / 5) * 5
        
        # Convert back to rupees as Decimal for exact formatting
        from decimal import Decimal
        rupees = Decimal(rounded_paise) / Decimal(100)
        
        return f"{rupees:.2f}"
    
    def place_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float = 0,
        order_type: str = "MARKET",
        product_type: str = "INTRADAY"
    ) -> Optional[Order]:
        """
        Place an order with AngelOne with comprehensive autonomous logging
        
        Args:
            symbol: Stock symbol (e.g., RELIANCE-EQ)
            action: BUY, SELL, or SL
            quantity: Number of shares
            price: Price (for limit orders, 0 for market orders)
            order_type: MARKET or LIMIT
            product_type: INTRADAY, CNC, NRML (Angel One API uses "INTRADAY" not "MIS")
            
        Returns:
            Order object if successful, None otherwise
        """
        from .bot_logging import log_order, log_error
        
        # Log order initiation
        log_order("PLACING", symbol, action, quantity, price)
        
        # 🔴 CRITICAL FIX: Prevent duplicate SELL orders for same symbol
        # BUT: Don't check for SL orders - they're placed via place_order with action="SELL" but order_type="STOPLOSS-MARKET"
        # SL orders are handled separately and we don't have duplicate concerns there
        if action == "SELL" and order_type not in ["STOPLOSS-MARKET", "STOPLOSS-LIMIT", "STOPLOSS_MARKET", "STOPLOSS_LIMIT"]:
            # This is a regular SELL order - check for duplicates
            # TODO: Implement get_orders() or query_orders() to actually check the broker
            # For now, skip this check to prevent AttributeError
            # all_orders = self.get_orders()
            # for existing_order in all_orders:
            #     if (existing_order.symbol == symbol and 
            #         existing_order.action == "SELL" and 
            #         existing_order.status not in ["REJECTED", "CANCELLED"]):
            #         log_event("DUPLICATE_SELL_BLOCKED", ...)
            #         return existing_order
            log_event("DUPLICATE_SELL_CHECK_SKIPPED", f"Skipping duplicate check for {symbol} - will be handled at broker level", symbol=symbol)
        elif action == "SELL" and order_type in ["STOPLOSS-MARKET", "STOPLOSS-LIMIT", "STOPLOSS_MARKET", "STOPLOSS_LIMIT"]:
            # This is an SL order - no duplicate check needed
            log_event("SL_ORDER_BYPASS_DUPLICATE_CHECK", f"SL order for {symbol} - skipping duplicate check", 
                     symbol=symbol, order_type=order_type)
        
        # Get instrument token BEFORE login (instruments don't need auth)
        token = self.get_instrument_token(symbol)
        if not token:
            error_msg = f"Cannot place order - no token for {symbol}"
            log_order("FAILED", symbol, action, quantity, price, error=error_msg)
            log_event("ERROR", error_msg)
            return None
        
        # Check if this is paper trading
        if DevConfig.is_paper_trading():
            log_order("PAPER_TRADING", symbol, action, quantity, price)
            return self._place_paper_order(symbol, action, quantity, price)
        
        # 🔧 CRITICAL FIX: Prevent order placement outside market hours (9:15 AM - 3:30 PM IST)
        # This prevents phantom orders during startup/shutdown before/after market
        from .config import is_market_open, get_market_status
        if not is_market_open():
            market_status = get_market_status()
            error_msg = f"Cannot place order outside market hours | {symbol} | {action} | Current time: {market_status.get('current_time')} | Market: {market_status.get('market_open')} - {market_status.get('market_close')}"
            log_order("BLOCKED_OUTSIDE_MARKET_HOURS", symbol, action, quantity, price, error=error_msg)
            log_event("ORDER_BLOCKED", error_msg)
            return None
        
        try:
            # Handle price as either string (already formatted) or float (needs rounding)
            # If price is already a string (e.g., "7036.90"), keep it as-is
            # If price is a float, round it to tick size
            if isinstance(price, str):
                # Already formatted string - use as-is
                pass
            elif price > 0:
                # Float price - round to valid NSE tick size
                original_price = price
                price = self.round_to_tick_size(price)
                if price != original_price:
                    log_event("TICK_SIZE_ADJUSTMENT", 
                             f"{symbol}: Adjusted price from {original_price} to {price}")
            
            # Determine variety based on order type
            # Angel One requires variety="STOPLOSS" for stop-loss orders, "NORMAL" for others
            is_stoploss = order_type in ["STOPLOSS-MARKET", "STOPLOSS-LIMIT", "STOPLOSS_MARKET", "STOPLOSS_LIMIT"]
            variety = "STOPLOSS" if is_stoploss else "NORMAL"
            
            # Normalize order type to use underscores (Angel One format)
            normalized_order_type = order_type.replace("-", "_")
            
            # Prepare order parameters (matching Angel One API docs format)
            order_params = {
                "variety": variety,
                "tradingsymbol": symbol,  # Keep -EQ suffix! Angel One requires it
                "symboltoken": token,
                "transactiontype": action,
                "exchange": AngelOneConfig.EXCHANGE,
                "ordertype": normalized_order_type,
                "producttype": product_type,
                "duration": AngelOneConfig.DURATION,
                "quantity": str(quantity),
                "price": "0",  # Required even for MARKET orders per Angel One docs
                "squareoff": "0",  # Required per Angel One docs
                "stoploss": "0"  # Required per Angel One docs
            }
            
            # Override price for limit orders
            # Handle price as either string (pre-formatted) or float
            price_str = price if isinstance(price, str) else f"{price:.2f}"
            price_val = float(price_str) if isinstance(price, str) else price
            
            if order_type == "LIMIT" and price_val > 0:
                order_params["price"] = price_str

            # Add trigger price for stop-loss orders
            if is_stoploss and price_val > 0:
                # Use exact string formatting to preserve precision (e.g. '7036.90' not '7036.9')
                order_params["triggerprice"] = price_str
                # For STOPLOSS orders (both MARKET and LIMIT), set price = trigger price
                order_params["price"] = price_str
            
            # Log detailed order parameters
            log_order("SUBMITTING", symbol, action, quantity, price, 
                     api_response={"order_params": order_params})
            
            # Ensure valid session
            if not self.ensure_session():
                error_msg = "Cannot place order - session establishment failed"
                log_order("FAILED", symbol, action, quantity, price, error=error_msg)
                log_event("ERROR", error_msg)
                return None
            
            # Place order
            log_event("ORDER", f"Placing {action} order", 
                     symbol=symbol, quantity=quantity, price=price, type=order_type)
            
            # DEBUG: Log SmartConnect state before order
            log_event("ORDER_DEBUG", f"SmartConnect instance ID before order: {id(self.smart_api)}",
                     has_access_token=hasattr(self.smart_api, 'access_token'),
                     access_token_set=bool(getattr(self.smart_api, 'access_token', None)),
                     api_key=self.smart_api.api_key if hasattr(self.smart_api, 'api_key') else None)
            
            # DEBUG: Log exact params being sent
            log_event("ORDER_PARAMS_DEBUG", f"Params dict: {order_params}")
            
            # 🔴 ANTI-BURST: Prevent AG8001 rate limit errors from Angel One
            # AngelOne has strict rate limits - add minimum interval between placeOrder calls
            time_since_last_order = time.time() - self.last_place_order_time
            if time_since_last_order < self.min_place_order_interval:
                wait_time = self.min_place_order_interval - time_since_last_order
                log_event("ORDER_ANTI_BURST", f"Adding {wait_time*1000:.0f}ms delay to prevent AG8001 rate limit",
                         time_since_last_order=time_since_last_order,
                         min_interval=self.min_place_order_interval)
                time.sleep(wait_time)
            
            self.last_place_order_time = time.time()  # Record this placeOrder call time
            
            # Increased timeout from 10s to 60s to handle burst of multiple alerts
            # With 20 alerts arriving simultaneously and 8 req/sec rate limit,
            # all orders need ~2.5 seconds to execute + network delays + other API calls
            # Use 60s timeout to handle worst-case scenarios with high system load
            response = self._safe_api_call(self.smart_api.placeOrder, order_params, timeout=60.0)
            
            # Log API response
            log_order("API_RESPONSE", symbol, action, quantity, price, 
                     api_response=response)
            
            # Check if request was queued due to rate limit
            if isinstance(response, dict) and response.get("__QUEUED_FOR_RETRY__"):
                # Order was queued for automatic retry
                log_order("QUEUED_FOR_RETRY", symbol, action, quantity, price,
                         api_response={"status": "QUEUED", "reason": "Rate limit exceeded"})
                log_event("ORDER_QUEUED_FOR_RETRY", f"Order queued for retry due to rate limit",
                         symbol=symbol, action=action, quantity=quantity, price=price)
                # Return a placeholder order that indicates it's pending retry
                order = Order("PENDING_RETRY", symbol, action, quantity, price)
                order.status = "QUEUED"
                self.pending_orders["PENDING_RETRY_" + symbol] = order
                return order
            
            # Angel One returns order ID as string on success, dict with status=False on error
            if response and isinstance(response, str):
                # Success - response is the order ID (string)
                order_id = response
                
                # Create order tracking object
                order = Order(order_id, symbol, action, quantity, price)
                self.pending_orders[order_id] = order
                
                # Log successful order placement
                log_order("PLACED", symbol, action, quantity, price, order_id=order_id,
                         api_response={"order_id": order_id, "status": "SUCCESS"})
                
                log_event("ORDER", f"Order placed successfully", 
                         order_id=order_id, symbol=symbol, action=action)
                
                return order
            elif response and isinstance(response, dict) and response.get('status'):
                # Success - old format with dict and status=True
                order_id = response['data']['orderid']
                
                # Create order tracking object
                order = Order(order_id, symbol, action, quantity, price)
                self.pending_orders[order_id] = order
                
                # Log successful order placement
                log_order("PLACED", symbol, action, quantity, price, order_id=order_id,
                         api_response={"order_id": order_id, "status": "SUCCESS"})
                
                log_event("ORDER", f"Order placed successfully", 
                         order_id=order_id, symbol=symbol, action=action)
                
                return order
            else:
                # Error - response can be None (token invalid, rate limit timeout, OR broker error), dict (broker error), or other
                if response is None:
                    # 🔑 PRIORITY 1: Check if token is invalid or session expired
                    if not self.smart_api or not hasattr(self.smart_api, 'access_token') or not self.smart_api.access_token:
                        error_msg = "Order failed - invalid or expired authentication token"
                        error_code = "TOKEN_INVALID"
                        log_event("TOKEN_ERROR", f"Order placement failed due to invalid token | symbol={symbol}")
                    # 🔍 PRIORITY 2: Check if we have extracted error details from the exception
                    elif hasattr(self, 'last_api_error_code') and self.last_api_error_code:
                        # Broker returned an error - use extracted details
                        error_code = self.last_api_error_code
                        error_msg = self.last_api_error_message
                        log_event("BROKER_ERROR_EXTRACTED", f"Using extracted broker error: {error_code}")
                    else:
                        # 🕒 PRIORITY 3: No extracted error and token is valid - genuine rate limit timeout
                        # This happens when rate limiter times out after 30 seconds
                        # Indicates too many requests queued up - order placement failed due to rate limiting
                        error_msg = "Rate limit timeout - order placement blocked by rate limiter"
                        error_code = "RATE_LIMIT_TIMEOUT"
                elif isinstance(response, dict):
                    error_msg = response.get('message', 'Unknown error')
                    # Try both errorcode (lowercase) and errorCode (capitalized) for compatibility
                    error_code = response.get('errorcode') or response.get('errorCode') or response.get('code')
                else:
                    # Some other response format
                    error_msg = f"Unexpected response: {str(response)}"
                    error_code = "UNEXPECTED_RESPONSE"
                
                # 🔍 STORE ERROR FOR LATER RETRIEVAL - helps api.py log actual reason
                self.last_order_error = error_msg
                self.last_order_error_symbol = symbol
                self.last_order_error_time = datetime.now().isoformat()
                
                # 🔍 CLASSIFY REJECTION TYPE
                rejection_type = self._classify_order_rejection(error_msg, error_code, symbol)
                
                # 🔴 LOG SPECIAL ALERTS FOR SCRUTINY/BLACKLIST/OBSERVATION
                if rejection_type in ["SCRUTINY", "BLACKLIST", "OBSERVATION", "TRADING_HALT"]:
                    log_event("SYMBOL_RESTRICTION", 
                             f"Symbol {symbol} has trading restriction - {rejection_type}",
                             symbol=symbol,
                             rejection_type=rejection_type,
                             error_message=error_msg,
                             error_code=error_code)
                
                # Log failed order placement with classification
                log_order("FAILED", symbol, action, quantity, price, 
                         api_response=response, error=error_msg)
                log_event("ORDER_REJECTION_CLASSIFIED", f"Order placement failed for {symbol}",
                         symbol=symbol, action=action, rejection_type=rejection_type,
                         error_message=error_msg, error_code=error_code)
                
                return None
                
        except Exception as e:
            # Comprehensive error logging
            log_error("ORDER_PLACEMENT_EXCEPTION", f"Order placement failed for {symbol} {action}", 
                     e, context={"symbol": symbol, "action": action, "quantity": quantity, "price": price})
            
            log_order("ERROR", symbol, action, quantity, price, error=str(e))
            
            log_event("ERROR", f"Order placement exception: {str(e)}",
                     symbol=symbol, action=action)
            return None
    
    def modify_order(self, order_id: str, symbol: str, quantity: int, price: float, 
                    order_type: str = "STOPLOSS_MARKET", product_type: str = "INTRADAY") -> bool:
        """
        Modify an existing order (e.g., update stop-loss price for trailing SL)
        
        ⚠️ CRITICAL: Must pass all required parameters to Angel One API!
        
        Args:
            order_id: Existing order ID to modify
            symbol: Stock symbol (e.g., RELIANCE-EQ)
            quantity: New quantity
            price: New price/trigger price for SL orders
            order_type: Order type (STOPLOSS_MARKET, STOPLOSS_LIMIT, etc) - MUST match original!
            product_type: Product type (INTRADAY, CNC) - MUST match original!
            
        Returns:
            True if modification successful, False otherwise
        """
        from .bot_logging import log_event
        
        # Get instrument token (REQUIRED by Angel One modifyOrder API)
        token = self.get_instrument_token(symbol)
        if not token:
            log_event("MODIFY_ORDER_FAILED", f"Cannot modify order - no token for {symbol}",
                     order_id=order_id, symbol=symbol)
            return False
        
        # Check if this is paper trading
        if DevConfig.is_paper_trading():
            log_event("MODIFY_ORDER", f"Paper trading - modifying order simulated",
                     order_id=order_id, symbol=symbol, quantity=quantity, price=price)
            return True
        
        # Ensure valid session
        if not self.ensure_session():
            log_event("MODIFY_ORDER_FAILED", f"Session establishment failed for order modification",
                     order_id=order_id, symbol=symbol)
            return False
        
        try:
            # Handle price formatting
            if isinstance(price, str):
                pass  # Already formatted
            else:
                # Round to valid NSE tick size
                price = self.round_to_tick_size(price)
            
            price_str = price if isinstance(price, str) else f"{price:.2f}"
            
            # Determine variety based on order type
            # Angel One requires variety="STOPLOSS" for stop-loss orders
            is_stoploss = order_type in ["STOPLOSS-MARKET", "STOPLOSS-LIMIT", "STOPLOSS_MARKET", "STOPLOSS_LIMIT"]
            variety = "STOPLOSS" if is_stoploss else "NORMAL"
            
            # Normalize order type to use underscores (Angel One format)
            normalized_order_type = order_type.replace("-", "_")
            
            # Prepare modify order parameters (Angel One API format)
            # According to Angel One docs: https://smartapi.angelbroking.com/docs/Orders#modifyorder
            # REQUIRED params: variety, orderid, ordertype, producttype, duration, price, quantity
            # OPTIONAL but included: tradingsymbol, symboltoken, exchange
            # 🔧 CRITICAL FIX: For STOPLOSS orders, use 'triggerprice' field, NOT 'price' field!
            modify_params = {
                "variety": variety,                    # REQUIRED: NORMAL or STOPLOSS
                "orderid": order_id,                   # REQUIRED: existing order ID
                "ordertype": normalized_order_type,    # REQUIRED: must match original (STOPLOSS_MARKET, etc)
                "producttype": product_type,           # REQUIRED: must match original (INTRADAY, CNC, etc)
                "duration": AngelOneConfig.DURATION,   # REQUIRED: DAY or IOC
                "quantity": str(quantity),             # REQUIRED: quantity (usually stays same)
                "tradingsymbol": symbol,               # Helpful for logging/debugging
                "symboltoken": token,                  # Helpful for logging/debugging
                "exchange": AngelOneConfig.EXCHANGE,   # Helpful for logging/debugging
            }
            
            # 🔧 CRITICAL FIX: For STOPLOSS orders, use 'triggerprice', not 'price'!
            # Angel One modifyOrder API requires:
            # - For NORMAL orders: use "price" field
            # - For STOPLOSS orders: use "triggerprice" field
            # Using wrong field makes API return SUCCESS but order is NOT actually modified!
            if is_stoploss:
                modify_params["triggerprice"] = price_str  # SL orders need triggerprice
            else:
                modify_params["price"] = price_str         # Normal orders need price
            
            log_event("MODIFY_ORDER", f"Modifying order {order_id} for {symbol}",
                     order_id=order_id, symbol=symbol, quantity=quantity, 
                     new_price=price, order_type=order_type, product=product_type,
                     params=modify_params)
            
            # Add delay to avoid AG8001 rate limit (like place_order does)
            time_since_last_order = time.time() - self.last_place_order_time
            if time_since_last_order < self.min_place_order_interval:
                wait_time = self.min_place_order_interval - time_since_last_order
                log_event("MODIFY_ORDER_ANTI_BURST", f"Adding {wait_time*1000:.0f}ms delay to prevent AG8001",
                         wait_time=wait_time)
                time.sleep(wait_time)
            
            # Call modify order API
            response = self._safe_api_call(self.smart_api.modifyOrder, modify_params, timeout=30.0)
            
            log_event("MODIFY_ORDER_RESPONSE", f"Modify order API response",
                     order_id=order_id, response=response)
            
            # Angel One returns success indicator (either string order ID or dict with status=True)
            if response and (isinstance(response, str) or (isinstance(response, dict) and response.get('status'))):
                log_event("MODIFY_ORDER_SUCCESS", f"Order modified successfully on broker",
                         order_id=order_id, symbol=symbol, new_price=price, 
                         order_type=order_type, product=product_type)
                return True
            else:
                error_msg = response.get('message', str(response)) if isinstance(response, dict) else str(response)
                log_event("MODIFY_ORDER_FAILED", f"Failed to modify order: {error_msg}",
                         order_id=order_id, symbol=symbol, error=error_msg)
                return False
        
        except Exception as e:
            log_event("MODIFY_ORDER_EXCEPTION", f"Exception modifying order: {str(e)}",
                     order_id=order_id, symbol=symbol, error=str(e))
            return False
    
    def _place_paper_order(self, symbol: str, action: str, quantity: int, price: float) -> Order:
        """
        Simulate order placement for paper trading
        
        Args:
            symbol: Stock symbol
            action: BUY, SELL, SL
            quantity: Number of shares
            price: Price
            
        Returns:
            Mock Order object
        """
        # Generate mock order ID
        order_id = f"PAPER_{int(time.time() * 1000)}"
        
        # Use mock price if available
        if price == 0 and symbol in DevConfig.MOCK_PRICES:
            price = DevConfig.MOCK_PRICES[symbol]
        
        # Create mock order
        order = Order(order_id, symbol, action, quantity, price)
        order.status = OrderStatus.FILLED  # Instantly fill paper orders
        order.confirmed_at = datetime.now()
        order.filled_at = datetime.now()
        
        log_event("PAPER_ORDER", f"Paper {action} order simulated",
                 order_id=order_id, symbol=symbol, quantity=quantity, price=price)
        
        return order
    
    @rate_limited(call_type="order_status", timeout=15.0)
    def check_order_status(self, order: Order) -> bool:
        """
        Check and update order status using bulk orderBook cache
        
        IMPORTANT: This method now reads from bulk_order_fetcher's cache instead of
        calling orderBook directly. The bulk fetcher calls orderBook every 5 seconds
        in the background, reducing API calls from 30 per order to 1 per order.
        
        Args:
            order: Order object to check
            
        Returns:
            True if order is filled, False if still pending
        """
        if DevConfig.is_paper_trading():
            # Paper orders are instantly filled
            return order.status == OrderStatus.FILLED
        
        if not self.ensure_session():
            return False
        
        try:
            # 🔑 KEY FIX: Read from bulk order fetcher cache instead of calling orderBook
            order_data = None
            
            if self.bulk_order_fetcher and self.bulk_order_fetcher.is_cache_fresh():
                # Use cached orderBook result (updated every 5 seconds in background)
                order_data = self.bulk_order_fetcher.get_order_data(order.order_id)
                cache_status = "cache_hit"
            else:
                # Fallback: call orderBook directly if cache not available
                # This happens during startup before first bulk fetch completes
                order_history = self._safe_api_call(self.smart_api.orderBook, timeout=5.0)
                
                if order_history and order_history.get('status'):
                    orders = order_history.get('data', [])
                    for od in orders:
                        if od.get('orderid') == order.order_id:
                            order_data = od
                            break
                cache_status = "direct_call"
            
            if not order_data:
                return False
            
            status = order_data.get('status', '').upper()
            
            # 🔧 FIX: Capture actual filled price (averageprice) from broker
            if 'averageprice' in order_data and order_data['averageprice'] > 0:
                order.average_price = order_data['averageprice']
            
            if status in ['COMPLETE', 'FILLED']:
                order.status = OrderStatus.FILLED
                order.filled_at = datetime.now()
                # Remove from pending orders
                if order.order_id in self.pending_orders:
                    del self.pending_orders[order.order_id]
                log_event("ORDER", f"Order filled (via {cache_status})", order_id=order.order_id, 
                         average_price=order.average_price)
                return True
            elif status in ['REJECTED', 'CANCELLED']:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = order_data.get('text', 'Unknown')
                # Remove from pending orders
                if order.order_id in self.pending_orders:
                    del self.pending_orders[order.order_id]
                log_event("ORDER", f"Order rejected (via {cache_status})", 
                         order_id=order.order_id, reason=order.rejection_reason)
                return False
            elif status == 'PENDING':
                order.status = OrderStatus.PENDING
                return False
            else:
                order.status = OrderStatus.CONFIRMED
                order.confirmed_at = datetime.now()
                return False
            
        except Exception as e:
            log_event("ERROR", f"Error checking order status: {str(e)}")
            return False
    
    def wait_for_buy_confirmation(self, symbol: str, timeout: int = 60) -> Tuple[bool, str, Optional[Order]]:
        """
        Wait for BUY order confirmation and prevent SELL orders during this time
        
        Args:
            symbol: Symbol to monitor
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (success, message, order_object)
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Check all pending BUY orders for this symbol
            pending_buy_orders = []
            for order in self.pending_orders.values():
                if (order.symbol == symbol and 
                    order.action == "BUY" and 
                    order.status == OrderStatus.PENDING):
                    pending_buy_orders.append(order)
            
            if not pending_buy_orders:
                return False, f"No pending BUY orders found for {symbol}", None
            
            # Check status of the first (should be only) BUY order
            buy_order = pending_buy_orders[0]
            
            if self.check_order_status(buy_order):
                if buy_order.status == OrderStatus.FILLED:
                    log_event("BUY_CONFIRMED", f"BUY order confirmed and filled for {symbol}",
                             order_id=buy_order.order_id, elapsed_time=time.time() - start_time)
                    return True, "BUY order confirmed and filled", buy_order
                elif buy_order.status == OrderStatus.REJECTED:
                    log_event("BUY_REJECTED", f"BUY order rejected for {symbol}",
                             order_id=buy_order.order_id, reason=buy_order.rejection_reason)
                    return False, f"BUY order rejected: {buy_order.rejection_reason}", buy_order
            
            time.sleep(1)  # Check every second
        
        log_event("BUY_TIMEOUT", f"BUY order confirmation timeout for {symbol}", timeout=timeout)
        return False, f"BUY order confirmation timeout ({timeout}s)", None
    
    def get_order_book(self) -> List[Dict[str, Any]]:
        """
        Fetch complete order book from broker
        
        Returns:
            List of order dictionaries with fields:
            - orderid: Order ID
            - symbol/tradingsymbol: Symbol name
            - ordertype: Order type (MARKET, LIMIT, STOPLOSS_MARKET, etc.)
            - status: Order status (open, complete, rejected, cancelled, trigger pending)
            - producttype: Product type (INTRADAY, DELIVERY, CARRYFORWARD)
            - transactiontype: BUY or SELL
            - triggerprice: Trigger price for SL orders
            - price: Limit price
            - averageprice: Filled price
            - quantity: Order quantity
            - filledshares: Filled quantity
        """
        try:
            order_history = self._safe_api_call(self.smart_api.orderBook, timeout=5.0)
            
            if order_history and order_history.get('status'):
                orders = order_history.get('data', [])
                log_event("ORDER_BOOK_FETCH", f"Fetched {len(orders)} orders from broker")
                return orders
            else:
                log_event("ORDER_BOOK_FETCH_FAILED", "Failed to fetch order book", 
                         response=order_history)
                return []
                
        except Exception as e:
            log_event("ERROR", f"Error fetching order book: {str(e)}", 
                     traceback=traceback.format_exc())
            return []
    
    def find_pending_sl_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Find all pending SL orders for a symbol
        
        Args:
            symbol: Symbol to search for
            
        Returns:
            List of pending SL order dictionaries
        """
        try:
            order_book = self.get_order_book()
            pending_sl_orders = []
            
            for order in order_book:
                order_symbol = order.get('tradingsymbol') or order.get('symbol', '')
                order_type = order.get('ordertype', '').upper()
                status = order.get('status', '').lower()
                transaction_type = order.get('transactiontype', '').upper()
                
                # Match: SELL + STOPLOSS type + pending status
                if (order_symbol == symbol and 
                    transaction_type == 'SELL' and
                    'STOPLOSS' in order_type and
                    status in ['open', 'trigger pending', 'pending']):
                    pending_sl_orders.append(order)
                    log_event("PENDING_SL_FOUND", f"Found pending SL for {symbol}", 
                             order_id=order.get('orderid'), 
                             trigger_price=order.get('triggerprice'),
                             status=status)
            
            return pending_sl_orders
            
        except Exception as e:
            log_event("ERROR", f"Error finding pending SL orders: {str(e)}", 
                     traceback=traceback.format_exc())
            return []
    
    def wait_for_order_confirmation(self, order: Order, timeout: int = 30) -> bool:
        """
        Wait for order confirmation with timeout and comprehensive logging
        
        Args:
            order: Order to wait for
            timeout: Timeout in seconds
            
        Returns:
            True if order confirmed/filled, False if timeout or rejection
        """
        from .bot_logging import log_order, log_error
        
        start_time = time.time()
        last_status = order.status
        check_count = 0
        
        log_order("WAITING_CONFIRMATION", order.symbol, order.action, 
                 order.quantity, order.price, order_id=order.order_id,
                 api_response={"timeout": timeout, "start_time": start_time})
        
        while time.time() - start_time < timeout:
            check_count += 1
            
            # Check order status
            status_updated = self.check_order_status(order)
            elapsed_time = time.time() - start_time
            
            # Log status changes
            if order.status != last_status:
                log_order("STATUS_CHANGE", order.symbol, order.action,
                         order.quantity, order.price, order_id=order.order_id,
                         api_response={
                             "old_status": last_status,
                             "new_status": order.status,
                             "elapsed_time": elapsed_time,
                             "check_count": check_count
                         })
                last_status = order.status
            
            # Order confirmed/filled
            if order.status == OrderStatus.FILLED:
                log_order("CONFIRMED", order.symbol, order.action,
                         order.quantity, order.price, order_id=order.order_id,
                         api_response={
                             "final_status": order.status,
                             "confirmation_time": elapsed_time,
                             "total_checks": check_count
                         })
                return True
            
            # Order rejected or cancelled
            if order.status in [OrderStatus.REJECTED, OrderStatus.CANCELLED]:
                log_order("REJECTED", order.symbol, order.action,
                         order.quantity, order.price, order_id=order.order_id,
                         error=f"Order {order.status}: {getattr(order, 'rejection_reason', 'Unknown reason')}",
                         api_response={
                             "final_status": order.status,
                             "elapsed_time": elapsed_time,
                             "total_checks": check_count
                         })
                return False
            
            time.sleep(1)  # Check every second
        
        # Timeout occurred
        final_elapsed = time.time() - start_time
        log_order("TIMEOUT", order.symbol, order.action,
                 order.quantity, order.price, order_id=order.order_id,
                 error=f"Order confirmation timeout after {final_elapsed:.1f}s",
                 api_response={
                     "timeout_duration": final_elapsed,
                     "total_checks": check_count,
                     "final_status": order.status
                 })
        
        log_event("ORDER", f"Order confirmation timeout", order_id=order.order_id)
        return False
    
    def has_pending_order(self, symbol: str, action: str = None) -> bool:
        """
        Check if there's a pending order for the symbol
        
        Args:
            symbol: Symbol to check
            action: Optional - specific action to check (BUY, SELL, SL)
            
        Returns:
            True if pending order exists, False otherwise
        """
        for order in self.pending_orders.values():
            if order.symbol == symbol:
                # Check if order is still pending
                if order.status == OrderStatus.PENDING:
                    # If action specified, check for specific action
                    if action is None or order.action == action:
                        return True
        return False
    
    def has_pending_buy_order(self, symbol: str) -> bool:
        """
        Check specifically for pending BUY orders for a symbol
        
        Args:
            symbol: Symbol to check
            
        Returns:
            True if pending BUY order exists, False otherwise
        """
        return self.has_pending_order(symbol, "BUY")
    
    def get_pending_orders_for_symbol(self, symbol: str) -> List[Order]:
        """
        Get all pending orders for a symbol
        
        Args:
            symbol: Symbol to check
            
        Returns:
            List of pending Order objects
        """
        pending_orders = []
        for order in self.pending_orders.values():
            if order.symbol == symbol and order.status == OrderStatus.PENDING:
                pending_orders.append(order)
        return pending_orders
    
    def can_place_order(self, symbol: str, action: str) -> Tuple[bool, str]:
        """
        Check if it's safe to place an order for the symbol with comprehensive race condition prevention
        
        Args:
            symbol: Symbol to check
            action: Intended action (BUY, SELL, SL)
            
        Returns:
            Tuple of (can_place, reason)
        """
        # Get all pending orders for this symbol
        pending_orders = self.get_pending_orders_for_symbol(symbol)
        
        if pending_orders:
            # Check for specific race conditions
            for pending_order in pending_orders:
                if action == "BUY":
                    # Can't place another BUY if there's any pending order
                    return False, f"Pending {pending_order.action} order exists for {symbol} (Order ID: {pending_order.order_id})"
                
                elif action in ["SELL", "SL", "EXIT"]:
                    # 🚨 CRITICAL: Can't place SELL/SL if BUY is still pending
                    if pending_order.action == "BUY":
                        return False, f"Cannot place {action} order - BUY order still pending confirmation for {symbol} (Order ID: {pending_order.order_id})"
                    
                    # Can't place multiple SELL orders
                    if pending_order.action in ["SELL", "SL"]:
                        return False, f"Pending {pending_order.action} order already exists for {symbol} (Order ID: {pending_order.order_id})"
        
        # Additional checks for SELL/SL orders - ensure we actually have a position
        if action in ["SELL", "SL", "EXIT"]:
            # This should be coordinated with the position management system
            # For now, we'll rely on the calling code to verify position existence
            pass
        
        return True, "OK"
    
    def _get_symbol_lock(self, symbol: str) -> threading.Lock:
        """
        Get or create a lock for a specific symbol to prevent race conditions
        
        Args:
            symbol: Symbol to get lock for
            
        Returns:
            Threading lock for the symbol
        """
        with self.symbol_lock_manager:
            if symbol not in self.symbol_locks:
                self.symbol_locks[symbol] = threading.Lock()
            return self.symbol_locks[symbol]
    
    def place_order_safe(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float = 0
    ) -> Optional[Order]:
        """
        Safely place an order with symbol-level locking to prevent race conditions
        
        Args:
            symbol: Stock symbol
            action: BUY, SELL, SL
            quantity: Number of shares
            price: Price (0 for market orders)
            
        Returns:
            Order object if successful, None otherwise
        """
        # Get symbol-specific lock to prevent same-symbol race conditions
        symbol_lock = self._get_symbol_lock(symbol)
        
        with symbol_lock:
            # Check per-symbol cooldown to avoid retry storms
            cooldown_until = self.symbol_cooldowns.get(symbol, 0)
            if time.time() < cooldown_until:
                log_event("ORDER_BLOCKED_COOLDOWN", f"Order blocked due to cooldown", symbol=symbol, action=action,
                         cooldown_until=cooldown_until)
                return None
            # Check if it's safe to place order (now with symbol-level protection)
            can_place, reason = self.can_place_order(symbol, action)
            if not can_place:
                log_event("ORDER_BLOCKED", f"Order blocked: {reason}",
                         symbol=symbol, action=action)
                return None
            # Place the order
            order = self.place_order(symbol, action, quantity, price)

            # If order placement failed, set/increase cooldown for this symbol
            if order is None:
                prev_backoff = self.symbol_backoffs.get(symbol, self.backoff_base_seconds)
                # exponential backoff (double), but ensure at least base
                new_backoff = min(max(self.backoff_base_seconds, int(prev_backoff * 2)), self.backoff_max_seconds)
                # if prev_backoff was base and this is first failure, keep it as base
                if prev_backoff == self.backoff_base_seconds:
                    new_backoff = self.backoff_base_seconds

                # store values
                self.symbol_backoffs[symbol] = new_backoff
                self.symbol_cooldowns[symbol] = time.time() + new_backoff

                log_event("ORDER_COOLDOWN_SET", f"Set cooldown for symbol due to failed order",
                         symbol=symbol, backoff_seconds=new_backoff)
            else:
                # success -> reset any backoff
                if symbol in self.symbol_backoffs:
                    self.symbol_backoffs[symbol] = self.backoff_base_seconds
                if symbol in self.symbol_cooldowns:
                    self.symbol_cooldowns[symbol] = 0

            return order
    
    @rate_limited(call_type="ltp", timeout=10.0)
    def get_ltp(self, symbol: str) -> Optional[float]:
        """
        Get Last Traded Price for a symbol with AG8001 retry logic
        
        Args:
            symbol: Stock symbol
            
        Returns:
            LTP or None if not available
        """
        if DevConfig.is_paper_trading():
            # Return mock price for paper trading
            return DevConfig.MOCK_PRICES.get(symbol, 0.0)
        
        if not self.ensure_session():
            return None
        
        try:
            token = self.get_instrument_token(symbol)
            if not token:
                return None
            
            # Get LTP from AngelOne with rate limiting and AG8001 retry
            ltp_data = self._safe_api_call_with_ag8001_retry(
                self.smart_api.ltpData, 
                AngelOneConfig.EXCHANGE, 
                symbol.replace("-EQ", ""), 
                token,
                max_retries=2,
                initial_backoff=0.5,
                timeout=3.0
            )
            
            if ltp_data and ltp_data.get('status'):
                ltp = float(ltp_data['data']['ltp'])
                return ltp
            
            return None
            
        except Exception as e:
            log_event("ERROR", f"Error getting LTP for {symbol}: {str(e)}")
            return None
    
    @rate_limited(call_type="ltp", timeout=10.0)
    def get_ltp_bulk(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """
        Get LTP for multiple symbols in fewer API calls using bulk marketData endpoint.
        
        OPTIMIZATION: Instead of calling get_ltp() N times (N API calls), this fetches
        up to 50 symbols in a single request, dramatically reducing API usage and rate limiting.
        
        Args:
            symbols: List of stock symbols (e.g., ["RELIANCE-EQ", "INFY-EQ"])
        
        Returns:
            Dictionary mapping symbol -> LTP value (None if not available)
        
        Example:
            ltps = broker.get_ltp_bulk(["RELIANCE-EQ", "INFY-EQ", "TECHM-EQ"])
            # Returns: {"RELIANCE-EQ": 2945.5, "INFY-EQ": 1850.3, "TECHM-EQ": 1580.2}
        """
        if not symbols:
            return {}
        
        if DevConfig.is_paper_trading():
            # Return mock prices for paper trading
            return {sym: DevConfig.MOCK_PRICES.get(sym, 0.0) for sym in symbols}
        
        if not self.ensure_session():
            return {sym: None for sym in symbols}
        
        try:
            # Update bulk fetcher with current smart_api instance
            if self.bulk_ltp_fetcher and not self.bulk_ltp_fetcher.smart_api:
                self.bulk_ltp_fetcher.smart_api = self.smart_api
            
            # Build token dictionary, grouped by exchange
            token_dict = {}
            symbol_to_token = {}
            
            for symbol in symbols:
                # Determine exchange based on symbol suffix
                if "-EQ" in symbol or "NSE" in symbol.upper():
                    exchange = "NSE"
                elif "NFO" in symbol.upper() or any(x in symbol for x in ["DEC", "JAN", "FEB", "MAR"]):
                    exchange = "NFO"
                else:
                    # Default to NSE for safety
                    exchange = "NSE"
                
                token = self.get_instrument_token(symbol)
                if not token:
                    log_event("BULK_LTP_NO_TOKEN", f"No token found for {symbol}")
                    continue
                
                if exchange not in token_dict:
                    token_dict[exchange] = []
                
                token_dict[exchange].append(token)
                symbol_to_token[token] = symbol
            
            if not token_dict:
                log_event("BULK_LTP_NO_TOKENS", f"Could not map any symbols to tokens")
                return {sym: None for sym in symbols}
            
            log_event("BULK_LTP_FETCH", f"Fetching LTP for {len(symbol_to_token)} symbols",
                     exchanges=list(token_dict.keys()), symbol_count=len(symbol_to_token))
            
            # Fetch bulk LTP with batching for >50 symbols
            ltp_by_token = self.bulk_ltp_fetcher.fetch_bulk_ltp_batched(token_dict)
            
            # Convert back to symbol mapping
            result = {sym: None for sym in symbols}
            for token_key, ltp in ltp_by_token.items():
                parts = token_key.split("_", 1)
                if len(parts) == 2:
                    token = parts[1]
                    symbol = symbol_to_token.get(token)
                    if symbol:
                        result[symbol] = float(ltp)
            
            log_event("BULK_LTP_SUCCESS", f"Successfully fetched LTP for {sum(1 for v in result.values() if v)} symbols")
            
            return result
        
        except Exception as e:
            log_event("BULK_LTP_ERROR", f"Error fetching bulk LTP: {str(e)}", error=str(e))
            return {sym: None for sym in symbols}
    
    def get_rate_limiter_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics (compat shim)

        See `get_rate_limiter_stats` earlier for rationale.
        """
        if hasattr(self.rate_limiter, "get_statistics"):
            return self.rate_limiter.get_statistics()
        if hasattr(self.rate_limiter, "get_stats"):
            return self.rate_limiter.get_stats()
        try:
            return {"info": str(self.rate_limiter)}
        except Exception:
            return {"info": "unavailable"}
    
    def reset_rate_limiter_stats(self):
        """Reset rate limiter statistics (compat shim)"""
        if hasattr(self.rate_limiter, "reset_statistics"):
            self.rate_limiter.reset_statistics()
            log_event("RATE_LIMITER", "Rate limiter statistics reset")
            return
        if hasattr(self.rate_limiter, "reset_stats"):
            self.rate_limiter.reset_stats()
            log_event("RATE_LIMITER", "Rate limiter statistics reset (legacy)")
            return
        log_event("RATE_LIMITER", "Rate limiter reset not supported on this implementation")

    def get_user_profile(self) -> Optional[Dict[str, Any]]:
        """Get user profile information from broker"""
        try:
            # Use the SmartConnect instance to get profile
            if hasattr(self.smart_api, 'getProfile') and self.smart_api and self.refresh_token:
                profile = self.smart_api.getProfile(self.refresh_token)
                if profile:
                    log_event("USER_PROFILE", f"Retrieved profile for user: {profile.get('name', 'Unknown')}")
                    return profile
            
            # Fallback: return basic info if profile not available
            log_event("USER_PROFILE", "Profile method not available, returning basic info")
            return {
                "name": "Trading User",
                "status": "active" if self.is_logged_in() else "inactive",
                "broker": "AngelOne",
                "session_active": bool(self.session_token)
            }
            
        except Exception as e:
            log_event("ERROR", f"Error getting user profile: {str(e)}")
            # Still return fallback info to show the session is working
            return {
                "name": "Trading User",
                "status": "active" if self.is_logged_in() else "inactive",
                "broker": "AngelOne",
                "session_active": bool(self.session_token),
                "profile_error": str(e)
            }

    @rate_limited(call_type="historical", timeout=15.0)
    def get_historical_data(self, symbol: str, interval: str = "ONE_MINUTE", 
                          days_back: int = 5) -> Optional[List[Dict]]:
        """
        Get historical candlestick data for a symbol (with caching)
        
        Args:
            symbol: Stock symbol (e.g., 'RELIANCE-EQ')
            interval: Time interval ('ONE_MINUTE', 'FIVE_MINUTE', 'FIFTEEN_MINUTE', 'ONE_HOUR', 'ONE_DAY')
            days_back: Number of days of historical data to fetch
            
        Returns:
            List of OHLC data or None if failed
            Format: [{'timestamp': str, 'open': float, 'high': float, 'low': float, 'close': float, 'volume': int}]
        """
        try:
            # Try to get cached data first
            from .data_cache import get_cache
            cache = get_cache()
            
            cached_data = cache.get_cached_historical_data(symbol, interval, days_back)
            if cached_data:
                log_event("HISTORICAL", f"Retrieved cached {len(cached_data)} candles for {symbol}")
                return cached_data
        
        except Exception as e:
            log_event("WARNING", f"Cache lookup failed for {symbol}: {e}")
        
        # Cache miss - fetch fresh data
        if not self.ensure_session():
            return None
        
        try:
            token = self.get_instrument_token(symbol)
            if not token:
                log_event("ERROR", f"Cannot get historical data: token not found for {symbol}")
                return None
            
            # Calculate date range
            from datetime import datetime, timedelta
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            
            # Format dates for API (assuming YYYY-MM-DD HH:MM format)
            from_date = start_date.strftime("%Y-%m-%d 09:15")
            to_date = end_date.strftime("%Y-%m-%d 15:30")
            
            # Prepare historical data parameters
            historic_params = {
                "exchange": AngelOneConfig.EXCHANGE,
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_date,
                "todate": to_date
            }
            
            log_event("HISTORICAL", f"Fetching {days_back}d {interval} data for {symbol}")
            
            # Get historical data from AngelOne API
            response = self._safe_api_call(
                self.smart_api.getCandleData,
                historic_params,
                timeout=15.0
            )
            
            # Handle invalid/expired token edge-case by attempting one refresh+retry
            if not response or not response.get('status'):
                # Some SmartAPI responses use 'errorCode' or 'message' instead of 'status'
                err_code = response.get('errorCode') if isinstance(response, dict) else None
                err_msg = response.get('message') if isinstance(response, dict) else None
                if err_code == 'AG8001' or (isinstance(err_msg, str) and 'Invalid Token' in err_msg):
                    log_event("SESSION", f"Historical data failed due to invalid token, attempting session refresh for {symbol}")
                    # Try refresh/login and retry once
                    if self._refresh_session_if_needed():
                        response = self._safe_api_call(
                            self.smart_api.getCandleData,
                            historic_params,
                            timeout=15.0
                        )

            if not response or not response.get('status'):
                log_event("ERROR", f"Historical data API failed for {symbol}: {response}")
                return None
            
            # Parse the response data
            candle_data = response.get('data', [])
            if not candle_data:
                log_event("WARNING", f"No historical data returned for {symbol}")
                return None
            
            # Convert to standardized format
            formatted_data = []
            for candle in candle_data:
                try:
                    # AngelOne format: [timestamp, open, high, low, close, volume]
                    formatted_candle = {
                        'timestamp': candle[0],
                        'open': float(candle[1]),
                        'high': float(candle[2]), 
                        'low': float(candle[3]),
                        'close': float(candle[4]),
                        'volume': int(candle[5]) if len(candle) > 5 else 0
                    }
                    formatted_data.append(formatted_candle)
                except (IndexError, ValueError) as e:
                    log_event("ERROR", f"Error parsing candle data: {e}")
                    continue
            
            # Cache the fetched data
            try:
                cache = get_cache()
                cache.cache_historical_data(symbol, interval, days_back, formatted_data)
                log_event("HISTORICAL", f"Cached {len(formatted_data)} candles for {symbol}")
            except Exception as e:
                log_event("WARNING", f"Failed to cache data for {symbol}: {e}")
            
            log_event("HISTORICAL", f"Successfully fetched {len(formatted_data)} candles for {symbol}")
            return formatted_data
            
        except Exception as e:
            log_event("ERROR", f"Error getting historical data for {symbol}: {str(e)}")
            return None

    def calculate_technical_indicators(self, historical_data: List[Dict]) -> Dict[str, float]:
        """
        Calculate technical indicators from historical OHLC data
        
        Args:
            historical_data: List of OHLC candles from get_historical_data()
            
        Returns:
            Dictionary with calculated indicators
        """
        if not historical_data or len(historical_data) < 20:
            return {}
        
        try:
            # Extract price arrays
            closes = [candle['close'] for candle in historical_data]
            highs = [candle['high'] for candle in historical_data]
            lows = [candle['low'] for candle in historical_data]
            volumes = [candle['volume'] for candle in historical_data]
            
            indicators = {}
            
            # RSI (14-period)
            if len(closes) >= 15:
                indicators['rsi'] = self._calculate_rsi(closes, 14)
            
            # Simple Moving Averages
            if len(closes) >= 20:
                indicators['sma_20'] = sum(closes[-20:]) / 20
            if len(closes) >= 50:
                indicators['sma_50'] = sum(closes[-50:]) / 50
            
            # Exponential Moving Average (20-period)
            if len(closes) >= 20:
                indicators['ema_20'] = self._calculate_ema(closes, 20)
            
            # VWAP (Volume Weighted Average Price)
            if volumes and len(volumes) == len(closes):
                indicators['vwap'] = self._calculate_vwap(closes, volumes)
            
            # ATR (Average True Range, 14-period)
            if len(historical_data) >= 15:
                indicators['atr'] = self._calculate_atr(highs, lows, closes, 14)
            
            # Bollinger Bands (20-period, 2 std dev)
            if len(closes) >= 20:
                bb_middle, bb_upper, bb_lower = self._calculate_bollinger_bands(closes, 20, 2)
                indicators['bb_middle'] = bb_middle
                indicators['bb_upper'] = bb_upper
                indicators['bb_lower'] = bb_lower
                indicators['bb_position'] = (closes[-1] - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
            
            # Current price vs indicators
            current_price = closes[-1]
            indicators['current_price'] = current_price
            
            # Price position indicators
            if 'sma_20' in indicators:
                indicators['price_vs_sma20'] = ((current_price - indicators['sma_20']) / indicators['sma_20']) * 100
            
            log_event("TECHNICAL", f"Calculated {len(indicators)} indicators")
            return indicators
            
        except Exception as e:
            log_event("ERROR", f"Error calculating technical indicators: {str(e)}")
            return {}

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate RSI indicator"""
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

    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return sum(prices) / len(prices)
        
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # Start with SMA
        
        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        
        return round(ema, 2)

    def _calculate_vwap(self, prices: List[float], volumes: List[int]) -> float:
        """Calculate Volume Weighted Average Price"""
        if len(prices) != len(volumes) or not volumes:
            return sum(prices) / len(prices)
        
        total_volume = sum(volumes)
        if total_volume == 0:
            return sum(prices) / len(prices)
        
        weighted_sum = sum(price * volume for price, volume in zip(prices, volumes))
        return round(weighted_sum / total_volume, 2)

    def _calculate_atr(self, highs: List[float], lows: List[float], 
                      closes: List[float], period: int = 14) -> float:
        """Calculate Average True Range"""
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
            return sum(true_ranges) / len(true_ranges)
        
        return round(sum(true_ranges[-period:]) / period, 2)

    def _calculate_bollinger_bands(self, prices: List[float], period: int = 20, 
                                  std_dev: int = 2) -> tuple:
        """Calculate Bollinger Bands"""
        if len(prices) < period:
            avg = sum(prices) / len(prices)
            return avg, avg, avg
        
        # Calculate middle band (SMA)
        sma = sum(prices[-period:]) / period
        
        # Calculate standard deviation
        variance = sum((price - sma) ** 2 for price in prices[-period:]) / period
        std = variance ** 0.5
        
        # Calculate upper and lower bands
        upper_band = sma + (std * std_dev)
        lower_band = sma - (std * std_dev)
        
        return round(sma, 2), round(upper_band, 2), round(lower_band, 2)

    def get_atr(self, symbol: str, period: int = 14, interval: str = "ONE_MINUTE") -> Optional[float]:
        """
        Get ATR (Average True Range) for a symbol - used for dynamic position sizing and SL
        
        Args:
            symbol: Stock symbol
            period: ATR period (default: 14)
            interval: Time interval for historical data (default: ONE_MINUTE)
            
        Returns:
            ATR value or None if calculation fails
        """
        try:
            # Get recent historical data
            historical_data = self.get_historical_data(symbol, interval=interval)
            
            if not historical_data or len(historical_data) < period + 1:
                log_event("WARNING", f"Insufficient data for ATR calculation: {symbol} ({len(historical_data) if historical_data else 0} candles)")
                return None
            
            # Extract OHLC data
            highs = [candle['high'] for candle in historical_data]
            lows = [candle['low'] for candle in historical_data]
            closes = [candle['close'] for candle in historical_data]
            
            # Calculate and return ATR
            atr = self._calculate_atr(highs, lows, closes, period)
            log_event("TECHNICAL", f"Calculated ATR for {symbol}: {atr} (period: {period})")
            return atr
            
        except Exception as e:
            log_event("ERROR", f"Error calculating ATR for {symbol}: {str(e)}")
            return None

    def get_enhanced_analytics(self, symbol: str, interval: str = "FIVE_MINUTE", 
                             days_back: int = 5) -> Dict[str, Any]:
        """
        Get comprehensive analytics combining historical data and technical indicators
        
        Args:
            symbol: Stock symbol
            interval: Time interval for historical data
            days_back: Days of historical data to analyze
            
        Returns:
            Dictionary with historical data, technical indicators, and analytics summary
        """
        # Import at function level BEFORE any try/except blocks
        from .bot_logging import log_analytics, log_event
        from .data_cache import get_cache
        
        try:
            # Try to get cached technical indicators first
            cache = get_cache()
            
            cached_data = cache.get_cached_technical_indicators(symbol, interval, days_back)
            if cached_data:
                # Use cached indicators but add fresh metadata
                indicators = cached_data['indicators']
                signals = cached_data['signals']
                
                analytics = {
                    "symbol": symbol,
                    "interval": interval,
                    "data_points": indicators.get('data_points', 0),
                    "current_price": indicators.get('current_price', 0),
                    "technical_indicators": indicators,
                    "signals": signals,
                    "last_updated": datetime.now().isoformat(),
                    "cached": True,
                    "request": {
                        "symbol": symbol,
                        "interval": interval,
                        "days_back": days_back,
                        "timestamp": datetime.now().isoformat()
                    }
                }
                
                log_analytics("CACHE_HIT", symbol=symbol,
                             calculation_type="enhanced_analytics", 
                             result={"cached": True, "age_seconds": cache_age_seconds})
                return analytics
            
            # Cache miss - fetch fresh data
            log_analytics("CACHE_MISS", symbol=symbol, 
                         calculation_type="enhanced_analytics",
                         details={"interval": interval, "days_back": days_back})
            
            # Get historical data (with caching)
            log_analytics("DATA_FETCH_START", symbol=symbol,
                         calculation_type="historical_data",
                         details={"interval": interval, "days_back": days_back})
            
            historical_data = self.get_historical_data(symbol, interval, days_back)
            if not historical_data:
                log_analytics("DATA_FETCH_FAILED", symbol=symbol,
                             calculation_type="historical_data",
                             error="Failed to fetch historical data")
                return {"error": "Failed to fetch historical data"}
            
            log_analytics("DATA_FETCH_SUCCESS", symbol=symbol,
                         calculation_type="historical_data",
                         result={"data_points": len(historical_data)})
            
            # Calculate technical indicators
            log_analytics("INDICATOR_CALC_START", symbol=symbol,
                         calculation_type="technical_indicators",
                         details={"data_points": len(historical_data)})
            
            indicators = self.calculate_technical_indicators(historical_data)
            if not indicators:
                log_analytics("INDICATOR_CALC_FAILED", symbol=symbol,
                             calculation_type="technical_indicators",
                             error="Failed to calculate technical indicators")
                return {"error": "Failed to calculate technical indicators"}
            
            log_analytics("INDICATOR_CALC_SUCCESS", symbol=symbol,
                         calculation_type="technical_indicators",
                         result={"indicators_count": len(indicators)})
            
            # Add data points to indicators
            indicators['data_points'] = len(historical_data)
            
            # Generate analytics summary
            current_price = indicators.get('current_price', 0)
            rsi = indicators.get('rsi', 50)
            bb_position = indicators.get('bb_position', 0.5)
            
            # Create signals
            signals = {
                "rsi_signal": "OVERSOLD" if rsi < 30 else "OVERBOUGHT" if rsi > 70 else "NEUTRAL",
                "bb_signal": "LOWER" if bb_position < 0.2 else "UPPER" if bb_position > 0.8 else "MIDDLE",
                "trend_signal": "BULLISH" if indicators.get('price_vs_sma20', 0) > 0 else "BEARISH"
            }
            
            # Cache the calculated indicators and signals
            cache.cache_technical_indicators(symbol, interval, days_back, indicators, signals)
            
            # Create analytics summary
            analytics = {
                "symbol": symbol,
                "interval": interval,
                "data_points": len(historical_data),
                "current_price": current_price,
                "technical_indicators": indicators,
                "signals": signals,
                "last_updated": datetime.now().isoformat(),
                "cached": False,
                "historical_data_sample": historical_data[-5:] if len(historical_data) >= 5 else historical_data,
                "request": {
                    "symbol": symbol,
                    "interval": interval,
                    "days_back": days_back,
                    "timestamp": datetime.now().isoformat()
                }
            }
            
            log_analytics("ANALYTICS_COMPLETE", symbol=symbol,
                         calculation_type="enhanced_analytics",
                         result={"indicators_calculated": len(indicators),
                                "signals_generated": len(signals),
                                "data_points": len(historical_data)},
                         performance_metrics={"cache_status": "MISS"})
            
            return analytics
            
        except Exception as e:
            log_analytics("ANALYTICS_ERROR", symbol=symbol,
                         calculation_type="enhanced_analytics",
                         error=str(e))
            log_event("ERROR", f"Error generating enhanced analytics for {symbol}: {str(e)}")
            return {"error": str(e)}

    def _classify_order_rejection(self, error_msg: str, error_code: Optional[str] = None, 
                                 symbol: str = None) -> str:
        """
        Classify order rejection reasons to identify scrutiny, blacklist, observation status.
        
        Args:
            error_msg: Error message from broker
            error_code: Error code from broker (optional)
            symbol: Symbol being traded (optional, for logging)
            
        Returns:
            Classification type string:
            - SCRUTINY: Symbol under regulatory scrutiny (NSE/BSE action)
            - BLACKLIST: Symbol blacklisted by broker
            - OBSERVATION: Symbol under observation (caution list)
            - TRADING_HALT: Trading halted on symbol
            - CIRCUIT_BREAKER: Upper/lower circuit limit
            - RATE_LIMITED: API rate limited
            - SESSION_ERROR: Session/auth error
            - INSUFFICIENT_FUNDS: Not enough capital
            - INSTRUMENT_ERROR: Invalid/unavailable instrument
            - API_ERROR: Generic API error
            - UNKNOWN: Unclassified error
        """
        if not error_msg:
            return "UNKNOWN"
        
        error_lower = error_msg.lower()
        
        # ===== ERROR CODE BASED CLASSIFICATION (Most reliable) =====
        # Check error codes first - these are definitive
        if error_code:
            error_code_upper = error_code.upper()
            
            # AB4036: Token categorised under cautionary listings by exchange
            if error_code_upper == 'AB4036':
                return "OBSERVATION"
            
            # AG8001: Invalid/expired token
            if error_code_upper == 'AG8001':
                return "SESSION_ERROR"
            
            # AG8002: Rate limit exceeded
            if error_code_upper == 'AG8002':
                return "RATE_LIMITED"
            
            # AB1007: Qty/tick size/price range errors (NOT fund-related despite AB1 prefix)
            if error_code_upper == 'AB1007':
                return "INSTRUMENT_ERROR"
            
            # AB2xxx: Order-related errors (quantity, price, etc.)
            if error_code_upper.startswith('AB2'):
                return "INSTRUMENT_ERROR"
            
            # AB1xxx: Fund/margin related errors (but exclude AB1007 handled above)
            if error_code_upper.startswith('AB1'):
                return "INSUFFICIENT_FUNDS"
        
        # ===== SCRUTINY / BLACKLIST / OBSERVATION DETECTION =====
        
        # Scrutiny patterns - NSE/BSE regulatory action
        scrutiny_patterns = [
            'under scrutiny', 'regulatory scrutiny', 'nse scrutiny', 'bse scrutiny',
            'scrutinized', 'under examination', 'regulatory examination',
            'under regulatory action', 'regulatory action pending'
        ]
        
        # Blacklist patterns
        blacklist_patterns = [
            'blacklist', 'blacklisted', 'in blacklist', 'blacklist list',
            'suspended symbol', 'symbol suspended', 'banned symbol', 'symbol banned',
            'delisted', 'de-list', 'removal from listing'
        ]
        
        # Observation/Caution patterns
        observation_patterns = [
            'under observation', 'observation list', 'caution list', 'under caution',
            'monitoring', 'under monitoring', 'watch list', 'on watch',
            'restricted trading', 'restricted symbol', 'restricted for trading'
        ]
        
        # Trading halt patterns
        halt_patterns = [
            'trading halt', 'halted', 'halt', 'trading halted', 'market halt',
            'trading suspended', 'suspended from trading', 'no trading',
            'not available for trading', 'not tradeable'
        ]
        
        # Circuit breaker patterns
        circuit_patterns = [
            'upper circuit', 'lower circuit', 'circuit limit', 'circuit breaker',
            'circuit hit', 'at circuit limit', 'price limit'
        ]
        
        # Rate limit patterns
        rate_patterns = [
            'rate limit', 'rate exceeded', 'api rate', 'request limit', 'too many',
            'access denied', 'throttl', 'request timeout'
        ]
        
        # Session/Auth patterns
        session_patterns = [
            'invalid token', 'token expired', 'session invalid', 'session expired',
            'unauthorized', 'not authenticated', 'auth failed', 'login failed'
        ]
        
        # Funds patterns
        funds_patterns = [
            'insufficient fund', 'not enough balance', 'margin shortfall',
            'capital insuffic', 'exceeds limit', 'exceed margin'
        ]
        
        # Instrument patterns
        instrument_patterns = [
            'token not found', 'invalid instrument', 'unknown instrument',
            'instrument error', 'symbol not found', 'unknown symbol',
            'invalid symbol', 'not available', 'unavailable'
        ]
        
        # Check patterns in order of importance (most critical first)
        if any(p in error_lower for p in scrutiny_patterns):
            return "SCRUTINY"
        
        if any(p in error_lower for p in blacklist_patterns):
            return "BLACKLIST"
        
        if any(p in error_lower for p in observation_patterns):
            return "OBSERVATION"
        
        if any(p in error_lower for p in halt_patterns):
            return "TRADING_HALT"
        
        if any(p in error_lower for p in circuit_patterns):
            return "CIRCUIT_BREAKER"
        
        if any(p in error_lower for p in rate_patterns):
            return "RATE_LIMITED"
        
        if any(p in error_lower for p in session_patterns):
            return "SESSION_ERROR"
        
        if any(p in error_lower for p in funds_patterns):
            return "INSUFFICIENT_FUNDS"
        
        if any(p in error_lower for p in instrument_patterns):
            return "INSTRUMENT_ERROR"
        
        if 'error' in error_lower or 'failed' in error_lower:
            return "API_ERROR"
        
        return "UNKNOWN"


# =============================================================================
# Testing
# =============================================================================

if __name__ == "__main__":
    """Test AngelOne broker functionality"""
    print("=== AngelOne Broker Test ===")
    
    broker = AngelOneBroker()
    
    # Test login
    print("1. Testing login...")
    if broker.login():
        print("✅ Login successful")
    else:
        print("❌ Login failed")
        exit(1)
    
    # Test instrument lookup
    print("2. Testing instrument lookup...")
    token = broker.get_instrument_token("RELIANCE-EQ")
    if token:
        print(f"✅ RELIANCE-EQ token: {token}")
    else:
        print("❌ Token lookup failed")
    
    # Test LTP
    print("3. Testing LTP...")
    ltp = broker.get_ltp("RELIANCE-EQ")
    if ltp:
        print(f"✅ RELIANCE-EQ LTP: {ltp}")
    else:
        print("❌ LTP fetch failed")
    
    # Test order placement (paper mode)
    print("4. Testing order placement...")
    order = broker.place_order_safe("RELIANCE-EQ", "BUY", 10, 2450.50)
    if order:
        print(f"✅ Order placed: {order.order_id}")
    else:
        print("❌ Order placement failed")
    
    # Test historical data (new feature)
    print("5. Testing historical data...")
    historical_data = broker.get_historical_data("RELIANCE-EQ", "FIVE_MINUTE", 2)
    if historical_data:
        print(f"✅ Historical data: {len(historical_data)} candles")
        print(f"   Latest: {historical_data[-1] if historical_data else 'None'}")
    else:
        print("❌ Historical data fetch failed")
    
    # Test technical indicators (new feature)
    print("6. Testing technical indicators...")
    if historical_data:
        indicators = broker.calculate_technical_indicators(historical_data)
        if indicators:
            print(f"✅ Technical indicators calculated: {len(indicators)} indicators")
            print(f"   RSI: {indicators.get('rsi', 'N/A')}")
            print(f"   Current Price: {indicators.get('current_price', 'N/A')}")
        else:
            print("❌ Technical indicators calculation failed")
    
    # Test enhanced analytics (new feature)
    print("7. Testing enhanced analytics...")
    analytics = broker.get_enhanced_analytics("RELIANCE-EQ", "FIVE_MINUTE", 2)
    if analytics and not analytics.get('error'):
        print(f"✅ Enhanced analytics generated")
        print(f"   Data points: {analytics.get('data_points', 'N/A')}")
        print(f"   RSI Signal: {analytics.get('signals', {}).get('rsi_signal', 'N/A')}")
        print(f"   Trend Signal: {analytics.get('signals', {}).get('trend_signal', 'N/A')}")
    else:
        print(f"❌ Enhanced analytics failed: {analytics.get('error', 'Unknown error')}")
    
    print("✅ All tests completed")