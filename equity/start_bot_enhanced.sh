#!/bin/bash

# Enhanced Bot Startup Script
# Ensures EOD scheduler runs correctly, even with late starts

set -e

BOT_DIR="/root/santhosh/trading/equity"
PYTHON3="/usr/bin/python3"
LOG_FILE="$BOT_DIR/logs/bot_startup.log"

echo "=========================================="
echo "🚀 Enhanced Trading Bot Startup"
echo "=========================================="
echo ""

# Log startup
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting enhanced bot startup..." >> "$LOG_FILE"

# Check Python
if ! command -v $PYTHON3 &> /dev/null; then
    echo "❌ Python3 not found"
    exit 1
fi

echo "✅ Python3 found: $($PYTHON3 --version)"

# Change to bot directory
cd "$BOT_DIR"

# Check if bot is already running
if pgrep -f "python.*main.py" > /dev/null; then
    echo "⚠️  Bot is already running"
    echo "Stop it first: pkill -f 'python.*main.py'"
    exit 1
fi

# Display current time
CURRENT_TIME=$(date '+%H:%M:%S')
echo "📅 Current time: $CURRENT_TIME"

# Get trigger time
TRIGGER_TIME="15:30"
echo "⏰ EOD trigger time: $TRIGGER_TIME"

# Check if we're after market close (16:00)
HOUR=$(date '+%H')
if [ "$HOUR" -ge "16" ]; then
    echo "⚠️  Warning: Current time ($CURRENT_TIME) is after market close (16:00)"
    echo "    Bot may not catch any signals today"
    echo "    EOD learning will trigger if you start now"
fi

# Check if we're before trigger time
CURRENT_MINUTES=$(($(date '+%H') * 60 + $(date '+%M')))
TRIGGER_MINUTES=$((15 * 60 + 30))

if [ "$CURRENT_MINUTES" -lt "$TRIGGER_MINUTES" ]; then
    echo "✅ Good: Current time is BEFORE trigger time (15:30)"
    echo "   EOD learning will trigger automatically at 15:30"
else
    echo "⚠️  Warning: Current time is AFTER trigger time (15:30)"
    echo "   Bot will attempt to run missed EOD update on startup"
fi

echo ""
echo "📝 Starting bot with:"
echo "   Directory: $BOT_DIR"
echo "   Python: $PYTHON3"
echo "   Log: logs/2025-11-$(date '+%d')/bot.log"
echo ""

# Start the bot
echo "Starting bot..."
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Executing: python3 main.py" >> "$LOG_FILE"

# Run bot in background but capture output
$PYTHON3 main.py 2>&1 | tee -a "$LOG_FILE" &

# Get bot PID
BOT_PID=$!
echo "✅ Bot started with PID: $BOT_PID"

# Wait a moment for bot to initialize
sleep 3

# Check if bot is still running
if kill -0 $BOT_PID 2>/dev/null; then
    echo "✅ Bot is running"
    echo ""
    echo "=========================================="
    echo "🎯 Status: READY FOR TRADING"
    echo "=========================================="
    echo ""
    
    # Show what to expect
    if [ "$CURRENT_MINUTES" -lt "$TRIGGER_MINUTES" ]; then
        echo "Expected behavior:"
        echo "  • Trading: 09:15 - 16:00 IST"
        echo "  • EOD Learning: Automatically at 15:30"
        echo "  • Status: Will update ML models at 15:30 ✅"
    else
        echo "Expected behavior:"
        echo "  • Late start detected"
        echo "  • EOD Learning: Will run immediately"
        echo "  • Status: Will update ML models now ✅"
    fi
    
    echo ""
    echo "Monitor logs:"
    echo "  tail -f logs/2025-11-$(date '+%d')/bot.log"
    echo ""
    
    # Wait for bot to keep running
    wait $BOT_PID
else
    echo "❌ Bot failed to start"
    tail -20 "$LOG_FILE"
    exit 1
fi
