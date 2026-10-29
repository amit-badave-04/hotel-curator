"""Smoke-test a deployed hotel-curator from outside.

    python scripts/smoke.py https://hotel-curator.fly.dev
    python scripts/smoke.py https://example.org --model absent
    python scripts/smoke.py http://127.0.0.1:8080          # docker compose

The deploy workflow runs it after every deploy. It first waits (up to
--wait seconds) for /api/readyz to answer "ready", since a machine that
has just started may need a moment, then checks once:

- the home page: 200, the app's HTML and script, nginx's exact CSP (from
  web/nginx/site.conf), a request id;
- a deep link (/explore) gets the same app (the single-page fallback);
- the hashed script: 200 with its one-year cache lifetime;
- /api/readyz: ready with hotels, and the model when one is expected;
- /api/v1/meta: the same hotel count, analyze_available as expected;
- /api/v1/hotels: one hotel per page, total equal to the hotel count, and
  that hotel's own page;
- POST /api/v1/analyze: labels from the same model, or 503 when no model
  is expected;
- robots.txt names this address's sitemap;
- /NOTICE.txt and /THIRD-PARTY-NOTICES.txt (the data, model and code
  attribution the footer links) are served.

Each check prints PASS or FAIL with what it saw; the exit code is 1 if any
check fails. It needs only the standard library.
"""

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ModelExpectation = Literal["required", "absent", "any"]
SITE_CONF = Path(__file__).resolve().parents[1] / "web" / "nginx" / "site.conf"
TIMEOUT_SECONDS = 20
WAIT_SECONDS = 180
POLL_SECONDS = 5
SCRIPT = re.compile(r'<script type="module"[^>]* src="(/assets/[^"]+\.js)"')
REQUEST_ID = re.compile(r"[0-9a-f]{32}")
IMMUTABLE = "public, max-age=31536000, immutable"
SAMPLE_REVIEW = {"liked": "Spotless room and very friendly staff."}
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
DNS_NAME = re.compile(
    r"(?=.{4,100}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)


@dataclass(frozen=True)
class Answer:
    """One HTTP answer: status (0 when no answer), headers, body."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def header(self, name: str) -> str:
        """The first value of a header, or ""."""
        return next((v for k, v in self.headers if k == name.lower()), "")

    def json(self) -> Any:
        """The body as JSON, or None if it is not JSON."""
        try:
            return json.loads(self.body)
        except (ValueError, RecursionError):
            return None


@dataclass(frozen=True)
class Result:
    """One check: PASS when ok."""

    name: str
    ok: bool
    detail: str


Fetch = Callable[[str, str, bytes | None], Answer]
"""(method, url, JSON body or None) -> Answer."""


def fetch(method: str, url: str, body: bytes | None = None) -> Answer:
    """One request; HTTP errors are answers, network errors status 0."""
    headers = {"User-Agent": "hotel-curator-smoke"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(  # noqa: S310 - base URL validated
        url, data=body, method=method, headers=headers
    )
    try:
        try:
            # base_url() admits only https, or http on this machine.
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as r:  # noqa: S310  # nosec B310
                status, raw, data = r.status, r.headers, r.read()
        except urllib.error.HTTPError as error:
            # Reading the body can fail too; the outer handler takes that.
            status, raw, data = error.code, error.headers, error.read()
    except (OSError, ValueError, http.client.HTTPException) as error:
        return Answer(0, (), f"{type(error).__name__}: {error}".encode())
    return Answer(status, tuple((k.lower(), v) for k, v in raw.items()), data)


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


def base_url(value: str) -> str:
    """``https://<dns name>``, or ``http://`` on a local address.

    Raises:
        ValueError: For anything else (a path, a query, another scheme).
    """
    url = urllib.parse.urlsplit(value)
    host = url.hostname or ""
    local = url.scheme == "http" and host in LOCAL_HOSTS
    # The netloc itself: no port, no user, lower case.
    named = url.scheme == "https" and DNS_NAME.fullmatch(url.netloc) is not None
    if not (local or named) or url.path not in ("", "/") or url.query:
        raise ValueError(f"not a site address: {value!r}")
    return f"{url.scheme}://{url.netloc}"


def wait_ready(
    get: Callable[[str], Answer],
    base: str,
    wait: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Answer:
    """Poll /api/readyz until it is ready or ``wait`` seconds pass."""
    deadline = clock() + wait
    while True:
        answer = get(f"{base}/api/readyz")
        body = answer.json()
        ready = isinstance(body, dict) and body.get("status") == "ready"
        if (answer.status == 200 and ready) or clock() >= deadline:
            return answer
        sleep(POLL_SECONDS)


def check_page(name: str, answer: Answer, csp: str) -> Result:
    """An HTML page of the app, with nginx's headers (its exact CSP)."""
    text = answer.body.decode("utf-8", "replace")
    checks = (
        (f"HTTP {answer.status}", answer.status != 200),
        ("no app root", 'id="root"' not in text),
        ("no app script", not SCRIPT.search(text)),
        ("another CSP", answer.header("content-security-policy") != csp),
        (
            "no request id",
            not REQUEST_ID.fullmatch(answer.header("x-request-id")),
        ),
    )
    problems = [problem for problem, bad in checks if bad]
    return Result(
        name, not problems, "; ".join(problems) or "the app, nginx's CSP"
    )


def check_asset(answer: Answer | None) -> Result:
    """The hashed script the home page loads."""
    if answer is None:
        return Result(
            "script cached for a year", False, "no script on the page"
        )
    cache = answer.header("cache-control")
    ok = answer.status == 200 and cache == IMMUTABLE
    return Result(
        "script cached for a year", ok, f"HTTP {answer.status}, {cache!r}"
    )


def _model_problem(model: object, expect: ModelExpectation) -> str:
    if expect == "required" and not (isinstance(model, str) and model):
        return "no model loaded"
    if expect == "absent" and model:
        return f"model {model!r} loaded, none expected"
    return ""


def check_ready(answer: Answer, expect: ModelExpectation) -> Result:
    """Readiness: ready, with hotels, and the model if expected."""
    body = answer.json()
    if answer.status != 200 or not isinstance(body, dict):
        detail = answer.body[:200].decode("utf-8", "replace")
        return Result("API ready", False, f"HTTP {answer.status}: {detail}")
    hotels = body.get("hotels")
    problem = (
        "not ready"
        if body.get("status") != "ready"
        else "no hotels"
        if not (
            isinstance(hotels, int)
            and not isinstance(hotels, bool)
            and hotels > 0
        )
        else _model_problem(body.get("model"), expect)
    )
    return Result(
        "API ready",
        not problem,
        problem or f"{hotels} hotels, model {body.get('model')}",
    )


def check_meta(
    answer: Answer, hotels: object, expect: ModelExpectation
) -> Result:
    """/v1/meta agrees with readiness and says whether analyze can answer."""
    body = answer.json()
    if answer.status != 200 or not isinstance(body, dict):
        return Result("meta", False, f"HTTP {answer.status}")
    problems = []
    if body.get("hotels") != hotels:
        problems.append(f"{body.get('hotels')} hotels, readiness said {hotels}")
    available = body.get("analyze_available")
    if expect != "any" and available is not (expect == "required"):
        problems.append(f"analyze_available is {available}")
    return Result(
        "meta", not problems, "; ".join(problems) or f"{hotels} hotels"
    )


def check_search(
    get: Callable[[str], Answer], base: str, hotels: object
) -> list[Result]:
    """One page of search results, and the first hotel's own page."""
    answer = get(f"{base}/api/v1/hotels?page_size=1")
    body = answer.json()
    items = body.get("items") if isinstance(body, dict) else None
    if answer.status != 200 or not isinstance(items, list) or len(items) != 1:
        return [
            Result("search", False, f"HTTP {answer.status}, no single hotel")
        ]
    total_ok = body.get("total") == hotels
    search = Result(
        "search", total_ok, f"total {body.get('total')} of {hotels}"
    )
    hotel_id = items[0].get("hotel_id") if isinstance(items[0], dict) else None
    if not (
        isinstance(hotel_id, str) and re.fullmatch(r"h_[0-9a-f]{10}", hotel_id)
    ):
        return [search, Result("hotel page", False, f"bad id {hotel_id!r}")]
    page = get(f"{base}/api/v1/hotels/{hotel_id}")
    detail = page.json()
    ok = (
        page.status == 200
        and isinstance(detail, dict)
        and detail.get("hotel_id") == hotel_id
    )
    return [
        search,
        Result("hotel page", ok, f"HTTP {page.status} for {hotel_id}"),
    ]


def check_analyze(
    answer: Answer, model: object, expect: ModelExpectation
) -> Result:
    """The model labels a review, or answers 503 when none is loaded."""
    if expect == "absent" or (expect == "any" and not model):
        return Result(
            "analyze",
            answer.status == 503,
            f"HTTP {answer.status}, 503 expected",
        )
    body = answer.json()
    parts = body.get("parts") if isinstance(body, dict) else None
    labels = [
        label
        for part in parts or []
        if isinstance(part, dict)
        for label in part.get("labels") or []
    ]
    same = isinstance(body, dict) and body.get("model") == model
    ok = answer.status == 200 and same and bool(labels)
    return Result("analyze", ok, f"HTTP {answer.status}, {len(labels)} labels")


def check_robots(answer: Answer, base: str) -> Result:
    """robots.txt points at this address's sitemap.

    A local stack (http) is built for its public address, so there any
    https sitemap line will do.
    """
    text = answer.body.decode("utf-8", "replace")
    if base.startswith("http://"):
        want = "Sitemap: https://<public address>/sitemap.xml"
        found = re.search(
            r"^Sitemap: https://[a-z0-9.-]+/sitemap\.xml$", text, re.M
        )
    else:
        want = f"Sitemap: {base}/sitemap.xml"
        found = re.search(f"^{re.escape(want)}$", text, re.M)
    ok = answer.status == 200 and found is not None
    return Result("robots.txt", ok, f"HTTP {answer.status}, {want!r} expected")


NOTICE_MARK = b"Booking.com"
THIRD_PARTY_MARK = b"Third-party software in the hotel-curator web app"


def check_attribution(notice: Answer, third_party: Answer) -> Result:
    """The attribution files the footer links are served."""
    ok = (
        notice.status == 200
        and NOTICE_MARK in notice.body
        and third_party.status == 200
        and THIRD_PARTY_MARK in third_party.body
    )
    detail = (
        f"NOTICE HTTP {notice.status}, "
        f"third-party licences HTTP {third_party.status}"
    )
    return Result("attribution files", ok, detail)


def run(
    base: str,
    expect: ModelExpectation = "required",
    wait: float = WAIT_SECONDS,
    fetch_url: Fetch = fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Result]:
    """Every check, in the order the module docstring lists them."""
    csp = expected_csp(SITE_CONF.read_text())

    def get(url: str) -> Answer:
        return fetch_url("GET", url, None)

    ready = wait_ready(get, base, wait, sleep=sleep)
    home = get(f"{base}/")
    script = SCRIPT.search(home.body.decode("utf-8", "replace"))
    body = ready.json() if isinstance(ready.json(), dict) else {}
    hotels, model = body.get("hotels"), body.get("model")
    analyze = fetch_url(
        "POST", f"{base}/api/v1/analyze", json.dumps(SAMPLE_REVIEW).encode()
    )
    return [
        check_page("home page", home, csp),
        check_page("deep link", get(f"{base}/explore"), csp),
        check_asset(get(f"{base}{script.group(1)}") if script else None),
        check_ready(ready, expect),
        check_meta(get(f"{base}/api/v1/meta"), hotels, expect),
        *check_search(get, base, hotels),
        check_analyze(analyze, model, expect),
        check_robots(get(f"{base}/robots.txt"), base),
        check_attribution(
            get(f"{base}/NOTICE.txt"), get(f"{base}/THIRD-PARTY-NOTICES.txt")
        ),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "url", help="the site, e.g. https://hotel-curator.fly.dev"
    )
    parser.add_argument(
        "--model", choices=("required", "absent", "any"), default="required"
    )
    parser.add_argument("--wait", type=float, default=WAIT_SECONDS)
    args = parser.parse_args(argv)
    try:
        base = base_url(args.url)
    except ValueError as error:
        parser.error(str(error))
    results = run(base, args.model, args.wait)
    for result in results:
        print(
            f"{'PASS' if result.ok else 'FAIL'}  {result.name}: {result.detail}"
        )
    failed = [r for r in results if not r.ok]
    print(f"{len(results) - len(failed)} of {len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
