#!/bin/bash

# Quick-start script for bulk data collection and preparation
# Usage: ./start_data_collection.sh [days] [workers]

set -e

DAYS=${1:-30}
WORKERS=${2:-5}

TRADING_ROOT="/root/santhosh/trading"
TOOLS_DIR="$TRADING_ROOT/options/tools"
DATA_DIR="$TRADING_ROOT/options/data/training"

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║         BULK HISTORICAL DATA COLLECTION & PREPARATION         ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 Configuration:"
echo "   Days to collect:    $DAYS"
echo "   Parallel workers:   $WORKERS"
echo "   Output directory:   $DATA_DIR"
echo ""

# Step 1: Collect data
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 1: Collecting historical data..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 "$TOOLS_DIR/bulk_historical_data_collector.py" \
    --days "$DAYS" \
    --workers "$WORKERS"

if [ $? -ne 0 ]; then
    echo "❌ Data collection failed"
    exit 1
fi

echo ""
echo "✅ Data collection complete"
echo ""

# Step 2: Check collected data
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 2: Verifying collected data..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

CANDLES=$(wc -l < "$DATA_DIR/historical_candles.csv" 2>/dev/null || echo 0)
GREEKS=$(wc -l < "$DATA_DIR/historical_greeks.csv" 2>/dev/null || echo 0)

echo "   Candles: $((CANDLES - 1)) rows"
echo "   Greeks:  $((GREEKS - 1)) rows"
echo ""

if [ "$CANDLES" -lt 10 ] || [ "$GREEKS" -lt 10 ]; then
    echo "⚠️  Warning: Very little data collected"
    echo "   This may affect training quality"
fi

echo ""

# Step 3: Prepare data
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 3: Preparing data for neural training..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 "$TOOLS_DIR/data_preparation_pipeline.py"

if [ $? -ne 0 ]; then
    echo "❌ Data preparation failed"
    exit 1
fi

echo ""

# Step 4: Show results
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 4: Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "✅ All steps completed successfully!"
echo ""
echo "📁 Output files:"
echo ""

# List output files with sizes
if [ -d "$DATA_DIR/prepared" ]; then
    ls -lh "$DATA_DIR/prepared/" | tail -n +2 | awk '{printf "   %s  (%s)\n", $9, $5}'
fi

echo ""
echo "📊 Data summary:"
if [ -f "$DATA_DIR/prepared/data_info.json" ]; then
    python3 << 'PYTHON'
import json
with open('/root/santhosh/trading/options/data/training/prepared/data_info.json') as f:
    info = json.load(f)
    print(f"   Total rows:    {info['total_rows']:,}")
    print(f"   Sequences:     {info['sequences']:,}")
    print(f"   Seq length:    {info['sequence_length']}")
    print(f"   Features:      {info['features']}")
    print(f"   Symbols:       {info['symbols']}")
    print(f"   Date range:    {info['date_range']['start']} to {info['date_range']['end']}")
PYTHON
fi

echo ""
echo "🚀 Next steps:"
echo "   1. Train LSTM/CNN models with prepared data"
echo "   2. Backtest predictions on sequences"
echo "   3. Deploy neural filter into bot entry logic"
echo "   4. Monitor improvements (target: 50-55% win rate)"
echo ""
