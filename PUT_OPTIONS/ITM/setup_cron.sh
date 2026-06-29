#!/bin/bash

# Cron Setup Script for ITM PUT Options Bot
# Automatically starts bot at 9 AM and triggers EOD learning at 3:30 PM
# This ensures EOD learning never misses due to late bot starts

BOT_DIR="/root/santhosh/trading/ITM_put_options"
PYTHON="/usr/bin/python3"

CRON_REBOOT='45 8 * * 1-5 /sbin/shutdown -r +5 # System reboot at 8:45 AM (5 min delay)'
LEGACY_CRON_REBOOT='45 8 * * 1-5 /sbin/shutdown -r +5'
CRON_INSTRUMENT='50 8 * * 1-5 cd /root/santhosh/trading/ITM_put_options && python3 tools/fetch_nfo_instruments.py >> /tmp/optbot_instrument_refresh.log 2>&1 # Daily instrument refresh'
CRON_ROUTER='55 8 * * 1-5 systemctl is-active --quiet webhook-router.service || systemctl start webhook-router.service >> /tmp/router_health.log 2>&1 # Ensure router is running'
LEGACY_CRON_ROUTER='55 8 * * 1-5 sudo systemctl is-active webhook-router || sudo systemctl start webhook-router >> /tmp/router_health.log 2>&1'
CRON_START='0 9 * * 1-5 systemctl start itm_put_optbot.service >> /tmp/itm_put_optbot_startup.log 2>&1 # ITM put options bot auto-start (port 8083)'
CRON_HEALTH='*/30 9-16 * * 1-5 systemctl is-active --quiet itm_put_optbot.service || systemctl start itm_put_optbot.service >> /tmp/itm_put_optbot_health.log 2>&1 # ITM put options bot health check'
LEGACY_CRON_START='0 9 * * 1-5 cd /root/santhosh/trading/ITM_put_options && python3 main.py >> /tmp/itm_put_optbot_startup.log 2>&1 # ITM put options bot auto-start (port 8083)'
LEGACY_CRON_HEALTH_DIRECT="*/30 9-16 * * 1-5 pgrep -f '/root/santhosh/trading/ITM_put_options/main.py' > /dev/null || cd /root/santhosh/trading/ITM_put_options && python3 main.py >> /tmp/itm_put_optbot_health.log 2>&1 # ITM put options bot health check"
CRON_CLEANUP='0 0 * * 0 find /root/santhosh/trading/ITM_put_options/logs -type d -mtime +7 -exec rm -rf {} \; 2>/dev/null # ITM options cleanup old logs'
CRON_EXPORT='0 16 * * 1-5 cd /root/santhosh/trading/ITM_put_options && python3 -c "from optcode.trade_logger import get_trade_logger; logger = get_trade_logger(); logger.export_analysis()" >> /tmp/itm_put_optbot_eod_export.log 2>&1 # EOD stats export'

echo "=========================================="
echo "📅 Setting up Automatic ITM Options Trading Schedule"
echo "=========================================="
echo ""

# Backup current crontab
CRONTAB_BACKUP="/tmp/crontab_backup_$(date +%s).txt"
crontab -l > "$CRONTAB_BACKUP" 2>/dev/null || true

echo "Current crontab backed up to: $CRONTAB_BACKUP"
echo ""

# Create a temporary crontab file
TEMP_CRON="/tmp/new_crontab_itm_options.txt"

# Copy existing crontab
crontab -l > "$TEMP_CRON" 2>/dev/null || true

remove_line() {
	local line="$1"
	local tmp_file
	tmp_file="${TEMP_CRON}.tmp"
	grep -Fvx -- "$line" "$TEMP_CRON" > "$tmp_file" 2>/dev/null || true
	mv "$tmp_file" "$TEMP_CRON"
}

# Remove only this bot's managed lines so other bots keep their cron jobs.
remove_line "$CRON_REBOOT"
remove_line "$LEGACY_CRON_REBOOT"
remove_line "$CRON_INSTRUMENT"
remove_line "$CRON_ROUTER"
remove_line "$LEGACY_CRON_ROUTER"
remove_line "$CRON_START"
remove_line "$CRON_HEALTH"
remove_line "$LEGACY_CRON_START"
remove_line "$LEGACY_CRON_HEALTH_DIRECT"
remove_line "$CRON_CLEANUP"
remove_line "$CRON_EXPORT"

echo "Adding new cron jobs for ITM PUT Options Bot..."
echo ""

# Add reboot at 8:45 AM (5 minute delay, finishes by 8:50)
echo "$CRON_REBOOT" >> "$TEMP_CRON"
echo "✅ Added: System reboot at 08:45 AM with 5-min delay (Mon-Fri)"

# Add instrument.json refresh at 8:50 AM (before market open)
echo "$CRON_INSTRUMENT" >> "$TEMP_CRON"
echo "✅ Added: Daily instrument.json refresh at 08:50 AM (Mon-Fri)"

# Add webhook router check at 8:55 AM (ensure it's running)
echo "$CRON_ROUTER" >> "$TEMP_CRON"
echo "✅ Added: Webhook router health check at 08:55 AM (Mon-Fri)"

# Add bot startup at 9 AM (Monday-Friday) - ITM bot on port 8083
echo "$CRON_START" >> "$TEMP_CRON"
echo "✅ Added: Auto-start ITM put options bot at 09:00 AM on port 8083 (Mon-Fri)"

# Add bot health check every 30 minutes during trading hours (9 AM to 4 PM)
echo "$CRON_HEALTH" >> "$TEMP_CRON"
echo "✅ Added: ITM put options bot health check every 30 min (09:00-16:00)"

# Add log cleanup (keep only 7 days)
echo "$CRON_CLEANUP" >> "$TEMP_CRON"
echo "✅ Added: Log cleanup (daily, keep 7 days)"

# Add EOD stats export at 4:00 PM (after market close)
echo "$CRON_EXPORT" >> "$TEMP_CRON"
echo "✅ Added: EOD stats export at 16:00 (Mon-Fri)"

echo ""
echo "=========================================="
echo "Preview of new cron jobs:"
echo "=========================================="
grep -E "reboot|router|optbot|options bot|instrument" "$TEMP_CRON" || true

echo ""
echo "=========================================="
echo "Installing crontab..."
echo "=========================================="

# Install new crontab
crontab "$TEMP_CRON"

# Verify installation
echo ""
echo "Installed cron jobs for Options Bot:"
crontab -l | grep -E "reboot|router|optbot|options bot|instrument" || echo "No matching jobs found"

echo ""
echo "=========================================="
echo "✅ ITM PUT Options Bot Cron setup complete!"
echo "=========================================="
echo ""
echo "Schedule:"
echo "  🔄 08:45 AM  - System reboot (5-min delay, done by 08:50)"
echo "  📊 08:50 AM  - Refresh instrument.json from broker"
echo "  🚀 08:55 AM  - Webhook router health check (ensure running)"
echo "  🕘 09:00 AM  - ITM put options bot auto-starts (port 8083)"
echo "  🔍 Every 30m - ITM put options bot health check (09:00-16:00)"
echo "  📊 16:00 PM  - EOD stats export"
echo "  🗑️  00:00 AM - Cleanup old logs"
echo ""
echo "Backup of old crontab: $CRONTAB_BACKUP"
echo "Restore with: crontab $CRONTAB_BACKUP"
echo ""
echo "Monitor cron logs:"
echo "  tail -f /tmp/optbot_instrument_refresh.log"
echo "  tail -f /tmp/router_health.log"
echo "  tail -f /tmp/itm_put_optbot_startup.log"
echo "  tail -f /tmp/itm_put_optbot_health.log"
echo "  tail -f /tmp/itm_put_optbot_eod_export.log"
echo ""
