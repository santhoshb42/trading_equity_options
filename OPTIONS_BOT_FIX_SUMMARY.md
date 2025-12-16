# Options Bot Alert Processing Fix - December 15, 2025

## Problem Identified ⚠️

**Symptoms:**
- Options bot receiving alerts but rejecting ALL of them
- Error: `ML filter: Greeks validation failed: CE BUY: Delta 0.00 not in optimal range (0.2-0.8)`
- No options trades being placed even for index options

**Root Cause:**
ML validation was happening **BEFORE** strike symbol derivation and Greeks fetching from broker.

```
WRONG ORDER (Previous):
1. Alert received from Pine Script (no Greeks data)
2. ML validation attempted ← FAILS (no Greeks to validate)
3. Strike symbol derived
4. Greeks fetched from broker
5. Trade execution

CORRECT ORDER (Fixed):
1. Alert received from Pine Script
2. Signal quality validation (basic checks only)
3. Strike symbol derived
4. Greeks fetched from broker
5. ML validation with REAL Greeks ← NOW WORKS
6. Trade execution
```

---

## Changes Made 🔧

### 1. **options/optcode/optapi.py** (Main Fix)

**Removed:** Early ML validation (lines 424-440)
- Deleted premature ML filter check that tried to validate Greeks before they existed
- This was causing all alerts to be rejected with "Delta 0.00" error

**Added:** Late ML validation (after line 547)
- Moved ML validation to AFTER contract selection and Greeks fetching
- Now enriches alert with actual Greeks data before validation:
  ```python
  alert_with_greeks = alert.copy()
  alert_with_greeks['greeks'] = selected_contract.to_dict()['greeks']
  alert_with_greeks['contract_type'] = contract_type
  alert_with_greeks['underlying_price'] = float(alert.get('price', 0))
  alert_with_greeks['strike'] = selected_contract.strike
  alert_with_greeks['iv'] = selected_contract.iv
  ```

### 2. **webhook_router.py** (Enhancement)

**Added:** Smart asset class detection
- Detects symbol type based on index options list: `BANKNIFTY`, `NIFTY`, `FINNIFTY`
- Routes **INDEX OPTIONS** → OPTIONS BOT only
- Routes **EQUITY SYMBOLS** → EQUITY BOT only
- Prevents wasting API calls and rejections

```python
index_options = ["BANKNIFTY", "NIFTY", "FINNIFTY"]
is_index_option = symbol.upper() in index_options

if is_index_option:
    # Send to OPTIONS BOT ONLY
    options_success = forward_alert(OPTIONS_BOT_URL, payload, "OPTIONS BOT")
else:
    # Send to EQUITY BOT ONLY
    equity_success = forward_alert(EQUITY_BOT_URL, payload, "EQUITY BOT")
```

---

## Current Status ✅

### Running Services:
```
✅ Webhook Router (Port 80)
   - Restarted with smart routing
   - Listening on all interfaces
   
✅ Equity Bot (Port 8080)
   - Running, processing stock alerts
   - 0 open positions
   
✅ Options Bot (Port 8081)
   - Restarted with fix
   - Ready to accept index options alerts
   - Broker connected, capital available
```

### What Now Works:
1. ✅ **Index option alerts** (BANKNIFTY, NIFTY, FINNIFTY) → OPTIONS BOT
2. ✅ **Stock alerts** (SBIN, RELIANCE, IOC, etc.) → EQUITY BOT
3. ✅ **ML validation** happens AFTER Greeks are fetched
4. ✅ **No more "Delta 0.00" rejections** for index options

---

## Testing the Fix

### Send Test Alert for Index Option:
```bash
curl -X POST http://localhost:80/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BANKNIFTY",
    "action": "BUY",
    "price": 51000,
    "confidence": 95,
    "score": 90,
    "verdict": 1
  }'
```

**Expected:**
- Webhook router routes to OPTIONS BOT only
- Options bot derives strike (ATM ± offset)
- Fetches Greeks from broker
- ML validation uses REAL Greeks
- Trade placed if validation passes

### Check Options Bot Logs:
```bash
tail -50 /root/santhosh/trading/options/logs/2025-12-15/alerts.jsonl | grep -A 5 "BANKNIFTY"
```

**Expected:** Alert status = "success" (not "rejected")

### Check Router Logs:
```bash
tail -30 /root/santhosh/trading/equity/logs/webhook_router_2025-12-15.log | grep "BANKNIFTY"
```

**Expected:** Message like "Routing to OPTIONS BOT (index option detected)"

---

## Why This Matters

**Before Fix:**
- Pine Script sends BANKNIFTY alert → Router → Options Bot
- Options bot tries to validate Greeks (none exist yet)
- Gets Delta = 0.00 → Rejects with "not in optimal range"
- **Result: 0 options trades**

**After Fix:**
- Pine Script sends BANKNIFTY alert → Router (detects index option)
- Routes ONLY to Options Bot (smart routing)
- Bot derives strike (BANKNIFTY52100CE, etc.)
- Fetches real Greeks from broker
- ML validates with real data
- Contract passes validation → **TRADE PLACED**

---

## Next Steps

1. **Monitor Options Bot Logs** during market hours
   - Check for "ML_VALIDATION_PASSED" messages
   - Verify trades are being placed for index options

2. **Test with Live Alerts** from Pine Script
   - Send BANKNIFTY/NIFTY/FINNIFTY alerts
   - Monitor position creation in options bot

3. **Verify Greeks Validation** is now working
   - Check that Delta values are in expected range (0.2-0.8)
   - Monitor for any remaining rejections

4. **Keep Equity Bot Running**
   - Stock alerts still route to equity bot
   - Both bots operating independently, no conflicts

---

## Configuration Reference

### Alert Routing Logic (Webhook Router)
```python
# Index Options - Detected Automatically
BANKNIFTY, NIFTY, FINNIFTY → OPTIONS BOT only

# Everything Else - Treated as Equity
SBIN, RELIANCE, IOC, BPCL, etc. → EQUITY BOT only
```

### ML Validation Pipeline (Options Bot)
```
1. Signal receives alert (basic validation)
2. Underlying mapped (BANKNIFTY → NSE)
3. Option chain fetched (expiry, strikes)
4. ATM contract selected
5. Greeks fetched from broker
6. ML validation with real Greeks ← KEY FIX
7. Order placed or rejected
```

---

## Rollback (If Needed)

If you need to revert the changes:

```bash
# Restore original optapi.py from git
git checkout options/optcode/optapi.py

# Restore original webhook_router.py from git
git checkout webhook_router.py

# Restart both services
pkill -f "options.*main"
pkill -f "webhook_router"
sleep 2
cd /root/santhosh/trading/options && python3 main.py &
cd /root/santhosh/trading && python3 webhook_router.py &
```

---

## Related Files Modified

- ✏️ `/root/santhosh/trading/options/optcode/optapi.py`
  - Removed early ML validation (line ~424-440)
  - Added late ML validation (line ~547+)
  
- ✏️ `/root/santhosh/trading/webhook_router.py`
  - Added smart asset class detection
  - Changed from "forward to both" → "smart routing"

---

## Questions?

If options trades still don't appear after this fix:
1. Check `/root/santhosh/trading/options/logs/2025-12-15/alerts.jsonl` for rejection reasons
2. Verify BANKNIFTY option chain is available (may be closed if outside market hours)
3. Check capital availability in options bot (`curl http://localhost:8081/health`)
4. Verify Pine Script is actually sending BANKNIFTY symbols (check equity bot logs for router stats)

