"""``curator serve`` as the API container runs it (ADR 011, D16 and D17).

One real server process: every line it logs is JSON (uvicorn's own
loggers included, no second access log), metrics answer on their own
port, and a SIGTERM lets a request already in flight finish before the
process shuts down cleanly, as Fly's ``kill_signal`` expects.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from typer.testing import CliRunner

from hotel_curator.cli.main import app as cli
from hotel_curator.config import get_settings
from hotel_curator.db.release import release

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "data" / "demo" / "hotel_results.json.gz"
CURATOR = Path(sys.executable).with_name("curator")
SECRET = "The room was DIRTY, ask for Maria"
START_SECONDS = 60
HEADERS_SETTLE_SECONDS = 0.5
"""Time for the server to read the request line and headers."""
SHUTDOWN_SETTLE_SECONDS = 1.0
"""Time for the server to begin its graceful shutdown."""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as answer:
        return answer.status, answer.read().decode()


def _wait_until_up(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"server exited: {process.communicate()[1]}")
        try:
            _get(f"http://127.0.0.1:{port}/healthz")
            return
        except OSError:
            time.sleep(0.2)
    pytest.fail("server did not start")


def _read_all(conn: socket.socket) -> bytes:
    chunks = []
    while chunk := conn.recv(65536):
        chunks.append(chunk)
    return b"".join(chunks)


def _serve(tmp_path: Path) -> tuple[subprocess.Popen[str], int, int]:
    database = f"sqlite:///{tmp_path / 'serve.db'}"
    release(database, RESULTS)
    port, metrics_port = _free_port(), _free_port()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CURATOR_")},
        "CURATOR_HOME": str(ROOT),
        "CURATOR_DATABASE_URL": database,
        "CURATOR_LOG_FORMAT": "json",
        "CURATOR_METRICS_PORT": str(metrics_port),
    }
    process = subprocess.Popen(
        [str(CURATOR), "serve", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, port, metrics_port


def _analyze_across_sigterm(port: int, process: subprocess.Popen[str]) -> bytes:
    """Start a request, send SIGTERM mid-body, then finish the body."""
    body = json.dumps({"liked": SECRET}).encode()
    head = (
        f"POST /v1/analyze HTTP/1.1\r\nHost: test\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode()
    with socket.create_connection(("127.0.0.1", port), timeout=30) as conn:
        conn.sendall(head + body[:5])
        time.sleep(HEADERS_SETTLE_SECONDS)
        process.send_signal(signal.SIGTERM)
        time.sleep(SHUTDOWN_SETTLE_SECONDS)
        conn.sendall(body[5:])
        return _read_all(conn)


def test_serve_logs_only_json_and_finishes_a_request_on_sigterm(
    tmp_path: Path,
) -> None:
    process, port, metrics_port = _serve(tmp_path)
    try:
        _wait_until_up(port, process)
        status, ready = _get(f"http://127.0.0.1:{port}/readyz")
        assert status == 200 and json.loads(ready)["hotels"] > 0
        _, metrics = _get(f"http://127.0.0.1:{metrics_port}/metrics")
        answer = _analyze_across_sigterm(port, process)
        out, err = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    # The model may be missing here (an unpulled Git LFS pointer): 503 is
    # an answer too. What matters is a whole response, then a clean exit.
    status_line, _, rest = answer.partition(b"\r\n")
    assert status_line in (
        b"HTTP/1.1 200 OK",
        b"HTTP/1.1 503 Service Unavailable",
    )
    assert b"connection: close" in rest.lower()
    # uvicorn shuts down cleanly, then re-raises the signal it caught
    # (uvicorn/server.py, capture_signals): the exit is SIGTERM's.
    assert process.returncode == -signal.SIGTERM
    assert 'route="/readyz",status="200"} 1.0' in metrics
    assert out == ""
    records = [json.loads(line) for line in err.split("\n") if line]
    events = [record["event"] for record in records]
    assert "Application shutdown complete." in events
    assert any(event.startswith("Finished server process") for event in events)
    shutdown = events.index("Shutting down")
    finished = [
        i
        for i, record in enumerate(records)
        if record.get("path") == "/v1/analyze"
    ]
    assert finished and finished[0] > shutdown  # answered after SIGTERM
    loggers = {record["logger"] for record in records}
    assert "uvicorn.error" in loggers  # uvicorn's own lines, as JSON
    assert "uvicorn.access" not in loggers  # one access log: the app's
    assert "DIRTY" not in err


def test_serve_never_lets_uvicorn_rewrite_the_client_or_log_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Rate limits key on X-Real-IP, which the app reads itself; uvicorn
    # would otherwise take the peer from X-Forwarded-For (Fly's proxy adds
    # to it) and write its own plain-text logs.
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *_a, **kw: calls.append(kw))
    monkeypatch.delenv("CURATOR_METRICS_PORT", raising=False)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(cli, ["serve", "--port", "8123"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    (options,) = calls
    assert options["proxy_headers"] is False
    assert options["server_header"] is False
    assert options["log_config"] is None
    assert options["access_log"] is False
    assert options["port"] == 8123
