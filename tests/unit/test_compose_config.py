"""The settings docker compose passes to the API must be valid Settings."""

import re
from pathlib import Path

import pytest
import yaml

from hotel_curator.config import Settings

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = re.compile(r"\$\{(\w+):-([^}]*)\}")


def _api_environment() -> dict[str, str]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    env = compose["services"]["api"]["environment"]
    # Resolve ${VAR:-default} the way compose does when VAR is unset.
    return {
        k: DEFAULT.sub(lambda m: m.group(2), str(v)) for k, v in env.items()
    }


def test_compose_api_environment_is_valid_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no stray .env file
    env = _api_environment()
    assert all(key.startswith("CURATOR_") for key in env)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings()
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.forwarded_allow_ips == env["CURATOR_FORWARDED_ALLOW_IPS"]
    known = {f"CURATOR_{name.upper()}" for name in Settings.model_fields}
    assert set(env) <= known, set(env) - known


def test_env_example_names_only_real_settings() -> None:
    text = (ROOT / ".env.example").read_text()
    names = set(re.findall(r"^#? ?(CURATOR_\w+)=", text, flags=re.MULTILINE))
    known = {f"CURATOR_{name.upper()}" for name in Settings.model_fields}
    assert names, "no settings found in .env.example"
    assert names <= known, names - known
