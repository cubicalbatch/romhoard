"""End-to-end tests for the local Hasheous caching proxy."""

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

MATCH_BODY = json.dumps(
    {"name": "Tetris", "platform": {"name": "Nintendo Game Boy"}}
).encode()
LOOKUP = "/api/v1/Lookup/ByHash"


class FakeUpstreamHandler(BaseHTTPRequestHandler):
    """Configurable upstream: class attrs control the next responses."""

    calls = 0
    status = 200
    body = MATCH_BODY
    delay = 0.0
    received: list = []

    def do_POST(self):
        cls = type(self)
        cls.calls += 1
        cls.received.append(self.rfile.read(int(self.headers["Content-Length"])))
        if cls.delay:
            time.sleep(cls.delay)
        self.send_response(cls.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cls.body)))
        self.end_headers()
        self.wfile.write(cls.body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    """Load the proxy script, point it at a fake upstream + temp cache, serve it."""
    spec = importlib.util.spec_from_file_location(
        "hasheous_proxy", Path(__file__).parent.parent / "scripts" / "hasheous_proxy.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstreamHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        module, "UPSTREAM", f"http://127.0.0.1:{upstream.server_address[1]}"
    )
    monkeypatch.setattr(module, "CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    monkeypatch.setattr(module, "UPSTREAM_INTERVAL", 0)
    module.init_cache()
    FakeUpstreamHandler.calls = 0
    FakeUpstreamHandler.status = 200
    FakeUpstreamHandler.body = MATCH_BODY
    FakeUpstreamHandler.delay = 0.0
    FakeUpstreamHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.ProxyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield module, f"http://127.0.0.1:{server.server_address[1]}", FakeUpstreamHandler
    server.shutdown()
    server.server_close()
    upstream.shutdown()
    upstream.server_close()


def test_miss_then_hit_with_canonical_body(proxy):
    _, base, upstream = proxy

    r1 = requests.post(f"{base}{LOOKUP}", json={"crc": "ABCD1234"})
    assert r1.status_code == 200
    assert r1.content == MATCH_BODY
    assert r1.headers["X-Hasheous-Cache"] == "MISS"
    assert json.loads(upstream.received[0]) == {"crc": "ABCD1234"}

    # Same hash, different case and key spelling: HIT.
    r2 = requests.post(f"{base}{LOOKUP}", json={"CRC": "abcd1234"})
    assert r2.headers["X-Hasheous-Cache"] == "HIT"
    assert r2.content == MATCH_BODY
    assert upstream.calls == 1

    r3 = requests.post(f"{base}{LOOKUP}", json={"crc": "00000000"})
    assert r3.headers["X-Hasheous-Cache"] == "MISS"
    assert upstream.calls == 2


def test_not_found_is_cached(proxy):
    _, base, upstream = proxy
    upstream.status = 404
    upstream.body = b"{}"

    requests.post(f"{base}{LOOKUP}", json={"crc": "deadbeef"})
    r = requests.post(f"{base}{LOOKUP}", json={"crc": "deadbeef"})

    assert r.status_code == 404
    assert r.headers["X-Hasheous-Cache"] == "HIT"
    assert upstream.calls == 1


@pytest.mark.parametrize(
    "status,body", [(500, b"oops"), (429, b"slow"), (200, b"<html>")]
)
def test_errors_are_passed_through_but_not_cached(proxy, status, body):
    _, base, upstream = proxy
    upstream.status = status
    upstream.body = body

    r1 = requests.post(f"{base}{LOOKUP}", json={"crc": "deadbeef"})
    r2 = requests.post(f"{base}{LOOKUP}", json={"crc": "deadbeef"})

    assert r1.status_code == status
    assert r2.headers["X-Hasheous-Cache"] == "BYPASS"
    assert upstream.calls == 2


def test_unreachable_upstream_returns_502(proxy, monkeypatch):
    module, base, _ = proxy
    monkeypatch.setattr(module, "UPSTREAM", "http://127.0.0.1:9")

    r = requests.post(f"{base}{LOOKUP}", json={"crc": "deadbeef"})

    assert r.status_code == 502


def test_rejects_other_paths_methods_and_bodies(proxy):
    _, base, upstream = proxy

    assert requests.post(f"{base}/api/v1/Other", json={"crc": "x"}).status_code == 404
    assert requests.get(f"{base}{LOOKUP}").status_code == 405
    assert requests.post(f"{base}{LOOKUP}", data=b"not json").status_code == 400
    assert requests.post(f"{base}{LOOKUP}", json=["crc"]).status_code == 400
    assert upstream.calls == 0


def test_upstream_requests_are_paced(proxy, monkeypatch):
    module, base, upstream = proxy
    monkeypatch.setattr(module, "UPSTREAM_INTERVAL", 0.3)

    start = time.monotonic()
    threads = [
        threading.Thread(
            target=requests.post,
            args=(f"{base}{LOOKUP}",),
            kwargs={"json": {"crc": f"{i:08x}"}},
        )
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert upstream.calls == 3
    assert time.monotonic() - start >= 0.6


def test_slow_upstream_call_does_not_stall_queued_clients(proxy, monkeypatch):
    """The fetch runs outside the pacing lock.

    A client waits only for its start slot, not for other clients' in-flight
    upstream calls, so a few slow upstream answers cannot push waiting
    clients past their read timeout.
    """
    module, base, upstream = proxy
    monkeypatch.setattr(module, "UPSTREAM_INTERVAL", 0.2)
    monkeypatch.setattr(FakeUpstreamHandler, "delay", 0.4)

    start = time.monotonic()
    threads = [
        threading.Thread(
            target=requests.post,
            args=(f"{base}{LOOKUP}",),
            kwargs={"json": {"crc": f"{i:08x}"}},
        )
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start

    assert upstream.calls == 3
    # Starts stay paced (slots 0.2 s apart) ...
    assert elapsed >= 0.6
    # ... but the 0.4 s upstream fetches overlap: holding the lock through
    # each call would serialize the three fetches to >= 1.4 s of waits.
    assert elapsed < 1.2
