# Quick Reference - PUT Bot Configuration

## Current Status: READY ✅

```
Port:            8082 ✅
Endpoint:        /webhook/put_options ✅
Strike Logic:    PE (lower strikes) ✅
Entry Signals:   SELL (downside) ✅
SL Logic:        Reversed for PE ✅
Blacklist:       25 symbols ✅
Broker:          AngelOne (same as CE) ✅
```

---

## Start Commands

### Option 1: Use Current Broker (Easiest) 
```bash
cd /root/santhosh/trading/put_options
python3 main.py
```

### Option 2: Use Different SmartAPI (Future)
**If you provide separate broker credentials:**

```bash
# Update .env with new credentials
vi /root/santhosh/trading/put_options/.env

# Then start
cd /root/santhosh/trading/put_options
python3 main.py
```

---

## Test Webhook After Starting

```bash
curl -X POST http://127.0.0.1:8082/webhook/put_options \
  -H 'Content-Type: application/json' \
  -d '{"symbol": "RELIANCE", "action": "SELL", "confidence": 85, "reasons": ["downtrend"]}'
```

Expected response: `{"status": "success", "message": "Alert processed"}`

---

## Check Logs

```bash
tail -f /root/santhosh/trading/put_options/logs/put_options/bot.log
```

---

## All Files Updated

1. ✅ `optcode/optconfig.py` - Port 8082
2. ✅ `optcode/strike_selector.py` - PE logic
3. ✅ `optcode/entry_filter_engine.py` - SELL signals + blacklist
4. ✅ `optcode/optmonitor.py` - Reversed HARD_SL
5. ✅ `.env` - PUT_OPTIONS_WEBHOOK_PORT=8082
6. ✅ Documentation - 4 guides + summary

---

## Ready For:
- ✅ Starting bot
- ✅ Testing webhooks
- ✅ Paper trading
- ✅ Logging to verify logic
- ⚠️ NOT yet for live trading (verify first)

---

## Questions?

1. **Different Broker?** → See PUT_SMARTAPI_SETUP.md
2. **Different Port?** → Update PUT_OPTIONS_WEBHOOK_PORT in .env
3. **Verify Config?** → Run: `./verify_config.sh`
4. **Change Capital?** → Update OPTIONS_MAX_CAPITAL in .env
