#!/bin/bash
#
# INDESTRUCTIBLE TRADING BOT WATCHDOG
# Monitors bot health and ensures it's always running
# Run this script via cron every minute: */1 * * * * /root/santhosh/trading/equity/deployment/watchdog.sh
#

set -euo pipefail

# Configuration
BOT_DIR="/root/santhosh/trading/equity"
SERVICE_NAME="equity-trading-bot"
WEBHOOK_URL="http://localhost:80/health"
LOG_FILE="/var/log/trading-bot-watchdog.log"
PID_FILE="/var/run/trading-bot-watchdog.pid"
MAX_RESTART_ATTEMPTS=5
RESTART_COOLDOWN=60
STATE_FILE="/var/lib/trading-bot-state.json"

# Alert configuration (configure your notification preferences)
ALERT_EMAIL="your-email@example.com"  # Change this
ALERT_WEBHOOK=""  # Slack/Discord webhook for alerts
ALERT_SMS_API=""  # SMS API endpoint for critical alerts

# Create necessary directories
mkdir -p /var/log /var/lib /var/run

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Check if watchdog is already running
check_watchdog_lock() {
    if [[ -f "$PID_FILE" ]]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            exit 0  # Another watchdog is running
        else
            rm -f "$PID_FILE"
        fi
    fi
    echo $$ > "$PID_FILE"
}

# Send alert notification
send_alert() {
    local level="$1"
    local message="$2"
    
    log "🚨 ALERT [$level]: $message"
    
    # Email alert (if configured)
    if [[ -n "$ALERT_EMAIL" && "$ALERT_EMAIL" != "your-email@example.com" ]]; then
        echo "$message" | mail -s "Trading Bot Alert [$level]" "$ALERT_EMAIL" 2>/dev/null || true
    fi
    
    # Webhook alert (Slack/Discord)
    if [[ -n "$ALERT_WEBHOOK" ]]; then
        curl -s -X POST "$ALERT_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\":\"🤖 Trading Bot Alert [$level]: $message\"}" 2>/dev/null || true
    fi
    
    # Critical SMS alert
    if [[ "$level" == "CRITICAL" && -n "$ALERT_SMS_API" ]]; then
        curl -s -X POST "$ALERT_SMS_API" \
            -d "message=CRITICAL: Trading Bot Down - $message" 2>/dev/null || true
    fi
}

# Check if service is running
is_service_running() {
    systemctl is-active --quiet "$SERVICE_NAME"
}

# Check if webhook is responding
is_webhook_healthy() {
    local response=$(curl -s -w "%{http_code}" -o /dev/null --connect-timeout 5 --max-time 10 "$WEBHOOK_URL" 2>/dev/null || echo "000")
    [[ "$response" =~ ^(200|404)$ ]]  # 200 = healthy, 404 = server running but no health endpoint
}

# Check if port 80 is listening
is_port_listening() {
    netstat -tuln | grep -q ":80 "
}

# Get restart attempt count
get_restart_count() {
    if [[ -f "$STATE_FILE" ]]; then
        python3 -c "
import json
import sys
try:
    with open('$STATE_FILE', 'r') as f:
        data = json.load(f)
    print(data.get('restart_count', 0))
except:
    print(0)
" 2>/dev/null || echo "0"
    else
        echo "0"
    fi
}

# Update restart count
update_restart_count() {
    local count="$1"
    python3 -c "
import json
import time
data = {
    'restart_count': $count,
    'last_restart': time.time(),
    'watchdog_version': '2.0'
}
with open('$STATE_FILE', 'w') as f:
    json.dump(data, f, indent=2)
"
}

# Reset restart count if cooldown period has passed
reset_restart_count_if_needed() {
    if [[ -f "$STATE_FILE" ]]; then
        local should_reset=$(python3 -c "
import json
import time
try:
    with open('$STATE_FILE', 'r') as f:
        data = json.load(f)
    last_restart = data.get('last_restart', 0)
    if time.time() - last_restart > $RESTART_COOLDOWN:
        print('yes')
    else:
        print('no')
except:
    print('yes')
")
        if [[ "$should_reset" == "yes" ]]; then
            update_restart_count 0
        fi
    fi
}

# Start the trading bot service
start_bot() {
    log "🚀 Starting trading bot service..."
    
    # Kill any orphaned processes on port 80
    lsof -ti:80 | xargs -r kill -9 2>/dev/null || true
    sleep 2
    
    # Start systemd service
    systemctl start "$SERVICE_NAME"
    sleep 5
    
    # Verify it started
    if is_service_running; then
        log "✅ Trading bot service started successfully"
        return 0
    else
        log "❌ Failed to start trading bot service via systemd"
        return 1
    fi
}

# Emergency direct start (if systemd fails)
emergency_start() {
    log "🆘 Attempting emergency direct start..."
    
    cd "$BOT_DIR"
    source .venv/bin/activate
    
    # Kill any existing processes
    pkill -f "start_port80_server.py" 2>/dev/null || true
    pkill -f "python.*api.py" 2>/dev/null || true
    lsof -ti:80 | xargs -r kill -9 2>/dev/null || true
    sleep 3
    
    # Start directly in background
    nohup python start_port80_server.py > /var/log/trading-bot-emergency.log 2>&1 &
    sleep 5
    
    if is_port_listening; then
        log "✅ Emergency start successful"
        send_alert "WARNING" "Bot started via emergency method (systemd may be failing)"
        return 0
    else
        log "❌ Emergency start failed"
        return 1
    fi
}

# Main health check and recovery
main() {
    check_watchdog_lock
    
    log "🔍 Running watchdog health check..."
    
    # Reset restart count if cooldown period passed
    reset_restart_count_if_needed
    
    local restart_count=$(get_restart_count)
    local service_running=false
    local webhook_healthy=false
    local port_listening=false
    
    # Check service status
    if is_service_running; then
        service_running=true
        log "✅ Service is running"
    else
        log "❌ Service is not running"
    fi
    
    # Check port 80
    if is_port_listening; then
        port_listening=true
        log "✅ Port 80 is listening"
    else
        log "❌ Port 80 is not listening"
    fi
    
    # Check webhook health
    if is_webhook_healthy; then
        webhook_healthy=true
        log "✅ Webhook is responding"
    else
        log "❌ Webhook is not responding"
    fi
    
    # Determine if bot is healthy
    if [[ "$service_running" == true && "$webhook_healthy" == true && "$port_listening" == true ]]; then
        log "💚 Bot is healthy - all checks passed"
        update_restart_count 0  # Reset count on successful health check
        rm -f "$PID_FILE"
        exit 0
    fi
    
    # Bot is unhealthy - attempt recovery
    log "🔴 Bot is unhealthy - initiating recovery..."
    
    # Check restart limits
    if [[ $restart_count -ge $MAX_RESTART_ATTEMPTS ]]; then
        send_alert "CRITICAL" "Bot failed $MAX_RESTART_ATTEMPTS restart attempts. Manual intervention required."
        log "🛑 Maximum restart attempts reached. Waiting for manual intervention."
        rm -f "$PID_FILE"
        exit 1
    fi
    
    # Increment restart count
    restart_count=$((restart_count + 1))
    update_restart_count $restart_count
    
    log "🔄 Restart attempt $restart_count of $MAX_RESTART_ATTEMPTS"
    send_alert "WARNING" "Bot unhealthy, attempting restart $restart_count/$MAX_RESTART_ATTEMPTS"
    
    # Stop the service first
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sleep 3
    
    # Try normal start first
    if start_bot; then
        # Wait and verify
        sleep 10
        if is_webhook_healthy && is_port_listening; then
            log "✅ Bot recovery successful"
            send_alert "INFO" "Bot recovered successfully after restart"
            update_restart_count 0
            rm -f "$PID_FILE"
            exit 0
        fi
    fi
    
    # If normal start failed, try emergency start
    log "⚠️ Normal start failed, trying emergency start..."
    if emergency_start; then
        sleep 10
        if is_webhook_healthy && is_port_listening; then
            log "✅ Emergency recovery successful"
            send_alert "WARNING" "Bot recovered via emergency start"
            rm -f "$PID_FILE"
            exit 0
        fi
    fi
    
    # Recovery failed
    log "❌ Recovery failed"
    send_alert "CRITICAL" "Bot recovery failed after restart attempt $restart_count. Service may need manual intervention."
    
    rm -f "$PID_FILE"
    exit 1
}

# Cleanup on exit
trap 'rm -f "$PID_FILE"' EXIT

# Run main function
main "$@"