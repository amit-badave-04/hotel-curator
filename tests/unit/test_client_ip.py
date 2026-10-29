"""Client identity from one trusted header (api/clientip.py, ADR 011)."""

import pytest
from pydantic import ValidationError

from hotel_curator.api.clientip import (
    TrustedProxies,
    client_address,
    forwarded_request_id,
    rate_limit_key,
)
from hotel_curator.config import Settings

NGINX = "172.28.0.10"
NGINX_ID = "0123456789abcdef0123456789abcdef"


def _scope(peer: str | None, *headers: tuple[str, str]) -> dict[str, object]:
    return {
        "type": "http",
        "client": (peer, 40000) if peer else None,
        "headers": [(k.encode(), v.encode()) for k, v in headers],
    }


def test_parse_addresses_ranges_and_any() -> None:
    proxies = TrustedProxies.parse(" 172.28.0.10, 10.0.0.0/8 ,fdaa::/16")
    assert proxies.trusts(NGINX)
    assert proxies.trusts("10.1.2.3")
    assert proxies.trusts("fdaa:0:1::2")
    assert not proxies.trusts("172.28.0.11")
    assert proxies.trusts("::ffff:172.28.0.10")
    assert not proxies.trusts("::ffff:172.28.0.11")
    for spec in ("::ffff:172.28.0.10", "::ffff:172.28.0.0/120"):
        mapped = TrustedProxies.parse(spec)
        assert mapped.trusts("::ffff:172.28.0.10")
        assert mapped.trusts("172.28.0.10")
        assert not mapped.trusts("172.28.1.10")
    assert not proxies.trusts("testclient")
    assert not proxies.trusts(None)
    assert TrustedProxies.parse("*").trusts("203.0.113.9")
    assert not TrustedProxies.parse(None).trusts(NGINX)
    assert not TrustedProxies.parse("").trusts(NGINX)


@pytest.mark.parametrize("spec", ["nginx", "10.0.0.0/33", "10.0.0.1/8"])
def test_settings_refuse_a_bad_proxy_list(spec: str) -> None:
    with pytest.raises(ValidationError, match="forwarded_allow_ips"):
        Settings(forwarded_allow_ips=spec)


def test_x_real_ip_counts_only_from_a_trusted_proxy() -> None:
    trusted = TrustedProxies.parse(NGINX)
    real = ("x-real-ip", "198.51.100.7")
    assert client_address(_scope(NGINX, real), trusted) == "198.51.100.7"
    assert client_address(_scope("203.0.113.5", real), trusted) == (
        "203.0.113.5"
    )
    assert client_address(_scope(None, real), trusted) == "unknown"


@pytest.mark.parametrize(
    "headers",
    [
        (),
        (("x-real-ip", "not an address"),),
        (("x-real-ip", "198.51.100.7"), ("x-real-ip", "198.51.100.8")),
        (("x-forwarded-for", "198.51.100.7"),),
        (("forwarded", "for=198.51.100.7"),),
        (("fly-client-ip", "198.51.100.7"),),
        (("cf-connecting-ip", "198.51.100.7"),),
    ],
)
def test_anything_but_one_valid_x_real_ip_means_the_peer(
    headers: tuple[tuple[str, str], ...],
) -> None:
    trusted = TrustedProxies.parse(NGINX)
    assert client_address(_scope(NGINX, *headers), trusted) == NGINX


def test_ipv6_addresses_are_normalised() -> None:
    scope = _scope(NGINX, ("x-real-ip", "2001:DB8:0:0::0001"))
    assert client_address(scope, TrustedProxies.parse(NGINX)) == "2001:db8::1"


def test_request_id_is_adopted_only_from_a_trusted_proxy() -> None:
    trusted = TrustedProxies.parse(NGINX)
    sent = ("x-request-id", NGINX_ID)
    assert forwarded_request_id(_scope(NGINX, sent), trusted) == NGINX_ID
    assert forwarded_request_id(_scope("203.0.113.5", sent), trusted) is None
    for bad in ("short", "has space in it", "x" * 65, "<script>alert</s>"):
        scope = _scope(NGINX, ("x-request-id", bad))
        assert forwarded_request_id(scope, trusted) is None


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("198.51.100.7", "198.51.100.7"),
        ("::ffff:198.51.100.7", "198.51.100.7"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:bbbb::9", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("unknown", "unknown"),
    ],
)
def test_ipv6_clients_share_a_bucket_per_64(address: str, key: str) -> None:
    assert rate_limit_key(address) == key


def test_a_route_cannot_add_a_second_request_id() -> None:
    import asyncio

    from starlette.types import Message, Receive, Scope, Send

    from hotel_curator.api.middleware import RequestContextMiddleware

    async def route(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-request-id", b"route-own-id")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    app = RequestContextMiddleware(route, TrustedProxies.parse(NGINX))
    scope = _scope(NGINX, ("x-request-id", NGINX_ID))
    scope.update(method="GET", path="/v1/meta")
    asyncio.run(app(scope, receive, send))
    ids = [v for k, v in sent[0]["headers"] if k == b"x-request-id"]
    assert ids == [NGINX_ID.encode()]
