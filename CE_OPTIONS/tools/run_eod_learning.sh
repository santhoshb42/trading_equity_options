#!/bin/bash
# Run EOD learning for one CE_OPTIONS mode (OTM or ITM).
# Usage: BOT_MODE=OTM ./run_eod_learning.sh OTM
#        BOT_MODE=ITM ./run_eod_learning.sh ITM

MODE="${1:-${BOT_MODE:-OTM}}"
MODE="${MODE^^}"

BOT_DIR="/root/santhosh/trading/CE_OPTIONS"
DATA_DIR="$BOT_DIR/$MODE/data"
LOG_FILE="$BOT_DIR/$MODE/logs/eod_learning.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$MODE] $1" | tee -a "$LOG_FILE"; }

log "========== EOD Learning Started =========="

# Weekend check
DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then log "SKIPPED: weekend"; exit 0; fi

TODAY=$(date +%Y-%m-%d)
HOLIDAYS=("2026-01-26" "2026-03-10" "2026-03-25" "2026-04-02" "2026-04-14"
          "2026-04-21" "2026-05-01" "2026-05-15" "2026-08-15" "2026-08-19"
          "2026-10-02" "2026-10-25" "2026-11-11" "2026-11-16" "2026-12-25")
for h in "${HOLIDAYS[@]}"; do [ "$TODAY" = "$h" ] && log "SKIPPED: market holiday ($h)" && exit 0; done

PNL_FILE="$DATA_DIR/option_pnl_history.json"
if [ ! -f "$PNL_FILE" ]; then log "SKIPPED: no pnl history at $PNL_FILE"; exit 0; fi

ARCHIVE_COUNT=$(find "$DATA_DIR/archive" -name "*${TODAY}*" -type f 2>/dev/null | wc -l)
if [ "$ARCHIVE_COUNT" -gt 0 ]; then log "SKIPPED: already ran today"; exit 0; fi

log "Running EOD aggregation..."

BOT_MODE="$MODE" python3 -c "
import sys, os
os.environ['BOT_MODE'] = '$MODE'
sys.path.insert(0, '$BOT_DIR')
from optcode.eod_learning_aggregator import run_eod_learning
import json
result = run_eod_learning()
print(json.dumps(result, indent=2))
" >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    log "EOD Learning Completed"
else
    log "EOD Learning FAILED (exit $?)"
    exit 1
fi
