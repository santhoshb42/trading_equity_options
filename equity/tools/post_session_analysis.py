#!/usr/bin/env python3
"""
Post-Session Analytics Runner

Run this after trading session ends to:
1. Parse all trading logs for the day
2. Extract completed trades
3. Generate comprehensive analytics
4. Provide recommendations for next session

Usage:
    python3 post_session_analysis.py                    # Today's analysis
    python3 post_session_analysis.py 25-10-2025        # Specific date
    python3 post_session_analysis.py --backfill         # Backfill missing dates
"""

import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

# Add the eqcode directory to Python path
sys.path.append(str(Path(__file__).parent / "eqcode"))

from eqcode.analytics.log_parser import LogParser, run_post_session_analysis
from eqcode.analytics.target_analytics import TargetAnalytics
from eqcode.bot_logging import log_event


def format_currency(amount: float) -> str:
    """Format amount as Indian currency"""
    return f"₹{amount:,.2f}"


def format_percentage(percent: float) -> str:
    """Format percentage with sign"""
    return f"{percent:+.2f}%"


def print_analysis_results(analysis: dict):
    """Print formatted analysis results"""
    print("\n" + "="*60)
    print("🎯 POST-SESSION ANALYTICS REPORT")
    print("="*60)
    
    if not analysis.get('session_complete', False):
        print("❌ No completed trades found for analysis")
        if 'log_parsing' in analysis:
            parse_info = analysis['log_parsing']
            print(f"📊 Log parsing results:")
            print(f"   • Date: {parse_info.get('date', 'Unknown')}")
            print(f"   • Trades parsed: {parse_info.get('parsed_trades', 0)}")
            if parse_info.get('errors'):
                print(f"   • Errors: {len(parse_info['errors'])}")
                for error in parse_info['errors'][:3]:  # Show first 3 errors
                    print(f"     - {error}")
        return
    
    # Parse results
    parse_info = analysis['log_parsing']
    performance = analysis['daily_performance']
    recommendations = analysis['recommendations']
    
    print(f"📅 Date: {performance['date']}")
    print(f"📈 Trades processed: {parse_info['parsed_trades']}")
    
    # Target Performance
    print("\n💰 TARGET PERFORMANCE")
    print("-" * 30)
    target_data = performance['target']
    perf_data = performance['performance']
    
    print(f"Daily Target:     {format_currency(target_data['daily_target_amount'])}")
    print(f"Achieved:         {format_currency(perf_data['achieved_amount'])}")
    print(f"Achievement:      {perf_data['achievement_percent']:.1f}%")
    print(f"Remaining:        {format_currency(target_data['daily_target_amount'] - perf_data['achieved_amount'])}")
    
    # Trade Statistics
    print("\n📊 TRADE STATISTICS")
    print("-" * 30)
    print(f"Total Trades:     {perf_data['trades_completed']}")
    print(f"Profitable:       {perf_data['trades_profitable']}")
    print(f"Win Rate:         {(perf_data['trades_profitable'] / max(perf_data['trades_completed'], 1)) * 100:.1f}%")
    print(f"Avg Profit/Trade: {format_percentage(perf_data['avg_profit_per_trade'])}")
    print(f"Margin Efficiency: {perf_data['margin_efficiency']:.1f}%")
    
    # Portfolio Usage
    portfolio = performance['portfolio']
    print("\n🎯 PORTFOLIO UTILIZATION")
    print("-" * 30)
    print(f"Positions Used:   {portfolio['active_positions']}/{target_data['max_positions']}")
    print(f"Utilization:      {portfolio['utilization_percent']:.1f}%")
    print(f"Available Slots:  {portfolio['available_slots']}")
    
    # Top Symbols
    if performance['top_symbols']:
        print("\n⭐ TOP PERFORMING SYMBOLS")
        print("-" * 30)
        for i, symbol in enumerate(performance['top_symbols'][:3], 1):
            print(f"{i}. {symbol['symbol']}")
            print(f"   Success Rate: {symbol['target_achievement_rate']:.1f}%")
            print(f"   Avg Profit:   {format_percentage(symbol['avg_profit_percent'])}")
            print(f"   Total Trades: {symbol['total_trades']}")
    
    # Recent Trades
    if performance['recent_trades']:
        print("\n📈 RECENT TRADES")
        print("-" * 30)
        for trade in performance['recent_trades'][:5]:
            status = "✅" if trade['target_achieved'] else "❌"
            print(f"{status} {trade['symbol']}: {format_percentage(trade['profit_percent'])} "
                  f"({trade['hold_duration_minutes']}min)")
    
    # Recommendations
    rec_data = recommendations['trade_recommendations']
    print("\n💡 RECOMMENDATIONS FOR NEXT SESSION")
    print("-" * 30)
    
    if rec_data.get('recommended_symbols'):
        print("🎯 Focus on these symbols:")
        for symbol in rec_data['recommended_symbols'][:3]:
            print(f"   • {symbol['symbol']}: {symbol['success_rate']} success, {symbol['avg_profit']} avg profit")
    
    if recommendations.get('optimization_suggestions'):
        print("\n🔧 Strategy improvements:")
        for suggestion in recommendations['optimization_suggestions']:
            print(f"   • {suggestion}")
    
    # Summary Status
    target_status = recommendations['target_status']
    print("\n🎯 SESSION SUMMARY")
    print("-" * 30)
    
    if target_status['on_track']:
        print("✅ EXCELLENT: Target achieved or on track!")
    elif target_status['progress_percent'] >= 50:
        print("⚠️  GOOD: Halfway to target, keep pushing!")
    else:
        print("❌ NEEDS IMPROVEMENT: Below 50% of target")
    
    print(f"Final Score: {target_status['progress_percent']:.1f}% of daily target")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Post-session analytics runner')
    parser.add_argument('date', nargs='?', help='Date in DD-MM-YYYY format (default: today)')
    parser.add_argument('--backfill', action='store_true', help='Backfill missing analytics data')
    parser.add_argument('--json', action='store_true', help='Output raw JSON instead of formatted report')
    parser.add_argument('--quiet', action='store_true', help='Suppress log output')
    
    args = parser.parse_args()
    
    if not args.quiet:
        print("🚀 Starting post-session analytics...")
    
    try:
        if args.backfill:
            # Backfill missing analytics data
            log_parser = LogParser()
            missing_dates = log_parser.get_missing_analytics_dates()
            
            if not missing_dates:
                print("✅ All log dates already have analytics data")
                return
            
            print(f"📅 Found {len(missing_dates)} dates missing analytics data")
            
            if not args.quiet:
                print("Missing dates:", ", ".join(missing_dates[:5]))
                if len(missing_dates) > 5:
                    print(f"... and {len(missing_dates) - 5} more")
            
            # Confirm backfill
            if not args.quiet:
                confirm = input("Proceed with backfill? (y/n): ")
                if confirm.lower() != 'y':
                    print("Backfill cancelled")
                    return
            
            # Run backfill
            results = log_parser.backfill_analytics(missing_dates[0], missing_dates[-1])
            
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                print(f"✅ Backfilled {results['total_trades']} trades across {results['dates_processed']} dates")
                if results['errors']:
                    print(f"⚠️  {len(results['errors'])} errors occurred:")
                    for error in results['errors'][:3]:
                        print(f"   • {error}")
        
        else:
            # Run post-session analysis
            analysis = run_post_session_analysis(args.date)
            
            if args.json:
                print(json.dumps(analysis, indent=2))
            else:
                print_analysis_results(analysis)
    
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        if not args.quiet:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()