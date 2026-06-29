#!/bin/bash
################################################################################
#                 ITM PUT OPTIONS BOT SYSTEMD SETUP SCRIPT
#
# This script sets up the ITM options bot as a systemd service that:
# - Auto-starts on system boot
# - Auto-restarts on crash/failure
# - Can be managed with systemctl
# - Provides logging via journalctl
# - Monitors health and isolation
################################################################################

set -e

echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║               ITM PUT OPTIONS BOT - SYSTEMD SETUP                         ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ This script must be run as root"
    echo "   Run: sudo bash setup_systemd.sh"
    exit 1
fi

BOT_DIR="/root/santhosh/trading/ITM_put_options"
SERVICE_FILE="/etc/systemd/system/itm_put_optbot.service"
WATCHDOG_SERVICE="/etc/systemd/system/itm_put_optbot-watchdog.service"
SERVICE_TEMPLATE="$BOT_DIR/deployment/options-trading-bot.service"
WATCHDOG_TEMPLATE="$BOT_DIR/deployment/optbot-watchdog.service"
LEGACY_SERVICE="itm_put_optbot_legacy.service"

echo ""
echo "📋 Configuration:"
echo "   Bot Directory:    $BOT_DIR"
echo "   Service File:     $SERVICE_FILE"
echo "   Watchdog Service: $WATCHDOG_SERVICE"
echo "   Port:             8083 (separate from OTM PE on 8082)"
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

if [ ! -f "$SERVICE_TEMPLATE" ]; then
    echo "❌ Service template not found: $SERVICE_TEMPLATE"
    exit 1
fi
echo "✅ service template found"

if [ ! -f "$WATCHDOG_TEMPLATE" ]; then
    echo "❌ Watchdog template not found: $WATCHDOG_TEMPLATE"
    exit 1
fi
echo "✅ watchdog template found"

# Step 2: Create systemd service files
echo ""
echo "Step 2: Creating systemd service files..."

if [ -f "$SERVICE_FILE" ]; then
    echo "ℹ️  Backing up existing service file..."
    cp "$SERVICE_FILE" "$SERVICE_FILE.backup.$(date +%s)"
fi

if [ -f "$WATCHDOG_SERVICE" ]; then
    echo "ℹ️  Backing up existing watchdog service file..."
    cp "$WATCHDOG_SERVICE" "$WATCHDOG_SERVICE.backup.$(date +%s)"
fi

install -m 0644 "$SERVICE_TEMPLATE" "$SERVICE_FILE"
install -m 0644 "$WATCHDOG_TEMPLATE" "$WATCHDOG_SERVICE"
chmod 0755 "$BOT_DIR/optbot-watchdog.sh"

echo "✅ Service files configured"

# Step 3: Reload systemd daemon
echo ""
echo "Step 3: Reloading systemd daemon..."
systemctl daemon-reload
echo "✅ Systemd daemon reloaded"

# Step 3b: Retire legacy ITM service if present
echo ""
echo "Step 3b: Retiring legacy ITM service..."
if systemctl list-unit-files | grep -q "^$LEGACY_SERVICE"; then
    echo "ℹ️  Found legacy unit $LEGACY_SERVICE; stopping and disabling it to prevent duplicate launches"
    systemctl stop "$LEGACY_SERVICE" 2>/dev/null || true
    systemctl disable "$LEGACY_SERVICE" 2>/dev/null || true
    systemctl reset-failed "$LEGACY_SERVICE" 2>/dev/null || true
    echo "✅ Legacy unit $LEGACY_SERVICE retired"
else
    echo "✅ No legacy ITM unit found"
fi

# Step 4: Enable services
echo ""
echo "Step 4: Enabling services..."
systemctl enable itm_put_optbot.service
echo "✅ itm_optbot.service enabled (starts on boot)"

systemctl enable itm_put_optbot-watchdog.service
echo "✅ itm_optbot-watchdog.service enabled"

# Step 5: Display service status
echo ""
echo "Step 5: Service Status"
echo "─────────────────────────────────────────────────────"
systemctl status itm_optbot.service --no-pager || echo "Service not running yet"
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
echo "      sudo systemctl start itm_put_optbot.service"
echo ""
echo "   Stop the bot:"
echo "      sudo systemctl stop itm_put_optbot.service"
echo ""
echo "   Restart the bot:"
echo "      sudo systemctl restart itm_put_optbot.service"
echo ""
echo "   Check status:"
echo "      sudo systemctl status itm_put_optbot.service"
echo ""
echo "   View logs (last 50 lines):"
echo "      sudo journalctl -u itm_put_optbot.service -n 50 -f"
echo ""
echo "   View all logs:"
echo "      sudo journalctl -u itm_put_optbot.service --no-pager"
echo ""
echo "   Enable auto-start on boot:"
echo "      sudo systemctl enable itm_put_optbot.service"
echo ""
echo "   Disable auto-start:"
echo "      sudo systemctl disable itm_optbot.service"
echo ""
echo "   Watch watchdog logs:"
echo "      sudo journalctl -u itm_put_optbot-watchdog.service -f"
echo ""
echo "   Check if auto-restart is enabled:"
echo "      sudo systemctl status itm_put_optbot.service | grep 'Restart='  "
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
echo "   Verify isolation from OTM bot and port 8080:"
echo "      netstat -tuln | grep -E ':(8080|8081)'"
echo ""
echo "   View OTM bot service:"
echo "      sudo systemctl status optbot.service"
echo ""
echo "⚙️  CONFIGURATION:"
echo ""
echo "   Edit service file:"
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
echo "      sudo systemctl start itm_put_optbot.service"
echo ""
echo "   2. Check if it's running:"
echo "      sudo systemctl status itm_put_optbot.service"
echo ""
echo "   3. View the logs:"
echo "      sudo journalctl -u itm_put_optbot.service -f"
echo ""
echo "   4. Run health check:"
echo "      python3 health_check.py"
echo ""
echo "═══════════════════════════════════════════════════════════════════════════"
echo ""
