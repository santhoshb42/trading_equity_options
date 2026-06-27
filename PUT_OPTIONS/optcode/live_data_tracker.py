"""
Real-Time Live Trading Data Tracker

Maintains live_data.json with:
1. Summary statistics (budget, trades, PNL, etc.)
2. Individual trade details with all current metrics
3. Auto-updated every monitoring cycle

Output: /root/santhosh/trading/ITM_put_options/data/live_data.json
"""

import json
import tempfile
import os
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
import threading

from .optconfig import BASE_DIR, DATA_DIR, OptionsCapitalConfig, OptionsTradingConfig, build_empty_live_data, get_market_status
from .optlogging import logger

# =============================================================================
# Live Data Tracker
# =============================================================================

class LiveDataTracker:
    """
    Real-time trading statistics tracker
    
    Maintains live_data.json with:
    - Daily trading summary (budget, trades, PNL)
    - Individual trade details (entry, current, exit info)
    - Updated every monitoring cycle
    - THREAD-SAFE writes with atomic file operations
    """
    
    def __init__(self):
        self.data_dir = DATA_DIR
        self.live_data_file = self.data_dir / 'live_data.json'
        self.live_data_trades_file = self.data_dir / 'live_data_trades.csv'
        self.telemetry_file = self.data_dir / 'trial_sl_premium_snapshots.jsonl'
        self.candle_telemetry_file = self.data_dir / 'trial_sl_candle_context.jsonl'
        self.post_exit_results_file = self.data_dir / 'trial_sl_post_exit_results.jsonl'
        self.post_exit_state_file = self.data_dir / 'trial_sl_post_exit_watchers.json'
        self.trading_mode = getattr(OptionsTradingConfig, 'TRADING_MODE', 'PAPER')
        self.max_daily_budget = getattr(OptionsCapitalConfig, 'MAX_DAILY_BUDGET', 100000)
        self.snapshot_interval_seconds = max(1, int(os.getenv('OPTIONS_PREMIUM_SNAPSHOT_INTERVAL_SECONDS', '3')))
        self.post_exit_poll_interval_seconds = max(1, int(os.getenv('OPTIONS_POST_EXIT_POLL_INTERVAL_SECONDS', '5')))
        self.post_exit_watch_seconds = max(30, int(os.getenv('OPTIONS_POST_EXIT_WATCH_SECONDS', '300')))
        self.post_exit_checkpoints = [30, 120, 300, 600]
        self.enable_candle_telemetry = os.getenv('OPTIONS_ENABLE_CANDLE_TELEMETRY', 'true').lower() == 'true'
        self.candle_interval = os.getenv('OPTIONS_CANDLE_TELEMETRY_INTERVAL', 'ONE_MINUTE')
        
        # Thread-safe lock for file writes (critical for preventing corruption)
        self._file_lock = threading.RLock()
        self._last_saved_signature = None
        self._last_save_changed = False
        self._last_snapshot_at: Dict[str, datetime] = {}
        self._post_exit_watchers: Dict[str, Dict[str, Any]] = {}
        self._candle_context_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._candle_context_cache_max = 120  # ~2 hours × 60 underlyings before eviction
        
        # Initialize live data structure
        self.live_data = build_empty_live_data(market_status=get_market_status())
        
        # Load existing data from file if it exists
        if self.live_data_file.exists():
            try:
                with self._file_lock:
                    with open(self.live_data_file, 'r') as f:
                        existing_data = json.load(f)
                        # Preserve existing trades
                        if 'trades' in existing_data:
                            self.live_data['trades'] = existing_data['trades']
                        if 'index_summary' in existing_data:
                            self.live_data['index_summary'] = existing_data['index_summary']
                        if 'non_index_summary' in existing_data:
                            self.live_data['non_index_summary'] = existing_data['non_index_summary']
                        self._last_saved_signature = self._build_signature(existing_data)
                        logger.info(f"LIVE_DATA_TRACKER: Loaded {len(self.live_data['trades'])} existing trades from file")
            except Exception as e:
                logger.warning(f"LIVE_DATA_TRACKER: Failed to load existing data | {str(e)}")

        self._load_post_exit_watchers()
        
        logger.info("LIVE_DATA_TRACKER: INITIALIZED")

    def _append_jsonl(self, file_path: Path, payload: Dict[str, Any]) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(payload, separators=(',', ':')) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def _load_post_exit_watchers(self) -> None:
        if not self.post_exit_state_file.exists():
            return
        try:
            with self._file_lock:
                with open(self.post_exit_state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._post_exit_watchers = data
        except Exception as e:
            logger.warning(f"LIVE_DATA_TRACKER: POST_EXIT_LOAD_FAILED | {str(e)}")
            self._post_exit_watchers = {}

    def _save_post_exit_watchers(self) -> None:
        with self._file_lock:
            self.post_exit_state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.post_exit_state_file, 'w', encoding='utf-8') as f:
                json.dump(self._post_exit_watchers, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

    @staticmethod
    def _minute_bucket_key(underlying: str, bucket_dt: Optional[datetime] = None) -> str:
        effective_dt = bucket_dt or datetime.now()
        return f"{underlying}:{effective_dt.strftime('%Y-%m-%dT%H:%M')}"

    @staticmethod
    def _build_candle_metrics(candle: Dict[str, Any]) -> Dict[str, Any]:
        open_price = float(candle.get('open', 0.0) or 0.0)
        high_price = float(candle.get('high', 0.0) or 0.0)
        low_price = float(candle.get('low', 0.0) or 0.0)
        close_price = float(candle.get('close', 0.0) or 0.0)
        candle_range = max(high_price - low_price, 0.0)
        body = close_price - open_price
        upper_wick = max(high_price - max(open_price, close_price), 0.0)
        lower_wick = max(min(open_price, close_price) - low_price, 0.0)

        return {
            'body': round(body, 4),
            'range': round(candle_range, 4),
            'direction': 'up' if body > 0 else 'down' if body < 0 else 'flat',
            'body_pct_of_range': round((abs(body) / candle_range * 100.0), 2) if candle_range > 0 else None,
            'upper_wick_pct_of_range': round((upper_wick / candle_range * 100.0), 2) if candle_range > 0 else None,
            'lower_wick_pct_of_range': round((lower_wick / candle_range * 100.0), 2) if candle_range > 0 else None,
        }

    def _fetch_candle_context(self, underlying: str) -> Optional[Dict[str, Any]]:
        if not self.enable_candle_telemetry or not underlying:
            return None

        cache_key = self._minute_bucket_key(underlying)
        if cache_key in self._candle_context_cache:
            return self._candle_context_cache[cache_key]

        try:
            from .angelone_options import get_options_broker
            broker = get_options_broker()
        except Exception as e:
            logger.debug(f"LIVE_DATA_TRACKER: CANDLE_BROKER_UNAVAILABLE | {underlying} | {str(e)}")
            self._candle_context_cache[cache_key] = None
            return None

        try:
            candles = broker.get_historical_data(
                underlying,
                interval=self.candle_interval,
                days_back=1,
                exchange='NSE',
            )
            if not candles:
                self._candle_context_cache[cache_key] = None
                return None

            latest_candle = dict(candles[-1])
            payload = {
                'underlying': underlying,
                'interval': self.candle_interval,
                'candle': {
                    'timestamp': latest_candle.get('timestamp'),
                    'open': round(float(latest_candle.get('open', 0.0) or 0.0), 4),
                    'high': round(float(latest_candle.get('high', 0.0) or 0.0), 4),
                    'low': round(float(latest_candle.get('low', 0.0) or 0.0), 4),
                    'close': round(float(latest_candle.get('close', 0.0) or 0.0), 4),
                    'volume': int(latest_candle.get('volume', 0) or 0),
                },
                'metrics': self._build_candle_metrics(latest_candle),
            }
            self._candle_context_cache[cache_key] = payload
            # Evict oldest entries to prevent unbounded growth (grows 1 entry/min/symbol)
            if len(self._candle_context_cache) > self._candle_context_cache_max:
                oldest = min(self._candle_context_cache, key=lambda k: k)
                del self._candle_context_cache[oldest]
            return payload
        except Exception as e:
            logger.debug(f"LIVE_DATA_TRACKER: CANDLE_FETCH_FAILED | {underlying} | {str(e)}")
            self._candle_context_cache[cache_key] = None
            return None

    def record_candle_context(
        self,
        *,
        event_type: str,
        symbol: str,
        underlying: str,
        option_premium: Optional[float] = None,
        elapsed_seconds: Optional[int] = None,
        trial_sl_price: Optional[float] = None,
        trail_profile: Optional[str] = None,
        trail_activation_threshold: Optional[float] = None,
        trailing_gap: Optional[float] = None,
        market_trend: Optional[str] = None,
        trend_strength: Optional[float] = None,
        exit_reason: Optional[str] = None,
        gain_percent: Optional[float] = None,
    ) -> None:
        candle_context = self._fetch_candle_context(underlying)
        if candle_context is None:
            return

        payload = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'symbol': symbol,
            'underlying': underlying,
            'option_premium': round(float(option_premium), 4) if option_premium is not None else None,
            'elapsed_seconds': elapsed_seconds,
            'trial_sl_price': round(float(trial_sl_price), 4) if trial_sl_price is not None else None,
            'trail_profile': trail_profile,
            'trail_activation_threshold': trail_activation_threshold,
            'trailing_gap': trailing_gap,
            'market_trend': market_trend,
            'trend_strength': trend_strength,
            'exit_reason': exit_reason,
            'gain_percent': round(float(gain_percent), 4) if gain_percent is not None else None,
            'candle_context': candle_context,
        }
        with self._file_lock:
            self._append_jsonl(self.candle_telemetry_file, payload)

    def _record_open_snapshot(
        self,
        *,
        symbol: str,
        current_premium: float,
        highest_premium: float,
        lowest_premium: float,
        quantity: int,
        trial_sl_enabled: bool,
        trial_sl_price: Optional[float],
        hard_sl_price: Optional[float],
        trial_sl_updates: int,
        trail_profile: Optional[str],
        trail_activation_threshold: Optional[float],
        trailing_gap: Optional[float],
        market_trend: Optional[str],
        trend_strength: Optional[float],
    ) -> None:
        now = datetime.now()
        last_snapshot = self._last_snapshot_at.get(symbol)
        if last_snapshot and (now - last_snapshot).total_seconds() < self.snapshot_interval_seconds:
            return

        self._last_snapshot_at[symbol] = now
        payload = {
            'timestamp': now.isoformat(),
            'phase': 'open',
            'symbol': symbol,
            'current_premium': round(current_premium, 4),
            'highest_premium': round(highest_premium, 4),
            'lowest_premium': round(lowest_premium, 4),
            'quantity': quantity,
            'trial_sl_enabled': bool(trial_sl_enabled),
            'trial_sl_price': round(trial_sl_price, 4) if trial_sl_price is not None else None,
            'hard_sl_price': round(hard_sl_price, 4) if hard_sl_price is not None else None,
            'trial_sl_updates': int(trial_sl_updates or 0),
            'trail_profile': trail_profile,
            'trail_activation_threshold': trail_activation_threshold,
            'trailing_gap': trailing_gap,
            'market_trend': market_trend,
            'trend_strength': trend_strength,
        }
        with self._file_lock:
            self._append_jsonl(self.telemetry_file, payload)

    def _start_post_exit_watch(
        self,
        *,
        symbol: str,
        underlying: str,
        exit_time: str,
        exit_premium: float,
        exit_reason: str,
        quantity: int,
        trail_profile: Optional[str],
        trail_activation_threshold: Optional[float],
        trailing_gap: Optional[float],
        market_trend: Optional[str],
        trend_strength: Optional[float],
    ) -> None:
        checkpoints = {
            str(seconds): None for seconds in self.post_exit_checkpoints if seconds <= self.post_exit_watch_seconds
        }
        watcher = {
            'symbol': symbol,
            'underlying': underlying,
            'exit_time': exit_time,
            'exit_premium': float(exit_premium),
            'exit_reason': exit_reason,
            'quantity': quantity,
            'trail_profile': trail_profile,
            'trail_activation_threshold': trail_activation_threshold,
            'trailing_gap': trailing_gap,
            'market_trend': market_trend,
            'trend_strength': trend_strength,
            'max_after_exit': float(exit_premium),
            'min_after_exit': float(exit_premium),
            'last_polled_at': None,
            'checkpoints': checkpoints,
        }
        with self._file_lock:
            self._post_exit_watchers[symbol] = watcher
            self._append_jsonl(self.telemetry_file, {
                'timestamp': datetime.now().isoformat(),
                'phase': 'post_exit_start',
                'symbol': symbol,
                'exit_premium': round(exit_premium, 4),
                'exit_reason': exit_reason,
                'trail_profile': trail_profile,
                'trail_activation_threshold': trail_activation_threshold,
                'trailing_gap': trailing_gap,
            })
            self._save_post_exit_watchers()

    def poll_post_exit_tracking(self) -> None:
        if not self._post_exit_watchers:
            return

        try:
            from .angelone_options import get_options_broker
            broker = get_options_broker()
        except Exception as e:
            logger.debug(f"LIVE_DATA_TRACKER: POST_EXIT_BROKER_UNAVAILABLE | {str(e)}")
            return

        now = datetime.now()
        completed_symbols = []

        for symbol, watcher in list(self._post_exit_watchers.items()):
            last_polled_at = watcher.get('last_polled_at')
            if last_polled_at:
                try:
                    last_polled_dt = datetime.fromisoformat(last_polled_at)
                    if (now - last_polled_dt).total_seconds() < self.post_exit_poll_interval_seconds:
                        continue
                except Exception:
                    pass

            try:
                exit_dt = datetime.fromisoformat(watcher['exit_time'])
            except Exception:
                completed_symbols.append(symbol)
                continue

            market_data = broker.get_market_data(symbol, exchange='NFO') or {}
            ltp = market_data.get('ltp')
            if ltp is None:
                continue

            elapsed = int((now - exit_dt).total_seconds())
            watcher['last_polled_at'] = now.isoformat()
            watcher['max_after_exit'] = max(float(watcher.get('max_after_exit', ltp)), float(ltp))
            watcher['min_after_exit'] = min(float(watcher.get('min_after_exit', ltp)), float(ltp))

            checkpoints = watcher.get('checkpoints', {})
            for seconds_str, checkpoint in checkpoints.items():
                seconds = int(seconds_str)
                if checkpoint is None and elapsed >= seconds:
                    checkpoints[seconds_str] = {
                        'timestamp': now.isoformat(),
                        'premium': round(float(ltp), 4),
                    }
                    self.record_candle_context(
                        event_type='post_exit_checkpoint',
                        symbol=symbol,
                        underlying=watcher.get('underlying') or symbol,
                        option_premium=float(ltp),
                        elapsed_seconds=elapsed,
                        trail_profile=watcher.get('trail_profile'),
                        trail_activation_threshold=watcher.get('trail_activation_threshold'),
                        trailing_gap=watcher.get('trailing_gap'),
                        market_trend=watcher.get('market_trend'),
                        trend_strength=watcher.get('trend_strength'),
                        exit_reason=watcher.get('exit_reason'),
                    )

            with self._file_lock:
                self._append_jsonl(self.telemetry_file, {
                    'timestamp': now.isoformat(),
                    'phase': 'post_exit',
                    'symbol': symbol,
                    'elapsed_seconds': elapsed,
                    'premium': round(float(ltp), 4),
                    'max_after_exit': round(float(watcher['max_after_exit']), 4),
                    'min_after_exit': round(float(watcher['min_after_exit']), 4),
                    'trail_profile': watcher.get('trail_profile'),
                })

            if elapsed >= self.post_exit_watch_seconds:
                result = {
                    'completed_at': now.isoformat(),
                    'symbol': symbol,
                    'watch_seconds': self.post_exit_watch_seconds,
                    'exit_time': watcher['exit_time'],
                    'exit_premium': round(float(watcher['exit_premium']), 4),
                    'exit_reason': watcher.get('exit_reason'),
                    'max_after_exit': round(float(watcher['max_after_exit']), 4),
                    'min_after_exit': round(float(watcher['min_after_exit']), 4),
                    'checkpoints': watcher.get('checkpoints', {}),
                    'trail_profile': watcher.get('trail_profile'),
                    'trail_activation_threshold': watcher.get('trail_activation_threshold'),
                    'trailing_gap': watcher.get('trailing_gap'),
                    'market_trend': watcher.get('market_trend'),
                    'trend_strength': watcher.get('trend_strength'),
                }
                with self._file_lock:
                    self._append_jsonl(self.post_exit_results_file, result)
                completed_symbols.append(symbol)

        if completed_symbols:
            with self._file_lock:
                for symbol in completed_symbols:
                    self._post_exit_watchers.pop(symbol, None)
                self._save_post_exit_watchers()

    def _build_signature(self, data: Dict[str, Any]) -> str:
        """Build a stable signature for change detection, ignoring wall-clock timestamp churn."""
        ignored_keys = {'timestamp', 'live_data_json_updated_at', 'live_data_trades_csv_updated_at'}
        comparable_data = {
            key: value
            for key, value in data.items()
            if key not in ignored_keys
        }
        return hashlib.sha256(
            json.dumps(comparable_data, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()

    @staticmethod
    def _get_file_updated_at(file_path: Path) -> Optional[str]:
        try:
            if file_path.exists():
                return datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
        except OSError:
            return None
        return None

    def _write_json_preserve_inode(self, output_data: Dict[str, Any]) -> None:
        """Write JSON in place so editor tabs continue following the file.

        `os.replace()` is great for crash safety, but it swaps the inode and VS Code can
        keep showing a stale buffer for frequently regenerated files like live_data.json.
        For this runtime artifact, prefer a locked in-place rewrite with flush+fsync.
        """
        serialized = json.dumps(output_data, indent=2)

        self.live_data_file.parent.mkdir(parents=True, exist_ok=True)
        if self.live_data_file.exists():
            with open(self.live_data_file, 'r+', encoding='utf-8') as f:
                f.seek(0)
                f.write(serialized)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
            return

        with open(self.live_data_file, 'w', encoding='utf-8') as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())

    @staticmethod
    def _combine_summaries(index_summary: Dict[str, Any], non_index_summary: Dict[str, Any], total_budget: float) -> Dict[str, Any]:
        budget_used = float(index_summary.get('budget_used', 0.0)) + float(non_index_summary.get('budget_used', 0.0))
        total_trades = int(index_summary.get('trades_today', 0)) + int(non_index_summary.get('trades_today', 0))
        ongoing_trades = int(index_summary.get('ongoing_trades', 0)) + int(non_index_summary.get('ongoing_trades', 0))
        closed_trades = int(index_summary.get('closed_trades', 0)) + int(non_index_summary.get('closed_trades', 0))
        winning_trades = int(index_summary.get('winning_trades', 0)) + int(non_index_summary.get('winning_trades', 0))
        losing_trades = int(index_summary.get('losing_trades', 0)) + int(non_index_summary.get('losing_trades', 0))
        total_pnl = float(index_summary.get('total_pnl', 0.0)) + float(non_index_summary.get('total_pnl', 0.0))
        unrealized_pnl = float(index_summary.get('unrealized_pnl', 0.0)) + float(non_index_summary.get('unrealized_pnl', 0.0))
        realized_pnl = float(index_summary.get('realized_pnl', 0.0)) + float(non_index_summary.get('realized_pnl', 0.0))
        win_rate = (winning_trades / closed_trades * 100) if closed_trades > 0 else 0.0
        total_pnl_percent = (total_pnl / budget_used * 100) if budget_used > 0 else 0.0
        unrealized_pnl_percent = (unrealized_pnl / budget_used * 100) if budget_used > 0 else 0.0
        realized_pnl_percent = (realized_pnl / budget_used * 100) if budget_used > 0 else 0.0
        return {
            'total_budget': round(total_budget, 2),
            'budget_used': round(budget_used, 2),
            'budget_remaining': round(max(0.0, total_budget - budget_used), 2),
            'budget_used_percent': round((budget_used / total_budget * 100) if total_budget > 0 else 0.0, 2),
            'total_trades_today': total_trades,
            'ongoing_trades': ongoing_trades,
            'closed_trades': closed_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate_percent': round(win_rate, 2),
            'total_pnl': round(total_pnl, 2),
            'total_pnl_percent': round(total_pnl_percent, 2),
            'unrealized_pnl': round(unrealized_pnl, 2),
            'unrealized_pnl_percent': round(unrealized_pnl_percent, 2),
            'realized_pnl': round(realized_pnl, 2),
            'realized_pnl_percent': round(realized_pnl_percent, 2),
        }
    
    def update_summary(self,
                      total_budget: float,
                      budget_used: float,
                      max_positions: int,
                      total_trades: int,
                      ongoing_count: int,
                      closed_count: int,
                      winning_count: int,
                      losing_count: int,
                      total_pnl: float,
                      avg_win: float = 0.0,
                      avg_loss: float = 0.0,
                      largest_win: float = 0.0,
                      largest_loss: float = 0.0,
                      market_status: str = 'OPEN') -> None:
        """
        Update summary statistics
        
        Args:
            total_budget: Total daily budget allocated
            budget_used: Budget consumed by ongoing trades
            max_positions: Max positions allowed
            total_trades: Total trades executed today
            ongoing_count: Currently open positions
            closed_count: Closed positions today
            winning_count: Winning trades count
            losing_count: Losing trades count
            total_pnl: Total P&L across all trades
            avg_win: Average win amount
            avg_loss: Average loss amount
            largest_win: Largest winning trade
            largest_loss: Largest losing trade
            market_status: OPEN or CLOSED
        """
        summary = self.live_data['summary']
        
        # Budget tracking
        summary['total_budget'] = total_budget
        summary['budget_used'] = budget_used
        summary['budget_remaining'] = max(0, total_budget - budget_used)
        summary['budget_used_percent'] = (budget_used / total_budget * 100) if total_budget > 0 else 0.0
        
        # Position tracking
        summary['total_trades_today'] = total_trades
        summary['ongoing_trades'] = ongoing_count
        summary['closed_trades'] = closed_count
        
        # Win/loss tracking
        summary['winning_trades'] = winning_count
        summary['losing_trades'] = losing_count
        win_rate = (winning_count / total_trades * 100) if total_trades > 0 else 0.0
        summary['win_rate_percent'] = round(win_rate, 2)
        
        # PNL tracking
        summary['total_pnl'] = round(total_pnl, 2)
        budget_used = summary.get('budget_used', 0.0)
        pnl_percent = (total_pnl / budget_used * 100) if budget_used > 0 else 0.0
        summary['total_pnl_percent'] = round(pnl_percent, 2)
        summary['avg_win'] = round(avg_win, 2)
        summary['avg_loss'] = round(avg_loss, 2)
        summary['largest_win'] = round(largest_win, 2)
        summary['largest_loss'] = round(largest_loss, 2)
        
        # Market status
        self.live_data['market_status'] = market_status
        self.live_data['timestamp'] = datetime.now().isoformat()
    
    def add_trade(self,
                 symbol: str,
                 underlying: str,
                 strike: float,
                 contract_type: str,  # CE or PE
                 action: str,  # BUY or SELL
                 quantity: int,
                 entry_time: str,
                 entry_premium: float,
                 entry_greeks: Optional[Dict[str, float]] = None,
                 entry_iv: float = 0.0,
                 underlying_alert_price: Optional[float] = None,
                 trade_id: Optional[str] = None) -> None:
        """
        Add or update a trade in live data
        
        Args:
            symbol: Full symbol (e.g., BANKNIFTY25DEC1900CE)
            underlying: Underlying asset (e.g., BANKNIFTY)
            strike: Strike price
            contract_type: CE or PE
            action: BUY or SELL
            quantity: Lot size
            entry_time: Entry time (ISO format)
            entry_premium: Premium paid at entry
            entry_greeks: Greeks dict {delta, gamma, theta, vega}
            entry_iv: IV at entry
            underlying_alert_price: Alert price that triggered trade
            trade_id: Unique trade identifier
        """
        trade_record = {
            'trade_id': trade_id or f"{symbol}_{entry_time}",
            'symbol': symbol,
            'underlying': underlying,
            'strike': round(strike, 2),
            'contract_type': contract_type,
            'action': action,
            'quantity': quantity,
            'entry_time': entry_time,
            'entry_premium': round(entry_premium, 2),
            'entry_value': round(entry_premium * quantity, 2),
            'entry_greeks': entry_greeks or {},
            'entry_iv': round(entry_iv, 2),
            'underlying_alert_price': round(underlying_alert_price, 2) if underlying_alert_price else None,
            'current_premium': entry_premium,  # Will be updated
            'current_value': round(entry_premium * quantity, 2),
            'current_greeks': entry_greeks or {},
            'current_iv': entry_iv,
            'highest_premium': entry_premium,  # For trailing exit tracking
            'lowest_premium': entry_premium,  # For HARD_SL optimization
            'unrealized_pnl': 0.0,
            'unrealized_pnl_percent': 0.0,
            'exit_time': None,
            'exit_premium': None,
            'exit_value': None,
            'exit_reason': None,  # PROFIT, LOSS, TIME, MANUAL, EXPIRY
            'exit_greeks': None,
            'exit_iv': None,
            'realized_pnl': None,
            'realized_pnl_percent': None,
            'duration_seconds': 0,
            'duration_formatted': '',
            'status': 'OPEN'  # OPEN, CLOSED
        }
        
        self.live_data['trades'].append(trade_record)
        self.record_candle_context(
            event_type='entry',
            symbol=symbol,
            underlying=underlying,
            option_premium=entry_premium,
        )
    
    def update_trade(self,
                    symbol: str,
                    current_premium: float,
                    current_greeks: Optional[Dict[str, float]] = None,
                    current_iv: float = 0.0,
                    highest_premium: Optional[float] = None,
                    quantity: int = 1,
                    lowest_premium: Optional[float] = None,
                    trial_sl_enabled: bool = False,
                    trial_sl_price: Optional[float] = None,
                    hard_sl_price: Optional[float] = None,
                    trial_sl_updates: int = 0,
                    trail_profile: Optional[str] = None,
                    trail_activation_threshold: Optional[float] = None,
                    trailing_gap: Optional[float] = None,
                    market_trend: Optional[str] = None,
                    trend_strength: Optional[float] = None) -> None:
        """
        Update current market data for an open trade
        
        Args:
            symbol: Full symbol of the position
            current_premium: Current market premium
            current_greeks: Current Greeks {delta, gamma, theta, vega}
            current_iv: Current IV
            highest_premium: Highest premium seen (for trailing SL)
            quantity: Quantity (for unrealized PNL)
        """
        # Find the trade
        for trade in self.live_data['trades']:
            if trade['symbol'] == symbol and trade['status'] == 'OPEN':
                # Update current market data
                trade['current_premium'] = round(current_premium, 2)
                trade['current_value'] = round(current_premium * quantity, 2)
                trade['current_greeks'] = current_greeks or trade['current_greeks']
                trade['current_iv'] = round(current_iv, 2)
                
                if highest_premium:
                    trade['highest_premium'] = max(trade['highest_premium'], round(highest_premium, 2))
                
                # Track lowest premium for HARD_SL analysis
                trade['lowest_premium'] = min(trade['lowest_premium'], round(current_premium, 2))
                if lowest_premium is not None:
                    trade['lowest_premium'] = min(trade['lowest_premium'], round(lowest_premium, 2))

                self._record_open_snapshot(
                    symbol=symbol,
                    current_premium=current_premium,
                    highest_premium=trade['highest_premium'],
                    lowest_premium=trade['lowest_premium'],
                    quantity=quantity,
                    trial_sl_enabled=trial_sl_enabled,
                    trial_sl_price=trial_sl_price,
                    hard_sl_price=hard_sl_price,
                    trial_sl_updates=trial_sl_updates,
                    trail_profile=trail_profile,
                    trail_activation_threshold=trail_activation_threshold,
                    trailing_gap=trailing_gap,
                    market_trend=market_trend,
                    trend_strength=trend_strength,
                )
                
                # Calculate unrealized PNL
                premium_diff = current_premium - trade['entry_premium']
                
                # For BUY: profit if current > entry
                # For SELL: profit if current < entry
                if trade['action'] == 'BUY':
                    unrealized_pnl = premium_diff * quantity
                else:  # SELL
                    unrealized_pnl = -premium_diff * quantity
                
                trade['unrealized_pnl'] = round(unrealized_pnl, 2)
                
                # Calculate unrealized PNL percent
                entry_value = trade['entry_value']
                if entry_value != 0:
                    pnl_percent = (unrealized_pnl / entry_value) * 100
                    trade['unrealized_pnl_percent'] = round(pnl_percent, 2)
                
                break
    
    def close_trade(self,
                   symbol: str,
                   exit_time: str,
                   exit_premium: float,
                   exit_reason: str,
                   exit_greeks: Optional[Dict[str, float]] = None,
                   exit_iv: float = 0.0,
                   quantity: int = 1,
                   entry_premium: float = 0.0,
                   entry_time: str = '',
                   trail_profile: Optional[str] = None,
                   trail_activation_threshold: Optional[float] = None,
                   trailing_gap: Optional[float] = None,
                   market_trend: Optional[str] = None,
                   trend_strength: Optional[float] = None) -> None:
        """
        Close a trade and record exit details
        
        Args:
            symbol: Full symbol of the position
            exit_time: Exit time (ISO format)
            exit_premium: Exit premium
            exit_reason: Why the trade exited (PROFIT, LOSS, TIME, MANUAL, EXPIRY)
            exit_greeks: Greeks at exit
            exit_iv: IV at exit
            quantity: Quantity for PNL calculation
            entry_premium: Entry premium for PNL calculation
            entry_time: Entry time for duration calculation
        """
        # Find the trade
        for trade in self.live_data['trades']:
            if trade['symbol'] == symbol and trade['status'] == 'OPEN':
                # Record exit details
                trade['exit_time'] = exit_time
                trade['exit_premium'] = round(exit_premium, 2)
                trade['exit_value'] = round(exit_premium * quantity, 2)
                trade['exit_reason'] = exit_reason
                trade['exit_greeks'] = exit_greeks or {}
                trade['exit_iv'] = round(exit_iv, 2)
                trade['status'] = 'CLOSED'
                
                # Preserve lowest_premium seen during trade lifetime
                
                # Calculate realized PNL
                premium_diff = exit_premium - entry_premium
                if trade['action'] == 'BUY':
                    realized_pnl = premium_diff * quantity
                else:  # SELL
                    realized_pnl = -premium_diff * quantity
                
                trade['realized_pnl'] = round(realized_pnl, 2)
                
                # Calculate realized PNL percent
                entry_value = trade['entry_value']
                if entry_value != 0:
                    pnl_percent = (realized_pnl / entry_value) * 100
                    trade['realized_pnl_percent'] = round(pnl_percent, 2)
                
                # Calculate duration
                if entry_time:
                    try:
                        entry_dt = datetime.fromisoformat(entry_time)
                        exit_dt = datetime.fromisoformat(exit_time)
                        duration = (exit_dt - entry_dt).total_seconds()
                        trade['duration_seconds'] = int(duration)
                        
                        # Format duration nicely
                        mins = int(duration // 60)
                        secs = int(duration % 60)
                        trade['duration_formatted'] = f"{mins}m {secs}s"
                    except Exception:
                        pass

                self._start_post_exit_watch(
                    symbol=symbol,
                    underlying=trade.get('underlying') or symbol,
                    exit_time=exit_time,
                    exit_premium=exit_premium,
                    exit_reason=exit_reason,
                    quantity=quantity,
                    trail_profile=trail_profile,
                    trail_activation_threshold=trail_activation_threshold,
                    trailing_gap=trailing_gap,
                    market_trend=market_trend,
                    trend_strength=trend_strength,
                )
                self.record_candle_context(
                    event_type='exit',
                    symbol=symbol,
                    underlying=trade.get('underlying') or symbol,
                    option_premium=exit_premium,
                    trail_profile=trail_profile,
                    trail_activation_threshold=trail_activation_threshold,
                    trailing_gap=trailing_gap,
                    market_trend=market_trend,
                    trend_strength=trend_strength,
                    exit_reason=exit_reason,
                )
                
                break
    
    def save(self) -> bool:
        """
        Generate live_data.json by scraping option_positions.json and option_pnl_history.json
        
        Returns:
            True if saved successfully
        """
        try:
            # Read option_positions.json for open trades
            positions_file = self.data_dir / 'option_positions.json'
            open_positions = []
            if positions_file.exists():
                with open(positions_file, 'r') as f:
                    pos_data = json.load(f)
                    positions_raw = pos_data.get('positions', [])
                    # Handle both dict and list formats
                    if isinstance(positions_raw, dict):
                        open_positions = list(positions_raw.values())
                    else:
                        open_positions = positions_raw
            
            # Read option_pnl_history.json for closed trades
            pnl_file = self.data_dir / 'option_pnl_history.json'
            closed_trades = []
            if pnl_file.exists():
                with open(pnl_file, 'r') as f:
                    pnl_data = json.load(f)
                    # Handle both list and dict with 'trades' key
                    if isinstance(pnl_data, list):
                        closed_trades = pnl_data
                    else:
                        closed_trades = pnl_data.get('trades', [])
            
            # Calculate summary
            ongoing_count = len(open_positions)
            ongoing_budget = sum(p.get('entry_premium', 0) * p.get('quantity', 0) for p in open_positions)
            total_unrealized_pnl = sum(p.get('unrealized_pnl', 0) for p in open_positions)
            
            # Closed trades stats (today only)
            today = datetime.now().date().isoformat()
            # Check both 'closed_at' and 'exit_time' fields
            today_closed = [t for t in closed_trades if (t.get('closed_at', '') or t.get('exit_time', '')).startswith(today)]
            closed_count = len(today_closed)
            winning_trades = len([t for t in today_closed if t.get('pnl', 0) > 0])
            losing_trades = len([t for t in today_closed if t.get('pnl', 0) < 0])
            total_realized_pnl = sum(t.get('pnl', 0) for t in today_closed)

            today_index_open = [
                p for p in open_positions
                if str(p.get('entry_time', '')).startswith(today)
                and OptionsCapitalConfig.is_index_underlying(p.get('underlying') or p.get('symbol') or '')
            ]
            today_index_closed = [
                t for t in today_closed
                if OptionsCapitalConfig.is_index_underlying(t.get('underlying') or t.get('symbol') or '')
            ]
            today_non_index_closed = [
                t for t in today_closed
                if not OptionsCapitalConfig.is_index_underlying(t.get('underlying') or t.get('symbol') or '')
            ]
            index_ongoing_count = len(today_index_open)
            index_closed_count = len(today_index_closed)
            non_index_open = [
                p for p in open_positions
                if not OptionsCapitalConfig.is_index_underlying(p.get('underlying') or p.get('symbol') or '')
            ]
            non_index_ongoing_count = len(non_index_open)
            non_index_closed_count = len(today_non_index_closed)
            index_budget_used = sum(
                p.get('entry_premium_total', p.get('entry_premium', 0) * p.get('quantity', 0))
                for p in today_index_open
            ) + sum(
                t.get('entry_premium_total', t.get('entry_premium', 0) * t.get('quantity', 0))
                for t in today_index_closed
            )
            non_index_budget_used = sum(
                p.get('entry_premium_total', p.get('entry_premium', 0) * p.get('quantity', 0))
                for p in non_index_open
            ) + sum(
                t.get('entry_premium_total', t.get('entry_premium', 0) * t.get('quantity', 0))
                for t in today_non_index_closed
            )
            daily_counts = OptionsCapitalConfig.get_daily_trade_counts()
            index_trades_today = max(index_ongoing_count + index_closed_count, daily_counts['index'])
            non_index_trades_today = max(non_index_ongoing_count + non_index_closed_count, daily_counts['non_index'])
            index_winning_trades = len([t for t in today_index_closed if t.get('pnl', 0) > 0])
            index_losing_trades = len([t for t in today_index_closed if t.get('pnl', 0) < 0])
            non_index_winning_trades = len([t for t in today_non_index_closed if t.get('pnl', 0) > 0])
            non_index_losing_trades = len([t for t in today_non_index_closed if t.get('pnl', 0) < 0])
            index_realized_pnl = sum(t.get('pnl', 0) for t in today_index_closed)
            non_index_realized_pnl = sum(t.get('pnl', 0) for t in today_non_index_closed)
            index_unrealized_pnl = sum(p.get('unrealized_pnl', 0) for p in today_index_open)
            non_index_unrealized_pnl = sum(p.get('unrealized_pnl', 0) for p in non_index_open)
            index_total_pnl = index_unrealized_pnl + index_realized_pnl
            non_index_total_pnl = non_index_unrealized_pnl + non_index_realized_pnl
            index_total_pnl_percent = (index_total_pnl / index_budget_used * 100) if index_budget_used > 0 else 0.0
            non_index_total_pnl_percent = (non_index_total_pnl / non_index_budget_used * 100) if non_index_budget_used > 0 else 0.0
            index_win_rate = (index_winning_trades / index_closed_count * 100) if index_closed_count > 0 else 0.0
            non_index_win_rate = (non_index_winning_trades / non_index_closed_count * 100) if non_index_closed_count > 0 else 0.0
            
            # Budget used: ongoing positions + closed trades (today)
            closed_budget = sum(t.get('entry_premium_total', t.get('entry_premium', 0) * t.get('quantity', 0)) for t in today_closed)
            budget_used = ongoing_budget + closed_budget
            
            # Win rate
            win_rate = (winning_trades / closed_count * 100) if closed_count > 0 else 0.0
            
            # Calculate PnL percentages (based on budget_used, not total_budget)
            total_budget = OptionsCapitalConfig.MAX_CAPITAL
            unrealized_pnl_percent = (total_unrealized_pnl / budget_used * 100) if budget_used > 0 else 0.0
            realized_pnl_percent = (total_realized_pnl / budget_used * 100) if budget_used > 0 else 0.0
            total_pnl_percent = (((total_unrealized_pnl + total_realized_pnl) / budget_used) * 100) if budget_used > 0 else 0.0
            
            now = datetime.now()
            live_data_json_updated_at = now.isoformat()
            live_data_trades_csv_updated_at = self._get_file_updated_at(self.live_data_trades_file)

            # Create summary
            output_data = {
                'timestamp': live_data_json_updated_at,
                'live_data_json_updated_at': live_data_json_updated_at,
                'live_data_trades_csv_updated_at': live_data_trades_csv_updated_at,
                'trading_mode': self.trading_mode,
                'market_status': get_market_status(),
                'index_summary': {
                    'budget_used': round(index_budget_used, 2),
                    'trades_today': index_trades_today,
                    'trade_limit': OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY,
                    'trade_slots_remaining': max(0, OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY - index_trades_today),
                    'ongoing_trades': index_ongoing_count,
                    'closed_trades': index_closed_count,
                    'winning_trades': index_winning_trades,
                    'losing_trades': index_losing_trades,
                    'win_rate_percent': round(index_win_rate, 2),
                    'total_pnl': round(index_total_pnl, 2),
                    'total_pnl_percent': round(index_total_pnl_percent, 2),
                    'unrealized_pnl': round(index_unrealized_pnl, 2),
                    'realized_pnl': round(index_realized_pnl, 2),
                },
                'non_index_summary': {
                    'budget_used': round(non_index_budget_used, 2),
                    'trades_today': non_index_trades_today,
                    'trade_limit': OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY,
                    'trade_slots_remaining': max(0, OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY - non_index_trades_today),
                    'ongoing_trades': non_index_ongoing_count,
                    'closed_trades': non_index_closed_count,
                    'winning_trades': non_index_winning_trades,
                    'losing_trades': non_index_losing_trades,
                    'win_rate_percent': round(non_index_win_rate, 2),
                    'total_pnl': round(non_index_total_pnl, 2),
                    'total_pnl_percent': round(non_index_total_pnl_percent, 2),
                    'unrealized_pnl': round(non_index_unrealized_pnl, 2),
                    'realized_pnl': round(non_index_realized_pnl, 2),
                },
            }
            
            current_signature = self._build_signature(output_data)
            payload_changed = current_signature != self._last_saved_signature
            
            # Write JSON in place so generated-file editor tabs stay in sync.
            try:
                with self._file_lock:  # Critical section - prevent concurrent writes
                    self._write_json_preserve_inode(output_data)
                    self._last_saved_signature = current_signature
                    self._last_save_changed = payload_changed
                    logger.debug(
                        f"LIVE_DATA_TRACKER: SAVED | ongoing={ongoing_count} | closed={closed_count} | "
                        f"pnl=₹{total_unrealized_pnl + total_realized_pnl:.2f} | payload_changed={payload_changed} | file={self.live_data_file}"
                    )
                    # Preserve in-memory trades list — output_data is the JSON summary
                    # (no 'trades' key) but self.live_data['trades'] is the source of truth
                    # for update_trade() / close_trade(). Overwriting without it causes KeyError.
                    output_data['trades'] = self.live_data.get('trades', [])
                    self.live_data = output_data
            except Exception as write_err:
                logger.error(f"LIVE_DATA_TRACKER: WRITE_ERROR | {str(write_err)}")
                self._last_save_changed = False
                return False
            
            return True
            
        except Exception as e:
            self._last_save_changed = False
            logger.error(f"LIVE_DATA_TRACKER: SAVE_ERROR | {str(e)}")
            return False
    
    def get_live_data(self) -> Dict[str, Any]:
        """Get current live data snapshot"""
        return self.live_data.copy()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics only"""
        return self._combine_summaries(
            self.live_data.get('index_summary', {}),
            self.live_data.get('non_index_summary', {}),
            OptionsCapitalConfig.MAX_CAPITAL,
        )
    
    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all open trades"""
        return [t for t in self.live_data['trades'] if t['status'] == 'OPEN']
    
    def get_closed_trades(self) -> List[Dict[str, Any]]:
        """Get all closed trades"""
        return [t for t in self.live_data['trades'] if t['status'] == 'CLOSED']
    
    def clear_daily_data(self) -> None:
        """Clear daily data for new trading day"""
        self.live_data['trades'] = []
        self._last_snapshot_at = {}
        self._post_exit_watchers = {}
        if self.post_exit_state_file.exists():
            try:
                self.post_exit_state_file.unlink()
            except OSError:
                pass
        self.live_data['market_status'] = get_market_status()
        self.live_data['timestamp'] = datetime.now().isoformat()
        self.live_data['index_summary'] = {
            'budget_used': 0.0,
            'trades_today': 0,
            'trade_limit': OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY,
            'trade_slots_remaining': OptionsCapitalConfig.MAX_INDEX_TRADES_PER_DAY,
            'ongoing_trades': 0,
            'closed_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate_percent': 0.0,
            'total_pnl': 0.0,
            'total_pnl_percent': 0.0,
            'unrealized_pnl': 0.0,
            'realized_pnl': 0.0,
        }
        self.live_data['non_index_summary'] = {
            'budget_used': 0.0,
            'trades_today': 0,
            'trade_limit': OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY,
            'trade_slots_remaining': OptionsCapitalConfig.MAX_NON_INDEX_TRADES_PER_DAY,
            'ongoing_trades': 0,
            'closed_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate_percent': 0.0,
            'total_pnl': 0.0,
            'total_pnl_percent': 0.0,
            'unrealized_pnl': 0.0,
            'realized_pnl': 0.0,
        }
        logger.info("LIVE_DATA_TRACKER: DAILY_DATA_CLEARED")


# Global instance
_live_data_tracker = None

def get_live_data_tracker() -> LiveDataTracker:
    """Get or create global live data tracker instance"""
    global _live_data_tracker
    if _live_data_tracker is None:
        _live_data_tracker = LiveDataTracker()
    return _live_data_tracker
