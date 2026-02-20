#!/usr/bin/env python3
"""
Check current LTP vs entry premium for today's option positions
"""
import sys
import os

# Change to options directory
os.chdir('/root/santhosh/trading/options')
sys.path.insert(0, '/root/santhosh/trading/options')

# Now import from optcode
from optcode.angelone_options import AngelOneOptionsBroker
import json

def main():
    # Get symbols and entry premiums
    symbols = []
    entry_premiums = {}
    underlyings = {}
    
    with open('logs/2026-01-29/alerts.jsonl') as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get('status') == 'position_created':
                    details = entry.get('details', {})
                    symbol = details.get('symbol')
                    premium = details.get('entry_premium')
                    underlying = entry.get('alert', {}).get('symbol')
                    if symbol and premium:
                        if symbol not in entry_premiums:  # Take first entry only
                            symbols.append(symbol)
                            entry_premiums[symbol] = float(premium)
                            underlyings[symbol] = underlying
            except:
                pass
    
    print(f'📊 Checking {len(symbols)} option contracts for LTP vs Entry Premium\n')
    
    # Initialize broker
    broker = AngelOneOptionsBroker()
    
    # Get LTPs in bulk
    ltps = broker.get_ltp_bulk(symbols, exchange='NFO')
    
    # Calculate changes
    results = []
    for symbol in symbols:
        ltp = ltps.get(symbol)
        if ltp is not None:
            entry = entry_premiums.get(symbol, 0)
            change = ltp - entry
            change_pct = (change / entry * 100) if entry > 0 else 0
            results.append((underlyings.get(symbol, ''), symbol, entry, ltp, change, change_pct))
    
    # Sort by change_pct
    results.sort(key=lambda x: x[5], reverse=True)
    
    # Print
    print(f"{'Underlying':<12} {'Symbol':<28} {'Entry':>8} {'LTP':>8} {'Change':>8} {'Change%':>10}")
    print('=' * 85)
    for underlying, symbol, entry, ltp, change, change_pct in results:
        emoji = '🟢' if change_pct > 0 else '🔴' if change_pct < 0 else '⚪'
        print(f'{emoji} {underlying:<10} {symbol:<28} {entry:>8.2f} {ltp:>8.2f} {change:>8.2f} {change_pct:>9.1f}%')
    
    # Summary
    print('\n' + '=' * 85)
    positive = sum(1 for r in results if r[5] > 0)
    negative = sum(1 for r in results if r[5] < 0)
    avg_change = sum(r[5] for r in results) / len(results) if results else 0
    total_entry = sum(r[2] for r in results)
    total_current = sum(r[3] for r in results)
    total_pnl = total_current - total_entry
    total_pnl_pct = (total_pnl / total_entry * 100) if total_entry > 0 else 0
    
    print(f'\n📈 Positive: {positive} ({positive/len(results)*100:.1f}%)')
    print(f'📉 Negative: {negative} ({negative/len(results)*100:.1f}%)')
    print(f'📊 Average Change: {avg_change:.2f}%')
    print(f'💰 Total P&L: ₹{total_pnl:.2f} ({total_pnl_pct:.2f}%)')
    print(f'💵 Per Premium P&L: Entry=₹{total_entry:.2f} Current=₹{total_current:.2f}')

if __name__ == '__main__':
    main()
