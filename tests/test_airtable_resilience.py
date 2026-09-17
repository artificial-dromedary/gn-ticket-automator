"""Surviving Airtable being slow.

A read timeout on one request used to fail the whole scheduled scan: the run
exited non-zero, the user was not scanned, and the cause was a blip that would
have gone away on a second attempt. These cover the timeout allowance and the
retry that now sits under every call the client makes.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from requests.adapters import HTTPAdapter

import airtable_integration
from airtable_integration import RETRY_ATTEMPTS, create_airtable_client


def test_the_read_allowance_is_longer_than_the_timeout_that_failed_a_scan():
    connect, read = airtable_integration.REQUEST_TIMEOUT

    assert read > 15, "15 seconds is what a real scan timed out on"
    # An unreachable host should give up quickly; a slow one is worth waiting out.
    assert connect < read


def test_the_retry_policy_covers_the_failures_worth_retrying():
    policy = create_airtable_client("k").http.get_adapter("https://api.airtable.com").max_retries

    assert policy.read == RETRY_ATTEMPTS >= 1
    assert policy.connect == RETRY_ATTEMPTS
    # Airtable's own rate limit and gateway errors say "later", not "never".
    for status in (429, 500, 502, 503, 504):
        assert status in policy.status_forcelist
    # A write here sets one field to a fixed value, so repeating it is safe.
    assert {"GET", "PATCH"} <= set(policy.allowed_methods)
    # Anything that might create a second record is not.
    assert "POST" not in policy.allowed_methods


class _StallingHandler(BaseHTTPRequestHandler):
    """Stalls the first `stall_first` reads, then answers normally."""

    requests_seen = 0
    stall_first = 0
    stall_seconds = 0.0

    def do_GET(self):
        cls = type(self)
        cls.requests_seen += 1
        if cls.requests_seen <= cls.stall_first:
            time.sleep(cls.stall_seconds)

        body = json.dumps({"records": []}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # The client gave up on a stalled read and closed the socket.

    def log_message(self, *args):
        pass


@pytest.fixture
def stalling_airtable(monkeypatch):
    """A client pointed at a local server whose stalling each test sets up.

    Threaded on purpose: a single-threaded server would still be asleep in the
    stalled handler when the retry arrived, and would time that one out too —
    the test would then fail for its own reasons rather than the client's.
    """
    _StallingHandler.requests_seen = 0
    _StallingHandler.stall_first = 0
    _StallingHandler.stall_seconds = 0.0

    server = ThreadingHTTPServer(("127.0.0.1", 0), _StallingHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Short enough that a stalled read gives up inside the test.
    monkeypatch.setattr(airtable_integration, "REQUEST_TIMEOUT", (2, 0.25))

    client = create_airtable_client("k")
    client.base_url = f"http://127.0.0.1:{server.server_port}/v0/app/Sessions"
    # The policy is mounted for https, the only scheme Airtable speaks. Reuse that
    # same Retry here, with the waiting between attempts taken out, so the test
    # exercises the configuration production runs with rather than a stand-in.
    policy = client.http.get_adapter("https://api.airtable.com").max_retries
    client.http.mount("http://", HTTPAdapter(max_retries=policy.new(backoff_factor=0)))

    yield client

    server.shutdown()
    server.server_close()


def test_a_slow_first_read_is_retried_instead_of_failing_the_scan(stalling_airtable):
    _StallingHandler.stall_first = 1
    _StallingHandler.stall_seconds = 1.0

    sessions = stalling_airtable.get_sessions()

    assert sessions == []
    assert _StallingHandler.requests_seen == 2, "the first read timed out and was not retried"


def test_a_host_that_stays_slow_still_raises(stalling_airtable):
    """Retrying buys a blip a second chance, not an unbounded wait."""
    _StallingHandler.stall_first = RETRY_ATTEMPTS + 5
    _StallingHandler.stall_seconds = 2.0

    with pytest.raises(Exception, match="Failed to fetch sessions from Airtable"):
        stalling_airtable.get_sessions()

    assert _StallingHandler.requests_seen == RETRY_ATTEMPTS + 1
