#!/usr/bin/env python3
"""
CE Pine Script Backtester v1.0
================================
Downloads 1-minute OHLCV for top CE underlying stocks via yfinance,
simulates all CE v7.1 pine trigger conditions, measures 15-min forward
returns, then grid-searches optimal filter thresholds.

Usage:
  python3 ce_pine_backtest.py              # use yfinance (7 days, free)
  python3 ce_pine_backtest.py --days 30    # use AngelOne API (30 days, needs running bot)
  python3 ce_pine_backtest.py --cached     # skip download, use saved data

Output:
  ce_backtest_results.csv   — per-alert detail
  ce_backtest_grid.csv      — grid search results sorted by total expected PnL
"""

import sys, os, json, time, argparse
from datetime import datetime, timedelta, date
from pathlib import Path
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

# ─── Config ───────────────────────────────────────────────────────────────────

SYMBOLS = [
    'DIXON','PREMIERENE','ASIANPAINT','SUNPHARMA','SIEMENS','AMBER','GODREJPROP',
    'COFORGE','HINDUNILVR','MARUTI','BDL','TATAELXSI','TRENT','SBILIFE',
    'INDUSTOWER','WAAREEENER','AUROPHARMA','MAZDOCK','INDIGO','OBEROIRLTY',
    'HEROMOTOCO','MUTHOOTFIN','GRASIM','MFSL','ABB','RELIANCE','VOLTAS',
    'TORNTPHARM','HAL','DIVISLAB','POLICYBZR','ASTRAL','CAMS','MANKIND',
    'EICHERMOT','TITAN','UNOMINDA','PRESTIGE','DMART','ADANIPORTS','JSWSTEEL',
    'PNBHOUSING','SUPREMEIND','LODHA','CUMMINSIND','BHARTIARTL','BHARATFORG',
    'POLYCAB','BAJFINANCE','KPITTECH','PERSISTENT','TVSMOTOR','DALBHARAT',
    'BRITANNIA','HDFCLIFE','LUPIN','APOLLOHOSP','KAYNES','PIIND','CIPLA',
    'AXISBANK','DLF','COLPAL','HDFCAMC','SRF','FORTIS','CGPOWER','BLUESTARCO',
    'SONACOMS','APLAPOLLO','GLENMARK','HINDZINC','JINDALSTEL','HINDALCO',
    'ONGC','VEDL','TECHM','PIDILITIND','AUBANK','BANKNIFTY','NIFTY',
]

YFINANCE_MAP = {
    'BANKNIFTY': '^NSEBANK', 'NIFTY': '^NSEI',
    'M&M': 'M%26M.NS', 'LODHA': 'LODHA.NS', 'TMPV': 'TMPVT.NS',
}

DATA_DIR = Path('/root/santhosh/trading/CE_OPTIONS/tools/backtest_data')
DATA_DIR.mkdir(exist_ok=True)

FORWARD_MINUTES = 15   # how far ahead to measure outcome
WIN_THRESHOLD   = 0.002  # underlying +0.2% in 15 min = win (CE delta ~0.3 → option +0.06%)
LOSS_THRESHOLD  = -0.002 # underlying -0.2% = loss (symmetric)
MIN_HOUR = 9
MIN_MIN  = 30
MAX_HOUR = 14
MAX_MIN  = 45  # no new entries after 14:45

# ─── Indicator computation ────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist        = macd_line - signal_line
    return macd_line, signal_line, hist

def bollinger(series: pd.Series, period=20, std=2.0):
    mid   = series.rolling(period).mean()
    sigma = series.rolling(period).std()
    return mid + std*sigma, mid, mid - std*sigma

def atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()

def vwap_daily(df: pd.DataFrame) -> pd.Series:
    """VWAP that resets at market open each calendar day."""
    df = df.copy()
    df['_date'] = df.index.date
    df['_tp']   = (df['High'] + df['Low'] + df['Close']) / 3
    df['_cum_vol'] = df.groupby('_date')['Volume'].cumsum()
    df['_cum_tv']  = df.groupby('_date').apply(
        lambda g: (g['_tp'] * g['Volume']).cumsum()
    ).reset_index(level=0, drop=True)
    return (df['_cum_tv'] / df['_cum_vol']).replace([np.inf, -np.inf], np.nan)

def day_change(df: pd.DataFrame) -> pd.Series:
    """% change from previous day close to current price (resets intraday)."""
    prev_close = df['Close'].resample('D').last().shift(1).reindex(df.index, method='ffill')
    return (df['Close'] - prev_close) / prev_close * 100

def day_open(df: pd.DataFrame) -> pd.Series:
    """First bar open each day."""
    first_open = df['Open'].groupby(df.index.date).first()
    return df.index.map(lambda t: first_open.get(t.date(), np.nan))

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df['Close']; h = df['High']; l = df['Low']; v = df['Volume']

    df['ema9']    = ema(c, 9)
    df['ema20']   = ema(c, 20)
    df['rsi14']   = rsi(c, 14)
    df['macd_line'], df['macd_sig'], df['macd_hist'] = macd(c)
    df['bb_upper'], df['bb_mid'], df['bb_lower']     = bollinger(c)
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid'] * 100
    df['vol_ma']   = v.rolling(20).mean()
    df['vol_ratio'] = v / df['vol_ma']
    df['atr14']    = atr(h, l, c, 14)
    df['vwap']     = vwap_daily(df)
    df['day_chg']  = day_change(df)
    df['day_open_val'] = day_open(df)

    # Derived
    df['ema9_slope']  = df['ema9'].diff()
    df['vwap_rising'] = (c > df['vwap']) & (df['vwap'] > df['vwap'].shift(1))
    df['macd_hist_rising2'] = (df['macd_hist'] > df['macd_hist'].shift(1)) & \
                               (df['macd_hist'].shift(1) > df['macd_hist'].shift(2))

    return df

# ─── Signal simulation (CE pine v7.1) ─────────────────────────────────────────

def simulate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    For each bar, compute whether each CE trigger would fire.
    Returns df with signal columns added.
    """
    hist_floor  = params['hist_floor']
    vol_ceil    = params['vol_ceil']
    dc_ceil     = params['dc_ceil']
    rsi_min     = params['rsi_min']
    cont_hist   = params['cont_hist_floor']   # continuationSequenceReady macdHist floor
    cont_rsi    = params['cont_rsi_min']       # MOMENTUM_CONTINUATION rsi floor

    h = df['macd_hist']
    r = df['rsi14']
    v_ratio = df['vol_ratio']
    c = df['Close']
    o = df['Open']
    vwap = df['vwap']
    ema9 = df['ema9']
    ema20 = df['ema20']
    day_chg = df['day_chg']
    day_o   = df['day_open_val']
    bb_w    = df['bb_width']

    # ── Shared gates ──
    vol_ok      = v_ratio < vol_ceil                             # not excess volume
    stock_not_ext = day_chg < dc_ceil                           # stock not overextended
    price_above_day_open = c > day_o
    vwap_rising = df['vwap_rising']
    ema_trend_flex = (ema9 > ema20) | df['macd_hist_rising2']

    # ── MACD Burst Sequence ──
    hist_shift    = (h.shift(2) <= 0) & (h.shift(1) > 0)
    hist_confirm  = h.shift(1) >= hist_floor
    hist_burst    = (h > 0) & (h >= h.shift(1) * 1.5) & (h > h.shift(1))
    price_burst   = o > c.shift(1)
    vol_burst     = (v_ratio > 1.0) & vol_ok
    burst_ready   = hist_shift & hist_confirm & hist_burst & price_burst & vol_burst

    # ── Continuation Sequence ──
    hist_cont_ok  = h > cont_hist
    vol_cont_ok   = (v_ratio > 0.9) & vol_ok
    cont_ready    = hist_cont_ok & price_burst & vol_cont_ok

    sequence_ready = burst_ready | cont_ready

    # ── Sustained Hist Rise (3/4/5/6 bar alternatives) ──
    hist_rise3 = (h > h.shift(1)) & (h.shift(1) > h.shift(2)) & (h.shift(2) > h.shift(3))
    hist_rise4 = hist_rise3 & (h.shift(3) > h.shift(4))
    hist_rise5 = hist_rise4 & (h.shift(4) > h.shift(5))
    hist_rise6 = hist_rise5 & (h.shift(5) > h.shift(6))
    sustained_rise = (hist_rise3 | hist_rise4 | hist_rise5 | hist_rise6) & (h > 0)

    # ── RSI recovery ──
    rsi_rising = r > r.shift(1)
    rsi_recovery = ((r > rsi_min) & rsi_rising) | (r > 75)

    # ── Price structure (OR-based) ──
    higher_low    = (df['Low'] > df['Low'].shift(1)) & (df['Low'].shift(1) >= df['Low'].shift(2))
    bullish_close = c > o
    price_structure = (higher_low | bullish_close) & vwap_rising

    # ── Session Rising Filter (OR-based) ──
    intraday_rising = (h > hist_floor) & ema_trend_flex & vwap_rising & (c > ema20)
    session_rising  = price_structure | \
                      ((day_chg >= 0.20) & vwap_rising & ema_trend_flex & rsi_recovery) | \
                      (intraday_rising & rsi_recovery)

    # ── MACD context classification ──
    deep_ctx  = (df['macd_line'].shift(1) <= 0) & (df['macd_sig'].shift(1) <= 0)
    accel_ctx = (df['macd_line'].shift(1) > 0) & (df['macd_sig'].shift(1) > 0)
    neutral_ctx = ~deep_ctx & ~accel_ctx

    # ── BB filter ──
    bb_avg_w  = bb_w.rolling(10).mean()
    bb_compress = bb_w < bb_avg_w * 0.8
    bb_expand   = (bb_w > bb_w.shift(1)) & (bb_w.shift(1) >= bb_w.shift(2)) & (bb_w.shift(2) >= bb_w.shift(3))
    bb_filter   = bb_compress | (bb_expand & (c > df['bb_mid']))

    # ── Time filter ──
    hour  = df.index.hour
    minute = df.index.minute
    in_time = ((hour == MIN_HOUR) & (minute >= MIN_MIN)) | \
              ((hour > MIN_HOUR) & (hour < MAX_HOUR)) | \
              ((hour == MAX_HOUR) & (minute <= MAX_MIN))

    # ── Deep MACD Reversal ──
    deep_rev_setup = deep_ctx & sequence_ready
    df['sig_deep'] = deep_rev_setup & sustained_rise & ema_trend_flex & rsi_recovery & \
                     bb_filter & price_structure & session_rising & stock_not_ext & \
                     in_time & price_above_day_open

    # ── MACD Reversal ──
    macd_rev_setup = neutral_ctx & sequence_ready
    df['sig_macd'] = macd_rev_setup & in_time & stock_not_ext & price_above_day_open & \
                     ~df['sig_deep']

    # ── Momentum Acceleration ──
    ema9_s1 = ema9.shift(1); ema20_s1 = ema20.shift(1)
    mom_trend_strict = (ema9_s1 > ema20_s1) & (ema20_s1 >= ema20.shift(2)) & (c.shift(1) > ema9_s1)
    hist_accel = hist_shift & hist_confirm & hist_burst
    mom_rsi    = (r.shift(1) > 50) & (r.shift(1) > r.shift(2))
    mom_candle = (c.shift(1) > o.shift(1)) & (c.shift(1) >= c.shift(2))
    dist_ema   = (ema9 > 0) & ((c - ema9).abs() / ema9 * 100 < 1.5)
    accel_setup = accel_ctx & sequence_ready
    df['sig_accel'] = accel_setup & sustained_rise & mom_trend_strict & hist_accel & \
                      mom_rsi & mom_candle & dist_ema & session_rising & stock_not_ext & \
                      in_time & price_above_day_open & ~df['sig_deep']

    # ── Momentum Continuation (4th type) ──
    # cont_hist = direct macd_hist floor on the CONTINUATION signal itself
    # (distinct from cont_hist used in sequence_ready above, which guards reversal signals)
    cont_mc_hist_floor = params.get('cont_mc_hist_floor', 0.0)   # NEW: default 0 = no filter
    stock_winner  = day_chg >= 0.50
    cont_vol_ok   = (v_ratio > 1.2) & vol_ok
    cont_rsi_ok   = r > cont_rsi
    cont_hist_ok  = h >= cont_mc_hist_floor
    df['sig_cont'] = stock_winner & cont_rsi_ok & cont_vol_ok & cont_hist_ok & ema_trend_flex & \
                     sustained_rise & vwap_rising & price_above_day_open & stock_not_ext & \
                     in_time & ~df['sig_deep'] & ~df['sig_accel'] & ~df['sig_macd']

    # ── Raw combined signal + type ──
    df['signal'] = df['sig_deep'] | df['sig_macd'] | df['sig_accel'] | df['sig_cont']
    df['sig_type'] = 'NONE'
    df.loc[df['sig_deep'],  'sig_type'] = 'DEEP_MACD_REVERSAL'
    df.loc[df['sig_macd'],  'sig_type'] = 'MACD_REVERSAL'
    df.loc[df['sig_accel'], 'sig_type'] = 'MOMENTUM_ACCELERATION'
    df.loc[df['sig_cont'],  'sig_type'] = 'MOMENTUM_CONTINUATION'

    # ── 30-bar cooldown: keep only the first signal in each 30-min window ──
    # Prevents consecutive bars in a sustained trend from each firing (no double-trading).
    COOLDOWN = 30
    signal_pos = np.where(df['signal'].values)[0]
    if len(signal_pos) > 0:
        # Find kept positions with numpy loop (fast — iterates only over signal positions)
        kept_flags = np.ones(len(signal_pos), dtype=bool)
        last_kept = signal_pos[0]
        for j in range(1, len(signal_pos)):
            if signal_pos[j] - last_kept >= COOLDOWN:
                last_kept = signal_pos[j]
            else:
                kept_flags[j] = False
        kept_pos = signal_pos[kept_flags]

        # Rebuild signal arrays using kept positions only
        n = len(df)
        sig_arr  = df['sig_type'].values.copy()
        sig_m    = np.zeros(n, dtype=bool)
        deep_m   = np.zeros(n, dtype=bool)
        macd_m   = np.zeros(n, dtype=bool)
        accel_m  = np.zeros(n, dtype=bool)
        cont_m   = np.zeros(n, dtype=bool)
        type_arr = np.full(n, 'NONE', dtype=object)

        col_to_mask = {
            'DEEP_MACD_REVERSAL':    deep_m,
            'MACD_REVERSAL':         macd_m,
            'MOMENTUM_ACCELERATION': accel_m,
            'MOMENTUM_CONTINUATION': cont_m,
        }
        for p in kept_pos:
            stype = sig_arr[p]
            sig_m[p] = True
            type_arr[p] = stype
            if stype in col_to_mask:
                col_to_mask[stype][p] = True

        df['signal']   = sig_m
        df['sig_deep'] = deep_m
        df['sig_macd'] = macd_m
        df['sig_accel']= accel_m
        df['sig_cont'] = cont_m
        df['sig_type'] = type_arr

    return df

# ─── Forward return measurement ───────────────────────────────────────────────

def measure_outcomes(df: pd.DataFrame, fwd_bars: int = FORWARD_MINUTES) -> pd.DataFrame:
    """
    For each signal bar, measure the % price change over the next fwd_bars bars.
    Returns a DataFrame of alerts with outcomes.
    """
    signal_bars = df[df['signal']].copy()
    if signal_bars.empty:
        return pd.DataFrame()

    results = []
    close_vals = df['Close'].values
    idx_map    = {ts: i for i, ts in enumerate(df.index)}

    for ts, row in signal_bars.iterrows():
        i = idx_map[ts]
        entry_price = row['Close']
        fwd_end = min(i + fwd_bars, len(close_vals) - 1)

        # Check if we cross into the next day (don't carry overnight)
        entry_date = ts.date()
        fwd_prices = close_vals[i+1 : fwd_end+1]

        # Find first SL hit (-0.3%) or target hit (+0.3%) within fwd window
        outcome_pct = None
        for fp in fwd_prices:
            pct = (fp - entry_price) / entry_price
            if pct >= 0.003:    # +0.3% = target hit
                outcome_pct = pct
                break
            elif pct <= -0.003:  # -0.3% = SL hit
                outcome_pct = pct
                break
        if outcome_pct is None and len(fwd_prices) > 0:
            outcome_pct = (fwd_prices[-1] - entry_price) / entry_price

        if outcome_pct is None:
            continue

        # Classify
        if outcome_pct >= WIN_THRESHOLD:
            outcome = 'WIN'
        elif outcome_pct <= LOSS_THRESHOLD:
            outcome = 'LOSS'
        else:
            outcome = 'NEUTRAL'

        results.append({
            'timestamp':   ts,
            'symbol':      row.get('_symbol', '?'),
            'sig_type':    row['sig_type'],
            'entry_price': entry_price,
            'macd_hist':   row['macd_hist'],
            'rsi':         row['rsi14'],
            'vol_ratio':   row['vol_ratio'],
            'day_chg':     row['day_chg'],
            'vwap_dist':   (entry_price - row['vwap']) / row['vwap'] * 100 if row['vwap'] > 0 else 0,
            'ema_spread':  (row['ema9'] - row['ema20']) / row['ema20'] * 100 if row['ema20'] > 0 else 0,
            'outcome_pct': outcome_pct * 100,
            'outcome':     outcome,
            'win':         outcome == 'WIN',
        })

    return pd.DataFrame(results)

# ─── Data download ────────────────────────────────────────────────────────────

def download_yfinance(symbols: list, period: str = '7d') -> dict:
    import yfinance as yf
    data = {}
    print(f"\nDownloading {len(symbols)} symbols via yfinance ({period})...")

    for sym in symbols:
        yf_ticker = YFINANCE_MAP.get(sym, f'{sym}.NS')
        try:
            df = yf.download(yf_ticker, period=period, interval='1m',
                             auto_adjust=True, progress=False)
            if df.empty:
                print(f"  {sym}: empty")
                continue
            # Flatten multi-level columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df['_symbol'] = sym
            df = add_indicators(df)
            data[sym] = df
            print(f"  {sym}: {len(df)} bars, {df.index[0].date()} → {df.index[-1].date()}")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")
        time.sleep(0.3)  # polite to yfinance

    return data

def load_cached(symbols: list) -> dict:
    data = {}
    for sym in symbols:
        f = DATA_DIR / f'{sym}_1m.csv.gz'
        if f.exists():
            df = pd.read_csv(f, index_col=0, parse_dates=True, compression='gzip')
            df = add_indicators(df)
            df['_symbol'] = sym
            data[sym] = df
    print(f"Loaded {len(data)} symbols from cache")
    return data

def save_cache(data: dict):
    for sym, df in data.items():
        cols_to_save = ['Open','High','Low','Close','Volume']
        f = DATA_DIR / f'{sym}_1m.csv.gz'
        df[cols_to_save].to_csv(f, compression='gzip')
    print(f"Saved {len(data)} symbols to {DATA_DIR}")

# ─── Grid search ──────────────────────────────────────────────────────────────

GRID = {
    'hist_floor':       [0.20, 0.30, 0.40, 0.50],
    'vol_ceil':         [1.5, 2.0, 999],
    'dc_ceil':          [2.0, 3.0, 999],
    'rsi_min':          [40, 45, 50],
    'cont_hist_floor':  [0.30, 0.50, 0.70],    # for sequence_ready in reversal signals
    'cont_rsi_min':     [55, 60, 65],
    'cont_mc_hist_floor': [0.0, 0.50, 0.80, 1.00],  # NEW: direct hist floor for MOMENTUM_CONTINUATION
}

def run_grid_search(all_data: dict) -> pd.DataFrame:
    # Build all param combinations
    import itertools
    keys   = list(GRID.keys())
    values = list(GRID.values())
    combos = list(itertools.product(*values))
    print(f"\nGrid search: {len(combos)} combinations × {len(all_data)} symbols")

    records = []
    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        total = wins = 0
        total_pct = 0.0

        for sym, df in all_data.items():
            try:
                df2 = simulate_signals(df.copy(), params)
                res = measure_outcomes(df2)
                if res.empty:
                    continue
                total     += len(res)
                wins      += res['win'].sum()
                total_pct += res['outcome_pct'].sum()
            except:
                pass

        if total == 0:
            continue

        wr  = wins / total * 100
        avg = total_pct / total
        rec = {**params,
               'n_alerts': total,
               'n_wins':   wins,
               'win_rate': wr,
               'avg_pct':  avg,
               'total_pct': total_pct,
        }
        records.append(rec)

        if i % 50 == 0:
            print(f"  [{i}/{len(combos)}] best so far: {max(records, key=lambda r: r['total_pct'])['total_pct']:.1f}% total")

    grid_df = pd.DataFrame(records).sort_values('total_pct', ascending=False)
    return grid_df

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--cached', action='store_true')
    parser.add_argument('--symbols', nargs='+', default=SYMBOLS)
    parser.add_argument('--quick', action='store_true', help='Skip grid, just show signal stats')
    args = parser.parse_args()

    # 1. Load data
    if args.cached:
        all_data = load_cached(args.symbols)
    else:
        period = f'{args.days}d'
        all_data = download_yfinance(args.symbols, period=period)
        save_cache(all_data)

    if not all_data:
        print("No data loaded. Exiting.")
        return

    total_bars = sum(len(d) for d in all_data.values())
    date_ranges = [(sym, str(d.index[0].date()), str(d.index[-1].date())) for sym, d in all_data.items()]
    print(f"\nLoaded {len(all_data)} symbols, {total_bars:,} total 1-min bars")
    earliest = min(d.index[0].date() for d in all_data.values())
    latest   = max(d.index[-1].date() for d in all_data.values())
    print(f"Date range: {earliest} → {latest}")

    # 2. Baseline: run with current v7.1 params
    baseline_params = {
        'hist_floor': 0.30, 'vol_ceil': 1.5, 'dc_ceil': 3.0,
        'rsi_min': 40, 'cont_hist_floor': 0.50, 'cont_rsi_min': 60,
        'cont_mc_hist_floor': 0.0,   # baseline = no filter on CONTINUATION hist (shows full picture)
    }
    print(f"\n{'='*60}")
    print("BASELINE (v7.1 params):")
    all_alerts = []
    for sym, df in all_data.items():
        try:
            df2 = simulate_signals(df.copy(), baseline_params)
            res = measure_outcomes(df2)
            if not res.empty:
                all_alerts.append(res)
        except Exception as e:
            print(f"  Error {sym}: {e}")

    if all_alerts:
        base_df = pd.concat(all_alerts, ignore_index=True)
        print(f"  Total signals: {len(base_df)}")
        print(f"  Win rate:      {base_df['win'].mean()*100:.1f}%")
        print(f"  Avg outcome:   {base_df['outcome_pct'].mean():+.3f}%")
        print(f"  Total pct:     {base_df['outcome_pct'].sum():+.1f}%")

        # Per-type breakdown
        print(f"\n  By signal type:")
        for st in ['DEEP_MACD_REVERSAL','MACD_REVERSAL','MOMENTUM_ACCELERATION','MOMENTUM_CONTINUATION']:
            sub = base_df[base_df['sig_type'] == st]
            if sub.empty: continue
            print(f"    {st:28} n={len(sub):4d}  WR={sub['win'].mean()*100:.0f}%  avg={sub['outcome_pct'].mean():+.3f}%  total={sub['outcome_pct'].sum():+.1f}%")

        # By MACD hist bucket
        print(f"\n  By MACD hist at signal:")
        for lo,hi in [(0,.1),(.1,.3),(.3,.5),(.5,1),(1,99)]:
            sub = base_df[(base_df['macd_hist']>=lo) & (base_df['macd_hist']<hi)]
            if sub.empty: continue
            hi_label = 'inf' if hi >= 99 else f'{hi:.0f}'
            print(f"    MACD {lo:.1f}-{hi_label:>3}  n={len(sub):4d}  WR={sub['win'].mean()*100:.0f}%  avg={sub['outcome_pct'].mean():+.3f}%")

        # By vol ratio bucket
        print(f"\n  By volume ratio at signal:")
        for lo,hi in [(0,.9),(.9,1.2),(1.2,1.5),(1.5,2),(2,99)]:
            sub = base_df[(base_df['vol_ratio']>=lo) & (base_df['vol_ratio']<hi)]
            if sub.empty: continue
            hi_label2 = 'inf' if hi >= 99 else f'{hi:.0f}'
            print(f"    Vol {lo:.1f}-{hi_label2:>3}x  n={len(sub):4d}  WR={sub['win'].mean()*100:.0f}%  avg={sub['outcome_pct'].mean():+.3f}%")

        # By day change bucket
        print(f"\n  By stock day change at signal:")
        for lo,hi in [(-99,0),(0,0.5),(0.5,1.5),(1.5,3),(3,99)]:
            sub = base_df[(base_df['day_chg']>=lo) & (base_df['day_chg']<hi)]
            if sub.empty: continue
            print(f"    DayChg {lo:+.0f} to {hi:+.0f}%  n={len(sub):4d}  WR={sub['win'].mean()*100:.0f}%  avg={sub['outcome_pct'].mean():+.3f}%")

        base_df.to_csv(DATA_DIR / 'ce_backtest_baseline.csv', index=False)
        print(f"\n  Saved detail: {DATA_DIR}/ce_backtest_baseline.csv")

    if args.quick:
        return

    # 3. Grid search
    print(f"\n{'='*60}")
    print("GRID SEARCH...")
    grid_df = run_grid_search(all_data)

    out_path = DATA_DIR / 'ce_backtest_grid.csv'
    grid_df.to_csv(out_path, index=False)
    print(f"\nSaved grid results: {out_path}")

    # Show top 20
    print(f"\nTop 20 parameter combinations by total_pct (sum of all alert forward returns):")
    print(f"{'hist_floor':>10} {'vol_ceil':>8} {'dc_ceil':>7} {'rsi_min':>7} {'c_hist':>6} {'c_rsi':>5} "
          f"{'n':>5} {'WR%':>6} {'avg%':>7} {'total%':>8}")
    print('-'*80)
    for _, row in grid_df.head(20).iterrows():
        print(f"{row['hist_floor']:>10.2f} {row['vol_ceil']:>8.1f} {row['dc_ceil']:>7.1f} "
              f"{row['rsi_min']:>7.0f} {row['cont_hist_floor']:>6.2f} {row['cont_rsi_min']:>5.0f} "
              f"{row['n_alerts']:>5.0f} {row['win_rate']:>5.1f}% "
              f"{row['avg_pct']:>+7.3f}% {row['total_pct']:>+8.1f}%")

    print(f"\n--- Current v7.1 params ---")
    v71 = grid_df[
        (grid_df['hist_floor'] == 0.30) & (grid_df['vol_ceil'] == 1.5) &
        (grid_df['dc_ceil'] == 3.0) & (grid_df['rsi_min'] == 40)
    ]
    if not v71.empty:
        r = v71.iloc[0]
        print(f"  n={r['n_alerts']:.0f}  WR={r['win_rate']:.1f}%  avg={r['avg_pct']:+.3f}%  total={r['total_pct']:+.1f}%")
    else:
        print("  (not in grid)")

if __name__ == '__main__':
    main()
