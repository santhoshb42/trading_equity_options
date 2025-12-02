#!/bin/bash

# Cron Setup Script
# Automatically starts bot at 9 AM and triggers EOD learning at 3:30 PM
# This ensures EOD learning never misses due to late bot starts

BOT_DIR="/root/santhosh/trading/equity"
PYTHON="/usr/bin/python3"

echo "=========================================="
echo "📅 Setting up Automatic Trading Schedule"
echo "=========================================="
echo ""

# Backup current crontab
CRONTAB_BACKUP="/tmp/crontab_backup_$(date +%s).txt"
crontab -l > "$CRONTAB_BACKUP" 2>/dev/null || true

echo "Current crontab backed up to: $CRONTAB_BACKUP"
echo ""

# Create a temporary crontab file
TEMP_CRON="/tmp/new_crontab.txt"

# Copy existing crontab
crontab -l > "$TEMP_CRON" 2>/dev/null || true

# Remove old bot-related entries
sed -i '/trading.*bot/d' "$TEMP_CRON" 2>/dev/null || true
sed -i '/eod.*scheduler/d' "$TEMP_CRON" 2>/dev/null || true
sed -i '/start_trading_bot/d' "$TEMP_CRON" 2>/dev/null || true

echo "Adding new cron jobs..."
echo ""

# Add webhook router startup at 8:55 AM (before bots)
echo "55 8 * * 1-5 sudo systemctl start webhook-router >> /tmp/router_startup.log 2>&1 # Start webhook router" >> "$TEMP_CRON"
echo "✅ Added: Start webhook router at 08:55 AM (Mon-Fri)"

# Add bot startup at 9 AM (Monday-Friday)
echo "0 9 * * 1-5 cd $BOT_DIR && bash start_bot_enhanced.sh >> /tmp/bot_startup.log 2>&1 # Auto-start bot" >> "$TEMP_CRON"
echo "✅ Added: Auto-start bot at 09:00 AM (Mon-Fri)"

# Add EOD learning trigger at 3:15 PM (15 min before market close)
echo "15 15 * * 1-5 $PYTHON $BOT_DIR/eqcode/eod_scheduler.py --trigger-now >> /tmp/eod_trigger.log 2>&1 # EOD learning" >> "$TEMP_CRON"
echo "✅ Added: EOD learning trigger at 15:15 (Mon-Fri)"

# Add bot health check every 30 minutes during trading hours
echo "*/30 9-16 * * 1-5 pgrep -f 'python.*main.py' > /dev/null || cd $BOT_DIR && bash start_bot_enhanced.sh >> /tmp/bot_health.log 2>&1 # Health check" >> "$TEMP_CRON"
echo "✅ Added: Bot health check every 30 min (09:00-16:00)"

# Add log cleanup (keep only 7 days)
echo "0 0 * * 0 find $BOT_DIR/logs -type d -mtime +7 -exec rm -rf {} \; 2>/dev/null # Cleanup old logs" >> "$TEMP_CRON"
echo "✅ Added: Log cleanup (daily, keep 7 days)"

echo ""
echo "=========================================="
echo "Preview of new cron jobs:"
echo "=========================================="
grep -E "router|start_bot_enhanced|eod_scheduler|Health check|Cleanup old" "$TEMP_CRON" || true

echo ""
echo "=========================================="
echo "Installing crontab..."
echo "=========================================="

# Install new crontab
crontab "$TEMP_CRON"

# Verify installation
echo ""
echo "Installed cron jobs:"
crontab -l | grep -E "router|start_bot_enhanced|eod_scheduler|Health check|Cleanup" || echo "No matching jobs found"

echo ""
echo "=========================================="
echo "✅ Cron setup complete!"
echo "=========================================="
echo ""
echo "Schedule:"
echo "  🚀 08:55 AM  - Webhook router starts"
echo "  🕘 09:00 AM  - Bot auto-starts"
echo "  🎯 15:15 PM  - EOD learning triggers"
echo "  🔍 Every 30m - Bot health check"
echo "  🗑️  00:00 AM - Cleanup old logs"
echo ""
echo "Backup of old crontab: $CRONTAB_BACKUP"
echo "Restore with: crontab $CRONTAB_BACKUP"
echo ""
echo "Monitor cron logs:"
echo "  tail -f /tmp/router_startup.log"
echo "  tail -f /tmp/bot_startup.log"
echo "  tail -f /tmp/eod_trigger.log"
echo "  tail -f /tmp/bot_health.log"
echo ""
