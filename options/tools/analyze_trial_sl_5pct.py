#!/usr/bin/env python3
"""
Analyze if activating TRIAL_SL at 5% (instead of 10%) would improve results
"""
import json

# Load today's trades
with open('data/option_pnl_history.json', 'r') as f:
    trades = json.load(f)

# Filter Feb 3 trades
feb3_trades = [t for t in trades if t.get('closed_at', '').startswith('2026-02-03')]

print(f"📊 Analyzing TRIAL_SL at 5% vs 10% for Feb 3, 2026")
print(f"Total trades: {len(feb3_trades)}\n")
print("=" * 130)

# Categorize trades
trades_5_to_10 = []  # Peaked between 5-10% (would activate TRIAL_SL at 5%)
trades_above_10 = []  # Peaked above 10% (already activated at 10%)
trades_below_5 = []   # Never reached 5% (wouldn't activate even at 5%)

current_total_pnl = 0
potential_total_pnl_5pct = 0

for trade in feb3_trades:
    entry = trade['entry_premium']
    peak = trade['highest_premium']
    exit_prem = trade['exit_premium']
    qty = trade['quantity']
    actual_pnl = trade['pnl']
    peak_pct = ((peak - entry) / entry * 100) if entry > 0 else 0
    
    current_total_pnl += actual_pnl
    
    # Simulate 5% TRIAL_SL
    if peak_pct >= 5:
        # TRIAL_SL would activate at 5%
        trial_sl_activated_at = entry * 1.05
        
        # Trail from peak with 10% buffer (same as current)
        trailing_sl_price = peak * 0.90
        
        # Exit at trailing SL
        potential_exit = max(trailing_sl_price, trial_sl_activated_at)
        potential_pnl = (potential_exit - entry) * qty
        
        potential_total_pnl_5pct += potential_pnl
        
        if 5 <= peak_pct < 10:
            trades_5_to_10.append({
                'symbol': trade['symbol'],
                'actual_pnl': actual_pnl,
                'potential_pnl': potential_pnl,
                'difference': potential_pnl - actual_pnl,
                'peak_pct': peak_pct,
                'exit_reason': trade['exit_reason']
            })
        else:  # peak >= 10%
            trades_above_10.append({
                'symbol': trade['symbol'],
                'actual_pnl': actual_pnl,
                'potential_pnl': potential_pnl,
                'difference': potential_pnl - actual_pnl,
                'peak_pct': peak_pct,
                'exit_reason': trade['exit_reason']
            })
    else:
        # Below 5%, no TRIAL_SL even at 5% threshold
        potential_total_pnl_5pct += actual_pnl
        trades_below_5.append({
            'symbol': trade['symbol'],
            'actual_pnl': actual_pnl,
            'potential_pnl': actual_pnl,  # Same as actual
            'difference': 0,
            'peak_pct': peak_pct,
            'exit_reason': trade['exit_reason']
        })

# Print results
print(f"\n🔴 TRADES PEAKED 5-10% (Would benefit from 5% TRIAL_SL activation):")
print(f"{'Symbol':<30} {'Actual PnL':>12} {'If 5% SL':>12} {'Difference':>12} {'Peak':>8} {'Exit Reason':<40}")
print("=" * 130)

trades_5_to_10.sort(key=lambda x: x['difference'], reverse=True)
total_saved_5_10 = 0
for t in trades_5_to_10:
    emoji = '🟢' if t['difference'] > 0 else '🔴'
    print(f"{emoji} {t['symbol']:<28} {t['actual_pnl']:>11.2f} {t['potential_pnl']:>11.2f} {t['difference']:>11.2f} {t['peak_pct']:>7.1f}% {t['exit_reason']:<40}")
    total_saved_5_10 += t['difference']

print(f"\n📊 Subtotal (5-10% peak): Saved ₹{total_saved_5_10:,.2f}")

print(f"\n\n🟢 TRADES PEAKED >10% (TRIAL_SL already activated at 10%, checking if 5% would differ):")
print(f"{'Symbol':<30} {'Actual PnL':>12} {'If 5% SL':>12} {'Difference':>12} {'Peak':>8} {'Exit Reason':<40}")
print("=" * 130)

trades_above_10.sort(key=lambda x: x['difference'], reverse=True)
total_diff_above_10 = 0
for t in trades_above_10[:15]:  # Show top 15
    emoji = '🟢' if t['difference'] > 0 else '⚪' if abs(t['difference']) < 100 else '🔴'
    print(f"{emoji} {t['symbol']:<28} {t['actual_pnl']:>11.2f} {t['potential_pnl']:>11.2f} {t['difference']:>11.2f} {t['peak_pct']:>7.1f}% {t['exit_reason']:<40}")
    total_diff_above_10 += t['difference']

for t in trades_above_10[15:]:
    total_diff_above_10 += t['difference']

print(f"\n📊 Subtotal (>10% peak): Net change ₹{total_diff_above_10:,.2f}")

print(f"\n\n⚪ TRADES PEAKED <5% (No TRIAL_SL even at 5%):")
print(f"Count: {len(trades_below_5)} trades (no change with 5% threshold)")

# Summary
print("\n" + "=" * 130)
print(f"\n💰 FINAL SUMMARY:")
print(f"   Current total P&L (10% TRIAL_SL): ₹{current_total_pnl:,.2f}")
print(f"   Potential P&L (5% TRIAL_SL):      ₹{potential_total_pnl_5pct:,.2f}")
print(f"   Net Improvement:                   ₹{potential_total_pnl_5pct - current_total_pnl:,.2f}")
print(f"\n   Trades 5-10% peak: {len(trades_5_to_10)} (saved ₹{total_saved_5_10:,.2f})")
print(f"   Trades >10% peak:  {len(trades_above_10)} (change ₹{total_diff_above_10:,.2f})")
print(f"   Trades <5% peak:   {len(trades_below_5)} (no change)")

if potential_total_pnl_5pct > current_total_pnl:
    print(f"\n✅ RECOMMENDATION: Switch to 5% TRIAL_SL threshold (saves ₹{potential_total_pnl_5pct - current_total_pnl:,.2f})")
else:
    print(f"\n❌ RECOMMENDATION: Keep 10% TRIAL_SL threshold (switching would lose ₹{abs(potential_total_pnl_5pct - current_total_pnl):,.2f})")

print()
