"""Local read-through caching proxy for the ScreenScraper API.

Development-only tool for matching experiments: replays previously seen
ScreenScraper responses so repeated requests never consume rate limits.
Runs on loopback, stores responses permanently in SQLite, and is active
only when RomHoard sets SCREENSCRAPER_API_BASE to point at it.

Usage:
    uv run python scripts/screenscraper_proxy.py

Cache reset: delete data/screenscraper-proxy.sqlite3.
"""

import contextlib
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import requests

BIND_HOST = "127.0.0.1"
PORT = 8765
UPSTREAM = "https://api.screenscraper.fr"
CACHE_PATH = "data/screenscraper-proxy.sqlite3"
UPSTREAM_TIMEOUT = (10, 180)  # Matches ScreenScraperClient.DEFAULT_TIMEOUT

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    cache_key TEXT PRIMARY KEY,
    status INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    body BLOB NOT NULL,
    created_at TEXT NOT NULL
)
"""


def _cache_key(method: str, path: str, query: str) -> str:
    """Hash method, path, and canonical query params. Never store the raw query (credentials)."""
    params = sorted(parse_qsl(query, keep_blank_values=True))
    material = json.dumps([method, path, params], separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cacheable(status: int, body: bytes) -> bool:
    """True only for a ScreenScraper answer: a documented 404 no-match, or a
    200 whose body is valid JSON with a top-level response object and no error."""
    if status == 404:
        return True
    if status != 200:
        return False
    try:
        data = json.loads(body)
    except ValueError:
        return False
    response = data.get("response") if isinstance(data, dict) else None
    return isinstance(response, dict) and "erreur" not in response


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "ScreenScraperProxy/1.0"

    def do_GET(self):
        parts = urlsplit(self.path)
        if not parts.path.startswith("/api2/"):
            self._reject(404)
            return

        key = _cache_key("GET", parts.path, parts.query)
        cached = self._lookup(key)
        if cached is not None:
            self._send(parts.path, *cached, cache="HIT")
            return

        try:
            upstream = requests.get(f"{UPSTREAM}{self.path}", timeout=UPSTREAM_TIMEOUT)
        except requests.RequestException:
            self._send(parts.path, 502, "text/plain", b"upstream unreachable", cache="BYPASS")
            return

        status = upstream.status_code
        content_type = upstream.headers.get("Content-Type", "application/octet-stream")
        body = upstream.content
        if _cacheable(status, body):
            self._store(key, status, content_type, body)
            self._send(parts.path, status, content_type, body, cache="MISS")
        else:
            self._send(parts.path, status, content_type, body, cache="BYPASS")

    def do_POST(self):
        self._reject(405)

    do_PUT = do_PATCH = do_DELETE = do_HEAD = do_POST

    def _reject(self, status: int):
        self._log(urlsplit(self.path).path, status, "-")
        self.send_error(status)

    def _send(self, path: str, status: int, content_type: str, body: bytes, cache: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-ScreenScraper-Cache", cache)
        self.end_headers()
        self.wfile.write(body)
        self._log(path, status, cache)

    def _log(self, path: str, status: int, cache: str):
        # Never log query strings: they carry devid/devpassword/ssid/sspassword.
        print(f"{self.log_date_time_string()} {path} {status} {cache}", file=sys.stderr)

    def log_message(self, format, *args):
        pass  # Suppressed: default format includes the full request path with query.

    @staticmethod
    def _connect() -> contextlib.closing[sqlite3.Connection]:
        return contextlib.closing(sqlite3.connect(CACHE_PATH))

    def _lookup(self, key: str):
        with self._connect() as conn:
            return conn.execute(
                "SELECT status, content_type, body FROM responses WHERE cache_key = ?",
                (key,),
            ).fetchone()

    def _store(self, key: str, status: int, content_type: str, body: bytes):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO responses VALUES (?, ?, ?, ?, ?)",
                (key, status, content_type, body, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()


def init_cache():
    with contextlib.closing(sqlite3.connect(CACHE_PATH)) as conn:
        conn.execute(_SCHEMA)
        conn.commit()


def main():
    init_cache()
    server = ThreadingHTTPServer((BIND_HOST, PORT), ProxyHandler)
    print(
        f"ScreenScraper proxy on http://{BIND_HOST}:{PORT} -> {UPSTREAM} (cache: {CACHE_PATH})",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
