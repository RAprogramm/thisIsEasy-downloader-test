"""Tests for speedtest.py.

Pure-function unit tests plus integration tests against a local HTTP server
bound to the loopback interface. No external network access is used;
pytest-socket enforces this suite-wide (see pyproject.toml addopts).
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

import pytest

import speedtest

PAYLOAD = b"x" * (1024 * 1024)


class RecordingHandler(BaseHTTPRequestHandler):
    """Serve fixed payloads and record request statistics per handler class."""

    requests_seen = 0
    in_flight = 0
    max_in_flight = 0
    accept_encodings: list[str] = []

    def log_message(self, format: str, *args: object) -> None:
        """Silence the standard per-request stderr logging."""

    def do_GET(self) -> None:
        """Answer GET requests according to the path."""
        cls = type(self)
        cls.requests_seen += 1
        cls.in_flight += 1
        cls.max_in_flight = max(cls.max_in_flight, cls.in_flight)
        cls.accept_encodings.append(self.headers.get("Accept-Encoding", ""))
        try:
            if self.path == "/missing":
                self.send_error(404)
                return
            if self.path == "/error":
                self.send_error(500)
                return
            if self.path == "/slow":
                time.sleep(2.0)
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            with contextlib.suppress(OSError):
                self.wfile.write(PAYLOAD)
        finally:
            cls.in_flight -= 1


class Server(NamedTuple):
    """Handle to the local test server."""

    url: str
    handler: type[RecordingHandler]


@pytest.fixture
def server() -> Iterator[Server]:
    """Start a local HTTP server on a free loopback port for one test."""
    handler = type(
        "ScopedHandler",
        (RecordingHandler,),
        {
            "requests_seen": 0,
            "in_flight": 0,
            "max_in_flight": 0,
            "accept_encodings": [],
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield Server(url=f"http://127.0.0.1:{port}", handler=handler)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_exactly_ten_requests_by_default(server: Server) -> None:
    assert speedtest.main([server.url]) == 0
    assert server.handler.requests_seen == 10


def test_total_bytes_match_payload(server: Server) -> None:
    results = speedtest.run(server.url, requests=3, timeout_s=10.0)
    summary = speedtest.compute(results)
    assert summary.total_bytes == 3 * len(PAYLOAD)


def test_requests_are_sequential(server: Server) -> None:
    assert speedtest.main([server.url, "-n", "4"]) == 0
    assert server.handler.max_in_flight == 1


def test_identity_encoding_requested(server: Server) -> None:
    assert speedtest.main([server.url, "-n", "1"]) == 0
    assert server.handler.accept_encodings == ["identity"]


def test_fetch_once_reports_bytes(server: Server) -> None:
    result = speedtest.fetch_once(server.url, timeout_s=10.0)
    assert result.bytes_read == len(PAYLOAD)
    assert result.elapsed_s > 0


def test_http_404_exits_network_error(
    server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    code = speedtest.main([f"{server.url}/missing"])
    captured = capsys.readouterr()
    assert code == 1
    assert "404" in captured.err
    assert "Traceback" not in captured.err


def test_http_500_exits_network_error(
    server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    code = speedtest.main([f"{server.url}/error"])
    captured = capsys.readouterr()
    assert code == 1
    assert "500" in captured.err
    assert "Traceback" not in captured.err


def test_timeout_exits_network_error(
    server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    code = speedtest.main([f"{server.url}/slow", "-t", "0.2"])
    captured = capsys.readouterr()
    assert code == 1
    assert "timed out" in captured.err
    assert "Traceback" not in captured.err


def test_invalid_scheme_exits_args_error() -> None:
    assert speedtest.main(["ftp://example.com/file"]) == 2


def test_missing_host_exits_args_error() -> None:
    assert speedtest.main(["http:///file"]) == 2


def test_garbage_url_exits_args_error() -> None:
    assert speedtest.main(["not a url"]) == 2


def test_missing_url_argument_exits_args_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        speedtest.parse_args([])
    assert exit_info.value.code == 2


def test_non_positive_requests_exits_args_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        speedtest.parse_args(["http://example.com", "-n", "0"])
    assert exit_info.value.code == 2


def test_non_positive_timeout_exits_args_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        speedtest.parse_args(["http://example.com", "-t", "0"])
    assert exit_info.value.code == 2


def test_compute_formula_on_synthetic_data() -> None:
    results = [speedtest.RequestResult(bytes_read=5_000_000, elapsed_s=1.0)] * 10
    summary = speedtest.compute(results)
    assert summary.requests == 10
    assert summary.total_bytes == 50_000_000
    assert summary.total_time_s == pytest.approx(10.0)
    assert summary.avg_time_s == pytest.approx(1.0)
    assert summary.speed_mbit_s == pytest.approx(40.0)
    assert summary.speed_mbyte_s == pytest.approx(5.0)


def test_compute_weights_by_size_not_by_mean_speed() -> None:
    results = [
        speedtest.RequestResult(bytes_read=9_000_000, elapsed_s=1.0),
        speedtest.RequestResult(bytes_read=1_000_000, elapsed_s=0.1),
    ]
    summary = speedtest.compute(results)
    assert summary.speed_mbit_s == pytest.approx(10_000_000 * 8 / 1.1 / 1_000_000)
    assert summary.speed_mbit_s != pytest.approx((72.0 + 80.0) / 2)


def test_compute_empty_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        speedtest.compute([])


def test_compute_zero_total_time_is_not_a_division_error() -> None:
    summary = speedtest.compute([speedtest.RequestResult(bytes_read=0, elapsed_s=0.0)])
    assert summary.speed_mbit_s == 0.0
    assert summary.avg_time_s == 0.0


def test_format_report_contains_units_and_byte_count() -> None:
    summary = speedtest.compute(
        [speedtest.RequestResult(bytes_read=52_400_000, elapsed_s=8.05)]
    )
    report = speedtest.format_report(summary)
    assert "52.40 MB (52,400,000 bytes)" in report
    assert "Mbit/s" in report
    assert "MB/s" in report


def test_validate_url_accepts_http_and_https() -> None:
    assert speedtest.validate_url("http://example.com/a") == "http://example.com/a"
    assert speedtest.validate_url("https://example.com/a") == "https://example.com/a"


def test_validate_url_rejects_non_http_scheme() -> None:
    with pytest.raises(speedtest.InvalidUrlError, match="scheme"):
        speedtest.validate_url("ftp://example.com/a")


def test_validate_url_rejects_missing_host() -> None:
    with pytest.raises(speedtest.InvalidUrlError, match="host"):
        speedtest.validate_url("http:///a")
