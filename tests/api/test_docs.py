"""The API docs page loads nothing from third-party hosts."""

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hotel_curator.api.app import create_app
from hotel_curator.api.routes.docs import DOCS_CSP
from hotel_curator.config import Settings
from tests.helpers import POLICY_PATH

VENDORED = {
    "swagger-ui-bundle.js": (
        "050bc415ee7048dcd881682678f720264e7da5e373f7461d7c58c755305255f7"
    ),
    "swagger-ui.css": (
        "1ac324f7dcd27e4b9386b4bd6421271ec147e922a22c05ba24b11515e9aa6321"
    ),
}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(  # type: ignore[call-arg]
        home=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'docs.db'}",
        tier_policy=POLICY_PATH,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_docs_page_is_self_contained(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == DOCS_CSP
    assert "https:" not in DOCS_CSP
    html = response.text
    assert not re.search(r"(src|href)=\"(https?:)?//", html)
    assert "<script>" not in html  # no inline script
    for asset in re.findall(r"(?:src|href)=\"(docs-assets/[^\"]+)\"", html):
        assert client.get(f"/{asset}").status_code == 200, asset


def test_vendored_swagger_ui_matches_its_pinned_hashes(
    client: TestClient,
) -> None:
    for name, digest in VENDORED.items():
        body = client.get(f"/docs-assets/{name}").content
        assert hashlib.sha256(body).hexdigest() == digest, name


def test_docs_are_not_part_of_the_api_schema(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "docs" in p]
    assert client.get("/redoc").status_code == 404
