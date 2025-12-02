#!/usr/bin/env python3
"""
Missed Opportunities Analyzer

Quick command-line tool to analyze missed trading opportunities.
Run this after market hours to see what alerts were missed and why.

Usage:
    python3 missed_opportunities.py                    # Today's analysis
    python3 missed_opportunities.py --date 2025-10-25  # Specific date (YYYY-MM-DD)
    python3 missed_opportunities.py --summary          # Quick summary only
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from eqcode.analytics.alert_tracker import get_alert_summary
from eqcode.config import BASE_DIR


def analyze_missed_opportunities(date_str=None, summary_only=False):
    """
    Analyze missed trading opportunities for a given date
    
    Args:
        date_str: Date in YYYY-MM-DD format (default: today)
        summary_only: If True, show only summary stats
    """
    if not date_str:
        date_str = datetime.now().strftime('%d-%m-%Y')
    
    print(f"\n🔍 MISSED OPPORTUNITIES ANALYSIS - {date_str}")
    print("=" * 60)
    
    # Get alert summary
    summary = get_alert_summary(date_str)
    
    if 'error' in summary:
        print(f"❌ Error: {summary['error']}")
        return
    
    # Overall statistics
    total = summary['total_alerts']
    executed = summary['executed_alerts'] 
    missed = summary['missed_alerts']
    execution_rate = summary['execution_rate']
    
    print(f"\n📊 OVERALL STATISTICS:")
    print(f"   Total Alerts Received: {total}")
    print(f"   ✅ Successfully Executed: {executed}")
    print(f"   ❌ Missed Opportunities: {missed}")
    print(f"   📈 Execution Rate: {execution_rate:.1f}%")
    
    if missed == 0:
        print(f"\n🎉 EXCELLENT! No missed opportunities today!")
        return
    
    # Missed opportunities details
    if not summary_only and summary['missed_opportunities']:
        print(f"\n❌ MISSED OPPORTUNITIES BREAKDOWN:")
        print("-" * 50)
        
        for i, missed_alert in enumerate(summary['missed_opportunities'], 1):
            symbol = missed_alert['symbol']
            action = missed_alert['action']
            price = missed_alert['price']
            reason = missed_alert['reason']
            time = missed_alert['timestamp'].split('T')[1][:8]  # Extract time
            
            print(f"{i:2d}. {time} | {symbol:12s} | {action:4s} @ ₹{price:8.2f} | {reason}")
    
    # Symbol-wise analysis
    if summary['symbols_analysis']:
        print(f"\n📈 SYMBOL-WISE ANALYSIS:")
        print("-" * 50)
        print(f"{'Symbol':<12s} | {'Total':<5s} | {'Exec':<4s} | {'Miss':<4s} | {'Rate':<6s}")
        print("-" * 50)
        
        for symbol, data in summary['symbols_analysis'].items():
            total_sym = data['total_alerts']
            exec_sym = data['executed']
            miss_sym = data['missed']
            rate_sym = data['execution_rate']
            
            print(f"{symbol:<12s} | {total_sym:<5d} | {exec_sym:<4d} | {miss_sym:<4d} | {rate_sym:<6.1f}%")
    
    # Recommendations
    print(f"\n💡 RECOMMENDATIONS:")
    
    if execution_rate < 50:
        print("   🚨 LOW EXECUTION RATE - Consider increasing capital or reducing position size")
    elif execution_rate < 80:
        print("   ⚠️  MODERATE EXECUTION RATE - Some opportunities being missed")
    else:
        print("   ✅ GOOD EXECUTION RATE - Most opportunities being captured")
    
    # Top missed opportunities
    high_value_missed = [m for m in summary['missed_opportunities'] if m['price'] > 1000]
    if high_value_missed:
        print(f"   📊 {len(high_value_missed)} high-value opportunities missed (>₹1000)")
    
    capital_issues = [m for m in summary['missed_opportunities'] if 'capital' in m['reason'].lower()]
    if capital_issues:
        print(f"   💰 {len(capital_issues)} alerts missed due to capital constraints")
    
    print(f"\n" + "=" * 60)


def main():
    """Main function with command-line interface"""
    parser = argparse.ArgumentParser(description='Analyze missed trading opportunities')
    parser.add_argument('--date', '-d', help='Date in DD-MM-YYYY format (default: today)')
    parser.add_argument('--summary', '-s', action='store_true', help='Show summary only')
    parser.add_argument('--json', '-j', action='store_true', help='Output as JSON')
    
    args = parser.parse_args()
    
    if args.json:
        # JSON output for programmatic use
        summary = get_alert_summary(args.date)
        print(json.dumps(summary, indent=2))
    else:
        # Human-readable analysis
        analyze_missed_opportunities(args.date, args.summary)


if __name__ == "__main__":
    main()