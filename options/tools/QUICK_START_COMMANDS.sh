#!/usr/bin/env bash
# QUICK REFERENCE: Bulk Data Collection Commands
# Copy-paste these to get started quickly

# ============================================================================
# ONE-COMMAND SOLUTION
# ============================================================================

# Make script executable (first time only)
chmod +x /root/santhosh/trading/options/tools/start_data_collection.sh

# Run collection with default settings (30 days, 5 workers)
/root/santhosh/trading/options/tools/start_data_collection.sh

# Run with more data (60 days) and more workers (10 = faster)
/root/santhosh/trading/options/tools/start_data_collection.sh 60 10

# ============================================================================
# STEP-BY-STEP ALTERNATIVE
# ============================================================================

# Step 1: Collect 30 days of data
python3 /root/santhosh/trading/options/tools/bulk_historical_data_collector.py \
    --days 30 \
    --workers 5

# Step 2: Prepare data for neural training
python3 /root/santhosh/trading/options/tools/data_preparation_pipeline.py

# Step 3: Check what was created
ls -lh /root/santhosh/trading/options/data/training/prepared/

# ============================================================================
# CUSTOM OPTIONS
# ============================================================================

# Collect only specific symbols
python3 /root/santhosh/trading/options/tools/bulk_historical_data_collector.py \
    --symbols HCLTECH UBL SAIL VEDL HINDALCO

# Collect 60 days with 10 workers (fast!)
python3 /root/santhosh/trading/options/tools/bulk_historical_data_collector.py \
    --days 60 \
    --workers 10

# Check only (no collection)
python3 /root/santhosh/trading/options/tools/bulk_historical_data_collector.py --stats

# ============================================================================
# MONITORING
# ============================================================================

# Watch progress in real-time
tail -f /root/santhosh/trading/options/data/training/historical_candles.csv

# Count rows collected so far
wc -l /root/santhosh/trading/options/data/training/*.csv

# Check final result
python3 << 'EOF'
import json
with open('/root/santhosh/trading/options/data/training/prepared/data_info.json') as f:
    info = json.load(f)
    print(f"✅ Total sequences ready: {info['sequences']:,}")
    print(f"   Symbols covered: {info['symbols']}")
    print(f"   Date range: {info['date_range']['start']} to {info['date_range']['end']}")
EOF

# ============================================================================
# TROUBLESHOOTING
# ============================================================================

# If collector fails, check API credentials
python3 << 'EOF'
import sys
sys.path.insert(0, '/root/santhosh/trading/options/optcode')
from config import BROKER_API_KEY, BROKER_CLIENT_ID
print(f"API Key: {BROKER_API_KEY[:10]}...")
print(f"Client ID: {BROKER_CLIENT_ID}")
EOF

# If preparation fails, check source files exist
test -f /root/santhosh/trading/options/data/training/historical_candles.csv && \
echo "✅ Candles file found" || \
echo "❌ Run collector first"

# If data is incomplete, increase workers
python3 /root/santhosh/trading/options/tools/bulk_historical_data_collector.py \
    --days 30 \
    --workers 20  # Maximum workers

# ============================================================================
# INTEGRATION WITH YOUR TRADING BOT
# ============================================================================

# Run collection every 3 days automatically
(crontab -l 2>/dev/null; echo "0 18 */3 * * /root/santhosh/trading/options/tools/start_data_collection.sh") | crontab -

# Or run daily after market close
(crontab -l 2>/dev/null; echo "0 16 * * 1-5 python3 /root/santhosh/trading/options/tools/bulk_historical_data_collector.py --days 1") | crontab -

# ============================================================================
# NEXT STEPS
# ============================================================================

# After Jan 10 (when you have 50+ trades), run:
# /root/santhosh/trading/options/tools/start_data_collection.sh

# After Jan 20 (when you have 200+ trades), run:
# /root/santhosh/trading/options/tools/start_data_collection.sh 20 10

# Then train neural models (Jan 21+):
# python3 train_neural_ml.py --data /root/santhosh/trading/options/data/training/prepared/sequences.npy

# ============================================================================
# DOCUMENTATION
# ============================================================================

# For detailed guide:
cat /root/santhosh/trading/options/tools/BULK_DATA_COLLECTION_GUIDE.md

# ============================================================================
