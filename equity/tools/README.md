# Trading Bot Tools Directory

This directory contains standalone command-line tools for trading analysis and system management.

## 📊 Analytics Tools

### **Missed Opportunities Analysis**
```bash
python3 missed_opportunities.py                    # Today's analysis
python3 missed_opportunities.py --date 25-10-2025  # Specific date
python3 missed_opportunities.py --summary          # Quick summary only
python3 missed_opportunities.py --json             # JSON output
```

**Purpose**: Analyze why alerts were missed during trading session. Provides insights into capital constraints, position limits, and execution failures.

### **Post-Session Analysis**
```bash
python3 post_session_analysis.py                   # Today's analysis
python3 post_session_analysis.py 25-10-2025       # Specific date
python3 post_session_analysis.py --backfill        # Backfill missing dates
python3 post_session_analysis.py --json            # JSON output
```

**Purpose**: Comprehensive end-of-day performance analysis. Parses logs, calculates target achievement, provides strategy recommendations.

## 🛠️ System Management Tools

### **Log Cleanup**
```bash
python3 cleanup_logs.py
```

**Purpose**: Consolidates duplicate log directories and manages log retention.

### **Instrument Data**
```bash
python3 download_instruments.py
```

**Purpose**: Updates AngelOne instrument data for symbol mapping.

### **Target Monitoring**
```bash
python3 target_monitor.py
```

**Purpose**: Real-time monitoring of 5% daily target progress.

## 🧪 Testing Framework

See `testing/` subdirectory for comprehensive test suite including:
- Unit tests for core functionality
- Integration tests for API endpoints
- Alert burst handling tests
- Race condition prevention tests

## 📋 Usage Tips

1. **Run from equity root directory**: `cd /root/santhosh/trading/equity`
2. **Virtual environment**: Tools automatically use the configured .venv
3. **JSON output**: Add `--json` flag for programmatic processing
4. **Date format**: Always use DD-MM-YYYY format for dates
5. **Help**: Add `--help` to any tool for detailed usage information

## 🔗 Integration

These tools are also available via web API endpoints:
- **Missed Opportunities**: `GET /missed_opportunities?date=DD-MM-YYYY`
- **Post-Session Analysis**: `GET /analytics/post-session-analysis?date=DD-MM-YYYY`

For complete documentation, see: `../docs/analytics.md` and `../docs/equity-bot.md`