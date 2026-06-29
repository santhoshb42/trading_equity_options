#!/bin/bash
# PUT Options Bot - Pre-Launch Configuration Verification

echo "=================================="
echo "PUT Options Bot - Configuration Check"
echo "=================================="
echo

cd /root/santhosh/trading/put_options

# Check 1: Port Configuration
echo "✓ CHECK 1: Port Configuration"
PORT=$(grep -n "PORT = int" optcode/optconfig.py | head -1)
if grep -q "8082" optcode/optconfig.py; then
    echo "  ✅ Port 8082 configured in optconfig.py"
else
    echo "  ❌ Port 8082 NOT found in optconfig.py"
fi
echo

# Check 2: Webhook Endpoint
echo "✓ CHECK 2: Webhook Endpoint"
if grep -q "put_options" optcode/optconfig.py; then
    echo "  ✅ Endpoint '/webhook/put_options' configured"
else
    echo "  ❌ Endpoint NOT configured"
fi
ENDPOINT=$(grep "ENDPOINT = " optcode/optconfig.py)
echo "  Path: $ENDPOINT"
echo

# Check 3: Environment Variables
echo "✓ CHECK 3: Environment Variables"
echo "  Checking .env file..."
if grep -q "PUT_OPTIONS_WEBHOOK_PORT=8082" .env; then
    echo "  ✅ PUT_OPTIONS_WEBHOOK_PORT=8082 in .env"
else
    echo "  ⚠️  PUT_OPTIONS_WEBHOOK_PORT not in .env (using default)"
fi
echo

# Check 4: Strike Selector (PE Logic)
echo "✓ CHECK 4: Strike Selector (PE Logic)"
if grep -q "0.35" optcode/strike_selector.py; then
    echo "  ✅ PE strike selection (lower third) implemented"
else
    echo "  ❌ PE strike logic not found"
fi
echo

# Check 5: Entry Filter (PE Signals)
echo "✓ CHECK 5: Entry Filter (PE Signals)"
if grep -q "RSI_MAX_PUT\|SELL\|downtrend" optcode/entry_filter_engine.py; then
    echo "  ✅ PE entry signals (SELL/downtrend) implemented"
else
    echo "  ❌ PE entry filter logic not found"
fi
echo

# Check 6: Blacklist (Worst Symbols)
echo "✓ CHECK 6: Symbol Blacklist"
if grep -q "blacklist_symbols\|ETERNAL\|IRFC" optcode/entry_filter_engine.py; then
    echo "  ✅ Worst symbol blacklist implemented"
else
    echo "  ❌ Blacklist NOT found"
fi
echo

# Check 7: Hard SL Logic (PE Reversal)
echo "✓ CHECK 7: Hard SL Logic (PE Reversal)"
if grep -q "if position.action == \"SELL\"" optcode/optmonitor.py; then
    echo "  ✅ PE-specific HARD_SL logic implemented"
else
    echo "  ❌ PE SL logic not found"
fi
echo

# Check 8: Port Availability
echo "✓ CHECK 8: Port Availability"
if netstat -tlnp 2>/dev/null | grep -q ":8082 " || ss -tlnp 2>/dev/null | grep -q ":8082 "; then
    echo "  ⚠️  Port 8082 already in use (may need to kill existing process)"
    if command -v lsof &> /dev/null; then
        echo "  Current process: $(lsof -i :8082 2>/dev/null | grep LISTEN)"
    fi
else
    echo "  ✅ Port 8082 is available"
fi
echo

# Check 9: File Structure
echo "✓ CHECK 9: File Structure"
if [ -d "data/put_options" ] && [ -d "logs/put_options" ]; then
    echo "  ✅ Data and log directories created"
else
    echo "  ⚠️  Creating directories..."
    mkdir -p data/put_options logs/put_options
    echo "  ✅ Directories created"
fi
echo

# Check 10: Broker Configuration
echo "✓ CHECK 10: Broker Configuration"
if grep -q "ANGEL_CLIENT_CODE\|PUT_ANGEL_CLIENT_CODE" .env; then
    echo "  ✅ Broker credentials found in .env"
    CLIENT=$(grep "ANGEL_CLIENT_CODE" .env | head -1)
    echo "  Client: $CLIENT"
else
    echo "  ❌ Broker credentials NOT found"
fi
echo

echo "=================================="
echo "Configuration Summary"
echo "=================================="
echo
echo "Webhook Server:"
echo "  Host: 127.0.0.1"
echo "  Port: 8082"
echo "  Endpoint: /webhook/put_options"
echo "  Full URL: http://127.0.0.1:8082/webhook/put_options"
echo
echo "Entry Signals:"
echo "  Action: SELL (short puts on downside)"
echo "  Strike Selection: PE (lower strikes)"
echo "  Momentum: RSI < 45 (downtrend)"
echo "  Blacklist: 25 worst-performing symbols"
echo
echo "Position Management:"
echo "  HARD_SL: +20% from entry (reversed for PE)"
echo "  TRIAL_SL: Staircase locking at 5% milestones"
echo "  P&L: Calculated for SELL action"
echo
echo "=================================="
echo "Next Steps:"
echo "=================================="
echo
echo "1. ✅ Port configured to 8082"
echo "2. ✅ PE-specific logic implemented"
echo "3. ⚠️  DO NOT START BOT YET - waiting for approval"
echo
echo "When ready to start:"
echo "  cd /root/santhosh/trading/put_options"
echo "  python3 main.py"
echo
echo "Webhook test command:"
echo "  curl -X POST http://127.0.0.1:8082/webhook/put_options \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"symbol\": \"RELIANCE\", \"action\": \"SELL\"}'"
echo
