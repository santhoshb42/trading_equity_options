# TRADING READY - QUICK START GUIDE (Dec 17)

## ✅ System Status: READY

```
Equity Bot:   PAPER mode ✅ (no real money)
Options Bot:  PAPER mode ✅ (no real money)
Webhook:      Running ✅
Alert Routing: Both bots ✅
```

---

## BEFORE 9:30 AM (Market Open)

```bash
# Verify bots are running
curl -s http://127.0.0.1:8080/health | jq '.status'    # Should say "healthy"
curl -s http://127.0.0.1:8081/health | jq '.status'    # Should say "healthy"

# Check trading mode
grep "PAPER\|LIVE" /root/santhosh/trading/equity/logs/*/statistics.log | tail -1
grep "mode\|PAPER\|LIVE" /root/santhosh/trading/options/logs/*/optbot.log | tail -1
```

---

## WHAT'S DIFFERENT FROM YESTERDAY

### ✅ FIXED
1. Equity bot now in **PAPER mode** (was LIVE - dangerous!)
2. Webhook router improved (30s timeout, retry logic)
3. Both bots receive all alerts (verified routing)

### ⚠️ KNOWN LIMITATION
- Options bot webhook slow (30s) when receiving alerts during bulk data fetch
- **Solution**: Keep TradingView alert spacing 2-5 seconds apart

---

## EXPECTED BEHAVIOR

### When You Send an Alert:
```
1. TradingView → Router (port 80) ← 1 second
2. Router → Equity Bot (port 8080) ← 0.5 seconds
3. Router → Options Bot (port 8081) ← 20-30 seconds (may be slow)
4. Both bots process alert independently
5. Orders placed in PAPER mode (simulated, not real)
6. Monitoring tracks position
7. SELL order placed when profit/stop hit
```

---

## MONITORING

### Check if alerts were received:
```bash
tail /root/santhosh/trading/equity/logs/$(date +%Y-%m-%d)/statistics.log
tail /root/santhosh/trading/options/logs/$(date +%Y-%m-%d)/optbot.log
```

### Check bot positions:
```bash
curl -s http://127.0.0.1:8081/health | jq '.open_positions'  # Options positions
```

### Check for errors:
```bash
grep -i "ERROR\|FAIL\|timeout" /root/santhosh/trading/equity/logs/$(date +%Y-%m-%d)/statistics.log
grep -i "ERROR\|FAIL\|timeout" /root/santhosh/trading/options/logs/$(date +%Y-%m-%d)/optbot.log
```

---

## IF SOMETHING GOES WRONG

### Equity Bot not responding:
```bash
ps aux | grep "python.*equity.*main.py"  # Check if running
tail /root/santhosh/trading/equity/logs/$(date +%Y-%m-%d)/statistics.log
```

### Options Bot not responding:
```bash
ps aux | grep "python.*options.*main.py"  # Check if running
tail /root/santhosh/trading/options/logs/$(date +%Y-%m-%d)/optbot.log
```

### Restart both bots:
```bash
pkill -f "python.*main.py"
sleep 3
cd /root/santhosh/trading && nohup python3 equity/main.py > /tmp/eq.log 2>&1 &
cd /root/santhosh/trading && nohup python3 options/main.py > /tmp/opt.log 2>&1 &
sleep 8
# Verify both healthy:
curl -s http://127.0.0.1:8080/health | jq '.status'
curl -s http://127.0.0.1:8081/health | jq '.status'
```

---

## KEY FILES FOR MONITORING

```
Equity logs:        /root/santhosh/trading/equity/logs/2025-12-17/statistics.log
Options logs:       /root/santhosh/trading/options/logs/2025-12-17/optbot.log
Router logs:        /root/santhosh/trading/equity/logs/webhook_router_2025-12-17.log
Alert history:      /root/santhosh/trading/equity/logs/2025-12-17/alerts.log
```

---

## TEST RESULTS FROM DEC 16

```
✅ 5 alerts tested successfully
✅ Both bots received all alerts
✅ Equity bot processing: OK
✅ Options bot processing: OK (slow webhook, expected)
✅ Bulk LTP operations: Working
✅ Rate limiting: No issues
✅ Paper mode: Confirmed for both bots
```

---

## IMPORTANT REMINDERS

1. **BOTH BOTS IN PAPER MODE** - No real orders will be placed ✅
2. **Alert spacing** - Keep TradingView alerts 2-5 seconds apart
3. **Monitor first 5 alerts** - Watch logs for any unexpected behavior
4. **Bulk operations working** - System efficiently handles 50+ symbols
5. **Rate limiting resolved** - Dec 16 morning errors are fixed

---

## SUPPORT

All changes committed to git:
```
git log --oneline | head -5
```

View what was changed:
```
git diff HEAD~5..HEAD -- equity/eqcode/config.py webhook_router.py
```

---

**System Status**: ✅ READY FOR TRADING  
**Last Test**: Dec 16, 14:08 UTC  
**Mode**: PAPER (Safe)  

Good luck! 🚀
