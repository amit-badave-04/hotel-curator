"""Who sent a request: one trusted header from one trusted hop (ADR 011).

nginx computes the client address once (the TCP peer in compose,
``Fly-Client-IP`` behind Fly's edge, ``CF-Connecting-IP`` behind
Cloudflare) and overwrites ``X-Real-IP`` with it. The API believes that
header, and nginx's ``X-Request-ID``, only when the TCP peer is a trusted
proxy (``CURATOR_FORWARDED_ALLOW_IPS``). ``X-Forwarded-For``, ``Forwarded``,
``Fly-Client-IP`` and ``CF-Connecting-IP`` are never read here.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Self

from starlette.types import Scope

REAL_IP_HEADER = b"x-real-ip"
REQUEST_ID_HEADER = b"x-request-id"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,64}")
"""nginx's ``$request_id`` is 32 hex digits; anything else is not adopted."""
ANY = "*"
UNKNOWN = "unknown"
IPV4_MAPPED_PREFIX = 96
"""Length of the ::ffff:0:0/96 prefix that carries IPv4 in IPv6."""
IPV6_BUCKET_PREFIX = 64
"""One rate-limit bucket per IPv6 /64: a single host often owns a whole
/64 and could otherwise rotate addresses to reset its limits."""

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class TrustedProxies:
    """Peers whose ``X-Real-IP`` and ``X-Request-ID`` are believed."""

    networks: tuple[Network, ...] = ()
    any_peer: bool = False

    @classmethod
    def parse(cls, spec: str | None) -> Self:
        """Parse ``"*"`` or comma-separated addresses and CIDR ranges.

        Raises:
            ValueError: On an entry that is neither.
        """
        entries = [e.strip() for e in (spec or "").split(",") if e.strip()]
        if ANY in entries:
            return cls(any_peer=True)
        return cls(tuple(_unmapped(ipaddress.ip_network(e)) for e in entries))

    def trusts(self, peer: str | None) -> bool:
        """True if ``peer`` (an address string) is a trusted proxy.

        An IPv4-mapped IPv6 peer (``::ffff:a.b.c.d``, as a dual-stack
        listener reports IPv4 clients) is matched as its IPv4 address.
        """
        if self.any_peer:
            return True
        address = _ip(peer)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address is not None and any(
            address in network for network in self.networks
        )


def _unmapped(network: Network) -> Network:
    """An IPv4-mapped IPv6 range (``::ffff:a.b.c.d/n``) as its IPv4 range.

    Peers are compared in IPv4 form (see ``trusts``), so the allow-list
    must be too.
    """
    first = network.network_address
    if (
        isinstance(first, ipaddress.IPv6Address)
        and first.ipv4_mapped is not None
        and network.prefixlen >= IPV4_MAPPED_PREFIX
    ):
        bits = network.prefixlen - IPV4_MAPPED_PREFIX
        return ipaddress.IPv4Network(f"{first.ipv4_mapped}/{bits}")
    return network


def _ip(value: str | None) -> Address | None:
    try:
        return ipaddress.ip_address(value or "")
    except ValueError:
        return None


def _header(scope: Scope, name: bytes) -> str | None:
    values = [v for k, v in scope.get("headers", []) if k == name]
    # Exactly one: a repeated header means something other than nginx set it.
    return values[0].decode("latin-1").strip() if len(values) == 1 else None


def _peer(scope: Scope) -> str | None:
    client = scope.get("client")
    return str(client[0]) if client else None


def client_address(scope: Scope, trusted: TrustedProxies) -> str:
    """The client's address for rate limits.

    ``X-Real-IP`` when a trusted proxy sent exactly one valid address,
    otherwise the TCP peer.
    """
    peer = _peer(scope)
    if trusted.trusts(peer):
        forwarded = _ip(_header(scope, REAL_IP_HEADER))
        if forwarded is not None:
            return forwarded.compressed
    return peer or UNKNOWN


def forwarded_request_id(scope: Scope, trusted: TrustedProxies) -> str | None:
    """The request id nginx assigned, so both logs share one id."""
    if not trusted.trusts(_peer(scope)):
        return None
    value = _header(scope, REQUEST_ID_HEADER)
    if value is None or not REQUEST_ID_PATTERN.fullmatch(value):
        return None
    return value


def rate_limit_key(address: str) -> str:
    """The bucket an address counts against: itself, or its IPv6 /64."""
    ip = _ip(address)
    if not isinstance(ip, ipaddress.IPv6Address):
        return address
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.compressed
    prefix = f"{ip}/{IPV6_BUCKET_PREFIX}"
    return ipaddress.IPv6Network(prefix, strict=False).compressed
