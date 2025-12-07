#!/usr/bin/env python3
"""
Performance Tracking Dashboard
Run daily to monitor trading performance and get actionable recommendations
"""

import json
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

# Add eqcode to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'eqcode'))

def load_trades(days=30):
    """Load last N days of trade data"""
    trades = []
    data_file = Path(__file__).parent / 'data' / 'ml_training_data.jsonl'
    
    if not data_file.exists():
        return []
    
    cutoff = datetime.now() - timedelta(days=days)
    
    try:
        with open(data_file, 'r') as f:
            for line in f:
                try:
                    trade = json.loads(line.strip())
                    # Parse exit time
                    trade_date = datetime.fromisoformat(trade['exit_time'].replace('Z', '+00:00'))
                    if trade_date > cutoff:
                        trades.append(trade)
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    continue
    except Exception as e:
        print(f"Error loading trades: {e}")
        return []
    
    return trades

def calculate_metrics(trades):
    """Calculate performance metrics"""
    if not trades:
        return None
    
    total = len(trades)
    wins = [t for t in trades if t['outcome'] == 'WIN']
    losses = [t for t in trades if t['outcome'] == 'LOSS']
    
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total) * 100 if total > 0 else 0
    
    avg_win = sum(t['exit_profit_pct'] for t in wins) / win_count if wins else 0
    avg_loss = sum(t['exit_profit_pct'] for t in losses) / loss_count if losses else 0
    
    total_pnl = sum(t['exit_profit_pct'] for t in trades)
    
    # Calculate profit factor
    total_wins_pnl = sum(t['exit_profit_pct'] for t in wins)
    total_losses_pnl = abs(sum(t['exit_profit_pct'] for t in losses))
    profit_factor = total_wins_pnl / total_losses_pnl if total_losses_pnl > 0 else float('inf')
    
    # Best and worst trades
    best_trade = max(trades, key=lambda t: t['exit_profit_pct'])
    worst_trade = min(trades, key=lambda t: t['exit_profit_pct'])
    
    # Analyze exit reasons
    exit_reasons = defaultdict(int)
    for trade in trades:
        exit_reasons[trade.get('exit_reason', 'UNKNOWN')] += 1
    
    # Analyze by stage
    stage_stats = defaultdict(lambda: {'count': 0, 'wins': 0})
    for trade in trades:
        stage = trade.get('stage', 'UNKNOWN')
        stage_stats[stage]['count'] += 1
        if trade['outcome'] == 'WIN':
            stage_stats[stage]['wins'] += 1
    
    return {
        'total_trades': total,
        'wins': win_count,
        'losses': loss_count,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'total_pnl': total_pnl,
        'profit_factor': profit_factor,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'exit_reasons': dict(exit_reasons),
        'stage_stats': dict(stage_stats),
    }

def print_dashboard(metrics):
    """Print formatted dashboard"""
    if not metrics:
        print("\n" + "="*70)
        print("❌ NO TRADE DATA YET")
        print("="*70)
        print("\n📊 Status: Bot is running but hasn't completed any trades yet")
        print("\n🎯 Next Steps:")
        print("   1. Wait for TradingView alerts to arrive")
        print("   2. Bot will execute trades and collect data")
        print("   3. After 20-30 trades, ML can start learning")
        print("   4. Run this script daily to track progress")
        print("\n💡 Tip: Check if bot is receiving alerts:")
        print("   grep 'ALERT_RECEIVED' /tmp/eqbot_final.log | tail -5")
        print("\n" + "="*70 + "\n")
        return
    
    print("\n" + "="*70)
    print("📊 PERFORMANCE DASHBOARD - Last 30 Days")
    print("="*70)
    
    print(f"\n📈 Trade Statistics:")
    print(f"   Total Trades: {metrics['total_trades']}")
    print(f"   Wins: {metrics['wins']} | Losses: {metrics['losses']}")
    print(f"   Win Rate: {metrics['win_rate']:.1f}%")
    
    # Win rate assessment
    if metrics['win_rate'] >= 55:
        verdict = "✅ EXCELLENT - Signals are working!"
    elif metrics['win_rate'] >= 50:
        verdict = "✅ GOOD - Profitable setup"
    elif metrics['win_rate'] >= 45:
        verdict = "⚠️  BREAK-EVEN - Needs tuning"
    else:
        verdict = "🚨 POOR - Signals need fixing!"
    print(f"   Verdict: {verdict}")
    
    print(f"\n💰 Profit Metrics:")
    print(f"   Avg Win: +{metrics['avg_win']:.2f}%")
    print(f"   Avg Loss: {metrics['avg_loss']:.2f}%")
    print(f"   Profit Factor: {metrics['profit_factor']:.2f}")
    print(f"   Total P&L: {metrics['total_pnl']:+.2f}%")
    
    # Profit factor assessment
    if metrics['profit_factor'] >= 2.0:
        pf_verdict = "✅ EXCELLENT - Great risk/reward!"
    elif metrics['profit_factor'] >= 1.5:
        pf_verdict = "✅ GOOD - Sustainable edge"
    elif metrics['profit_factor'] >= 1.0:
        pf_verdict = "⚠️  BREAK-EVEN - Needs improvement"
    else:
        pf_verdict = "🚨 LOSING - Avg loss too large!"
    print(f"   Verdict: {pf_verdict}")
    
    # Calculate capital impact (assuming ₹2000 per trade)
    capital_per_trade = 2000
    total_pnl_rupees = (metrics['total_pnl'] / 100) * (metrics['total_trades'] * capital_per_trade)
    print(f"   Est. Capital Impact: ₹{total_pnl_rupees:,.0f}")
    
    print(f"\n🏆 Best/Worst:")
    print(f"   Best: {metrics['best_trade']['symbol']} "
          f"{metrics['best_trade']['exit_profit_pct']:+.2f}% "
          f"({metrics['best_trade'].get('exit_reason', 'UNKNOWN')})")
    print(f"   Worst: {metrics['worst_trade']['symbol']} "
          f"{metrics['worst_trade']['exit_profit_pct']:+.2f}% "
          f"({metrics['worst_trade'].get('exit_reason', 'UNKNOWN')})")
    
    # Exit reasons breakdown
    print(f"\n🚪 Exit Reasons:")
    for reason, count in sorted(metrics['exit_reasons'].items(), key=lambda x: x[1], reverse=True):
        pct = (count / metrics['total_trades']) * 100
        print(f"   {reason}: {count} ({pct:.1f}%)")
    
    # Stage performance
    print(f"\n📊 Stage Performance:")
    for stage, stats in sorted(metrics['stage_stats'].items()):
        stage_wr = (stats['wins'] / stats['count']) * 100 if stats['count'] > 0 else 0
        print(f"   {stage}: {stats['count']} trades, {stage_wr:.1f}% win rate")
    
    print(f"\n🎯 Recommendations:")
    if metrics['total_trades'] < 20:
        print("   1. 📊 Need more data - keep collecting (target: 30+ trades)")
        print("   2. 🔍 ML filter can't train yet (needs 20+ samples)")
        print("   3. ⏳ Be patient - no tuning until sufficient data")
    elif metrics['win_rate'] < 45:
        print("   1. 🚨 URGENT: Increase ML threshold to 0.75+ (filter aggressively)")
        print("   2. 🔍 Review TradingView signal strategy - may be flawed")
        print("   3. 💰 Reduce position size to ₹1,000 (capital preservation)")
        print("   4. 📝 Consider changing signal source if trend continues")
    elif metrics['win_rate'] < 50:
        print("   1. ⚠️  Keep ML threshold at 0.60-0.65 (neutral)")
        print("   2. 🔧 Optimize profit locks (analyze exit reasons above)")
        print("   3. 📊 Collect more data (need 50+ trades for confidence)")
        print("   4. 🛑 Consider widening SL if many premature stops")
    elif metrics['profit_factor'] < 1.5:
        print("   1. 💰 Avg wins too small - adjust profit locks higher")
        print("   2. 🛑 Consider widening SL to -0.7% (fewer premature stops)")
        print("   3. 📈 Let winners run longer (review profit lock levels)")
    else:
        print("   1. ✅ System working well - scale up position size!")
        print("   2. 📈 Consider increasing base capital to ₹2,500-3,000")
        print("   3. 🎯 Can lower ML threshold to 0.55 (take more signals)")
        print("   4. 🚀 Continue current strategy - it's profitable!")
    
    print(f"\n📅 ML Training Status:")
    try:
        from ml_signal_filter import MLSignalFilter
        ml = MLSignalFilter()
        status = ml.get_training_status()
        
        print(f"   Model Exists: {'✅ Yes' if status['model_exists'] else '❌ No'}")
        print(f"   Training Samples: {status['training_samples']}")
        print(f"   Last Trained: {status['last_trained']}")
        
        if status['training_samples'] < 20:
            print(f"   Status: 🔴 Insufficient data (need {20 - status['training_samples']} more trades)")
        elif not status['model_exists']:
            print(f"   Status: ⚠️  Ready to train - run: python3 -c \"from eqcode.ml_signal_filter import MLSignalFilter; MLSignalFilter().train_model()\"")
        else:
            print(f"   Status: ✅ Model trained and active")
    except Exception as e:
        print(f"   Status: ❌ Error checking ML status: {e}")
    
    print("\n" + "="*70 + "\n")

def print_quick_stats():
    """Print quick stats for last 7 days"""
    trades_7d = load_trades(days=7)
    if not trades_7d:
        print("❌ No trades in last 7 days")
        return
    
    wins = sum(1 for t in trades_7d if t['outcome'] == 'WIN')
    losses = len(trades_7d) - wins
    win_rate = (wins / len(trades_7d)) * 100
    total_pnl = sum(t['exit_profit_pct'] for t in trades_7d)
    
    print(f"📊 Last 7 Days: {len(trades_7d)} trades | {wins}W-{losses}L | {win_rate:.1f}% WR | {total_pnl:+.2f}% P&L")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Trading Performance Tracker')
    parser.add_argument('--days', type=int, default=30, help='Number of days to analyze (default: 30)')
    parser.add_argument('--quick', action='store_true', help='Show quick stats only')
    
    args = parser.parse_args()
    
    if args.quick:
        print_quick_stats()
    else:
        trades = load_trades(days=args.days)
        metrics = calculate_metrics(trades)
        print_dashboard(metrics)
