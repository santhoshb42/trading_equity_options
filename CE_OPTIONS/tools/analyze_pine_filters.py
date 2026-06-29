#!/usr/bin/env python3
"""
Pine Script Filter Analysis Tool
Fetches today's top gainers from AngelOne, downloads 1-min candles,
and simulates every v9.0 pine filter to find what's blocking alerts.
"""

import sys, os, math, time
sys.path.insert(0, '/root/santhosh/trading/CE_OPTIONS')
os.chdir('/root/santhosh/trading/CE_OPTIONS')

from dotenv import load_dotenv
load_dotenv('/root/santhosh/trading/CE_OPTIONS/tools/.env')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# ─── Broker setup ──────────────────────────────────────────────────────────────
from optcode.angelone_options import AngelOneOptionsBroker

# ─── Constants matching pine v9.0 ─────────────────────────────────────────────
EMA_FAST          = 9
EMA_SLOW          = 20
RSI_LEN           = 14
MACD_FAST         = 12
MACD_SLOW         = 26
MACD_SIGNAL_LEN   = 9
BB_LEN            = 20
BB_STD            = 2.0
VOL_MA_LEN        = 20
VOL_MULTIPLIER    = 1.2
VOL_CEIL_MULTI    = 1.5
HIST_FLOOR        = 0.05
HIST_BURST_MULTI  = 1.5
DAY_CHANGE_CEIL   = 3.0
ADX_LEN           = 14

TRADE_WINDOW_START = (9, 30)
TRADE_WINDOW_END   = (15, 0)


# ─── Technical indicators ─────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    macd_line   = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist        = macd_line - signal_line
    return macd_line, signal_line, hist

def bollinger(series: pd.Series, period=20, std_dev=2.0):
    mid   = series.rolling(period).mean()
    s     = series.rolling(period).std()
    upper = mid + std_dev * s
    lower = mid - std_dev * s
    width = (upper - lower) / mid * 100
    return upper, mid, lower, width

def dmi_adx(high: pd.Series, low: pd.Series, close: pd.Series, period=14):
    """Compute +DI, -DI, ADX."""
    tr  = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()

    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_di  = 100 * pd.Series(plus_dm,  index=high.index).ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(span=period, adjust=False).mean() / atr

    dx  = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(span=period, adjust=False).mean()
    return plus_di, minus_di, adx

def vwap_series(df: pd.DataFrame) -> pd.Series:
    hlc3 = (df['high'] + df['low'] + df['close']) / 3
    cum_vol  = df['volume'].expanding().sum()
    cum_tp_v = (hlc3 * df['volume']).expanding().sum()
    return cum_tp_v / cum_vol.replace(0, np.nan)

def ta_rising(series: pd.Series, n: int) -> pd.Series:
    """True when series has risen for n consecutive bars."""
    result = pd.Series(True, index=series.index)
    for i in range(1, n + 1):
        result &= series > series.shift(i)
    return result


# ─── Build indicator frame ────────────────────────────────────────────────────

def build_indicators(df: pd.DataFrame, prev_close: float, day_open: float,
                     nifty_change: float) -> pd.DataFrame:
    d = df.copy()

    d['ema9']  = ema(d['close'], EMA_FAST)
    d['ema20'] = ema(d['close'], EMA_SLOW)
    d['rsi']   = rsi(d['close'], RSI_LEN)

    ml, ms, mh = macd(d['close'], MACD_FAST, MACD_SLOW, MACD_SIGNAL_LEN)
    d['macd_line']   = ml
    d['macd_signal'] = ms
    d['macd_hist']   = mh

    bb_upper, bb_mid, bb_lower, bb_width = bollinger(d['close'], BB_LEN, BB_STD)
    d['bb_upper'] = bb_upper
    d['bb_mid']   = bb_mid
    d['bb_lower'] = bb_lower
    d['bb_width'] = bb_width

    d['vol_ma']  = d['volume'].rolling(VOL_MA_LEN).mean()
    d['avg_body'] = (d['close'] - d['open']).abs().rolling(10).mean()
    d['body_size'] = (d['close'] - d['open']).abs()
    d['candle_range']  = d['high'] - d['low']
    d['avg_range'] = d['candle_range'].rolling(10).mean()

    d['atr']    = (pd.concat([d['high'] - d['low'],
                               (d['high'] - d['close'].shift()).abs(),
                               (d['low']  - d['close'].shift()).abs()], axis=1)
                   .max(axis=1).ewm(span=14, adjust=False).mean())

    _, _, d['adx'] = dmi_adx(d['high'], d['low'], d['close'], ADX_LEN)
    d['vwap']      = vwap_series(d)

    d['prev_close']   = prev_close
    d['day_open']     = day_open
    d['day_change']   = (d['close'] - prev_close) / prev_close * 100 if prev_close else np.nan
    d['nifty_change'] = nifty_change

    return d


# ─── Pine v9.0 gate evaluation ────────────────────────────────────────────────

def evaluate_pine_gates(d: pd.DataFrame) -> pd.DataFrame:
    """Compute every named condition from pine v9.0, plus proposed v9.1 bypass."""

    # ── volume / range ──────────────────────────────────────────────────────
    d['volumeNotExcess']       = d['volume'] < d['vol_ma'] * VOL_CEIL_MULTI
    d['stockNotOverextended']  = d['day_change'].isna() | (d['day_change'] < DAY_CHANGE_CEIL)
    d['volFilter']             = d['volume'] > d['vol_ma'] * VOL_MULTIPLIER
    d['rangeExpansion']        = d['candle_range'] > d['avg_range'] * 1.3
    d['atrExpansion']          = d['atr'] > d['atr'].shift(1)

    # ── BB ──────────────────────────────────────────────────────────────────
    d['bb_avg_width'] = d['bb_width'].rolling(10).mean()
    d['bbCompression'] = d['bb_width'] < d['bb_avg_width'] * 0.8
    d['bbExpansion']   = ((d['bb_width'] > d['bb_width'].shift(1)) &
                          (d['bb_width'].shift(1) >= d['bb_width'].shift(2)) &
                          (d['bb_width'].shift(2) >= d['bb_width'].shift(3)))
    d['bbFilter']      = d['bbCompression'] | (d['bbExpansion'] & (d['close'] > d['bb_mid']))

    # ── candle quality ──────────────────────────────────────────────────────
    d['bloomingCandle']    = (d['close'] > d['open']) & (d['body_size'] >= d['avg_body']) & (d['close'] > d['ema9'])
    d['noUpperReject']     = (d['high'] - d['close']) < (d['close'] - d['open'])
    d['closeInUpperHalf']  = d['close'] > (d['high'] + d['low']) / 2

    # ── trend ───────────────────────────────────────────────────────────────
    d['macdHistRising']    = ta_rising(d['macd_hist'], 2)
    d['emaTrendStrict']    = (d['ema9'] > d['ema20']) & (d['ema20'] > d['ema20'].shift(1))
    d['emaTrendFlexible']  = (d['ema9'] > d['ema20']) | d['macdHistRising']
    d['emaSupport']        = (d['low'] >= d['ema9']) | (d['close'] > d['ema9'])
    d['emaSlope']          = (d['ema9'] > d['ema9'].shift(1)) & (d['ema9'].shift(1) > d['ema9'].shift(2))
    d['trendFilterFlexible'] = d['emaTrendFlexible'] & d['emaSupport'] & d['emaSlope']

    d['rsiRising']    = ta_rising(d['rsi'], 2)
    d['rsiRecovery']  = (d['rsi'] > 40.0) & d['rsiRising']

    # ── VWAP ────────────────────────────────────────────────────────────────
    d['vwapRising']   = (d['close'] > d['vwap']) & (d['vwap'] > d['vwap'].shift(1))

    # ── price structure ─────────────────────────────────────────────────────
    d['higherLow']     = (d['low'] > d['low'].shift(1)) & (d['low'].shift(1) >= d['low'].shift(2))
    d['bullishClose']  = d['close'] > d['open']
    d['priceStructure'] = (d['higherLow'] | d['bullishClose']) & d['vwapRising']

    # ── session rising ───────────────────────────────────────────────────────
    d['intradayRisingStructure'] = ((d['macd_hist'] > HIST_FLOOR) & d['emaTrendFlexible'] &
                                    d['vwapRising'] & (d['close'] > d['ema20']))
    d['sessionRisingFilter'] = (
        d['priceStructure'] |
        ((d['day_change'] >= 0.20) & d['vwapRising'] & d['emaTrendFlexible'] & d['rsiRecovery']) |
        (d['intradayRisingStructure'] & d['rsiRecovery'])
    )

    # ── MACD burst sequence ─────────────────────────────────────────────────
    d['histShiftPrevBar']   = (d['macd_hist'].shift(2) <= 0) & (d['macd_hist'].shift(1) > 0)
    d['histConfirmCandle1'] = d['macd_hist'].shift(1) >= HIST_FLOOR
    d['histBurstCandle2']   = ((d['macd_hist'] > 0) &
                                (d['macd_hist'] >= d['macd_hist'].shift(1) * HIST_BURST_MULTI) &
                                (d['macd_hist'] > d['macd_hist'].shift(1)))
    d['volumeBurstCandle2'] = (d['volume'] > d['vol_ma']) & d['volumeNotExcess']
    d['burstSequenceReady'] = (d['histShiftPrevBar'] & d['histConfirmCandle1'] &
                                d['histBurstCandle2'] & d['bloomingCandle'] & d['volumeBurstCandle2'])

    # continuationSequenceReady
    d['histContCandle2']     = d['macd_hist'] > 0.60
    d['contVolValid_seq']    = (d['volume'] > d['vol_ma'] * 0.9) & d['volumeNotExcess']
    d['continuationSequenceReady'] = (d['histContCandle2'] & d['bloomingCandle'] & d['contVolValid_seq'])

    d['sequenceReady'] = d['burstSequenceReady'] | d['continuationSequenceReady']

    # sustainedHistRise
    d['histRise3'] = ta_rising(d['macd_hist'], 3)
    d['histRise4'] = ta_rising(d['macd_hist'], 4)
    d['histRise5'] = ta_rising(d['macd_hist'], 5)
    d['histRise6'] = ta_rising(d['macd_hist'], 6)
    d['sustainedHistRise'] = ((d['histRise3'] | d['histRise4'] | d['histRise5'] | d['histRise6']) &
                               (d['macd_hist'] > 0))

    # ── contexts ─────────────────────────────────────────────────────────────
    d['deepContext']         = (d['macd_line'].shift(1) <= 0) & (d['macd_signal'].shift(1) <= 0)
    d['accelerationContext'] = (d['macd_line'].shift(1) > 0)  & (d['macd_signal'].shift(1) > 0)

    # ── DEEP trigger ────────────────────────────────────────────────────────
    d['priceAboveDayOpen']  = d['close'] > d['day_open']
    d['adxDeepValid']       = d['adx'] > 15
    d['deepReversalSetup']  = d['deepContext'] & d['sequenceReady']

    # time window
    d['hourIST']   = d.index.hour
    d['minuteIST'] = d.index.minute
    d['inTradeWindow'] = (
        ((d['hourIST'] == 9) & (d['minuteIST'] >= 30)) |
        ((d['hourIST'] >= 10) & (d['hourIST'] < 15)) |
        ((d['hourIST'] == 15) & (d['minuteIST'] <= 0))
    )

    # distanceFromEMA
    d['confirmDistFromEMA'] = (d['close'].shift(1) - d['ema9'].shift(1)).abs() / d['ema9'].shift(1) * 100
    d['confirmNotExtended'] = d['confirmDistFromEMA'] < 1.5

    d['deepReversalTrigger'] = (
        d['deepReversalSetup'] & d['sustainedHistRise'] & d['trendFilterFlexible'] &
        d['rsiRecovery'] & d['bbFilter'] & d['priceStructure'] & d['sessionRisingFilter'] &
        d['adxDeepValid'] & d['noUpperReject'] & d['stockNotOverextended'] &
        d['inTradeWindow'] & d['priceAboveDayOpen']
    )

    # ── ACCEL trigger ────────────────────────────────────────────────────────
    d['momentumTrendStrict'] = ((d['ema9'].shift(1) > d['ema20'].shift(1)) &
                                 (d['ema20'].shift(1) >= d['ema20'].shift(2)) &
                                 (d['close'].shift(1) > d['ema9'].shift(1)))
    d['histShiftCandle0']  = d['histShiftPrevBar']   # alias
    d['histVelocity']      = d['histBurstCandle2']   # alias
    d['momentumHistAccel'] = d['histShiftCandle0'] & d['histConfirmCandle1'] & d['histVelocity']
    d['momentumRSI']       = (d['rsi'].shift(1) > 50) & (d['rsi'].shift(1) > d['rsi'].shift(2))
    d['momentumVolume']    = (d['volume'] > d['vol_ma']) & d['volumeNotExcess']
    d['momentumCandles']   = (d['close'].shift(1) > d['open'].shift(1)) & (d['close'].shift(1) >= d['close'].shift(2))
    d['momentumVol']       = (d['atr'].shift(1) >= d['atr'].shift(2)) | (d['bb_width'].shift(1) >= d['bb_width'].shift(2))
    d['momentumNotExtended'] = d['confirmNotExtended'] & (d['open'] < d['bb_upper'])
    d['momentumAccelSetup']  = d['accelerationContext'] & d['sequenceReady']

    d['momentumAccelTrigger_v9'] = (
        d['momentumAccelSetup'] & d['sustainedHistRise'] & d['momentumTrendStrict'] &
        d['momentumHistAccel'] & d['momentumRSI'] & d['momentumVolume'] &
        d['momentumCandles'] & d['momentumVol'] & d['momentumNotExtended'] &
        d['rangeExpansion'] & d['sessionRisingFilter'] & d['stockNotOverextended'] &
        d['inTradeWindow'] & ~d['deepReversalTrigger'] & d['priceAboveDayOpen']
    )

    # ── v9.1 proposed: sustained bypass for ACCEL ────────────────────────────
    d['accelHistValid_v91'] = (d['momentumHistAccel'] |
                                (d['continuationSequenceReady'] & d['sustainedHistRise'] & d['macdHistRising']))
    d['momentumAccelTrigger_v91'] = (
        d['momentumAccelSetup'] & d['sustainedHistRise'] & d['momentumTrendStrict'] &
        d['accelHistValid_v91'] & d['momentumRSI'] & d['momentumVolume'] &
        d['momentumCandles'] & d['momentumVol'] & d['momentumNotExtended'] &
        d['rangeExpansion'] & d['sessionRisingFilter'] & d['stockNotOverextended'] &
        d['inTradeWindow'] & ~d['deepReversalTrigger'] & d['priceAboveDayOpen']
    )

    # ── CONTINUATION trigger ─────────────────────────────────────────────────
    d['stockDayWinner']  = d['day_change'].notna() & (d['day_change'] >= 0.25)
    d['contBurstReady']  = (d['histShiftPrevBar'] &
                             (d['macd_hist'].shift(1) >= 0.05) &
                             (d['macd_hist'] >= d['macd_hist'].shift(1) * HIST_BURST_MULTI) &
                             d['bloomingCandle'])
    d['contRsiValid']    = (d['rsi'] > 40) & (d['rsi'] < 78) & (d['rsi'] > d['rsi'].shift(1))
    d['contVolValid']    = (d['volume'] > d['vol_ma'] * 1.0) & d['volumeNotExcess']
    d['adxContValid']    = (d['adx'] > 18) & (d['adx'] < 40)

    d['momentumContinuationTrigger_v9'] = (
        d['stockDayWinner'] & d['contRsiValid'] & d['contVolValid'] & d['contBurstReady'] &
        d['emaTrendFlexible'] & d['adxContValid'] & d['vwapRising'] & d['macdHistRising'] &
        d['closeInUpperHalf'] & d['priceAboveDayOpen'] & d['stockNotOverextended'] &
        d['inTradeWindow'] & ~d['deepReversalTrigger'] & ~d['momentumAccelTrigger_v9']
    )

    # ── v9.1 proposed: sustained bypass for CONT ─────────────────────────────
    d['combinedContReady_v91'] = (d['contBurstReady'] |
                                   (d['continuationSequenceReady'] & d['sustainedHistRise'] & d['macdHistRising']))
    d['momentumContinuationTrigger_v91'] = (
        d['stockDayWinner'] & d['contRsiValid'] & d['contVolValid'] & d['combinedContReady_v91'] &
        d['emaTrendFlexible'] & d['adxContValid'] & d['vwapRising'] & d['macdHistRising'] &
        d['closeInUpperHalf'] & d['priceAboveDayOpen'] & d['stockNotOverextended'] &
        d['inTradeWindow'] & ~d['deepReversalTrigger'] & ~d['momentumAccelTrigger_v91']
    )

    # ── any v9 / v9.1 ────────────────────────────────────────────────────────
    d['any_v9_trigger']  = d['deepReversalTrigger'] | d['momentumAccelTrigger_v9']  | d['momentumContinuationTrigger_v9']
    d['any_v91_trigger'] = d['deepReversalTrigger'] | d['momentumAccelTrigger_v91'] | d['momentumContinuationTrigger_v91']

    return d


# ─── Diagnosis: explain why every candle during 9:30-15:00 didn't fire ────────

GATE_GROUPS = {
    'DEEP': [
        ('deepContext',          'macdLine[1]<=0 & signal[1]<=0'),
        ('sequenceReady',        'burst or cont-seq (hist>0.60)'),
        ('sustainedHistRise',    'hist rising 3+bars'),
        ('trendFilterFlexible',  'EMA/MACD trend ok'),
        ('rsiRecovery',          'RSI>40 & rising'),
        ('bbFilter',             'BB compression or expansion'),
        ('priceStructure',       'higherLow/bullishClose & VWAP rising'),
        ('sessionRisingFilter',  'session rising'),
        ('adxDeepValid',         'ADX>15'),
        ('noUpperReject',        'upper wick < body'),
        ('stockNotOverextended', 'dayChange < 3%'),
        ('inTradeWindow',        '9:30-15:00'),
        ('priceAboveDayOpen',    'close > dayOpen'),
    ],
    'ACCEL_v9': [
        ('accelerationContext',  'macdLine[1]>0 & signal[1]>0'),
        ('sequenceReady',        'burst or cont-seq'),
        ('sustainedHistRise',    'hist rising 3+bars'),
        ('momentumTrendStrict',  'ema9>ema20 & close>ema9 (prev)'),
        ('momentumHistAccel',    '*** 3-candle flip+burst (BLOCKS sustained trends) ***'),
        ('momentumRSI',          'RSI[1]>50 & rising'),
        ('momentumVolume',       'vol > MA'),
        ('momentumCandles',      'prev bullish candle'),
        ('momentumVol',          'ATR or BB expanding'),
        ('momentumNotExtended',  'confirmNotExtended & open<bbUpper'),
        ('rangeExpansion',       'range > avg*1.3'),
        ('sessionRisingFilter',  'session rising'),
        ('stockNotOverextended', 'dayChange < 3%'),
        ('inTradeWindow',        '9:30-15:00'),
        ('priceAboveDayOpen',    'close > dayOpen'),
    ],
    'ACCEL_v91 (proposed)': [
        ('accelerationContext',  'macdLine[1]>0 & signal[1]>0'),
        ('sequenceReady',        'burst or cont-seq'),
        ('sustainedHistRise',    'hist rising 3+bars'),
        ('momentumTrendStrict',  'ema9>ema20 & close>ema9 (prev)'),
        ('accelHistValid_v91',   'flip+burst OR (contSeq & sustained & rising)'),
        ('momentumRSI',          'RSI[1]>50 & rising'),
        ('momentumVolume',       'vol > MA'),
        ('momentumCandles',      'prev bullish candle'),
        ('momentumVol',          'ATR or BB expanding'),
        ('momentumNotExtended',  'confirmNotExtended & open<bbUpper'),
        ('rangeExpansion',       'range > avg*1.3'),
        ('sessionRisingFilter',  'session rising'),
        ('stockNotOverextended', 'dayChange < 3%'),
        ('inTradeWindow',        '9:30-15:00'),
        ('priceAboveDayOpen',    'close > dayOpen'),
    ],
    'CONT_v9': [
        ('stockDayWinner',       'dayChange >= 0.25%'),
        ('contRsiValid',         'RSI 40-78 & rising'),
        ('contVolValid',         'vol >= MA & not excess'),
        ('contBurstReady',       '*** histShift + 1.5x burst (BLOCKS sustained uptrends) ***'),
        ('emaTrendFlexible',     'EMA/MACD trend'),
        ('adxContValid',         'ADX 18-40'),
        ('vwapRising',           'close>VWAP & VWAP rising'),
        ('macdHistRising',       'hist rising 2 bars'),
        ('closeInUpperHalf',     'close > bar midpoint'),
        ('priceAboveDayOpen',    'close > dayOpen'),
        ('stockNotOverextended', 'dayChange < 3%'),
        ('inTradeWindow',        '9:30-15:00'),
    ],
    'CONT_v91 (proposed)': [
        ('stockDayWinner',        'dayChange >= 0.25%'),
        ('contRsiValid',          'RSI 40-78 & rising'),
        ('contVolValid',          'vol >= MA & not excess'),
        ('combinedContReady_v91', 'flip+burst OR (contSeq & sustained & rising)'),
        ('emaTrendFlexible',      'EMA/MACD trend'),
        ('adxContValid',          'ADX 18-40'),
        ('vwapRising',            'close>VWAP & VWAP rising'),
        ('macdHistRising',        'hist rising 2 bars'),
        ('closeInUpperHalf',      'close > bar midpoint'),
        ('priceAboveDayOpen',     'close > dayOpen'),
        ('stockNotOverextended',  'dayChange < 3%'),
        ('inTradeWindow',         '9:30-15:00'),
    ],
}


def analyze_symbol(symbol: str, df_indicators: pd.DataFrame) -> Dict:
    """Find best candle during trade window; report what gates fail."""
    trade_df = df_indicators[df_indicators['inTradeWindow']].copy()

    if trade_df.empty:
        return {'symbol': symbol, 'status': 'NO_TRADE_WINDOW_DATA'}

    # Find if any v9 trigger fires — if so, note when
    fired_v9  = trade_df[trade_df['any_v9_trigger']]
    fired_v91 = trade_df[trade_df['any_v91_trigger']]

    # Find the "best candidate" candle — highest % day change during the session
    best_idx = trade_df['day_change'].idxmax() if trade_df['day_change'].notna().any() else trade_df.index[-1]
    best = trade_df.loc[best_idx]

    result = {
        'symbol': symbol,
        'best_time': str(best_idx),
        'best_close': round(float(best['close']), 2),
        'best_day_change_pct': round(float(best['day_change']) if not pd.isna(best['day_change']) else 0, 2),
        'v9_fired_at': [str(t) for t in fired_v9.index],
        'v91_fired_at': [str(t) for t in fired_v91.index],
        'new_fires_from_v91': [str(t) for t in fired_v91.index if t not in fired_v9.index],
    }

    # Find the FIRST candle in trade window where the signal should have fired
    # (highest day_change AND continuationSequenceReady — i.e., hist > 0.60)
    candidate = trade_df[(trade_df['day_change'] >= 0.25) & (trade_df['continuationSequenceReady'])]
    if candidate.empty:
        candidate = trade_df[trade_df['day_change'] >= 0.25]
    if candidate.empty:
        candidate = trade_df

    # Pick the earliest strong candle
    diag_idx = candidate.index[0] if not candidate.empty else best_idx
    row = trade_df.loc[diag_idx]

    result['diag_time'] = str(diag_idx)
    result['diag_macd_hist'] = round(float(row['macd_hist']), 4)
    result['diag_hist_shift'] = bool(row['histShiftPrevBar'])
    result['diag_cont_seq']   = bool(row['continuationSequenceReady'])
    result['diag_accel_ctx']  = bool(row['accelerationContext'])
    result['diag_adx']        = round(float(row['adx']), 1)
    result['diag_rsi']        = round(float(row['rsi']), 1)
    result['diag_day_change'] = round(float(row['day_change']) if not pd.isna(row['day_change']) else 0, 2)

    # Gate-by-gate failure analysis for each trigger at diag_idx
    gate_results = {}
    for trigger_name, gates in GATE_GROUPS.items():
        failures = []
        for col, desc in gates:
            try:
                val = bool(trade_df.loc[diag_idx, col])
            except KeyError:
                val = False
            if not val:
                failures.append(f"{col} ({desc})")
        gate_results[trigger_name] = failures

    result['gate_failures'] = gate_results
    return result


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 80)
    print("  PINE SCRIPT FILTER ANALYSIS — TOP GAINERS TODAY")
    print("  Simulating v9.0 gates to find what blocked top gainers")
    print("=" * 80 + "\n")

    broker = AngelOneOptionsBroker()
    if not broker.authenticate():
        print("❌ Authentication failed. Check .env credentials.")
        sys.exit(1)
    print("✅ AngelOne authenticated\n")

    # ── 1. Fetch top gainers ─────────────────────────────────────────────────
    print("Fetching top gainers (PercPriceGainers, NEAR expiry)...")
    gainers = broker.get_top_gainers(limit=20, datatype='PercPriceGainers', expiry_type='NEAR')
    if not gainers:
        print("  No PercPriceGainers data — trying PercOIGainers...")
        gainers = broker.get_top_gainers(limit=20, datatype='PercOIGainers', expiry_type='NEAR')
    if not gainers:
        print("❌ Could not fetch top gainers. Trying ALL expiry...")
        gainers = broker.get_top_gainers(limit=20, datatype='PercPriceGainers', expiry_type='ALL')

    if not gainers:
        print("❌ No gainers data available from broker. Market may be closed.")
        sys.exit(1)

    print(f"\n{'Rank':<5} {'Symbol':<20} {'Change%':>8}")
    print("-" * 35)
    for g in gainers[:20]:
        chg = g.get('change_pct')
        chg_str = f"{chg:+.2f}%" if chg is not None else "  n/a"
        print(f"{g['rank']:<5} {g['symbol']:<20} {chg_str:>8}")

    symbols = [g['symbol'] for g in gainers[:15]]
    print(f"\nAnalyzing top {len(symbols)}: {', '.join(symbols)}\n")

    # ── 2. Fetch NIFTY50 day change ──────────────────────────────────────────
    print("Fetching NIFTY50 candles for market_trend...")
    nifty_change = 0.0
    try:
        nifty_candles = broker.get_historical_data('NIFTY', interval='ONE_MINUTE', days_back=1, exchange='NSE')
        if nifty_candles and len(nifty_candles) >= 2:
            nifty_prev = nifty_candles[0]['close']
            nifty_last = nifty_candles[-1]['close']
            nifty_change = (nifty_last - nifty_prev) / nifty_prev * 100 if nifty_prev else 0
        print(f"  NIFTY50 change today: {nifty_change:+.2f}%")
    except Exception as e:
        print(f"  NIFTY fetch failed: {e} — using 0%")

    market_trend = "GOOD" if nifty_change >= 0.5 else ("NEUTRAL" if nifty_change > -0.4 else "BAD")
    print(f"  market_trend: {market_trend}\n")

    # ── 3. Download candles + run analysis ───────────────────────────────────
    today = datetime.now().strftime('%Y-%m-%d')
    results = []

    for symbol in symbols:
        print(f"  [{symbol}] downloading 1-min candles... ", end='', flush=True)
        try:
            candles = broker.get_historical_data(symbol, interval='ONE_MINUTE', days_back=1, exchange='NSE')
            if not candles or len(candles) < 50:
                print(f"insufficient data ({len(candles) if candles else 0} candles)")
                continue

            # Build DataFrame
            df = pd.DataFrame(candles)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.set_index('timestamp').sort_index()
            # Keep only today
            df = df[df.index.date == pd.Timestamp(today).date()]
            if df.empty:
                print("no today data")
                continue

            # prev_close = first bar open (approximation for day open)
            # For real prev close, use yesterday's last candle — grab from 2-day fetch
            candles_2d = broker.get_historical_data(symbol, interval='ONE_MINUTE', days_back=2, exchange='NSE')
            df_2d = pd.DataFrame(candles_2d)
            df_2d['timestamp'] = pd.to_datetime(df_2d['timestamp'])
            df_2d = df_2d.set_index('timestamp').sort_index()
            yesterday = df_2d[df_2d.index.date < pd.Timestamp(today).date()]
            prev_close = float(yesterday['close'].iloc[-1]) if not yesterday.empty else float(df['open'].iloc[0])
            day_open   = float(df['open'].iloc[0])

            print(f"{len(df)} candles | prev_close={prev_close:.2f}")

            d = build_indicators(df, prev_close, day_open, nifty_change)
            d = evaluate_pine_gates(d)
            res = analyze_symbol(symbol, d)
            results.append(res)
            time.sleep(0.3)  # rate limit

        except Exception as e:
            print(f"ERROR: {e}")

    # ── 4. Print results ──────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  RESULTS: v9.0 vs v9.1 (proposed) — per symbol")
    print("=" * 80)

    for res in results:
        sym = res['symbol']
        if 'status' in res:
            print(f"\n{sym}: {res['status']}")
            continue

        v9_fires  = res['v9_fired_at']
        v91_fires = res['new_fires_from_v91']

        print(f"\n{'─'*60}")
        print(f"  {sym}  |  day_change={res['best_day_change_pct']:+.2f}%  |  peak_close={res['best_close']}")
        print(f"  Diag candle: {res['diag_time']}  |  hist={res['diag_macd_hist']}  "
              f"rsi={res['diag_rsi']}  adx={res['diag_adx']}  "
              f"accel_ctx={res['diag_accel_ctx']}  hist_shift={res['diag_hist_shift']}  "
              f"cont_seq={res['diag_cont_seq']}")

        if v9_fires:
            print(f"  ✅ v9.0 FIRED at: {', '.join(v9_fires[:5])}")
        else:
            print(f"  ❌ v9.0 NEVER FIRED")

        if v91_fires:
            print(f"  🆕 v9.1 NEW fires: {', '.join(v91_fires[:5])}")
        elif not v9_fires:
            print(f"  ⚠️  v9.1 ALSO no fire — deeper blocker exists")

        # Show gate failures for each trigger
        for trigger, failures in res['gate_failures'].items():
            if failures:
                print(f"\n  {trigger} BLOCKED by:")
                for f in failures:
                    marker = "  ●" if "BLOCKS" in f else "  ·"
                    print(f"{marker} {f}")
            else:
                print(f"\n  {trigger} → ✅ ALL GATES PASS")

    # ── 5. Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)

    missed = [r for r in results if not r.get('v9_fired_at') and 'status' not in r]
    would_fix = [r for r in missed if r.get('new_fires_from_v91')]
    still_blocked = [r for r in missed if not r.get('new_fires_from_v91')]

    print(f"\n  Top gainers analyzed:  {len(results)}")
    print(f"  v9.0 fires:            {len(results) - len(missed)}")
    print(f"  v9.0 MISSED:           {len(missed)}")
    print(f"  Fixed by v9.1:         {len(would_fix)}  → {[r['symbol'] for r in would_fix]}")
    print(f"  Still blocked (v9.1):  {len(still_blocked)}  → {[r['symbol'] for r in still_blocked]}")

    # Common gate failures across all missed stocks
    if missed:
        all_failures = {}
        for r in missed:
            for trigger, failures in r['gate_failures'].items():
                for f in failures:
                    gate_name = f.split('(')[0].strip()
                    all_failures[gate_name] = all_failures.get(gate_name, 0) + 1

        sorted_failures = sorted(all_failures.items(), key=lambda x: -x[1])
        print(f"\n  Most common blockers across missed stocks (ACCEL_v9 + CONT_v9):")
        for gate, count in sorted_failures[:10]:
            print(f"    {count:2}x  {gate}")

    print()


if __name__ == '__main__':
    main()
