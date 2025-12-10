# ✅ **Deployment Checklist - Bulk Data Fetching System**

## Pre-Market (Before 9:15 AM)

### System Status Verification
- [ ] **Options Bot**
  - [x] `_fetch_from_angel()` implemented (real chain data)
  - [x] Tested with TECHM 30DEC 1600CE (₹24.50 real LTP)
  - [x] Angel One broker integration working
  - [ ] Verify webhook responding to alerts
  - [ ] Check that positions.json loads without errors
  - [ ] Confirm monitoring loop running (check logs)

- [ ] **Equity Bot**
  - [x] Bulk LTP fetcher integrated into monitor.py
  - [x] Rate limiter configured (8 req/sec, 50% critical reserve)
  - [x] 6-layer protection verified (priority limiter, anti-burst, adaptive, bulk, retry, smart skip)
  - [ ] Monitor showing "Using bulk LTP fetch" in logs
  - [ ] Check that all 5 positions loading without errors
  - [ ] Verify rate limiter initialized correctly
  - [ ] Confirm SL placement retry logic working

### File Integrity Checks
- [ ] **Python Files Compile**
  ```bash
  python3 -m py_compile equity/eqcode/bulk_ltp_fetcher.py
  python3 -m py_compile equity/eqcode/bulk_candle_fetcher.py
  python3 -m py_compile equity/eqcode/monitor.py
  python3 -m py_compile equity/eqcode/angelone.py
  ```
  - [ ] No syntax errors
  - [ ] All imports available

- [ ] **Configuration Files**
  - [ ] `config.py` has correct Angel One API key
  - [ ] `instrument.json` exists and is readable
  - [ ] Watchlist symbols match available instruments
  - [ ] Feed token configured correctly

- [ ] **Log Files & Data**
  - [ ] Clear old logs: `rm equity/logs/*.log`
  - [ ] Backup current positions: `cp equity/data/positions.json equity/data/positions.json.backup`
  - [ ] Clear cache on startup: `bulk_ltp_fetcher.clear_cache()`

### API & Broker Connectivity
- [ ] **Angel One Connection**
  - [ ] Test ping to Angel One API
  - [ ] Verify authentication token is valid
  - [ ] Check that MarketData API is accessible
  - [ ] Confirm WebSocket feed_token working

- [ ] **Rate Limiter Status**
  - [ ] Initial utilization should be 0%
  - [ ] Monitor `/equity/logs/monitor.log` for rate limit messages
  - [ ] Verify CRITICAL bucket has 4 req/sec reserved
  - [ ] Check that non-CRITICAL operations respect 1 req/sec

---

## Market Hours (9:15 AM - 3:30 PM)

### Hourly Health Checks

**Every 30 minutes, verify:**

- [ ] **API Metrics**
  ```bash
  tail -20 equity/logs/monitor.log | grep -i "api\|rate\|bulk"
  ```
  - [ ] Rate utilization staying below 10%
  - [ ] Bulk LTP calls showing (should see ~2 calls per 5-sec cycle)
  - [ ] No "RATE_LIMIT" errors
  - [ ] No "Failed to fetch" messages

- [ ] **Position Monitoring**
  ```bash
  tail -20 equity/logs/monitor.log | grep -i "position\|ltp\|price"
  ```
  - [ ] All 5 positions showing LTP updates
  - [ ] SL checks completing in <50ms
  - [ ] No missed monitoring cycles
  - [ ] Positions not stuck (LTP updating every cycle)

- [ ] **Options Bot**
  - [ ] Check for new alerts in webhook logs
  - [ ] Verify option orders executing (check Angel One dashboard)
  - [ ] Confirm real premium prices (not test data)
  - [ ] Exit conditions triggering correctly (check positions.json)

### Trade Monitoring Specific Actions

**When Alert Received:**
1. [ ] Log shows webhook received (check timestamp)
2. [ ] Option contract fetched successfully
3. [ ] Real premium price loaded (not 0.00)
4. [ ] Order placed at correct price
5. [ ] Position added to monitor

**When Position Hit SL/Target:**
1. [ ] Monitor detected it (check LTP < SL)
2. [ ] Exit order placed immediately
3. [ ] Position removed from active list
4. [ ] P&L calculated correctly in logs
5. [ ] No "Failed to exit" messages

### Critical Red Flags 🚨

**STOP if you see:**
- [ ] **"RATE_LIMIT" error** → Indicates bulk fetch failed, system degrading
- [ ] **"position not found in angular"** → Market data stale/unavailable
- [ ] **"Failed to place SL"** after 3 retries → SL order failing consistently
- [ ] **Monitoring latency > 200ms** → System overloaded
- [ ] **Zero API calls in logs** → Monitoring not running
- [ ] **Same LTP for 5+ minutes** → Feed stuck/disconnected

**Action:** If any red flag appears:
1. Check `/equity/logs/monitor.log` for error details
2. Verify Angel One API status dashboard
3. Check network connectivity
4. Restart monitor process if needed
5. Alert user before continuing

---

## Key Metrics to Monitor

### Rate Limiting Success Criteria
| Metric | Target | Check Location |
|--------|--------|-----------------|
| API calls/min | <30 | monitor.log (search "req") |
| LTP bulk cache hit % | >90% | monitor.log (search "cache") |
| Rate utilization % | <10% | rate limiter debug logs |
| SL placement success % | 100% | monitor.log (search "order") |
| Monitoring latency | <50ms | monitor.log (search "latency") |

### Bulk Fetcher Specific Metrics
| Metric | Target | Check Location |
|--------|--------|-----------------|
| Bulk LTP calls per cycle | 1 (was 5) | monitor.log (search "bulk") |
| API reduction achieved | 80% | 5 calls → 1 call per bucket |
| Candle fetch success rate | 100% (when integrated) | logs (search "candle") |
| False breakout rate | <10% (when integrated) | options webhook logs |

---

## Logs to Monitor

### Primary Log File
```
/root/santhosh/trading/equity/logs/monitor.log
```

**Watch for:**
```
# Good signs
✅ "Bulk LTP fetch for bucket"
✅ "5 positions checked, 0 to exit"
✅ "Rate limiter: 5% utilization"
✅ "Cache hit for token 3045"

# Warning signs
⚠️ "API call failed, retrying..."
⚠️ "Fallback to individual LTP calls"
⚠️ "Rate utilization: 35%+"

# Critical signs 🚨
❌ "RATE_LIMIT rejected"
❌ "Failed to exit position after 3 attempts"
❌ "Monitoring cycle took 500ms+"
```

### Secondary Log Files
```
/root/santhosh/trading/options/logs/   # Options bot events
/root/santhosh/trading/logs/           # System-wide logs
```

---

## Command Reference for Monitoring

### Check Current Rate Limiter Status
```bash
tail -50 equity/logs/monitor.log | grep -i "rate\|utilization"
```

### Count API Calls in Last Hour
```bash
grep "$(date -d '1 hour ago' +%Y-%m-%d' '%H)" equity/logs/monitor.log | wc -l
```

### Find All Bulk LTP Calls
```bash
grep "bulk_ltp\|bulk.*fetch" equity/logs/monitor.log
```

### Check for Rate Limit Errors
```bash
grep -i "rate_limit\|rejected\|throttle" equity/logs/monitor.log
```

### Monitor Real-Time Logs
```bash
tail -f equity/logs/monitor.log
```

### Find Exit Events (SL/Target)
```bash
grep "exit\|position closed\|target\|stop" equity/logs/monitor.log
```

---

## Post-Market (After 3:30 PM)

### Daily Reconciliation
- [ ] **Count P&L Transactions**
  - [ ] Compare broker P&L vs system logs
  - [ ] Check that all exits recorded correctly
  - [ ] Verify no missed positions in morning

- [ ] **Review Performance Metrics**
  - [ ] API calls: Should be <600 for full day (target 80 calls/min avg)
  - [ ] Rate limit hits: Should be 0
  - [ ] SL placement success: Should be 100%
  - [ ] Monitoring latency: Should avg <50ms

- [ ] **Check Data Integrity**
  - [ ] positions.json matches Angel One holdings
  - [ ] backup files created (check timestamps)
  - [ ] No corrupted entries in logs

- [ ] **Prepare for Tomorrow**
  - [ ] Analyze any errors or warnings
  - [ ] Check if candle fetcher ready to integrate
  - [ ] Plan any optimizations needed

### Cleanup Tasks
```bash
# Archive old logs
gzip equity/logs/*.log

# Backup positions
cp equity/data/positions.json equity/data/positions.json.backup.$(date +%s)

# Clear temporary cache files (if any)
find . -name "*.pyc" -delete
find . -name "__pycache__" -type d -exec rm -rf {} +
```

---

## Integration Checklist (Optional, Phase 2)

### Candle Analyzer Integration
- [ ] Create enhanced entry validation in webhook_router.py
- [ ] Test breakout confirmation with live candle data
- [ ] Measure false entry reduction

### WebSocket Streaming Setup
- [ ] Implement `_fetch_via_streaming()` in BulkCandleFetcher
- [ ] Setup WebSocket connection pooling
- [ ] Monitor real-time candle updates
- [ ] Validate streaming vs API fallback behavior

### Advanced Monitoring Signals
- [ ] Add momentum-based SL to monitor.py
- [ ] Implement trend break exit logic
- [ ] Create candle analysis dashboard
- [ ] Add false move detection for position protection

---

## Success Validation Checklist

### By End of Market (Day 1)
- [x] Options bot trading with real market data (TECHM test done)
- [x] Equity bot monitoring with bulk LTP (integrated)
- [x] Rate limiter protecting against rejections (6-layer verification)
- [ ] Zero "RATE_LIMIT" errors in logs
- [ ] All SL orders executing correctly
- [ ] API calls <30 per minute

### By End of Week
- [ ] Consistent daily operation (no manual interventions)
- [ ] Candle fetcher integrated into webhook (optional)
- [ ] Monitoring latency consistently <50ms
- [ ] Rate utilization staying <10% even with 10+ positions
- [ ] False signal rate <10% (when candles integrated)

### By End of Month
- [ ] Fully automated system (no manual monitoring needed)
- [ ] P&L trending positive with validated exit strategy
- [ ] All 6 rate limit protection layers verified under load
- [ ] Ready for 100+ position scaling
- [ ] WebSocket streaming operational (if implemented)

---

## Emergency Procedures

### If Monitoring Stuck (No LTP updates for 5 min)
```bash
# 1. Check monitor process
ps aux | grep monitor.py

# 2. Restart monitor
cd equity && ./start_bot_enhanced.sh

# 3. Verify broker connectivity
python3 -c "from eqcode.angelone import AngelOneBroker; b = AngelOneBroker(); print(b.get_ltp('3045'))"
```

### If Rate Limiter Hitting Limit
```bash
# 1. Check current utilization
grep "utilization" equity/logs/monitor.log | tail -5

# 2. Reduce monitoring frequency (if needed)
# Edit monitor.py: MONITOR_INTERVAL = 10 (was 5)

# 3. Clear cache to reduce API load
# In monitor.py: self.bulk_ltp_fetcher.clear_cache()
```

### If Candle Fetcher Fails
```bash
# 1. Check if streaming available
python3 -c "from eqcode.bulk_candle_fetcher import BulkCandleFetcher; f = BulkCandleFetcher(None, None); print('Initialized')"

# 2. Fall back to historical API (automatic)
# System will automatically use historical API if streaming fails

# 3. Check Angel One getCandleData API status
curl https://api.angelbroking.com/rest/secure/historicalData/
```

---

## Support Contacts

- **Angel One Support:** https://www.angelbroking.com/support
- **API Status:** Check Angel One dashboard
- **System Logs:** `/root/santhosh/trading/equity/logs/monitor.log`

---

## Sign-Off

**System Verified By:** [Your Name]  
**Date:** 2024-12-[XX]  
**Status:** ✅ **READY FOR PRODUCTION**

### Confirmation Checklist
- [x] All code compiles without errors
- [x] All imports verified working
- [x] Rate limiter configured correctly
- [x] Bulk LTP integrated and tested
- [x] Documentation complete
- [x] Fallback mechanisms in place
- [x] Emergency procedures documented
- [x] Monitoring checklist prepared

**Approval:** System ready for live trading tomorrow. Monitor logs hourly and watch for rate limit errors (should be zero).
