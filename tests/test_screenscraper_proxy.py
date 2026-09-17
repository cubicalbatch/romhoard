"""End-to-end tests for the local ScreenScraper caching proxy."""

import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

GOOD_BODY = b'{"response": {"jeux": []}}'
ERROR_BODY = b'{"response": {"erreur": "Unknown game"}}'


class FakeUpstreamHandler(BaseHTTPRequestHandler):
    """Configurable upstream: class attrs control the next responses."""

    calls = 0
    status = 200
    body = GOOD_BODY

    def do_GET(self):
        type(self).calls += 1
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(type(self).body)))
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    """Load the proxy script, point it at a fake upstream + temp cache, serve it."""
    spec = importlib.util.spec_from_file_location(
        "screenscraper_proxy", Path(__file__).parent.parent / "scripts" / "screenscraper_proxy.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstreamHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    monkeypatch.setattr(module, "UPSTREAM", f"http://127.0.0.1:{upstream.server_address[1]}")
    monkeypatch.setattr(module, "CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    module.init_cache()
    FakeUpstreamHandler.calls = 0
    FakeUpstreamHandler.status = 200
    FakeUpstreamHandler.body = GOOD_BODY
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.ProxyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield module, f"http://127.0.0.1:{server.server_address[1]}", FakeUpstreamHandler
    server.shutdown()
    server.server_close()
    upstream.shutdown()
    upstream.server_close()


def test_hit_miss_and_key_semantics(proxy):
    module, base, upstream = proxy

    # First request: MISS from upstream.
    r1 = requests.get(f"{base}/api2/jeuRecherche.php", params={"devid": "x", "recherche": "mario"})
    assert r1.status_code == 200
    assert r1.content == GOOD_BODY
    assert r1.headers["X-ScreenScraper-Cache"] == "MISS"
    assert upstream.calls == 1

    # Same params, different order: HIT, upstream not called again.
    r2 = requests.get(f"{base}/api2/jeuRecherche.php", params={"recherche": "mario", "devid": "x"})
    assert r2.content == r1.content
    assert r2.headers["X-ScreenScraper-Cache"] == "HIT"
    assert upstream.calls == 1

    # Changed parameter value: MISS.
    r3 = requests.get(f"{base}/api2/jeuRecherche.php", params={"devid": "x", "recherche": "zelda"})
    assert r3.headers["X-ScreenScraper-Cache"] == "MISS"
    assert upstream.calls == 2


def test_cache_survives_proxy_restart(proxy):
    module, base, upstream = proxy
    r1 = requests.get(f"{base}/api2/jeuInfos.php", params={"devid": "x", "gameid": "1"})
    assert r1.headers["X-ScreenScraper-Cache"] == "MISS"

    # New server process equivalent: fresh handler instance, same SQLite file.
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.ProxyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        r2 = requests.get(
            f"http://127.0.0.1:{server.server_address[1]}/api2/jeuInfos.php",
            params={"gameid": "1", "devid": "x"},
        )
    finally:
        server.shutdown()
        server.server_close()
    assert r2.status_code == r1.status_code
    assert r2.content == r1.content
    assert r2.headers["X-ScreenScraper-Cache"] == "HIT"
    assert upstream.calls == 1


def test_404_is_cached(proxy):
    module, base, upstream = proxy
    upstream.status = 404
    upstream.body = ERROR_BODY

    r1 = requests.get(f"{base}/api2/jeuInfos.php", params={"devid": "x", "crc": "DEADBEEF"})
    assert r1.status_code == 404
    assert r1.headers["X-ScreenScraper-Cache"] == "MISS"

    # Upstream "recovers"; the stored 404 must still be replayed.
    upstream.status = 200
    upstream.body = GOOD_BODY
    r2 = requests.get(f"{base}/api2/jeuInfos.php", params={"devid": "x", "crc": "DEADBEEF"})
    assert r2.status_code == 404
    assert r2.headers["X-ScreenScraper-Cache"] == "HIT"
    assert upstream.calls == 1


def test_temporary_and_semantic_errors_never_cached(proxy):
    module, base, upstream = proxy

    upstream.status = 429
    upstream.body = b"rate limited"
    for _ in range(2):
        r = requests.get(f"{base}/api2/jeuRecherche.php", params={"devid": "x", "recherche": "mario"})
        assert r.status_code == 429
        assert r.headers["X-ScreenScraper-Cache"] == "BYPASS"

    upstream.status = 200
    upstream.body = ERROR_BODY
    for _ in range(2):
        r = requests.get(f"{base}/api2/jeuRecherche.php", params={"devid": "x", "recherche": "mario"})
        assert r.status_code == 200
        assert r.json() == {"response": {"erreur": "Unknown game"}}
        assert r.headers["X-ScreenScraper-Cache"] == "BYPASS"

    assert upstream.calls == 4


def test_routing_and_methods(proxy):
    module, base, upstream = proxy

    r = requests.get(f"{base}/not-api2/path")
    assert r.status_code == 404

    r = requests.post(f"{base}/api2/jeuRecherche.php", data={})
    assert r.status_code == 405
    assert upstream.calls == 0
