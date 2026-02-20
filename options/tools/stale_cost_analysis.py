#!/usr/bin/env python3
"""
Analyze STALE_CONSOLIDATION impact for Feb 1, 2026 from option_pnl_history.json
"""
import sys
import os
import json

os.chdir('/root/santhosh/trading/options')
sys.path.insert(0, '/root/santhosh/trading/options')

from optcode.angelone_options import AngelOneOptionsBroker

def main():
    print('📊 Analyzing STALE_CONSOLIDATION Impact from option_pnl_history.json\n')
    print('=' * 120)
    
    # Load option_pnl_history.json
    with open('data/option_pnl_history.json', 'r') as f:
        trades = json.load(f)
    
    # Filter for Feb 1 STALE_CONSOLIDATION exits
    stale_trades = [
        t for t in trades 
        if t.get('exit_reason', '').startswith('STALE_CONSOLIDATION')
        and t.get('closed_at', '').startswith('2026-02-01')
    ]
    
    print(f'\n🔍 Found {len(stale_trades)} STALE_CONSOLIDATION exits on 2026-02-01\n')
    
    if not stale_trades:
        print('❌ No STALE exits found')
        return
    
    # Get symbols and fetch current LTPs
    symbols = [t['symbol'] for t in stale_trades]
    
    print('📡 Fetching current LTPs from broker...')
    broker = AngelOneOptionsBroker()
    ltps = broker.get_ltp_bulk(symbols, exchange='NFO')
    print(f'✅ Got LTPs for {len(ltps)}/{len(symbols)} symbols\n')
    
    # Calculate impact
    results = []
    total_actual_pnl = 0
    total_potential_pnl = 0
    total_opportunity_cost = 0
    
    for trade in stale_trades:
        symbol = trade['symbol']
        entry_premium = trade['entry_premium']
        exit_premium = trade['exit_premium']
        quantity = trade['quantity']
        actual_pnl = trade['pnl']
        closed_at = trade['closed_at']
        
        ltp = ltps.get(symbol)
        
        if ltp is not None:
            # What would PnL be if we held until current LTP?
            potential_pnl = (ltp - entry_premium) * quantity
            
            # Opportunity cost = potential - actual
            # Negative = we lost money by exiting early (bad decision)
            # Positive = we saved money by exiting early (good decision)
            opportunity_cost = potential_pnl - actual_pnl
            
            results.append({
                'time': closed_at.split('T')[1][:5],
                'symbol': symbol,
                'entry_premium': entry_premium,
                'exit_premium': exit_premium,
                'current_ltp': ltp,
                'quantity': quantity,
                'actual_pnl': actual_pnl,
                'potential_pnl': potential_pnl,
                'opportunity_cost': opportunity_cost
            })
            
            total_actual_pnl += actual_pnl
            total_potential_pnl += potential_pnl
            total_opportunity_cost += opportunity_cost
    
    # Sort by opportunity cost (most negative first = biggest losses)
    results.sort(key=lambda x: x['opportunity_cost'])
    
    # Print results
    print(f"{'Time':<8} {'Symbol':<28} {'Entry₹':>9} {'Exit₹':>9} {'LTP₹':>9} {'Qty':>4} {'Act.PnL':>11} {'Pot.PnL':>11} {'Impact':>11}")
    print('=' * 120)
    
    for r in results:
        emoji = '🟢' if r['opportunity_cost'] > 100 else '⚪' if r['opportunity_cost'] > -100 else '🔴'
        print(f"{emoji} {r['time']:<6} {r['symbol']:<28} {r['entry_premium']:>9.2f} {r['exit_premium']:>9.2f} {r['current_ltp']:>9.2f} "
              f"{r['quantity']:>4} {r['actual_pnl']:>10.2f} {r['potential_pnl']:>10.2f} {r['opportunity_cost']:>10.2f}")
    
    # Summary
    print('\n' + '=' * 120)
    print('\n💰 FINANCIAL IMPACT SUMMARY:')
    print(f'   Total STALE exits: {len(results)}')
    print(f'   Actual P&L realized: ₹{total_actual_pnl:,.2f}')
    print(f'   Potential P&L if held: ₹{total_potential_pnl:,.2f}')
    print(f'   💸 Net Opportunity Cost: ₹{total_opportunity_cost:,.2f}')
    
    # Analysis
    saved = sum(1 for r in results if r['opportunity_cost'] > 100)
    neutral = sum(1 for r in results if -100 <= r['opportunity_cost'] <= 100)
    lost = sum(1 for r in results if r['opportunity_cost'] < -100)
    
    print(f'\n📊 DECISION QUALITY:')
    print(f'   🟢 Good exits (saved ₹100+): {saved} ({saved/len(results)*100:.1f}%)')
    print(f'   ⚪ Neutral (±₹100): {neutral} ({neutral/len(results)*100:.1f}%)')
    print(f'   🔴 Bad exits (lost ₹100+): {lost} ({lost/len(results)*100:.1f}%)')
    
    # Top winners and losers
    print('\n🔴 TOP 10 WORST EXITS (Should have held):')
    worst = [r for r in results if r['opportunity_cost'] < 0][:10]
    for i, r in enumerate(worst, 1):
        change = ((r['current_ltp'] - r['exit_premium']) / r['exit_premium'] * 100) if r['exit_premium'] > 0 else 0
        print(f"   {i:2}. {r['symbol']:<28} Lost: ₹{abs(r['opportunity_cost']):>8,.2f}  "
              f"(Exit@₹{r['exit_premium']:.2f} → Now@₹{r['current_ltp']:.2f}, {change:+.1f}%)")
    
    print('\n🟢 TOP 5 BEST EXITS (Saved money):')
    best = [r for r in results if r['opportunity_cost'] > 0][-5:][::-1]
    for i, r in enumerate(best, 1):
        change = ((r['current_ltp'] - r['exit_premium']) / r['exit_premium'] * 100) if r['exit_premium'] > 0 else 0
        print(f"   {i}. {r['symbol']:<28} Saved: ₹{r['opportunity_cost']:>8,.2f}  "
              f"(Exit@₹{r['exit_premium']:.2f} → Now@₹{r['current_ltp']:.2f}, {change:+.1f}%)")
    
    print('\n✅ Done!\n')

if __name__ == '__main__':
    main()
