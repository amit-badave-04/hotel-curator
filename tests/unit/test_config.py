"""Tests for typed settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from hotel_curator.config import Settings


def test_paths_derive_from_home(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path)
    assert settings.data_dir == tmp_path / "data"
    assert settings.processed_dir == tmp_path / "data" / "processed"
    assert settings.raw_dir == tmp_path / "data" / "raw"
    assert settings.labels_dir == tmp_path / "data" / "labels"
    assert settings.manifests_dir == tmp_path / "data" / "manifests"
    assert settings.demo_dir == tmp_path / "data" / "demo"
    assert settings.artifacts_dir == tmp_path / "artifacts"
    assert settings.resolved_tier_policy == (
        tmp_path / "config" / "tier_policy.yaml"
    )


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
    monkeypatch.setenv("CURATOR_LOG_FORMAT", "json")
    monkeypatch.setenv("CURATOR_RATE_LIMIT_PER_MINUTE", "7")
    settings = Settings()
    assert settings.home == tmp_path
    assert settings.log_format == "json"
    assert settings.rate_limit_per_minute == 7


def test_invalid_enum_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURATOR_LOG_FORMAT", "xml")
    with pytest.raises(ValidationError):
        Settings()


def test_env_example_lists_only_real_settings() -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    names = {
        line.split("=", 1)[0].lstrip("# ").strip()
        for line in example.read_text().splitlines()
        if line.lstrip("# ").startswith("CURATOR_")
    }
    fields = {f"CURATOR_{name.upper()}" for name in Settings.model_fields}
    assert names <= fields, names - fields
