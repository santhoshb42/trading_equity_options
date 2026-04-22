#!/usr/bin/env python3

from __future__ import annotations

import importlib
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class ScenarioResult:
    bot: str
    scenario: str
    passed: bool
    detail: str


class FakeTracker:
    def close_trade(self, **_: Any) -> None:
        return None

    def save(self) -> bool:
        return True


class FakeTradeLogger:
    def log_trade_exit(self, **_: Any) -> None:
        return None


class FakeRateLimiter:
    def wait_for_call_permission(self, timeout: float = 0.0, request_type: str = "") -> bool:
        return True

    def record_call(self, request_type: str, success: bool) -> None:
        return None

    def queue_request(self, request_type: str, callback: Callable[[], Any]) -> None:
        return None

    def get_utilization(self) -> float:
        return 0.0


class FakeMoveDetector:
    def close_position_monitoring(self, _: str) -> None:
        return None

    def monitor_position(self, *_: Any, **__: Any) -> None:
        return None


class FakeMarketDetector:
    def __init__(self, threshold: float = 10.0, reason: str = "simulation") -> None:
        self.threshold = threshold
        self.reason = reason

    def update_market_data(self, **_: Any) -> None:
        return None

    def get_trial_sl_threshold(self, **_: Any) -> Tuple[float, str]:
        return self.threshold, self.reason


class FakeSmartAPI:
    def __init__(self) -> None:
        self.cancel_calls: List[Tuple[Any, ...]] = []

    def cancelOrder(self, *args: Any, **kwargs: Any) -> str:
        self.cancel_calls.append(args if args else tuple(kwargs.items()))
        return "CANCELLED"


class FakeSymbolPool:
    def __init__(self) -> None:
        self.removed: List[Tuple[str, str]] = []

    def remove_symbol(self, symbol: str, exit_reason: str = "") -> None:
        self.removed.append((symbol, exit_reason))


class FakePosition:
    def __init__(self, symbol: str, underlying: str = "NIFTY") -> None:
        self.symbol = symbol
        self.underlying = underlying
        self.strike = 22500.0
        self.expiry = "2026-04-30"
        self.contract_type = "CE"
        self.action = "BUY"
        self.quantity = 50
        self.entry_premium = 100.0
        self.entry_time = datetime.now()
        self.order_id = "BUY-1"
        self.trade_id = None
        self.underlying_alert_price = None
        self.sector_data: Dict[str, Any] = {}
        self.market_trend = "NEUTRAL"
        self.trend_strength = None
        self.current_premium = 100.0
        self.highest_premium = 110.0
        self.lowest_premium = 95.0
        self.current_greeks = {'delta': 0.5, 'gamma': 0.05, 'theta': -0.02, 'vega': 0.1}
        self.current_iv = 25.0
        self.entry_greeks = dict(self.current_greeks)
        self.exit_greeks = dict(self.current_greeks)
        self.entry_iv = 25.0
        self.entry_pcr = None
        self.current_pcr = None
        self.entry_oi_buildup = None
        self.current_oi = None
        self.exit_premium: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.exit_order_id: Optional[str] = None
        self.exit_reason: Optional[str] = None
        self.sl_order_id: Optional[str] = None
        self.sl_order_price: Optional[float] = None
        self.trailing_sl_activated = False
        self.trailing_sl_activation_time = None
        self.last_trailing_sl_price = None
        self.trailing_sl_update_count = 0
        self.hard_sl_price = 80.0
        self.trial_sl_enabled = False
        self.trial_sl_price = 95.0
        self.trial_sl_activation_time = None
        self.trial_sl_update_count = 0
        self.trial_sl_expected_threshold = 10.0
        self.last_modify_time = None
        self.last_modified_sl_price = None
        self.modify_pending = False
        self.last_attempted_sl_price = None
        self.unrealized_pnl = 0.0
        self.realized_pnl: Optional[float] = None
        self.last_updated = datetime.now()

    def close_position(self, exit_premium: float, exit_reason: str, exit_greeks: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        self.exit_premium = exit_premium
        self.exit_time = datetime.now()
        self.exit_reason = exit_reason
        self.exit_greeks = exit_greeks or dict(self.current_greeks)
        self.realized_pnl = (exit_premium - self.entry_premium) * self.quantity
        duration = (self.exit_time - self.entry_time).total_seconds()
        invested = self.entry_premium * self.quantity
        pnl_percent = (self.realized_pnl / invested * 100) if invested else 0.0
        return {
            'symbol': self.symbol,
            'underlying': self.underlying,
            'entry_premium': self.entry_premium,
            'exit_premium': exit_premium,
            'entry_time': self.entry_time.isoformat(),
            'exit_time': self.exit_time.isoformat(),
            'duration': duration,
            'pnl': self.realized_pnl,
            'pnl_percent': pnl_percent,
            'exit_reason': exit_reason,
        }


class FakeBroker:
    def __init__(
        self,
        *,
        cancel_results: Optional[List[bool]] = None,
        place_order_results: Optional[List[Optional[str]]] = None,
        modify_results: Optional[List[Optional[str]]] = None,
        order_status_sequences: Optional[Dict[str, List[Optional[Dict[str, Any]]]]] = None,
        order_book: Optional[List[Dict[str, Any]]] = None,
        cleanup_return_ids: Optional[List[str]] = None,
    ) -> None:
        self.cancel_results = cancel_results or []
        self.place_order_results = place_order_results or []
        self.modify_results = modify_results or []
        self.order_status_sequences = order_status_sequences or {}
        self.order_book = list(order_book or [])
        self.cleanup_return_ids = list(cleanup_return_ids or [])
        self.cancel_calls: List[Tuple[str, str, str]] = []
        self.place_calls: List[Dict[str, Any]] = []
        self.modify_calls: List[Dict[str, Any]] = []
        self.cleanup_calls: List[Tuple[str, List[str]]] = []
        self.last_order_error = ""

    def cancel_order(self, order_id: str, symbol: str, order_type: str = "STOPLOSS_MARKET") -> bool:
        self.cancel_calls.append((order_id, symbol, order_type))
        if self.cancel_results:
            return self.cancel_results.pop(0)
        return True

    def place_options_order(self, **kwargs: Any) -> Optional[str]:
        self.place_calls.append(kwargs)
        if self.place_order_results:
            return self.place_order_results.pop(0)
        return "EXIT-1"

    def modify_order(self, **kwargs: Any) -> Optional[str]:
        self.modify_calls.append(kwargs)
        if self.modify_results:
            return self.modify_results.pop(0)
        return kwargs['order_id']

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        sequence = self.order_status_sequences.get(order_id)
        if sequence is None:
            return None
        if sequence:
            result = sequence.pop(0)
            if result is None:
                return None
            return dict(result)
        return None

    def get_order_book(self) -> List[Dict[str, Any]]:
        return [dict(order) for order in self.order_book]

    def cancel_outstanding_orders_for_symbol(self, symbol: str, exclude_order_ids: Optional[List[str]] = None) -> List[str]:
        self.cleanup_calls.append((symbol, list(exclude_order_ids or [])))
        return list(self.cleanup_return_ids)

    def get_market_data(self, *_: Any, **__: Any) -> Dict[str, Any]:
        return {'ltp': 22500.0, 'open': 22400.0}


@contextmanager
def patched(obj: Any, name: str, value: Any):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


@contextmanager
def patched_many(patches: List[Tuple[Any, str, Any]]):
    originals: List[Tuple[Any, str, Any]] = []
    for obj, name, value in patches:
        originals.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)
    try:
        yield
    finally:
        for obj, name, value in reversed(originals):
            setattr(obj, name, value)


def build_monitor(module: Any, broker: FakeBroker, position: FakePosition) -> Any:
    monitor = module.OptionPositionMonitor.__new__(module.OptionPositionMonitor)
    monitor.broker = broker
    monitor.positions = {position.symbol: position}
    monitor.closed_positions = []
    monitor._positions_lock = threading.RLock()
    monitor._closing_lock = threading.Lock()
    monitor._closing_symbols = set()
    monitor.symbol_pool = FakeSymbolPool()
    monitor._save_positions = lambda: None
    monitor._save_pnl_history = lambda pnl_info: None
    return monitor


def run_buy_confirmation_checks(bot: str, angelone_module: Any) -> List[ScenarioResult]:
    broker_class = angelone_module.AngelOneOptionsBroker
    results: List[ScenarioResult] = []

    def make_broker(order_book_factory: Callable[[], List[Dict[str, Any]]]) -> Any:
        broker = broker_class.__new__(broker_class)
        broker.pending_buy_orders = {
            'OID-1': {'order_id': 'OID-1', 'symbol': 'TESTSYM', 'quantity': 50, 'status': 'PENDING'}
        }
        broker.pending_buy_orders_by_symbol = {'TESTSYM': 'OID-1'}
        broker.get_order_book = order_book_factory
        broker.smart_api = FakeSmartAPI()
        return broker

    with patched_many([
        (angelone_module.OptionsTradingConfig, 'TRADING_MODE', 'LIVE'),
        (angelone_module.time, 'sleep', lambda _: None),
    ]):
        broker = make_broker(lambda: [{'orderid': 'OID-1', 'orderstatus': 'COMPLETE'}])
        passed = broker_class.wait_for_buy_confirmation(broker, 'TESTSYM', timeout=0.05, order_id='OID-1')
        results.append(
            ScenarioResult(
                bot,
                'buy_confirm_orderstatus_complete',
                passed and not broker.pending_buy_orders,
                'Expected COMPLETE in orderstatus to confirm BUY and clear pending tracking.',
            )
        )

        broker = make_broker(lambda: [{'orderid': 'OID-1', 'orderstate': 'COMPLETE'}])
        passed = broker_class.wait_for_buy_confirmation(broker, 'TESTSYM', timeout=0.05, order_id='OID-1')
        results.append(
            ScenarioResult(
                bot,
                'buy_confirm_orderstate_complete',
                passed,
                'Expected COMPLETE in orderstate to confirm BUY as well as orderstatus.',
            )
        )

        broker = make_broker(lambda: [{'orderid': 'OID-1', 'orderstatus': 'OPEN'}])
        passed = broker_class.wait_for_buy_confirmation(broker, 'TESTSYM', timeout=0.01, order_id='OID-1')
        cancel_calls = getattr(broker.smart_api, 'cancel_calls', [])
        results.append(
            ScenarioResult(
                bot,
                'buy_confirm_timeout_sends_cancel',
                (not passed) and bool(cancel_calls) and cancel_calls[0] == ('OID-1', 'NORMAL'),
                'Expected BUY confirmation timeout to send a broker cancel so an untracked BUY cannot turn into a zombie fill.',
            )
        )

    return results


def run_broker_cleanup_checks(bot: str, angelone_module: Any) -> List[ScenarioResult]:
    broker_class = angelone_module.AngelOneOptionsBroker
    results: List[ScenarioResult] = []

    broker = broker_class.__new__(broker_class)
    cancelled: List[Tuple[str, str, str]] = []
    broker.get_order_book = lambda: [
        {
            'orderid': 'SL-ZOMBIE',
            'tradingsymbol': 'TESTSYM',
            'ordertype': 'STOPLOSS_MARKET',
            'variety': 'STOPLOSS',
            'orderstatus': 'TRIGGER PENDING',
        },
        {
            'orderid': 'SELL-ZOMBIE',
            'tradingsymbol': 'TESTSYM',
            'ordertype': 'MARKET',
            'variety': 'NORMAL',
            'orderstatus': 'OPEN',
        },
        {
            'orderid': 'EXIT-LIVE',
            'tradingsymbol': 'TESTSYM',
            'ordertype': 'MARKET',
            'variety': 'NORMAL',
            'orderstatus': 'OPEN',
        },
        {
            'orderid': 'FILLED-OLD',
            'tradingsymbol': 'TESTSYM',
            'ordertype': 'STOPLOSS_MARKET',
            'variety': 'STOPLOSS',
            'orderstatus': 'COMPLETE',
        },
    ]

    def fake_cancel(order_id: str, symbol: str, order_type: str = 'STOPLOSS_MARKET') -> bool:
        cancelled.append((order_id, symbol, order_type))
        return True

    broker.cancel_order = fake_cancel

    with patched(angelone_module.OptionsTradingConfig, 'TRADING_MODE', 'LIVE'):
        result = broker_class.cancel_outstanding_orders_for_symbol(broker, 'TESTSYM', exclude_order_ids=['EXIT-LIVE'])

    results.append(
        ScenarioResult(
            bot,
            'cleanup_cancels_only_live_zombies',
            result == ['SL-ZOMBIE', 'SELL-ZOMBIE'] and cancelled == [
                ('SL-ZOMBIE', 'TESTSYM', 'STOPLOSS_MARKET'),
                ('SELL-ZOMBIE', 'TESTSYM', 'MARKET'),
            ],
            'Expected stale pending broker SELL orders to be cancelled, while excluding the current live exit order and ignoring terminal orders.',
        )
    )

    return results


def run_monitor_checks(bot: str, monitor_module: Any) -> List[ScenarioResult]:
    results: List[ScenarioResult] = []
    fake_rate_limiter = FakeRateLimiter()
    patches = [
        (monitor_module.OptionsTradingConfig, 'TRADING_MODE', 'LIVE'),
        (monitor_module, 'get_trade_logger', lambda: FakeTradeLogger()),
        (monitor_module, 'get_live_data_tracker', lambda: FakeTracker()),
        (monitor_module, 'get_fake_move_detector', lambda: FakeMoveDetector()),
        (monitor_module, 'get_market_condition_detector', lambda: FakeMarketDetector()),
        (monitor_module, 'get_options_rate_limiter', lambda: fake_rate_limiter),
        (monitor_module, 'log_position', lambda *args, **kwargs: None),
        (monitor_module, 'log_pnl', lambda *args, **kwargs: None),
        (monitor_module, 'log_event', lambda *args, **kwargs: None),
        (monitor_module.time, 'sleep', lambda _: None),
    ]

    with patched_many(patches):
        position = FakePosition('TESTSYM')
        position.sl_order_id = 'SL-OLD'
        position.trial_sl_price = 95.0
        broker = FakeBroker(modify_results=['SL-NEW'])
        monitor = build_monitor(monitor_module, broker, position)
        passed = monitor.modify_sl_order('TESTSYM', 96.2)
        results.append(
            ScenarioResult(
                bot,
                'modify_sl_updates_order_id',
                passed and position.sl_order_id == 'SL-NEW' and abs(position.sl_order_price - 96.2) < 1e-9,
                'Expected modified SL order to replace local sl_order_id with the broker returned order id.',
            )
        )

        position = FakePosition('TESTSYM')
        position.sl_order_id = 'SL-OLD'
        position.trial_sl_price = 95.0
        broker = FakeBroker(
            modify_results=['QUEUED_123_TESTSYM'],
            order_book=[
                {
                    'orderid': 'SL-NEW',
                    'tradingsymbol': 'TESTSYM',
                    'transactiontype': 'SELL',
                    'ordertype': 'STOPLOSS_MARKET',
                    'variety': 'STOPLOSS',
                    'orderstatus': 'TRIGGER PENDING',
                }
            ],
        )
        monitor = build_monitor(monitor_module, broker, position)
        passed = monitor.modify_sl_order('TESTSYM', 96.2)
        synced = position.sl_order_id != 'SL-OLD'
        results.append(
            ScenarioResult(
                bot,
                'modify_sl_queued_keeps_sync',
                passed and synced,
                'Expected queued SL modify path to preserve broker/local order-id sync. Local state currently remains on the stale order id.',
            )
        )

        position = FakePosition('TESTSYM')
        position.sl_order_id = 'SL-OLD'
        broker = FakeBroker(cancel_results=[False, False, False])
        monitor = build_monitor(monitor_module, broker, position)
        close_result = monitor.close_position('TESTSYM', 101.0, 'MANUAL_EXIT')
        results.append(
            ScenarioResult(
                bot,
                'manual_exit_blocks_on_sl_cancel_failure',
                close_result is None and not broker.place_calls,
                'Expected manual exit to stop before placing SELL if broker SL cancel fails.',
            )
        )

        position = FakePosition('TESTSYM')
        position.sl_order_id = 'SL-OLD'
        broker = FakeBroker(
            cancel_results=[True],
            place_order_results=['EXIT-1'],
            order_status_sequences={
                'EXIT-1': [
                    {'status': 'OPEN', 'average_price': 0.0},
                    {'status': 'COMPLETE', 'average_price': 102.5},
                ]
            },
        )
        monitor = build_monitor(monitor_module, broker, position)
        close_result = monitor.close_position('TESTSYM', 101.0, 'STALE_CONSOLIDATION')
        results.append(
            ScenarioResult(
                bot,
                'manual_exit_waits_for_fill',
                close_result is not None and position.symbol not in monitor.positions and close_result['exit_premium'] == 102.5,
                'Expected manual exit to cancel SL, place SELL, wait for COMPLETE, and only then close local state.',
            )
        )

        position = FakePosition('TESTSYM')
        position.sl_order_id = 'SL-BROKER'
        broker = FakeBroker(order_status_sequences={'SL-BROKER': [{'status': 'COMPLETE', 'average_price': 88.8}]})
        monitor = build_monitor(monitor_module, broker, position)
        reconcile_result = monitor._reconcile_broker_stop_exit('TESTSYM', 90.0, 'HARD_SL_HIT')
        results.append(
            ScenarioResult(
                bot,
                'broker_managed_stop_reconciles',
                reconcile_result is not None and not broker.place_calls and position.symbol not in monitor.positions and abs(reconcile_result['exit_premium'] - 88.8) < 1e-9,
                'Expected broker-managed SL fill to reconcile locally without placing an extra SELL.',
            )
        )

        position = FakePosition('TESTSYM')
        broker = FakeBroker(order_status_sequences={'SL-BROKER': [{'status': 'TRIGGER PENDING', 'average_price': 0.0}]})
        position.sl_order_id = 'SL-BROKER'
        monitor = build_monitor(monitor_module, broker, position)
        reconcile_result = monitor._reconcile_broker_stop_exit('TESTSYM', 90.0, 'HARD_SL_HIT')
        results.append(
            ScenarioResult(
                bot,
                'broker_stop_pending_waits_without_extra_sell',
                reconcile_result is None and not broker.place_calls and position.symbol in monitor.positions,
                'Expected a still-pending broker SL to keep the local position open and avoid placing an extra manual SELL.',
            )
        )

        position = FakePosition('TESTSYM')
        position.hard_sl_price = None
        broker = FakeBroker(place_order_results=['SL-INIT'])
        monitor = build_monitor(monitor_module, broker, position)
        sl_placed = monitor.place_stop_loss_order('TESTSYM')
        first_sl_call = broker.place_calls[0] if broker.place_calls else {}
        results.append(
            ScenarioResult(
                bot,
                'place_stop_loss_places_live_stoploss_market',
                sl_placed
                and position.sl_order_id == 'SL-INIT'
                and first_sl_call.get('action') == 'SELL'
                and first_sl_call.get('order_type') == 'STOPLOSS_MARKET'
                and first_sl_call.get('allow_queue') is False
                and abs(float(first_sl_call.get('price', 0.0)) - 90.0) < 1e-9,
                'Expected post-BUY SL placement to create a real STOPLOSS_MARKET SELL at 10% below entry with queueing disabled.',
            )
        )

        position = FakePosition('TESTSYM')
        position.sl_order_id = 'SL-BASE'
        if bot == 'PE':
            position.current_premium = 109.0
            position.highest_premium = 110.0
            expected_activation_sl = 110.0
        else:
            position.current_premium = 111.2
            position.highest_premium = 111.2
            expected_activation_sl = 111.0
        broker = FakeBroker(modify_results=['SL-ACTIVE'])
        monitor = build_monitor(monitor_module, broker, position)
        closed = monitor.check_trailing_stop_losses()
        activation_call = broker.modify_calls[0] if broker.modify_calls else {}
        results.append(
            ScenarioResult(
                bot,
                'trial_sl_activation_modifies_broker_sl',
                not closed
                and position.trial_sl_enabled
                and position.sl_order_id == 'SL-ACTIVE'
                and abs(position.trial_sl_price - expected_activation_sl) < 1e-9
                and activation_call.get('order_id') == 'SL-BASE'
                and abs(float(activation_call.get('new_price', 0.0)) - expected_activation_sl) < 1e-9,
                'Expected reaching the TRIAL_SL activation threshold to immediately push the new broker SL to the activation milestone.',
            )
        )

        position = FakePosition('TESTSYM')
        position.sl_order_id = 'SL-ACTIVE'
        position.trial_sl_enabled = True
        position.trial_sl_price = 110.0 if bot == 'PE' else 111.0
        position.current_premium = 121.0 if bot == 'PE' else 118.2
        position.highest_premium = 120.0
        broker = FakeBroker(modify_results=['SL-TRAIL'])
        monitor = build_monitor(monitor_module, broker, position)
        closed = monitor.check_trailing_stop_losses()
        trail_call = broker.modify_calls[0] if broker.modify_calls else {}
        expected_trailing_sl = 120.0 if bot == 'PE' else 118.0
        results.append(
            ScenarioResult(
                bot,
                'trial_sl_update_modifies_broker_sl',
                not closed
                and position.sl_order_id == 'SL-TRAIL'
                and position.trial_sl_update_count == 1
                and abs(position.trial_sl_price - expected_trailing_sl) < 1e-9
                and trail_call.get('order_id') == 'SL-ACTIVE'
                and abs(float(trail_call.get('new_price', 0.0)) - expected_trailing_sl) < 1e-9,
                'Expected further peak gains after TRIAL_SL activation to push the broker SL upward according to the bot\'s configured trailing strategy.',
            )
        )

        position = FakePosition('TESTSYM')
        position.sl_order_id = 'SL-OLD'
        broker = FakeBroker(
            cancel_results=[True],
            place_order_results=['EXIT-1'],
            order_status_sequences={
                'EXIT-1': [{'status': 'COMPLETE', 'average_price': 101.0}],
            },
            cleanup_return_ids=['ZOMBIE-SELL-1'],
        )
        monitor = build_monitor(monitor_module, broker, position)
        close_result = monitor.close_position('TESTSYM', 101.0, 'MANUAL_EXIT')
        cleanup_symbol, exclusions = broker.cleanup_calls[0] if broker.cleanup_calls else ('', [])
        results.append(
            ScenarioResult(
                bot,
                'manual_exit_invokes_post_close_cleanup',
                close_result is not None
                and cleanup_symbol == 'TESTSYM'
                and 'SL-OLD' in exclusions
                and 'EXIT-1' in exclusions,
                'Expected a successful manual exit to run broker cleanup afterward so leftover pending SELL orders are cancelled before they become zombies.',
            )
        )

    return results


def print_results(results: List[ScenarioResult]) -> int:
    failed = [result for result in results if not result.passed]
    print('Live Broker Sync Simulation')
    print('=' * 32)
    for result in results:
        status = 'PASS' if result.passed else 'FAIL'
        print(f"[{status}] {result.bot:<3} {result.scenario}: {result.detail}")
    print('=' * 32)
    print(f"Passed: {len(results) - len(failed)} / {len(results)}")
    if failed:
        print('Blocking failures:')
        for result in failed:
            print(f"- {result.bot} {result.scenario}")
        return 1
    return 0


def main() -> int:
    suites = [
        (
            'CE',
            importlib.import_module('options.optcode.angelone_options'),
            importlib.import_module('options.optcode.optmonitor'),
        ),
        (
            'PE',
            importlib.import_module('put_options.optcode.angelone_options'),
            importlib.import_module('put_options.optcode.optmonitor'),
        ),
    ]

    results: List[ScenarioResult] = []
    for bot, angelone_module, monitor_module in suites:
        results.extend(run_buy_confirmation_checks(bot, angelone_module))
        results.extend(run_broker_cleanup_checks(bot, angelone_module))
        results.extend(run_monitor_checks(bot, monitor_module))
    return print_results(results)


if __name__ == '__main__':
    raise SystemExit(main())