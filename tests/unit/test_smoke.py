"""scripts/smoke.py on a canned site (no network).

Each failure case changes exactly one thing in an otherwise healthy site,
so every condition of every check is shown to matter on its own.
"""

import http.server
import json
import socket
import threading
from collections.abc import Iterator

import pytest

from tests.helpers import load_script

smoke = load_script("smoke")
REAL_RUN = smoke.run
BASE = "https://curator.example"
REQUEST_ID = "0123456789abcdef0123456789abcdef"
CSP = smoke.expected_csp(smoke.SITE_CONF.read_text())
PAGE = (
    b'<div id="root"></div>'
    b'<script type="module" crossorigin src="/assets/index-abc123.js"></script>'
)
HOTEL = "h_00000000a1"
SCRIPT = f"GET {BASE}/assets/index-abc123.js"


def _answer(status: int = 200, body: object = b"", **headers: str) -> object:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    pairs = tuple((k.replace("_", "-"), v) for k, v in headers.items())
    return smoke.Answer(status, pairs, raw)


def _page(status: int = 200, body: bytes = PAGE, **headers: str) -> object:
    values = {
        "content_security_policy": CSP,
        "x_request_id": REQUEST_ID,
        **headers,
    }
    return _answer(status, body, **{k: v for k, v in values.items() if v})


def _analyze(model: str | None) -> object:
    if model is None:
        return _answer(503, {"error": {"code": "unavailable"}})
    return _answer(
        200, {"model": model, "parts": [{"labels": [{"label": "x"}]}]}
    )


def _site(model: str | None = "m1", **changes: object) -> dict[str, object]:
    ready = {
        "status": "ready",
        "hotels": 5,
        **({"model": model} if model else {}),
    }
    site: dict[str, object] = {
        f"GET {BASE}/": _page(),
        f"GET {BASE}/explore": _page(),
        SCRIPT: _answer(cache_control=smoke.IMMUTABLE),
        f"GET {BASE}/api/readyz": _answer(200, ready),
        f"GET {BASE}/api/v1/meta": _answer(
            200, {"hotels": 5, "analyze_available": model is not None}
        ),
        f"GET {BASE}/api/v1/hotels?page_size=1": _answer(
            200, {"items": [{"hotel_id": HOTEL}], "total": 5}
        ),
        f"GET {BASE}/api/v1/hotels/{HOTEL}": _answer(200, {"hotel_id": HOTEL}),
        f"POST {BASE}/api/v1/analyze": _analyze(model),
        f"GET {BASE}/robots.txt": _answer(
            200, f"Sitemap: {BASE}/sitemap.xml\n".encode()
        ),
    }
    site.update(changes)
    return site


def _run(site: dict[str, object], expect: str = "required") -> dict[str, bool]:
    def fetch(method: str, url: str, body: bytes | None) -> object:
        return site.get(f"{method} {url}", smoke.Answer(404, (), b""))

    results = smoke.run(BASE, expect, 0, fetch, sleep=lambda _s: None)
    return {r.name: r.ok for r in results}


def _failed(outcome: dict[str, bool]) -> set[str]:
    return {name for name, ok in outcome.items() if not ok}


def test_a_healthy_site_passes_every_check() -> None:
    outcome = _run(_site())
    assert len(outcome) == 9
    assert all(outcome.values()), outcome


def test_the_csp_checked_is_the_one_nginx_sends() -> None:
    assert CSP.startswith("default-src 'self'; script-src 'self';")
    with pytest.raises(ValueError, match="no server-level"):
        smoke.expected_csp("location / {}\n")


READY = f"GET {BASE}/api/readyz"
SEARCH = f"GET {BASE}/api/v1/hotels?page_size=1"
ROBOTS = f"GET {BASE}/robots.txt"
WEAK_CSP = CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
LABELLED = {"parts": [{"labels": [{"label": "x"}]}]}
KEYS = {
    "home page": f"GET {BASE}/",
    "deep link": f"GET {BASE}/explore",
    "script cached for a year": SCRIPT,
    "API ready": READY,
    "meta": f"GET {BASE}/api/v1/meta",
    "search": SEARCH,
    "hotel page": f"GET {BASE}/api/v1/hotels/{HOTEL}",
    "analyze": f"POST {BASE}/api/v1/analyze",
    "robots.txt": ROBOTS,
}
ONE_CHANGE = {
    "home page": [
        _page(status=500),
        _page(body=PAGE.replace(b'id="root"', b'id="app"')),
        _page(content_security_policy="default-src 'none'"),
        _page(content_security_policy=WEAK_CSP),
        _page(content_security_policy=""),
        _page(x_request_id="not-an-id"),
        _page(x_request_id=""),
    ],
    "deep link": [_page(status=404)],
    "script cached for a year": [
        _answer(404, cache_control=smoke.IMMUTABLE),
        _answer(cache_control="no-cache"),
    ],
    "API ready": [
        _answer(200, {"status": "starting", "hotels": 5, "model": "m1"}),
        _answer(200, {"status": "ready", "hotels": 0, "model": "m1"}),
        _answer(200, {"status": "ready", "hotels": True, "model": "m1"}),
        _answer(200, {"status": "ready", "hotels": "5", "model": "m1"}),
        _answer(503, {"status": "ready", "hotels": 5, "model": "m1"}),
        _answer(200, [1, 2]),
    ],
    "meta": [
        _answer(200, {"hotels": 4, "analyze_available": True}),
        _answer(200, {"hotels": 5, "analyze_available": False}),
        _answer(500, {"hotels": 5, "analyze_available": True}),
    ],
    "search": [_answer(200, {"items": [{"hotel_id": HOTEL}], "total": 3})],
    "hotel page": [
        _answer(500, {"hotel_id": HOTEL}),
        _answer(200, {"hotel_id": "h_00000000b2"}),
    ],
    "analyze": [
        _answer(429, {"model": "m1", **LABELLED}),
        _answer(200, {"model": "m1", "parts": [{"labels": []}]}),
        _answer(200, {"model": "m2", **LABELLED}),
    ],
    "robots.txt": [
        _answer(404, f"Sitemap: {BASE}/sitemap.xml\n".encode()),
        _answer(200, b"Sitemap: https://other.example/sitemap.xml\n"),
        _answer(200, f"Sitemap: {BASE}/sitemap.xml.bak\n".encode()),
    ],
}
CASES = [
    pytest.param(check, answer, id=f"{check}-{i}")
    for check, answers in ONE_CHANGE.items()
    for i, answer in enumerate(answers)
]


@pytest.mark.parametrize(("check", "answer"), CASES)
def test_one_broken_thing_fails_its_own_check(
    check: str, answer: object
) -> None:
    failed = _failed(_run(_site(**{KEYS[check]: answer})))
    assert check in failed
    # A broken readiness answer also leaves meta, search and analyze
    # without the count and model they compare against.
    if check != "API ready":
        assert failed == {check}


def test_search_needs_exactly_one_hotel_with_a_proper_id() -> None:
    two = _answer(200, {"items": [{"hotel_id": HOTEL}] * 2, "total": 5})
    assert _failed(_run(_site(**{SEARCH: two}))) == {"search"}
    bad_id = _answer(200, {"items": [{"hotel_id": "../x"}], "total": 5})
    assert _failed(_run(_site(**{SEARCH: bad_id}))) == {"hotel page"}


def test_a_page_without_its_script_fails_the_asset_check_too() -> None:
    bare = _page(body=b'<div id="root"></div>')
    assert _failed(_run(_site(**{KEYS["home page"]: bare}))) == {
        "home page",
        "script cached for a year",
    }


def test_a_site_without_a_model_passes_when_none_is_expected() -> None:
    assert all(_run(_site(model=None), "absent").values())
    assert all(_run(_site(model=None), "any").values())
    required = _run(_site(model=None), "required")
    assert _failed(required) == {"API ready", "meta", "analyze"}


def test_a_model_where_none_is_expected_fails() -> None:
    assert _failed(_run(_site(), "absent")) == {"API ready", "meta", "analyze"}


def test_it_waits_for_readiness_then_gives_up_at_the_deadline() -> None:
    answers = [_answer(503, {"status": "database unavailable"})] * 2 + [
        _answer(200, {"status": "ready", "hotels": 5})
    ]
    seen: list[str] = []
    now = [0.0]

    def get(url: str) -> object:
        seen.append(url)
        return answers.pop(0)

    def sleep(seconds: float) -> None:
        now[0] += seconds

    ready = smoke.wait_ready(get, BASE, 60, clock=lambda: now[0], sleep=sleep)
    assert ready.status == 200 and len(seen) == 3 and now[0] == 10
    stuck = smoke.wait_ready(
        lambda _u: _answer(503, b""),
        BASE,
        12,
        clock=lambda: now[0],
        sleep=sleep,
    )
    assert stuck.status == 503


@pytest.fixture
def truncating_server() -> Iterator[str]:
    """Answers 502 with a body cut short, as a dropped connection does."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(502)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"partial")
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_network_errors_are_answers_not_crashes(truncating_server: str) -> None:
    refused = smoke.fetch("GET", "http://127.0.0.1:9/api/readyz")
    assert refused.status == 0 and b"Error" in refused.body
    cut = smoke.fetch("GET", f"{truncating_server}/api/readyz")
    assert cut.status == 0
    assert b"IncompleteRead" in cut.body


@pytest.mark.parametrize(
    "url",
    [
        "https://hotel-curator.fly.dev",
        "https://hotel-curator.fly.dev/",
        "http://127.0.0.1:8080",
        "http://localhost:4173",
    ],
)
def test_site_addresses_are_accepted(url: str) -> None:
    assert smoke.base_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://hotel-curator.fly.dev",
        "https://hotel-curator.fly.dev/api",
        "https://hotel-curator.fly.dev/?x=1",
        "https://hotel-curator.fly.dev:8443",
        "ftp://hotel-curator.fly.dev",
        "https://Curator.Example",
        "hotel-curator.fly.dev",
    ],
)
def test_other_addresses_are_refused(url: str) -> None:
    with pytest.raises(ValueError, match="not a site address"):
        smoke.base_url(url)
    with pytest.raises(SystemExit):
        smoke.main([url])


def test_robots_on_a_local_stack_needs_only_an_https_sitemap() -> None:
    good = _answer(200, b"Sitemap: https://hotel-curator.fly.dev/sitemap.xml\n")
    assert smoke.check_robots(good, "http://127.0.0.1:8080").ok
    bad = _answer(200, b"Sitemap: http://127.0.0.1/sitemap.xml\n")
    assert not smoke.check_robots(bad, "http://127.0.0.1:8080").ok
    assert not smoke.check_robots(good, BASE).ok


def test_main_reports_and_exits_with_the_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    site = _site()

    def fetch(method: str, url: str, body: bytes | None) -> object:
        return site.get(f"{method} {url}", smoke.Answer(404, (), b""))

    monkeypatch.setattr(
        smoke,
        "run",
        lambda base, expect, wait: REAL_RUN(base, expect, wait, fetch),
    )
    assert smoke.main([BASE, "--wait", "0"]) == 0
    assert "9 of 9 checks passed" in capsys.readouterr().out
    site[ROBOTS] = _answer(404, b"")
    assert smoke.main([BASE, "--wait", "0"]) == 1
    assert "FAIL  robots.txt" in capsys.readouterr().out
