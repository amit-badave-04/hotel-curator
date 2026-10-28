"""scripts/check_edge.py on canned answers (no network)."""

import http.client
import json

import pytest

from tests.helpers import load_script

edge = load_script("check_edge")
DOMAIN = "curator.example"
SITE = f"https://{DOMAIN}/"
CSP = edge.expected_csp(edge.SITE_CONF.read_text())
REQUEST_ID = "0123456789abcdef0123456789abcdef"
HOME_HTML = b'<script type="module" src="/assets/index-abc123.js"></script>'


def _answer(
    status: int = 200, body: bytes = b"", **headers: str | list[str]
) -> object:
    pairs = []
    for name, value in headers.items():
        values = value if isinstance(value, list) else [value]
        pairs += [(name.replace("_", "-"), v) for v in values]
    return edge.Answer(status, tuple(pairs), body)


def _home(**overrides: str | list[str]) -> object:
    headers: dict[str, str | list[str]] = {
        "server": "cloudflare",
        "content_security_policy": CSP,
        "strict_transport_security": edge.HSTS,
        "x_request_id": REQUEST_ID,
        **overrides,
    }
    return _answer(200, HOME_HTML, **headers)


def _site(**changes: object) -> dict[str, object]:
    answers: dict[str, object] = {
        SITE: _home(),
        f"https://www.{DOMAIN}/": _answer(301, location=SITE),
        f"http://{DOMAIN}/": _answer(301, location=SITE),
        f"{SITE}api/readyz": _answer(
            200,
            json.dumps(
                {"status": "ready", "hotels": 1494, "model": "m"}
            ).encode(),
        ),
        f"https://{DOMAIN}/assets/index-abc123.js": _answer(
            200, cache_control=edge.IMMUTABLE
        ),
        **changes,
    }
    return answers


REFUSED_NAME = f"handshake refused: {edge.UNRECOGNIZED_NAME}"
ADDRESSES = [("IPv4", "203.0.113.10"), ("IPv6", "2001:db8::10")]


def _run(
    answers: dict[str, object],
    addresses: list[tuple[str, str]] | None = None,
    **probes: str,
) -> dict[str, object]:
    seen = {"own": REFUSED_NAME, "site": "HTTP 400", "tls": "refused"}
    seen.update(probes)

    def probe(address: str, sni: str) -> str:
        return seen["own"] if sni == edge.ORIGIN else seen["site"]

    results = edge.run(
        DOMAIN,
        edge.ORIGIN,
        fetch_url=answers.__getitem__,
        probe=probe,
        tls_probe=lambda _domain: seen["tls"],
        resolve=lambda _host: ADDRESSES if addresses is None else addresses,
    )
    return {r.name: r.ok for r in results}


def test_the_csp_checked_is_the_one_nginx_sends() -> None:
    assert CSP.startswith("default-src 'self'; script-src 'self'")
    with pytest.raises(ValueError, match="no server-level"):
        edge.expected_csp("location / {}\n")


def test_a_correct_edge_passes_every_check() -> None:
    outcome = _run(_site())
    assert outcome and all(ok is True for ok in outcome.values()), outcome


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        ({"server": "nginx"}, "served by Cloudflare"),
        (
            {"strict_transport_security": [edge.HSTS, "max-age=31536000"]},
            "one HSTS value",
        ),
        (
            {"content_security_policy": [CSP, "script-src 'unsafe-inline'"]},
            "CSP unchanged",
        ),
        ({"x_request_id": "not-an-id"}, "request id"),
    ],
)
def test_a_changed_header_fails_its_check(
    change: dict[str, str | list[str]], failed: str
) -> None:
    outcome = _run(_site(**{SITE: _home(**change)}))
    assert outcome[failed] is False
    assert sum(ok is False for ok in outcome.values()) == 1


def test_an_injected_script_fails() -> None:
    body = HOME_HTML + b'<script src="/cdn-cgi/scripts/rocket-loader.min.js">'
    home = edge.Answer(200, _home().headers, body)
    assert _run(_site(**{SITE: home}))["no injected script"] is False


def test_missing_redirects_readiness_and_cache_fail() -> None:
    outcome = _run(
        _site(
            **{
                f"https://www.{DOMAIN}/": _answer(200),
                f"http://{DOMAIN}/": _answer(301, location=f"http://{DOMAIN}/"),
                f"{SITE}api/readyz": _answer(
                    200, json.dumps({"status": "ready", "hotels": 9}).encode()
                ),
                f"https://{DOMAIN}/assets/index-abc123.js": _answer(
                    200, cache_control="no-cache"
                ),
            }
        )
    )
    for name in (
        "www to apex",
        "http to https",
        "API ready with a model",
        "asset cached for a year",
    ):
        assert outcome[name] is False, name


OWN = f"origin refuses {edge.ORIGIN}"


@pytest.mark.parametrize(
    ("probes", "failed"),
    [
        ({"own": "HTTP 200"}, OWN),
        ({"own": "handshake refused: SSLEOFError"}, OWN),
        ({"own": "no connection: TimeoutError"}, OWN),
        ({"site": "HTTP 200"}, edge.CERT_CHECK),
        ({"site": "HTTP 403"}, edge.CERT_CHECK),  # the IP gate, not the cert
        ({"site": "HTTP 421"}, edge.CERT_CHECK),
        ({"site": "no connection: ConnectionRefusedError"}, edge.CERT_CHECK),
        ({"tls": "accepted"}, "TLS 1.1 refused"),
        ({"tls": "inconclusive: TimeoutError"}, "TLS 1.1 refused"),
    ],
)
def test_an_open_unreached_or_unproven_origin_fails(
    probes: dict[str, str], failed: str
) -> None:
    assert _run(_site(), **probes)[failed] is False


def test_an_origin_that_does_not_resolve_fails_both_checks() -> None:
    outcome = _run(_site(), addresses=[])
    assert outcome[OWN] is False
    assert outcome[edge.CERT_CHECK] is False


def test_every_address_must_refuse() -> None:
    def probe(address: str, sni: str) -> str:
        if address == ADDRESSES[1][1]:
            return "no connection: OSError"
        return REFUSED_NAME if sni == edge.ORIGIN else "HTTP 400"

    results = edge.check_origin(
        probe, edge.ORIGIN, DOMAIN, resolve=lambda _host: ADDRESSES
    )
    assert [r.ok for r in results] == [False, False]
    assert "IPv6 2001:db8::10: no connection" in results[0].detail


def test_unavailable_tls_probe_skips_and_network_errors_fail() -> None:
    assert _run(_site(), tls="unavailable")["TLS 1.1 refused"] is None

    def broken(url: str) -> object:
        raise http.client.IncompleteRead(b"partial")

    results = edge.run(
        DOMAIN,
        edge.ORIGIN,
        fetch_url=broken,
        probe=lambda *_: "no connection: OSError",
        tls_probe=lambda _d: "refused",
        resolve=lambda _host: ADDRESSES,
    )
    outcome = {r.name: r.ok for r in results}
    assert outcome["home 200"] is False
    assert outcome["asset cached for a year"] is False
    assert outcome[OWN] is False


@pytest.mark.parametrize(
    "body",
    [
        b"[1, 2]",
        b'"ready"',
        b'{"status": "ready", "hotels": "many", "model": "m"}',
        b'{"status": "ready", "hotels": true, "model": "m"}',
        b"[" * 100_000,
    ],
)
def test_odd_readiness_answers_fail_cleanly(body: bytes) -> None:
    result = edge.check_ready(edge.Answer(200, (), body))
    assert result.ok is False


def test_main_refuses_names_that_are_not_dns_names() -> None:
    for bad in ("Curator.Example", "curator.example; rm -rf /", "x"):
        with pytest.raises(SystemExit):
            edge.main([bad])
