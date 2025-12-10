# Production Monitoring Guide - Dec 11, 2025 Trading Session

## What to Watch For in the Logs

### 1. SL Rounding Success (5 Paise Multiples)

**Expected Log Pattern:**
```
[10:30:15] EICHERMOT SL place attempt
[10:30:15] SL Price: 7205.30 (calculated from 7241.50 entry)
[10:30:15] Placing SL order...
[10:30:16] ORDER: Order placed, order_id=4521830001
[10:30:18] ORDER: Order filled, order_id=4521830001, average_price=7205.30
```

**What NOT to See:**
```
❌ "5 paise multiple" error
❌ "Lot size validation" error
❌ REJECTED status with paise-related message
```

**Verification Command:**
```bash
# Check for SL errors
grep -i "5 paise\|paise multiple\|REJECTED" equity/logs/2025-12-11/bot.log | wc -l
# Should return: 0 (no errors)

# Check SL placement success
grep "ORDER: Order filled" equity/logs/2025-12-11/bot.log | wc -l
# Should return: 4+ (one per order)
```

---

### 2. Bulk Order Fetcher Activity (Rate Limit Fix)

**On Broker Initialization (10:29-10:30):**
```
[10:29:45] BROKER_INIT: ✅ Bulk LTP fetcher initialized - will fetch up to 50 symbols per request
[10:29:46] BROKER_INIT: ✅ Bulk order fetcher initialized - will reduce orderBook API calls
```

**During Order Confirmation (every 5 seconds):**
```
[10:30:06] BULK_ORDER_FETCHER: Fetched 4 orders from broker
[10:30:11] BULK_ORDER_FETCHER: Fetched 4 orders from broker
[10:30:16] BULK_ORDER_FETCHER: Fetched 4 orders from broker
[10:30:21] BULK_ORDER_FETCHER: Fetched 4 orders from broker
```

**Order Status Checks (every 1 second, reading from cache):**
```
[10:30:06] ORDER: Order filled (via cache_hit)
[10:30:07] ORDER: Order filled (via cache_hit)
[10:30:08] ORDER: Order filled (via cache_hit)
```

**Verification Command:**
```bash
# Count orderBook API calls
grep "BULK_ORDER_FETCHER: Fetched" equity/logs/2025-12-11/bot.log | wc -l
# Expected: ~7-8 calls (every 5 seconds over ~35-40 minutes)

# Confirm cache hits
grep "cache_hit" equity/logs/2025-12-11/bot.log | wc -l
# Expected: 20+ (each order status check reads cache)

# Confirm NO rate limit errors
grep -i "rate limit\|AG8001\|access denied" equity/logs/2025-12-11/bot.log
# Expected: Empty (no rate limit hits)
```

---

### 3. Alert Processing Continuity

**Expected Behavior:**
```
[09:45:00] WEBHOOK: Received alert for RELIANCE
[10:00:05] WEBHOOK: Received alert for EICHERMOT
[10:15:30] WEBHOOK: Received alert for ALKEM
[10:30:45] WEBHOOK: Received alert for CHOLAFIN
[10:45:15] WEBHOOK: Received alert for BAJFINANCE
[11:00:00] WEBHOOK: Received alert for TECHM
... continuing until 3:30 PM without gaps ...
```

**Verification Command:**
```bash
# Check alert timeline
grep "WEBHOOK: Received alert" equity/logs/2025-12-11/bot.log | cut -d' ' -f1 | uniq
# Should show: Multiple different times, no long gaps

# Count total alerts processed
grep "WEBHOOK: Received alert" equity/logs/2025-12-11/bot.log | wc -l
# Expected: Similar to previous days (NOT zero or very low)
```

---

### 4. Rate Limit Recovery (If Occurs)

**If Rate Limit Is Hit (should NOT happen now):**
```
[10:30:45] ERROR: Access denied because of exceeding access rate (AG8001)
[10:30:46] RATE_LIMIT: Waiting 60 seconds before retry...
[10:31:46] RATE_LIMIT: Resuming operations
```

**Expected:** No such messages (rate limit should NOT occur)

**Verification Command:**
```bash
# Check for rate limit errors
grep -i "access denied\|rate.*limit\|AG8001" equity/logs/2025-12-11/bot.log
# Expected: No matches (clean run)

# If there ARE matches, the fix is not working
# Check: 
#   1. How many orderBook calls? (grep BULK_ORDER_FETCHER | wc -l)
#   2. When do they occur relative to rate limit error?
```

---

### 5. API Call Efficiency Metrics

**Compare Dec 10 (Before Fix) vs Dec 11 (After Fix):**

```bash
# Dec 10 (BEFORE)
grep "orderBook\|BULK_ORDER" equity/logs/2025-12-10/bot.log | wc -l
# Result: ~85+ lines (inefficient)

# Dec 11 (AFTER)
grep "BULK_ORDER_FETCHER: Fetched" equity/logs/2025-12-11/bot.log | wc -l
# Expected: ~6-8 lines (efficient)

# Ratio improvement
# 85 → 8 = 10.6x improvement (EXPECTED)
# If you see similar numbers, something is wrong
```

---

## Troubleshooting Checklist

### If SL Orders Still Get "5 Paise" Errors:
- [ ] Verify code changes applied: `grep "round(sl_paise / 5)" equity/eqcode/api.py`
- [ ] Check git log: `git log --oneline | grep -i "5 paise"`
- [ ] Restart bot to load new code

### If Rate Limit Still Occurs:
- [ ] Verify bulk_order_fetcher started: `grep "Bulk order fetcher initialized" equity/logs/2025-12-11/bot.log`
- [ ] Check BulkOrderFetcher class exists: `ls equity/eqcode/bulk_order_fetcher.py`
- [ ] Verify imports work: `python3 -c "from equity.eqcode.bulk_order_fetcher import BulkOrderFetcher"`
- [ ] Check for exceptions: `grep -i "error\|exception" equity/logs/2025-12-11/bot.log | grep -i "bulk_order"`

### If Alerts Stop Processing:
- [ ] Check rate limit status: `grep -i "AG8001\|rate.*limit" equity/logs/2025-12-11/bot.log`
- [ ] Verify webhook receiver is running: Check webhook router process
- [ ] Check capital limits: `grep "CAPITAL_LIMIT" equity/logs/2025-12-11/bot.log`
- [ ] Verify session is active: `grep "SESSION_ACTIVE\|SESSION_REFRESH" equity/logs/2025-12-11/bot.log`

---

## Success Criteria

✅ **Session is successful if:**
1. [ ] No "5 paise multiple" SL errors
2. [ ] No rate limit (AG8001) errors
3. [ ] Alerts processed continuously 09:30-15:30
4. [ ] orderBook API calls: 6-10 total (not 85+)
5. [ ] At least 4 orders executed successfully
6. [ ] SL orders filled at correct (5-paise-rounded) prices

✅ **Impact metrics:**
- SL Success Rate: 100% (vs 0% before)
- API Efficiency: 10x improvement (85 → 8 calls)
- Uptime: Continuous (vs interrupted after 10:00 AM)

---

## Post-Session Analysis

**Run this analysis after market close:**

```bash
#!/bin/bash
LOG_FILE="equity/logs/2025-12-11/bot.log"

echo "=== MONITORING SUMMARY ===" 
echo "SL Errors:"
grep "5 paise" "$LOG_FILE" | wc -l
echo ""

echo "Rate Limit Errors:"
grep -i "AG8001\|access denied" "$LOG_FILE" | wc -l
echo ""

echo "OrderBook API Calls:"
grep "BULK_ORDER_FETCHER: Fetched" "$LOG_FILE" | wc -l
echo ""

echo "Cache Hits:"
grep "cache_hit" "$LOG_FILE" | wc -l
echo ""

echo "Total Orders Placed:"
grep "ORDER: Order placed" "$LOG_FILE" | wc -l
echo ""

echo "Orders Filled:"
grep "ORDER: Order filled" "$LOG_FILE" | wc -l
echo ""

echo "Alerts Processed:"
grep "WEBHOOK: Received alert" "$LOG_FILE" | wc -l
echo ""

echo "Alert Timeline:"
grep "WEBHOOK: Received alert" "$LOG_FILE" | head -3 | cut -d' ' -f1
echo "..."
grep "WEBHOOK: Received alert" "$LOG_FILE" | tail -3 | cut -d' ' -f1
```

---

## Conclusion

This is the first time a REAL architectural solution has been implemented instead of configuration tweaks or workarounds. 

**Expected Result:** Clean trading session with no rate limit issues and proper SL order placement.

If this session shows the expected metrics, the fixes are proven effective and production-ready.
