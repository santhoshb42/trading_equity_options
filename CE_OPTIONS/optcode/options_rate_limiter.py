"""
Rate Limiter Module - Options Trading Bot

Implements token bucket rate limiting for AngelOne API calls.
AngelOne has strict limits:
- 8 requests per second (options trading is heavier)
- 180 requests per minute  
- Violations can lead to account suspension

This module ensures we never exceed these limits, with request queue
for automatic retry when rate limited.
"""

import time
import threading
import json
import os
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from collections import deque
import fcntl

from .optlogging import logger, log_event

# ── Account-level GLOBAL backstop (defense-in-depth) ──────────────────────────
# Was a single 8/sec · 180/min pool that ALL endpoints shared — which throttled cheap
# LTP/quote calls (real cap 500/min) down to the candle endpoint's 180/min. That global
# pool was the root cause of ~20s alert→order latency. We now enforce per-endpoint budgets
# (below) and keep only a generous global backstop here so the account is never hammered.
REQUESTS_PER_SECOND = 35   # global backstop / sec — set NOT to bind (≈ sum of per-endpoint limits);
REQUESTS_PER_MINUTE = 1500 # per-endpoint caps below are the real gate. (was 18/800 = over-throttled
                           # the aggregate to ~half of what AngelOne allows across 4 bots.)
GLOBAL_PER_SECOND = REQUESTS_PER_SECOND
GLOBAL_PER_MINUTE = REQUESTS_PER_MINUTE
# v2 state file: new per-endpoint schema. Renamed so a half-restarted fleet (mixed old/new
# code) never reads an incompatible schema from the same file.
SHARED_RATE_LIMIT_STATE_FILE = "/tmp/angelone_options_shared_rate_limit_v2.json"

# Reserve a slice of the GLOBAL backstop for trading-critical operations so low-value
# background work cannot starve live trading and position protection.
CRITICAL_SECOND_RESERVE = 3
CRITICAL_MINUTE_RESERVE = 60

# ── PER-ENDPOINT limits (the real fix) ────────────────────────────────────────
# Set BELOW AngelOne's documented per-client caps (verified against the SmartAPI rate-limit
# doc): getLtpData 10/s·500/min ; getMarketData(FULL)/quote 10/s·500/min ;
# getCandleData 3/s·180/min ; order APIs 20/s·500/min. Each class is enforced INDEPENDENTLY
# across all 4 bots via the shared file lock — so LTP/quote no longer wait behind candle quota.
ENDPOINT_LIMITS = {
    # class:   (per_second, per_minute) — AngelOne documented per-endpoint caps with a thin margin.
    # Shared across all 4 bots (CE+PE) on the one account via the file lock. PE carries more load,
    # so these are account-wide budgets, not per-bot.
    'ltp':    (10, 490),   # getLtpData: 10/s, 500/min
    'quote':  (10, 490),   # getMarketData (LTP/OHLC/FULL): 10/s, 500/min
    'candle': (3, 178),    # getCandleData: 3/s, 180/min
    'order':  (19, 490),   # order APIs: 20/s, 500/min
}
DEFAULT_ENDPOINT_LIMIT = (6, 200)  # conservative cap for any unmapped request_type

# request_type prefix → endpoint class (request_type strings come from angelone_options.py)
ENDPOINT_CLASS_PREFIXES = (
    ('bulk_ltp_quote', 'ltp'),
    ('ltp_fetch', 'ltp'),
    ('fetch_chain', 'ltp'),
    ('market_data', 'quote'),
    ('oi_fetch', 'quote'),
    ('pcr_oi', 'quote'),
    ('historical', 'candle'),
    ('place_order', 'order'),
    ('modify_order', 'order'),
    ('modify_sl', 'order'),
    ('cancel_order', 'order'),
    ('close_position', 'order'),
)

CRITICAL_REQUEST_PREFIXES = (
    "place_order",
    "close_position",
    "modify_order",
    "modify_sl",
    "cancel_order",
    "bulk_ltp_quote",
    "ltp_fetch",
)

# AngelOne caps these endpoints at 1 request/second PER CLIENT (shared across all 4 bots).
# The global 8/sec gate is too loose for them, so they get a dedicated 1/sec sub-limit.
# On denial the callers fall back to their stale cache rather than breaching the API.
ONE_PER_SECOND_PREFIXES = (
    "get_order_book",
    "get_trade_book",
    "get_position",
    "get_holding",
    "search_scrip",
)


def _one_per_second_endpoint(request_type: Optional[str]) -> Optional[str]:
    normalized = _normalize_request_type(request_type)
    for prefix in ONE_PER_SECOND_PREFIXES:
        if normalized.startswith(prefix):
            return prefix
    return None


def _normalize_request_type(request_type: Optional[str]) -> str:
    return (request_type or "api_call").lower()


def _endpoint_class(request_type: Optional[str]) -> str:
    """Map a request_type to its AngelOne endpoint class for per-endpoint rate limiting."""
    normalized = _normalize_request_type(request_type)
    for prefix, cls in ENDPOINT_CLASS_PREFIXES:
        if normalized.startswith(prefix):
            return cls
    return 'default'


def is_critical_request(request_type: Optional[str]) -> bool:
    normalized = _normalize_request_type(request_type)
    return normalized.startswith(CRITICAL_REQUEST_PREFIXES)


class SharedRateLimiterState:
    """Cross-process rate gate for a shared AngelOne API key."""

    def __init__(self, state_file: str = SHARED_RATE_LIMIT_STATE_FILE):
        self.state_file = state_file
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        if not os.path.exists(self.state_file):
            with open(self.state_file, 'w', encoding='ascii') as handle:
                json.dump({'second_calls': [], 'minute_calls': [], 'updated_at': 0}, handle)

    def try_acquire_slot(self, request_type: str) -> tuple[bool, str]:
        now = time.time()
        critical = is_critical_request(request_type)
        ep_class = _endpoint_class(request_type)
        one_per_sec = _one_per_second_endpoint(request_type)

        with open(self.state_file, 'a+', encoding='ascii') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read().strip()

            try:
                state = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                state = {}

            # Global account backstop windows (all endpoints).
            g_sec = [ts for ts in state.get('global_second', []) if now - ts < 1.0]
            g_min = [ts for ts in state.get('global_minute', []) if now - ts < 60.0]
            # AngelOne 1/sec endpoints (order book / trade book / position / scrip).
            endpoint_calls = {
                ep: [ts for ts in calls if now - ts < 1.0]
                for ep, calls in (state.get('endpoint_calls', {}) or {}).items()
            }
            # Per-endpoint-class windows (ltp / quote / candle / order / default).
            class_sec = {
                c: [ts for ts in calls if now - ts < 1.0]
                for c, calls in (state.get('class_second', {}) or {}).items()
            }
            class_min = {
                c: [ts for ts in calls if now - ts < 60.0]
                for c, calls in (state.get('class_minute', {}) or {}).items()
            }

            def deny(reason: str) -> tuple[bool, str]:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False, reason

            # 1) AngelOne 1/sec endpoints — enforce FIRST (hard per-client cap).
            if one_per_sec is not None and len(endpoint_calls.get(one_per_sec, [])) >= 1:
                return deny(f"Shared per-endpoint 1/sec limit reached ({one_per_sec})")

            # 2) Per-endpoint-class limits — the real AngelOne caps, enforced independently
            #    so cheap LTP/quote calls are never throttled by the candle budget.
            sec_lim, min_lim = ENDPOINT_LIMITS.get(ep_class, DEFAULT_ENDPOINT_LIMIT)
            if len(class_sec.get(ep_class, [])) >= sec_lim:
                return deny(f"Per-endpoint {ep_class} limit reached ({sec_lim}/sec)")
            if len(class_min.get(ep_class, [])) >= min_lim:
                return deny(f"Per-endpoint {ep_class} limit reached ({min_lim}/min)")

            # 3) Global account backstop (defense-in-depth), with a reserve for critical ops.
            if len(g_sec) >= GLOBAL_PER_SECOND:
                return deny(f"Global backstop {GLOBAL_PER_SECOND}/sec reached")
            if len(g_min) >= GLOBAL_PER_MINUTE:
                return deny(f"Global backstop {GLOBAL_PER_MINUTE}/min reached")
            if not critical:
                if len(g_sec) >= GLOBAL_PER_SECOND - CRITICAL_SECOND_RESERVE:
                    return deny(f"Global per-second reserve held for critical ({request_type})")
                if len(g_min) >= GLOBAL_PER_MINUTE - CRITICAL_MINUTE_RESERVE:
                    return deny(f"Global per-minute reserve held for critical ({request_type})")

            # Acquire across all relevant windows.
            g_sec.append(now)
            g_min.append(now)
            class_sec.setdefault(ep_class, []).append(now)
            class_min.setdefault(ep_class, []).append(now)
            if one_per_sec is not None:
                endpoint_calls.setdefault(one_per_sec, []).append(now)

            # Keep the file small: drop fully-aged buckets.
            endpoint_calls = {ep: c for ep, c in endpoint_calls.items() if c}
            class_sec = {c: v for c, v in class_sec.items() if v}
            class_min = {c: v for c, v in class_min.items() if v}

            handle.seek(0)
            handle.truncate()
            json.dump({
                'global_second': g_sec,
                'global_minute': g_min,
                'endpoint_calls': endpoint_calls,
                'class_second': class_sec,
                'class_minute': class_min,
                'updated_at': now,
            }, handle)
            handle.flush()
            # NO os.fsync — this is an ephemeral /tmp coordination file. flush() makes the write
            # visible to other processes via the page cache (which is all the flock peers need).
            # fsync forces a DISK write under the exclusive lock — ~ms each — and was serializing
            # every parallel alert thread on the rate limiter. Durability is irrelevant here.
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        return True, "OK"


class RequestQueue:
    """
    Priority queue for API requests when rate limited
    Implements exponential backoff and retry logic
    """
    def __init__(self, max_retries: int = 5):
        self.queue = deque()
        self.max_retries = max_retries
        self.lock = threading.Lock()
        self.processing = False
    
    def add_request(self, request_type: str, callback, args: tuple = (), kwargs: dict = None, request_key: Optional[str] = None):
        """Add a request to the queue with retry logic"""
        if kwargs is None:
            kwargs = {}
        critical = is_critical_request(request_type)
        request_key = request_key or request_type
        
        with self.lock:
            for existing_request in self.queue:
                if existing_request.get('key') != request_key:
                    continue

                existing_request['type'] = request_type
                existing_request['callback'] = callback
                existing_request['args'] = args
                existing_request['kwargs'] = kwargs
                existing_request['created_at'] = time.time()
                existing_request['critical'] = critical
                log_event("REQUEST_QUEUE", f"Request updated: {request_type}", queue_size=len(self.queue), critical=critical)
                logger.info(f"REQUEST_QUEUE: Updated {request_type} (queue size: {len(self.queue)} | critical={critical})")
                return

            request = {
                'key': request_key,
                'type': request_type,
                'callback': callback,
                'args': args,
                'kwargs': kwargs,
                'retries': 0,
                'created_at': time.time(),
                'critical': critical,
            }
            if critical:
                self.queue.appendleft(request)
            else:
                self.queue.append(request)
        
        log_event("REQUEST_QUEUE", f"Request queued: {request_type}", queue_size=len(self.queue), critical=critical)
        logger.info(f"REQUEST_QUEUE: Queued {request_type} (queue size: {len(self.queue)} | critical={critical})")
    
    def process_queue(self, rate_limiter):
        """Process queued requests with exponential backoff"""
        with self.lock:
            if self.processing or not self.queue:
                return
            self.processing = True
        
        try:
            while True:
                with self.lock:
                    if not self.queue:
                        break
                    request = self.queue[0]
                
                # Calculate exponential backoff
                backoff_time = min(2 ** request['retries'], 30)  # Max 30 seconds
                elapsed = time.time() - request['created_at']
                
                if elapsed < backoff_time:
                    time.sleep(0.1)  # Small sleep before retry
                    continue
                
                # Try to execute request
                can_call, _ = rate_limiter.can_make_call(request['type'])
                if can_call:
                    try:
                        result = request['callback'](*request['args'], **request['kwargs'])
                        log_event("REQUEST_QUEUE", f"Request executed: {request['type']}")
                        logger.info(f"REQUEST_QUEUE: Executed {request['type']} after {request['retries']} retries")
                        with self.lock:
                            self.queue.popleft()
                    except Exception as e:
                        request['retries'] += 1
                        if request['retries'] >= self.max_retries:
                            log_event("REQUEST_QUEUE", f"Request failed after {self.max_retries} retries: {request['type']}", error=str(e))
                            logger.error(f"REQUEST_QUEUE: Failed {request['type']} after {self.max_retries} retries: {str(e)}")
                            with self.lock:
                                self.queue.popleft()
                        else:
                            log_event("REQUEST_QUEUE", f"Request retry {request['retries']}/{self.max_retries}: {request['type']}", error=str(e))
                            logger.warning(f"REQUEST_QUEUE: Retrying {request['type']} ({request['retries']}/{self.max_retries}): {str(e)}")
                else:
                    time.sleep(0.1)  # Wait before next check
        finally:
            with self.lock:
                self.processing = False


class TokenBucket:
    """
    Token bucket rate limiter for API calls
    """
    
    def __init__(self, capacity: int, refill_rate: float, name: str = "default"):
        """
        Initialize token bucket
        
        Args:
            capacity: Maximum number of tokens (burst capacity)
            refill_rate: Tokens added per second
            name: Name for logging purposes
        """
        self.capacity = capacity
        self.tokens = capacity  # Start with full bucket
        self.refill_rate = refill_rate
        self.name = name
        self.last_refill = time.time()
        self.lock = threading.Lock()
        
        log_event("RATE_LIMITER", f"Token bucket '{name}' initialized",
                 capacity=capacity, refill_rate=refill_rate)
        logger.info(f"RATE_LIMITER: Token bucket '{name}' initialized (capacity={capacity}, refill_rate={refill_rate})")
    
    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        elapsed = now - self.last_refill
        
        # Add tokens based on elapsed time
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now
    
    def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens from bucket with autonomous logging
        
        Args:
            tokens: Number of tokens to consume
            
        Returns:
            True if tokens were consumed, False if not enough tokens
        """
        with self.lock:
            self._refill()
            
            tokens_before = self.tokens
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                
                # Log successful token consumption
                bucket_status = {
                    "bucket_name": self.name,
                    "tokens_consumed": tokens,
                    "tokens_before": int(tokens_before),
                    "tokens_after": int(self.tokens),
                    "capacity": self.capacity,
                    "utilization_percent": round((1 - self.tokens / self.capacity) * 100, 1)
                }
                log_event("RATE_LIMITER", "TOKEN_CONSUMED", 
                         tokens_available=int(self.tokens),
                         bucket_status=bucket_status)
                return True
            else:
                # Log token shortage
                shortage = tokens - self.tokens
                bucket_status = {
                    "bucket_name": self.name,
                    "tokens_needed": tokens,
                    "tokens_available": int(self.tokens),
                    "shortage": int(shortage),
                    "capacity": self.capacity,
                    "next_refill_seconds": round(shortage / self.refill_rate, 2)
                }
                log_event("RATE_LIMITER", "TOKEN_SHORTAGE", 
                         tokens_available=int(self.tokens),
                         bucket_status=bucket_status)
                return False
    
    def wait_for_tokens(self, tokens: int = 1, timeout: float = 30.0) -> bool:
        """
        Wait for tokens to become available with detailed logging
        
        Args:
            tokens: Number of tokens needed
            timeout: Maximum time to wait
            
        Returns:
            True if tokens were acquired, False if timeout
        """
        start_time = time.time()
        wait_iterations = 0
        total_wait_time = 0
        
        # Log wait initiation
        log_event("RATE_LIMITER", "WAIT_INITIATED",
                 tokens_available=int(self.tokens),
                 bucket_status={
                     "bucket_name": self.name,
                     "tokens_needed": tokens,
                     "timeout": timeout
                 })
        
        while time.time() - start_time < timeout:
            if self.consume(tokens):
                # Log successful acquisition
                elapsed_time = time.time() - start_time
                log_event("RATE_LIMITER", "TOKENS_ACQUIRED",
                         tokens_available=int(self.tokens),
                         wait_time_ms=round(elapsed_time * 1000, 1),
                         bucket_status={
                             "bucket_name": self.name,
                             "wait_iterations": wait_iterations,
                             "total_wait_time_ms": round(total_wait_time * 1000, 1),
                             "elapsed_time_ms": round(elapsed_time * 1000, 1)
                         })
                logger.debug(f"RATE_LIMITER: Tokens acquired after {wait_iterations} iterations ({elapsed_time*1000:.1f}ms)")
                return True
            
            # Calculate wait time until next token
            with self.lock:
                self._refill()
                if self.tokens < tokens:
                    wait_time = (tokens - self.tokens) / self.refill_rate
                    wait_time = min(wait_time, 0.1)  # Maximum 100ms wait per iteration
                    
                    # Log wait iteration
                    wait_iterations += 1
                    if wait_iterations % 10 == 0:  # Log every 10th iteration to avoid spam
                        log_event("RATE_LIMITER", "WAITING",
                                 tokens_available=int(self.tokens),
                                 wait_time_ms=round(wait_time * 1000, 1),
                                 bucket_status={
                                     "bucket_name": self.name,
                                     "wait_iteration": wait_iterations,
                                     "tokens_needed": tokens
                                 })
                    
                    time.sleep(wait_time)
                    total_wait_time += wait_time
        
        # Log timeout
        elapsed_time = time.time() - start_time
        log_event("RATE_LIMITER", "WAIT_TIMEOUT",
                 tokens_available=int(self.tokens),
                 wait_time_ms=round(elapsed_time * 1000, 1),
                 bucket_status={
                     "bucket_name": self.name,
                     "tokens_needed": tokens,
                     "timeout": timeout,
                     "wait_iterations": wait_iterations,
                     "final_tokens": int(self.tokens)
                 })
        
        logger.warning(f"RATE_LIMITER: Timeout waiting for {tokens} tokens in '{self.name}' after {elapsed_time:.2f}s")
        return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get current bucket status"""
        with self.lock:
            self._refill()
            return {
                "name": self.name,
                "tokens": round(self.tokens, 2),
                "capacity": self.capacity,
                "refill_rate": self.refill_rate,
                "utilization": round((self.capacity - self.tokens) / self.capacity * 100, 1)
            }


class OptionsRateLimiter:
    """
    Rate limiter specifically designed for AngelOne API (Options Trading)
    
    Implements multiple rate limiting strategies:
    1. Requests per second limit
    2. Requests per minute limit
    3. Call frequency tracking
    4. Burst protection
    5. Automatic request queueing on rate limit
    """
    
    def __init__(self):
        # Full capacity limits from AngelOne
        self.rps_limit = REQUESTS_PER_SECOND  # 8 req/sec
        self.rpm_limit = REQUESTS_PER_MINUTE  # 180 req/min
        
        # Token buckets for different time windows
        self.second_bucket = TokenBucket(
            capacity=self.rps_limit,
            refill_rate=self.rps_limit,
            name="per_second"
        )
        
        self.minute_bucket = TokenBucket(
            capacity=self.rpm_limit,
            refill_rate=self.rpm_limit / 60.0,  # Convert to per-second rate
            name="per_minute"
        )
        
        # Request queue for rate-limited requests
        self.request_queue = RequestQueue(max_retries=5)
        
        # Call tracking for analysis
        self.call_history = deque(maxlen=1000)  # Keep last 1000 calls
        self.call_count_1min = 0
        self.call_count_5min = 0
        self.last_cleanup = time.time()
        
        # Statistics
        self.total_calls = 0
        self.blocked_calls = 0
        self.queued_calls = 0
        self.wait_time_total = 0.0
        
        self.lock = threading.Lock()
        self.shared_state = SharedRateLimiterState()
        
        log_event("RATE_LIMITER", "Options rate limiter initialized",
                 rps_limit=self.rps_limit, rpm_limit=self.rpm_limit)
        logger.info(f"RATE_LIMITER: Options rate limiter initialized (RPS={self.rps_limit}, RPM={self.rpm_limit})")
    
    def _cleanup_history(self):
        """Clean up old call history"""
        now = time.time()
        if now - self.last_cleanup < 30:  # Cleanup every 30 seconds
            return
        
        # Remove calls older than 5 minutes
        cutoff_time = now - 300  # 5 minutes
        while self.call_history and self.call_history[0] < cutoff_time:
            self.call_history.popleft()
        
        # Recalculate counts
        one_min_ago = now - 60
        five_min_ago = now - 300
        
        self.call_count_1min = sum(1 for call_time in self.call_history if call_time > one_min_ago)
        self.call_count_5min = len(self.call_history)
        
        self.last_cleanup = now
    
    def can_make_call(self, request_type: str = "api_call") -> tuple[bool, str]:
        """
        Check if we can make an API call without violating limits
        
        Returns:
            Tuple of (can_call, reason)
        """
        with self.lock:
            self._cleanup_history()
            critical = is_critical_request(request_type)

            if not critical:
                self.second_bucket._refill()
                self.minute_bucket._refill()
                if self.second_bucket.tokens <= CRITICAL_SECOND_RESERVE:
                    return False, f"Reserved per-second capacity held for critical requests ({request_type})"
                if self.minute_bucket.tokens <= CRITICAL_MINUTE_RESERVE:
                    return False, f"Reserved per-minute capacity held for critical requests ({request_type})"
            
            # Check local process buckets first
            if not self.second_bucket.consume(1):
                return False, f"Rate limit: {self.rps_limit} calls/second exceeded"
            
            if not self.minute_bucket.consume(1):
                # Return the token to second bucket since we couldn't use it
                self.second_bucket.tokens = min(self.second_bucket.capacity, 
                                              self.second_bucket.tokens + 1)
                return False, f"Rate limit: {self.rpm_limit} calls/minute exceeded"
            
            shared_ok, shared_reason = self.shared_state.try_acquire_slot(request_type)
            if not shared_ok:
                self.second_bucket.tokens = min(self.second_bucket.capacity, self.second_bucket.tokens + 1)
                self.minute_bucket.tokens = min(self.minute_bucket.capacity, self.minute_bucket.tokens + 1)
                return False, shared_reason

            return True, "OK"
    
    def wait_for_call_permission(self, timeout: float = 30.0, request_type: str = "api_call") -> bool:
        """
        Wait until we can make an API call with adaptive backoff
        
        Args:
            timeout: Maximum time to wait
            
        Returns:
            True if permission granted, False if timeout
        """
        start_time = time.time()
        adaptive_delay = 0  # Adaptive delay when approaching limits
        
        while time.time() - start_time < timeout:
            with self.lock:
                self._cleanup_history()
                
                # Check utilization and add adaptive delay if high
                current_utilization = (1 - self.second_bucket.tokens / self.second_bucket.capacity) * 100
                if current_utilization > 75:  # If >75% utilized, add backoff delay
                    adaptive_delay = 0.05 * (current_utilization / 100)  # 0-5ms delay based on utilization
                    
                    if current_utilization > 85:  # Critical utilization
                        log_event("RATE_LIMITER", f"⚠️ High utilization ({current_utilization:.1f}%), adding adaptive backoff",
                                 utilization_percent=round(current_utilization, 1),
                                 tokens_available=round(self.second_bucket.tokens, 2),
                                 adaptive_delay_ms=round(adaptive_delay * 1000, 1))
                        logger.warning(f"RATE_LIMITER: High utilization {current_utilization:.1f}% - adding adaptive backoff")
            
            can_call, reason = self.can_make_call(request_type)
            if can_call:
                # Add adaptive delay before returning
                if adaptive_delay > 0:
                    time.sleep(adaptive_delay)
                return True
            
            # Wait for tokens to become available
            # Check which bucket is the limiting factor
            if "second" in reason:
                wait_time = 1.0 / self.rps_limit  # Wait for next second slot
            else:
                wait_time = 60.0 / self.rpm_limit  # Wait for next minute slot
            
            time.sleep(min(wait_time + adaptive_delay, 0.1))
        
        self.blocked_calls += 1
        log_event("RATE_LIMITER", f"⛔ Call blocked due to timeout: {reason}", request_type=request_type)
        logger.error(f"RATE_LIMITER: Call blocked due to timeout: {reason} | request_type={request_type}")
        return False
    
    def queue_request(self, request_type: str, callback, args: tuple = (), kwargs: dict = None, request_key: Optional[str] = None):
        """
        Queue a request for retry when rate limited
        
        Args:
            request_type: Type of request (placeOrder, ltpData, etc.)
            callback: Callable function to execute
            args: Arguments to pass to callback
            kwargs: Keyword arguments to pass to callback
            
        Returns:
            True if queued successfully
        """
        self.queued_calls += 1
        self.request_queue.add_request(request_type, callback, args, kwargs, request_key=request_key)
        log_event("RATE_LIMITER", f"Request queued for retry: {request_type}",
                 queue_size=len(self.request_queue.queue),
                 queued_calls=self.queued_calls)
        logger.info(f"RATE_LIMITER: Queued {request_type} (queue size: {len(self.request_queue.queue)})")
        return True
    
    def process_pending_requests(self):
        """Process any queued requests with available tokens"""
        if len(self.request_queue.queue) > 0:
            self.request_queue.process_queue(self)
    
    def record_call(self, call_type: str = "api_call", success: bool = True):
        """
        Record an API call for tracking
        
        Args:
            call_type: Type of API call (login, order, ltp, etc.)
            success: Whether the call was successful
        """
        with self.lock:
            now = time.time()
            self.call_history.append(now)
            self.total_calls += 1
            
            if success:
                log_event("RATE_LIMITER", f"API call recorded: {call_type}")
            else:
                log_event("RATE_LIMITER", f"Failed API call recorded: {call_type}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        with self.lock:
            self._cleanup_history()
            
            second_status = self.second_bucket.get_status()
            minute_status = self.minute_bucket.get_status()
            
            avg_wait_time = (self.wait_time_total / self.total_calls 
                           if self.total_calls > 0 else 0)
            
            return {
                "total_calls": self.total_calls,
                "blocked_calls": self.blocked_calls,
                "queued_calls": self.queued_calls,
                "success_rate": round((self.total_calls - self.blocked_calls) / max(self.total_calls, 1) * 100, 2),
                "calls_last_1min": self.call_count_1min,
                "calls_last_5min": self.call_count_5min,
                "avg_wait_time": round(avg_wait_time, 3),
                "queued_requests": len(self.request_queue.queue),
                "second_bucket": second_status,
                "minute_bucket": minute_status,
                "limits": {
                    "rps": self.rps_limit,
                    "rpm": self.rpm_limit
                }
            }
    
    def get_utilization(self) -> float:
        """
        Get current rate limiter utilization (0.0 to 1.0)
        Based on per-second bucket (more immediate)
        
        Returns:
            Utilization percentage as float (0.0 = empty, 1.0 = full)
        """
        with self.lock:
            self.second_bucket._refill()
            utilization = (self.second_bucket.capacity - self.second_bucket.tokens) / self.second_bucket.capacity
            return max(0.0, min(1.0, utilization))  # Clamp between 0 and 1
    
    def reset_statistics(self):
        """Reset statistics counters"""
        with self.lock:
            self.total_calls = 0
            self.blocked_calls = 0
            self.queued_calls = 0
            self.wait_time_total = 0.0
            self.call_history.clear()
            self.call_count_1min = 0
            self.call_count_5min = 0
            
        log_event("RATE_LIMITER", "Statistics reset")
        logger.info("RATE_LIMITER: Statistics reset")


# =============================================================================
# Global rate limiter instance (singleton)
# =============================================================================

_options_rate_limiter_instance = None

def get_options_rate_limiter() -> OptionsRateLimiter:
    """Get or create the global options rate limiter instance"""
    global _options_rate_limiter_instance
    if _options_rate_limiter_instance is None:
        _options_rate_limiter_instance = OptionsRateLimiter()
    return _options_rate_limiter_instance


# =============================================================================
# Rate Limited API Call Decorator
# =============================================================================

def rate_limited_call(call_type: str = "api_call", timeout: float = 30.0):
    """
    Decorator to rate limit API calls with automatic retry queueing on timeout
    
    Args:
        call_type: Type of API call for logging
        timeout: Maximum time to wait for permission
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            limiter = get_options_rate_limiter()
            
            # Wait for permission to make the call
            start_wait = time.time()
            if not limiter.wait_for_call_permission(timeout):
                # CRITICAL: Queue the request for retry instead of losing it
                log_event("RATE_LIMITER", f"Rate limit timeout for {call_type} - queuing for retry")
                logger.error(f"RATE_LIMITER: Rate limit timeout for {call_type} - queuing for retry")
                
                # Create a retry callback
                def retry_callback():
                    return func(*args, **kwargs)
                
                # Queue the request
                limiter.queue_request(
                    request_type=call_type,
                    callback=retry_callback,
                    args=(),
                    kwargs={}
                )
                
                # Return a marker indicating it was queued
                return {"__QUEUED_FOR_RETRY__": True, "call_type": call_type}
            
            wait_time = time.time() - start_wait
            limiter.wait_time_total += wait_time
            
            # Make the API call
            try:
                result = func(*args, **kwargs)
                success = result is not None
                limiter.record_call(call_type, success)
                return result
            except Exception as e:
                limiter.record_call(call_type, False)
                raise e
        
        return wrapper
    return decorator
