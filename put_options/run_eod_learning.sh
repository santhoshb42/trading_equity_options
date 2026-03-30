#!/bin/bash

################################################################################
# Smart EOD Learning Wrapper Script
#
# Only runs on trading days (Monday-Friday)
# Skips weekends and Indian stock market holidays
# Prevents duplicate learning from the same data
#
# Usage: /root/santhosh/trading/put_options/run_eod_learning.sh
# Typically run via cron at market close (3:30 PM IST): 30 15 * * 1-5
################################################################################

LOG_FILE="/root/santhosh/trading/put_options/logs/eod_learning.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "=========================================="
log "EOD Learning Wrapper Started"

# Get current date and day of week
TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)  # 1=Monday, 2=Tuesday, ..., 5=Friday, 6=Saturday, 7=Sunday

log "Today: $TODAY | Day of Week: $DOW"

# Check if today is a weekend (Saturday=6, Sunday=7)
if [ "$DOW" -eq 6 ] || [ "$DOW" -eq 7 ]; then
    log "⏭️  SKIPPED: Weekend (market closed)"
    exit 0
fi

# Indian Stock Market Holidays for 2025-2026
# Check against this list to prevent learning on market closed days
# Format: YYYY-MM-DD
MARKET_HOLIDAYS=(
    "2025-01-26"  # Republic Day
    "2025-03-08"  # Maha Shivaratri
    "2025-03-25"  # Holi
    "2025-03-29"  # Good Friday
    "2025-04-11"  # Eid-ul-Fitr (market may be open or closed - check official NSE)
    "2025-04-17"  # Ram Navami
    "2025-04-21"  # Mahavir Jayanti
    "2025-05-01"  # May Day
    "2025-05-23"  # Buddha Purnima
    "2025-07-17"  # Muharram
    "2025-08-15"  # Independence Day
    "2025-08-27"  # Janmashtami
    "2025-09-16"  # Milad-un-Nabi
    "2025-10-02"  # Gandhi Jayanti
    "2025-10-20"  # Dussehra
    "2025-10-29"  # Diwali
    "2025-10-30"  # Diwali (day 2, if market closed)
    "2025-11-01"  # Diwali (day 3, if market closed)
    "2025-11-15"  # Guru Nanak Jayanti
    "2025-12-25"  # Christmas
    # 2026 holidays
    "2026-01-26"  # Republic Day
    "2026-03-10"  # Holi
    "2026-03-25"  # Ramzan Id (Eid-ul-Fitr)
    "2026-04-02"  # Good Friday
    "2026-04-14"  # Ambedkar Jayanti
    "2026-04-21"  # Ram Navami
    "2026-05-01"  # May Day
    "2026-05-15"  # Buddha Purnima
    "2026-08-15"  # Independence Day
    "2026-08-19"  # Janmashtami
    "2026-09-30"  # Gandhi Jayanti (observed)
    "2026-10-02"  # Gandhi Jayanti
    "2026-10-25"  # Dussehra
    "2026-11-11"  # Diwali
    "2026-11-16"  # Guru Nanak Jayanti
    "2026-12-25"  # Christmas
)

# Check if today is a market holiday
for holiday in "${MARKET_HOLIDAYS[@]}"; do
    if [ "$TODAY" = "$holiday" ]; then
        log "⏭️  SKIPPED: Market Holiday ($TODAY)"
        exit 0
    fi
done

log "✅ Trading day detected - proceeding with EOD learning"

# Check if learning files exist (option_pnl_history.json is required)
PNL_FILE="/root/santhosh/trading/put_options/data/option_pnl_history.json"
if [ ! -f "$PNL_FILE" ]; then
    log "⚠️  WARNING: option_pnl_history.json not found - skipping learning"
    exit 0
fi

# Check if this script already ran today (by checking if archive has today's files)
ARCHIVE_DIR="/root/santhosh/trading/put_options/data/archive"
ARCHIVE_COUNT=$(find "$ARCHIVE_DIR" -name "*${TODAY}*" -type f 2>/dev/null | wc -l)
if [ "$ARCHIVE_COUNT" -gt 0 ]; then
    log "⚠️  WARNING: Learning already completed today (archive files exist)"
    log "   Skipping to prevent duplicate learning from same data"
    exit 0
fi

log "Running EOD learning aggregation..."

# Run the Python EOD learning script
cd "$SCRIPT_DIR"
python3 -c "
import sys
sys.path.insert(0, '/root/santhosh/trading/put_options/optcode')
from eod_learning_aggregator import run_eod_learning
import json

result = run_eod_learning()
print(json.dumps(result, indent=2))
" >> "$LOG_FILE" 2>&1

RESULT=$?
if [ $RESULT -eq 0 ]; then
    log "✅ EOD Learning Completed Successfully"
else
    log "❌ EOD Learning Failed (exit code: $RESULT)"
    exit 1
fi

log "=========================================="
exit 0
