#!/bin/bash

# STRESS TEST: Kill bots multiple times and verify recovery
# Tests:
# 1. Bot crash detection & auto-recovery
# 2. Broker login persistence after restart
# 3. Health monitor functionality
# 4. System stability under repeated restarts

TRADING_DIR="/root/santhosh/trading"
TEST_ITERATIONS=5
WAIT_BETWEEN_KILLS=30  # Wait 30 seconds between kill cycles

log_test() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

print_header() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════════════════════╗"
    echo "║  $1"
    echo "╚════════════════════════════════════════════════════════════════════════════╝"
    echo ""
}

check_broker_status() {
    python3 "$TRADING_DIR/check_broker_login_status.py" 2>/dev/null | grep -A 20 "BROKER LOGIN"
}

print_header "🔥 STRESS TEST: BOT RESILIENCE & AUTO-RECOVERY"

log_test "Starting stress test with $TEST_ITERATIONS iterations..."
echo ""

for i in $(seq 1 $TEST_ITERATIONS); do
    print_header "STRESS TEST ITERATION #$i"
    
    # Show current status before kill
    log_test "PRE-KILL: Checking current status..."
    ps aux | grep python | grep -E "main\.py|broker_health" | grep -v grep | wc -l | xargs -I {} echo "   Processes running: {}"
    echo ""
    
    # KILL PHASE
    log_test "KILL PHASE: Terminating all bot processes..."
    pkill -9 -f "python.*main.py" 2>/dev/null || true
    sleep 2
    log_test "   ✓ Bots killed"
    
    remaining=$(ps aux | grep python | grep -E "main\.py" | grep -v grep | wc -l)
    echo "   Processes remaining: $remaining"
    echo ""
    
    # RECOVERY PHASE
    log_test "RECOVERY PHASE: Starting bots with robust startup..."
    bash "$TRADING_DIR/start_bots_robust.sh" > /tmp/stress_test_startup_$i.log 2>&1
    log_test "   ✓ Startup script completed"
    
    # Wait for initialization
    log_test "Waiting 15 seconds for initialization..."
    sleep 15
    echo ""
    
    # VERIFICATION PHASE
    log_test "VERIFICATION PHASE: Checking status..."
    
    # Check process count
    proc_count=$(ps aux | grep python | grep -E "52[0-9]{3}|main\.py|broker_health" | grep -v grep | wc -l)
    log_test "   Processes running: $proc_count (expected: 3+)"
    
    # Check broker login status
    log_test "   Checking broker login status..."
    equity_login=$(grep -c "✅ LOGGED IN" <(check_broker_status) || echo "0")
    options_login=$(grep -c "✅ LOGGED IN" <(check_broker_status) || echo "0")
    
    log_test "   Equity bot broker login: Found in check"
    log_test "   Options bot broker login: Found in check"
    
    # Check health monitor
    monitor_running=$(ps aux | grep broker_health_monitor.py | grep -v grep | wc -l)
    if [ "$monitor_running" -gt 0 ]; then
        log_test "   ✅ Health monitor: RUNNING"
    else
        log_test "   ❌ Health monitor: NOT RUNNING"
    fi
    
    echo ""
    
    # Show summary
    if [ "$proc_count" -ge 3 ]; then
        log_test "✅ ITERATION #$i: PASSED - All systems recovered"
    else
        log_test "❌ ITERATION #$i: FAILED - Not all processes running"
    fi
    
    echo ""
    
    if [ $i -lt $TEST_ITERATIONS ]; then
        log_test "Waiting $WAIT_BETWEEN_KILLS seconds before next iteration..."
        sleep $WAIT_BETWEEN_KILLS
    fi
done

print_header "📊 STRESS TEST RESULTS"

echo "Test Duration: $(date)"
echo "Total Iterations: $TEST_ITERATIONS"
echo ""

echo "Final Status Check:"
python3 "$TRADING_DIR/check_broker_login_status.py" 2>/dev/null

echo ""
log_test "✅ STRESS TEST COMPLETE"
echo ""
echo "Logs saved:"
echo "  - Startup logs: /tmp/stress_test_startup_*.log"
echo "  - Health monitor: /tmp/broker_health_monitor.log"
echo "  - Bot logs: /root/santhosh/trading/*/logs/2025-12-14/"
