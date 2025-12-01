#!/bin/bash

# Cron Setup Script for Options Bot
# Automatically starts bot at 9 AM and triggers EOD learning at 3:30 PM
# This ensures EOD learning never misses due to late bot starts

BOT_DIR="/root/santhosh/trading/options"
PYTHON="/usr/bin/python3"

echo "=========================================="
echo "📅 Setting up Automatic Options Trading Schedule"
echo "=========================================="
echo ""

# Backup current crontab
CRONTAB_BACKUP="/tmp/crontab_backup_$(date +%s).txt"
crontab -l > "$CRONTAB_BACKUP" 2>/dev/null || true

echo "Current crontab backed up to: $CRONTAB_BACKUP"
echo ""

# Create a temporary crontab file
TEMP_CRON="/tmp/new_crontab_options.txt"

# Copy existing crontab
crontab -l > "$TEMP_CRON" 2>/dev/null || true

# Remove old options bot-related entries
sed -i '/options.*bot/d' "$TEMP_CRON" 2>/dev/null || true
sed -i '/optbot/d' "$TEMP_CRON" 2>/dev/null || true
sed -i '/main.py.*options/d' "$TEMP_CRON" 2>/dev/null || true

echo "Adding new cron jobs for Options Bot..."
echo ""

# Add reboot at 8:45 AM (5 minute delay, finishes by 8:50)
echo "45 8 * * 1-5 /sbin/shutdown -r +5 # System reboot at 8:45 AM (5 min delay)" >> "$TEMP_CRON"
echo "✅ Added: System reboot at 08:45 AM with 5-min delay (Mon-Fri)"

# Add webhook router check at 8:55 AM (ensure it's running)
echo "55 8 * * 1-5 sudo systemctl is-active webhook-router || sudo systemctl start webhook-router >> /tmp/router_health.log 2>&1 # Ensure router is running" >> "$TEMP_CRON"
echo "✅ Added: Webhook router health check at 08:55 AM (Mon-Fri)"

# Add bot startup at 9 AM (Monday-Friday) - now on port 8081
echo "0 9 * * 1-5 cd $BOT_DIR && python3 main.py >> /tmp/optbot_startup.log 2>&1 # Options bot auto-start (port 8081)" >> "$TEMP_CRON"
echo "✅ Added: Auto-start options bot at 09:00 AM on port 8081 (Mon-Fri)"

# Add bot health check every 30 minutes during trading hours (9 AM to 4 PM)
echo "*/30 9-16 * * 1-5 pgrep -f 'python.*main.py.*options' > /dev/null || cd $BOT_DIR && python3 main.py >> /tmp/optbot_health.log 2>&1 # Options bot health check" >> "$TEMP_CRON"
echo "✅ Added: Options bot health check every 30 min (09:00-16:00)"

# Add log cleanup (keep only 7 days)
echo "0 0 * * 0 find $BOT_DIR/logs -type d -mtime +7 -exec rm -rf {} \; 2>/dev/null # Options cleanup old logs" >> "$TEMP_CRON"
echo "✅ Added: Log cleanup (daily, keep 7 days)"

# Add EOD stats export at 4:00 PM (after market close)
echo "0 16 * * 1-5 cd $BOT_DIR && python3 -c \"from optcode.trade_logger import get_trade_logger; logger = get_trade_logger(); logger.export_analysis()\" >> /tmp/optbot_eod_export.log 2>&1 # EOD stats export" >> "$TEMP_CRON"
echo "✅ Added: EOD stats export at 16:00 (Mon-Fri)"

echo ""
echo "=========================================="
echo "Preview of new cron jobs:"
echo "=========================================="
grep -E "reboot|router|optbot|options bot" "$TEMP_CRON" || true

echo ""
echo "=========================================="
echo "Installing crontab..."
echo "=========================================="

# Install new crontab
crontab "$TEMP_CRON"

# Verify installation
echo ""
echo "Installed cron jobs for Options Bot:"
crontab -l | grep -E "reboot|router|optbot|options bot" || echo "No matching jobs found"

echo ""
echo "=========================================="
echo "✅ Options Bot Cron setup complete!"
echo "=========================================="
echo ""
echo "Schedule:"
echo "  🔄 08:45 AM  - System reboot (5-min delay, done by 08:50)"
echo "  🚀 08:55 AM  - Webhook router health check (ensure running)"
echo "  🕘 09:00 AM  - Options bot auto-starts (port 8081)"
echo "  🔍 Every 30m - Options bot health check (09:00-16:00)"
echo "  📊 16:00 PM  - EOD stats export"
echo "  🗑️  00:00 AM - Cleanup old logs"
echo ""
echo "Backup of old crontab: $CRONTAB_BACKUP"
echo "Restore with: crontab $CRONTAB_BACKUP"
echo ""
echo "Monitor cron logs:"
echo "  tail -f /tmp/router_health.log"
echo "  tail -f /tmp/optbot_startup.log"
echo "  tail -f /tmp/optbot_health.log"
echo "  tail -f /tmp/optbot_eod_export.log"
echo ""
