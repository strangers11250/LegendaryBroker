from datetime import date
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pandas as pd

from legendary_broker.api import create_app
from legendary_broker.storage import Database


def sample(close=102.0, symbol="SPY"):
    return pd.DataFrame([
        {"date": "2026-09-22", "symbol": symbol, "open": 100, "high": 103,
         "low": 99, "close": 101, "volume": 1000.5},
        {"date": "2026-09-23", "symbol": symbol, "open": 101, "high": 105,
         "low": 100, "close": close, "volume": 1100},
    ])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "bars.sqlite3")
        self.db.initialize()

    def test_upsert_is_idempotent_corrects_prices_and_isolates_feed(self):
        self.assertEqual(self.db.upsert_bars(sample()), 2)
        revision = self.db.revision()
        self.assertEqual(self.db.upsert_bars(sample()), 0)
        self.assertEqual(self.db.revision(), revision)
        self.assertEqual(self.db.upsert_bars(sample(104)), 1)
        self.db.upsert_bars(sample(103), feed="iex")
        bars = self.db.bars("spy", date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual([row["close"] for row in bars], [101, 104])
        self.assertEqual(bars[0]["volume"], 1000.5)
        self.assertEqual(self.db.overview()[0]["change_percent"], (104 / 101 - 1) * 100)

    def test_invalid_batch_is_atomic_and_rejects_nonfinite_values(self):
        for column, value in (("close", float("inf")), ("volume", float("nan")),
                              ("high", 1), ("symbol", "SPY';DROP"), ("date", "NaT")):
            with self.subTest(column=column):
                frame = sample()
                frame.loc[1, column] = value
                with self.assertRaises((ValueError, TypeError)):
                    self.db.upsert_bars(frame)
                self.assertEqual(self.db.symbols(), [])

    def test_aware_dates_use_new_york_session(self):
        frame = sample().iloc[[0]].copy()
        frame["date"] = [pd.Timestamp("2026-09-23T00:30:00Z")]
        self.db.upsert_bars(frame)
        self.assertEqual(self.db.symbols()[0]["latest_date"], "2026-09-22")

    def test_rejects_duplicate_batch_and_unsafe_queries(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.db.upsert_bars(pd.concat([sample(), sample()]))
        with self.assertRaises(ValueError):
            self.db.symbols("%")
        with self.assertRaises(ValueError):
            self.db.bars("SPY", date(2000, 1, 1), date(2026, 1, 1))

    def test_status_merges_and_noop_does_not_invalidate_cache(self):
        self.db.update_status(last_success="2026-09-23T23:00:00Z", interval_hours=4)
        revision = self.db.revision()
        self.db.update_status(interval_hours=4)
        self.assertEqual(self.db.revision(), revision)
        self.db.update_status(interval_hours=2)
        self.assertEqual(self.db.get_status()["interval_hours"], 2)
        self.assertEqual(self.db.get_status()["last_success"], "2026-09-23T23:00:00Z")

    def test_retention_prunes_only_refreshed_symbols_and_feed_atomically(self):
        self.db.upsert_bars(sample())
        self.db.upsert_bars(sample(symbol="QQQ"))
        self.db.upsert_bars(sample(), feed="iex")
        self.db.upsert_bars(sample(104).iloc[[1]], retain_start=date(2026, 9, 23),
                            retain_symbols=["SPY"])
        start, end = date(2026, 9, 1), date(2026, 9, 30)
        self.assertEqual(len(self.db.bars("SPY", start, end)), 1)
        self.assertEqual(len(self.db.bars("QQQ", start, end)), 2)
        self.assertEqual(len(self.db.bars("SPY", start, end, feed="iex")), 2)
        self.assertEqual(self.db.bars("SPY", start, end)[0]["close"], 104)

    def test_authoritative_snapshot_replaces_removed_rows_symbols_but_not_other_feeds(self):
        self.db.upsert_bars(sample())
        self.db.upsert_bars(sample(symbol="QQQ"))
        self.db.upsert_bars(sample(), feed="iex")
        self.db.replace_history(sample(104).iloc[[1]], symbols=["SPY"])
        start, end = date(2026, 9, 1), date(2026, 9, 30)
        self.assertEqual(len(self.db.bars("SPY", start, end)), 1)
        self.assertEqual(self.db.bars("QQQ", start, end), [])
        self.assertEqual(len(self.db.bars("SPY", start, end, feed="iex")), 2)
        with self.assertRaises(ValueError):
            self.db.replace_history(sample(), symbols=["SPY", "QQQ"])
        self.assertEqual(self.db.bars("SPY", start, end)[0]["close"], 104)


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "bars.sqlite3"
        with patch.dict(os.environ, {"BROKER_FEED": "sip"}):
            self.app = create_app(self.path)
        self.client = self.enterContext(TestClient(self.app))
        self.db = self.app.state.database

    def test_health_empty_database_and_index_proxy_labels(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        body = self.client.get("/api/overview").json()
        self.assertEqual(len(body["items"]), 4)
        self.assertIsNone(body["items"][0]["close"])
        self.assertIn("ETF proxy", body["items"][0]["name"])
        self.assertEqual(self.client.get("/api/symbols").json(), {"symbols": []})
        self.assertEqual(self.client.get("/api/bars/ZZZ").json()["bars"], [])

    def test_api_query_validation_readonly_and_security_headers(self):
        for path in ("/api/bars/SPY?start=2026-09-23&end=2026-09-22",
                     "/api/bars/SPY?start=2000-01-01&end=2026-01-01",
                     "/api/bars/SPY?start=wrong", "/api/bars/SPY?limit=5001",
                     "/api/symbols?q=%25", "/api/bars/SPY%27"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 422)
        response = self.client.post("/api/bars/SPY", json={"close": 999})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_etag_and_external_writer_invalidate_cached_data(self):
        self.db.upsert_bars(sample())
        url = "/api/bars/spy?start=2026-09-01&end=2026-09-30"
        response = self.client.get(url)
        self.assertEqual(response.json()["symbol"], "SPY")
        self.assertEqual(response.json()["bars"][-1]["close"], 102)
        etag = response.headers["etag"]
        self.assertEqual(self.client.get(url, headers={"If-None-Match": etag}).status_code, 304)
        external = Database(self.path)
        external.upsert_bars(sample(104))
        changed = self.client.get(url, headers={"If-None-Match": etag})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["bars"][-1]["close"], 104)
        self.assertNotEqual(changed.headers["etag"], etag)

    def test_status_never_exposes_unknown_private_worker_metadata(self):
        self.db.update_status(interval_hours=2, api_secret="never expose", running=True)
        response = self.client.get("/api/status").json()
        self.assertEqual(response["interval_hours"], 2)
        self.assertNotIn("api_secret", response)

    def test_database_failure_is_sanitized(self):
        with patch.object(self.db, "revision", side_effect=__import__("sqlite3").OperationalError("private path")):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private path", response.text)

    def test_static_dashboard_mount_and_redirect(self):
        dist = Path(self.temp.name) / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<h1>Dashboard</h1>", encoding="utf-8")
        with TestClient(create_app(self.path, dist)) as client:
            self.assertEqual(client.get("/", follow_redirects=False).headers["location"], "/web/")
            self.assertIn("Dashboard", client.get("/web/").text)
            self.assertEqual(client.get("/web/../../bars.sqlite3").status_code, 404)


if __name__ == "__main__":
    unittest.main()
