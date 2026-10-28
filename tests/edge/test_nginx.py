"""nginx in each edge mode, run for real in front of a stub API (ADR 011).

The site is rendered by the web image's own script
(web/nginx/15-curator-site.sh); only the listen ports are moved to free
ones. Skipped unless nginx, envsubst and openssl are installed; CI's edge
job installs them and sets CURATOR_TEST_EDGE_REQUIRED=1, so there a
missing tool is an error, not a skip. The image runs a newer nginx than
most distributions; everything used here exists in both.
"""

import base64
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NGINX_DIR = ROOT / "web" / "nginx"
RENDER = NGINX_DIR / "15-curator-site.sh"
HSTS = "max-age=86400"
CLIENT = "198.51.100.7"
CLOUDFLARE_EDGE = "173.245.48.1"
OUTSIDER = "203.0.113.5"
NAMES = "curator.test www.curator.test"
FORGED = {
    "X-Real-IP": "192.0.2.66",
    "X-Forwarded-For": "192.0.2.67",
    "Forwarded": "for=192.0.2.68",
    "True-Client-IP": "192.0.2.69",
}
IDENTITY_HEADERS = (
    "x-forwarded-for",
    "forwarded",
    "fly-client-ip",
    "cf-connecting-ip",
    "true-client-ip",
)

TOOLS = ("nginx", "envsubst", "openssl")
if os.environ.get("CURATOR_TEST_EDGE_REQUIRED") and not all(
    shutil.which(tool) for tool in TOOLS
):
    raise RuntimeError(f"{', '.join(TOOLS)} are required here")
pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in TOOLS),
    reason="needs nginx, envsubst and openssl",
)


class EchoHandler(BaseHTTPRequestHandler):
    """Stands in for the API: echoes what nginx sent, like the API's id."""

    def do_GET(self) -> None:
        body = json.dumps(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("x-request-id", self.headers.get("x-request-id", ""))
        # nginx must replace this with its own single value.
        self.send_header("strict-transport-security", "max-age=1")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture(scope="module")
def api() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@dataclass
class Edge:
    """A running nginx: its plain and TLS ports."""

    http_port: int
    tls_port: int


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _ipv6() -> bool:
    try:
        with socket.socket(socket.AF_INET6) as sock:
            sock.bind(("::1", 0))
    except OSError:
        return False
    return True


def render(conf: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the image's render script against a scratch nginx directory."""
    shutil.copytree(NGINX_DIR, conf / "curator", dirs_exist_ok=True)
    (conf / "conf.d").mkdir(exist_ok=True)
    return subprocess.run(
        ["sh", str(RENDER)],
        env={"PATH": os.environ["PATH"], "CURATOR_NGINX_DIR": str(conf), **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _main_conf(prefix: Path) -> Path:
    temp = prefix / "temp"
    temp.mkdir()
    user = "user root root;\n" if os.geteuid() == 0 else ""
    paths = "\n".join(
        f"    {name}_temp_path {temp / name};"
        for name in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
    )
    main = prefix / "conf" / "nginx.conf"
    main.write_text(
        f"{user}worker_processes 1;\npid {prefix / 'nginx.pid'};\n"
        f"error_log {prefix / 'error.log'} warn;\nevents {{}}\n"
        f"http {{\n{paths}\n    include conf.d/*.conf;\n}}\n"
    )
    return main


def _move_ports(site: Path, edge: Edge) -> None:
    text = site.read_text()
    if not _ipv6():
        text = re.sub(r"^\s*listen \[::\]:\d+.*\n", "", text, flags=re.M)
    text = re.sub(
        r"listen (\[::\]:)?8080", rf"listen \g<1>{edge.http_port}", text
    )
    text = re.sub(
        r"listen (\[::\]:)?8443", rf"listen \g<1>{edge.tls_port}", text
    )
    site.write_text(text)


ASSET = "/assets/index-0123abcd.js"


def _web_root(tmp_path: Path) -> Path:
    """A stand-in for the built app: index.html and one hashed asset."""
    root = tmp_path / "html"
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<!doctype html><title>t</title>")
    (root / ASSET.lstrip("/")).write_text("console.log(1);")
    return root


def _serve_from(site: Path, root: Path) -> None:
    text = site.read_text()
    assert "root /usr/share/nginx/html;" in text
    site.write_text(text.replace("/usr/share/nginx/html", str(root)))


def _wait_for(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("nginx exited")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"nginx did not listen on {port}")


@pytest.fixture
def start(tmp_path: Path, api: str) -> Iterator[object]:
    """Render a mode, check it with nginx -t, run it; stop it afterwards."""
    running: list[subprocess.Popen[bytes]] = []

    def _start(mode: str, **env: str) -> Edge:
        conf = tmp_path / "conf"
        rendered = render(
            conf,
            {
                "CURATOR_EDGE": mode,
                "CURATOR_API_UPSTREAM": api,
                "CURATOR_TLS_DIR": str(tmp_path / "tls"),
                **env,
            },
        )
        assert rendered.returncode == 0, rendered.stderr
        edge = Edge(_free_port(), _free_port())
        _move_ports(conf / "conf.d" / "default.conf", edge)
        _serve_from(conf / "curator" / "site.conf", _web_root(tmp_path))
        main = _main_conf(tmp_path)
        command = ["nginx", "-p", str(tmp_path), "-c", str(main)]
        checked = subprocess.run(
            [*command, "-t"], capture_output=True, text=True
        )
        assert checked.returncode == 0, checked.stderr
        process = subprocess.Popen(
            [*command, "-g", "daemon off;"], stdout=subprocess.DEVNULL
        )
        running.append(process)
        _wait_for(edge.http_port, process)
        return edge

    yield _start
    for process in running:
        process.terminate()
        process.wait(timeout=10)


@dataclass
class Answer:
    """One response: status, headers (lower-case, repeated ones kept)."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes

    def header(self, name: str) -> list[str]:
        """Every value of a header."""
        return [v for k, v in self.headers if k.lower() == name]

    def echoed(self) -> dict[str, str]:
        """The headers the stub API received."""
        return dict(json.loads(self.body)["headers"])


def get(port: int, path: str, headers: dict[str, str] | None = None) -> Answer:
    """Plain HTTP GET to nginx."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    response = conn.getresponse()
    answer = Answer(response.status, response.getheaders(), response.read())
    conn.close()
    return answer


def _assert_only_x_real_ip(seen: dict[str, str], client: str) -> None:
    assert seen["x-real-ip"] == client
    assert not set(IDENTITY_HEADERS) & set(seen), seen


def test_local_mode_keys_on_the_peer_and_drops_forged_headers(
    start: object,
) -> None:
    edge = start("local")  # type: ignore[operator]
    answer = get(edge.http_port, "/api/v1/hotels", FORGED)
    assert answer.status == 200
    _assert_only_x_real_ip(answer.echoed(), "127.0.0.1")
    assert answer.echoed()["x-forwarded-prefix"] == "/api"


def test_fly_mode_trusts_fly_client_ip_only(start: object) -> None:
    edge = start("fly")  # type: ignore[operator]
    headers = {**FORGED, "Fly-Client-IP": CLIENT, "CF-Connecting-IP": OUTSIDER}
    answer = get(edge.http_port, "/api/v1/hotels", headers)
    _assert_only_x_real_ip(answer.echoed(), CLIENT)
    private = get(edge.http_port, "/api/v1/hotels", {"X-Real-IP": CLIENT})
    _assert_only_x_real_ip(private.echoed(), "127.0.0.1")


def test_request_id_is_nginx_own_and_reaches_the_api(start: object) -> None:
    edge = start("fly")  # type: ignore[operator]
    answer = get(edge.http_port, "/api/v1/meta", {"X-Request-ID": "mine-1234"})
    sent = answer.echoed()["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", sent)
    assert answer.header("x-request-id") == [sent]
    static = get(edge.http_port, "/", {"X-Request-ID": "mine-1234"})
    assert re.fullmatch(r"[0-9a-f]{32}", static.header("x-request-id")[0])


@pytest.mark.parametrize("path", ["/", "/explore", "/api/v1/meta", "/api/docs"])
def test_every_answer_has_exactly_one_hsts_value(
    start: object, path: str
) -> None:
    edge = start("fly")  # type: ignore[operator]
    answer = get(edge.http_port, path)
    assert answer.header("strict-transport-security") == [HSTS]
    assert answer.header("x-content-type-options") == ["nosniff"]


def test_redirects_are_relative(start: object) -> None:
    edge = start("fly")  # type: ignore[operator]
    answer = get(edge.http_port, "/api")
    assert answer.status == 301
    assert answer.header("location") == ["/api/"]


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"CURATOR_EDGE": "public"}, "CURATOR_EDGE must be"),
        ({"CURATOR_API_UPSTREAM": "api:8000; evil"}, "must be host:port"),
        ({"CURATOR_API_UPSTREAM": ""}, "must be host:port"),
        ({"CURATOR_EDGE": "cloudflare"}, "CURATOR_SERVER_NAMES must be"),
        (
            {"CURATOR_EDGE": "cloudflare", "CURATOR_SERVER_NAMES": "a.test"},
            "CURATOR_ORIGIN_CERT_B64 is not set",
        ),
        (
            {"CURATOR_EDGE": "cloudflare", "CURATOR_SERVER_NAMES": "a" * 111},
            "a name over 110 characters",
        ),
        ({"CURATOR_TLS_DIR": "relative/tls"}, "CURATOR_TLS_DIR must be"),
        ({"CURATOR_TLS_DIR": "/tmp/tls;x"}, "CURATOR_TLS_DIR must be"),
        ({"CURATOR_TLS_DIR": "/"}, "CURATOR_TLS_DIR must be"),
        ({"CURATOR_TLS_DIR": "/tmp/../etc/nginx"}, "CURATOR_TLS_DIR must be"),
        (
            {"CURATOR_EDGE": "cloudflare", "CURATOR_SERVER_NAMES": "   "},
            "CURATOR_SERVER_NAMES has no names",
        ),
        (
            {"CURATOR_EDGE": "cloudflare", "CURATOR_SERVER_NAMES": "a..test"},
            "CURATOR_SERVER_NAMES has a bad name",
        ),
        (
            {"CURATOR_EDGE": "cloudflare", "CURATOR_SERVER_NAMES": "."},
            "CURATOR_SERVER_NAMES has a bad name",
        ),
        (
            {"CURATOR_EDGE": "cloudflare", "CURATOR_SERVER_NAMES": "-x.test"},
            "CURATOR_SERVER_NAMES has a bad name",
        ),
        (
            {"CURATOR_EDGE": "cloudflare", "CURATOR_SERVER_NAMES": "a-.test"},
            "CURATOR_SERVER_NAMES has a bad name",
        ),
    ],
)
def test_render_refuses_bad_settings(
    tmp_path: Path, env: dict[str, str], message: str
) -> None:
    rendered = render(
        tmp_path,
        {
            "CURATOR_EDGE": "fly",
            "CURATOR_API_UPSTREAM": "api:8000",
            "CURATOR_TLS_DIR": str(tmp_path / "tls"),
            **env,
        },
    )
    assert rendered.returncode == 1
    assert message in rendered.stderr
    assert not (tmp_path / "conf.d" / "default.conf").exists()


def _openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _ca(directory: Path, name: str) -> tuple[Path, Path]:
    key, cert = directory / f"{name}.key", directory / f"{name}.pem"
    _openssl(
        "req", "-x509", "-newkey", "ec", "-pkeyopt",
        "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1",
        "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(cert),
    )  # fmt: skip
    return key, cert


def _leaf(directory: Path, name: str, ca: tuple[Path, Path]) -> Path:
    key, csr = directory / f"{name}.key", directory / f"{name}.csr"
    cert, ext = directory / f"{name}.pem", directory / f"{name}.ext"
    ext.write_text(f"subjectAltName=DNS:{name},DNS:www.{name}\n")
    _openssl(
        "req", "-new", "-newkey", "ec", "-pkeyopt",
        "ec_paramgen_curve:prime256v1", "-nodes", "-subj", f"/CN={name}",
        "-keyout", str(key), "-out", str(csr),
    )  # fmt: skip
    _openssl(
        "x509", "-req", "-in", str(csr), "-CA", str(ca[1]), "-CAkey",
        str(ca[0]), "-CAcreateserial", "-days", "1", "-extfile", str(ext),
        "-out", str(cert),
    )  # fmt: skip
    return cert


@dataclass
class Pki:
    """Test stand-ins for Cloudflare's origin and origin-pull certificates."""

    origin_ca: Path
    pull_client: tuple[Path, Path]
    rogue_client: tuple[Path, Path]
    secrets: dict[str, str]


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    d = tmp_path_factory.mktemp("pki")
    origin_ca, pull_ca = _ca(d, "origin-ca"), _ca(d, "pull-ca")
    origin = _leaf(d, "curator.test", origin_ca)
    client = _leaf(d, "pull-client", pull_ca)
    rogue = _leaf(d, "rogue-client", _ca(d, "rogue-ca"))

    def b64(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode()

    secrets = {
        "CURATOR_ORIGIN_CERT_B64": b64(origin),
        "CURATOR_ORIGIN_KEY_B64": b64(d / "curator.test.key"),
        "CURATOR_ORIGIN_PULL_CA_B64": b64(pull_ca[1]),
    }
    return Pki(
        origin_ca[1],
        (client, d / "pull-client.key"),
        (rogue, d / "rogue-client.key"),
        secrets,
    )


def via_cloudflare(
    port: int,
    pki: Pki,
    *,
    source: str = CLOUDFLARE_EDGE,
    sni: str = "curator.test",
    host: str = "curator.test",
    client: tuple[Path, Path] | None = None,
    headers: dict[str, str] | None = None,
) -> Answer:
    """HTTPS through a PROXY protocol header, as Fly's TCP handler sends."""
    context = ssl.create_default_context(cafile=str(pki.origin_ca))
    if client is not None:
        context.load_cert_chain(str(client[0]), str(client[1]))
    raw = socket.create_connection(("127.0.0.1", port), timeout=5)
    raw.sendall(f"PROXY TCP4 {source} 127.0.0.1 40000 443\r\n".encode())
    with context.wrap_socket(raw, server_hostname=sni) as tls:
        lines = ["GET /api/v1/meta HTTP/1.1", f"Host: {host}"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        tls.sendall(
            ("\r\n".join([*lines, "Connection: close", "", ""])).encode()
        )
        response = http.client.HTTPResponse(tls)  # type: ignore[arg-type]
        response.begin()
        return Answer(response.status, response.getheaders(), response.read())


@pytest.fixture
def cloudflare(start: object, pki: Pki) -> Edge:
    edge: Edge = start(  # type: ignore[operator]
        "cloudflare", CURATOR_SERVER_NAMES=NAMES, **pki.secrets
    )
    return edge


def test_cloudflare_mode_trusts_cf_connecting_ip_from_cloudflare(
    cloudflare: Edge, pki: Pki
) -> None:
    headers = {**FORGED, "CF-Connecting-IP": CLIENT, "Fly-Client-IP": OUTSIDER}
    answer = via_cloudflare(
        cloudflare.tls_port, pki, client=pki.pull_client, headers=headers
    )
    assert answer.status == 200
    _assert_only_x_real_ip(answer.echoed(), CLIENT)
    assert answer.header("strict-transport-security") == [HSTS]
    www = via_cloudflare(
        cloudflare.tls_port, pki, client=pki.pull_client,
        sni="www.curator.test", host="www.curator.test",
    )  # fmt: skip
    assert www.status == 200


def test_cloudflare_mode_refuses_everything_else(
    cloudflare: Edge, pki: Pki
) -> None:
    port, pull = cloudflare.tls_port, pki.pull_client
    assert via_cloudflare(port, pki).status == 400  # no client certificate
    assert via_cloudflare(port, pki, client=pki.rogue_client).status == 400
    outsider = via_cloudflare(port, pki, client=pull, source=OUTSIDER)
    assert outsider.status == 403
    other_host = via_cloudflare(port, pki, client=pull, host="evil.test")
    assert other_host.status == 421
    with pytest.raises(ssl.SSLError):
        via_cloudflare(port, pki, client=pull, sni="evil.test")


def test_cloudflare_mode_health_port_serves_nothing_else(
    cloudflare: Edge,
) -> None:
    assert get(cloudflare.http_port, "/healthz").status == 200
    assert get(cloudflare.http_port, "/").status == 404
    assert get(cloudflare.http_port, "/api/v1/meta").status == 404


def test_cloudflare_secrets_are_written_privately_and_never_printed(
    tmp_path: Path, pki: Pki
) -> None:
    tls = tmp_path / "tls"
    rendered = render(
        tmp_path / "conf",
        {
            "CURATOR_EDGE": "cloudflare",
            "CURATOR_API_UPSTREAM": "api:8000",
            "CURATOR_SERVER_NAMES": NAMES,
            "CURATOR_TLS_DIR": str(tls),
            **pki.secrets,
        },
    )
    assert rendered.returncode == 0, rendered.stderr
    output = rendered.stdout + rendered.stderr
    assert all(value not in output for value in pki.secrets.values())
    assert oct(tls.stat().st_mode & 0o777) == "0o700"
    assert all(oct(f.stat().st_mode & 0o777) == "0o600" for f in tls.iterdir())
    bad = {**pki.secrets, "CURATOR_ORIGIN_KEY_B64": "bm90IGEgcGVt"}
    refused = render(
        tmp_path / "conf2",
        {
            "CURATOR_EDGE": "cloudflare",
            "CURATOR_API_UPSTREAM": "api:8000",
            "CURATOR_SERVER_NAMES": NAMES,
            "CURATOR_TLS_DIR": str(tmp_path / "tls2"),
            **bad,
        },
    )
    assert "CURATOR_ORIGIN_KEY_B64 is not a PEM file" in refused.stderr


def test_only_found_assets_are_cached_for_a_year(start: object) -> None:
    edge = start("fly")  # type: ignore[operator]
    found = get(edge.http_port, ASSET)
    assert found.status == 200
    assert found.header("cache-control") == [
        "public, max-age=31536000, immutable"
    ]
    missing = get(edge.http_port, "/assets/HotelPage-removed.js")
    assert missing.status == 404
    assert missing.header("cache-control") == ["no-store"]
    assert get(edge.http_port, "/explore").header("cache-control") == [
        "no-cache"
    ]
    for ranged in ("bytes=0-3", "bytes=9000-"):
        whole = get(edge.http_port, ASSET, {"Range": ranged})
        assert whole.status == 200
        assert whole.header("cache-control") == [
            "public, max-age=31536000, immutable"
        ]
        assert len(whole.header("strict-transport-security")) == 1


def _post(port: int, path: str, body: bytes) -> Answer:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, body=body)
    response = conn.getresponse()
    answer = Answer(response.status, response.getheaders(), response.read())
    conn.close()
    return answer


def _assert_api_headers(answer: Answer) -> None:
    assert re.fullmatch(r"[0-9a-f]{32}", answer.header("x-request-id")[0])
    for name, value in (
        ("x-request-id", answer.header("x-request-id")[0]),
        ("cache-control", "no-store"),
        ("strict-transport-security", HSTS),
        ("x-frame-options", "DENY"),
        ("x-content-type-options", "nosniff"),
        ("referrer-policy", "no-referrer"),
    ):
        assert answer.header(name) == [value], name
    assert answer.header("server") == ["nginx"]


def test_answers_nginx_makes_for_the_api_carry_its_headers(
    start: object,
) -> None:
    edge = start("fly", CURATOR_API_UPSTREAM=f"127.0.0.1:{_free_port()}")  # type: ignore[operator]
    for path in ("/api/v1/hotels", "/api/docs"):
        down = get(edge.http_port, path)
        assert down.status == 502
        _assert_api_headers(down)
    too_big = _post(edge.http_port, "/api/v1/analyze", b"x" * 70_000)
    assert too_big.status == 413
    _assert_api_headers(too_big)


class OwnIdHandler(EchoHandler):
    """An API that did not adopt nginx's id (it does not trust nginx)."""

    def do_GET(self) -> None:
        body = b'{"error": {"request_id": "api-own-id-0001"}}'
        self.send_response(404)
        self.send_header("x-request-id", "api-own-id-0001")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_the_request_id_header_matches_what_the_api_answered(
    start: object,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), OwnIdHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        upstream = f"127.0.0.1:{server.server_address[1]}"
        edge = start("fly", CURATOR_API_UPSTREAM=upstream)  # type: ignore[operator]
        answer = get(edge.http_port, "/api/v1/nope")
        assert answer.header("x-request-id") == ["api-own-id-0001"]
    finally:
        server.shutdown()


def test_api_answers_carry_each_header_once(start: object) -> None:
    edge = start("fly")  # type: ignore[operator]
    answer = get(edge.http_port, "/api/v1/meta")
    assert answer.status == 200
    _assert_api_headers(answer)
    assert answer.header("x-request-id") == [answer.echoed()["x-request-id"]]


def test_a_long_domain_still_starts(start: object, pki: Pki) -> None:
    domain = f"{'a' * 60}.{'b' * 30}.example"
    edge = start(  # type: ignore[operator]
        "cloudflare",
        CURATOR_SERVER_NAMES=f"{domain} www.{domain}",
        **pki.secrets,
    )
    assert get(edge.http_port, "/healthz").status == 200
