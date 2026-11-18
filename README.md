# speedtest-cli

A small console tool that measures download speed of a URL by making 10
sequential requests and reporting average time, total bytes, and speed.

[![CI](https://github.com/RAprogramm/thisIsEasy-downloader-test/actions/workflows/ci.yml/badge.svg)](https://github.com/RAprogramm/thisIsEasy-downloader-test/actions/workflows/ci.yml)

## Requirements

- Python 3.13+ (tested on 3.13, 3.14, 3.15)
- No runtime dependencies — standard library only

## Quick start

```bash
git clone https://github.com/RAprogramm/thisIsEasy-downloader-test.git
cd thisIsEasy-downloader-test
python speedtest.py <URL>
```

Example (a 10 MiB file from Cloudflare's speed-test endpoint):

```bash
python speedtest.py "https://speed.cloudflare.com/__down?bytes=10485760"
```

## Options

```
usage: speedtest [-h] [-n N] [-t SECONDS] url

positional arguments:
  url                   http(s) URL to download

options:
  -h, --help            show this help message and exit
  -n, --requests N      number of sequential requests (default: 10)
  -t, --timeout SECONDS
                        per-request timeout in seconds (default: 30)
```

## Example output

Real output from a run on a home connection (verified against
`curl -o /dev/null -w "%{size_download} %{time_total}"` on the same URL):

```
Request  1/10: 10.49 MB in 0.642 s
Request  2/10: 10.49 MB in 0.829 s
Request  3/10: 10.49 MB in 0.646 s
Request  4/10: 10.49 MB in 0.566 s
Request  5/10: 10.49 MB in 0.640 s
Request  6/10: 10.49 MB in 0.598 s
Request  7/10: 10.49 MB in 0.653 s
Request  8/10: 10.49 MB in 0.571 s
Request  9/10: 10.49 MB in 0.553 s
Request 10/10: 10.49 MB in 0.577 s
Requests:         10
Total downloaded: 104.86 MB (104,857,600 bytes)
Average time:     0.627 s
Total time:       6.273 s
Download speed:   133.72 Mbit/s (16.71 MB/s)
```

## Units and formulas

- **Speed is reported in megabits per second (Mbit/s)**, the unit ISPs and
  speed tests use. 1 Mbit = 10^6 bits (decimal, not binary).
- MB/s in parentheses uses 1 MB = 10^6 bytes.

| Quantity | Formula |
| --- | --- |
| `total_bytes` | sum of body bytes actually read from the socket |
| `total_time` | sum of per-request times, from request start (DNS, TCP, and TLS setup included) to the last byte read |
| `avg_time` | `total_time / N` |
| `speed` | `total_bytes * 8 / total_time / 1_000_000` Mbit/s |

Speed is computed from the totals, not as the mean of per-request speeds, so
large and small responses are weighted proportionally to their size.

Timings use `time.perf_counter()` (monotonic clock).

## Design decisions

- **Sequential requests only.** N requests, one after another, each response
  read fully to EOF in 64 KiB chunks. No parallelism, no buffering of the
  whole body in memory — files of hundreds of MB are fine.
- **`Accept-Encoding: identity`.** Without it a server may return gzip and the
  "speed" would reflect compressed bytes, not real content.
- **`Cache-Control: no-cache` / `Pragma: no-cache`** so intermediate caches
  avoid serving stale copies. A CDN may still answer from its cache — that is
  normal and still measures your last-mile link.
- **Error policy: stop on first failure.** Any timeout, DNS, TLS, connection
  error, non-2xx status, or truncated transfer aborts the run with a clear
  message and no traceback. Averaging a partial run would misrepresent the
  link.
- **Exit codes:** `0` success, `1` network/HTTP error, `2` bad arguments or
  URL, `130` interrupted with Ctrl+C.
- **Content-Length check:** if the header disagrees with the bytes actually
  read, the transfer is treated as truncated and the run stops with exit
  code 1. Early EOF during chunked reading does not raise by itself, so this
  explicit comparison is the only reliable truncation detector.
- Redirects are followed (urllib default).

## Limitations and honest-measurement caveats

- **Single connection.** One TCP stream cannot saturate a fast multi-gigabit
  link the way multi-stream speed tests (fast.com, Speedtest.net) do. Treat
  the number as a lower bound of your link capacity.
- **TCP slow start** distorts short transfers: on small files the measured
  speed is dominated by ramp-up, not steady state.
- **CDN and caching** can make repeated requests faster than a cold fetch.
- **Background traffic** (updates, cloud sync, video calls) skews results.
- The tool measures one URL; server-side throttling or load balancing makes
  individual requests vary.

## Development

Install the dev dependencies once — with uv (`uv sync`) or plain pip
(`pip install pytest pytest-cov pytest-socket`) — then the fastest check is:

```bash
pytest
```

36 tests, no external network, ~5 s. Bare `pytest` without these plugins
fails on the CLI options, so install them first. The CI workflow enforces
100% coverage; to check it locally, run:

```bash
pytest --cov=speedtest --cov-fail-under=100
```

Full lint, type-check, and test suite using [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy speedtest.py tests
uv run pytest
```

Without uv (plain Python ≥ 3.13):

```bash
python -m venv .venv && source .venv/bin/activate
pip install pytest pytest-cov pytest-socket ruff mypy
ruff check .
ruff format --check .
mypy speedtest.py tests
pytest
```

Tests never touch the external network: they run against a local
`http.server` on the loopback interface, and `pytest-socket` blocks all other
sockets suite-wide. Coverage is enforced at 100% by `pytest-cov`.

## AI usage

The code was drafted by AI coding assistants from a written specification.
The models that took part:

- **Claude Sonnet 5.5**
- **Qwen3.8 Flash Next**
- **GLM 5.3 Flash**

The author reviewed every change and verified the result manually: real runs
against a live URL, a cross-check against `curl`, and exit-code checks.

## License

MIT — see [LICENSE](LICENSE).
