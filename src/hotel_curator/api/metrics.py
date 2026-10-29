"""Prometheus metrics, served on a listener of their own (ADR 011, D17).

The metrics port is not a Fly service, so it has no public address: only
Fly's scraper (``[metrics]`` in fly.toml) and machines on the organisation's
private network reach it, and nginx never proxies it. Labels are bounded
and hold no request data: the method (one of a fixed set), the matched
route's template (``/v1/hotels/{hotel_id}``, never a hotel id) and the
status code.
"""

import logging
import socket
import sys
import threading
from dataclasses import dataclass
from socketserver import ThreadingMixIn
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    make_wsgi_app,
)
from starlette.types import Scope

logger = logging.getLogger(__name__)

KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "OPTIONS"})
"""Methods kept as labels; any other is counted as "other", so a client
cannot create new series by inventing methods."""
OTHER_METHOD = "other"
UNMATCHED_ROUTE = "unmatched"
"""Route label for requests answered before routing (404s, 429s, an
oversized body) or by no route."""
LATENCY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)  # fmt: skip
SERVER_STOP_SECONDS = 5


def method_label(scope: Scope) -> str:
    """The request method, or ``other`` for one outside a fixed set."""
    method = scope.get("method")
    return method if method in KNOWN_METHODS else OTHER_METHOD


def route_label(scope: Scope) -> str:
    """The matched route's path template (FastAPI records the route)."""
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE


class _QuietHandler(WSGIRequestHandler):
    """No plain-text line on stderr for each scrape."""

    def log_message(self, format: str, *args: Any) -> None:
        """Drop wsgiref's access line."""


class _MetricsHTTPServer(ThreadingMixIn, WSGIServer):
    """Threaded WSGI server whose errors go to the JSON log."""

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Log one line instead of socketserver's traceback on stderr.

        The peer's address is left out, like every other API log line.
        """
        error = sys.exc_info()[1]
        logger.warning("Metrics request failed: %s", type(error).__name__)


class _MetricsHTTPServer6(_MetricsHTTPServer):
    address_family = socket.AF_INET6


@dataclass
class MetricsServer:
    """A running metrics listener, stopped with :meth:`close`."""

    server: WSGIServer
    thread: threading.Thread

    @property
    def port(self) -> int:
        """The port bound (useful when asked for port 0)."""
        return int(self.server.server_address[1])

    def close(self) -> None:
        """Stop serving and release the port."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=SERVER_STOP_SECONDS)


class ApiMetrics:
    """The API's metrics, in a registry of their own (not the global one).

    Example:
        >>> metrics = ApiMetrics()
        >>> metrics.observe({"method": "GET"}, 404, 0.002)
        >>> metrics.sample("curator_http_requests_total",
        ...     method="GET", route="unmatched", status="404")
        1.0
    """

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        # Resident memory, CPU time and open files of this process.
        ProcessCollector(registry=self.registry)
        self._requests = Counter(
            "curator_http_requests",
            "Requests answered, by method, route template and status.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self._duration = Histogram(
            "curator_http_request_duration_seconds",
            "Time from the request's arrival to the end of its response.",
            ("method", "route"),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self._model_loaded = Gauge(
            "curator_model_loaded",
            "1 when the analysis model is loaded, else 0.",
            registry=self.registry,
        )

    def observe(self, scope: Scope, status: int, seconds: float) -> None:
        """Count one answered request and record how long it took."""
        method, route = method_label(scope), route_label(scope)
        self._requests.labels(method, route, str(status)).inc()
        self._duration.labels(method, route).observe(seconds)

    def set_model_loaded(self, loaded: bool) -> None:
        """Record whether ``/v1/analyze`` has a model to answer with."""
        self._model_loaded.set(1 if loaded else 0)

    def sample(self, name: str, **labels: str) -> float | None:
        """One sample's current value (``None`` if absent)."""
        return self.registry.get_sample_value(name, labels)

    def serve(self, port: int, host: str) -> MetricsServer:
        """Serve the metrics on ``host:port`` from a daemon thread.

        Raises:
            OSError: If the address cannot be bound.
        """
        ipv6 = ":" in host
        server_class = _MetricsHTTPServer6 if ipv6 else _MetricsHTTPServer
        server = make_server(
            host,
            port,
            make_wsgi_app(self.registry),
            server_class=server_class,
            handler_class=_QuietHandler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return MetricsServer(server, thread)
