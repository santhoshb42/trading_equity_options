#!/usr/bin/env python3
import json, sys, os
sys.path.insert(0, '/root/santhosh/trading/options')
os.chdir('/root/santhosh/trading/options')

from optcode.angelone_options import AngelOneOptionsBroker
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

with open('data/option_pnl_history.json') as f:
    all_trades = json.load(f)

today_trades = [t for t in all_trades if '2026-02-20' in t.get('entry_time', '')]
symbols = list(set([t['symbol'] for t in today_trades]))

print('=' * 150)
print('FETCHING CURRENT LTP FROM BROKER (BULK)')
print('=' * 150)

broker = AngelOneOptionsBroker()
if broker.authenticate():
    print(f'✓ Authenticated with AngelOne')
    print(f'Fetching LTP for {len(symbols)} contracts in bulk...\n')
    
    ltps = broker.get_ltp_bulk(symbols, exchange='NFO')
    fetched = len([v for v in ltps.values() if v])
    print(f'✓ Got LTP for {fetched}/{len(symbols)} contracts\n')
    
    analysis = []
    for trade in today_trades:
        symbol = trade['symbol']
        ltp = ltps.get(symbol)
        if ltp:
            cur_pnl = (ltp - trade['entry_premium']) * trade['quantity']
            diff = cur_pnl - trade['pnl']
        else:
            cur_pnl = diff = None
        analysis.append({
            'Symbol': symbol, 'Entry': trade['entry_premium'], 'Exit': trade['exit_premium'],
            'LTP': ltp, 'Qty': trade['quantity'], 'Realized': trade['pnl'], 'Current': cur_pnl,
            'Diff': diff, 'Reason': trade['exit_reason']
        })
    
    df = pd.DataFrame(analysis)
    
    print('=' * 150)
    print('LIVE LTP vs EXIT PRICE ANALYSIS')
    print('=' * 150 + '\n')
    header = '#   Symbol                          Entry     Exit  Current LTP    Qty        Realized       Current          Diff'
    print(header)
    print('-' * 150)
    
    for idx, row in df.iterrows():
        if pd.notna(row['LTP']):
            line = f"{idx+1:3} {row['Symbol']:<30} {row['Entry']:8.2f} {row['Exit']:8.2f} {row['LTP']:11.2f} {row['Qty']:9,.0f} {row['Realized']:12,.0f} {row['Current']:12,.0f} {row['Diff']:12,.0f}"
            print(line)
    
    real_sum = df['Realized'].sum()
    cur_sum = df['Current'].sum()
    diff_sum = cur_sum - real_sum
    
    print('\n' + '=' * 150)
    print('SUMMARY:')
    print(f'  Realized PnL (at exit):   ₹{real_sum:>12,.0f}')
    print(f'  Current PnL (at LTP):     ₹{cur_sum:>12,.0f}')
    print(f'  Change:                   ₹{diff_sum:>12,.0f}')
    if diff_sum > 0:
        print(f'  Status: Contracts UP by ₹{diff_sum:,.0f}')
    else:
        print(f'  Status: Contracts DOWN by ₹{abs(diff_sum):,.0f}')
    print('=' * 150)
else:
    print('✗ Failed to authenticate with broker')
