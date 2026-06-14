"""
EOD Learning Aggregator for Options Bot
Runs at 3:15 PM daily to parse all trading data and update symbol_stats.json with ML training data

Data sources:
- option_positions.json: Current open positions and pricing context
- option_pnl_history.json: Closed trades with PnL and entry context
- live_data.json: Summary statistics
- live_data_trades.csv: Detailed trade log

ML Features captured per symbol:
- Premium movements: highest premium, premium changes over time
- Moneyness: distance from strike
- Expiry distance: days to expiration
- PCR data: put-call ratio analysis (if available)
- OI tracking: open interest changes
- Trade outcomes: win/loss ratio, average PnL
- Recent form: cold/neutral/hot based on last 10 trades
- Reliability score: win rate based learning
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict
import statistics

from .optconfig import build_empty_live_data, DATA_DIR

logger = logging.getLogger(__name__)

TRADE_HISTORY_FIELDS = {
    'date',
    'won',
    'pnl',
    'pnl_percent',
    'entry_premium',
    'exit_premium',
    'highest_premium',
    'exit_reason',
    'duration_seconds',
    'quantity',
    'entry_time',
    'entry_time_bucket',
    'entry_type',
    'market_trend',
    'trend_strength',
    'confidence',
    'score',
    'momentum_score',
    'rsi_value',
    'rsi_expansion',
    'macd_hist',
    'day_change',
    'vwap_distance',
    'volume_ratio',
    'ema_spread',
    'atr_pc',
    'adx',
    'live_rsi_15m',
    'pcr',
    'iv_percentile',
    'ma_spread',
    'entry_context',
}


class EODLearningAggregator:
    """Aggregates all trading data for ML learning at end of day"""
    
    def __init__(self, base_path=None):
        if base_path is None:
            base_path = DATA_DIR
        self.base_path = Path(base_path)
        self.positions_file = self.base_path / "option_positions.json"
        self.pnl_history_file = self.base_path / "option_pnl_history.json"
        self.live_data_file = self.base_path / "live_data.json"
        self.learning_dir = self.base_path / "learning"
        self.symbol_stats_file = self.learning_dir / "symbol_stats.json"
        self.learning_dir.mkdir(exist_ok=True)
        
        logger.info(f"EOD_LEARNING_AGGREGATOR: Initialized | Path: {self.base_path}")

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            if value in (None, ""):
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _entry_time_bucket(entry_time_text: str) -> str:
        if not entry_time_text:
            return 'UNKNOWN'
        try:
            entry_time = datetime.fromisoformat(entry_time_text)
        except (TypeError, ValueError):
            return 'UNKNOWN'
        minute_bucket = (entry_time.minute // 15) * 15
        end_minute = minute_bucket + 14
        return f"{entry_time.hour:02d}:{minute_bucket:02d}-{entry_time.hour:02d}:{end_minute:02d}"

    @staticmethod
    def _summarize_bucket(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not trades:
            return {'trades': 0, 'wins': 0, 'win_rate': 0.0, 'total_pnl': 0.0, 'avg_pnl': 0.0}
        wins = [trade for trade in trades if trade.get('pnl', 0) > 0]
        total_pnl = sum(trade.get('pnl', 0) for trade in trades)
        return {
            'trades': len(trades),
            'wins': len(wins),
            'win_rate': round(len(wins) / len(trades), 4),
            'total_pnl': round(total_pnl, 2),
            'avg_pnl': round(total_pnl / len(trades), 2),
        }

    def _group_trade_performance(self, trades: List[Dict[str, Any]], field_name: str) -> Dict[str, Any]:
        buckets = defaultdict(list)
        for trade in trades:
            key = trade.get(field_name, 'UNKNOWN') or 'UNKNOWN'
            buckets[str(key).upper() if field_name in {'entry_type', 'market_trend'} else str(key)].append(trade)
        return {key: self._summarize_bucket(items) for key, items in buckets.items()}

    def _build_filter_summary(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        def summarize_numeric(field_name: str) -> Dict[str, float]:
            values = [self._safe_float(trade.get(field_name)) for trade in trades if trade.get(field_name) not in (None, '')]
            if not values:
                return {}
            return {
                'avg': round(statistics.mean(values), 4),
                'min': round(min(values), 4),
                'max': round(max(values), 4),
            }

        return {
            'confidence': summarize_numeric('confidence'),
            'score': summarize_numeric('score'),
            'rsi_value': summarize_numeric('rsi_value'),
            'rsi_expansion': summarize_numeric('rsi_expansion'),
            'macd_hist': summarize_numeric('macd_hist'),
            'momentum_score': summarize_numeric('momentum_score'),
            'trend_strength': summarize_numeric('trend_strength'),
            'day_change': summarize_numeric('day_change'),
            'vwap_distance': summarize_numeric('vwap_distance'),
            'volume_ratio': summarize_numeric('volume_ratio'),
            'ema_spread': summarize_numeric('ema_spread'),
            'atr_pc': summarize_numeric('atr_pc'),
            'adx': summarize_numeric('adx'),
            'live_rsi_15m': summarize_numeric('live_rsi_15m'),
            'pcr': summarize_numeric('pcr'),
            'iv_percentile': summarize_numeric('iv_percentile'),
            'ma_spread': summarize_numeric('ma_spread'),
        }

    def _sanitize_trade_history_record(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = {key: trade.get(key) for key in TRADE_HISTORY_FIELDS if key in trade}
        if 'entry_time_bucket' not in sanitized:
            sanitized['entry_time_bucket'] = self._entry_time_bucket(sanitized.get('entry_time', ''))
        if 'entry_context' not in sanitized or sanitized['entry_context'] is None:
            sanitized['entry_context'] = {}
        return sanitized
    
    def load_json_file(self, filepath: Path) -> Any:
        """Safely load JSON file"""
        try:
            if filepath.exists():
                with open(filepath, 'r') as f:
                    return json.load(f)
            return None
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Failed to load {filepath} | {str(e)}")
            return None
    
    def save_json_file(self, filepath: Path, data: Any) -> bool:
        """Safely save JSON file"""
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Failed to save {filepath} | {str(e)}")
            return False
    
    def extract_base_symbol(self, option_symbol: str) -> str:
        """Extract base symbol from option contract (e.g., INFY27JAN261640CE -> INFY)"""
        # Format: INFY27JAN261640CE
        # Remove expiry date and strike/type
        for i, char in enumerate(option_symbol):
            if char.isdigit() and i > 0:
                return option_symbol[:i]
        return option_symbol
    
    def analyze_closed_trades(self, filter_date: Optional[date] = None) -> Dict[str, Any]:
        """Analyze closed trades from option_pnl_history.json
        
        Args:
            filter_date: If provided, only include trades from this date (YYYY-MM-DD)
                        If None, includes ALL trades from history
        
        Usage:
            - Daily EOD: analyze_closed_trades(filter_date=datetime.now().date())
            - Manual restore: analyze_closed_trades(filter_date=None)  # All history
        """
        from datetime import datetime, date, timedelta
        
        pnl_history = self.load_json_file(self.pnl_history_file) or []
        
        # Filter trades by date if specified
        trades_by_symbol = defaultdict(list)
        total_trade_count = 0
        filtered_trade_count = 0
        
        for trade in pnl_history:
            symbol = trade.get('symbol', '')
            base_symbol = self.extract_base_symbol(symbol)
            closed_at_str = trade.get('closed_at', '')
            entry_context = trade.get('entry_context') or {}
            filter_inputs = (entry_context.get('filter_inputs') or {}) if isinstance(entry_context, dict) else {}
            alert_signal = (filter_inputs.get('alert_signal') or {}) if isinstance(filter_inputs, dict) else {}
            market_snapshot = (filter_inputs.get('market_snapshot') or {}) if isinstance(filter_inputs, dict) else {}
            
            try:
                closed_at = datetime.fromisoformat(closed_at_str).date()
                total_trade_count += 1
                
                # If filter_date specified, only include trades from that date
                # If filter_date is None, include ALL trades (for historical restore)
                if filter_date is not None and closed_at != filter_date:
                    continue  # Skip trades from other dates
                    
                filtered_trade_count += 1
            except Exception:
                # If we can't parse date, skip this trade
                logger.debug(f"EOD_LEARNING_AGGREGATOR: Could not parse date for trade: {closed_at_str}")
                continue
            
            trade_data = {
                'symbol': symbol,
                'entry_premium': trade.get('entry_premium', 0),
                'exit_premium': trade.get('exit_premium', 0),
                'pnl': trade.get('pnl', 0),
                'pnl_percent': trade.get('pnl_percent', 0),
                'duration_seconds': trade.get('duration', 0),
                'exit_reason': trade.get('exit_reason', 'UNKNOWN'),
                'closed_at': trade.get('closed_at', ''),
                'entry_time': trade.get('entry_time', ''),
                'entry_time_bucket': self._entry_time_bucket(trade.get('entry_time', '')),
                'quantity': trade.get('quantity', 1),
                # Market trend context at entry (Pine Script 7.18-E)
                'market_trend': entry_context.get('market_trend', trade.get('market_trend', 'UNKNOWN')),
                'trend_strength': self._safe_float(entry_context.get('trend_strength', trade.get('trend_strength'))),
                'entry_type': entry_context.get('entry_type', 'UNKNOWN'),
                'confidence': self._safe_float(entry_context.get('confidence')),
                'score': self._safe_float(entry_context.get('score')),
                'momentum_score': self._safe_float(entry_context.get('momentum_score', alert_signal.get('momentum_score'))),
                'rsi_value': self._safe_float(entry_context.get('rsi_value', alert_signal.get('rsi_value'))),
                'rsi_expansion': self._safe_float(entry_context.get('rsi_expansion', alert_signal.get('rsi_expansion'))),
                'macd_hist': self._safe_float(entry_context.get('macd_hist', alert_signal.get('macd_hist'))),
                'day_change': self._safe_float(entry_context.get('day_change', alert_signal.get('day_change'))),
                'vwap_distance': self._safe_float(entry_context.get('vwap_distance', alert_signal.get('vwap_distance'))),
                'volume_ratio': self._safe_float(entry_context.get('volume_ratio', alert_signal.get('volume_ratio'))),
                'ema_spread': self._safe_float(entry_context.get('ema_spread', alert_signal.get('ema_spread'))),
                'atr_pc': self._safe_float(entry_context.get('atr_pc', alert_signal.get('atr_pc'))),
                'adx': self._safe_float(entry_context.get('adx', alert_signal.get('adx'))),
                'setup_sequence': int(self._safe_float(entry_context.get('setup_sequence', alert_signal.get('setup_sequence')))),
                'live_rsi_15m': self._safe_float(market_snapshot.get('rsi_15m')),
                'pcr': self._safe_float(market_snapshot.get('pcr')),
                'iv_percentile': self._safe_float(market_snapshot.get('iv_percentile')),
                'ma_short': self._safe_float(market_snapshot.get('ma_short')),
                'ma_long': self._safe_float(market_snapshot.get('ma_long')),
                'ma_spread': self._safe_float(market_snapshot.get('ma_short')) - self._safe_float(market_snapshot.get('ma_long')),
                'slope': self._safe_float(market_snapshot.get('slope')),
                'entry_context': entry_context,
            }
            
            trades_by_symbol[base_symbol].append(trade_data)
        
        if filter_date:
            logger.info(f"EOD_LEARNING_AGGREGATOR: Analyzed closed trades for {filter_date} | "
                       f"total_available={total_trade_count} | today_only={filtered_trade_count} | "
                       f"unique_symbols={len(trades_by_symbol)}")
        else:
            logger.info(f"EOD_LEARNING_AGGREGATOR: Analyzed ALL closed trades from history | "
                       f"total_trades={total_trade_count} | "
                       f"unique_symbols={len(trades_by_symbol)}")
        
        return dict(trades_by_symbol)
    
    def analyze_open_positions(self) -> Dict[str, Any]:
        """Analyze all open positions from option_positions.json"""
        positions_data = self.load_json_file(self.positions_file) or {}
        positions = positions_data.get('positions', [])
        
        positions_by_symbol = defaultdict(list)
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            underlying = pos.get('underlying', '')
            
            position_data = {
                'symbol': symbol,
                'underlying': underlying,
                'strike': pos.get('strike', 0),
                'expiry': pos.get('expiry', ''),
                'contract_type': pos.get('contract_type', ''),
                'quantity': pos.get('quantity', 0),
                'entry_premium': pos.get('entry_premium', 0),
                'current_premium': pos.get('current_premium', 0),
                'highest_premium': pos.get('highest_premium', 0),
                'unrealized_pnl': pos.get('unrealized_pnl', 0),
                'days_to_expiry': pos.get('days_to_expiry', 0),
                'entry_time': pos.get('entry_time', ''),
                'last_updated': pos.get('last_updated', ''),
                'sl_order_price': pos.get('sl_order_price', 0),
                'underlying_alert_price': pos.get('underlying_alert_price', 0),
                'market_trend': pos.get('market_trend', 'UNKNOWN'),
                'trend_strength': self._safe_float(pos.get('trend_strength')),
                'entry_context': pos.get('entry_context', {}),
            }
            
            positions_by_symbol[underlying].append(position_data)
        
        logger.info(f"EOD_LEARNING_AGGREGATOR: Analyzed {len(positions)} open positions | "
                   f"{len(positions_by_symbol)} unique underlying symbols")
        
        return dict(positions_by_symbol)
    
    def build_symbol_stats(self, closed_trades: Dict[str, List[Any]], 
                          open_positions: Dict[str, List[Any]]) -> Dict[str, Any]:
        """Build comprehensive symbol statistics for ML training
        
        IMPORTANT: Merges with historical data, preserves trade_history across days
        """
        from optcode import optconfig
        
        # Load existing symbol stats to preserve historical data
        existing_stats = self.load_json_file(self.symbol_stats_file) or {}
        
        # Get all symbols from FO_UNIVERSE (217 symbols)
        # Ensure every symbol is present, even if no trades today
        all_fo_symbols = set(optconfig.OptionsTradingConfig.FO_UNIVERSE)
        
        # Also include any open positions or trades (shouldn't add new symbols, but be safe)
        all_symbols = all_fo_symbols | set(closed_trades.keys()) | set(open_positions.keys())
        
        symbol_stats = {}
        
        for symbol in all_symbols:
            trades = closed_trades.get(symbol, [])
            positions = open_positions.get(symbol, [])
            
            # Get historical trade history from existing stats first (for stats calculation)
            existing_trade_history = []
            if symbol in existing_stats:
                existing_trade_history = [
                    self._sanitize_trade_history_record(t)
                    for t in existing_stats[symbol].get('trade_history', [])
                    if isinstance(t, dict)
                ]
            
            # Build merged trade history (same logic as later in this method)
            # Convert existing history format to match trades format for stats calculation
            historical_trades_for_stats = [
                {
                    'pnl': t['pnl'],
                    'pnl_percent': t.get('pnl_percent', 0),
                    'entry_premium': t.get('entry_premium', 0),
                    'exit_premium': t.get('exit_premium', 0),
                    'highest_premium': t.get('highest_premium', 0),
                    'exit_reason': t.get('exit_reason', ''),
                    'duration_seconds': t.get('duration_seconds', 0),
                    'quantity': t.get('quantity', 1),
                    'entry_time': t.get('entry_time', ''),
                    'entry_time_bucket': t.get('entry_time_bucket', self._entry_time_bucket(t.get('entry_time', ''))),
                    'entry_type': t.get('entry_type', 'UNKNOWN'),
                    'market_trend': t.get('market_trend', 'UNKNOWN'),
                    'trend_strength': self._safe_float(t.get('trend_strength')),
                    'confidence': self._safe_float(t.get('confidence')),
                    'score': self._safe_float(t.get('score')),
                    'momentum_score': self._safe_float(t.get('momentum_score')),
                    'rsi_value': self._safe_float(t.get('rsi_value')),
                    'rsi_expansion': self._safe_float(t.get('rsi_expansion')),
                    'macd_hist': self._safe_float(t.get('macd_hist')),
                    'day_change': self._safe_float(t.get('day_change')),
                    'vwap_distance': self._safe_float(t.get('vwap_distance')),
                    'volume_ratio': self._safe_float(t.get('volume_ratio')),
                    'ema_spread': self._safe_float(t.get('ema_spread')),
                    'atr_pc': self._safe_float(t.get('atr_pc')),
                    'adx': self._safe_float(t.get('adx')),
                    'live_rsi_15m': self._safe_float(t.get('live_rsi_15m')),
                    'pcr': self._safe_float(t.get('pcr')),
                    'iv_percentile': self._safe_float(t.get('iv_percentile')),
                    'ma_spread': self._safe_float(t.get('ma_spread')),
                    'entry_context': t.get('entry_context', {}),
                }
                for t in existing_trade_history
            ]
            
            # Combine new trades + historical trades for statistics
            all_trades_for_stats = trades + historical_trades_for_stats
            
            # Calculate trade statistics from merged history
            wins = [t for t in all_trades_for_stats if t['pnl'] > 0]
            losses = [t for t in all_trades_for_stats if t['pnl'] < 0]
            breakevens = [t for t in all_trades_for_stats if t['pnl'] == 0]
            
            total_trades = len(all_trades_for_stats)
            total_profit = sum(t['pnl'] for t in all_trades_for_stats)
            
            # Recent form: analyze last 10 trades from merged history
            recent_trades = all_trades_for_stats[-10:]
            recent_wins = len([t for t in recent_trades if t['pnl'] > 0])
            recent_form = 'hot' if recent_wins >= 7 else ('cold' if recent_wins <= 3 else 'neutral')
            
            # Premium movement analysis from merged history
            premium_changes = [
                (t['exit_premium'] - t['entry_premium']) / t['entry_premium'] * 100
                for t in all_trades_for_stats if t['entry_premium'] > 0
            ]
            
            # Expiry distance distribution
            expiry_distances = [p['days_to_expiry'] for p in positions]
            
            # Moneyness analysis (strike vs underlying alert price)
            moneyness_values = []
            for p in positions:
                if p['underlying_alert_price'] > 0:
                    moneyness = (p['strike'] - p['underlying_alert_price']) / p['underlying_alert_price']
                    moneyness_values.append(moneyness)
            
            # Duration analysis
            durations = [t['duration_seconds'] for t in all_trades_for_stats if t['duration_seconds'] > 0]
            
            # Reliability score: based on win rate and consistency
            if total_trades > 0:
                win_rate = len(wins) / total_trades
                if win_rate >= 0.6:
                    reliability_score = 0.8
                elif win_rate >= 0.5:
                    reliability_score = 0.6
                elif win_rate >= 0.4:
                    reliability_score = 0.4
                else:
                    reliability_score = 0.2
            else:
                reliability_score = 0.5  # Default for new symbols

            # ----------------------------------------------------------------
            # PNL BY TREND: aggregate per-symbol performance split by market_trend
            # Enables future ML to weight position sizing by trend-symbol affinity
            # e.g. RELIANCE wins 70% on GOOD days → boost GOOD cap multiplier
            # ----------------------------------------------------------------
            _trend_buckets = {'GOOD': [], 'NEUTRAL': [], 'BAD': [], 'UNKNOWN': []}
            for t in all_trades_for_stats:
                _t = (t.get('market_trend') or 'UNKNOWN').strip().upper()
                _bucket = _t if _t in _trend_buckets else 'UNKNOWN'
                _trend_buckets[_bucket].append(t)

            def _trend_stats(bucket_trades):
                if not bucket_trades:
                    return {'trades': 0, 'wins': 0, 'win_rate': 0.0,
                            'total_pnl': 0.0, 'avg_pnl': 0.0}
                _wins = [t for t in bucket_trades if t['pnl'] > 0]
                return {
                    'trades':    len(bucket_trades),
                    'wins':      len(_wins),
                    'win_rate':  round(len(_wins) / len(bucket_trades), 4),
                    'total_pnl': round(sum(t['pnl'] for t in bucket_trades), 2),
                    'avg_pnl':   round(sum(t['pnl'] for t in bucket_trades) / len(bucket_trades), 2),
                }

            pnl_by_trend = {k: _trend_stats(v) for k, v in _trend_buckets.items()}
            entry_type_performance = self._group_trade_performance(all_trades_for_stats, 'entry_type')
            timing_performance = self._group_trade_performance(all_trades_for_stats, 'entry_time_bucket')
            trend_entry_type_performance = {
                trend_name: self._group_trade_performance(trend_trades, 'entry_type')
                for trend_name, trend_trades in _trend_buckets.items()
            }
            filter_summary = self._build_filter_summary(all_trades_for_stats)
            filter_summary_by_entry_type = {
                entry_type: self._build_filter_summary([trade for trade in all_trades_for_stats if trade.get('entry_type') == entry_type])
                for entry_type in sorted({trade.get('entry_type', 'UNKNOWN') for trade in all_trades_for_stats})
            }

            # best_trend: trend with most trades that has win_rate >= 0.5, else NEUTRAL
            _candidates = [
                (k, v) for k, v in pnl_by_trend.items()
                if k != 'UNKNOWN' and v['trades'] >= 3 and v['win_rate'] >= 0.5
            ]
            best_trend = max(_candidates, key=lambda x: x[1]['win_rate'])[0] \
                if _candidates else 'NEUTRAL'

            # Pre-compute confidence multiplier (used in probation logic below)
            conf_mult = 1.0 + (reliability_score - 0.5)

            # =================================================================
            # PROBATION LADDER — never lose data, earn recovery through probes
            # =================================================================
            ex = existing_stats.get(symbol, {})
            prob_status         = ex.get('probation_status', 'ACTIVE')
            prob_backoff        = ex.get('probation_backoff_days', 7)
            prob_streak         = ex.get('probation_streak', 0)
            prob_attempts       = ex.get('probation_probes_attempted', 0)
            prob_won_count      = ex.get('probation_probes_won', 0)
            prob_blocked_since  = ex.get('probation_blocked_since', None)
            prob_next_probe     = ex.get('probation_next_probe', None)
            prob_last_probe     = ex.get('probation_last_probe_date', None)
            today_str           = datetime.now().date().isoformat()

            # Detect probe trade: symbol was BLOCKED and new trades happened today
            # Guard with `today_str != prob_last_probe` to avoid double-processing
            probe_trade_today = (
                prob_status == 'BLOCKED'
                and len(trades) > 0
                and prob_next_probe is not None
                and today_str >= prob_next_probe
                and today_str != prob_last_probe
            )

            if probe_trade_today:
                probe_won_today = any(t['pnl'] > 0 for t in trades)
                prob_attempts  += len(trades)
                prob_last_probe = today_str
                if probe_won_today:
                    prob_won_count += sum(1 for t in trades if t['pnl'] > 0)
                    prob_streak    += 1
                    if prob_streak >= 3:
                        # 3 consecutive probe wins → fully rehabilitated
                        prob_status  = 'ACTIVE'
                        prob_backoff = 7   # reset for potential future block
                        logger.info(f"PROBATION: REHABILITATED | {symbol} | "
                                    f"3 consecutive probe wins | total_probes={prob_attempts}")
                    else:
                        # Good result — next probe in 5 days
                        next_dt        = datetime.now().date() + timedelta(days=5)
                        prob_next_probe = next_dt.isoformat()
                        logger.info(f"PROBATION: PROBE_WIN | {symbol} | "
                                    f"streak={prob_streak}/3 | next_probe={prob_next_probe}")
                else:
                    # Probe failed — reset streak, double the wait (max 56 days)
                    prob_streak  = 0
                    prob_backoff = min(prob_backoff * 2, 56)
                    next_dt        = datetime.now().date() + timedelta(days=prob_backoff)
                    prob_next_probe = next_dt.isoformat()
                    logger.info(f"PROBATION: PROBE_LOSS | {symbol} | "
                                f"backoff={prob_backoff}d | next_probe={prob_next_probe}")

            elif prob_status == 'ACTIVE':
                # Check if symbol should be freshly blocked
                if total_trades >= 5 and conf_mult <= 0.7:
                    prob_status        = 'BLOCKED'
                    prob_blocked_since = today_str
                    prob_streak        = 0
                    if prob_backoff < 7:
                        prob_backoff = 7
                    next_dt        = datetime.now().date() + timedelta(days=prob_backoff)
                    prob_next_probe = next_dt.isoformat()
                    cur_wr = (len(wins) / total_trades) if total_trades > 0 else 0
                    logger.info(f"PROBATION: NEWLY_BLOCKED | {symbol} | "
                                f"WR={cur_wr:.0%} | conf={conf_mult:.1f} | "
                                f"first_probe={prob_next_probe}")

            elif prob_status == 'BLOCKED':
                # Auto-recover: if win rate improved strongly without needing probes
                # (e.g. symbol was in data gap, got re-rated with new history)
                if total_trades >= 5 and conf_mult > 0.9:
                    prob_status = 'ACTIVE'
                    prob_streak = 0
                    logger.info(f"PROBATION: AUTO_RECOVERED | {symbol} | "
                                f"conf={conf_mult:.1f} -> ACTIVE")
            
            # Build new trade history from today's trades
            new_trade_history = [
                {
                    'date': t['closed_at'],
                    'won': t['pnl'] > 0,
                    'pnl': t['pnl'],
                    'pnl_percent': t['pnl_percent'],
                    'entry_premium': t['entry_premium'],
                    'exit_premium': t['exit_premium'],
                    'highest_premium': t.get('highest_premium', t['exit_premium']),
                    'exit_reason': t['exit_reason'],
                    'duration_seconds': t['duration_seconds'],
                    'quantity': t['quantity'],
                    'entry_time': t.get('entry_time', ''),
                    'entry_time_bucket': t.get('entry_time_bucket', 'UNKNOWN'),
                    'entry_type': t.get('entry_type', 'UNKNOWN'),
                    'market_trend': t.get('market_trend', 'UNKNOWN'),
                    'trend_strength': t.get('trend_strength', 0),
                    'confidence': t.get('confidence', 0),
                    'score': t.get('score', 0),
                    'momentum_score': t.get('momentum_score', 0),
                    'rsi_value': t.get('rsi_value', 0),
                    'rsi_expansion': t.get('rsi_expansion', 0),
                    'macd_hist': t.get('macd_hist', 0),
                    'day_change': t.get('day_change', 0),
                    'vwap_distance': t.get('vwap_distance', 0),
                    'volume_ratio': t.get('volume_ratio', 0),
                    'ema_spread': t.get('ema_spread', 0),
                    'atr_pc': t.get('atr_pc', 0),
                    'adx': t.get('adx', 0),
                    'live_rsi_15m': t.get('live_rsi_15m', 0),
                    'pcr': t.get('pcr', 0),
                    'iv_percentile': t.get('iv_percentile', 0),
                    'ma_spread': t.get('ma_spread', 0),
                    'entry_context': t.get('entry_context', {}),
                }
                for t in trades
            ]
            
            # Merge: prepend new trades, keep last 100 total
            merged_trade_history = new_trade_history + existing_trade_history
            merged_trade_history = merged_trade_history[:100]  # Keep last 100 trades
            
            # Build symbol record with merged history
            symbol_record = {
                'symbol': symbol,
                'last_updated': datetime.now().isoformat(),
                
                # Trade statistics
                'total_trades': total_trades,
                'wins': len(wins),
                'losses': len(losses),
                'breakevens': len(breakevens),
                'total_profit': total_profit,
                'avg_profit_per_trade': total_profit / total_trades if total_trades > 0 else 0,
                'avg_win': statistics.mean([t['pnl'] for t in wins]) if wins else 0,
                'avg_loss': statistics.mean([t['pnl'] for t in losses]) if losses else 0,
                'win_rate': len(wins) / total_trades if total_trades > 0 else 0,
                'win_rate_last_10': recent_wins / len(recent_trades) if recent_trades else 0,
                'recent_form': recent_form,
                'reliability_score': reliability_score,
                'confidence_multiplier': conf_mult,

                # Probation ladder state (persisted across days, never erased)
                'probation_status':          prob_status,       # 'ACTIVE' or 'BLOCKED'
                'probation_blocked_since':   prob_blocked_since,
                'probation_next_probe':      prob_next_probe,
                'probation_backoff_days':    prob_backoff,
                'probation_streak':          prob_streak,       # consecutive probe wins (3 = ACTIVE)
                'probation_probes_attempted': prob_attempts,
                'probation_probes_won':      prob_won_count,
                'probation_last_probe_date': prob_last_probe,

                # Premium movement analysis
                'premium_changes_percent': {
                    'avg': statistics.mean(premium_changes) if premium_changes else 0,
                    'min': min(premium_changes) if premium_changes else 0,
                    'max': max(premium_changes) if premium_changes else 0,
                },
                
                # Expiry analysis
                'expiry_analysis': {
                    'avg_days_to_expiry': statistics.mean(expiry_distances) if expiry_distances else 0,
                    'min_days': min(expiry_distances) if expiry_distances else 0,
                    'max_days': max(expiry_distances) if expiry_distances else 0,
                    'open_positions': len(positions),
                },
                
                # Moneyness analysis
                'moneyness_analysis': {
                    'avg_moneyness': statistics.mean(moneyness_values) if moneyness_values else 0,
                    'otm_ratio': len([m for m in moneyness_values if m < 0]) / len(moneyness_values) 
                                if moneyness_values else 0,
                },
                
                # Duration analysis
                'duration_analysis': {
                    'avg_duration_seconds': statistics.mean(durations) if durations else 0,
                    'avg_duration_minutes': statistics.mean(durations) / 60 if durations else 0,
                    'min_duration_seconds': min(durations) if durations else 0,
                    'max_duration_seconds': max(durations) if durations else 0,
                },
                
                # Exit reasons distribution
                'exit_reasons': self._count_exit_reasons(trades),

                # Market trend performance split (Pine Script 7.18-E)
                # ML training: learn which symbols win on which trend days
                'pnl_by_trend': pnl_by_trend,
                'best_trend':   best_trend,
                'entry_type_performance': entry_type_performance,
                'timing_performance': timing_performance,
                'trend_entry_type_performance': trend_entry_type_performance,
                'filter_summary': filter_summary,
                'filter_summary_by_entry_type': filter_summary_by_entry_type,

                # Trade history with merged data (IMPORTANT: preserves historical data)
                'trade_history': merged_trade_history,
            }
            
            symbol_stats[symbol] = symbol_record
        
        logger.info(f"EOD_LEARNING_AGGREGATOR: Built stats for {len(symbol_stats)} symbols | "
                   f"Merged with {len(existing_stats)} existing symbol records")
        return symbol_stats
    
    
    def _count_exit_reasons(self, trades: List[Any]) -> Dict[str, int]:
        """Count exit reasons distribution"""
        reasons = defaultdict(int)
        for trade in trades:
            reason = trade.get('exit_reason', 'UNKNOWN')
            reasons[reason] += 1
        return dict(reasons)
    
    def archive_learning_files(self) -> bool:
        """Archive learning source files to prevent duplicate learning
        
        Moves option_pnl_history.json and option_positions.json to data/archive/
        with date-stamped filenames to preserve history and prevent re-learning
        
        Returns:
            True if archival successful, False otherwise
        """
        import shutil
        
        archive_dir = self.base_path / "archive"
        archive_dir.mkdir(exist_ok=True)
        
        today = datetime.now().date().isoformat()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        try:
            # Archive option_pnl_history.json
            if self.pnl_history_file.exists():
                archive_pnl = archive_dir / f"option_pnl_history_{today}_{timestamp}.json"
                shutil.copy2(self.pnl_history_file, archive_pnl)
                logger.info(f"EOD_LEARNING_AGGREGATOR: Archived pnl_history to {archive_pnl.name}")
            
            # Archive option_positions.json
            if self.positions_file.exists():
                archive_pos = archive_dir / f"option_positions_{today}_{timestamp}.json"
                shutil.copy2(self.positions_file, archive_pos)
                logger.info(f"EOD_LEARNING_AGGREGATOR: Archived positions to {archive_pos.name}")
            
            # Clear the source files for next trading day (reset to empty state)
            # option_pnl_history.json stays as is (it's cumulative)
            # option_positions.json gets cleared for new day
            empty_positions = {
                "timestamp": datetime.now().isoformat(),
                "positions": []
            }
            with open(self.positions_file, 'w') as f:
                json.dump(empty_positions, f, indent=2)
            logger.info("EOD_LEARNING_AGGREGATOR: Cleared option_positions.json for next trading day")
            
            return True
            
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Failed to archive learning files | {str(e)}")
            return False
    
    def reset_live_data_files(self) -> bool:
        """Reset live_data files to clean state for next trading day
        
        Clears:
        - live_data.json (resets to empty summary)
        - live_data_trades.csv (clears trade table)
        - live_data_tables.md (clears markdown table)
        - option_positions.json (already done in archive_learning_files, but ensure clean)
        
        Returns:
            True if reset successful, False otherwise
        """
        try:
            # Reset live_data.json to empty summary
            empty_live_data = build_empty_live_data(market_status='CLOSED')
            
            live_data_file = self.base_path / 'live_data.json'
            with open(live_data_file, 'w') as f:
                json.dump(empty_live_data, f, indent=2)
            logger.info("EOD_LEARNING_AGGREGATOR: Reset live_data.json for next trading day")
            
            # Reset live_data_trades.csv
            csv_file = self.base_path / 'live_data_trades.csv'
            empty_csv = "Status,Timestamp\nRESET,{}".format(datetime.now().isoformat())
            with open(csv_file, 'w') as f:
                f.write(empty_csv)
            logger.info("EOD_LEARNING_AGGREGATOR: Reset live_data_trades.csv for next trading day")
            
            # Reset live_data_tables.md
            md_file = self.base_path / 'live_data_tables.md'
            empty_md = """# Live Trading Data

**Last Updated**: {}
**Status**: Reset for next trading day

No trades yet.
""".format(datetime.now().isoformat())
            with open(md_file, 'w') as f:
                f.write(empty_md)
            logger.info("EOD_LEARNING_AGGREGATOR: Reset live_data_tables.md for next trading day")
            
            return True
            
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Failed to reset live_data files | {str(e)}")
            return False
    
    def aggregate(self) -> Dict[str, Any]:
        """Main aggregation function - runs the complete EOD learning update
        
        Updates symbol_stats.json from option_pnl_history.json for the current day.
        Processes all closed trades from that day's trading, while preserving
        accumulated knowledge across trading days through merge logic.
        """
        from datetime import date
        logger.info("EOD_LEARNING_AGGREGATOR: Starting end-of-day aggregation")
        
        try:
            # Step 1: Analyze closed trades from option_pnl_history.json
            # For the current day (filter_date ensures we get today's trades only)
            today = date.today()
            closed_trades = self.analyze_closed_trades(filter_date=today)
            
            # Step 2: Analyze open positions
            open_positions = self.analyze_open_positions()
            
            # Step 3: Build symbol statistics
            symbol_stats = self.build_symbol_stats(closed_trades, open_positions)
            
            # Step 4: Save to symbol_stats.json
            success = self.save_json_file(self.symbol_stats_file, symbol_stats)
            
            if success:
                logger.info(f"EOD_LEARNING_AGGREGATOR: Successfully saved {len(symbol_stats)} "
                           f"symbol stats to {self.symbol_stats_file}")
                
                # Step 5: Archive learning source files to prevent duplicate learning
                self.archive_learning_files()
                
                # Step 6: Reset live_data files for next trading day
                self.reset_live_data_files()
                
                return {
                    'status': 'success',
                    'symbols_processed': len(symbol_stats),
                    'closed_trades_analyzed': sum(len(trades) for trades in closed_trades.values()),
                    'open_positions_analyzed': sum(len(positions) for positions in open_positions.values()),
                    'timestamp': datetime.now().isoformat(),
                    'files_archived': True,
                    'live_data_reset': True,
                }
            else:
                logger.error("EOD_LEARNING_AGGREGATOR: Failed to save symbol stats")
                return {
                    'status': 'failed',
                    'error': 'Failed to save symbol stats',
                }
        
        except Exception as e:
            logger.error(f"EOD_LEARNING_AGGREGATOR: Aggregation failed | {str(e)}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e),
            }


def run_eod_learning():
    """Execute end-of-day learning aggregation"""
    aggregator = EODLearningAggregator()
    return aggregator.aggregate()


if __name__ == "__main__":
    # For testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    result = run_eod_learning()
    print(json.dumps(result, indent=2))
