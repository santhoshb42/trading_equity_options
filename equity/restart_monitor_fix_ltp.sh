#!/bin/bash

# Quick restart script for equity monitor to fix stale LTP issue

echo "════════════════════════════════════════════════════════════════"
echo "  Restarting Equity Bot Monitor (Fix Stale LTP)"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Step 1: Kill existing process
echo "Step 1: Stopping existing monitor..."
PID=$(ps aux | grep "equity/main.py" | grep -v grep | awk '{print $2}')

if [ -z "$PID" ]; then
    echo "  ✅ No monitor process found (already stopped)"
else
    echo "  Found PID: $PID"
    kill $PID
    sleep 2
    
    # Verify it's dead
    if ps -p $PID > /dev/null 2>&1; then
        echo "  Process still running, force killing..."
        kill -9 $PID
    fi
    echo "  ✅ Monitor stopped"
fi

echo ""
echo "Step 2: Restarting monitor..."
cd /root/santhosh/trading/equity

nohup python3 main.py > monitor_restart.log 2>&1 &
MONITOR_PID=$!

sleep 2

# Verify it started
if ps -p $MONITOR_PID > /dev/null 2>&1; then
    echo "  ✅ Monitor started with PID: $MONITOR_PID"
else
    echo "  ❌ Monitor failed to start"
    echo "  Error logs:"
    cat monitor_restart.log
    exit 1
fi

echo ""
echo "Step 3: Waiting for LTP to refresh (next bucket check)..."
echo "  This should happen within 30 seconds..."
echo ""

# Watch for BUCKET_LTP_SUCCESS
LOGFILE="/root/santhosh/trading/equity/logs/2025-12-09/detailed.log"
MAX_WAIT=60
WAIT_TIME=0

while [ $WAIT_TIME -lt $MAX_WAIT ]; do
    if grep -q "BUCKET_LTP_SUCCESS\|BUCKET_LTP_BULK_SUCCESS" "$LOGFILE" 2>/dev/null; then
        echo "  ✅ LTP successfully refreshed!"
        echo ""
        echo "  Latest LTP fetch:"
        tail -5 "$LOGFILE" | grep "BUCKET_LTP"
        break
    fi
    
    echo -n "."
    sleep 2
    WAIT_TIME=$((WAIT_TIME + 2))
done

if [ $WAIT_TIME -ge $MAX_WAIT ]; then
    echo ""
    echo "  ⚠️  No LTP refresh detected yet (might still be processing)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  RESTART COMPLETE"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "1. Monitor the logs for TRAIL_ messages:"
echo "   tail -f /root/santhosh/trading/equity/logs/2025-12-09/detailed.log | grep TRAIL"
echo ""
echo "2. Run the debug script to verify LTP is fresh:"
echo "   cd /root/santhosh/trading && python3 equity/DEBUG_TRAILING_SL.py"
echo ""
echo "3. Look for these logs in order:"
echo "   • TRAIL_DEBUG           ← Position being checked"
echo "   • TRAIL_ACTIVATED       ← Trailing started"
echo "   • TRAIL_SL_CALCULATION  ← New SL being calculated"
echo "   • TRAIL_SL_STEPPED      ← Moving to next step"
echo "   • TRAIL_SL_MODIFIED     ← ✅ SUCCESS!"
echo ""
