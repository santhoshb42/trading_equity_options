#!/bin/bash
# Restart options bot to apply config changes

echo "🔄 Restarting Options Trading Bot..."
echo "   Stopping current bot process..."

# Kill existing bot process
pkill -f "python3.*options/main.py"

# Wait a moment for clean shutdown
sleep 2

echo "   ✅ Bot stopped"
echo ""
echo "   Starting bot with updated config..."
cd /root/santhosh/trading

# Start bot in background
nohup python3 options/main.py > /tmp/optbot_restart.log 2>&1 &

# Wait for startup
sleep 3

# Check if started successfully
if ps aux | grep -q "python3.*options/main.py" | grep -v grep; then
    echo "   ✅ Bot restarted successfully"
    echo ""
    echo "📊 Config changes applied:"
    echo "   • ENABLE_SENTIMENT_FILTER = False"
    echo "   • ENTRY_PCR_MIN = 0.15"
    echo "   • ENTRY_PCR_MAX = 1.5"
    echo ""
    echo "⏳ Bot should start processing new alerts immediately"
else
    echo "   ❌ Bot failed to start"
    echo "   Check /tmp/optbot_restart.log for errors"
fi
