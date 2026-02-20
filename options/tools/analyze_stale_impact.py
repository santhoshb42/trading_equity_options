#!/usr/bin/env python3
"""
Analyze the impact of STALE_CONSOLIDATION bug by comparing exit prices with current LTPs
"""
import sys
import os
import re
from datetime import datetime

# Change to options directory
os.chdir('/root/santhosh/trading/options')
sys.path.insert(0, '/root/santhosh/trading/options')

from optcode.angelone_options import AngelOneOptionsBroker

def parse_stale_exits(log_file, alerts_file):
    """Parse STALE exits from optbot.log and get entry/exit premiums from alerts.jsonl"""
    import json
    
    # First get all position data from alerts
    positions = {}
    with open(alerts_file, 'r') as f:
        for line in f:
            try:
                alert = json.loads(line)
                if alert.get('status') == 'position_created':
                    details = alert.get('details', {})
                    symbol = details.get('symbol')
                    if symbol:
                        positions[symbol] = {
                            'entry_premium': float(details.get('entry_premium', 0)),
                            'quantity': int(details.get('quantity', 30))
                        }
                elif alert.get('status') == 'position_closed':
                    details = alert.get('details', {})
                    symbol = details.get('symbol')
                    exit_reason = details.get('exit_reason', '')
                    if symbol and 'STALE' in exit_reason:
                        if symbol in positions:
                            positions[symbol]['exit_premium'] = float(details.get('exit_premium', 0))
                            positions[symbol]['pnl'] = float(details.get('pnl', 0))
                            positions[symbol]['exit_reason'] = exit_reason
            except:
                pass
    
    # Now parse from optbot.log for timestamps
    stale_exits = []
    pattern = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*STALE_CONSOL_EXIT \| ([A-Z0-9]+) \| Duration: (\d+)s \| PnL=₹([-\d.]+)'
    
    with open(log_file, 'r') as f:
        for line in f:
            match = re.search(pattern, line)
            if match:
                timestamp = match.group(1)
                symbol = match.group(2)
                duration = int(match.group(3))
                pnl = float(match.group(4))
                
                if symbol in positions and 'exit_premium' in positions[symbol]:
                    stale_exits.append({
                        'timestamp': timestamp,
                        'symbol': symbol,
                        'duration': duration,
                        'pnl': pnl,
                        'entry_premium': positions[symbol]['entry_premium'],
                        'exit_premium': positions[symbol]['exit_premium'],
                        'quantity': positions[symbol]['quantity']
                    })
    
    return stale_exits

def get_exit_premium_from_pnl(pnl, entry_premium, quantity):
    """Calculate exit premium from PnL"""
    # PnL = (exit_premium - entry_premium) * quantity
    # exit_premium = (PnL / quantity) + entry_premium
    return (pnl / quantity) + entry_premium

def main():
    log_file = 'logs/2026-02-01/optbot.log'
    alerts_file = 'logs/2026-02-01/alerts.jsonl'
    
    print('📊 Analyzing STALE_CONSOLIDATION Bug Impact\n')
    print('=' * 100)
    
    # Parse STALE exits
    stale_exits = parse_stale_exits(log_file, alerts_file)
    print(f'\n🔍 Found {len(stale_exits)} STALE exits\n')
    
    if not stale_exits:
        print('❌ No STALE exits found')
        return
    
    # Get symbols
    symbols = [entry['symbol'] for entry in stale_exits]
    
    # Initialize broker and get current LTPs
    print('📡 Fetching current LTPs from broker...')
    broker = AngelOneOptionsBroker()
    ltps = broker.get_ltp_bulk(symbols, exchange='NFO')
    print(f'✅ Got LTPs for {len(ltps)}/{len(symbols)} symbols\n')
    
    # Calculate impact
    results = []
    total_actual_pnl = 0
    total_potential_pnl = 0
    total_lost_opportunity = 0
    
    for entry in stale_exits:
        symbol = entry['symbol']
        actual_pnl = entry['pnl']
        entry_premium = entry['entry_premium']
        exit_premium = entry['exit_premium']
        quantity = entry['quantity']
        ltp = ltps.get(symbol)
        
        if ltp is not None:
            # What would PnL be if we held until current LTP?
            potential_pnl = (ltp - entry_premium) * quantity
            
            # Lost opportunity = potential - actual
            # If negative, we lost money by exiting early (should have held)
            # If positive, exiting early saved us (good decision)
            lost_opportunity = potential_pnl - actual_pnl
            
            results.append({
                'timestamp': entry['timestamp'],
                'symbol': symbol,
                'duration': entry['duration'],
                'entry_premium': entry_premium,
                'exit_premium': exit_premium,
                'current_ltp': ltp,
                'actual_pnl': actual_pnl,
                'potential_pnl': potential_pnl,
                'lost_opportunity': lost_opportunity
            })
            
            total_actual_pnl += actual_pnl
            total_potential_pnl += potential_pnl
            total_lost_opportunity += lost_opportunity
    
    # Sort by lost opportunity
    results.sort(key=lambda x: x['lost_opportunity'], reverse=True)
    
    # Print results
    print(f"{'Time':<8} {'Symbol':<28} {'Dur':>5} {'Entry₹':>8} {'Exit₹':>8} {'LTP₹':>8} {'Act.PnL':>10} {'Pot.PnL':>10} {'Lost':>10}")
    print('=' * 110)
    
    for r in results:
        time = r['timestamp'].split()[1][:5]
        emoji = '🟢' if r['lost_opportunity'] > 500 else '🟡' if r['lost_opportunity'] > 0 else '🔴'
        print(f"{emoji} {time:<6} {r['symbol']:<28} {r['duration']:>5}s {r['entry_premium']:>8.2f} {r['exit_premium']:>8.2f} {r['current_ltp']:>8.2f} "
              f"{r['actual_pnl']:>9.2f} {r['potential_pnl']:>9.2f} {r['lost_opportunity']:>9.2f}")
    
    # Summary
    print('\n' + '=' * 110)
    print('\n📈 IMPACT SUMMARY:')
    print(f'   Total STALE exits analyzed: {len(results)}')
    print(f'   Actual P&L from STALE exits: ₹{total_actual_pnl:,.2f}')
    print(f'   Potential P&L if held to now: ₹{total_potential_pnl:,.2f}')
    print(f'   💸 Total Lost Opportunity: ₹{total_lost_opportunity:,.2f}')
    
    # Winners vs Losers
    good_exits = sum(1 for r in results if r['lost_opportunity'] > 0)
    bad_exits = sum(1 for r in results if r['lost_opportunity'] < 0)
    print(f'\n   🟢 Good exits (saved money): {good_exits} ({good_exits/len(results)*100:.1f}%)')
    print(f'   🔴 Bad exits (lost opportunity): {bad_exits} ({bad_exits/len(results)*100:.1f}%)')
    
    # Biggest mistakes
    print('\n🔴 TOP 5 BIGGEST LOSSES (Should have held):')
    top_mistakes = [r for r in results if r['lost_opportunity'] < 0][:5]
    for i, r in enumerate(top_mistakes, 1):
        print(f"   {i}. {r['symbol']:<28} Lost: ₹{abs(r['lost_opportunity']):,.2f} (Exit@{r['exit_premium']:.2f} → LTP@{r['current_ltp']:.2f})")
    
    print('\n✅ Done!\n')

if __name__ == '__main__':
    main()
