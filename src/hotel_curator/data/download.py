"""Resumable, checksum-verified file download.

Why: every raw input of the pipeline is fetched by URL and pinned by its
SHA-256 digest, so a fresh machine gets byte-identical inputs or a loud
failure. Downloads resume from a ``.part`` file after an interruption, which
matters for the ~100 MB+ raw files on slow links.

Only HTTPS URLs are accepted. The file is written to ``<target>.part`` and
renamed into place only after the digest matches, so a half-written or
tampered file never sits at the final path.
"""

import logging
import re
from pathlib import Path

import httpx

from hotel_curator.checksums import CHUNK_SIZE, sha256_file

__all__ = [
    "CHUNK_SIZE",
    "ChecksumMismatchError",
    "InsecureRedirectError",
    "download_file",
    "sha256_file",
]

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60.0
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ChecksumMismatchError(RuntimeError):
    """The downloaded bytes do not match the pinned SHA-256 digest."""


class InsecureRedirectError(ValueError):
    """A download was redirected to a URL that is not HTTPS."""


def _require_https(request: httpx.Request) -> None:
    """Event hook: refuse to send any request (redirects too) without TLS."""
    if request.url.scheme != "https":
        raise InsecureRedirectError(f"refusing non-https request {request.url}")


class _RestartDownloadError(Exception):
    """The partial file cannot be resumed; start again from byte 0."""


def _stream_to_part(client: httpx.Client, url: str, part: Path) -> None:
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with client.stream("GET", url, headers=headers) as response:
        if response.url.scheme != "https":  # an injected client without hook
            raise InsecureRedirectError(f"redirected to {response.url}")
        if response.status_code == 416:
            raise _RestartDownloadError("range not satisfiable")
        response.raise_for_status()
        if response.status_code == 206:
            content_range = response.headers.get("Content-Range", "")
            if content_range and not content_range.startswith(
                f"bytes {offset}-"
            ):
                raise _RestartDownloadError(
                    f"unexpected Content-Range {content_range}"
                )
        elif offset:
            logger.info("Server ignored Range; restarting %s", url)
            offset = 0
        mode = "ab" if offset else "wb"
        with part.open(mode) as handle:
            for chunk in response.iter_bytes(CHUNK_SIZE):
                handle.write(chunk)


def download_file(
    url: str,
    target: Path,
    sha256: str,
    *,
    client: httpx.Client | None = None,
) -> Path:
    """Download ``url`` to ``target`` unless a verified copy already exists.

    Args:
        url: HTTPS URL of the file.
        target: Final path of the file.
        sha256: Expected lowercase hex SHA-256 digest.
        client: Optional preconfigured client (tests inject a mock).

    Returns:
        ``target``, guaranteed to match ``sha256``.

    Raises:
        ValueError: If the URL is not HTTPS or the digest is malformed.
        InsecureRedirectError: If a redirect leads away from HTTPS.
        ChecksumMismatchError: If the downloaded bytes do not match.
        httpx.HTTPError: On network or HTTP errors.
    """
    if not url.startswith("https://"):
        raise ValueError(f"Only https URLs are allowed, got {url!r}")
    if not _SHA256_RE.match(sha256):
        raise ValueError(f"Expected a 64-char lowercase hex sha256: {sha256!r}")
    part = target.with_name(target.name + ".part")
    if _already_verified(target, part, sha256):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    http = client or _default_client()
    try:
        _fetch_part(http, url, part)
    finally:
        if owns_client:
            http.close()
    actual = sha256_file(part)
    if actual != sha256:
        part.unlink(missing_ok=True)
        raise ChecksumMismatchError(
            f"{url}: expected sha256 {sha256}, got {actual}"
        )
    part.replace(target)
    logger.info("Downloaded and verified %s", target)
    return target


def _default_client() -> httpx.Client:
    """Follows redirects, but never to a non-HTTPS URL."""
    return httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        event_hooks={"request": [_require_https]},
    )


def _already_verified(target: Path, part: Path, sha256: str) -> bool:
    """True if ``target`` (or a complete ``.part``, then promoted) matches."""
    if target.exists() and sha256_file(target) == sha256:
        logger.info("Verified cached file %s", target)
        return True
    if part.exists() and sha256_file(part) == sha256:
        part.replace(target)
        logger.info("Promoted verified partial file %s", target)
        return True
    return False


def _fetch_part(client: httpx.Client, url: str, part: Path) -> None:
    """Resume into ``part``; start over once if the server cannot resume."""
    try:
        _stream_to_part(client, url, part)
    except _RestartDownloadError as exc:
        logger.info("Restarting %s from scratch: %s", url, exc)
        part.unlink(missing_ok=True)
        _stream_to_part(client, url, part)
