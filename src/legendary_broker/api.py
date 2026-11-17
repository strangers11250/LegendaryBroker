"""Public, read-only market-data HTTP API and same-origin dashboard."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from threading import RLock
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .storage import Database, normalize_symbol

logger = logging.getLogger(__name__)


class ResponseCache:
    """Bounded per-process cache; database revision also tracks external writers."""
    def __init__(self, maximum: int = 128, ttl: float = 60):
        self.maximum, self.ttl = maximum, ttl
        self.entries: OrderedDict = OrderedDict()
        self.lock = RLock()

    def response(self, request: Request, key: tuple, revision: int, build) -> Response:
        cache_key = (revision, *key)
        with self.lock:
            entry = self.entries.get(cache_key)
            if entry is None or entry[0] < time.monotonic():
                content = json.dumps(build(), separators=(",", ":"), allow_nan=False).encode()
                etag = '"' + hashlib.sha256(content).hexdigest() + '"'
                entry = (time.monotonic() + self.ttl, content, etag)
                self.entries[cache_key] = entry
            self.entries.move_to_end(cache_key)
            while len(self.entries) > self.maximum:
                self.entries.popitem(last=False)
        _, content, etag = entry
        headers = {"ETag": etag, "Cache-Control": "public, max-age=60, must-revalidate"}
        tags = request.headers.get("if-none-match", "").split(",")
        if etag in [tag.strip().removeprefix("W/") for tag in tags] or "*" in tags:
            return Response(status_code=304, headers=headers)
        return Response(content=content, media_type="application/json", headers=headers)


def create_app(db_path: str | Path | None = None,
               web_dir: str | Path | None = None) -> FastAPI:
    database = Database(db_path or os.getenv("BROKER_DB_PATH", "data/market.sqlite3"))
    feed = os.getenv("BROKER_FEED", "sip").lower()
    if feed not in {"sip", "iex"}:
        raise ValueError("BROKER_FEED must be sip or iex.")
    cache = ResponseCache()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
        yield

    app = FastAPI(title="LegendaryBroker market data", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.database = database
    app.state.response_cache = cache

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except sqlite3.Error:
            logger.exception("Market database unavailable")
            response = JSONResponse({"detail": "Market data temporarily unavailable."}, status_code=503)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/") and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    def cached(request, key, build):
        return cache.response(request, key, database.revision(), build)

    @app.get("/api/health")
    def health():
        database.revision()
        return {"status": "ok"}

    @app.get("/api/symbols")
    def symbols(request: Request, q: str = Query(default="", max_length=15),
                limit: int = Query(default=20, ge=1, le=100)):
        try:
            query = normalize_symbol(q) if q.strip() else ""
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return cached(request, ("symbols", query, limit),
                      lambda: {"symbols": database.symbols(query, limit, feed)})

    @app.get("/api/bars/{symbol}")
    def bars(request: Request, symbol: str, start: date | None = None,
             end: date | None = None, limit: int = Query(default=5000, ge=1, le=5000)):
        end = end or date.today()
        start = start or (end - timedelta(days=365))
        try:
            symbol = normalize_symbol(symbol)
            if start > end or (end - start).days > 3660:
                raise ValueError("Date range must be ordered and no longer than 3660 days.")
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

        def build():
            return {"symbol": symbol, "start": start.isoformat(), "end": end.isoformat(),
                    "feed": feed, "source": "alpaca", "adjustment": "all",
                    "bars": database.bars(symbol, start, end, feed, limit),
                    **database.bar_metadata(symbol, feed)}
        return cached(request, ("bars", symbol, start, end, limit), build)

    @app.get("/api/overview")
    def overview(request: Request):
        return cached(request, ("overview",), lambda: {
            "items": database.overview(feed), "feed": feed, "source": "alpaca",
            "adjustment": "all", **database.bar_metadata(feed=feed),
        })

    @app.get("/api/status")
    def status(request: Request):
        def build():
            # Explicit allowlist prevents accidental exposure of future private worker metadata.
            keys = {"last_attempt", "last_success", "last_error", "interval_hours", "running",
                    "rows_written", "symbols", "missing_symbols", "history_days"}
            worker = database.get_status()
            return {"running": False, "interval_hours": None,
                    **{key: worker[key] for key in keys if key in worker},
                    "feed": feed, "source": "alpaca", "adjustment": "all",
                    **database.bar_metadata(feed=feed)}
        return cached(request, ("status",), build)

    dist = Path(web_dir or os.getenv("BROKER_WEB_DIR") or
                Path(__file__).resolve().parents[2] / "web" / "dist")
    if (dist / "index.html").is_file():
        app.mount("/web", StaticFiles(directory=dist, html=True), name="web")

        @app.get("/", include_in_schema=False)
        def root():
            return RedirectResponse("/web/", status_code=307)
    else:
        @app.get("/", include_in_schema=False)
        def root():
            return {"service": "LegendaryBroker", "dashboard": "Build web/dist to enable /web/."}

    return app


app = create_app()
