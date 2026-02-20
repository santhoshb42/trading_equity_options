# PUT Options Bot - Configuration Guide

## Port Configuration ✅ COMPLETED

The PUT bot is now configured to listen on **port 8082** instead of 8081:

- **Webhook Endpoint**: `http://127.0.0.1:8082/webhook/put_options`
- **Configuration File**: `optcode/optconfig.py`
- **Environment Variable**: `PUT_OPTIONS_WEBHOOK_PORT=8082`

### Port Allocation:
- **Equity Bot**: Port 8080 (`/webhook/equity`)
- **CE Options Bot**: Port 8081 (`/webhook/options`)
- **PE Options Bot**: Port 8082 (`/webhook/put_options`) ← NEW

---

## Smart API Configuration

### Current Setup (Same Credentials as CE Bot)
Both the CE options bot and PUT options bot currently use the **same AngelOne credentials**:
```
ANGEL_API_KEY=1Ae3r5IS
ANGEL_CLIENT_CODE=S635655
ANGEL_PASSWORD=9172
ANGEL_TOTP_KEY=OZ5ZLOKKYQOIMP2YBW42EZYIWY
```

### Option 1: Use Same SmartAPI (Current Setup)
**Advantage**: Simpler setup, no changes needed
**Disadvantage**: Rate limits may apply if both bots trade heavily

**Status**: ✅ Ready to use

---

## Option 2: Use Different SmartAPI Instance (Recommended for Scale)

If you have a second AngelOne account or access to a different Smart API provider (Shoonya, Kite, etc.), follow these steps:

### Step 1: Create New Broker Config (Optional)
```python
# File: put_options/optcode/put_broker_config.py
class PutBrokerConfig:
    """Separate broker configuration for PUT options bot"""
    
    API_KEY = os.getenv("PUT_ANGEL_API_KEY", "")  # Different API key
    CLIENT_CODE = os.getenv("PUT_ANGEL_CLIENT_CODE", "")  # Different client
    PASSWORD = os.getenv("PUT_ANGEL_PASSWORD", "")
    TOTP_KEY = os.getenv("PUT_ANGEL_TOTP_KEY", "")
```

### Step 2: Update .env with New Credentials
```env
# CE Options Bot (Original)
ANGEL_API_KEY=1Ae3r5IS
ANGEL_CLIENT_CODE=S635655
ANGEL_PASSWORD=9172
ANGEL_TOTP_KEY=OZ5ZLOKKYQOIMP2YBW42EZYIWY

# PUT Options Bot (New/Different)
PUT_ANGEL_API_KEY=<NEW_API_KEY>
PUT_ANGEL_CLIENT_CODE=<NEW_CLIENT_CODE>
PUT_ANGEL_PASSWORD=<NEW_PASSWORD>
PUT_ANGEL_TOTP_KEY=<NEW_TOTP_KEY>
```

### Step 3: Update Broker Initialization
```python
# In put_options/main.py, modify angelone_options import:
# Instead of:
#   from optcode.angelone_options import get_options_broker
# Use:
#   from put_options.optcode.put_angelone_options import get_put_options_broker
```

---

## Using Alternative Smart API (Shoonya/Aliceblue)

If switching to **Shoonya** or **Aliceblue**, you'll need to:

1. Create a broker adapter: `put_options/optcode/shoonya_options.py`
2. Implement the same interface as `angelone_options.py`:
   - `place_options_order()`
   - `modify_sl_order()`
   - `get_option_price()`
   - `get_greeks()`
   - `get_iv_percentile()`

3. Update `main.py`:
```python
# from optcode.angelone_options import get_options_broker
from optcode.shoonya_options import get_shoonya_broker  # Alternative
```

---

## Current Status

✅ **Port Configuration**: 8082 configured in `optcode/optconfig.py`
✅ **Webhook Endpoint**: `/webhook/put_options` active
✅ **Same Broker**: Ready to use existing AngelOne credentials

⚠️ **If Using Different Broker**: 
- Requires broker adapter implementation
- Update environment variables with new credentials
- Modify main.py initialization

---

## Testing the Configuration

### Test Port Availability:
```bash
# Check if port 8082 is available
netstat -tlnp | grep 8082

# Or
ss -tlnp | grep 8082
```

### Test Webhook Connectivity:
```bash
# Test PUT bot webhook
curl -X POST http://127.0.0.1:8082/webhook/put_options \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "RELIANCE",
    "action": "SELL",
    "confidence": 85,
    "reasons": ["downtrend", "momentum"]
  }'
```

---

## Environment Variables Summary

| Variable | CE Options | PUT Options | Location |
|----------|-----------|-------------|----------|
| Webhook Port | 8081 | 8082 | `.env` |
| Port ENV Var | `OPTIONS_WEBHOOK_PORT` | `PUT_OPTIONS_WEBHOOK_PORT` | `.env` |
| Endpoint | `/webhook/options` | `/webhook/put_options` | `optconfig.py` |
| Client Code | `S635655` | Same (or PUT_ANGEL_CLIENT_CODE if different) | `.env` |
| Max Capital | 9,00,000 | 30,00,000 (configurable) | `.env` |
| Max Slots | 100 | 100 | `.env` |

---

## Next Steps

1. **Verify Port 8082 is available** (not in use)
2. **Update TradingView webhook** to send SELL signals to `http://your-server:8082/webhook/put_options`
3. **Start the PUT bot**: `python3 main.py` (from put_options directory)
4. **Monitor logs** for successful webhook connections
5. **Do NOT start trading** until all PE-specific logic fixes are complete (see PUT_OPTIONS_CRITICAL_CHANGES.md)

---

## Multiple Bot Startup Command

Once both configured and ready:

```bash
# Terminal 1: CE Options Bot (Port 8081)
cd /root/santhosh/trading/options && python3 main.py

# Terminal 2: PUT Options Bot (Port 8082)
cd /root/santhosh/trading/put_options && python3 main.py

# Terminal 3: Equity Bot (Port 8080) - if running
cd /root/santhosh/trading/equity && python3 main.py
```

---

## Troubleshooting

**Error**: "Address already in use"
- Solution: Check what's using port 8082: `lsof -i :8082`
- Kill process if needed: `kill -9 <PID>`

**Error**: "Failed to connect to broker"
- Solution: Verify AngelOne credentials in `.env`
- Check TOTP key is valid and time-synced

**Error**: "Webhook not receiving signals"
- Solution: Verify TradingView webhook URL is correct: `http://your-server:8082/webhook/put_options`
- Check firewall allows port 8082
