# Rate Limiting Fix for TITAN Order Rejection

## Problem
TITAN order was being rejected with **RATE_LIMITED** error even though:
- Only 1 active trade was running (minimal API calls)
- Bulk LTP fetching was implemented
- Rate limiter should have had plenty of capacity

## Root Cause Analysis
The issue was a **two-stage problem**:

### Stage 1: Pre-check Blocking (FIXED)
The `@rate_limited` decorator on `place_order()` was timing out because:
- Multiple validation API calls (analytics) were consuming tokens before order placement
- When a burst of alerts arrived, each `validate_buy_signal_with_analytics()` made an `get_enhanced_analytics()` API call
- By the time `place_order()` was called, the rate limiter had <1 token available
- The decorator would wait up to 30 seconds for tokens to become available
- After timeout, it would return a marker indicating the request was queued
- This marker was not handled correctly, causing the order to fail

### Stage 2: Exhausted Validation Calls (FIXED)
During burst alert processing:
- Each BUY alert → `validate_buy_signal_with_analytics()` → `get_enhanced_analytics()` API call
- Each SELL alert → `validate_sell_signal_with_analytics()` → `get_enhanced_analytics()` API call
- With Angel One's 8 req/sec limit, a burst of 20 alerts could exhaust the rate limiter
- Tokens were being consumed by validation BEFORE order placement, not allowing order placement to proceed

## Solutions Implemented

### 1. Removed @rate_limited Decorator from place_order()
**File:** `/root/santhosh/trading/equity/eqcode/angelone.py` (line 1350)

**Before:**
```python
@rate_limited(call_type="place_order", timeout=30.0)
def place_order(self, symbol: str, action: str, quantity: int, price: float = 0, ...):
```

**After:**
```python
def place_order(self, symbol: str, action: str, quantity: int, price: float = 0, ...):
```

**Why:** Removing the decorator means order placement now uses `_safe_api_call()` directly, which leverages the **PriorityRateLimiter** that:
- Reserves 50% of capacity for critical operations (orders)
- Gives orders infinite timeout (never fails due to rate limits)
- Prevents orders from being queued or delayed

### 2. Added Rate Limit Protection to Analytics Validation
**File:** `/root/santhosh/trading/equity/eqcode/api.py`

**For BUY signals (validate_buy_signal_with_analytics):**
```python
# CRITICAL FIX: Skip analytics validation if rate limiter is exhausted
try:
    rate_limiter = trading_state.broker.rate_limiter
    if hasattr(rate_limiter, 'get_statistics'):
        stats = rate_limiter.get_statistics()
        second_bucket = stats.get('second_bucket', {})
        available_tokens = second_bucket.get('tokens', 1)
        
        # If <3 tokens available, skip analytics to reserve tokens for order placement
        if available_tokens < 3:
            log_event("ANALYTICS_SKIPPED_RATE_LIMIT", ...)
            return {
                "approved": True,
                "reason": "Rate limit protection - analytics validation skipped",
                "signal": "RATE_LIMIT_PROTECTED",
                ...
            }
except Exception as e:
    # If rate limiter check fails, continue normally
    log_event("RATE_LIMIT_CHECK_ERROR", ...)
```

**For SELL signals (validate_sell_signal_with_analytics):**
- Applied identical protection logic

**Why:** When rate limiter is exhausted (<3 tokens available):
- Skip the expensive `get_enhanced_analytics()` API call
- Fallback to TradingView signal alone (still valid)
- Reserve tokens for critical order placement
- This allows orders to place even during burst alert scenarios

## Results

### Before Fix
- TITAN order rejected with RATE_LIMITED error
- Happens during burst of alerts when rate limiter exhausted
- Only 1 active trade, so not user-facing rate limiting

### After Fix
- Order placement uses PriorityRateLimiter with reserved capacity
- Analytics validation is intelligently skipped when rate limiter is exhausted
- Orders prioritized over validation API calls
- Burst alerts can be processed without blocking order placement

## Technical Details

### Priority Rate Limiter Features
- **Reserved Capacity:** 50% of rate limit reserved for critical operations
- **Infinite Timeout:** Critical ops (placeOrder, modifyOrder, cancelOrder) never fail
- **Queue Management:** Non-critical ops gracefully degrade when rate limited

### Analytics Validation Behavior
- **Normal:** Fetches enhanced analytics from Angel One API
- **Rate Limited:** Skips analytics, uses TradingView signal + basic checks
- **Graceful Degradation:** Never blocks order placement

### Rate Limit Thresholds
- Angel One API: 10 req/sec, 200 req/min
- Code limits: 8 req/sec, 180 req/min (conservative margin)
- Protection threshold: 3 tokens remaining triggers analytics skip

## Testing
1. Send burst of BUY alerts for multiple symbols
2. Verify orders are placed successfully
3. Check logs for `ANALYTICS_SKIPPED_RATE_LIMIT` messages during burst
4. Confirm TITAN and other orders complete successfully

## Monitoring
Look for these events in logs:
- `ANALYTICS_SKIPPED_RATE_LIMIT` - Analytics was skipped due to rate limiting (expected during bursts)
- `RATE_LIMIT_PROTECTED` - Order placement protected from rate limit failures
- `ORDER_PLACED` - Order successfully placed (new behavior with improved reliability)

## Future Improvements
1. Cache analytics results for 5-10 second window
2. Batch fetch analytics for multiple symbols
3. Implement adaptive validation based on market conditions
4. Monitor rate limiter utilization to tune thresholds
