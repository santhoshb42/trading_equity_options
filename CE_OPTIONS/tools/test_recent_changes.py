#!/usr/bin/env python3

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from options.optcode import angelone_options as call_broker_module
from options.optcode.angelone_options import AngelOneOptionsBroker
from options.optcode.entry_filter_engine import SetupQualityValidator
from options.optcode.optsignalvalidator import OptionsSignalValidator
from put_options.optcode.entry_filter_engine import SetupQualityValidator as PutSetupQualityValidator
from put_options.optcode.optsignalvalidator import OptionsSignalValidator as PutOptionsSignalValidator
from put_options.optcode.live_data_tracker import LiveDataTracker


class OptionsSignalValidatorTests(unittest.TestCase):
    def setUp(self):
        OptionsSignalValidator._last_alert_by_key.clear()

    def test_reentry_requires_previous_alert_baseline(self):
        alert = {
            'symbol': 'KFINTECH',
            'action': 'BUY',
            'entry_type': 'PRE_BREAKOUT',
            'tv_trigger_flag': 'REENTRY',
            'confidence': 90,
            'score': 90,
            'price': 950,
            'day_change': 2.5,
            'setup_sequence': 2,
        }

        with patch.dict(os.environ, {
            'CALL_OPTIONS_REENTRY_ALLOWED': 'true',
            'CALL_OPTIONS_REENTRY_MIN_ALERT_STEP_PCT': '1.0',
        }, clear=False):
            is_valid, message, processed = OptionsSignalValidator.validate_options_signal(alert)

        self.assertFalse(is_valid)
        self.assertIn('missing prior alert baseline', message)
        self.assertIsNone(processed)

    def test_reentry_passes_when_price_is_one_percent_above_previous_alert(self):
        initial_alert = {
            'symbol': 'KFINTECH',
            'action': 'BUY',
            'entry_type': 'PRE_BREAKOUT',
            'tv_trigger_flag': 'PRE_BREAKOUT',
            'confidence': 90,
            'score': 90,
            'price': 950,
            'day_change': 2.5,
            'setup_sequence': 1,
        }
        reentry_alert = {
            'symbol': 'KFINTECH',
            'action': 'BUY',
            'entry_type': 'PRE_BREAKOUT',
            'tv_trigger_flag': 'REENTRY',
            'confidence': 92,
            'score': 92,
            'price': 960,
            'day_change': 2.8,
            'setup_sequence': 2,
        }

        with patch.dict(os.environ, {
            'CALL_OPTIONS_REENTRY_ALLOWED': 'true',
            'CALL_OPTIONS_REENTRY_MIN_ALERT_STEP_PCT': '1.0',
        }, clear=False):
            first_valid, _, first_processed = OptionsSignalValidator.validate_options_signal(initial_alert)
            second_valid, second_message, second_processed = OptionsSignalValidator.validate_options_signal(reentry_alert)

        self.assertTrue(first_valid)
        self.assertTrue(first_processed['is_reentry_setup'] is False)
        self.assertTrue(second_valid, second_message)
        self.assertEqual(second_processed['underlying'], 'KFINTECH')
        self.assertTrue(second_processed['is_reentry_setup'])
        previous_alert = OptionsSignalValidator._get_previous_alert('KFINTECH', 'PRE_BREAKOUT')
        self.assertEqual(previous_alert['alert_price'], 960.0)

    def test_sequence_two_without_reentry_flag_does_not_require_prior_baseline(self):
        alert = {
            'symbol': 'KFINTECH',
            'action': 'BUY',
            'entry_type': 'PRE_BREAKOUT',
            'tv_trigger_flag': 'PRE_BREAKOUT',
            'confidence': 90,
            'score': 90,
            'price': 955,
            'day_change': 2.5,
            'setup_sequence': 2,
        }

        with patch.dict(os.environ, {
            'CALL_OPTIONS_REENTRY_ALLOWED': 'true',
            'CALL_OPTIONS_REENTRY_MIN_ALERT_STEP_PCT': '1.0',
        }, clear=False):
            is_valid, message, processed = OptionsSignalValidator.validate_options_signal(alert)

        self.assertTrue(is_valid, message)
        self.assertFalse(processed['is_reentry_setup'])


class SetupQualityValidatorTests(unittest.TestCase):
    def test_top_gainer_requirement_is_ignored_when_rank_data_unavailable(self):
        with patch.dict(os.environ, {
            'CALL_OPTIONS_REENTRY_ALLOWED': 'true',
            'CALL_OPTIONS_REENTRY_REQUIRE_TOP_GAINER': 'true',
            'CALL_OPTIONS_REENTRY_TOP_GAINERS_MAX_RANK': '15',
        }, clear=False):
            validator = SetupQualityValidator()

        signal = {
            'symbol': 'KFINTECH',
            'action': 'BUY',
            'entry_type': 'PRE_BREAKOUT',
            'day_change': 2.8,
            'setup_sequence': 2,
            'is_reentry_setup': True,
        }
        market_data = {
            'is_top_gainer': None,
            'top_gainer_rank': None,
        }

        is_valid, message = validator.validate(signal, market_data)
        self.assertTrue(is_valid, message)
        self.assertIn('Pre-breakout OK', message)

    def test_top_gainer_rank_rejects_reentry_when_outside_threshold(self):
        with patch.dict(os.environ, {
            'CALL_OPTIONS_REENTRY_ALLOWED': 'true',
            'CALL_OPTIONS_REENTRY_REQUIRE_TOP_GAINER': 'true',
            'CALL_OPTIONS_REENTRY_TOP_GAINERS_MAX_RANK': '10',
        }, clear=False):
            validator = SetupQualityValidator()

        signal = {
            'symbol': 'KFINTECH',
            'action': 'BUY',
            'entry_type': 'PULLBACK',
            'day_change': 3.1,
            'setup_sequence': 2,
            'is_reentry_setup': True,
        }
        market_data = {
            'is_top_gainer': True,
            'top_gainer_rank': 14,
        }

        is_valid, message = validator.validate(signal, market_data)
        self.assertFalse(is_valid)
        self.assertIn('top gainer rank 14 > 10', message)


class PutOptionsSignalValidatorTests(unittest.TestCase):
    def setUp(self):
        PutOptionsSignalValidator._last_alert_by_key.clear()

    def test_put_reentry_requires_previous_alert_baseline(self):
        alert = {
            'symbol': 'TRENT',
            'action': 'SELL',
            'original_action': 'BUY_PUT',
            'option_side': 'PE',
            'entry_type': 'PRE_FALL',
            'tv_trigger_flag': 'REENTRY',
            'confidence': 90,
            'score': 90,
            'price': 950,
            'day_change': -2.5,
            'setup_sequence': 2,
        }

        with patch.dict(os.environ, {
            'PUT_OPTIONS_REENTRY_ALLOWED': 'true',
            'PUT_OPTIONS_REENTRY_MIN_ALERT_STEP_PCT': '1.0',
        }, clear=False):
            is_valid, message, processed = PutOptionsSignalValidator.validate_options_signal(alert)

        self.assertFalse(is_valid)
        self.assertIn('missing prior alert baseline', message)
        self.assertIsNone(processed)

    def test_put_sequence_two_without_reentry_flag_does_not_require_prior_baseline(self):
        alert = {
            'symbol': 'TRENT',
            'action': 'SELL',
            'original_action': 'BUY_PUT',
            'option_side': 'PE',
            'entry_type': 'PRE_FALL',
            'tv_trigger_flag': 'PRE_FALL',
            'confidence': 90,
            'score': 90,
            'price': 945,
            'day_change': -2.5,
            'setup_sequence': 2,
        }

        with patch.dict(os.environ, {
            'PUT_OPTIONS_REENTRY_ALLOWED': 'true',
            'PUT_OPTIONS_REENTRY_MIN_ALERT_STEP_PCT': '1.0',
        }, clear=False):
            is_valid, message, processed = PutOptionsSignalValidator.validate_options_signal(alert)

        self.assertTrue(is_valid, message)
        self.assertFalse(processed['is_reentry_setup'])


class PutSetupQualityValidatorTests(unittest.TestCase):
    def test_put_retrade_requires_bearish_day_change(self):
        with patch.dict(os.environ, {
            'PUT_OPTIONS_REENTRY_ALLOWED': 'true',
            'ENTRY_FILTER_MIN_RETRADE_DAY_CHANGE_PUT': '2.0',
        }, clear=False):
            validator = PutSetupQualityValidator()

        signal = {
            'symbol': 'TRENT',
            'action': 'SELL',
            'entry_type': 'PULLUP',
            'day_change': -1.2,
            'setup_sequence': 2,
            'is_reentry_setup': True,
        }

        is_valid, message = validator.validate(signal, {})
        self.assertFalse(is_valid)
        self.assertIn('retrade bearish minimum', message)


class AngelOneTopGainersTests(unittest.TestCase):
    def _build_broker(self, smart_api):
        broker = AngelOneOptionsBroker.__new__(AngelOneOptionsBroker)
        broker.smart_api = smart_api
        broker._top_gainers_cache = {}
        broker._top_gainers_cache_time = {}
        broker._top_gainers_cache_ttl_seconds = 60.0
        broker._top_gainers_fetch_lock = threading.Lock()
        broker.ensure_authenticated = lambda: True
        return broker

    def test_get_top_gainers_normalizes_symbols_and_uses_cache(self):
        calls = []

        def fake_gainers_losers(payload):
            calls.append(payload)
            return {
                'status': True,
                'data': [
                    {'tradingSymbol': 'KFINTECH26MAY26FUT', 'percentChange': 82.24},
                    {'tradingsymbol': 'VEDL26MAY26FUT', 'pChange': '53.31'},
                ],
            }

        broker = self._build_broker(SimpleNamespace(gainersLosers=fake_gainers_losers))

        with patch.object(call_broker_module, 'call_with_timeout', side_effect=lambda func, timeout, payload: func(payload)):
            first = broker.get_top_gainers(limit=2)
            second = broker.get_top_gainers(limit=2)

        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0]['symbol'], 'KFINTECH')
        self.assertEqual(first[0]['rank'], 1)
        self.assertEqual(first[1]['symbol'], 'VEDL')
        self.assertEqual(first[1]['change_pct'], 53.31)


class PutLiveDataTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.tracker = LiveDataTracker()
        self.tracker.data_dir = Path(self.temp_dir.name)
        self.tracker.live_data_file = self.tracker.data_dir / 'live_data.json'
        self.tracker.telemetry_file = self.tracker.data_dir / 'trial_sl_premium_snapshots.jsonl'
        self.tracker.candle_telemetry_file = self.tracker.data_dir / 'trial_sl_candle_context.jsonl'
        self.tracker.post_exit_results_file = self.tracker.data_dir / 'trial_sl_post_exit_results.jsonl'
        self.tracker.post_exit_state_file = self.tracker.data_dir / 'trial_sl_post_exit_watchers.json'
        self.tracker.live_data = {
            'timestamp': '',
            'market_status': 'OPEN',
            'summary': {},
            'trades': [],
        }
        self.tracker._post_exit_watchers = {}
        self.tracker._last_snapshot_at = {}
        self.tracker._candle_context_cache = {}
        self.tracker._fetch_candle_context = lambda underlying: None

    def test_update_trade_records_lowest_premium_and_close_trade_creates_post_exit_result(self):
        entry_time = '2026-05-03T10:00:00'
        exit_time = '2026-05-03T10:05:00'
        symbol = 'TEST26MAY26100PE'

        self.tracker.add_trade(
            symbol=symbol,
            underlying='TEST',
            strike=100,
            contract_type='PE',
            action='BUY',
            quantity=25,
            entry_time=entry_time,
            entry_premium=10.0,
            underlying_alert_price=100.0,
            trade_id='trade-1',
        )
        self.tracker.update_trade(
            symbol=symbol,
            current_premium=12.0,
            highest_premium=12.5,
            quantity=25,
            lowest_premium=9.5,
            trial_sl_enabled=True,
            trial_sl_price=11.2,
            hard_sl_price=9.0,
            trial_sl_updates=2,
            trail_profile='adaptive_staircase',
            trail_activation_threshold=5.0,
            trailing_gap=0.8,
            market_trend='DOWN',
            trend_strength=0.8,
        )

        open_trade = self.tracker.live_data['trades'][0]
        self.assertEqual(open_trade['lowest_premium'], 9.5)
        self.assertEqual(open_trade['highest_premium'], 12.5)

        telemetry_rows = [json.loads(line) for line in self.tracker.telemetry_file.read_text().splitlines() if line.strip()]
        self.assertEqual(len(telemetry_rows), 1)
        self.assertEqual(telemetry_rows[0]['phase'], 'open')
        self.assertEqual(telemetry_rows[0]['lowest_premium'], 9.5)
        self.assertTrue(telemetry_rows[0]['trial_sl_enabled'])
        self.assertEqual(telemetry_rows[0]['trial_sl_updates'], 2)

        self.tracker.post_exit_watch_seconds = 0
        self.tracker.post_exit_checkpoints = []
        self.tracker.close_trade(
            symbol=symbol,
            exit_time=exit_time,
            exit_premium=11.0,
            exit_reason='TRIAL_SL_HIT',
            quantity=25,
            entry_premium=10.0,
            entry_time=entry_time,
            trail_profile='adaptive_staircase',
            trail_activation_threshold=5.0,
            trailing_gap=0.8,
            market_trend='DOWN',
            trend_strength=0.8,
        )

        fake_broker = SimpleNamespace(get_market_data=lambda symbol, exchange='NFO': {'ltp': 10.8})
        with patch('put_options.optcode.angelone_options.get_options_broker', return_value=fake_broker):
            self.tracker.poll_post_exit_tracking()

        closed_trade = self.tracker.live_data['trades'][0]
        self.assertEqual(closed_trade['status'], 'CLOSED')
        self.assertFalse(self.tracker._post_exit_watchers)

        post_exit_rows = [json.loads(line) for line in self.tracker.post_exit_results_file.read_text().splitlines() if line.strip()]
        self.assertEqual(len(post_exit_rows), 1)
        self.assertEqual(post_exit_rows[0]['symbol'], symbol)
        self.assertEqual(post_exit_rows[0]['exit_reason'], 'TRIAL_SL_HIT')
        self.assertEqual(post_exit_rows[0]['max_after_exit'], 11.0)
        self.assertEqual(post_exit_rows[0]['min_after_exit'], 10.8)


if __name__ == '__main__':
    unittest.main()