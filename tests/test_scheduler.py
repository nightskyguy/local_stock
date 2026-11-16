"""Network-free tests for the daily-update scheduler and the fetch end-date guard.

Run from the project folder:   python -m unittest discover -s tests -v
"""
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import stock_system as ss  # noqa: E402

logging.disable(logging.CRITICAL)  # importing the module opens the real log; keep tests out of it

ET = ZoneInfo('America/New_York')


def et(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


class FetchEndDateTests(unittest.TestCase):
    """2026-11-16 is a Monday, 2026-11-14 a Saturday."""

    def test_mid_session_weekday_stops_at_previous_day(self):
        self.assertEqual(ss._fetch_end_date(et(2026, 11, 16, 11, 51), datetime(2026, 11, 16).date()),
                         datetime(2026, 11, 15).date())

    def test_pre_market_weekday_stops_at_previous_day(self):
        self.assertEqual(ss._fetch_end_date(et(2026, 11, 16, 7, 0), datetime(2026, 11, 16).date()),
                         datetime(2026, 11, 15).date())

    def test_just_before_cutoff_still_previous_day(self):
        self.assertEqual(ss._fetch_end_date(et(2026, 11, 16, 16, 29), datetime(2026, 11, 16).date()),
                         datetime(2026, 11, 15).date())

    def test_after_cutoff_includes_today(self):
        self.assertEqual(ss._fetch_end_date(et(2026, 11, 16, 16, 30), datetime(2026, 11, 16).date()),
                         datetime(2026, 11, 16).date())

    def test_weekend_includes_today(self):
        self.assertEqual(ss._fetch_end_date(et(2026, 11, 14, 11, 0), datetime(2026, 11, 14).date()),
                         datetime(2026, 11, 14).date())

    def test_local_date_behind_et_uses_local_date(self):
        # 22:00 PST Mon = 01:00 ET Tue: after the close, local date is still Monday.
        self.assertEqual(ss._fetch_end_date(et(2026, 11, 17, 1, 0), datetime(2026, 11, 16).date()),
                         datetime(2026, 11, 16).date())


class SlotTests(unittest.TestCase):
    def test_slot_today_when_past_update_time(self):
        now = datetime(2026, 11, 16, 20, 0)  # Mon 20:00, slot 18:00
        self.assertEqual(ss._most_recent_update_slot(now, 18, 0), datetime(2026, 11, 16, 18, 0))

    def test_slot_yesterday_when_before_update_time(self):
        now = datetime(2026, 11, 16, 9, 0)   # Mon 09:00 -> Sunday is skipped -> Sat 18:00
        self.assertEqual(ss._most_recent_update_slot(now, 18, 0), datetime(2026, 11, 14, 18, 0))

    def test_sunday_has_no_slot(self):
        now = datetime(2026, 11, 15, 19, 0)  # Sunday evening -> Saturday's slot
        self.assertEqual(ss._most_recent_update_slot(now, 18, 0), datetime(2026, 11, 14, 18, 0))

    def test_exactly_at_slot(self):
        now = datetime(2026, 11, 17, 18, 0)
        self.assertEqual(ss._most_recent_update_slot(now, 18, 0), now)


class CatchupNeededTests(unittest.TestCase):
    NOW = datetime(2026, 11, 16, 9, 0)  # Monday morning; most recent slot = Sat 18:00

    def test_never_recorded(self):
        self.assertTrue(ss._catchup_needed(self.NOW, 18, 0, None))

    def test_garbage_value(self):
        self.assertTrue(ss._catchup_needed(self.NOW, 18, 0, 'yesterday'))

    def test_stale_last_run(self):
        self.assertTrue(ss._catchup_needed(self.NOW, 18, 0, '2026-11-13 18:00:07'))  # Fri

    def test_saturday_slot_ran(self):
        self.assertFalse(ss._catchup_needed(self.NOW, 18, 0, '2026-11-14 18:00:07'))

    def test_manual_update_after_slot_counts(self):
        self.assertFalse(ss._catchup_needed(self.NOW, 18, 0, '2026-11-16 08:51:42'))

    def test_run_just_before_slot_is_stale(self):
        self.assertTrue(ss._catchup_needed(self.NOW, 18, 0, '2026-11-14 17:59:59'))


class MisfireTests(unittest.TestCase):
    """The reason for the change: a run that is late must still fire."""

    def _run_late_job(self, **extra):
        fired = threading.Event()
        sched = BackgroundScheduler()
        late = datetime.now().astimezone() - timedelta(hours=2)
        with mock.patch.object(ss, 'scheduled_update', fired.set):
            if extra.pop('control', False):  # APScheduler defaults: 1s grace
                sched.add_job(ss.scheduled_update, 'cron', hour=18, minute=0, next_run_time=late)
            else:
                ss._register_update_job(sched, 18, 0, next_run_time=late)
            sched.start()
            try:
                return fired.wait(timeout=3)
            finally:
                sched.shutdown(wait=False)

    def test_control_default_settings_drop_a_late_run(self):
        self.assertFalse(self._run_late_job(control=True))

    def test_registered_job_runs_a_late_run(self):
        self.assertTrue(self._run_late_job())


class DbBackedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = ss.DB_PATH
        ss.DB_PATH = os.path.join(self._tmp.name, 'test.db')
        ss.setup_database()
        ss.initialize_default_config()

    def tearDown(self):
        ss.DB_PATH = self._old_db
        self._tmp.cleanup()

    def test_update_all_symbols_records_last_run(self):
        self.assertIsNone(ss.get_config('last_update_run'))
        with mock.patch.object(ss, 'get_tracked_symbols', return_value=['AAA']), \
                mock.patch.object(ss, 'smart_update_symbol', return_value=True):
            ss.update_all_symbols()
        stored = ss.get_config('last_update_run')
        self.assertIsNotNone(stored)
        self.assertLess(abs((datetime.now() - datetime.strptime(stored, '%Y-%m-%d %H:%M:%S')).total_seconds()), 5)

    def test_start_scheduler_queues_catchup_when_never_run(self):
        fired = threading.Event()
        with mock.patch.object(ss, 'scheduled_update', fired.set):
            sched = ss.start_scheduler()
            try:
                self.assertIsNotNone(sched.get_job('catchup_update'))
                self.assertIsNotNone(sched.get_job('daily_update'))
                self.assertTrue(fired.wait(timeout=10), 'catch-up job did not run')
            finally:
                sched.shutdown(wait=False)

    def test_start_scheduler_no_catchup_when_current(self):
        ss.set_config('last_update_run', (datetime.now()).strftime('%Y-%m-%d %H:%M:%S'))
        with mock.patch.object(ss, 'scheduled_update', lambda: None):
            sched = ss.start_scheduler()
            try:
                self.assertIsNone(sched.get_job('catchup_update'))
                self.assertIsNotNone(sched.get_job('daily_update'))
            finally:
                sched.shutdown(wait=False)

    def test_smart_update_uses_final_candle_end_date(self):
        conn = ss.get_db_connection()
        conn.execute("INSERT INTO symbols (symbol, active) VALUES ('ZZZ', 1)")
        conn.execute("INSERT INTO daily_quotes (symbol, quote_date, close, volume) VALUES ('ZZZ', '2026-11-12', 10, 1)")
        conn.commit()
        conn.close()
        calls = []
        with mock.patch.object(ss, '_fetch_end_date', return_value=datetime(2026, 11, 15).date()), \
                mock.patch.object(ss, 'fetch_data_multi_source',
                                  side_effect=lambda s, a, b: calls.append((s, a, b))):
            ss.smart_update_symbol('ZZZ')
        self.assertEqual(calls, [('ZZZ', '2026-11-13', '2026-11-15')])

    def test_smart_update_skips_when_nothing_final_to_fetch(self):
        conn = ss.get_db_connection()
        conn.execute("INSERT INTO symbols (symbol, active) VALUES ('ZZZ', 1)")
        conn.execute("INSERT INTO daily_quotes (symbol, quote_date, close, volume) VALUES ('ZZZ', '2026-11-13', 10, 1)")
        conn.commit()
        conn.close()
        with mock.patch.object(ss, '_fetch_end_date', return_value=datetime(2026, 11, 13).date()), \
                mock.patch.object(ss, 'fetch_data_multi_source') as fetch:
            self.assertTrue(ss.smart_update_symbol('ZZZ'))
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
