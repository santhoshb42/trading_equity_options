# Rate Limiting Architecture - Complete Guide

**Status**: Most Stable | **Version**: 2.0 | **Last Updated**: January 2, 2026

---

## Table of Contents

1. [Rate Limiting Overview](#rate-limiting-overview)
2. [AngelOne API Limits](#angelone-api-limits)
3. [Token Bucket Implementation](#token-bucket-implementation)
4. [Request Queue & Retry Logic](#request-queue--retry-logic)
5. [API Call Optimization](#api-call-optimization)
6. [Real-World Performance](#real-world-performance)
7. [Monitoring & Debugging](#monitoring--debugging)
8. [Capacity Planning](#capacity-planning)

---

## Rate Limiting Overview

**Problem**: AngelOne API has strict limits that, if violated, can:
- Block API access temporarily
- Suspend trading account
- Lose positions due to inability to exit

**Solution**: Implement multi-layer rate limiting:
1. Token bucket (per-second enforcement)
2. Request queue (backlog management)
3. Bucketed LTP updates (API optimization)
4. Retry logic with exponential backoff

### System Design

```
┌──────────────────────────────────┐
│  API Call Request                │
├──────────────────────────────────┤
│                                  │
│  ┌──────────────────────────────┐│
│  │  Can make call?              ││
│  │  (Token Bucket Check)        ││
│  └────────┬─────────────────────┘│
│           │                      │
│        YES│                      │
│           ▼                      │
│  ┌──────────────────────────────┐│
│  │  Execute API Call            ││
│  │  Consume 1 token             ││
│  └────────┬─────────────────────┘│
│           │                      │
│           ▼                      │
│      ✅ Success                  │
│                                  │
│  ❌ Rate Limited?                │
│           │                      │
│        YES│                      │
│           ▼                      │
│  ┌──────────────────────────────┐│
│  │  Queue Request               ││
│  │  (Retry Later)               ││
│  └──────────────────────────────┘│
│                                  │
└──────────────────────────────────┘
```

---

## AngelOne API Limits

### Hard Limits

| Limit Type | Value | Violation Result |
|-----------|-------|------------------|
| **Per Second** | 8 requests/sec | HTTP 429 (Too Many Requests) |
| **Per Minute** | 180 requests/min | HTTP 429 + temporary block |
| **Per Hour** | 10,800 requests/hr | Extended block + manual review |
| **Per Day** | 86,400 requests/day | Account review required |

### How to Calculate

```
Per-Second Limit: 8 requests/sec = 1 request every 125ms

Per-Minute Limit: 180 requests/min = 3 requests/sec average

Per-Hour Limit: 10,800 requests/hr = 3 requests/sec average

Max Sustainable Rate: 6 requests/sec (75% of limit for safety buffer)
```

### Endpoint Categories

Different endpoints might have different limits:

| Endpoint Type | Typical Limit | Our Usage |
|---------------|--------------|----------|
| `getLtpData` | 8/sec | Heavy (LTP updates) |
| `getCandleData` | 8/sec | Moderate (initial setup) |
| `placeOrder` | 8/sec | Light (entries) |
| `getOrderBook` | 8/sec | Light (monitoring) |
| `getOptionChain` | 8/sec | Heavy (Greeks fetching) |

---

## Token Bucket Implementation

**Algorithm**: Tokens refill at fixed rate; consume 1 token per API call

### Core Design

```python
class TokenBucket:
    """
    Token bucket rate limiter
    
    Tokens refill at 8/second (AngelOne limit)
    Each API call consumes 1 token
    When bucket empty, queue for retry
    """
    
    def __init__(self, capacity=8, refill_rate=8.0):
        """
        Args:
            capacity: Max tokens (burst capacity)
            refill_rate: Tokens added per second
        
        Examples:
            # Conservative (safe)
            TokenBucket(capacity=4, refill_rate=6.0)  # 6 calls/sec max
            
            # Aggressive (risk)
            TokenBucket(capacity=8, refill_rate=8.0)  # 8 calls/sec max
        """
        self.capacity = capacity
        self.tokens = capacity  # Start full
        self.refill_rate = refill_rate
        self.last_refill = time.time()
        self.lock = threading.Lock()
    
    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        elapsed = now - self.last_refill
        
        # Calculate tokens earned
        tokens_to_add = elapsed * self.refill_rate
        
        # Add tokens, capped at capacity
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        
        self.last_refill = now
    
    def consume(self, tokens=1):
        """
        Try to consume tokens
        
        Returns:
            True if consumed, False if insufficient tokens
        """
        with self.lock:
            self._refill()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            else:
                return False
    
    def wait_for_tokens(self, tokens=1):
        """
        Block until tokens available (synchronous)
        
        WARNING: Use sparingly - blocks entire bot
        """
        while not self.consume(tokens):
            time.sleep(0.01)  # 10ms check interval
```

### Timing Example

```
Time: 0.00s → tokens=8.0 (full capacity)
  Call 1 → consume(1) = True → tokens=7.0

Time: 0.10s → tokens=7.8 (refill 0.8 tokens)
  Call 2 → consume(1) = True → tokens=6.8

Time: 0.20s → tokens=7.6 (refill 0.8 tokens)
  Call 3 → consume(1) = True → tokens=6.6

...continuous consumption...

Time: 1.00s → tokens=8.0 (refill complete cycle)
  Capacity: 8 calls per second
```

### Thread-Safe Design

```python
def consume(self, tokens=1):
    """Thread-safe token consumption"""
    
    with self.lock:  # ← Exclusive lock
        # Refill must be done inside lock
        # Otherwise multiple threads race to refill
        self._refill()
        
        # Check and consume atomically
        # No race condition here
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
```

---

## Request Queue & Retry Logic

**Purpose**: Queue API calls when rate limited instead of failing

### RequestQueue Implementation

```python
class RequestQueue:
    """
    Queue for API requests when rate limited
    Implements exponential backoff and retry logic
    """
    
    def __init__(self, max_retries=5):
        self.queue = deque()  # FIFO queue
        self.max_retries = max_retries
        self.lock = threading.Lock()
        self.processing = False
    
    def add_request(self, request_type, callback, args=(), kwargs=None):
        """
        Add request to queue
        
        Args:
            request_type: Name for logging ("get_ltp", "place_order", etc)
            callback: Function to call when rate limit clears
            args: Positional arguments
            kwargs: Keyword arguments
        """
        if kwargs is None:
            kwargs = {}
        
        with self.lock:
            request = {
                'type': request_type,
                'callback': callback,
                'args': args,
                'kwargs': kwargs,
                'retries': 0,
                'created_at': time.time()
            }
            self.queue.append(request)
            
            logger.info(f"Queued {request_type} (queue size: {len(self.queue)})")
    
    def process_queue(self, rate_limiter):
        """
        Process queued requests when tokens available
        
        Exponential backoff formula:
        backoff = min(2^retries, 30 seconds)
        
        Retry 0: wait 1 sec (2^0)
        Retry 1: wait 2 sec (2^1)
        Retry 2: wait 4 sec (2^2)
        Retry 3: wait 8 sec (2^3)
        Retry 4: wait 16 sec (2^4)
        Retry 5: wait 30 sec (max)
        """
        
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
                
                # Calculate backoff
                backoff = min(2 ** request['retries'], 30)
                elapsed = time.time() - request['created_at']
                
                # Wait if backoff not expired
                if elapsed < backoff:
                    time.sleep(0.1)
                    continue
                
                # Try to execute
                can_call, wait_time = rate_limiter.can_make_call()
                
                if can_call:
                    try:
                        # Execute the queued callback
                        result = request['callback'](*request['args'], 
                                                     **request['kwargs'])
                        
                        logger.info(f"Executed {request['type']} "
                                  f"after {request['retries']} retries")
                        
                        with self.lock:
                            self.queue.popleft()
                            
                    except Exception as e:
                        request['retries'] += 1
                        
                        if request['retries'] >= self.max_retries:
                            logger.error(f"Failed {request['type']} "
                                       f"after {self.max_retries} retries: {e}")
                            with self.lock:
                                self.queue.popleft()
                        else:
                            logger.warning(f"Retry {request['type']} "
                                         f"({request['retries']}/{self.max_retries}): {e}")
                else:
                    # Rate limit active, wait
                    time.sleep(wait_time)
        
        finally:
            with self.lock:
                self.processing = False
```

### Retry Timeline Example

```
Request: get_market_data(["INFY27JAN261640CE"])

Time 0.0s: Add to queue (attempt 0)
├─ Queued, waiting for rate limit clearance

Time 0.1s: Try execute (attempt 0)
├─ Rate limited! Add to queue with retry=1
├─ Backoff = 2^1 = 2 seconds

Time 2.1s: Try execute (attempt 1)
├─ Rate limited! Add to queue with retry=2
├─ Backoff = 2^2 = 4 seconds

Time 6.1s: Try execute (attempt 2)
├─ Rate limited! Add to queue with retry=3
├─ Backoff = 2^3 = 8 seconds

Time 14.1s: Try execute (attempt 3)
├─ ✅ Success! Tokens available
└─ Remove from queue

Total time: 14.1 seconds to complete
```

---

## API Call Optimization

### Problem: LTP Update Explosion

**Naive approach** (❌ Fails):
```
30 positions × get_market_data every 5 sec
= 30 API calls / 5 sec
= 6 calls/second
= 360 calls/minute
= Exceeds 180/min limit! ❌
```

### Solution 1: Bucketed Updates

Divide positions into buckets, rotate through them:

```python
class LTPBucketManager:
    def __init__(self, bucket_size=5):
        self.bucket_size = bucket_size
        self.buckets = []  # List of symbol lists
        self.current_bucket_index = 0
    
    def create_buckets(self, symbols):
        """Divide symbols into buckets"""
        self.buckets = []
        for i in range(0, len(symbols), self.bucket_size):
            bucket = symbols[i:i+self.bucket_size]
            self.buckets.append(bucket)
        self.current_bucket_index = 0
    
    def get_current_bucket(self):
        """Get current bucket and rotate"""
        if not self.buckets:
            return []
        current = self.buckets[self.current_bucket_index]
        self.current_bucket_index = (self.current_bucket_index + 1) % len(self.buckets)
        return current
```

**With bucketing** (✅ Works):
```
30 positions ÷ 5 bucket_size = 6 buckets

Cycle 1 (5.0s): Update bucket 1 (P1-P5) → 5 API calls
Cycle 2 (5.5s): Update bucket 2 (P6-P10) → 5 API calls
Cycle 3 (6.0s): Update bucket 3 (P11-P15) → 5 API calls
...
Cycle 6 (7.5s): Update bucket 6 (P26-P30) → 5 API calls
Cycle 7 (8.0s): Update bucket 1 (P1-P5) → 5 API calls

30 API calls / 6 cycles = 5 calls/cycle
= 1 call/second
= 60 calls/minute ✅
```

### Solution 2: Batch Operations

Combine multiple operations into single call:

```
❌ Individual calls (expensive):
get_ltp("INFY27JAN261640CE")        → 1 call
get_ltp("TCS27JAN263500CE")         → 1 call
get_option_chain("BANKNIFTY")       → 1 call
Total: 3 calls

✅ Batch call (efficient):
get_market_data(symbols=[
    "INFY27JAN261640CE",
    "TCS27JAN263500CE",
    "BANKNIFTY27JAN1950PE"
])
Total: 1 call for 3 data points
```

### Solution 3: Caching

Store data to reduce repeated calls:

```python
class DataCache:
    def __init__(self, ttl_seconds=2):
        self.data = {}
        self.timestamps = {}
        self.ttl = ttl_seconds
    
    def get(self, key):
        """Get cached value if fresh"""
        if key in self.data:
            age = time.time() - self.timestamps[key]
            if age < self.ttl:
                return self.data[key]  # Use cache
            else:
                del self.data[key]      # Expired
        return None
    
    def set(self, key, value):
        """Cache value"""
        self.data[key] = value
        self.timestamps[key] = time.time()
```

**Example**:
```
Option chain for INFY fetched at 10:00:01
├─ Cached
├─ Greeks calculated
└─ Used for 5 positions

New request at 10:00:02 for same option chain
├─ Cache hit! Use previous data (1 second old)
├─ No API call needed
└─ Save 1 API call ✓

Cache expires at 10:00:03
├─ Next request fetches fresh data
└─ API call needed again
```

---

## Real-World Performance

### Daily API Call Budget

**Scenario**: 30 concurrent positions, 8-hour trading day

```
LTP Updates:
├─ 30 positions
├─ 5-second bucket rotation = 6 buckets
├─ 5 calls/cycle
├─ 12 cycles/minute × 60 minutes = 720 cycles/hour
├─ 720 cycles × 5 calls = 3,600 calls/hour
├─ 8 hours × 3,600 = 28,800 calls/day
└─ Budget limit: 86,400/day → 28,800 = 33% ✅

Entry/Exit Orders:
├─ Max 30 trades/day
├─ 1 entry + 1 exit per trade
├─ 30 × 2 = 60 calls/day
└─ Budget: 1% usage

Position Checks:
├─ 20 calls/hour for monitoring
├─ 8 hours × 20 = 160 calls/day
└─ Budget: 0.2% usage

Option Chain Fetches:
├─ 30 positions × 2 fetches/5min
├─ 2 × 12 × 8 = 192 calls/day
└─ Budget: 0.2% usage

TOTAL: 28,800 + 60 + 160 + 192 = 29,212 calls/day
BUDGET: 86,400 calls/day
USAGE: 33.8% ✅
```

---

## Monitoring & Debugging

### Real-Time Monitoring

```python
class RateLimitMonitor:
    """Monitor rate limit status in real-time"""
    
    def __init__(self, rate_limiter):
        self.rl = rate_limiter
    
    def get_status(self):
        """Get current rate limit status"""
        return {
            'tokens_available': self.rl.tokens,
            'bucket_capacity': self.rl.capacity,
            'refill_rate': f"{self.rl.refill_rate} tokens/sec",
            'queue_size': len(self.rl.request_queue.queue),
            'calls_this_minute': self.calls_this_minute(),
            'calls_this_hour': self.calls_this_hour(),
            'usage_percentage': self.usage_percentage()
        }
    
    def usage_percentage(self):
        """Calculate usage as % of limit"""
        calls_per_minute = self.calls_this_minute()
        limit_per_minute = 180
        return (calls_per_minute / limit_per_minute) * 100
```

### Logs to Monitor

```
[RATE_LIMIT] Token bucket status:
  Available: 6.5/8 tokens
  Per-minute: 142 calls
  Usage: 78.9%

[RATE_LIMIT] Rate limit detected
  HTTP Code: 429
  Queued: get_market_data
  Retry: 1/5
  Backoff: 2 seconds

[RATE_LIMIT] Queue processing
  Pending: 3 requests
  Executing: get_option_chain
  Status: Success
  Total backlog time: 5.2s

[WARNING] Approaching rate limit
  Usage: 85% of budget
  Pending queue: 5 requests
  Recommendation: Reduce bucket size or increase cycle time
```

---

## Capacity Planning

### Sizing Decisions

For **N concurrent positions**:

```
Bucket Size = 5 (fixed)
Num Buckets = ceil(N / 5)
LTP calls/5sec = 5
LTP calls/min = 60

Options:
├─ 10 positions: 2 buckets → 60 calls/min ✓
├─ 20 positions: 4 buckets → 60 calls/min ✓
├─ 30 positions: 6 buckets → 60 calls/min ✓
└─ 50 positions: 10 buckets → 100 calls/min ✓

Limit: 180 calls/min
Available for other ops: 180 - 100 = 80 calls/min ✓
```

### Scaling Strategies

**If hitting rate limits**:

1. **Increase bucket size** (refresh less frequently)
   ```python
   LTPBucketManager(bucket_size=10)  # 30 positions → 3 buckets
   # Result: 30 calls/min instead of 60
   ```

2. **Reduce concurrent positions**
   ```python
   MAX_SLOTS = 20  # Down from 30
   # Result: Lower API load
   ```

3. **Increase cycle time** (monitor less frequently)
   ```python
   MONITOR_INTERVAL = 10  # seconds (was 5)
   # Result: 30 calls/min instead of 60
   ```

4. **Batch more aggressively**
   ```python
   # Fetch multiple option chains in single call
   get_market_data(symbols=[...])  # Combine LTP + option chain
   ```

---

## Summary

### Key Takeaways

✅ **Token Bucket**: Enforces per-second limit
✅ **Request Queue**: Buffers during peaks
✅ **Bucketed Updates**: Distributes API load
✅ **Exponential Backoff**: Intelligent retry
✅ **Caching**: Reduces duplicate calls
✅ **Monitoring**: Real-time rate limit tracking

### Never Exceed

- 8 API calls/second
- 180 API calls/minute
- Current implementation uses <34% of daily budget

### Monitor These

- `queue_size` (should be 0-1 normally)
- `usage_percentage` (should be <70%)
- `calls_this_minute` (should be <180)
- `backoff_delays` (should be minimal)

---

**Questions?** See ARCHITECTURE.md for system design or ML.md for learning details.
