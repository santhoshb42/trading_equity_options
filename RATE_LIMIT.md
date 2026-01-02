# Rate Limiting Strategy

**Status**: Most Stable | **Last Updated**: January 2, 2026

## AngelOne API Limits

- **8 requests/second** (RPS) - enforced by token bucket
- **180 requests/minute** (RPM) - enforced by token bucket  
- **50 symbols/HTTP call** for LTP (SmartAPI /quote endpoint)
- **1 RPS for /quote endpoint** - separate from main bucket

## Implementation

### Token Bucket Rate Limiter

**File**: `options/optcode/options_rate_limiter.py`

Two token buckets enforce AngelOne limits:
1. **Per-second bucket**: Capacity 8 tokens, refill 8/sec
2. **Per-minute bucket**: Capacity 180 tokens, refill 3/sec

Both buckets must have tokens for API call to proceed. If either bucket depleted, request queues with exponential backoff.

```python
second_bucket = TokenBucket(capacity=8, refill_rate=8, name="per_second")
minute_bucket = TokenBucket(capacity=180, refill_rate=3, name="per_minute")
```

### BULK LTP Fetching

**File**: `options/optcode/angelone_options.py` (line 1971)

Fetch LTP for multiple symbols in batches:

```
56 option positions
├─ Batch 1: 50 symbols → POST /quote → 1 HTTP call
├─ Batch 2: 6 symbols → POST /quote → 1 HTTP call
└─ Total: 2 HTTP calls (vs 56 individual calls)
```

**Rate limiting**: 1 RPS for /quote endpoint (managed separately, does NOT consume token bucket tokens)

## Performance

### Daily Budget (8-hour trading day, 10-second monitoring cycle)

```
LTP Monitoring:
├─ 56 positions × 2 batches per cycle = 2 HTTP calls
├─ 6 cycles/minute × 60 min × 8 hours = 2,880 HTTP calls
├─ Budget: 180 RPM × 480 min = 86,400 calls available
├─ Usage: 3.3% of budget
└─ Headroom: 83,520 calls remaining ✅

Order Placement & Management:
├─ Entry orders: ~10-20 trades/day × 2 = 20-40 calls
├─ Position monitoring: ~2,400 calls
├─ Greeks refresh: ~80 calls
└─ Total: ~2,500 additional calls

TOTAL USAGE: ~5,400 calls/day
BUDGET REMAINING: 80,900 calls/day (94% headroom)
```

## How They Work Together

1. **Entry Signal** → Check token bucket → POST order (1 token consumed)
2. **Monitor Position** → Check token bucket → GET position status (1 token consumed)
3. **Fetch LTP for 56 positions** → Check /quote rate limit → 2 HTTP calls (1 RPS wait, no token bucket impact)
4. **Calculate Greeks** → Check token bucket → API call (1 token consumed)

## Monitoring

Rate limiter logs all activity to `events.jsonl`:

```json
{"event_type": "RATE_LIMITER", "bucket_status": {...}}
{"event_type": "BULK_MARKET_DATA", "total_symbols": 56, "api_calls_made": 2}
```

Check real-time bucket status:
```python
from options.optcode.options_rate_limiter import get_options_rate_limiter
limiter = get_options_rate_limiter()
print(limiter.second_bucket.get_status())
print(limiter.minute_bucket.get_status())
```
