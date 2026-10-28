"""Per-client rate limiting with the ``limits`` library (SR-3).

A moving-window limiter keyed by client address and route group, enforced
in ASGI middleware so a request is counted (and refused) before its body is
read or parsed. Counters live in process memory: with several workers each
enforces its own limit (documented in the API description); a shared Redis
storage is a configuration change.
"""

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass

from limits import RateLimitItem, RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.types import ASGIApp, Receive, Scope, Send

from hotel_curator.api.clientip import UNKNOWN, rate_limit_key
from hotel_curator.api.middleware import send_error

TOO_MANY = "Too many requests; try again later."


@dataclass(frozen=True)
class RateRule:
    """``per_minute`` requests per client on paths under ``prefix``."""

    prefix: str
    group: str
    item: RateLimitItem

    @classmethod
    def per_minute(cls, prefix: str, group: str, limit: int) -> "RateRule":
        """A rule of ``limit`` requests per minute."""
        return cls(prefix, group, RateLimitItemPerMinute(limit))

    def applies(self, path: str) -> bool:
        """True for the prefix itself and every path below it."""
        return path == self.prefix or path.startswith(f"{self.prefix}/")


class RateLimitMiddleware:
    """Refuse requests over any matching rule with 429 and ``Retry-After``.

    CORS preflights (``OPTIONS``) are not counted. The client is the
    address :class:`~hotel_curator.api.middleware.RequestContextMiddleware`
    put in the scope state: ``X-Real-IP`` from a trusted proxy, otherwise
    the TCP peer (:mod:`hotel_curator.api.clientip`).
    """

    def __init__(
        self,
        app: ASGIApp,
        limiter: MovingWindowRateLimiter,
        rules: Sequence[RateRule],
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.rules = tuple(rules)

    def _retry_after(self, rule: RateRule, client: str) -> int:
        reset_at, _ = self.limiter.get_window_stats(
            rule.item, rule.group, client
        )
        return max(1, math.ceil(reset_at - time.time()))

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Handle one ASGI connection."""
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        address = scope.get("state", {}).get("client")
        if address is None:
            peer = scope.get("client")
            address = peer[0] if peer else UNKNOWN
        client = rate_limit_key(address)
        for rule in (r for r in self.rules if r.applies(scope["path"])):
            if not self.limiter.hit(rule.item, rule.group, client):
                retry = str(self._retry_after(rule, client)).encode()
                request_id = scope.get("state", {}).get("request_id")
                await send_error(
                    send, 429, TOO_MANY, request_id, [(b"retry-after", retry)]
                )
                return
        await self.app(scope, receive, send)


def new_limiter() -> MovingWindowRateLimiter:
    """A fresh in-memory limiter (one per app instance)."""
    return MovingWindowRateLimiter(MemoryStorage())
