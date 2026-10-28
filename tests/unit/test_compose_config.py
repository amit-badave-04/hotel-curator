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
    # The API image sets CURATOR_ENVIRONMENT=production (Dockerfile).
    dockerfile = (ROOT / "Dockerfile").read_text()
    baked = re.search(r"^ENV CURATOR_ENVIRONMENT=(\w+)$", dockerfile, re.M)
    assert baked is not None and baked.group(1) == "production"
    monkeypatch.setenv("CURATOR_ENVIRONMENT", baked.group(1))
    settings = Settings()
    assert settings.db_url().startswith("postgresql+psycopg://")
    assert settings.forwarded_allow_ips == env["CURATOR_FORWARDED_ALLOW_IPS"]
    known = {f"CURATOR_{name.upper()}" for name in Settings.model_fields}
    assert set(env) <= known, set(env) - known


def test_env_example_names_only_real_settings() -> None:
    text = (ROOT / ".env.example").read_text()
    names = set(re.findall(r"^#? ?(CURATOR_\w+)=", text, flags=re.MULTILINE))
    known = {f"CURATOR_{name.upper()}" for name in Settings.model_fields}
    assert names, "no settings found in .env.example"
    assert names <= known, names - known


def test_database_is_reachable_only_through_the_api_socket() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())[
        "services"
    ]
    db, api = services["db"], services["api"]
    assert db["network_mode"] == "none"
    assert "ports" not in db and "networks" not in db
    assert db["command"] == ["postgres", "-c", "listen_addresses="]
    socket = "db-socket:/var/run/postgresql"
    assert socket in db["volumes"] and socket in api["volumes"]
    users = [
        name for name, s in services.items() if socket in s.get("volumes", [])
    ]
    assert sorted(users) == ["api", "db"]
    assert (
        "host=/var/run/postgresql" in api["environment"]["CURATOR_DATABASE_URL"]
    )


def test_api_role_is_not_the_superuser_and_alone_on_the_socket() -> None:
    db = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"][
        "db"
    ]
    assert db["environment"]["POSTGRES_USER"] == "postgres"
    assert "./docker/db-init:/docker-entrypoint-initdb.d:ro" in db["volumes"]
    script = (ROOT / "docker" / "db-init" / "10-app-role.sh").read_text()
    assert "CREATE ROLE curator LOGIN NOSUPERUSER" in script
    assert "NOBYPASSRLS" in script
    hba = script.split("<<'HBA'\n", 1)[1].split("\nHBA", 1)[0]
    rules = [
        line.split() for line in hba.splitlines() if not line.startswith("#")
    ]
    assert rules == [
        ["local", "curator", "curator", "trust"],
        ["local", "all", "postgres", "peer"],
    ]
