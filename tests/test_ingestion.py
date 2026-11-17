import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from legendary_broker.ingestion import (
    IngestionConfig, ProviderError, completed_bars, fetch_daily, load_config,
    run_once, run_worker, worker_lock,
)
from legendary_broker.storage import Database


NOW = datetime(2026, 11, 16, 18, tzinfo=timezone.utc)


def sample(symbols=("SPY", "QQQ"), close=101):
    return pd.DataFrame([
        {"date": "2026-11-13", "symbol": symbol, "open": 100, "high": 104,
         "low": 99, "close": close, "volume": 1000}
        for symbol in symbols
    ])


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "market.sqlite3")
        self.db.initialize()
        self.config = IngestionConfig(symbols=("SPY", "QQQ"))

    def rows(self):
        with self.db.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM bars ORDER BY symbol")]

    def test_stores_complete_batch_and_idempotently_repairs_adjustments(self):
        self.assertTrue(run_once(self.db, self.config, now=NOW, fetcher=lambda *_: sample()))
        self.assertTrue(run_once(self.db, self.config, now=NOW,
                                 fetcher=lambda *_: sample(close=102)))
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual({row["close"] for row in self.rows()}, {102})
        self.assertEqual({row["adjustment"] for row in self.rows()}, {"all"})
        self.assertEqual(self.db.get_status()["rows_written"], 2)
        self.assertIsNotNone(self.db.get_status()["last_success"])

    def test_partial_symbol_response_preserves_all_previous_bars(self):
        run_once(self.db, self.config, now=NOW, fetcher=lambda *_: sample())
        success = self.db.get_status()["last_success"]
        self.assertFalse(run_once(self.db, self.config, now=NOW,
                                  fetcher=lambda *_: sample(("SPY",), close=102)))
        self.assertEqual({row["close"] for row in self.rows()}, {101})
        status = self.db.get_status()
        self.assertEqual(status["last_error"], "configured_symbols_missing")
        self.assertEqual(status["missing_symbols"], ["QQQ"])
        self.assertEqual(status["last_success"], success)
        self.assertFalse(status["running"])

    def test_authoritative_snapshot_removes_old_and_unconfigured_rows(self):
        old = sample(("SPY", "QQQ", "MSFT"))
        old["date"] = "2010-01-04"
        self.db.upsert_bars(old)
        # A different feed is deliberately preserved in its own namespace.
        self.db.upsert_bars(old, feed="iex")
        self.assertTrue(run_once(self.db, self.config, now=NOW, fetcher=lambda *_: sample()))
        sip = [row for row in self.rows() if row["feed"] == "sip"]
        iex = [row for row in self.rows() if row["feed"] == "iex"]
        self.assertEqual({row["symbol"] for row in sip}, {"SPY", "QQQ"})
        self.assertEqual({row["session_date"] for row in sip}, {"2026-11-13"})
        self.assertEqual(len(iex), 3)

    def test_bad_provider_bar_does_not_publish_partial_batch(self):
        run_once(self.db, self.config, now=NOW, fetcher=lambda *_: sample())
        bad = sample(close=102)
        bad.loc[0, "high"] = 98
        self.assertFalse(run_once(self.db, self.config, now=NOW, fetcher=lambda *_: bad))
        self.assertEqual({row["close"] for row in self.rows()}, {101})
        self.assertEqual(self.db.get_status()["last_error"], "data_validation_failed")

    def test_provider_exception_never_exposes_secret(self):
        def broken(*_):
            raise RuntimeError("SECRET example credential in provider body")
        with self.assertLogs("legendary_broker.ingestion", level="ERROR") as output:
            self.assertFalse(run_once(self.db, self.config, now=NOW, fetcher=broken))
        self.assertNotIn("SECRET", " ".join(output.output))
        self.assertNotIn("SECRET", json.dumps(self.db.get_status()))

    def test_rejects_current_session_even_with_injected_provider(self):
        current = sample()
        current["date"] = NOW.date().isoformat()
        self.assertFalse(run_once(self.db, self.config, now=NOW, fetcher=lambda *_: current))
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.db.get_status()["last_error"], "invalid_session_dates")

    def test_lock_prevents_overlapping_worker_and_releases(self):
        with worker_lock(self.db.path) as first:
            self.assertTrue(first)
            with worker_lock(self.db.path) as second:
                self.assertFalse(second)
            fetcher = unittest.mock.Mock()
            self.assertTrue(run_once(self.db, self.config, now=NOW, fetcher=fetcher))
            fetcher.assert_not_called()
        with worker_lock(self.db.path) as acquired:
            self.assertTrue(acquired)

    def test_retry_is_bounded_and_interruptible(self):
        fetcher = unittest.mock.Mock(side_effect=ProviderError("provider_unavailable", transient=True))
        stop = unittest.mock.Mock()
        stop.wait.return_value = False
        stop.is_set.return_value = False
        self.assertFalse(run_once(self.db, self.config, now=NOW, fetcher=fetcher, stop=stop))
        self.assertEqual(fetcher.call_count, 3)
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [2, 4])
        stop.wait.return_value = True
        fetcher.reset_mock()
        self.assertFalse(run_once(self.db, self.config, now=NOW, fetcher=fetcher, stop=stop))
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(self.db.get_status()["last_error"], "interrupted")

    def test_configuration_validation_and_environment_precedence(self):
        file = Path(self.temp.name) / "ingestion.json"
        file.write_text(json.dumps({"interval_hours": 3, "symbols": ["spy"], "feed": "iex"}))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_config(file).interval_hours, 3)
            self.assertEqual(load_config(file).symbols, ("SPY",))
            with patch.dict(os.environ, {"BROKER_FETCH_INTERVAL_HOURS": "0.5"}):
                self.assertEqual(load_config(file).interval_hours, 0.5)
            for value in [0, -1, "nan", "inf", True]:
                file.write_text(json.dumps({"interval_hours": value}))
                with self.assertRaises(ValueError):
                    load_config(file)
            file.write_text(json.dumps({"symbols": ["SPY", "SPY"]}))
            with self.assertRaises(ValueError):
                load_config(file)
            for values in [{"history_days": 30.5}, {"history_days": float("inf")},
                           {"feed": 3}, {"history_days": True}]:
                file.write_text(json.dumps(values))
                with self.assertRaises((ValueError, TypeError)):
                    load_config(file)

    def test_worker_reloads_interval_while_waiting(self):
        file = Path(self.temp.name) / "ingestion.json"
        file.write_text('{"interval_hours":6}')
        stop = threading.Event()
        moment = [NOW]
        intervals = []

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment[0]

        def refresh(_db, config, **_kwargs):
            intervals.append(config.interval_hours)
            if len(intervals) == 2:
                stop.set()

        def wait(_seconds):
            file.write_text('{"interval_hours":0.25}')
            moment[0] += timedelta(minutes=15)
            return False

        with patch.dict(os.environ, {}, clear=True), \
                patch("legendary_broker.ingestion.datetime", Clock), \
                patch("legendary_broker.ingestion.run_once", refresh), \
                patch.object(stop, "wait", wait):
            run_worker(self.db, file, stop)
        self.assertEqual(intervals, [6, 0.25])


class ProviderTests(unittest.TestCase):
    def test_new_york_date_mapping_and_current_session_exclusion(self):
        # 01:00 UTC belongs to the preceding NY date, including across DST.
        raw = pd.DataFrame([
            {"symbol": "SPY", "timestamp": "2026-11-14T01:00:00Z", "open": 100,
             "high": 103, "low": 99, "close": 101, "volume": 1000},
            {"symbol": "SPY", "timestamp": "2026-11-16T05:00:00Z", "open": 101,
             "high": 104, "low": 100, "close": 102, "volume": 1100},
        ])
        frame = completed_bars(raw, date(2026, 11, 1), date(2026, 11, 16))
        self.assertEqual(frame.date.dt.date.tolist(), [date(2026, 11, 13)])

    def test_pagination_and_explicit_adjustment_feed_and_timeout(self):
        pages = [
            {"bars": {"SPY": [{"t": "2026-11-13T05:00:00Z", "o": 100, "h": 103,
                                "l": 99, "c": 101, "v": 1000}]}, "next_page_token": "page2"},
            {"bars": {"QQQ": [{"t": "2026-11-13T05:00:00Z", "o": 100, "h": 103,
                                "l": 99, "c": 101, "v": 1000}]}, "next_page_token": None},
        ]
        with patch.dict(os.environ, {"ALPACA_API_KEY": "test", "ALPACA_API_SECRET": "test"}), \
                patch("legendary_broker.ingestion.urlopen",
                      side_effect=[io.BytesIO(json.dumps(page).encode()) for page in pages]) as client:
            frame = fetch_daily(IngestionConfig(symbols=("SPY", "QQQ")),
                                date(2026, 11, 1), date(2026, 11, 16), NOW)
        self.assertEqual(set(frame.symbol), {"SPY", "QQQ"})
        first = parse_qs(urlsplit(client.call_args_list[0].args[0].full_url).query)
        second = parse_qs(urlsplit(client.call_args_list[1].args[0].full_url).query)
        self.assertEqual(first["adjustment"], ["all"])
        self.assertEqual(first["feed"], ["sip"])
        self.assertEqual(second["page_token"], ["page2"])
        self.assertEqual(client.call_args_list[0].kwargs["timeout"], 30)

    def test_authorization_response_is_sanitized_and_not_retried(self):
        failure = HTTPError("https://example.invalid", 403, "SECRET", {}, None)
        with patch.dict(os.environ, {"ALPACA_API_KEY": "test", "ALPACA_API_SECRET": "test"}), \
                patch("legendary_broker.ingestion.urlopen", side_effect=failure), \
                self.assertRaises(ProviderError) as caught:
            fetch_daily(IngestionConfig(), date(2026, 11, 1), date(2026, 11, 16), NOW)
        self.assertEqual(caught.exception.code, "provider_authorization_failed")
        self.assertFalse(caught.exception.transient)
        self.assertNotIn("SECRET", str(caught.exception))

    def test_repeated_pagination_token_is_rejected(self):
        page = {"bars": {}, "next_page_token": "again"}
        with patch.dict(os.environ, {"ALPACA_API_KEY": "test", "ALPACA_API_SECRET": "test"}), \
                patch("legendary_broker.ingestion.urlopen", side_effect=[
                    io.BytesIO(json.dumps(page).encode()), io.BytesIO(json.dumps(page).encode())
                ]), self.assertRaises(ProviderError) as caught:
            fetch_daily(IngestionConfig(), date(2026, 11, 1), date(2026, 11, 16), NOW)
        self.assertEqual(caught.exception.code, "invalid_provider_pagination")

    def test_midnight_request_end_respects_delayed_sip_entitlement(self):
        just_after_midnight = datetime(2026, 11, 16, 5, 2, tzinfo=timezone.utc)
        page = {"bars": {}, "next_page_token": None}
        with patch.dict(os.environ, {"ALPACA_API_KEY": "test", "ALPACA_API_SECRET": "test"}), \
                patch("legendary_broker.ingestion.urlopen",
                      return_value=io.BytesIO(json.dumps(page).encode())) as client:
            fetch_daily(IngestionConfig(), date(2026, 11, 1), date(2026, 11, 16),
                        just_after_midnight)
        query = parse_qs(urlsplit(client.call_args.args[0].full_url).query)
        end = datetime.fromisoformat(query["end"][0])
        self.assertLessEqual(end, just_after_midnight - timedelta(minutes=16))


if __name__ == "__main__":
    unittest.main()
