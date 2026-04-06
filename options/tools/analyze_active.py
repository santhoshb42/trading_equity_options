import json

with open('/root/santhosh/trading/options/data/learning/symbol_stats.json') as f:
    data = json.load(f)

ACTIVE_EXITS = {'TRIAL_SL_HIT', 'HARD_SL_HIT', 'STALE_CONSOLIDATION'}

symbol_summary = []
all_trades = []

for sym, stats in data.items():
    if not isinstance(stats, dict):
        continue

    wins = losses = 0
    total_pnl = 0

    for trade in stats.get('trade_history', []):
        if not isinstance(trade, dict):
            continue
        pnl = trade.get('profit', trade.get('pnl', None))
        if pnl is None:
            continue
        reason = (trade.get('exit_reason', '') or '').split('(')[0].strip()
        if reason not in ACTIVE_EXITS:
            continue
        pnl = float(pnl)
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        all_trades.append({
            'symbol': sym,
            'pnl': round(pnl, 2),
            'exit_reason': reason,
        })

    total = wins + losses
    if total == 0:
        continue

    symbol_summary.append({
        'symbol': sym,
        'total_trades': total,
        'wins': wins,
        'losses': losses,
        'total_profit': round(total_pnl, 2),
        'avg_profit': round(total_pnl / total, 2),
        'win_rate': round(100 * wins / total, 1),
    })

total_wins   = sum(s['wins'] for s in symbol_summary)
total_losses = sum(s['losses'] for s in symbol_summary)
total_all    = total_wins + total_losses
total_pnl    = sum(s['total_profit'] for s in symbol_summary)

print(f"  Active exits : TRIAL_SL_HIT | HARD_SL_HIT | STALE_CONSOLIDATION")
print(f"  Symbols      : {len(symbol_summary)}")
print(f"  Trades       : {total_all}  ({total_wins} W / {total_losses} L)")
wr = 100*total_wins/total_all if total_all else 0
print(f"  Win Rate     : {wr:.1f}%")
print(f"  Total P&L    : Rs {total_pnl:+,.0f}")
print()

H = "=" * 68

# Per exit reason
print(H)
print("  P&L BY EXIT REASON")
print(H)
reason_pnl = {}; reason_cnt = {}; reason_wins = {}
for t in all_trades:
    r = t['exit_reason']
    reason_pnl[r]  = reason_pnl.get(r, 0)  + t['pnl']
    reason_cnt[r]  = reason_cnt.get(r, 0)  + 1
    reason_wins[r] = reason_wins.get(r, 0) + (1 if t['pnl'] > 0 else 0)
print(f"{'Exit Reason':<25} {'Count':>6} {'Win%':>6} {'Total P&L':>14} {'Avg P&L':>10}")
print("-" * 68)
for r in ['TRIAL_SL_HIT', 'HARD_SL_HIT', 'STALE_CONSOLIDATION']:
    cnt  = reason_cnt.get(r, 0)
    wins = reason_wins.get(r, 0)
    pnl  = reason_pnl.get(r, 0)
    wr2  = 100*wins/cnt if cnt else 0
    avg  = pnl/cnt if cnt else 0
    ok   = "OK" if avg > 0 else "!!"
    print(f"[{ok}] {r:<23} {cnt:>6} {wr2:>5.0f}% {pnl:>+14,.0f} {avg:>+10,.0f}")

print()
print(H)
print("  TOP 10 BEST TOTAL PROFIT (active exits only)")
print(H)
print(f"{'Symbol':<14} {'Trades':>6} {'W':>4} {'L':>4} {'Win%':>6} {'Total P&L':>12} {'Avg':>8}")
print("-" * 60)
for s in sorted(symbol_summary, key=lambda x: x['total_profit'], reverse=True)[:10]:
    print(f"{s['symbol']:<14} {s['total_trades']:>6} {s['wins']:>4} {s['losses']:>4} "
          f"{s['win_rate']:>5.0f}% {s['total_profit']:>+12,.0f} {s['avg_profit']:>+8,.0f}")

print()
print(H)
print("  BOTTOM 10 WORST TOTAL PROFIT (active exits only)")
print(H)
print(f"{'Symbol':<14} {'Trades':>6} {'W':>4} {'L':>4} {'Win%':>6} {'Total P&L':>12} {'Avg':>8}")
print("-" * 60)
for s in sorted(symbol_summary, key=lambda x: x['total_profit'])[:10]:
    print(f"{s['symbol']:<14} {s['total_trades']:>6} {s['wins']:>4} {s['losses']:>4} "
          f"{s['win_rate']:>5.0f}% {s['total_profit']:>+12,.0f} {s['avg_profit']:>+8,.0f}")

print()
print(H)
print("  BEST WIN RATE (min 5 trades)")
print(H)
print(f"{'Symbol':<14} {'Trades':>6} {'W':>4} {'L':>4} {'Win%':>6} {'Total P&L':>12} {'Avg':>8}")
print("-" * 60)
for s in sorted([x for x in symbol_summary if x['total_trades'] >= 5],
                key=lambda x: x['win_rate'], reverse=True)[:10]:
    print(f"{s['symbol']:<14} {s['total_trades']:>6} {s['wins']:>4} {s['losses']:>4} "
          f"{s['win_rate']:>5.0f}% {s['total_profit']:>+12,.0f} {s['avg_profit']:>+8,.0f}")

print()
print(H)
print("  WORST WIN RATE (min 5 trades)")
print(H)
print(f"{'Symbol':<14} {'Trades':>6} {'W':>4} {'L':>4} {'Win%':>6} {'Total P&L':>12} {'Avg':>8}")
print("-" * 60)
for s in sorted([x for x in symbol_summary if x['total_trades'] >= 5],
                key=lambda x: x['win_rate'])[:10]:
    print(f"{s['symbol']:<14} {s['total_trades']:>6} {s['wins']:>4} {s['losses']:>4} "
          f"{s['win_rate']:>5.0f}% {s['total_profit']:>+12,.0f} {s['avg_profit']:>+8,.0f}")

# STALE breakdown
print()
print(H)
print("  STALE_CONSOLIDATION — worst symbols (net loss from stale exits)")
print(H)
stale_by = {}
for t in all_trades:
    if t['exit_reason'] != 'STALE_CONSOLIDATION':
        continue
    sym = t['symbol']
    stale_by.setdefault(sym, {'n': 0, 'pnl': 0})
    stale_by[sym]['n']   += 1
    stale_by[sym]['pnl'] += t['pnl']
print(f"{'Symbol':<14} {'Count':>6} {'Total P&L':>12} {'Avg':>8}")
print("-" * 44)
for sym, d in sorted(stale_by.items(), key=lambda x: x[1]['pnl'])[:12]:
    avg = d['pnl']/d['n']
    print(f"{sym:<14} {d['n']:>6} {d['pnl']:>+12,.0f} {avg:>+8,.0f}")

# HARD_SL breakdown
print()
print(H)
print("  HARD_SL_HIT — worst symbols (most hard-stopped loss)")
print(H)
hard_by = {}
for t in all_trades:
    if t['exit_reason'] != 'HARD_SL_HIT':
        continue
    sym = t['symbol']
    hard_by.setdefault(sym, {'n': 0, 'pnl': 0})
    hard_by[sym]['n']   += 1
    hard_by[sym]['pnl'] += t['pnl']
print(f"{'Symbol':<14} {'Count':>6} {'Total P&L':>12} {'Avg':>8}")
print("-" * 44)
for sym, d in sorted(hard_by.items(), key=lambda x: x[1]['pnl'])[:12]:
    avg = d['pnl']/d['n']
    print(f"{sym:<14} {d['n']:>6} {d['pnl']:>+12,.0f} {avg:>+8,.0f}")
