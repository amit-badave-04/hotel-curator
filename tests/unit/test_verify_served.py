"""The served model package must be the one the promotion rule judged."""

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from hotel_curator.checksums import sha256_file
from hotel_curator.cli.main import app
from hotel_curator.inference.verify import (
    LFS_POINTER_PREFIX,
    PACKAGE_FILES,
    check_served_package,
)

ROOT = Path(__file__).resolve().parents[2]
WINNER = "tiny-ft"


def _artifacts(tmp_path: Path) -> Path:
    """A promoted package whose files match the promotion record."""
    package = tmp_path / "models" / WINNER
    package.mkdir(parents=True)
    for name in PACKAGE_FILES:
        (package / name).write_bytes(f"content of {name}".encode())
    files = [
        {"file": name, "sha256": sha256_file(package / name)}
        for name in PACKAGE_FILES
    ]
    record = {
        "winner": WINNER,
        "rows": [{"name": WINNER, "package": {"files_sha256": files}}],
    }
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "promotion.json").write_text(json.dumps(record))
    return tmp_path


def test_matching_package_is_ok(tmp_path: Path) -> None:
    served = check_served_package(_artifacts(tmp_path))
    assert served.ok
    assert served.winner == WINNER
    assert served.directory == tmp_path / "models" / WINNER


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, "model.onnx: missing"),
        (LFS_POINTER_PREFIX + b"\noid sha256:abc\n", "Git LFS pointer"),
        (b"different weights", "SHA-256 differs"),
    ],
)
def test_wrong_model_file_is_reported(
    tmp_path: Path, content: bytes | None, expected: str
) -> None:
    artifacts = _artifacts(tmp_path)
    model = artifacts / "models" / WINNER / "model.onnx"
    if content is None:
        model.unlink()
    else:
        model.write_bytes(content)
    served = check_served_package(artifacts)
    assert not served.ok
    assert len(served.problems) == 1
    assert expected in served.problems[0]


def test_no_winner_is_an_error(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    path = artifacts / "evals" / "promotion.json"
    record = json.loads(path.read_text())
    record["winner"] = None
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="promoted no model"):
        check_served_package(artifacts)


def test_cli_exit_code_follows_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hotel_curator.config import get_settings

    artifacts = _artifacts(tmp_path / "home" / "artifacts")
    monkeypatch.setenv("CURATOR_HOME", str(artifacts.parent))
    get_settings.cache_clear()
    try:
        ok = CliRunner().invoke(app, ["model", "verify-served"])
        assert ok.exit_code == 0, ok.output
        assert "every file matches" in ok.output
        (artifacts / "models" / WINNER / "metadata.json").write_text("{}")
        bad = CliRunner().invoke(app, ["model", "verify-served"])
        assert bad.exit_code == 1
        assert "metadata.json: SHA-256 differs" in bad.output
    finally:
        get_settings.cache_clear()


def test_api_image_checks_the_weights_when_required() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "ARG REQUIRE_MODEL=0" in dockerfile
    guard = 'if [ "$REQUIRE_MODEL" = "1" ]; then curator model verify-served'
    assert guard in dockerfile
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    args = compose["services"]["api"]["build"]["args"]
    assert args["REQUIRE_MODEL"] == "${REQUIRE_MODEL:-0}"
