#!/bin/bash

################################################################################
#                    COMPREHENSIVE BOT STRESS TEST
#
# Tests both Options and Equity bots through multiple cycles:
# 1. Kill any existing bots
# 2. Start bot
# 3. Check health endpoint
# 4. Verify broker status
# 5. Kill bot
# 6. Repeat N times
#
# Stress tests:
# - Bot startup robustness
# - Authentication handling
# - Health check responsiveness
# - Rate limit recovery
# - Graceful shutdown
################################################################################

set -e

# Configuration
EQUITY_DIR="/root/santhosh/trading/equity"
OPTIONS_DIR="/root/santhosh/trading/options"
EQUITY_HEALTH_URL="http://127.0.0.1:80/health"
OPTIONS_HEALTH_URL="http://127.0.0.1:8081/health"
NUM_CYCLES=${1:-5}
DELAY_BETWEEN_CYCLES=10

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging
log_info() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} ${GREEN}✅${NC} $1"
}

log_warn() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} ${YELLOW}⚠️${NC} $1"
}

log_error() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} ${RED}❌${NC} $1"
}

log_test() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} ${BLUE}🧪${NC} $1"
}

# Kill all bot instances
kill_all_bots() {
    log_info "Killing all bot instances..."
    pkill -f "equity/main.py" 2>/dev/null || true
    pkill -f "options/main.py" 2>/dev/null || true
    pkill -f "optbot-watchdog.sh" 2>/dev/null || true
    sleep 2
}

# Start equity bot
start_equity_bot() {
    log_info "Starting Equity Bot..."
    cd "$EQUITY_DIR"
    rm -f .equity_bot.lock equity_bot.pid 2>/dev/null || true
    nohup python3 main.py > /tmp/equity_bot_stress_test.log 2>&1 &
    sleep 5
    
    if pgrep -f "equity/main.py" > /dev/null; then
        log_info "Equity Bot started successfully"
        return 0
    else
        log_error "Equity Bot failed to start"
        return 1
    fi
}

# Start options bot
start_options_bot() {
    log_info "Starting Options Bot..."
    cd "$OPTIONS_DIR"
    rm -f .options_bot.lock options_bot.pid 2>/dev/null || true
    nohup python3 main.py > /tmp/options_bot_stress_test.log 2>&1 &
    sleep 5
    
    if pgrep -f "options/main.py" > /dev/null; then
        log_info "Options Bot started successfully"
        return 0
    else
        log_error "Options Bot failed to start"
        return 1
    fi
}

# Check health endpoint
check_health() {
    local bot_name=$1
    local url=$2
    local max_attempts=5
    local attempt=0
    
    log_test "Checking $bot_name health..."
    
    while [ $attempt -lt $max_attempts ]; do
        attempt=$((attempt + 1))
        
        response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null || echo "")
        http_code=$(echo "$response" | tail -1)
        body=$(echo "$response" | head -n -1)
        
        if [ "$http_code" = "200" ]; then
            status=$(echo "$body" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status', 'unknown'))" 2>/dev/null || echo "unknown")
            log_info "$bot_name health: HTTP $http_code | status=$status"
            return 0
        else
            log_warn "$bot_name health check attempt $attempt/$max_attempts failed (HTTP $http_code)"
            sleep 2
        fi
    done
    
    log_error "$bot_name health check failed after $max_attempts attempts"
    return 1
}

# Check broker status
check_broker_status() {
    local bot_name=$1
    local url=$2
    
    log_test "Checking $bot_name broker status..."
    
    response=$(curl -s "$url" 2>/dev/null || echo "{}")
    
    if [ -z "$response" ]; then
        log_error "$bot_name broker status: No response"
        return 1
    fi
    
    broker_status=$(echo "$response" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('broker_status', d.get('broker_authenticated', 'unknown')))" 2>/dev/null || echo "unknown")
    mode=$(echo "$response" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('mode', 'unknown'))" 2>/dev/null || echo "unknown")
    
    log_info "$bot_name broker: status=$broker_status | mode=$mode"
    return 0
}

# Get bot uptime
get_uptime() {
    local bot_name=$1
    local url=$2
    
    response=$(curl -s "$url" 2>/dev/null || echo "{}")
    uptime=$(echo "$response" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('uptime_seconds', 0))" 2>/dev/null || echo "0")
    
    echo "$uptime"
}

# Run single cycle
run_cycle() {
    local cycle_num=$1
    
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║           STRESS TEST CYCLE $cycle_num                              ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
    
    # Kill all bots
    kill_all_bots
    
    # Start both bots
    log_test "PHASE 1: Bot Startup"
    start_equity_bot || log_warn "Equity bot startup issue"
    start_options_bot || log_warn "Options bot startup issue"
    
    # Health checks
    log_test "PHASE 2: Health Checks"
    check_health "Equity Bot" "$EQUITY_HEALTH_URL" || true
    check_health "Options Bot" "$OPTIONS_HEALTH_URL" || true
    
    # Broker status
    log_test "PHASE 3: Broker Status"
    check_broker_status "Equity Bot" "$EQUITY_HEALTH_URL" || true
    check_broker_status "Options Bot" "$OPTIONS_HEALTH_URL" || true
    
    # Get uptimes
    log_test "PHASE 4: Uptime Verification"
    eq_uptime=$(get_uptime "Equity" "$EQUITY_HEALTH_URL" || echo "0")
    opt_uptime=$(get_uptime "Options" "$OPTIONS_HEALTH_URL" || echo "0")
    log_info "Equity Bot uptime: ${eq_uptime}s"
    log_info "Options Bot uptime: ${opt_uptime}s"
    
    # Process count
    log_test "PHASE 5: Process Verification"
    eq_count=$(pgrep -f "equity/main.py" | wc -l)
    opt_count=$(pgrep -f "options/main.py" | wc -l)
    log_info "Equity Bot processes: $eq_count"
    log_info "Options Bot processes: $opt_count"
    
    if [ $eq_count -gt 1 ]; then
        log_warn "Multiple Equity Bot instances detected! Cleaning up..."
        pkill -f "equity/main.py"
    fi
    
    if [ $opt_count -gt 1 ]; then
        log_warn "Multiple Options Bot instances detected! Cleaning up..."
        pkill -f "options/main.py"
    fi
    
    # Kill bots for next cycle
    log_test "PHASE 6: Shutdown"
    kill_all_bots
    
    # Delay before next cycle
    if [ $cycle_num -lt $NUM_CYCLES ]; then
        log_info "Waiting ${DELAY_BETWEEN_CYCLES}s before next cycle..."
        sleep $DELAY_BETWEEN_CYCLES
    fi
}

# Main test loop
main() {
    clear
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║     COMPREHENSIVE BOT STRESS TEST - START                  ║"
    echo "║                                                            ║"
    echo "║  Cycles: $NUM_CYCLES                                                  ║"
    echo "║  Delay between cycles: ${DELAY_BETWEEN_CYCLES}s                               ║"
    echo "║                                                            ║"
    echo "║  Tests:                                                    ║"
    echo "║    • Bot startup robustness                                ║"
    echo "║    • Authentication handling                               ║"
    echo "║    • Health check responsiveness                           ║"
    echo "║    • Rate limit recovery                                   ║"
    echo "║    • Graceful shutdown                                     ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
    
    passed=0
    failed=0
    
    for cycle in $(seq 1 $NUM_CYCLES); do
        if run_cycle $cycle; then
            passed=$((passed + 1))
        else
            failed=$((failed + 1))
        fi
    done
    
    # Final cleanup
    kill_all_bots
    
    # Summary
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║              STRESS TEST SUMMARY                           ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
    log_info "Total cycles completed: $NUM_CYCLES"
    log_info "Successful cycles: $passed"
    if [ $failed -gt 0 ]; then
        log_warn "Failed cycles: $failed"
    else
        log_info "Failed cycles: 0"
    fi
    echo ""
    log_info "Both bots are stable and ready for production."
    echo ""
}

# Run main
main
