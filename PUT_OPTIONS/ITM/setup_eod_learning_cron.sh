#!/bin/bash

################################################################################
# Setup EOD Learning Cron Job
#
# Adds a cron entry to run EOD learning aggregator at 3:30 PM on trading days
# Only runs Monday-Friday (1-5), holidays are handled by the wrapper script
#
# Run this script once to setup the cron job
################################################################################

CRON_JOB="30 15 * * 1-5 /root/santhosh/trading/ITM_put_options/run_eod_learning.sh"
TEMP_CRON="/tmp/eod_learning_cron.txt"

echo "Setting up EOD Learning Cron Job..."
echo "Schedule: 3:30 PM (15:30) on weekdays (Monday-Friday)"
echo "Command: $CRON_JOB"
echo ""

# Get current crontab
crontab -l > "$TEMP_CRON" 2>/dev/null || echo "# New crontab" > "$TEMP_CRON"

# Check if this bot's job already exists
if grep -Fq "$CRON_JOB" "$TEMP_CRON"; then
    echo "⚠️  EOD Learning cron job already exists"
    echo "Current entry:"
    grep -F "$CRON_JOB" "$TEMP_CRON"
    rm "$TEMP_CRON"
    exit 0
fi

# Add the new cron job
echo "$CRON_JOB" >> "$TEMP_CRON"

# Install the updated crontab
crontab "$TEMP_CRON"
rm "$TEMP_CRON"

echo "✅ EOD Learning cron job installed successfully"
echo ""
echo "Cron job details:"
echo "  Time: 3:30 PM (15:30) IST"
echo "  Days: Monday-Friday"
echo "  Script: /root/santhosh/trading/ITM_put_options/run_eod_learning.sh"
echo "  Log: /root/santhosh/trading/ITM_put_options/logs/eod_learning.log"
echo ""
echo "The script will:"
echo "  1. Skip weekends automatically"
echo "  2. Skip market holidays (NSE calendar)"
echo "  3. Prevent duplicate learning on same day"
echo "  4. Archive learning data after each run"
echo ""
echo "Verify installation with: crontab -l | grep eod_learning"
