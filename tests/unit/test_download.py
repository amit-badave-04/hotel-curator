"""Tests for the resumable, checksum-verified downloader (no real network)."""

import hashlib
from pathlib import Path

import httpx
import pytest

from hotel_curator.data import download
from hotel_curator.data.download import (
    ChecksumMismatchError,
    download_file,
    sha256_file,
)

PAYLOAD = b"hotel,review\n" * 5000
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)


def _transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        range_header = request.headers.get("range")
        if range_header:
            start = int(range_header.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=PAYLOAD[start:])
        return httpx.Response(200, content=PAYLOAD)

    return httpx.MockTransport(handler)


def test_download_writes_file_and_verifies_checksum(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    with httpx.Client(transport=_transport(calls)) as client:
        out = download_file(
            "https://example.test/data.csv",
            tmp_path / "data.csv",
            DIGEST,
            client=client,
        )
    assert out.read_bytes() == PAYLOAD
    assert sha256_file(out) == DIGEST
    assert not (tmp_path / "data.csv.part").exists()


def test_existing_valid_file_is_not_downloaded_again(tmp_path: Path) -> None:
    target = tmp_path / "data.csv"
    target.write_bytes(PAYLOAD)
    calls: list[httpx.Request] = []
    with httpx.Client(transport=_transport(calls)) as client:
        download_file("https://example.test/d", target, DIGEST, client=client)
    assert calls == []


def test_partial_download_resumes_with_range(tmp_path: Path) -> None:
    target = tmp_path / "data.csv"
    (tmp_path / "data.csv.part").write_bytes(PAYLOAD[:1000])
    calls: list[httpx.Request] = []
    with httpx.Client(transport=_transport(calls)) as client:
        download_file("https://example.test/d", target, DIGEST, client=client)
    assert calls[0].headers["range"] == "bytes=1000-"
    assert target.read_bytes() == PAYLOAD


def test_checksum_mismatch_removes_file_and_raises(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    with (
        httpx.Client(transport=_transport(calls)) as client,
        pytest.raises(ChecksumMismatchError),
    ):
        download_file(
            "https://example.test/d", tmp_path / "x", "0" * 64, client=client
        )
    assert not (tmp_path / "x").exists()
    assert not (tmp_path / "x.part").exists()


def test_rejects_non_https_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="https"):
        download_file("http://example.test/d", tmp_path / "x", DIGEST)


def test_rejects_malformed_digest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sha256"):
        download_file("https://example.test/d", tmp_path / "x", "abc")


def test_complete_part_file_is_verified_and_promoted(tmp_path: Path) -> None:
    (tmp_path / "data.csv.part").write_bytes(PAYLOAD)
    calls: list[httpx.Request] = []
    with httpx.Client(transport=_transport(calls)) as client:
        out = download_file(
            "https://example.test/d",
            tmp_path / "data.csv",
            DIGEST,
            client=client,
        )
    assert calls == []
    assert out.read_bytes() == PAYLOAD


def test_range_not_satisfiable_restarts_from_scratch(tmp_path: Path) -> None:
    # A stale, over-long .part makes the server answer 416.
    (tmp_path / "x.part").write_bytes(PAYLOAD + b"garbage")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers.get("range"):
            return httpx.Response(416)
        return httpx.Response(200, content=PAYLOAD)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out = download_file(
            "https://example.test/d", tmp_path / "x", DIGEST, client=client
        )
    assert out.read_bytes() == PAYLOAD
    assert len(calls) == 2


def test_mismatched_content_range_restarts(tmp_path: Path) -> None:
    (tmp_path / "x.part").write_bytes(PAYLOAD[:1000])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("range"):
            return httpx.Response(
                206,
                content=PAYLOAD[500:],
                headers={"Content-Range": f"bytes 500-{SIZE - 1}/{SIZE}"},
            )
        return httpx.Response(200, content=PAYLOAD)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out = download_file(
            "https://example.test/d", tmp_path / "x", DIGEST, client=client
        )
    assert out.read_bytes() == PAYLOAD


def test_redirect_to_plain_http_is_refused(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"Location": "http://x.test/f"})
        return httpx.Response(200, content=b"tampered")

    transport = httpx.MockTransport(handler)
    hooked = {"request": [download._require_https]}
    for client in (
        httpx.Client(transport=transport, follow_redirects=True),
        httpx.Client(
            transport=transport, follow_redirects=True, event_hooks=hooked
        ),
    ):
        with client, pytest.raises(download.InsecureRedirectError):
            download.download_file(
                "https://x.test/f", tmp_path / "f", "0" * 64, client=client
            )
    assert not (tmp_path / "f").exists()
