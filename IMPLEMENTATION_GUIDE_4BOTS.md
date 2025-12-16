# Implementation Guide: Adding 2 More Bots

## Step-by-Step Walkthrough

### STEP 1: Create Directory Structure (5 minutes)

```bash
# Create directories for Bot 3 (Equity Bot 2)
mkdir -p /root/santhosh/trading/equity2/data
mkdir -p /root/santhosh/trading/equity2/logs
mkdir -p /root/santhosh/trading/equity2/tools

# Create directories for Bot 4 (Options Bot 2)
mkdir -p /root/santhosh/trading/options2/data
mkdir -p /root/santhosh/trading/options2/logs
mkdir -p /root/santhosh/trading/options2/tools
```

### STEP 2: Copy Main Entry Points (5 minutes)

```bash
# Copy main.py files
cp /root/santhosh/trading/equity/main.py /root/santhosh/trading/equity2/
cp /root/santhosh/trading/options/main.py /root/santhosh/trading/options2/

# Create symlinks to shared code (avoids duplication)
ln -s ../equity/eqcode /root/santhosh/trading/equity2/eqcode
ln -s ../equity/tools /root/santhosh/trading/equity2/tools
ln -s ../equity/docs /root/santhosh/trading/equity2/docs

ln -s ../options/optcode /root/santhosh/trading/options2/optcode
ln -s ../options/tools /root/santhosh/trading/options2/tools
ln -s ../options/docs /root/santhosh/trading/options2/docs
```

### STEP 3: Create Configuration Files (10 minutes)

#### equity2/.env

```bash
cat > /root/santhosh/trading/equity2/.env << 'EOF'
# Equity Trading Bot 2 Configuration

# Webhook Server Configuration
WEBHOOK_PORT=8082
WEBHOOK_HOST=0.0.0.0

# AngelOne Broker Configuration
# Change these if using separate broker account
ANGEL_API_KEY=<YOUR_API_KEY>
ANGEL_CLIENT_CODE=<YOUR_CLIENT_CODE_2>
ANGEL_PASSWORD=<YOUR_PASSWORD>
ANGEL_TOTP_SECRET=<YOUR_TOTP_SECRET>

# Trading Configuration
TRADING_MODE=PAPER
MAX_CAPITAL=100000
CAP_PER_TRADE=2000
MAX_SLOTS=5
DEFAULT_SL_PERCENTAGE=2.0
TARGET_PROFIT_PERCENTAGE=3.0

# Monitoring Interval
MONITOR_INTERVAL=20

# Risk Management
DAILY_LOSS_LIMIT=10000
MAX_LOSS_PERCENTAGE=10

# Market Hours (IST - India Standard Time)
MARKET_START_TIME=09:15
MARKET_END_TIME=15:30
EOF

echo "✅ Created equity2/.env"
```

#### options2/.env

```bash
cat > /root/santhosh/trading/options2/.env << 'EOF'
# Options Trading Bot 2 Configuration

# Webhook Server Configuration
OPTIONS_WEBHOOK_PORT=8083
WEBHOOK_HOST=0.0.0.0

# AngelOne Broker Configuration
ANGEL_API_KEY=<YOUR_API_KEY>
ANGEL_CLIENT_CODE=<YOUR_CLIENT_CODE_2>
ANGEL_PASSWORD=<YOUR_PASSWORD>
ANGEL_TOTP_KEY=<YOUR_TOTP_SECRET>

# Trading Configuration
TRADING_MODE=PAPER
OPTIONS_MAX_CAPITAL=900000
OPTIONS_CAP_PER_TRADE=30000
OPTIONS_MAX_SLOTS=30

# Options Parameters
MIN_CONFIDENCE=90
MIN_SIGNAL_QUALITY=90
IV_PERCENTILE_MIN=30
IV_PERCENTILE_MAX=90
MAX_DELTA=0.8
MIN_DELTA=0.2

# Monitoring
OPTIONS_MONITOR_INTERVAL=5

# Greeks Monitoring
GREEKS_UPDATE_INTERVAL=10

# Market Hours (IST)
MARKET_START_TIME=09:15
MARKET_END_TIME=15:30
EOF

echo "✅ Created options2/.env"
```

### STEP 4: Initialize Data Files (5 minutes)

```bash
# Create empty position files
cat > /root/santhosh/trading/equity2/data/positions.json << 'EOF'
{}
EOF

cat > /root/santhosh/trading/options2/data/positions.json << 'EOF'
{}
EOF

# Create session files (for broker authentication)
cat > /root/santhosh/trading/equity2/data/session.json << 'EOF'
{}
EOF

cat > /root/santhosh/trading/options2/data/session.json << 'EOF'
{}
EOF

echo "✅ Created initial data files"
```

### STEP 5: Update Webhook Router (10 minutes)

**File:** `/root/santhosh/trading/webhook_router.py`

Find these lines (~line 38-40):
```python
# Downstream bot endpoints
EQUITY_BOT_URL = os.getenv("EQUITY_BOT_URL", "http://127.0.0.1:8080/webhook")
OPTIONS_BOT_URL = os.getenv("OPTIONS_BOT_URL", "http://127.0.0.1:8081/webhook/options")
```

Replace with:
```python
# Downstream bot endpoints
EQUITY_BOT_1_URL = os.getenv("EQUITY_BOT_1_URL", "http://127.0.0.1:8080/webhook")
EQUITY_BOT_2_URL = os.getenv("EQUITY_BOT_2_URL", "http://127.0.0.1:8082/webhook")
OPTIONS_BOT_1_URL = os.getenv("OPTIONS_BOT_1_URL", "http://127.0.0.1:8081/webhook/options")
OPTIONS_BOT_2_URL = os.getenv("OPTIONS_BOT_2_URL", "http://127.0.0.1:8083/webhook/options")
```

Find the `handle_webhook()` function (around line 95), locate:
```python
# Forward to both bots
logger.info(f"🔄 Forwarding alert to both bots...")

equity_success = forward_alert(EQUITY_BOT_URL, payload, "EQUITY BOT")
options_success = forward_alert(OPTIONS_BOT_URL, payload, "OPTIONS BOT")

if equity_success:
    STATS["equity_forwarded"] += 1
if options_success:
    STATS["options_forwarded"] += 1
```

Replace with:
```python
# Forward to all bots
logger.info(f"🔄 Forwarding alert to all 4 bots...")

equity_1_success = forward_alert(EQUITY_BOT_1_URL, payload, "EQUITY BOT 1")
equity_2_success = forward_alert(EQUITY_BOT_2_URL, payload, "EQUITY BOT 2")
options_1_success = forward_alert(OPTIONS_BOT_1_URL, payload, "OPTIONS BOT 1")
options_2_success = forward_alert(OPTIONS_BOT_2_URL, payload, "OPTIONS BOT 2")

if equity_1_success or equity_2_success:
    STATS["equity_forwarded"] += 1
if options_1_success or options_2_success:
    STATS["options_forwarded"] += 1
```

### STEP 6: Create Systemd Service Files (15 minutes)

#### /etc/systemd/system/equity-bot-1.service

```ini
[Unit]
Description=Equity Trading Bot 1
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/santhosh/trading/equity
ExecStart=/usr/bin/python3 /root/santhosh/trading/equity/main.py
Restart=always
RestartSec=10
StandardOutput=append:/root/santhosh/trading/equity/logs/systemd.log
StandardError=append:/root/santhosh/trading/equity/logs/systemd.log

[Install]
WantedBy=multi-user.target
```

#### /etc/systemd/system/equity-bot-2.service

```ini
[Unit]
Description=Equity Trading Bot 2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/santhosh/trading/equity2
ExecStart=/usr/bin/python3 /root/santhosh/trading/equity2/main.py
Restart=always
RestartSec=10
StandardOutput=append:/root/santhosh/trading/equity2/logs/systemd.log
StandardError=append:/root/santhosh/trading/equity2/logs/systemd.log

[Install]
WantedBy=multi-user.target
```

#### /etc/systemd/system/options-bot-1.service

```ini
[Unit]
Description=Options Trading Bot 1
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/santhosh/trading/options
ExecStart=/usr/bin/python3 /root/santhosh/trading/options/main.py
Restart=always
RestartSec=10
StandardOutput=append:/root/santhosh/trading/options/logs/systemd.log
StandardError=append:/root/santhosh/trading/options/logs/systemd.log

[Install]
WantedBy=multi-user.target
```

#### /etc/systemd/system/options-bot-2.service

```ini
[Unit]
Description=Options Trading Bot 2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/santhosh/trading/options2
ExecStart=/usr/bin/python3 /root/santhosh/trading/options2/main.py
Restart=always
RestartSec=10
StandardOutput=append:/root/santhosh/trading/options2/logs/systemd.log
StandardError=append:/root/santhosh/trading/options2/logs/systemd.log

[Install]
WantedBy=multi-user.target
```

### STEP 7: Manual Testing (30 minutes per bot)

#### Test Bot 3 (Equity Bot 2)

```bash
# Start the bot manually
cd /root/santhosh/trading/equity2
python3 main.py

# In another terminal, monitor logs
tail -f /root/santhosh/trading/equity2/logs/2025-12-*/alerts.log
tail -f /root/santhosh/trading/equity2/logs/2025-12-*/events.log

# Verify startup sequence:
# 1. Configuration validation message
# 2. Broker login message
# 3. Monitor initialization
# 4. "Webhook server started on 0.0.0.0:8082"
# 5. Ready for alerts

# If successful: Press Ctrl+C to stop
```

#### Test Bot 4 (Options Bot 2)

```bash
# Start the bot manually
cd /root/santhosh/trading/options2
python3 main.py

# Monitor logs
tail -f /root/santhosh/trading/options2/logs/2025-12-*/alerts.jsonl
tail -f /root/santhosh/trading/options2/logs/2025-12-*/events.jsonl

# Verify:
# 1. Configuration validation
# 2. Broker authentication
# 3. Instrument manager loaded
# 4. Option chain cache initialized
# 5. "Webhook API server listening on 0.0.0.0:8083"

# If successful: Press Ctrl+C to stop
```

### STEP 8: Send Test Alerts (10 minutes)

```bash
# Test that all 4 bots receive the alert

# Send one alert to router (port 80)
curl -X POST http://localhost:80/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "SBIN-EQ",
    "action": "BUY",
    "price": 500.50,
    "confidence": 95,
    "score": 90,
    "verdict": 1
  }'

# Check equity bot 1 logs
tail -5 /root/santhosh/trading/equity/logs/2025-12-*/alerts.log
# Should see: SBIN-EQ BUY alert received

# Check equity bot 2 logs
tail -5 /root/santhosh/trading/equity2/logs/2025-12-*/alerts.log
# Should see: SBIN-EQ BUY alert received

# Check both received same alert ✅
```

### STEP 9: Register Systemd Services (10 minutes)

```bash
# Reload systemd daemon to recognize new services
sudo systemctl daemon-reload

# Enable all 4 services to start on reboot
sudo systemctl enable equity-bot-1.service
sudo systemctl enable equity-bot-2.service
sudo systemctl enable options-bot-1.service
sudo systemctl enable options-bot-2.service

# Start all services
sudo systemctl start equity-bot-1.service
sudo systemctl start equity-bot-2.service
sudo systemctl start options-bot-1.service
sudo systemctl start options-bot-2.service

# Verify all are running
sudo systemctl status equity-bot-1.service
sudo systemctl status equity-bot-2.service
sudo systemctl status options-bot-1.service
sudo systemctl status options-bot-2.service

# Expected output: "● equity-bot-1.service - Equity Trading Bot 1"
#                  "Loaded: loaded (/etc/systemd/system/...)"
#                  "Active: active (running)"
```

### STEP 10: Monitor Dashboard (Ongoing)

```bash
# Create a monitoring script to check all 4 bots
cat > /root/santhosh/trading/monitor_all_bots.sh << 'EOF'
#!/bin/bash

echo "================================================"
echo "     TRADING SYSTEM STATUS - 4 BOTS"
echo "================================================"

echo ""
echo "SYSTEMD STATUS:"
echo "───────────────"
systemctl status equity-bot-1.service | grep "Active:"
systemctl status equity-bot-2.service | grep "Active:"
systemctl status options-bot-1.service | grep "Active:"
systemctl status options-bot-2.service | grep "Active:"

echo ""
echo "PROCESS HEALTH:"
echo "───────────────"
ps aux | grep "python3.*main.py" | grep -v grep | wc -l
echo "Bots running (should be 4):"

echo ""
echo "PORT STATUS:"
echo "────────────"
echo "Port 8080 (Equity Bot 1):"
lsof -i :8080 | grep LISTEN || echo "NOT LISTENING"

echo "Port 8081 (Options Bot 1):"
lsof -i :8081 | grep LISTEN || echo "NOT LISTENING"

echo "Port 8082 (Equity Bot 2):"
lsof -i :8082 | grep LISTEN || echo "NOT LISTENING"

echo "Port 8083 (Options Bot 2):"
lsof -i :8083 | grep LISTEN || echo "NOT LISTENING"

echo ""
echo "RECENT LOGS (last alert from each bot):"
echo "──────────────────────────────────────"
echo "Equity Bot 1:"
tail -1 /root/santhosh/trading/equity/logs/*/alerts.log 2>/dev/null | head -1

echo "Equity Bot 2:"
tail -1 /root/santhosh/trading/equity2/logs/*/alerts.log 2>/dev/null | head -1

echo "Options Bot 1:"
tail -1 /root/santhosh/trading/options/logs/*/alerts.jsonl 2>/dev/null | head -1 | cut -c1-80

echo "Options Bot 2:"
tail -1 /root/santhosh/trading/options2/logs/*/alerts.jsonl 2>/dev/null | head -1 | cut -c1-80

echo ""
echo "================================================"
EOF

chmod +x /root/santhosh/trading/monitor_all_bots.sh

# Run the monitoring script
/root/santhosh/trading/monitor_all_bots.sh
```

---

## Verification Checklist

After completing all steps, verify:

```
□ equity2/ directory exists with structure
□ options2/ directory exists with structure
□ Symlinks created (eqcode, optcode)
□ equity2/.env file configured
□ options2/.env file configured
□ webhook_router.py updated with new endpoints
□ Systemd service files created in /etc/systemd/system/
□ All 4 services enabled: systemctl list-unit-files | grep bot
□ All 4 services running: systemctl status *-bot-*.service
□ Ports 8080-8083 listening: lsof -i :8080 etc.
□ Test alert sent and received by all 4 bots
□ Logs show no errors in any bot
```

---

## Post-Deployment: First Week

### Daily Monitoring

```bash
# Run this daily to check health
/root/santhosh/trading/monitor_all_bots.sh

# Check logs for errors
grep -i "error\|exception\|failed" /root/santhosh/trading/equity*/logs/*/events.log
grep -i "error\|exception\|failed" /root/santhosh/trading/options*/logs/*/events.log

# Check rate limiter status
grep "RATE_LIMIT\|AG8001" /root/santhosh/trading/*/logs/*/*.log
# Should NOT see any AG8001 errors
```

### Week 1 Goals

- [ ] All 4 bots running stably for 5 trading days
- [ ] No crashes or unexpected restarts
- [ ] Each bot receives and processes alerts
- [ ] No rate limiting errors (AG8001)
- [ ] Memory usage stable (~600-800 MB)
- [ ] CPU usage <20%
- [ ] Logs clean (no error messages)

### Metrics to Track

```bash
# Create metrics collection script
cat > /root/santhosh/trading/collect_metrics.sh << 'EOF'
#!/bin/bash

DATE=$(date '+%Y-%m-%d %H:%M:%S')
MEMORY=$(ps aux | grep "python3.*main.py" | grep -v grep | awk '{print $6}' | awk '{s+=$1} END {print s}')
CPU=$(ps aux | grep "python3.*main.py" | grep -v grep | awk '{print $3}' | awk '{s+=$1} END {print s}')

echo "$DATE | Memory: ${MEMORY}KB | CPU: ${CPU}%" >> /root/santhosh/trading/metrics.log
EOF

# Schedule daily metric collection
(crontab -l 2>/dev/null; echo "0 * * * * /root/santhosh/trading/collect_metrics.sh") | crontab -

# Review after week 1
tail -50 /root/santhosh/trading/metrics.log
```

---

## Troubleshooting

### Bot Doesn't Start

**Problem:** `python3 main.py` exits immediately

**Solution:**
```bash
cd /root/santhosh/trading/equity2
python3 main.py 2>&1 | head -50
# Look for error message in first 50 lines

# Common issues:
# 1. Port already in use: Change WEBHOOK_PORT in .env
# 2. Missing .env file: Create it (see STEP 3)
# 3. Broker authentication: Check ANGEL_API_KEY, etc. in .env
# 4. Missing symlinks: Create them (see STEP 2)
```

### Bot Starts But Doesn't Receive Alerts

**Problem:** Bot running but no alerts in logs

**Solution:**
```bash
# 1. Check webhook router is running
ps aux | grep webhook_router

# 2. Verify port is correct
curl http://localhost:8082/health
# Should return 200 OK

# 3. Send manual test alert
curl -X POST http://localhost:8082/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol": "TEST", "action": "BUY"}'

# 4. Check logs
tail -20 /root/santhosh/trading/equity2/logs/*/alerts.log
```

### High Memory Usage

**Problem:** Bot using >300 MB memory

**Solution:**
```bash
# Check what's using memory
ps aux | grep python | grep main

# Look for leaks in option chain cache
du -sh /root/santhosh/trading/options2/data/
# If >100 MB, clear cache:
rm /root/santhosh/trading/options2/data/option_chain_cache.json
# Restart bot

# Check for circular logging
ls -lh /root/santhosh/trading/*/logs/*/
# If any log >100 MB, they're rotating incorrectly
```

---

## Success Criteria (End of Week 1)

If you see this, you're good to go:

```
✅ 4 bots all running (systemctl status shows "active")
✅ All ports listening (8080, 8081, 8082, 8083)
✅ Alerts received by all 4 bots (check logs)
✅ Memory stable at 600-800 MB
✅ CPU <20% average
✅ No rate limit errors in logs
✅ No crashes or unexpected restarts
✅ Manual test alerts processed correctly
✅ Each bot maintains independent positions
✅ System health monitor shows all green
```

If all above are YES: **Deployment successful! 🎉**

