#!/bin/bash
# Install cron jobs for CE_OPTIONS bots (OTM + ITM).
# Manages: daily reboot, instrument refresh, router health, bot health, log cleanup.

BOT_DIR="/root/santhosh/trading/CE_OPTIONS"
PYTHON="/usr/bin/python3"

CRON_REBOOT='45 8 * * 1-5 /sbin/shutdown -r +5 # Reboot at 08:45 (5-min delay, market prep)'
CRON_INSTRUMENT="50 8 * * 1-5 cd $BOT_DIR && $PYTHON tools/fetch_nfo_instruments.py >> /tmp/ce_instrument_refresh.log 2>&1 # CE instrument refresh"
CRON_ROUTER='55 8 * * 1-5 systemctl is-active --quiet webhook-router.service || systemctl start webhook-router.service >> /tmp/router_health.log 2>&1 # Router health'
CRON_OTM_START='0 9 * * 1-5 systemctl is-active --quiet ce-otm.service || systemctl start ce-otm.service >> /tmp/ce_otm_startup.log 2>&1 # CE OTM auto-start'
CRON_ITM_START='0 9 * * 1-5 systemctl is-active --quiet ce-itm.service || systemctl start ce-itm.service >> /tmp/ce_itm_startup.log 2>&1 # CE ITM auto-start'
CRON_OTM_HEALTH='*/30 9-16 * * 1-5 systemctl is-active --quiet ce-otm.service || systemctl start ce-otm.service >> /tmp/ce_otm_health.log 2>&1 # CE OTM health'
CRON_ITM_HEALTH='*/30 9-16 * * 1-5 systemctl is-active --quiet ce-itm.service || systemctl start ce-itm.service >> /tmp/ce_itm_health.log 2>&1 # CE ITM health'
CRON_CLEANUP="0 0 * * 0 find $BOT_DIR/OTM/logs $BOT_DIR/ITM/logs -type d -mtime +7 -exec rm -rf {} \\; 2>/dev/null # CE log cleanup"

echo "=== CE OPTIONS CRON SETUP ==="

CRONTAB_BACKUP="/tmp/crontab_backup_$(date +%s).txt"
crontab -l > "$CRONTAB_BACKUP" 2>/dev/null || true
echo "Backed up crontab to $CRONTAB_BACKUP"

TEMP_CRON="/tmp/new_crontab_ce.txt"
crontab -l > "$TEMP_CRON" 2>/dev/null || true

remove_line() { grep -Fvx -- "$1" "$TEMP_CRON" > "${TEMP_CRON}.tmp" 2>/dev/null || true; mv "${TEMP_CRON}.tmp" "$TEMP_CRON"; }

remove_line "$CRON_REBOOT"
remove_line "$CRON_INSTRUMENT"
remove_line "$CRON_ROUTER"
remove_line "$CRON_OTM_START"
remove_line "$CRON_ITM_START"
remove_line "$CRON_OTM_HEALTH"
remove_line "$CRON_ITM_HEALTH"
remove_line "$CRON_CLEANUP"

echo "$CRON_REBOOT"      >> "$TEMP_CRON" && echo "Added: reboot 08:45"
echo "$CRON_INSTRUMENT"  >> "$TEMP_CRON" && echo "Added: instrument refresh 08:50"
echo "$CRON_ROUTER"      >> "$TEMP_CRON" && echo "Added: router health 08:55"
echo "$CRON_OTM_START"   >> "$TEMP_CRON" && echo "Added: CE OTM auto-start 09:00"
echo "$CRON_ITM_START"   >> "$TEMP_CRON" && echo "Added: CE ITM auto-start 09:00"
echo "$CRON_OTM_HEALTH"  >> "$TEMP_CRON" && echo "Added: CE OTM health every 30m"
echo "$CRON_ITM_HEALTH"  >> "$TEMP_CRON" && echo "Added: CE ITM health every 30m"
echo "$CRON_CLEANUP"     >> "$TEMP_CRON" && echo "Added: log cleanup weekly"

crontab "$TEMP_CRON"
rm "$TEMP_CRON"

echo ""
echo "=== INSTALLED CRON JOBS ==="
crontab -l | grep -E "reboot|ce_|ce-otm|ce-itm|router|cleanup" || true
echo ""
echo "Restore with: crontab $CRONTAB_BACKUP"
