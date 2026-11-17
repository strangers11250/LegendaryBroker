"""Read-only integration smoke for the built dashboard and lookup API."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def verify(base: str, allow_empty: bool = False) -> None:
    def request(path: str, *, headers=None, method="GET"):
        try:
            response = urlopen(Request(base.rstrip("/") + path, headers=headers or {},
                                       method=method), timeout=15)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    code, _, body = request("/api/health")
    assert code == 200 and json.loads(body)["status"] == "ok", "API health failed"
    code, headers, page = request("/web/")
    assert code == 200, "Built dashboard not served"
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assets = re.findall(r'(?:src|href)="(/web/assets/[^\"]+)"', page.decode())
    assert assets, "No production assets linked"
    for asset in assets:
        code, _, body = request(asset)
        assert code == 200, f"Missing static asset: {asset}"
        assert b"ALPACA_API_SECRET=" not in body
    code, headers, body = request("/api/overview")
    overview = json.loads(body)
    assert code == 200 and len(overview["items"]) == 4
    assert all("proxy" in item["name"] for item in overview["items"])
    assert request("/api/overview", headers={"If-None-Match": headers["ETag"]})[0] == 304
    code, _, body = request("/api/symbols?q=A")
    assert code == 200 and "symbols" in json.loads(body)
    end = date.today()
    code, _, body = request(f"/api/bars/AAPL?start={end-timedelta(days=60)}&end={end}")
    history = json.loads(body)
    assert code == 200 and history["symbol"] == "AAPL"
    assert all(bar["symbol"] == "AAPL" for bar in history["bars"])
    if not allow_empty:
        assert all(item["close"] for item in overview["items"]), "Missing benchmark data"
        assert history["bars"], "Missing AAPL history"
    assert request("/api/bars/AAPL?start=2026-11-16&end=2026-01-01")[0] == 422
    assert request("/api/bars/AAPL", method="POST")[0] == 405
    code, _, body = request("/api/status")
    status = json.loads(body)
    assert code == 200
    print(json.dumps({"assets": len(assets), "proxy_cards": 4,
                      "stock_sessions": len(history["bars"]), "etag_status": 304,
                      "last_success": status.get("last_success"),
                      "last_error": status.get("last_error")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()
    verify(args.base, args.allow_empty)
