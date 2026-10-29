"""Metrics and error reports (ADR 011, D17).

Metric labels stay bounded and hold no request data; the metrics listener
is separate from the API's port and stops with the app; an error report
carries the exception and the request id, nothing from the request.
"""

import json
import socket
import struct
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from pydantic import ValidationError
from typer.testing import CliRunner

from hotel_curator.api import app as app_module
from hotel_curator.api.app import create_app
from hotel_curator.api.errorreports import init_error_reports, scrub_event
from hotel_curator.api.metrics import ApiMetrics
from hotel_curator.api.state import AppState
from hotel_curator.cli.main import app as cli
from hotel_curator.config import Settings, get_settings
from hotel_curator.db.migrate import upgrade
from hotel_curator.db.session import make_engine
from hotel_curator.logging import configure_logging
from tests.helpers import POLICY_PATH

SECRET = "The room was DIRTY, ask for Maria"
DSN = "https://0123456789abcdef0123456789abcdef@sentry.example/1"
TEMPLATE = "/v1/hotels/{hotel_id}"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "home": tmp_path,
        "database_url": f"sqlite:///{tmp_path / 'test.db'}",
        "tier_policy": POLICY_PATH,
        "log_format": "json",
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


@contextmanager
def _running(settings: Settings) -> Iterator[TestClient]:
    upgrade(settings.db_url())
    with TestClient(create_app(settings)) as client:
        yield client


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _requests(metrics: ApiMetrics, method: str, route: str, status: int) -> Any:
    return metrics.sample(
        "curator_http_requests_total",
        method=method,
        route=route,
        status=str(status),
    )


def test_requests_are_counted_by_route_template_with_bounded_labels(
    tmp_path: Path,
) -> None:
    with _running(_settings(tmp_path)) as c:
        c.get("/v1/hotels/h_00000000a1")
        c.get("/v1/hotels/h_00000000b2")
        c.get(f"/no/such/{quote(SECRET)}")
        c.request("BREW", "/healthz")
        metrics: ApiMetrics = c.app.state.metrics  # type: ignore[attr-defined]
    assert _requests(metrics, "GET", TEMPLATE, 404) == 2
    assert _requests(metrics, "GET", "unmatched", 404) == 1
    assert _requests(metrics, "other", "/healthz", 405) == 1
    assert (
        metrics.sample(
            "curator_http_request_duration_seconds_count",
            method="GET",
            route=TEMPLATE,
        )
        == 2
    )
    assert metrics.sample("curator_model_loaded") == 0
    exposed = generate_latest(metrics.registry).decode()
    for leak in ("h_00000000a1", "BREW", "DIRTY", "/no/such"):
        assert leak not in exposed


def test_the_model_gauge_follows_the_model_loaded_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AppState, "model", lambda self: object())
    with _running(_settings(tmp_path)) as c:
        metrics: ApiMetrics = c.app.state.metrics  # type: ignore[attr-defined]
    assert metrics.sample("curator_model_loaded") == 1


def test_metrics_have_their_own_listener_that_stops_with_the_app(
    tmp_path: Path,
) -> None:
    port = _free_port()
    with _running(_settings(tmp_path, metrics_port=port)) as c:
        c.get("/healthz")
        url = f"http://127.0.0.1:{port}/metrics"
        with urllib.request.urlopen(url, timeout=5) as answer:
            body = answer.read().decode()
        assert c.get("/metrics").status_code == 404  # not on the API's port
    line = 'curator_http_requests_total{method="GET",route="/healthz",'
    assert f'{line}status="200"}} 1.0' in body
    assert "process_resident_memory_bytes" in body
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()


def test_a_taken_metrics_port_is_logged_and_the_api_still_serves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        with _running(_settings(tmp_path, metrics_port=port)) as c:
            assert c.get("/healthz").status_code == 200
    records = [json.loads(line) for line in capsys.readouterr().err.split("\n")
               if line]  # fmt: skip
    (failed,) = [r for r in records if r["level"] == "error"]
    assert failed["event"].startswith("Metrics listener not started")


def test_the_metrics_listener_logs_a_broken_connection_as_json(
    capfd: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO", "json")
    listener = ApiMetrics().serve(0, "127.0.0.1")
    try:
        with socket.create_connection(("127.0.0.1", listener.port)) as conn:
            # Close with a reset, before the request line is complete.
            linger = struct.pack("ii", 1, 0)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
            conn.sendall(b"GET /met")
        url = f"http://127.0.0.1:{listener.port}/metrics"
        with urllib.request.urlopen(url, timeout=5) as answer:
            assert answer.status == 200
        err, deadline = "", time.monotonic() + 5
        while "request failed" not in err and time.monotonic() < deadline:
            time.sleep(0.05)
            err += capfd.readouterr().err
    finally:
        listener.close()
    (line,) = [line for line in err.split("\n") if line]
    record = json.loads(line)  # no traceback, no access line for the scrape
    assert record["event"] == "Metrics request failed: ConnectionResetError"
    assert "127.0.0.1" not in line


def test_metrics_and_several_workers_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURATOR_METRICS_PORT", "9091")
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(cli, ["serve", "--workers", "2"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 2
    assert "CURATOR_METRICS_PORT needs a single worker" in result.output


@pytest.mark.parametrize(
    "overrides",
    [
        {"metrics_host": "localhost"},
        {"metrics_host": "0.0.0.0:9091"},
        {"metrics_port": 80},
        {"metrics_port": 70000},
    ],
)
def test_metrics_settings_are_checked(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**overrides)  # type: ignore[arg-type]


def test_no_dsn_means_no_error_reports(tmp_path: Path) -> None:
    assert init_error_reports(_settings(tmp_path)) is None


def test_a_dsn_without_sentry_installed_stops_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    with pytest.raises(RuntimeError, match="sentry extra"):
        init_error_reports(_settings(tmp_path, sentry_dsn=DSN))


def test_the_scrubber_drops_what_a_request_or_a_log_line_could_carry() -> None:
    event: Any = {
        "request": {"url": f"/v1/x?q={SECRET}", "data": SECRET},
        "user": {"ip_address": "203.0.113.9"},
        "breadcrumbs": {"values": [{"message": SECRET}]},
        "extra": {"note": SECRET},
        "tags": {"request_id": "abc12345"},
        "exception": {"values": [{"type": "RuntimeError"}]},
    }
    assert scrub_event(event, {}) == {
        "tags": {"request_id": "abc12345"},
        "exception": {"values": [{"type": "RuntimeError"}]},
    }


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Events Sentry would send, kept in memory; Sentry off afterwards."""
    sentry_sdk = pytest.importorskip("sentry_sdk")
    from sentry_sdk.transport import Transport

    events: list[Any] = []

    class Captured(Transport):
        def capture_envelope(self, envelope: Any) -> None:
            event = envelope.get_event()
            if event is not None:
                events.append(event)

    monkeypatch.setattr(
        app_module,
        "init_error_reports",
        lambda s: init_error_reports(s, transport=Captured()),
    )
    yield events
    sentry_sdk.get_client().close()
    sentry_sdk.init(default_integrations=False)  # no DSN: nothing is sent


def test_an_unexpected_error_is_reported_without_request_data(
    tmp_path: Path, captured: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sentry_sdk

    flushes: list[float] = []
    monkeypatch.setattr(sentry_sdk, "flush", flushes.append)
    settings = _settings(tmp_path, sentry_dsn=DSN)
    upgrade(settings.db_url())
    app = create_app(settings)

    @app.post("/v1/boom")
    def boom() -> None:
        review_text = SECRET  # a local the report must not carry
        raise RuntimeError(f"failed after {len(review_text)} characters")

    with TestClient(app) as c:
        response = c.post(
            f"/v1/boom?q={quote(SECRET)}",
            json={"liked": SECRET},
            headers={"x-note": SECRET, "cookie": f"s={quote(SECRET)}"},
        )
    assert response.status_code == 500
    assert flushes == [2.0]  # at shutdown: uvicorn's exit skips atexit
    assert sentry_sdk.get_client().integrations == {}  # only the reporter
    (event,) = captured
    assert event["tags"]["request_id"] == response.headers["x-request-id"]
    (error,) = event["exception"]["values"]
    assert error["type"] == "RuntimeError"
    frames = error["stacktrace"]["frames"]
    assert any(frame["function"] == "boom" for frame in frames)
    assert all("vars" not in frame for frame in frames)
    assert not {"request", "user", "breadcrumbs", "extra"} & set(event)
    assert "DIRTY" not in json.dumps(event, default=str)


def test_a_failed_search_logs_and_reports_no_search_terms(
    tmp_path: Path,
    captured: list[Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(tmp_path, sentry_dsn=DSN)
    term = "maria-private-term"
    with _running(settings) as c:
        engine = make_engine(settings.db_url())
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE hotels RENAME TO hotels_gone")
        engine.dispose()
        response = c.get("/v1/hotels", params={"q": term})
    assert response.status_code == 500
    (event,) = captured
    errors = event["exception"]["values"]  # with the driver's own cause
    wrapped = [e for e in errors if e["module"].startswith("sqlalchemy")]
    assert "parameters hidden" in wrapped[-1]["value"]
    records = [json.loads(line) for line in capsys.readouterr().err.split("\n")
               if line]  # fmt: skip
    # The test client's own httpx logger prints the URL it requested.
    server = json.dumps([r for r in records if "httpx" not in r["logger"]])
    assert "parameters hidden" in server  # the logged traceback, too
    assert term not in server
    assert term not in json.dumps(event, default=str)
