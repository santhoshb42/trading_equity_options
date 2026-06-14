#!/bin/bash
# Install EOD learning cron job for PUT_OPTIONS bots.
# Runs at 15:30 on weekdays for both OTM and ITM modes.

CRON_OTM="30 15 * * 1-5 BOT_MODE=OTM /root/santhosh/trading/PUT_OPTIONS/tools/run_eod_learning.sh OTM >> /tmp/pe_otm_eod_learning.log 2>&1"
CRON_ITM="30 15 * * 1-5 BOT_MODE=ITM /root/santhosh/trading/PUT_OPTIONS/tools/run_eod_learning.sh ITM >> /tmp/pe_itm_eod_learning.log 2>&1"
TEMP_CRON="/tmp/eod_learning_cron_pe.txt"

echo "Setting up EOD Learning Cron for PUT_OPTIONS (OTM + ITM)..."

crontab -l > "$TEMP_CRON" 2>/dev/null || echo "# New crontab" > "$TEMP_CRON"

grep -Fvx "$CRON_OTM" "$TEMP_CRON" > "${TEMP_CRON}.tmp" 2>/dev/null || true; mv "${TEMP_CRON}.tmp" "$TEMP_CRON"
grep -Fvx "$CRON_ITM" "$TEMP_CRON" > "${TEMP_CRON}.tmp" 2>/dev/null || true; mv "${TEMP_CRON}.tmp" "$TEMP_CRON"

echo "$CRON_OTM" >> "$TEMP_CRON"
echo "$CRON_ITM" >> "$TEMP_CRON"

crontab "$TEMP_CRON"
rm "$TEMP_CRON"

echo "Installed:"
crontab -l | grep "PUT_OPTIONS.*run_eod_learning"
