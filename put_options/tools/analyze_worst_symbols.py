#!/usr/bin/env python3
"""
Analyze symbol performance from symbol_stats.json
Categorize worst performers into different failure patterns
"""
import json
from collections import defaultdict

# Load symbol stats
with open('/root/santhosh/trading/put_options/data/learning/symbol_stats.json', 'r') as f:
    stats = json.load(f)

print("📊 Symbol Performance Analysis\n")
print("=" * 100)

# Categories
always_losers = []  # 100% loss rate
frequent_losers = []  # >70% loss rate
no_trades = []  # 0 trades
negative_pnl = []  # Negative total P&L
low_volume = []  # <5 trades

for symbol, data in stats.items():
    total = data['total_trades']
    wins = data['wins']
    losses = data['losses']
    total_profit = data['total_profit']
    win_rate = data['win_rate']
    
    if total == 0:
        no_trades.append(symbol)
    elif total < 5:
        low_volume.append((symbol, total, total_profit, win_rate))
    elif losses > 0 and wins == 0:  # All losses
        always_losers.append((symbol, total, total_profit, win_rate))
    elif win_rate < 0.3:  # <30% win rate
        frequent_losers.append((symbol, total, total_profit, win_rate))
    elif total_profit < 0:  # Negative P&L
        negative_pnl.append((symbol, total, total_profit, win_rate))

# Sort by worst metrics
always_losers.sort(key=lambda x: (x[1], x[2]))  # Most trades, worst loss
frequent_losers.sort(key=lambda x: x[2])  # Worst total P&L
negative_pnl.sort(key=lambda x: x[2])  # Most negative
low_volume.sort(key=lambda x: x[2])  # Worst P&L

print(f"\n🔴 CATEGORY 1: ALWAYS LOSERS (0% Win Rate)")
print(f"Symbols that NEVER won a single trade")
print(f"{'Symbol':<20} {'Trades':>8} {'Total Loss':>15} {'Win Rate':>10}")
print("-" * 100)
for symbol, trades, pnl, win_rate in always_losers[:20]:
    print(f"{symbol:<20} {trades:>8} {pnl:>14,.2f} {win_rate*100:>9.1f}%")
print(f"\nTotal: {len(always_losers)} symbols")

print(f"\n\n🔴 CATEGORY 2: FREQUENT LOSERS (<30% Win Rate)")
print(f"Symbols with very poor win rates")
print(f"{'Symbol':<20} {'Trades':>8} {'Total P&L':>15} {'Win Rate':>10}")
print("-" * 100)
for symbol, trades, pnl, win_rate in frequent_losers[:20]:
    print(f"{symbol:<20} {trades:>8} {pnl:>14,.2f} {win_rate*100:>9.1f}%")
print(f"\nTotal: {len(frequent_losers)} symbols")

print(f"\n\n🔴 CATEGORY 3: NEGATIVE P&L (Despite Some Wins)")
print(f"Symbols that lose money overall even with wins")
print(f"{'Symbol':<20} {'Trades':>8} {'Total P&L':>15} {'Win Rate':>10}")
print("-" * 100)
for symbol, trades, pnl, win_rate in negative_pnl[:20]:
    print(f"{symbol:<20} {trades:>8} {pnl:>14,.2f} {win_rate*100:>9.1f}%")
print(f"\nTotal: {len(negative_pnl)} symbols")

print(f"\n\n⚪ CATEGORY 4: LOW VOLUME (<5 trades)")
print(f"Symbols with too few trades to judge")
print(f"{'Symbol':<20} {'Trades':>8} {'Total P&L':>15} {'Win Rate':>10}")
print("-" * 100)
for symbol, trades, pnl, win_rate in low_volume[:20]:
    print(f"{symbol:<20} {trades:>8} {pnl:>14,.2f} {win_rate*100:>9.1f}%")
print(f"\nTotal: {len(low_volume)} symbols")

print(f"\n\n⚪ CATEGORY 5: NO TRADES (Never traded)")
print(f"Total: {len(no_trades)} symbols")

# Overall statistics
total_symbols = len(stats)
traded_symbols = total_symbols - len(no_trades)
print(f"\n\n" + "=" * 100)
print(f"\n📈 SUMMARY:")
print(f"   Total symbols tracked: {total_symbols}")
print(f"   Traded symbols: {traded_symbols}")
print(f"   Never traded: {len(no_trades)} ({len(no_trades)/total_symbols*100:.1f}%)")
print(f"   Always losers (0% win): {len(always_losers)} ({len(always_losers)/traded_symbols*100:.1f}%)")
print(f"   Frequent losers (<30% win): {len(frequent_losers)} ({len(frequent_losers)/traded_symbols*100:.1f}%)")
print(f"   Negative P&L: {len(negative_pnl)} ({len(negative_pnl)/traded_symbols*100:.1f}%)")
print(f"   Low volume (<5 trades): {len(low_volume)} ({len(low_volume)/traded_symbols*100:.1f}%)")

# Calculate total loss from worst performers
total_loss_always = sum(x[2] for x in always_losers)
total_loss_frequent = sum(x[2] for x in frequent_losers)
total_loss_negative = sum(x[2] for x in negative_pnl)

print(f"\n💸 COST OF WORST PERFORMERS:")
print(f"   Always losers cost: ₹{total_loss_always:,.2f}")
print(f"   Frequent losers cost: ₹{total_loss_frequent:,.2f}")
print(f"   Negative P&L cost: ₹{total_loss_negative:,.2f}")

# Top worst performers by total loss
all_bad = always_losers + frequent_losers + negative_pnl
all_bad_unique = {}
for symbol, trades, pnl, win_rate in all_bad:
    if symbol not in all_bad_unique or pnl < all_bad_unique[symbol][2]:
        all_bad_unique[symbol] = (symbol, trades, pnl, win_rate)

worst_20 = sorted(all_bad_unique.values(), key=lambda x: x[2])[:20]

print(f"\n\n🔥 TOP 20 WORST PERFORMERS (Overall):")
print(f"{'Symbol':<20} {'Trades':>8} {'Total Loss':>15} {'Win Rate':>10}")
print("-" * 100)
for symbol, trades, pnl, win_rate in worst_20:
    print(f"{symbol:<20} {trades:>8} {pnl:>14,.2f} {win_rate*100:>9.1f}%")

print("\n✅ Done!\n")
