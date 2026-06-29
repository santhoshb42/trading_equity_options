#!/bin/bash
################################################################################
#                       OPTIONS BOT WATCHDOG SCRIPT
#
# This script monitors the options bot and ensures it stays alive.
# It checks:
# 1. Process is running
# 2. Health endpoint responds
# 3. Port 8083 is listening
# 4. Isolation from equity bot (port 80 separate)
# 5. Logs are being written
#
# If any check fails, it restarts the bot automatically.
################################################################################

set -e

# Configuration
BOT_NAME="itm_put_optbot"
BOT_DIR="/root/santhosh/trading/ITM_put_options"
BOT_SCRIPT="$BOT_DIR/main.py"
BOT_PORT="8083"
BOT_PID_FILE="$BOT_DIR/ITM_put_options_bot.pid"
BOT_SERVICE="itm_put_optbot.service"
HEALTH_CHECK_URL="http://127.0.0.1:$BOT_PORT/health"
EQUITY_PORT="80"
LOG_DIR="$BOT_DIR/logs"
WATCHDOG_LOG="$BOT_DIR/optbot-watchdog.log"
CHECK_INTERVAL=30
CONSECUTIVE_FAILURES=0
MAX_CONSECUTIVE_FAILURES=3

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    local level=$1
    shift
    local message="$@"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [$level] $message" | tee -a "$WATCHDOG_LOG"
}

# Get bot PID
get_bot_pid() {
    pgrep -f "$BOT_SCRIPT" | head -1 || echo ""
}

# Check if bot process is running
check_process() {
    local pid=$(get_bot_pid)
    if [ -z "$pid" ]; then
        log "ERROR" "Bot process not running"
        return 1
    fi
    
    if ! kill -0 "$pid" 2>/dev/null; then
        log "ERROR" "Bot process (PID $pid) is not responsive"
        return 1
    fi
    
    log "INFO" "Bot process running (PID: $pid)"
    return 0
}

# Check health endpoint
check_health() {
    if ! command -v curl &> /dev/null; then
        log "WARNING" "curl not found, skipping health check"
        return 0
    fi
    
    local response=$(curl -s -m 5 -w "%{http_code}" -o /dev/null "$HEALTH_CHECK_URL" 2>/dev/null || echo "000")
    
    if [ "$response" = "200" ]; then
        log "INFO" "Health check passed (HTTP $response)"
        return 0
    else
        log "ERROR" "Health check failed (HTTP $response)"
        return 1
    fi
}

# Check if a TCP port is listening using the best available tool.
is_port_listening() {
    if command -v ss &> /dev/null; then
        ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q LISTEN
        return $?
    fi

    if command -v lsof &> /dev/null; then
        lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | tail -n +2 | grep -q .
        return $?
    fi

    if command -v netstat &> /dev/null; then
        netstat -tuln 2>/dev/null | grep -q ":$1 "
        return $?
    fi

    log "WARNING" "No socket inspection command available; skipping port check"
    return 0
}

# Check if bot port is listening
check_port_listening() {
    if is_port_listening "$BOT_PORT"; then
        log "INFO" "Port $BOT_PORT is listening"
        return 0
    else
        log "ERROR" "Port $BOT_PORT is not listening"
        return 1
    fi
}

# Verify isolation from equity bot
check_isolation() {
    local equity_listening=0
    local options_listening=0
    
    # Check equity bot port
    if is_port_listening "$EQUITY_PORT"; then
        equity_listening=1
    fi
    
    # Check options bot port
    if is_port_listening "$BOT_PORT"; then
        options_listening=1
    fi
    
    # Both ports should be listening (both bots can run together)
    # or at least options bot should be listening
    if [ $options_listening -eq 1 ]; then
        if [ $equity_listening -eq 1 ]; then
            log "INFO" "✅ Both bots isolated: Equity on :$EQUITY_PORT, Options on :$BOT_PORT"
        else
            log "INFO" "Options bot on :$BOT_PORT (Equity bot not running)"
        fi
        return 0
    else
        log "ERROR" "Options bot port $BOT_PORT not listening"
        return 1
    fi
}

# Check if logs are being written
check_logs() {
    if [ ! -d "$LOG_DIR" ]; then
        log "WARNING" "Log directory doesn't exist yet: $LOG_DIR"
        return 0
    fi
    
    local current_date=$(date '+%Y-%m-%d')
    local log_file="$LOG_DIR/$current_date/optbot.log"
    
    if [ ! -f "$log_file" ]; then
        log "WARNING" "Log file not found: $log_file"
        return 0
    fi
    
    # Check if log was updated in last 5 minutes
    local mod_time=$(stat -f%m "$log_file" 2>/dev/null || stat -c%Y "$log_file" 2>/dev/null || echo 0)
    local current_time=$(date +%s)
    local time_diff=$((current_time - mod_time))
    
    if [ $time_diff -lt 300 ]; then
        log "INFO" "Logs being written (updated $time_diff seconds ago)"
        return 0
    else
        log "WARNING" "Logs not updated recently (last update $time_diff seconds ago)"
        return 0  # Don't fail on this, just warn
    fi
}

# Restart the bot
restart_bot() {
    log "CRITICAL" "Restarting options bot..."
    
    if command -v systemctl &> /dev/null; then
        log "INFO" "Restarting via systemd unit: $BOT_SERVICE"
        systemctl restart "$BOT_SERVICE"
        sleep 5

        if systemctl is-active --quiet "$BOT_SERVICE" && check_process; then
            local pid=$(get_bot_pid)
            if [ -n "$pid" ]; then
                echo "$pid" > "$BOT_PID_FILE"
            fi
            log "INFO" "Bot restart verification successful"
            return 0
        fi

        log "ERROR" "Bot restart verification failed after systemd restart"
        return 1
    fi

    log "WARNING" "systemctl unavailable; falling back to direct restart"

    local pid=$(get_bot_pid)
    if [ -n "$pid" ]; then
        log "INFO" "Killing existing process (PID: $pid)"
        kill -9 "$pid" 2>/dev/null || true
        sleep 2
    fi

    cd "$BOT_DIR"
    nohup /usr/bin/python3 main.py > /dev/null 2>&1 &
    local new_pid=$!
    log "INFO" "Bot restarted with PID: $new_pid"
    echo "$new_pid" > "$BOT_PID_FILE"
    sleep 5

    if check_process; then
        log "INFO" "Bot restart verification successful"
        return 0
    fi

    log "ERROR" "Bot restart verification failed"
    return 1
}

# Main monitoring loop
main() {
    log "INFO" "=========================================="
    log "INFO" "Options Bot Watchdog Started"
    log "INFO" "Bot Directory: $BOT_DIR"
    log "INFO" "Health Check: $HEALTH_CHECK_URL"
    log "INFO" "Check Interval: ${CHECK_INTERVAL}s"
    log "INFO" "=========================================="
    
    while true; do
        log "INFO" "--- Performing health checks ---"
        
        local all_checks_passed=true
        
        # Run all checks
        if ! check_process; then
            all_checks_passed=false
        fi
        
        if ! check_port_listening; then
            all_checks_passed=false
        fi
        
        if ! check_health; then
            all_checks_passed=false
        fi
        
        if ! check_isolation; then
            all_checks_passed=false
        fi
        
        if ! check_logs; then
            all_checks_passed=false
        fi
        
        # Handle failures
        if [ "$all_checks_passed" = true ]; then
            CONSECUTIVE_FAILURES=0
            log "INFO" "✅ All checks passed"
        else
            CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))
            log "WARNING" "Check failed (consecutive failures: $CONSECUTIVE_FAILURES/$MAX_CONSECUTIVE_FAILURES)"
            
            if [ $CONSECUTIVE_FAILURES -ge $MAX_CONSECUTIVE_FAILURES ]; then
                restart_bot
                CONSECUTIVE_FAILURES=0
            fi
        fi
        
        # Wait before next check
        sleep "$CHECK_INTERVAL"
    done
}

# Handle signals
trap 'log "INFO" "Watchdog shutting down..."; exit 0' SIGTERM SIGINT

# Run main loop
main
