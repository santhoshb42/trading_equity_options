#!/bin/bash
################################################################################
#                 PUT OPTIONS BOT SYSTEMD SETUP SCRIPT
#
# This script sets up the PUT options bot as a systemd service that:
# - Auto-starts on system boot (port 8082, separate from CE bot)
# - Auto-restarts on crash/failure
# - Can be managed with systemctl
# - Provides logging via journalctl
# - Monitors health and isolation from CE bot
################################################################################

set -e

echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║              PUT OPTIONS BOT - SYSTEMD SETUP                          ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ This script must be run as root"
    echo "   Run: sudo bash setup_systemd.sh"
    exit 1
fi

BOT_DIR="/root/santhosh/trading/put_options"
SERVICE_FILE="/etc/systemd/system/put_optbot.service"
WATCHDOG_SERVICE="/etc/systemd/system/put_optbot-watchdog.service"

echo ""
echo "📋 Configuration:"
echo "   Bot Directory:    $BOT_DIR"
echo "   Service File:     $SERVICE_FILE"
echo "   Watchdog Service: $WATCHDOG_SERVICE"
echo "   Port:             8082 (separate from CE bot on 8081)"
echo ""

# Step 1: Verify files exist
echo "Step 1: Verifying files..."
if [ ! -f "$BOT_DIR/main.py" ]; then
    echo "❌ main.py not found in $BOT_DIR"
    exit 1
fi
echo "✅ main.py found"

if [ ! -f "$BOT_DIR/.env" ]; then
    echo "❌ .env file not found in $BOT_DIR"
    exit 1
fi
echo "✅ .env file found"

if [ ! -f "$BOT_DIR/optbot-watchdog.sh" ]; then
    echo "❌ optbot-watchdog.sh not found"
    exit 1
fi
echo "✅ optbot-watchdog.sh found"

# Step 2: Create systemd service files
echo ""
echo "Step 2: Creating systemd service files..."

if [ -f "$SERVICE_FILE" ]; then
    echo "ℹ️  Backing up existing service file..."
    cp "$SERVICE_FILE" "$SERVICE_FILE.backup.$(date +%s)"
fi

echo "✅ Service files configured"

# Step 3: Reload systemd daemon
echo ""
echo "Step 3: Reloading systemd daemon..."
systemctl daemon-reload
echo "✅ Systemd daemon reloaded"

# Step 4: Enable services
echo ""
echo "Step 4: Enabling services..."
systemctl enable put_optbot.service
echo "✅ put_optbot.service enabled (starts on boot)"

systemctl enable put_optbot-watchdog.service
echo "✅ put_optbot-watchdog.service enabled"

# Step 5: Display service status
echo ""
echo "Step 5: Service Status"
echo "─────────────────────────────────────────────────────"
systemctl status put_optbot.service --no-pager || echo "Service not running yet"
echo ""

# Step 6: Provide usage instructions
echo ""
echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║                         SETUP COMPLETE ✅                             ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "📚 COMMON COMMANDS:"
echo ""
echo "   Start the bot:"
echo "      sudo systemctl start put_optbot.service"
echo ""
echo "   Stop the bot:"
echo "      sudo systemctl stop put_optbot.service"
echo ""
echo "   Restart the bot:"
echo "      sudo systemctl restart put_optbot.service"
echo ""
echo "   Check status:"
echo "      sudo systemctl status put_optbot.service"
echo ""
echo "   View logs (last 50 lines):"
echo "      sudo journalctl -u optbot.service -n 50 -f"
echo ""
echo "   View all logs:"
echo "      sudo journalctl -u optbot.service --no-pager"
echo ""
echo "   Enable auto-start on boot:"
echo "      sudo systemctl enable optbot.service"
echo ""
echo "   Disable auto-start:"
echo "      sudo systemctl disable optbot.service"
echo ""
echo "   Watch watchdog logs:"
echo "      sudo journalctl -u optbot-watchdog.service -f"
echo ""
echo "   Check if auto-restart is enabled:"
echo "      sudo systemctl status optbot.service | grep 'Restart='  "
echo ""
echo "📊 HEALTH CHECK:"
echo ""
echo "   Run comprehensive health check:"
echo "      cd $BOT_DIR"
echo "      python3 health_check.py"
echo ""
echo "   View health report:"
echo "      cat $BOT_DIR/data/health_check_report.json | python3 -m json.tool"
echo ""
echo "🔍 ISOLATION VERIFICATION:"
echo ""
echo "   Verify isolation from CE bot and port 8082:"
echo "      netstat -tuln | grep -E ':(8081|8082)'"
echo ""
echo "   Verify this bot is on port 8082:"
echo "      lsof -i :8082"
echo ""
echo "   View CE bot service (should be separate):"
echo "      sudo systemctl status optbot.service"
echo ""
echo "⚙️  CONFIGURATION:"
echo ""
echo "   Edit PUT bot service file:"
echo "      sudo nano $SERVICE_FILE"
echo ""
echo "   Edit environment variables:"
echo "      nano $BOT_DIR/.env"
echo ""
echo "🔄 AUTO-RESTART BEHAVIOR:"
echo ""
echo "   The bot will automatically restart if:"
echo "   - Process crashes (exit code > 0)"
echo "   - Health check fails (3 consecutive failures)"
echo "   - Port becomes unresponsive"
echo "   - Watchdog detects issues"
echo ""
echo "   Restart delay: 5 seconds"
echo "   Max restarts: Unlimited"
echo ""
echo "✅ Next Steps:"
echo ""
echo "   1. Start the service:"
echo "      sudo systemctl start put_optbot.service"
echo ""
echo "   2. Check if it's running:"
echo "      sudo systemctl status put_optbot.service"
echo ""
echo "   3. View the logs:"
echo "      sudo journalctl -u put_optbot.service -f"
echo ""
echo "   4. Run health check:"
echo "      python3 health_check.py"
echo ""
echo "═══════════════════════════════════════════════════════════════════════════"
echo ""
