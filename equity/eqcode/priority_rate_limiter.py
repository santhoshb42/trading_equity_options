"""
Priority-Based Rate Limiter for Trading Operations

CRITICAL REQUIREMENT: Order placement must NEVER fail due to rate limits.

Architecture:
1. Reserve 50% capacity for CRITICAL operations (order placement/modification)
2. Non-critical operations (LTP, position checks) use remaining capacity
3. CRITICAL operations get unlimited wait time
4. Non-critical operations timeout quickly

Priority Levels:
- P0 (CRITICAL): Order placement, order modification, SL placement - NEVER TIMEOUT
- P1 (HIGH): Order status checks, position verification - 10s timeout
- P2 (NORMAL): LTP data, holdings - 5s timeout  
- P3 (LOW): Historical data, analytics - 2s timeout
"""

import time
import threading
from typing import Optional, Dict, Any, Callable, Tuple
from collections import deque
from enum import IntEnum

from .bot_logging import log_event


class Priority(IntEnum):
    """Request priority levels"""
    CRITICAL = 0  # Order operations - NEVER timeout, reserved capacity
    HIGH = 1      # Order verification - short timeout
    NORMAL = 2    # LTP, positions - normal timeout
    LOW = 3       # Analytics, history - quick timeout


class PriorityTokenBucket:
    """
    Token bucket with priority-based allocation
    
    Reserves capacity for critical operations to ensure orders never fail
    """
    
    def __init__(self, capacity: int, refill_rate: float, name: str = "default"):
        """
        Initialize priority token bucket
        
        Args:
            capacity: Maximum number of tokens
            refill_rate: Tokens added per second
            name: Name for logging
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.name = name
        
        # Reserve 50% capacity for CRITICAL operations
        self.critical_reserve = int(capacity * 0.5)
        self.general_capacity = capacity - self.critical_reserve
        
        # Separate token pools
        self.critical_tokens = self.critical_reserve  # Reserved for P0
        self.general_tokens = self.general_capacity   # Available to all
        
        self.last_refill = time.time()
        self.lock = threading.Lock()
        
        log_event("PRIORITY_RATE_LIMITER", 
                 f"Priority bucket '{name}' initialized",
                 total_capacity=capacity,
                 critical_reserve=self.critical_reserve,
                 general_capacity=self.general_capacity,
                 refill_rate=refill_rate)
    
    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        elapsed = now - self.last_refill
        
        # Calculate tokens to add
        tokens_to_add = elapsed * self.refill_rate
        
        # Refill critical pool first (up to reserve)
        critical_deficit = self.critical_reserve - self.critical_tokens
        if critical_deficit > 0:
            critical_refill = min(tokens_to_add, critical_deficit)
            self.critical_tokens += critical_refill
            tokens_to_add -= critical_refill
        
        # Refill general pool with remaining tokens
        if tokens_to_add > 0:
            self.general_tokens = min(self.general_capacity, self.general_tokens + tokens_to_add)
        
        self.last_refill = now
    
    def consume(self, tokens: int = 1, priority: Priority = Priority.NORMAL) -> bool:
        """
        Try to consume tokens from appropriate pool
        
        Args:
            tokens: Number of tokens to consume
            priority: Request priority level
            
        Returns:
            True if tokens consumed, False otherwise
        """
        with self.lock:
            self._refill()
            
            if priority == Priority.CRITICAL:
                # CRITICAL: Can use both pools
                total_available = self.critical_tokens + self.general_tokens
                if total_available >= tokens:
                    # Consume from critical pool first
                    if self.critical_tokens >= tokens:
                        self.critical_tokens -= tokens
                    else:
                        # Use critical tokens + general tokens
                        tokens_needed = tokens - self.critical_tokens
                        self.general_tokens -= tokens_needed
                        self.critical_tokens = 0
                    
                    log_event("PRIORITY_TOKEN_CONSUMED",
                             f"CRITICAL request consumed {tokens} tokens",
                             priority="CRITICAL",
                             critical_tokens=int(self.critical_tokens),
                             general_tokens=int(self.general_tokens))
                    return True
                else:
                    log_event("PRIORITY_TOKEN_SHORTAGE",
                             f"CRITICAL request needs {tokens}, only {int(total_available)} available",
                             priority="CRITICAL",
                             critical_tokens=int(self.critical_tokens),
                             general_tokens=int(self.general_tokens))
                    return False
            
            else:
                # Non-critical: Can only use general pool
                if self.general_tokens >= tokens:
                    self.general_tokens -= tokens
                    log_event("PRIORITY_TOKEN_CONSUMED",
                             f"{priority.name} request consumed {tokens} tokens",
                             priority=priority.name,
                             general_tokens=int(self.general_tokens))
                    return True
                else:
                    log_event("PRIORITY_TOKEN_SHORTAGE",
                             f"{priority.name} request needs {tokens}, only {int(self.general_tokens)} available",
                             priority=priority.name,
                             general_tokens=int(self.general_tokens),
                             critical_reserve=int(self.critical_tokens))
                    return False
    
    def wait_for_tokens(self, tokens: int = 1, priority: Priority = Priority.NORMAL, 
                       timeout: Optional[float] = None) -> bool:
        """
        Wait for tokens with priority-based timeout
        
        Args:
            tokens: Number of tokens needed
            priority: Request priority
            timeout: Max wait time (None = infinite for CRITICAL)
            
        Returns:
            True if acquired, False if timeout
        """
        # CRITICAL operations never timeout
        if priority == Priority.CRITICAL and timeout is None:
            timeout = float('inf')
        
        # Default timeouts by priority
        if timeout is None:
            timeout_map = {
                Priority.CRITICAL: float('inf'),  # Never timeout
                Priority.HIGH: 10.0,
                Priority.NORMAL: 5.0,
                Priority.LOW: 2.0
            }
            timeout = timeout_map.get(priority, 5.0)
        
        start_time = time.time()
        wait_count = 0
        
        log_event("PRIORITY_WAIT_START",
                 f"{priority.name} waiting for {tokens} tokens",
                 priority=priority.name,
                 timeout=timeout if timeout != float('inf') else "INFINITE")
        
        while True:
            if self.consume(tokens, priority):
                elapsed = time.time() - start_time
                log_event("PRIORITY_TOKENS_ACQUIRED",
                         f"{priority.name} acquired tokens after {elapsed:.2f}s",
                         priority=priority.name,
                         wait_time_ms=int(elapsed * 1000),
                         wait_iterations=wait_count)
                return True
            
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                log_event("PRIORITY_WAIT_TIMEOUT",
                         f"{priority.name} timeout after {elapsed:.2f}s",
                         priority=priority.name,
                         wait_time_ms=int(elapsed * 1000),
                         wait_iterations=wait_count)
                return False
            
            # Adaptive sleep based on priority
            if priority == Priority.CRITICAL:
                sleep_time = 0.05  # 50ms for critical - aggressive retry
            elif priority == Priority.HIGH:
                sleep_time = 0.1   # 100ms for high
            else:
                sleep_time = 0.2   # 200ms for normal/low
            
            time.sleep(sleep_time)
            wait_count += 1
            
            # Log every 20 iterations for critical ops
            if priority == Priority.CRITICAL and wait_count % 20 == 0:
                log_event("PRIORITY_WAITING",
                         f"CRITICAL still waiting... {wait_count} iterations",
                         priority="CRITICAL",
                         elapsed_s=round(elapsed, 1))
    
    def get_status(self) -> Dict[str, Any]:
        """Get current bucket status"""
        with self.lock:
            self._refill()
            return {
                "name": self.name,
                "critical_tokens": int(self.critical_tokens),
                "critical_reserve": self.critical_reserve,
                "general_tokens": int(self.general_tokens),
                "general_capacity": self.general_capacity,
                "total_capacity": self.capacity,
                "critical_utilization": round((1 - self.critical_tokens / self.critical_reserve) * 100, 1),
                "general_utilization": round((1 - self.general_tokens / self.general_capacity) * 100, 1),
                "refill_rate": self.refill_rate
            }


class PriorityRateLimiter:
    """
    Priority-based rate limiter that ensures critical operations never fail
    
    Design:
    - Reserves 50% capacity for critical operations (orders)
    - Critical operations have infinite timeout
    - Non-critical operations timeout quickly to avoid blocking
    """
    
    # API endpoint to priority mapping
    ENDPOINT_PRIORITIES = {
        # CRITICAL - Order operations (P0)
        'placeOrder': Priority.CRITICAL,
        'modifyOrder': Priority.CRITICAL,
        'cancelOrder': Priority.CRITICAL,
        'orderBook': Priority.HIGH,  # Need quick verification
        
        # HIGH - Position/capital verification (P1)
        'position': Priority.HIGH,
        'holding': Priority.HIGH,
        'rmsLimit': Priority.HIGH,
        
        # NORMAL - Market data (P2)
        'ltpData': Priority.NORMAL,
        'getQuote': Priority.NORMAL,
        'searchScrip': Priority.NORMAL,
        
        # LOW - Analytics/history (P3)
        'getCandleData': Priority.LOW,
        'getMarginCalculator': Priority.LOW,
        'login': Priority.CRITICAL,  # Login is critical
    }
    
    def __init__(self, rps_limit: int = 8, rpm_limit: int = 180):
        """
        Initialize priority rate limiter
        
        Args:
            rps_limit: Requests per second limit
            rpm_limit: Requests per minute limit
        """
        self.rps_limit = rps_limit
        self.rpm_limit = rpm_limit
        
        # Priority-aware token buckets
        self.second_bucket = PriorityTokenBucket(
            capacity=rps_limit,
            refill_rate=rps_limit,
            name="per_second"
        )
        
        self.minute_bucket = PriorityTokenBucket(
            capacity=rpm_limit,
            refill_rate=rpm_limit / 60.0,
            name="per_minute"
        )
        
        # Statistics
        self.stats = {
            Priority.CRITICAL: {"calls": 0, "blocked": 0, "total_wait_ms": 0},
            Priority.HIGH: {"calls": 0, "blocked": 0, "total_wait_ms": 0},
            Priority.NORMAL: {"calls": 0, "blocked": 0, "total_wait_ms": 0},
            Priority.LOW: {"calls": 0, "blocked": 0, "total_wait_ms": 0},
        }
        self.lock = threading.Lock()
        
        log_event("PRIORITY_RATE_LIMITER",
                 "Priority rate limiter initialized",
                 rps_limit=rps_limit,
                 rpm_limit=rpm_limit,
                 critical_reserve_pct=50)
    
    def get_priority(self, endpoint: str) -> Priority:
        """
        Get priority for API endpoint
        
        Args:
            endpoint: API endpoint name
            
        Returns:
            Priority level
        """
        return self.ENDPOINT_PRIORITIES.get(endpoint, Priority.NORMAL)
    
    def acquire(self, endpoint: str = "api_call", timeout: Optional[float] = None) -> bool:
        """
        Acquire permission to make API call
        
        Args:
            endpoint: API endpoint name
            timeout: Max wait time (None = use priority default)
            
        Returns:
            True if acquired, False if timeout/blocked
        """
        priority = self.get_priority(endpoint)
        start_time = time.time()
        
        with self.lock:
            self.stats[priority]["calls"] += 1
        
        # Wait for both buckets
        if not self.second_bucket.wait_for_tokens(1, priority, timeout):
            with self.lock:
                self.stats[priority]["blocked"] += 1
            log_event("PRIORITY_ACQUIRE_FAILED",
                     f"{endpoint} ({priority.name}) blocked - second bucket timeout",
                     endpoint=endpoint,
                     priority=priority.name)
            return False
        
        if not self.minute_bucket.wait_for_tokens(1, priority, timeout):
            # Return token to second bucket
            with self.second_bucket.lock:
                self.second_bucket.general_tokens = min(
                    self.second_bucket.general_capacity,
                    self.second_bucket.general_tokens + 1
                )
            
            with self.lock:
                self.stats[priority]["blocked"] += 1
            log_event("PRIORITY_ACQUIRE_FAILED",
                     f"{endpoint} ({priority.name}) blocked - minute bucket timeout",
                     endpoint=endpoint,
                     priority=priority.name)
            return False
        
        # Success - record wait time
        elapsed_ms = int((time.time() - start_time) * 1000)
        with self.lock:
            self.stats[priority]["total_wait_ms"] += elapsed_ms
        
        if elapsed_ms > 100:  # Log if waited >100ms
            log_event("PRIORITY_ACQUIRED_WITH_WAIT",
                     f"{endpoint} ({priority.name}) acquired after {elapsed_ms}ms",
                     endpoint=endpoint,
                     priority=priority.name,
                     wait_time_ms=elapsed_ms)
        
        return True
    
    def refund_tokens(self, endpoint: str = "api_call", tokens: int = 1) -> bool:
        """
        Refund tokens when an API call fails after tokens were consumed
        
        CRITICAL: This prevents token starvation when API calls fail with exceptions.
        When rate limiter consumes tokens via acquire() but the API call throws an exception,
        we must refund the tokens to avoid depleting the bucket.
        
        Args:
            endpoint: API endpoint name
            tokens: Number of tokens to refund (default 1)
            
        Returns:
            True if refund successful, False otherwise
        """
        priority = self.get_priority(endpoint)
        
        try:
            # Refund to general pool (safe, won't exceed capacity)
            with self.second_bucket.lock:
                old_general = self.second_bucket.general_tokens
                self.second_bucket.general_tokens = min(
                    self.second_bucket.general_capacity,
                    self.second_bucket.general_tokens + tokens
                )
            
            with self.minute_bucket.lock:
                old_minute = self.minute_bucket.general_tokens
                self.minute_bucket.general_tokens = min(
                    self.minute_bucket.general_capacity,
                    self.minute_bucket.general_tokens + tokens
                )
            
            log_event("RATE_LIMIT_TOKEN_REFUND",
                     f"Refunded {tokens} tokens for {endpoint} ({priority.name}) after API failure",
                     endpoint=endpoint,
                     priority=priority.name,
                     second_bucket_refund=int(self.second_bucket.general_tokens - old_general),
                     minute_bucket_refund=int(self.minute_bucket.general_tokens - old_minute))
            
            return True
        except Exception as e:
            log_event("RATE_LIMIT_REFUND_FAILED",
                     f"Failed to refund tokens for {endpoint}: {str(e)}",
                     endpoint=endpoint,
                     error=str(e))
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics"""
        with self.lock:
            stats_copy = {
                p.name: {
                    "calls": self.stats[p]["calls"],
                    "blocked": self.stats[p]["blocked"],
                    "success_rate": round(
                        (self.stats[p]["calls"] - self.stats[p]["blocked"]) / max(self.stats[p]["calls"], 1) * 100, 2
                    ),
                    "avg_wait_ms": round(
                        self.stats[p]["total_wait_ms"] / max(self.stats[p]["calls"], 1), 1
                    )
                }
                for p in Priority
            }
        
        return {
            "by_priority": stats_copy,
            "second_bucket": self.second_bucket.get_status(),
            "minute_bucket": self.minute_bucket.get_status(),
            "limits": {
                "rps": self.rps_limit,
                "rpm": self.rpm_limit
            }
        }
    
    # Compatibility methods for existing code
    def can_make_call(self) -> Tuple[bool, str]:
        """Check if can make call (non-blocking)"""
        # Quick check without consuming tokens
        with self.second_bucket.lock:
            self.second_bucket._refill()
            if self.second_bucket.general_tokens < 1:
                return False, "Per-second limit reached"
        
        with self.minute_bucket.lock:
            self.minute_bucket._refill()
            if self.minute_bucket.general_tokens < 1:
                return False, "Per-minute limit reached"
        
        return True, "OK"
    
    def wait_for_call_permission(self, timeout: float = 30.0) -> bool:
        """Wait for permission (backward compatible)"""
        return self.acquire("api_call", timeout)
    
    def record_call(self, call_type: str = "api_call", success: bool = True):
        """Record API call (for compatibility)"""
        # Stats already tracked in acquire()
        pass
