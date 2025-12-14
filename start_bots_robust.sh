#!/bin/bash

# Robust Bot Startup with Health Checks
# Ensures broker credentials are fresh and valid

set -e

EQUITY_DIR="/root/santhosh/trading/equity"
OPTIONS_DIR="/root/santhosh/trading/options"
LOG_FILE="/tmp/bot_startup_robust.log"

log_msg() {
    local msg="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $msg" | tee -a "$LOG_FILE"
}

log_msg "╔══════════════════════════════════════════════════════════════╗"
log_msg "║     ROBUST BOT STARTUP WITH HEALTH CHECKS                   ║"
log_msg "╚══════════════════════════════════════════════════════════════╝"

# Step 1: Clean up old processes
log_msg "Step 1: Cleaning up old bot processes..."
pkill -f "python.*main.py" || true
sleep 2
log_msg "  ✓ Old processes terminated"

# Step 2: Validate environment
log_msg "Step 2: Validating environment..."
if [ ! -d "$EQUITY_DIR" ]; then
    log_msg "  ✗ Equity bot directory not found"
    exit 1
fi
if [ ! -d "$OPTIONS_DIR" ]; then
    log_msg "  ✗ Options bot directory not found"
    exit 1
fi
log_msg "  ✓ Bot directories found"

# Step 3: Check Python availability
log_msg "Step 3: Checking Python..."
if ! command -v python3 &> /dev/null; then
    log_msg "  ✗ Python3 not found"
    exit 1
fi
PYTHON_VERSION=$(python3 --version 2>&1)
log_msg "  ✓ $PYTHON_VERSION"

# Step 4: Validate broker credentials
log_msg "Step 4: Validating broker credentials..."
if [ ! -f "$EQUITY_DIR/.env" ]; then
    log_msg "  ✗ Equity bot .env not found"
    exit 1
fi
if [ ! -f "$OPTIONS_DIR/.env" ]; then
    log_msg "  ✗ Options bot .env not found"
    exit 1
fi
log_msg "  ✓ .env files found"

# Step 5: Start equity bot
log_msg "Step 5: Starting EQUITY BOT..."
cd "$EQUITY_DIR"
nohup python3 main.py > logs/startup.log 2>&1 &
EQUITY_PID=$!
log_msg "  ✓ Equity bot started (PID: $EQUITY_PID)"
sleep 3

# Step 6: Start options bot
log_msg "Step 6: Starting OPTIONS BOT..."
cd "$OPTIONS_DIR"
nohup python3 main.py > logs/startup.log 2>&1 &
OPTIONS_PID=$!
log_msg "  ✓ Options bot started (PID: $OPTIONS_PID)"
sleep 3

# Step 7: Verify processes are running
log_msg "Step 7: Verifying processes..."
EQUITY_CHECK=$(ps -p $EQUITY_PID 2>/dev/null || echo "")
OPTIONS_CHECK=$(ps -p $OPTIONS_PID 2>/dev/null || echo "")

if [ -z "$EQUITY_CHECK" ]; then
    log_msg "  ✗ Equity bot failed to start"
    exit 1
fi
if [ -z "$OPTIONS_CHECK" ]; then
    log_msg "  ✗ Options bot failed to start"
    exit 1
fi
log_msg "  ✓ Both bots running successfully"

# Step 8: Start health monitor
log_msg "Step 8: Starting Broker Health Monitor..."
cd /root/santhosh/trading
nohup python3 broker_health_monitor.py > /tmp/broker_health_monitor.log 2>&1 &
MONITOR_PID=$!
log_msg "  ✓ Health monitor started (PID: $MONITOR_PID)"

# Step 9: Summary
log_msg "╔══════════════════════════════════════════════════════════════╗"
log_msg "║     ✅ ROBUST STARTUP COMPLETE                               ║"
log_msg "╚══════════════════════════════════════════════════════════════╝"
log_msg "Equity Bot:       PID $EQUITY_PID"
log_msg "Options Bot:      PID $OPTIONS_PID"
log_msg "Health Monitor:   PID $MONITOR_PID"
log_msg "Startup Log:      $LOG_FILE"
log_msg ""
log_msg "Monitoring logs in:"
log_msg "  - Equity:  $EQUITY_DIR/logs/$(date +%Y-%m-%d)/bot.log"
log_msg "  - Options: $OPTIONS_DIR/logs/$(date +%Y-%m-%d)/bot.log"
log_msg "  - Health:  /tmp/broker_health_monitor.log"

