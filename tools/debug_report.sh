#!/bin/bash
################################################################################
# EQUITY TRADING BOT - POST-TRADE DEBUGGING SCRIPT
# 
# This script aggregates all logs and performs comprehensive analysis
# to help debug rate limit, order, and ML issues after trading hours.
#
# Usage:
#   ./debug_report.sh                    # Analyze today's trading
#   ./debug_report.sh 2025-11-20         # Analyze specific date
#   ./debug_report.sh 2025-11-20 detailed # Verbose output
#
################################################################################

set -o pipefail

# Configuration
TRADING_DIR="/root/santhosh/trading/equity"
LOG_DATE="${1:-$(date +%Y-%m-%d)}"
DETAIL_MODE="${2:-standard}"
REPORT_FILE="/root/santhosh/trading/DEBUG_REPORT_$(date +%Y%m%d_%H%M%S).txt"

# Convert date formats
if [[ "$LOG_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    LOG_DIR="$TRADING_DIR/logs/$LOG_DATE"
    LEGACY_LOG_DIR="$TRADING_DIR/logs/$(echo $LOG_DATE | awk -F- '{print $3"-"$2"-"$1}')"
else
    LOG_DIR="$TRADING_DIR/logs/$LOG_DATE"
    LEGACY_LOG_DIR="$TRADING_DIR/logs/$LOG_DATE"
fi

# Use whichever exists
if [ -d "$LEGACY_LOG_DIR" ] && [ ! -d "$LOG_DIR" ]; then
    LOG_DIR="$LEGACY_LOG_DIR"
fi

if [ ! -d "$LOG_DIR" ]; then
    echo "❌ Log directory not found: $LOG_DIR"
    exit 1
fi

BOT_LOG="$LOG_DIR/bot.log"
ERROR_LOG="$LOG_DIR/errors.log"
DETAILED_LOG="$LOG_DIR/detailed.log"
TRADES_CSV="$LOG_DIR/trades.csv"
STATS_JSON="/root/santhosh/trading/equity/data/learning/symbol_stats.json"

echo "=================================="
echo "   EQUITY TRADING BOT DEBUG REPORT"
echo "=================================="
echo "Date: $LOG_DATE"
echo "Generated: $(date)"
echo "Report saved to: $REPORT_FILE"
echo ""

{
    echo "╔════════════════════════════════════════════════════════════════════════════╗"
    echo "║                    EQUITY TRADING BOT DEBUG REPORT                         ║"
    echo "║                                                                            ║"
    echo "║ Date: $LOG_DATE"
    echo "║ Generated: $(date)"
    echo "╚════════════════════════════════════════════════════════════════════════════╝"
    echo ""
    
    # SECTION 1: SYSTEM STATUS
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "SECTION 1: SYSTEM & BOT STATUS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    if [ ! -f "$BOT_LOG" ]; then
        echo "❌ Bot log not found: $BOT_LOG"
    else
        echo "✓ Bot log found: $(wc -l < $BOT_LOG) lines"
        echo ""
        
        # Startup verification
        if grep -q "All components initialized successfully" "$BOT_LOG"; then
            echo "✓ Bot startup successful"
            grep "All components initialized" "$BOT_LOG" | head -1
        else
            echo "⚠️ Bot startup issues detected"
        fi
        echo ""
        
        # Configuration
        echo "Configuration:"
        grep "Current configuration\|cap_per_trade\|default_sl" "$BOT_LOG" | head -1 | \
            sed 's/.*| CONFIG | //'
        echo ""
    fi
    
    # SECTION 2: RATE LIMITER ANALYSIS
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "SECTION 2: RATE LIMITER ANALYSIS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    RATE_LIMITER_INIT=$(grep -c "RATE_LIMITER.*initialized" "$BOT_LOG" 2>/dev/null || echo "0")
    API_CALLS=$(grep -c "API call recorded:" "$BOT_LOG" 2>/dev/null || echo "0")
    FAILED_CALLS=$(grep -c "Failed API call recorded" "$BOT_LOG" 2>/dev/null || echo "0")
    TIMEOUTS=$(grep -c "Call blocked due to timeout\|rate_limit.*timeout" "$BOT_LOG" 2>/dev/null || echo "0")
    
    echo "Rate Limiter Status:"
    echo "  Initialization: $([ "$RATE_LIMITER_INIT" -gt 0 ] && echo '✓ Done' || echo '✗ Failed')"
    echo "  Total API calls: $API_CALLS"
    echo "  Failed calls: $FAILED_CALLS"
    echo "  Timeout events: $TIMEOUTS"
    
    if [ "$TIMEOUTS" -gt 0 ]; then
        echo "  ⚠️  Rate limit timeouts detected!"
    fi
    echo ""
    
    # Peak load analysis
    echo "Peak Load Analysis:"
    PEAK_LOAD=$(grep "API call recorded" "$BOT_LOG" 2>/dev/null | \
        awk '{print $1, $2}' | \
        awk '{split($2, a, ":"); print a[1]":"a[2]}' | \
        sort | uniq -c | sort -rn | head -1)
    if [ -n "$PEAK_LOAD" ]; then
        echo "  Peak minute: $PEAK_LOAD API calls"
    fi
    echo ""
    
    # SECTION 3: ORDER EXECUTION ANALYSIS
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "SECTION 3: ORDER EXECUTION ANALYSIS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    TOTAL_ORDERS=$(grep -c "ORDER.*placed\|order_placed" "$BOT_LOG" 2>/dev/null || echo "0")
    CONFIRMED=$(grep -c "ORDER_CONFIRMED\|order_confirmed" "$BOT_LOG" 2>/dev/null || echo "0")
    FAILED=$(grep -c "ORDER_FAILED\|order.*failed" "$BOT_LOG" 2>/dev/null || echo "0")
    REJECTED=$(grep -c "ORDER_REJECTED\|order.*rejected" "$BOT_LOG" 2>/dev/null || echo "0")
    
    echo "Order Statistics:"
    echo "  Total placed: $TOTAL_ORDERS"
    echo "  Confirmed: $CONFIRMED"
    echo "  Failed: $FAILED"
    echo "  Rejected: $REJECTED"
    echo ""
    
    if [ -f "$TRADES_CSV" ]; then
        TRADE_COUNT=$(($(wc -l < "$TRADES_CSV") - 1))
        echo "Executed Trades:"
        echo "  Total trades: $TRADE_COUNT"
        
        # Trade breakdown
        BUY_COUNT=$(awk -F, 'NR>1 && $3=="BUY" {count++} END {print count}' "$TRADES_CSV" 2>/dev/null || echo "0")
        SELL_COUNT=$(awk -F, 'NR>1 && $3=="SELL" {count++} END {print count}' "$TRADES_CSV" 2>/dev/null || echo "0")
        echo "  Buys: $BUY_COUNT"
        echo "  Sells: $SELL_COUNT"
        echo ""
    fi
    
    # Capital status
    echo "Capital Management:"
    LAST_CAPITAL=$(grep "available_capital\|CAPITAL.*status" "$BOT_LOG" 2>/dev/null | tail -1)
    if [ -n "$LAST_CAPITAL" ]; then
        echo "  Last status: $LAST_CAPITAL" | sed 's/.*| //'
    fi
    
    CAPITAL_RELEASED=$(grep -c "CAPITAL_RELEASED" "$BOT_LOG" 2>/dev/null || echo "0")
    echo "  Capital releases: $CAPITAL_RELEASED"
    echo ""
    
    # SL Analysis
    echo "Stop Loss Orders:"
    SL_PLACED=$(grep -c "SL_PLACED\|SL_ORDER.*placed" "$BOT_LOG" 2>/dev/null || echo "0")
    SL_HIT=$(grep -c "SL_HIT\|stop.*loss.*triggered" "$BOT_LOG" 2>/dev/null || echo "0")
    SL_FAILED=$(grep -c "SL_FAILED\|SL.*error" "$BOT_LOG" 2>/dev/null || echo "0")
    echo "  Placed: $SL_PLACED"
    echo "  Triggered: $SL_HIT"
    echo "  Failed: $SL_FAILED"
    echo ""
    
    # SECTION 4: ML/LEARNING ANALYSIS
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "SECTION 4: ML/LEARNING ANALYSIS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    FEEDBACK_INIT=$(grep -c "FEEDBACK_INIT" "$BOT_LOG" 2>/dev/null || echo "0")
    FILTERED_SIGNALS=$(grep -c "FILTERED\|filter.*rejected" "$BOT_LOG" 2>/dev/null || echo "0")
    EOD_LEARNING=$(grep -c "EOD.*learning\|learning.*update" "$BOT_LOG" 2>/dev/null || echo "0")
    
    echo "Learning System Status:"
    echo "  Feedback initialized: $([ "$FEEDBACK_INIT" -gt 0 ] && echo '✓' || echo '✗')"
    echo "  Signals filtered: $FILTERED_SIGNALS"
    echo "  EOD learning triggers: $EOD_LEARNING"
    echo ""
    
    # Performance stats
    if [ -f "$STATS_JSON" ]; then
        echo "Symbol Performance:"
        python3 << 'EOF' 2>/dev/null
import json
try:
    with open('/root/santhosh/trading/equity/data/learning/symbol_stats.json') as f:
        stats = json.load(f)
    
    total_trades = 0
    total_wins = 0
    best_wr = 0
    worst_wr = 1.0
    best_symbol = ""
    worst_symbol = ""
    
    for sym, data in stats.items():
        trades = data.get('total_trades', 0)
        wins = data.get('winning_trades', 0)
        wr = data.get('win_rate', 0)
        
        total_trades += trades
        total_wins += wins
        
        if wr > best_wr:
            best_wr = wr
            best_symbol = sym
        if wr < worst_wr and trades > 0:
            worst_wr = wr
            worst_symbol = sym
    
    print(f"  Total symbols: {len(stats)}")
    print(f"  Total trades: {total_trades}")
    print(f"  Overall win rate: {total_wins/total_trades if total_trades > 0 else 0:.1%}")
    print(f"  Best performing: {best_symbol} ({best_wr:.1%})")
    print(f"  Worst performing: {worst_symbol} ({worst_wr:.1%})")
except:
    print("  Stats file not accessible")
EOF
    fi
    echo ""
    
    # SECTION 5: ERROR ANALYSIS
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "SECTION 5: ERROR ANALYSIS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    if [ -f "$ERROR_LOG" ]; then
        ERROR_COUNT=$(wc -l < "$ERROR_LOG" 2>/dev/null || echo "0")
        echo "Error Log Status:"
        echo "  Total errors: $ERROR_COUNT"
        echo ""
        
        if [ "$ERROR_COUNT" -gt 0 ]; then
            echo "Top 5 Error Types:"
            grep -oE "^\[.*?\]" "$ERROR_LOG" 2>/dev/null | sort | uniq -c | sort -rn | head -5 | \
                sed 's/^/  /'
            echo ""
        fi
    fi
    
    # SECTION 6: RECOMMENDATIONS
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "SECTION 6: RECOMMENDATIONS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    ISSUES=0
    
    # Check rate limit issues
    if [ "$TIMEOUTS" -gt 0 ]; then
        echo "⚠️  ISSUE 1: Rate Limit Timeouts Detected"
        echo "   Action: Increase MONITOR_INTERVAL from 20s to 30s in .env"
        echo "   Severity: MEDIUM"
        echo ""
        ((ISSUES++))
    fi
    
    # Check order issues
    if [ "$FAILED" -gt 0 ] || [ "$REJECTED" -gt 0 ]; then
        echo "⚠️  ISSUE 2: Order Failures Detected"
        echo "   Failed: $FAILED | Rejected: $REJECTED"
        echo "   Action: Check DEBUG_GUIDE_ORDERS.md for detailed analysis"
        echo "   Severity: HIGH"
        echo ""
        ((ISSUES++))
    fi
    
    # Check capital issues
    if [ "$FAILED" -gt "$CONFIRMED" ] 2>/dev/null; then
        echo "⚠️  ISSUE 3: Capital Management Problem"
        echo "   Action: Review capital availability and position sizing"
        echo "   Severity: HIGH"
        echo ""
        ((ISSUES++))
    fi
    
    # Check SL issues
    if [ "$SL_FAILED" -gt 0 ]; then
        echo "⚠️  ISSUE 4: SL Placement Failures"
        echo "   Failed SLs: $SL_FAILED"
        echo "   Action: Increase SL placement retry in monitor.py"
        echo "   Severity: MEDIUM"
        echo ""
        ((ISSUES++))
    fi
    
    # Check win rate
    if [ -f "$STATS_JSON" ]; then
        WIN_RATE=$(python3 -c "import json; d=json.load(open('$STATS_JSON')); t=sum(x.get('total_trades',0) for x in d.values()); w=sum(x.get('winning_trades',0) for x in d.values()); print(w/t if t>0 else 0)" 2>/dev/null)
        if (( $(echo "$WIN_RATE < 0.5" | bc -l) )); then
            echo "⚠️  ISSUE 5: Low Win Rate"
            echo "   Current: $WIN_RATE"
            echo "   Action: Review filters in hybrid_learning_engine.py"
            echo "   Severity: MEDIUM"
            echo ""
            ((ISSUES++))
        fi
    fi
    
    if [ "$ISSUES" -eq 0 ]; then
        echo "✓ No critical issues detected!"
        echo "System operating normally."
        echo ""
    else
        echo "Summary: $ISSUES issue(s) found"
        echo "See detailed debugging guides:"
        echo "  - DEBUG_GUIDE_RATE_LIMIT.md (for rate limit issues)"
        echo "  - DEBUG_GUIDE_ORDERS.md (for order issues)"
        echo "  - DEBUG_GUIDE_ML.md (for ML issues)"
        echo ""
    fi
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "END OF REPORT"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

} | tee "$REPORT_FILE"

echo ""
echo "✓ Report saved to: $REPORT_FILE"
