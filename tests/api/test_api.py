"""API tests: behaviour, validation, errors and the security requirements.

Each test gets a fresh SQLite database migrated with Alembic and seeded with
synthetic hotels scored by the real pipeline. SR-n refers to the security
requirements in docs/specs/v2-plan.md.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest
from fastapi.testclient import TestClient

from hotel_curator.api.app import create_app
from hotel_curator.config import Settings
from hotel_curator.db.migrate import upgrade
from hotel_curator.db.seed import seed, write_results
from hotel_curator.db.session import (
    make_engine,
    make_session_factory,
    session_scope,
)
from hotel_curator.inference.package import Prediction
from hotel_curator.taxonomy import LABELS
from tests.helpers import POLICY_PATH, synthetic_results

GOOD = "h_00000000a1"
DIRTY = "h_00000000b2"
NOISY = "h_00000000c3"


@pytest.fixture(scope="module")
def results_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("demo") / "hotel_results.json.gz"
    write_results(path, synthetic_results())
    return path


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "home": tmp_path,
        "database_url": f"sqlite:///{tmp_path / 'test.db'}",
        "tier_policy": POLICY_PATH,
        "cors_origins": ["http://allowed.example"],
        "log_format": "console",
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


def _client(
    settings: Settings, results: Path | None, peer: str = "testclient"
) -> TestClient:
    upgrade(settings.db_url())
    if results is not None:
        engine = make_engine(settings.db_url())
        with session_scope(make_session_factory(engine)) as session:
            seed(session, results)
        engine.dispose()
    return TestClient(create_app(settings), client=(peer, 50000))


@pytest.fixture
def client(tmp_path: Path, results_file: Path) -> Iterator[TestClient]:
    with _client(_settings(tmp_path), results_file) as c:
        yield c


def _assert_error(response: object, status: int, code: str) -> dict:
    assert response.status_code == status  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"]
    return body["error"]


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["hotels"] == 5


def test_not_ready_without_data(tmp_path: Path) -> None:
    with _client(_settings(tmp_path), None) as c:
        response = c.get("/readyz")
    assert response.status_code == 503


def test_not_ready_when_the_database_fails(
    tmp_path: Path, results_file: Path
) -> None:
    settings = _settings(tmp_path)
    with _client(settings, results_file) as c:
        engine = make_engine(settings.db_url())
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE hotels RENAME TO hotels_gone")
        engine.dispose()
        response = c.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "database unavailable"}


def test_search_defaults_to_best_first(client: TestClient) -> None:
    page = client.get("/v1/hotels").json()
    assert page["total"] == 5
    tiers = [h["tier"] for h in page["items"]]
    assert tiers[:3] == ["excellent", "excellent", "good"]
    assert tiers[-1] == "not_enough_reviews"


def test_search_filters(client: TestClient) -> None:
    def ids(**params: object) -> list[str]:
        response = client.get("/v1/hotels", params=params)
        assert response.status_code == 200, response.text
        return sorted(h["hotel_id"] for h in response.json()["items"])

    assert ids(city="London") == [NOISY, "h_00000000d4"]
    assert ids(tier=["caution"]) == [DIRTY]
    assert ids(strong="cleanliness", city="Paris") == [GOOD]
    assert NOISY not in ids(avoid_weak="noise")
    assert ids(q="bravo") == [DIRTY]
    assert ids(q="100%") == ["h_00000000e5"]  # % is matched literally


def test_pagination(client: TestClient) -> None:
    first = client.get("/v1/hotels", params={"page_size": 2}).json()
    second = client.get("/v1/hotels", params={"page_size": 2, "page": 2}).json()
    assert len(first["items"]) == 2
    assert {h["hotel_id"] for h in first["items"]}.isdisjoint(
        h["hotel_id"] for h in second["items"]
    )
    assert first["total"] == second["total"] == 5


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 1000},
        {"tier": "platinum"},
        {"sort": "price; DROP TABLE hotels"},
        {"city": "Paris'; DROP TABLE hotels;--"},
        {"q": "x" * 81},
        {"q": "a\x00b"},
        {"q": "tab\there"},
        {"strong": "pool"},
    ],
)
def test_search_rejects_bad_input(client: TestClient, params: dict) -> None:
    error = _assert_error(
        client.get("/v1/hotels", params=params), 422, "invalid_request"
    )
    assert error["fields"]
    # SR-4: invalid values are not echoed back.
    for value in params.values():
        if len(str(value)) >= 4:
            assert str(value) not in json.dumps(error)
    assert client.get("/v1/hotels").json()["total"] == 5  # SR-14


def test_hotel_detail(client: TestClient) -> None:
    detail = client.get(f"/v1/hotels/{NOISY}").json()
    assert detail["label"] == "Good, but noisy"
    assert detail["if_ignored"] == {"noise": "excellent"}
    noise = next(a for a in detail["attributes"] if a["attribute"] == "noise")
    assert noise["grade"] == "weak"
    assert noise["quotes"]
    assert detail["provenance"]["params_version"]


def test_hotel_errors(client: TestClient) -> None:
    _assert_error(client.get("/v1/hotels/h_ffffffffff"), 404, "not_found")
    _assert_error(client.get("/v1/hotels/H_NOT_AN_ID"), 422, "invalid_request")
    # A literal "../" never reaches the app (the client removes dot segments
    # first), but an encoded ".." does, and the id pattern rejects it.
    error = _assert_error(
        client.get("/v1/hotels/%2E%2E"), 422, "invalid_request"
    )
    assert error["fields"][0]["location"] == ["path", "hotel_id"]
    # An encoded slash is decoded before routing, so it never matches the
    # one-segment hotel route at all.
    _assert_error(client.get("/v1/hotels/..%2Fetc"), 404, "not_found")


def test_compare(client: TestClient) -> None:
    response = client.get("/v1/compare", params={"ids": [GOOD, DIRTY]})
    assert response.status_code == 200
    rows = {r["attribute"]: r for r in response.json()["attributes"]}
    assert rows["cleanliness"]["leader"] == GOOD
    assert rows["cleanliness"]["leader_probability"] >= 0.95
    assert rows["staff"]["leader"] is None  # no credible difference


def test_compare_must_be_leader_has_fewer_complaints(
    client: TestClient,
) -> None:
    response = client.get("/v1/compare", params={"ids": [NOISY, GOOD]})
    assert response.status_code == 200
    noise = {r["attribute"]: r for r in response.json()["attributes"]}["noise"]
    assert noise["leader"] == GOOD
    assert noise["leader_probability"] >= 0.95


@pytest.mark.parametrize(
    ("ids", "status"),
    [
        ([GOOD], 422),
        ([GOOD, GOOD], 422),
        ([GOOD, DIRTY, NOISY, "h_00000000d4", "h_00000000e5"], 422),
        ([GOOD, "h_ffffffffff"], 404),
        ([GOOD, "bad"], 422),
    ],
)
def test_compare_errors(
    client: TestClient, ids: list[str], status: int
) -> None:
    assert client.get("/v1/compare", params={"ids": ids}).status_code == status


def test_cities_and_meta(client: TestClient) -> None:
    cities = client.get("/v1/cities").json()
    assert [c["city"] for c in cities] == ["London", "Paris", "Vienna"]
    meta = client.get("/v1/meta").json()
    assert meta["hotels"] == 5
    assert meta["model"] == "test-model"
    assert len(meta["attributes"]) == 8
    assert meta["policy"]["confirmation"] == 0.95
    assert meta["analyze_available"] is False
    period = meta["data_period"]
    assert period == {"first_month": "2015-09", "last_month": "2017-08"}


def test_meta_says_when_analyze_can_answer(client: TestClient) -> None:
    client.app.state.curator._model = _FakeModel()  # type: ignore[attr-defined]
    assert client.get("/v1/meta").json()["analyze_available"] is True


def test_openapi_documents_every_route(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/v1/hotels",
        "/v1/hotels/{hotel_id}",
        "/v1/compare",
        "/v1/cities",
        "/v1/meta",
        "/v1/analyze",
        "/healthz",
        "/readyz",
    ):
        assert path in paths


class _FakeModel:
    name = "fake-model"
    labels: ClassVar[list[str]] = list(LABELS)

    def predict(self, texts: list[str], parts: list[str]) -> Prediction:
        labels = np.zeros((len(texts), len(LABELS)), dtype=bool)
        probs = np.full((len(texts), len(LABELS)), 0.02)
        for i, text in enumerate(texts):
            if "dirty" in text.lower():
                k = LABELS.index("cleanliness_negative")
                labels[i, k], probs[i, k] = True, 0.93
        return Prediction(labels=labels, probabilities=probs)


def test_analyze_needs_a_model(client: TestClient) -> None:
    response = client.post("/v1/analyze", json={"liked": "nice"})
    _assert_error(response, 503, "unavailable")


def test_the_reason_a_model_is_missing_is_logged(
    tmp_path: Path, results_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _client(_settings(tmp_path), results_file) as c:
        assert c.app.state.curator.model() is None  # type: ignore[attr-defined]
    assert "no promotion decision; run curator model compare" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize("decision", ['{"winner": "x"', '{"rows": []}'])
def test_a_malformed_promotion_decision_does_not_stop_the_api(
    tmp_path: Path, results_file: Path, decision: str
) -> None:
    evals = tmp_path / "artifacts" / "evals"
    evals.mkdir(parents=True)
    (evals / "promotion.json").write_text(decision)
    with _client(_settings(tmp_path), results_file) as c:
        assert c.get("/readyz").status_code == 200
        _assert_error(
            c.post("/v1/analyze", json={"liked": "Clean room"}),
            503,
            "unavailable",
        )


def test_analyze_labels_without_echoing_text(client: TestClient) -> None:
    state = client.app.state.curator  # type: ignore[attr-defined]
    state._model = _FakeModel()
    secret = "The room was DIRTY, ask for Maria"
    response = client.post("/v1/analyze", json={"disliked": secret})
    assert response.status_code == 200
    body = response.json()
    assert body["parts"][0]["labels"][0]["label"] == "cleanliness_negative"
    assert "Maria" not in response.text  # SR-4


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"liked": "   "},
        {"liked": "x" * 2001},
        {"liked": 5},
        {"liked": ["a"]},
    ],
)
def test_analyze_rejects_bad_input(client: TestClient, payload: dict) -> None:
    state = client.app.state.curator  # type: ignore[attr-defined]
    state._model = _FakeModel()
    response = client.post("/v1/analyze", json=payload)
    _assert_error(response, 422, "invalid_request")


def test_oversized_body_is_rejected(client: TestClient) -> None:
    body = json.dumps({"liked": "a" * 70_000})
    response = client.post(
        "/v1/analyze",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413  # SR-2


def test_rate_limits(tmp_path: Path, results_file: Path) -> None:
    settings = _settings(
        tmp_path, rate_limit_per_minute=5, analyze_rate_limit_per_minute=2
    )
    with _client(settings, results_file) as c:
        c.app.state.curator._model = _FakeModel()  # type: ignore[attr-defined]
        codes = [
            c.post("/v1/analyze", json={"liked": "ok"}).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        limited = c.post("/v1/analyze", json={"liked": "ok"})
        assert int(limited.headers["retry-after"]) >= 1
        _assert_error(limited, 429, "rate_limited")
        # Counted before the body is parsed: malformed bodies are limited too.
        malformed = c.post(
            "/v1/analyze",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        _assert_error(malformed, 429, "rate_limited")
        statuses = [c.get("/v1/cities").status_code for _ in range(4)]
        assert statuses[-1] == 429
        assert c.get("/healthz").status_code == 200  # probes are not limited


NGINX = "172.28.0.10"


def _analyze_codes(c: TestClient, n: int, **headers: str) -> list[int]:
    return [
        c.post("/v1/analyze", json={"liked": "ok"}, headers=headers).status_code
        for _ in range(n)
    ]


def test_clients_behind_the_proxy_get_separate_buckets(
    tmp_path: Path, results_file: Path
) -> None:
    settings = _settings(
        tmp_path, analyze_rate_limit_per_minute=2, forwarded_allow_ips=NGINX
    )
    with _client(settings, results_file, peer=NGINX) as c:
        c.app.state.curator._model = _FakeModel()  # type: ignore[attr-defined]
        first = {"x-real-ip": "198.51.100.1"}
        assert _analyze_codes(c, 3, **first) == [200, 200, 429]
        assert _analyze_codes(c, 2, **{"x-real-ip": "198.51.100.2"}) == [
            200,
            200,
        ]
        # A client cannot leave its bucket with headers nginx overwrites
        # or the API ignores.
        dodges = {"x-forwarded-for": "203.0.113.9", "fly-client-ip": "::1"}
        assert _analyze_codes(c, 1, **first, **dodges) == [429]


def test_forged_identity_headers_from_other_peers_are_ignored(
    tmp_path: Path, results_file: Path
) -> None:
    settings = _settings(
        tmp_path, analyze_rate_limit_per_minute=2, forwarded_allow_ips=NGINX
    )
    with _client(settings, results_file, peer="203.0.113.50") as c:
        c.app.state.curator._model = _FakeModel()  # type: ignore[attr-defined]
        codes = [
            _analyze_codes(c, 1, **{"x-real-ip": f"198.51.100.{i}"})[0]
            for i in range(3)
        ]
        assert codes == [200, 200, 429]
        sent = "0123456789abcdef0123456789abcdef"
        echoed = c.get("/v1/cities", headers={"x-request-id": sent})
        assert echoed.headers["x-request-id"] != sent


def test_request_id_from_the_proxy_is_kept(
    tmp_path: Path, results_file: Path
) -> None:
    settings = _settings(tmp_path, forwarded_allow_ips=NGINX)
    with _client(settings, results_file, peer=NGINX) as c:
        sent = "0123456789abcdef0123456789abcdef"
        kept = c.get("/v1/cities", headers={"x-request-id": sent})
        assert kept.headers["x-request-id"] == sent
        bad = c.get("/v1/cities", headers={"x-request-id": "bad id"})
        assert len(bad.headers["x-request-id"]) == 32


def test_security_headers_and_request_id(client: TestClient) -> None:
    response = client.get("/v1/cities")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert len(response.headers["x-request-id"]) == 32


def test_cors_allow_list(client: TestClient) -> None:
    allowed = client.get(
        "/v1/cities", headers={"origin": "http://allowed.example"}
    )
    assert allowed.headers["access-control-allow-origin"] == (
        "http://allowed.example"
    )
    assert "access-control-allow-credentials" not in allowed.headers
    blocked = client.get(
        "/v1/cities", headers={"origin": "http://evil.example"}
    )
    assert "access-control-allow-origin" not in blocked.headers  # SR-13


def test_seed_is_idempotent(tmp_path: Path, results_file: Path) -> None:
    settings = _settings(tmp_path)
    upgrade(settings.db_url())
    engine = make_engine(settings.db_url())
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        assert seed(session, results_file) == 5
    with session_scope(factory) as session:
        assert seed(session, results_file) == 0
    engine.dispose()


def test_api_does_not_import_the_training_stack() -> None:
    """ADR 007: the serving image needs no torch, scikit-learn or spaCy."""
    import subprocess
    import sys

    code = (
        "import sys, hotel_curator.api.app\n"
        "heavy = {'torch', 'transformers', 'sklearn', 'lightgbm', 'mlflow',"
        " 'spacy'}\n"
        "print(sorted(heavy & set(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


def test_a_broken_model_package_is_reported_not_raised(
    tmp_path: Path, results_file: Path
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    # What a checkout without Git LFS leaves behind instead of weights.
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:00\n"
    (package / "model.onnx").write_text(pointer)
    (package / "tokenizer.json").write_text(pointer)
    (package / "metadata.json").write_text("{}")
    settings = _settings(tmp_path, model_package=package)
    with _client(settings, results_file) as c:
        ready = c.get("/readyz")
        assert ready.status_code == 200
        assert "model" not in ready.json()
        state = c.app.state.curator  # type: ignore[attr-defined]
        error = state._model_error
        assert error
        _assert_error(
            c.post("/v1/analyze", json={"liked": "Clean room"}),
            503,
            "unavailable",
        )
        assert state._model_error == error  # not retried per request


def test_unexpected_errors_keep_the_error_shape_and_headers(
    tmp_path: Path, results_file: Path
) -> None:
    settings = _settings(tmp_path)
    upgrade(settings.db_url())
    app = create_app(settings)

    @app.get("/v1/boom")
    def boom() -> None:
        raise RuntimeError("secret detail")

    with TestClient(app) as c:
        response = c.get("/v1/boom")
    assert response.status_code == 500
    error = _assert_error(response, 500, "internal_error")
    assert error["request_id"] == response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "secret detail" not in response.text


def test_oversized_chunked_body_gets_413(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"liked": "'
        for _ in range(100):
            yield b"x" * 1_000
        yield b'"}'

    response = client.post(
        "/v1/analyze",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    error = _assert_error(response, 413, "payload_too_large")
    assert error["request_id"] == response.headers["x-request-id"]


def test_trailing_slash_is_not_redirected(client: TestClient) -> None:
    response = client.get("/v1/cities/", follow_redirects=False)
    _assert_error(response, 404, "not_found")


def test_analyze_skips_parts_without_usable_text(client: TestClient) -> None:
    state = client.app.state.curator  # type: ignore[attr-defined]
    state._model = _FakeModel()
    _assert_error(
        client.post("/v1/analyze", json={"liked": "很好 😀"}),
        422,
        "invalid_request",
    )
    both = client.post(
        "/v1/analyze", json={"liked": "😀😀", "disliked": "The room was dirty"}
    )
    assert both.status_code == 200
    assert [p["part"] for p in both.json()["parts"]] == ["disliked"]


def test_meta_evaluates_the_served_model_not_the_latest_winner(
    tmp_path: Path, results_file: Path
) -> None:
    from hotel_curator.api.routes.meta import load_evaluation

    def row(name: str, f1: float, vs_v1: dict | None) -> dict:
        summary = {
            "test_review_macro_f1": f1,
            "test_review_macro_f1_low": f1 - 0.01,
            "test_review_macro_f1_high": f1 + 0.01,
            "test_mean_review_ece": 0.02,
        }
        return {
            "name": name,
            "family": "encoder",
            "summary": summary,
            "vs_v1": vs_v1,
        }

    diff = {"point": 0.2, "low": 0.1, "high": 0.3}
    evals = tmp_path / "evals"
    evals.mkdir()
    (evals / "promotion.json").write_text(
        json.dumps(
            {
                "winner": "newer-model",
                "v1_config": "v1",
                "test_reviews": 1200,
                "test_hotels": 182,
                "rows": [
                    row("v1", 0.6, None),
                    row("test-model", 0.85, diff),
                    row("newer-model", 0.9, diff),
                ],
            }
        )
    )
    served = load_evaluation(evals, "test-model")
    assert served is not None
    assert served.model == "test-model"
    assert served.macro_f1.point == 0.85
    assert (served.test_reviews, served.test_hotels) == (1200, 182)
    assert load_evaluation(evals, "unknown-model") is None
    assert load_evaluation(evals, None) is None


def test_meta_shows_the_served_package_numbers_when_judged(
    tmp_path: Path,
) -> None:
    from hotel_curator.api.routes.meta import Evaluation, load_evaluation

    def summary(f1: float) -> dict:
        return {
            "test_review_macro_f1": f1,
            "test_review_macro_f1_low": f1 - 0.01,
            "test_review_macro_f1_high": f1 + 0.01,
            "test_mean_review_ece": 0.01,
        }

    package = {
        "current": True,
        "test_review_macro_f1": {"point": 0.88, "low": 0.87, "high": 0.89},
        "test_mean_review_ece": 0.03,
    }
    winner = {
        "name": "m",
        "family": "encoder",
        "summary": summary(0.9),
        "vs_v1": {"point": 0.3, "low": 0.2, "high": 0.4},
        "package": package,
        "package_vs_v1": {"point": 0.28, "low": 0.18, "high": 0.38},
    }
    v1 = {"name": "v1", "family": "v1", "summary": summary(0.6)}
    evals = tmp_path / "evals"
    evals.mkdir()

    def evaluate(rows: list[dict]) -> Evaluation:
        decision = {"winner": "m", "v1_config": "v1", "rows": rows}
        (evals / "promotion.json").write_text(json.dumps(decision))
        result = load_evaluation(evals, "m")
        assert result is not None
        return result

    served = evaluate([v1, winner])
    assert served.macro_f1.point == 0.88
    assert served.difference.point == 0.28
    assert served.mean_review_ece == 0.03
    stale = {**winner, "package": {**package, "current": False}}
    assert evaluate([v1, stale]).macro_f1.point == 0.9
