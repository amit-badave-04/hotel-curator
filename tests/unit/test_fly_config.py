"""The three Fly configs must agree with the code they deploy (ADR 011)."""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from hotel_curator.api.app import create_app
from hotel_curator.cli.db import GRACEFUL_SHUTDOWN_SECONDS
from hotel_curator.config import Settings
from tests.helpers import load_script

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "fly.toml"
WEB = ROOT / "web" / "fly.toml"
WEB_CLOUDFLARE = ROOT / "web" / "fly.cloudflare.toml"
TLS_PORT = 8443
HEALTH_PORT = 8080
DB = ROOT / "deploy" / "postgres" / "fly.toml"
EXPOSE = re.compile(r"^EXPOSE (\d+)$", re.MULTILINE)
PINNED_FROM = re.compile(r"^FROM \S+@sha256:[0-9a-f]{64}", re.MULTILINE)


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _exposed(dockerfile: Path) -> int:
    match = EXPOSE.search(dockerfile.read_text(encoding="utf-8"))
    assert match is not None, f"no EXPOSE in {dockerfile}"
    return int(match.group(1))


@pytest.mark.parametrize("path", [API, WEB, WEB_CLOUDFLARE, DB])
def test_every_app_runs_in_lhr_on_one_small_machine(path: Path) -> None:
    config = _toml(path)
    assert config["primary_region"] == "lhr"
    assert [vm["size"] for vm in config["vm"]] == ["shared-cpu-1x"]


def test_api_is_private_warm_and_built_with_checked_weights() -> None:
    config = _toml(API)
    assert "http_service" not in config, "the API must not be public"
    (service,) = config["services"]
    assert service["internal_port"] == _exposed(ROOT / "Dockerfile")
    assert service["auto_stop_machines"] == "off"
    assert service["min_machines_running"] == 1
    assert config["build"]["args"]["REQUIRE_MODEL"] == "1"
    assert config["deploy"]["release_command"] == "curator db release"
    assert config["kill_signal"] == "SIGTERM"
    assert config["kill_timeout"] > GRACEFUL_SHUTDOWN_SECONDS


def test_api_health_checks_hit_real_routes() -> None:
    config = _toml(API)
    paths = {check["path"] for check in config["services"][0]["http_checks"]}
    paths |= {check["path"] for check in config["checks"].values()}
    assert paths == {"/healthz", "/readyz"}
    assert paths <= set(create_app(Settings()).openapi()["paths"])


def test_api_env_is_valid_production_settings_given_the_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no stray .env file
    env = _toml(API)["env"]
    fields = {f"CURATOR_{name.upper()}" for name in Settings.model_fields}
    assert set(env) <= fields, set(env) - fields
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    secrets = load_script("push_fly_secrets")
    generated = secrets.generate()
    for key in secrets.SECRETS[secrets.API_APP]:
        monkeypatch.setenv(key, generated[key])
    settings = Settings()
    assert settings.environment == "production"
    assert settings.migrate_on_start is False
    assert settings.log_format == "json"


def test_web_is_the_public_app_and_drains_on_sigquit() -> None:
    config = _toml(WEB)
    service = config["http_service"]
    assert service["internal_port"] == _exposed(ROOT / "web" / "Dockerfile")
    assert service["force_https"] is True
    assert service["auto_stop_machines"] == "off"
    assert service["min_machines_running"] == 1
    assert config["kill_signal"] == "SIGQUIT"
    assert config["env"]["CURATOR_EDGE"] == "fly"
    upstream = config["env"]["CURATOR_API_UPSTREAM"]
    assert upstream == f"{_toml(API)['app']}.flycast:8000"


def test_cloudflare_mode_passes_tls_through_and_checks_health_privately() -> (
    None
):
    fly, cloudflare = _toml(WEB), _toml(WEB_CLOUDFLARE)
    assert cloudflare["app"] == fly["app"]
    assert "http_service" not in cloudflare
    assert cloudflare["env"] == {**fly["env"], "CURATOR_EDGE": "cloudflare"}
    (service,) = cloudflare["services"]
    assert service["protocol"] == "tcp"
    assert service["internal_port"] == TLS_PORT
    assert service["ports"] == [{"port": 443, "handlers": ["proxy_proto"]}]
    assert service["auto_stop_machines"] == "off"
    (check,) = cloudflare["checks"].values()
    assert (check["port"], check["path"]) == (HEALTH_PORT, "/healthz")
    # The secrets and the server names never sit in the file.
    text = WEB_CLOUDFLARE.read_text()
    assert "CURATOR_SERVER_NAMES =" not in text and "B64 =" not in text


def test_nginx_templates_listen_where_the_fly_configs_point() -> None:
    nginx = ROOT / "web" / "nginx"
    fly = (nginx / "site-fly.conf.template").read_text()
    cloudflare = (nginx / "site-cloudflare.conf.template").read_text()
    assert f"listen {_toml(WEB)['http_service']['internal_port']};" in fly
    assert f"listen {TLS_PORT} ssl default_server proxy_protocol;" in cloudflare
    assert f"listen {HEALTH_PORT};" in cloudflare


def test_compose_and_fly_trust_x_real_ip_only_from_nginx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    web_ip = services["web"]["networks"]["frontend"]["ipv4_address"]
    api_env = services["api"]["environment"]
    assert api_env["CURATOR_FORWARDED_ALLOW_IPS"] == web_ip
    assert services["web"]["environment"]["CURATOR_EDGE"] == "local"
    assert _toml(API)["env"]["CURATOR_FORWARDED_ALLOW_IPS"] == "*"
    settings = Settings(forwarded_allow_ips="*")
    assert settings.forwarded_allow_ips == "*"


def test_database_is_private_and_keeps_its_data_on_the_volume() -> None:
    config = _toml(DB)
    assert "services" not in config and "http_service" not in config
    mount = config["mounts"]["destination"]
    assert config["env"]["PGDATA"].startswith(f"{mount}/")
    assert config["kill_signal"] == "SIGINT"
    secrets = load_script("push_fly_secrets")
    assert config["env"]["POSTGRES_DB"] == secrets.DB_NAME
    assert f"{config['app']}.internal" == secrets.DB_HOST


def test_deploy_script_points_at_these_configs_in_dependency_order() -> None:
    apps = load_script("deploy").APPS
    assert [app.key for app in apps] == ["db", "api", "web"]
    for app in apps:
        assert (ROOT / app.config).is_file()
        assert _toml(ROOT / app.config)["app"] == app.name
        dockerfile = _toml(ROOT / app.config)["build"]["dockerfile"]
        assert (ROOT / app.context / dockerfile).is_file()


def test_database_image_is_the_pinned_compose_image() -> None:
    dockerfile = (ROOT / "deploy" / "postgres" / "Dockerfile").read_text()
    assert PINNED_FROM.search(dockerfile)
    # A half-initialised volume must refuse the network, loopback included.
    assert "ENV POSTGRES_HOST_AUTH_METHOD=reject" in dockerfile
    assert '"--auth-local=peer --auth-host=scram-sha-256"' in dockerfile
    compose = (ROOT / "docker-compose.yml").read_text()
    image = re.search(r"^FROM (\S+)$", dockerfile, re.MULTILINE)
    assert image is not None
    assert f"image: {image.group(1)}" in compose


def test_roles_script_locks_pg_hba_before_anything_can_fail() -> None:
    script = (ROOT / "deploy" / "postgres" / "init" / "10-roles.sh").read_text()
    lines = script.splitlines()

    def first(fragment: str) -> int:
        """Index of the first non-comment line containing ``fragment``."""
        return next(
            i
            for i, line in enumerate(lines)
            if fragment in line and not line.lstrip().startswith("#")
        )

    lockout = first("printf 'local all postgres peer")
    hba = first('cat > "$PGDATA/pg_hba.conf"')
    assert lockout < first('case "$db" in') < hba
    assert hba < first(":?set it with") and hba < first("psql ")
