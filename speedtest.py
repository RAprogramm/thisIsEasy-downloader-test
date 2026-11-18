#!/usr/bin/env python3
"""Sequential download speed measurement for a single URL.

The tool sends N sequential requests to the given URL, reads each response
body to the end, and reports the average request time, the total number of
bytes downloaded, and the aggregate download speed in megabits per second
(1 Mbit = 10^6 bits).

Only the Python standard library is used, so the script runs on any machine
with Python 3.13+ without installation steps.

Example:
    $ python speedtest.py https://example.com/large-image.jpg
"""

from __future__ import annotations

import argparse
import http.client
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_REQUESTS = 10
DEFAULT_TIMEOUT_S = 30.0
CHUNK_SIZE = 64 * 1024
BITS_PER_MEGABIT = 1_000_000
BYTES_PER_MEGABYTE = 1_000_000

EXIT_OK = 0
EXIT_NETWORK = 1
EXIT_ARGS = 2
EXIT_INTERRUPT = 130

REQUEST_HEADERS = {
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "User-Agent": "speedtest-cli/1.0",
}


class SpeedtestError(Exception):
    """A network or HTTP failure that aborts a measurement run."""


class InvalidUrlError(ValueError):
    """A URL that cannot be measured: wrong scheme or missing host."""


@dataclass(frozen=True, slots=True)
class RequestResult:
    """Metrics of one fully completed request.

    Attributes:
        bytes_read: Number of body bytes actually read from the socket.
        elapsed_s: Seconds from request start, including DNS, TCP, and TLS
            connection setup, to the last byte read.
    """

    bytes_read: int
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class Summary:
    """Aggregated metrics of a measurement run.

    Attributes:
        requests: Number of successful requests in the run.
        total_bytes: Sum of body bytes across all requests.
        total_time_s: Sum of request durations in seconds.
        avg_time_s: total_time_s divided by the number of requests.
        speed_mbit_s: total_bytes * 8 / total_time_s / 10^6, in megabits/s.
        speed_mbyte_s: total_bytes / total_time_s / 10^6, in megabytes/s.
    """

    requests: int
    total_bytes: int
    total_time_s: float
    avg_time_s: float
    speed_mbit_s: float
    speed_mbyte_s: float


def validate_url(url: str) -> str:
    """Return the URL unchanged if it is measurable, raise otherwise.

    Args:
        url: Raw URL string taken from the command line.

    Returns:
        The same URL string.

    Raises:
        InvalidUrlError: The scheme is not http/https or the host is missing.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise InvalidUrlError(f"malformed URL: {url!r}") from exc
    if parsed.scheme not in ("http", "https"):
        raise InvalidUrlError(
            f"unsupported scheme {parsed.scheme!r}: use http or https"
        )
    if not parsed.netloc:
        raise InvalidUrlError(f"URL has no host: {url!r}")
    return url


def fetch_once(url: str, timeout_s: float) -> RequestResult:
    """Send one request and read the whole response body without buffering it.

    The body is streamed in 64 KiB chunks until EOF, so files of any size are
    measured without growing memory usage. The response is requested with
    ``Accept-Encoding: identity`` so the byte count reflects the real content
    instead of a compressed transfer.

    Args:
        url: Measurable URL (see validate_url).
        timeout_s: Socket timeout applied to connect and to every read.

    Returns:
        RequestResult with the number of bytes read and the elapsed time.

    Raises:
        SpeedtestError: Timeout, DNS, TLS, connection, non-2xx status, or a
            truncated transfer (Content-Length disagrees with bytes read).
    """
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    started = time.perf_counter()
    total = 0
    declared: str | None
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            if not 200 <= response.status < 300:
                raise SpeedtestError(
                    f"unexpected HTTP status {response.status} for {url}"
                )
            declared = response.headers.get("Content-Length")
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
    except TimeoutError as exc:
        raise SpeedtestError(f"timed out after {timeout_s:g}s: {url}") from exc
    except urllib.error.HTTPError as exc:
        raise SpeedtestError(f"HTTP error {exc.code} ({exc.reason}) for {url}") from exc
    except http.client.HTTPException as exc:
        raise SpeedtestError(f"invalid HTTP response for {url}: {exc}") from exc
    except urllib.error.URLError as exc:
        raise SpeedtestError(f"network error for {url}: {exc.reason}") from exc
    except OSError as exc:
        raise SpeedtestError(f"connection error for {url}: {exc}") from exc
    elapsed = time.perf_counter() - started
    _check_length(declared, total, url)
    return RequestResult(bytes_read=total, elapsed_s=elapsed)


def _check_length(declared: str | None, actual: int, url: str) -> None:
    """Fail the request when Content-Length disagrees with the bytes read.

    A mismatch means a truncated transfer: the response is not complete, so
    including it in the averages would misrepresent the measurement. Early
    EOF while reading with a fixed chunk size does not raise by itself, so
    this explicit comparison is the only reliable truncation detector.

    Args:
        declared: Raw Content-Length header value, if the server sent one.
        actual: Number of bytes actually read from the socket.
        url: Request URL, used for the error message.

    Raises:
        SpeedtestError: The declared length does not match the bytes read.
    """
    if declared is None:
        return
    try:
        expected = int(declared)
    except ValueError:
        return
    if expected != actual:
        raise SpeedtestError(
            f"truncated transfer for {url}: "
            f"Content-Length says {expected} bytes, read {actual}"
        )


def run(url: str, requests: int, timeout_s: float) -> list[RequestResult]:
    """Fetch the URL the given number of times, strictly one after another.

    Each request line is printed as it completes. The run stops at the first
    failure: averaging over a partial run would misrepresent the channel.

    Args:
        url: Measurable URL.
        requests: Number of sequential requests (at least 1).
        timeout_s: Per-request timeout in seconds.

    Returns:
        One RequestResult per successful request.

    Raises:
        SpeedtestError: Propagated from fetch_once on the first failure.
    """
    results: list[RequestResult] = []
    width = len(str(requests))
    for index in range(1, requests + 1):
        result = fetch_once(url, timeout_s)
        results.append(result)
        print(
            f"Request {index:>{width}d}/{requests}: "
            f"{result.bytes_read / BYTES_PER_MEGABYTE:.2f} MB "
            f"in {result.elapsed_s:.3f} s"
        )
    return results


def compute(results: Sequence[RequestResult]) -> Summary:
    """Aggregate request results into a Summary.

    Speed is computed from the totals (all bytes over all time), not as the
    mean of per-request speeds, so large and small responses are weighted
    proportionally to their size.

    Args:
        results: Non-empty sequence of completed request results.

    Returns:
        Summary with totals, average time, and speed.

    Raises:
        ValueError: The sequence is empty.
    """
    if not results:
        raise ValueError("cannot summarize an empty run")
    total_bytes = sum(result.bytes_read for result in results)
    total_time = sum(result.elapsed_s for result in results)
    speed_mbit_s = (
        total_bytes * 8 / total_time / BITS_PER_MEGABIT if total_time > 0 else 0.0
    )
    return Summary(
        requests=len(results),
        total_bytes=total_bytes,
        total_time_s=total_time,
        avg_time_s=total_time / len(results),
        speed_mbit_s=speed_mbit_s,
        speed_mbyte_s=speed_mbit_s / 8,
    )


def format_report(summary: Summary) -> str:
    """Render the final summary block for the console.

    Args:
        summary: Aggregated metrics produced by compute.

    Returns:
        A multi-line human-readable report.
    """
    megabytes = summary.total_bytes / BYTES_PER_MEGABYTE
    return "\n".join(
        [
            f"Requests:         {summary.requests}",
            f"Total downloaded: {megabytes:.2f} MB ({summary.total_bytes:,} bytes)",
            f"Average time:     {summary.avg_time_s:.3f} s",
            f"Total time:       {summary.total_time_s:.3f} s",
            f"Download speed:   {summary.speed_mbit_s:.2f} Mbit/s "
            f"({summary.speed_mbyte_s:.2f} MB/s)",
        ]
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    argparse exits with code 2 on unknown or malformed arguments.

    Args:
        argv: Argument list without the program name; None means sys.argv[1:].

    Returns:
        Namespace with url, requests, and timeout attributes.
    """
    parser = argparse.ArgumentParser(
        prog="speedtest",
        description="Measure sequential download speed of a URL.",
    )
    parser.add_argument("url", help="http(s) URL to download")
    parser.add_argument(
        "-n",
        "--requests",
        type=int,
        default=DEFAULT_REQUESTS,
        metavar="N",
        help=f"number of sequential requests (default: {DEFAULT_REQUESTS})",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        metavar="SECONDS",
        help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT_S:g})",
    )
    args = parser.parse_args(argv)
    if args.requests < 1:
        parser.error("--requests must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list without the program name; None means sys.argv[1:].

    Returns:
        Process exit code: 0 on success, 1 on a network or HTTP failure,
        2 on invalid arguments or URL, 130 on Ctrl+C interruption.
    """
    args = parse_args(argv)
    try:
        url = validate_url(args.url)
    except InvalidUrlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ARGS
    try:
        results = run(url, args.requests, args.timeout)
    except SpeedtestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NETWORK
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPT
    print(format_report(compute(results)))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
