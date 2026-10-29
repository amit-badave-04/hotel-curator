"""Check hotel-curator's public edge from outside, after the Cloudflare cutover.

    python scripts/check_edge.py example.org
    python scripts/check_edge.py example.org --origin hotel-curator.fly.dev

Each check prints PASS, FAIL or SKIP with what it saw; the exit code is 1
if any check fails. It needs only the standard library. What it checks is
in docs/deploy/cloudflare.md ("Check the result"):

- the site answers through Cloudflare with nginx's own CSP unchanged, one
  HSTS value, a request id, and no script Cloudflare injected;
- www redirects to the apex, http to https;
- /api/readyz is ready, with hotels and a model;
- hashed assets carry their one-year cache lifetime;
- the origin refuses anyone but Cloudflare at every address it resolves
  to (no handshake for its own name, 400 without Cloudflare's client
  certificate); an origin that cannot be reached fails;
- TLS 1.1 is refused (SKIP only if this machine cannot offer TLS 1.1).
"""

import argparse
import http.client
import json
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_CONF = ROOT / "web" / "nginx" / "site.conf"
ORIGIN = "hotel-curator.fly.dev"
HSTS = "max-age=86400"
TIMEOUT_SECONDS = 15
DOMAIN = re.compile(
    r"(?=.{4,100}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)
"""A lower-case DNS name, as scripts/deploy.py accepts."""
REQUEST_ID = re.compile(r"[0-9a-f]{32}")
ASSET = re.compile(r'src="(/assets/[^"]+\.js)"')
IMMUTABLE = "public, max-age=31536000, immutable"
INJECTED = (
    "/cdn-cgi/",
    "rocket-loader",
    "email-decode",
    "cloudflareinsights",
    "beacon.min.js",
    "zaraz",
    "data-cf-",
)
"""Markers of scripts Cloudflare features add to pages (checklist step 7)."""
REDIRECTS = frozenset({301, 302, 307, 308})
UNRECOGNIZED_NAME = "TLSV1_UNRECOGNIZED_NAME"
"""OpenSSL's reason when nginx's ssl_reject_handshake refuses a name."""
VERSION_REFUSALS = frozenset(
    {
        "TLSV1_ALERT_PROTOCOL_VERSION",
        "UNSUPPORTED_PROTOCOL",
        "SSLV3_ALERT_HANDSHAKE_FAILURE",
    }
)
"""OpenSSL's reasons when a server will not speak the offered version."""
CERT_CHECK = "origin refuses clients without Cloudflare's certificate"


@dataclass(frozen=True)
class Answer:
    """One HTTP answer: status, headers (lower-case names), body."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def header(self, name: str) -> list[str]:
        """Every value of a header."""
        return [value for key, value in self.headers if key == name.lower()]


@dataclass(frozen=True)
class Result:
    """One check: ok is True (PASS), False (FAIL) or None (SKIP)."""

    name: str
    ok: bool | None
    detail: str


Fetch = Callable[[str], Answer]
Probe = Callable[[str, str], str]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def fetch(url: str) -> Answer:
    """GET ``url`` without following redirects."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(  # noqa: S310 - https/http URLs built here
        url, headers={"User-Agent": "hotel-curator-check-edge"}
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status, headers, body = (
                response.status,
                response.headers,
                response.read(),
            )
    except urllib.error.HTTPError as error:
        status, headers, body = error.code, error.headers, error.read()
    pairs = tuple((k.lower(), v) for k, v in headers.items())
    return Answer(status, pairs, body)


def expected_csp(site_conf: str) -> str:
    """The page CSP nginx sends (the first server-level add_header)."""
    match = re.search(
        r'^add_header Content-Security-Policy "([^"]+)" always;$',
        site_conf,
        re.M,
    )
    if match is None:
        raise ValueError("no server-level Content-Security-Policy found")
    return match.group(1)


def check_home(answer: Answer, csp: str) -> list[Result]:
    """The page as Cloudflare serves it."""
    text = answer.body.decode("utf-8", "replace")
    injected = [marker for marker in INJECTED if marker in text]
    ids = answer.header("x-request-id")
    return [
        Result("home 200", answer.status == 200, f"status {answer.status}"),
        Result(
            "served by Cloudflare",
            answer.header("server") == ["cloudflare"],
            f"server {answer.header('server')}",
        ),
        Result(
            "CSP unchanged",
            answer.header("content-security-policy") == [csp],
            f"{len(answer.header('content-security-policy'))} CSP header(s)",
        ),
        Result(
            "one HSTS value",
            answer.header("strict-transport-security") == [HSTS],
            f"{answer.header('strict-transport-security')}",
        ),
        Result(
            "request id",
            len(ids) == 1 and bool(REQUEST_ID.fullmatch(ids[0])),
            f"{ids}",
        ),
        Result("no injected script", not injected, f"found {injected}"),
    ]


def check_redirect(name: str, answer: Answer, target: str) -> Result:
    """A permanent or temporary redirect to ``target``."""
    location = answer.header("location")
    ok = answer.status in REDIRECTS and location[:1] == [target]
    return Result(name, ok, f"status {answer.status}, location {location}")


def check_ready(answer: Answer) -> Result:
    """/api/readyz: hotels loaded and a model, so analyze works."""
    name = "API ready with a model"
    try:
        ready = json.loads(answer.body)
    except (ValueError, RecursionError):
        return Result(name, False, f"status {answer.status}, not JSON")
    if not isinstance(ready, dict):
        return Result(name, False, f"status {answer.status}, not an object")
    hotels = ready.get("hotels")
    ok = (
        answer.status == 200
        and ready.get("status") == "ready"
        and type(hotels) is int  # not a bool, which is an int subclass
        and hotels > 0
        and isinstance(ready.get("model"), str)
        and bool(ready["model"])
    )
    return Result(name, ok, json.dumps(ready))


def check_asset(answer: Answer | None) -> Result:
    """A hashed asset keeps its one-year lifetime through Cloudflare."""
    if answer is None:
        return Result("asset cached for a year", False, "no /assets/ script")
    cache = answer.header("cache-control")
    ok = answer.status == 200 and cache == [IMMUTABLE]
    return Result("asset cached for a year", ok, f"{answer.status} {cache}")


def origin_addresses(host: str, port: int = 443) -> list[tuple[str, str]]:
    """Every address ``host`` resolves to, as (family, address) pairs."""
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    families = {socket.AF_INET: "IPv4", socket.AF_INET6: "IPv6"}
    pairs = [
        (families.get(family, str(family)), str(address[0]))
        for family, _, _, _, address in found
    ]
    return sorted(set(pairs))


def origin_probe(address: str, sni: str, port: int = 443) -> str:
    """What the origin at ``address`` does for a client without
    Cloudflare's certificate, asking for the name ``sni``.

    Returns "HTTP <status>", "handshake refused: <OpenSSL reason>",
    "no connection: <error>" or "no HTTP answer". The origin's
    certificate is not checked: only whether it lets this client in.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # nosec B501 - testing refusal only
    request = f"GET / HTTP/1.1\r\nHost: {sni}\r\nConnection: close\r\n\r\n"
    try:
        with (
            socket.create_connection((address, port), TIMEOUT_SECONDS) as raw,
            context.wrap_socket(raw, server_hostname=sni) as tls,
        ):
            tls.sendall(request.encode("ascii"))
            first = tls.recv(64).decode("latin-1")
    except ssl.SSLError as error:
        return f"handshake refused: {error.reason or type(error).__name__}"
    except OSError as error:
        return f"no connection: {type(error).__name__}"
    match = re.match(r"HTTP/1\.[01] (\d{3})", first)
    return f"HTTP {match.group(1)}" if match else "no HTTP answer"


def tls11_probe(domain: str, port: int = 443) -> str:
    """Offer only TLS 1.0-1.1.

    Returns "refused" (the server rejected the version), "accepted",
    "unavailable" (this machine's OpenSSL will not offer TLS 1.1 at all)
    or "inconclusive: <error>" (anything else, such as no connection).
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # nosec B501 - testing refusal only
    try:
        context.set_ciphers("DEFAULT@SECLEVEL=0")
        context.minimum_version = ssl.TLSVersion.TLSv1
        context.maximum_version = ssl.TLSVersion.TLSv1_1
    except (ValueError, ssl.SSLError):
        return "unavailable"
    try:
        with (
            socket.create_connection((domain, port), TIMEOUT_SECONDS) as raw,
            context.wrap_socket(raw, server_hostname=domain),
        ):
            return "accepted"
    except ssl.SSLError as error:
        reason = error.reason or type(error).__name__
        if reason == "NO_PROTOCOLS_AVAILABLE":
            return "unavailable"
        return (
            "refused"
            if reason in VERSION_REFUSALS
            else f"inconclusive: {reason}"
        )
    except OSError as error:
        return f"inconclusive: {type(error).__name__}"


Resolve = Callable[[str], list[tuple[str, str]]]


def check_origin(
    probe: Probe, origin: str, domain: str, resolve: Resolve = origin_addresses
) -> list[Result]:
    """The origin lets nobody in but Cloudflare, at every address.

    Asked for its own name it refuses the handshake ("unrecognized name");
    asked for the site without Cloudflare's client certificate it answers
    400. Anything else fails, including an origin that cannot be reached:
    a check that never got there proves nothing.
    """
    addresses = resolve(origin)
    if not addresses:
        missing = f"{origin} does not resolve"
        return [
            Result(f"origin refuses {origin}", False, missing),
            Result(CERT_CHECK, False, missing),
        ]
    own = [(f, a, probe(a, origin)) for f, a in addresses]
    site = [(f, a, probe(a, domain)) for f, a in addresses]

    def describe(seen: list[tuple[str, str, str]]) -> str:
        return "; ".join(f"{f} {a}: {s}" for f, a, s in seen)

    refused = f"handshake refused: {UNRECOGNIZED_NAME}"
    return [
        Result(
            f"origin refuses {origin}",
            all(s == refused for _, _, s in own),
            describe(own),
        ),
        Result(
            CERT_CHECK,
            all(s == "HTTP 400" for _, _, s in site),
            describe(site),
        ),
    ]


def check_tls_floor(seen: str) -> Result:
    """Cloudflare's minimum TLS version is 1.2."""
    ok = {"refused": True, "unavailable": None}.get(seen, False)
    return Result("TLS 1.1 refused", ok, seen)


def _get(fetch_url: Fetch, url: str) -> Answer:
    try:
        return fetch_url(url)
    except (OSError, ValueError, http.client.HTTPException) as error:
        return Answer(0, (), f"{type(error).__name__}: {error}".encode())


def run(
    domain: str,
    origin: str,
    fetch_url: Fetch = fetch,
    probe: Probe = origin_probe,
    tls_probe: Callable[[str], str] = tls11_probe,
    resolve: Resolve = origin_addresses,
) -> list[Result]:
    """Every check, in the order docs/deploy/cloudflare.md lists them."""
    site = f"https://{domain}/"
    home = _get(fetch_url, site)
    results = check_home(home, expected_csp(SITE_CONF.read_text()))
    results += [
        check_redirect(
            "www to apex", _get(fetch_url, f"https://www.{domain}/"), site
        ),
        check_redirect(
            "http to https", _get(fetch_url, f"http://{domain}/"), site
        ),
        check_ready(_get(fetch_url, f"{site}api/readyz")),
    ]
    asset = ASSET.search(home.body.decode("utf-8", "replace"))
    asset_answer = (
        _get(fetch_url, f"https://{domain}{asset.group(1)}") if asset else None
    )
    results.append(check_asset(asset_answer))
    results += check_origin(probe, origin, domain, resolve)
    results.append(check_tls_floor(tls_probe(domain)))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("domain")
    parser.add_argument("--origin", default=ORIGIN)
    args = parser.parse_args(argv)
    for name in (args.domain, args.origin):
        if not DOMAIN.fullmatch(name):
            parser.error(f"not a lower-case DNS name: {name!r}")
    results = run(args.domain, args.origin)
    for result in results:
        label = {True: "PASS", False: "FAIL", None: "SKIP"}[result.ok]
        print(f"{label}  {result.name}: {result.detail}")
    failed = [r for r in results if r.ok is False]
    print(f"{len(results) - len(failed)} of {len(results)} checks did not fail")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
