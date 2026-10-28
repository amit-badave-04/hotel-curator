"""scripts/push_fly_secrets.py and scripts/deploy.py, without flyctl."""

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from hotel_curator.config import Settings
from tests.helpers import load_script

secrets_cli = load_script("push_fly_secrets")
deploy_cli = load_script("deploy")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\noid sha256:ab\n"


def test_generated_secrets_are_consistent_and_valid_settings() -> None:
    values = secrets_cli.generate()
    assert secrets_cli.problems_in(values) == []
    settings = Settings(
        environment="production",
        database_url=values["CURATOR_DATABASE_URL"],
        migration_database_url=values["CURATOR_MIGRATION_DATABASE_URL"],
    )
    assert "@hotel-curator-db.internal:5432/curator" in settings.db_url()
    assert values != secrets_cli.generate()


def test_init_writes_a_private_file_once(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.fly"
    assert secrets_cli.main(["init", "--env-file", str(env_file)]) == 0
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    before = env_file.read_text()
    assert secrets_cli.main(["init", "--env-file", str(env_file)]) == 1
    assert env_file.read_text() == before
    assert secrets_cli.main(["check", "--env-file", str(env_file)]) == 0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("KEY", "expected KEY=VALUE"),
        ("lower=x", "expected KEY=VALUE"),
        ("A=1\nA=2", "A given twice"),
        ("A=", "A is empty"),
        ("A=has space", "A is empty or has a space"),
        ("A=caf\u00e9", "outside printable ASCII"),
        ("A=user\uff1apw\uff20host", "outside printable ASCII"),
        ("A=x\x0bLEAK=y", "outside printable ASCII"),
        ("A=pass#word", "A has # or a quote"),
        ('A="quoted"', "A has # or a quote"),
        ("A='quoted'", "A has # or a quote"),
    ],
)
def test_parse_env_rejects_malformed_lines(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        secrets_cli.parse_env(f"# comment\n\n{text}\n")


def _with(**changes: str) -> dict[str, str]:
    values: dict[str, str] = secrets_cli.generate()
    values.update(changes)
    return values


def test_problems_name_missing_and_unknown_keys() -> None:
    values = secrets_cli.generate()
    del values["CURATOR_APP_PASSWORD"]
    values["CURATOR_EXTRA"] = "x"
    assert secrets_cli.problems_in(values) == [
        "CURATOR_EXTRA: not a known secret",
        "CURATOR_APP_PASSWORD: missing",
    ]


def test_problems_catch_urls_that_would_not_connect() -> None:
    values = secrets_cli.generate()
    url = values["CURATOR_DATABASE_URL"]
    broken = _with(
        CURATOR_DATABASE_URL=url.replace("sslmode=require", "sslmode=prefer")
        .replace("curator_app:", "curator_owner:")
        .replace(".internal:5432", ".internal:5433"),
        CURATOR_MIGRATION_DATABASE_URL=values[
            "CURATOR_MIGRATION_DATABASE_URL"
        ].replace("@", "x@"),
    )
    problems = " | ".join(secrets_cli.problems_in(broken))
    assert "CURATOR_DATABASE_URL: port must be 5432" in problems
    assert "CURATOR_DATABASE_URL: user must be 'curator_app'" in problems
    assert "CURATOR_DATABASE_URL: sslmode must be one of" in problems
    assert "CURATOR_DATABASE_URL: password differs" in problems
    assert "CURATOR_MIGRATION_DATABASE_URL: password differs" in problems


def test_problems_catch_weak_or_reused_passwords() -> None:
    values = _with(POSTGRES_PASSWORD="short")
    assert secrets_cli.problems_in(values) == [
        "POSTGRES_PASSWORD: needs 24 or more of A-Z, a-z, 0-9, _ and -"
    ]
    slashed = _with(POSTGRES_PASSWORD="a/b?c" * 6)
    assert secrets_cli.problems_in(slashed) == [
        "POSTGRES_PASSWORD: needs 24 or more of A-Z, a-z, 0-9, _ and -"
    ]
    same = _with(POSTGRES_PASSWORD=secrets_cli.generate()["POSTGRES_PASSWORD"])
    same["CURATOR_OWNER_PASSWORD"] = same["POSTGRES_PASSWORD"]
    assert "the three database passwords must differ" in (
        secrets_cli.problems_in(same)
    )


def test_push_sends_values_on_stdin_and_never_prints_them(
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = secrets_cli.generate()
    calls: list[tuple[list[str], str]] = []
    secrets_cli.push(
        values,
        ["hotel-curator-db", "hotel-curator-api"],
        flyctl="flyctl",
        dry_run=False,
        runner=lambda command, stdin: calls.append((command, stdin)),
    )
    out = capsys.readouterr().out
    assert all(value not in out for value in values.values())
    assert [command for command, _ in calls] == [
        ["flyctl", "secrets", "import", "--app", app, "--stage"]
        for app in ("hotel-curator-db", "hotel-curator-api")
    ]
    db_stdin = calls[0][1]
    assert db_stdin.splitlines() == [
        f"{key}={values[key]}"
        for key in secrets_cli.SECRETS["hotel-curator-db"]
    ]
    assert all(value not in " ".join(calls[0][0]) for value in values.values())


def test_push_refuses_an_inconsistent_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    env_file.write_text("POSTGRES_PASSWORD=x\n")
    code = secrets_cli.main(["push", "--env-file", str(env_file)])
    assert code == 1
    assert "error: CURATOR_DATABASE_URL: missing" in capsys.readouterr().err


def test_push_dry_run_lists_names_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    secrets_cli.write_env_file(env_file, secrets_cli.generate())
    args = ["push", "--dry-run", "--env-file", str(env_file)]
    assert secrets_cli.main([*args, "--app", "hotel-curator-api"]) == 0
    out = capsys.readouterr().out
    assert "--app hotel-curator-api --stage" in out
    assert "CURATOR_DATABASE_URL" in out
    assert "hotel-curator-db --stage" not in out
    assert "postgresql+psycopg" not in out


class FakeFly:
    """flyctl stand-in: canned JSON listings, recorded commands."""

    def __init__(self, listings: dict[str, list[dict[str, Any]]]) -> None:
        self.listings = listings
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> str:
        if command[-1] == "--json":
            key = " ".join(command[1:-1])
            return json.dumps(self.listings.get(key, []))
        self.commands.append(command[1:])
        return ""


def test_setup_creates_only_what_is_missing() -> None:
    fake = FakeFly(
        {
            "apps list": [
                {"Name": "hotel-curator-db"},
                {"Name": "hotel-curator"},
            ],
            "volumes list --app hotel-curator-db": [{"name": "pg_data"}],
        }
    )
    deploy_cli.setup(deploy_cli.Fly("flyctl", dry_run=False, runner=fake), "o")
    assert fake.commands == [
        ["apps", "create", "hotel-curator-api", "--org", "o"],
        ["ips", "allocate-v6", "--private", "--app", "hotel-curator-api"],
    ]


def test_setup_twice_is_a_no_op(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeFly(
        {
            "apps list": [{"Name": app.name} for app in deploy_cli.APPS],
            "volumes list --app hotel-curator-db": [{"Name": "pg_data"}],
            "ips list --app hotel-curator-api": [{"Type": "private_v6"}],
        }
    )
    deploy_cli.setup(deploy_cli.Fly("flyctl", dry_run=False, runner=fake), "o")
    assert fake.commands == []
    assert capsys.readouterr().out.count("exists") == 5


def test_dry_run_prints_but_runs_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeFly({})
    fly = deploy_cli.Fly("flyctl", dry_run=True, runner=fake)
    deploy_cli.deploy(fly, deploy_cli.APP_BY_KEY["db"])
    assert fake.commands == []
    root = deploy_cli.ROOT
    assert capsys.readouterr().out.startswith(
        f"$ flyctl deploy {root / 'deploy/postgres'} --config "
        f"{root / 'deploy/postgres/fly.toml'} "
    )


def test_plan_deploys_private_apps_without_public_addresses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert deploy_cli.main(["plan"]) == 0
    lines = capsys.readouterr().out.splitlines()
    deploys = [line for line in lines if line.startswith("$ flyctl deploy")]
    assert [line.split("--app ")[1].split()[0] for line in deploys] == [
        "hotel-curator-db",
        "hotel-curator-api",
        "hotel-curator",
    ]
    assert all("--ha=false" in line for line in deploys)
    assert ["--no-public-ips" in line for line in deploys] == [
        True,
        True,
        False,
    ]
    # flyctl changes into the context before reading --config, so both must
    # be absolute, and the config must exist where flyctl will look.
    for line in deploys:
        words = line.split()
        context, config = (
            Path(words[3]),
            Path(words[words.index("--config") + 1]),
        )
        assert context.is_absolute() and context.is_dir()
        assert config.is_absolute() and config.is_file()


@pytest.mark.parametrize(
    "answer", ['{"error": "x"}', '["a"]', "null", "", "Error: not logged in"]
)
def test_a_listing_that_is_not_a_list_of_objects_stops(answer: str) -> None:
    fly = deploy_cli.Fly("flyctl", dry_run=False, runner=lambda _c: answer)
    with pytest.raises(SystemExit, match="unexpected answer from flyctl"):
        deploy_cli.status(fly)


def test_status_fails_when_a_private_app_has_a_public_address(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeFly(
        {
            "ips list --app hotel-curator-api": [
                {"Type": "private_v6", "Address": "fdaa::3"},
                {"Type": "v4", "Address": "203.0.113.9"},
            ]
        }
    )
    fly = deploy_cli.Fly("flyctl", dry_run=False, runner=fake)
    assert deploy_cli.status(fly) == 1
    assert "hotel-curator-api has a public address 203.0.113.9" in (
        capsys.readouterr().err
    )
    clean = FakeFly({})
    ok = deploy_cli.Fly("flyctl", dry_run=False, runner=clean)
    assert deploy_cli.status(ok) == 0


def test_weights_check_finds_pointers_and_missing_models(
    tmp_path: Path,
) -> None:
    assert deploy_cli.lfs_pointers(tmp_path) == [
        tmp_path / "<winner>" / "model.onnx"
    ]
    winner = tmp_path / "winner"
    winner.mkdir()
    (winner / "model.onnx").write_bytes(LFS_POINTER)
    (tmp_path / "v1-roberta-base").mkdir()
    (tmp_path / "v1-roberta-base" / "model.onnx").write_bytes(LFS_POINTER)
    assert deploy_cli.lfs_pointers(tmp_path) == [winner / "model.onnx"]
    with pytest.raises(SystemExit, match="run git lfs pull"):
        deploy_cli.require_weights(tmp_path)
    (winner / "model.onnx").write_bytes(b"\x08\x07onnx weights")
    assert deploy_cli.lfs_pointers(tmp_path) == []
    deploy_cli.require_weights(tmp_path)


def test_app_names_only_follow_deploy() -> None:
    with pytest.raises(SystemExit):
        deploy_cli.main(["status", "api"])
    with pytest.raises(SystemExit):
        deploy_cli.main(["deploy", "cache"])


@pytest.mark.parametrize(
    "netloc",
    [
        "curator_app:pw/SECRETfrag@hotel-curator-db.internal:5432",
        "curator_app:pw@[SECRETfrag]:5432",
        "curator_app:pw@[SECRETfrag",
    ],
)
def test_a_mangled_url_is_reported_without_its_contents(netloc: str) -> None:
    url = f"postgresql+psycopg://{netloc}/curator?sslmode=require"
    problems = secrets_cli.problems_in(_with(CURATOR_DATABASE_URL=url))
    assert "CURATOR_DATABASE_URL: not a valid URL" in problems
    assert all("SECRETfrag" not in problem for problem in problems)


@pytest.mark.parametrize(
    "separator", ["\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028"]
)
def test_no_line_separator_can_turn_a_secret_into_a_key(
    tmp_path: Path, separator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    values = secrets_cli.generate()
    values["POSTGRES_PASSWORD"] += f"{separator}LEAKED_PART=x"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    assert secrets_cli.main(["check", "--env-file", str(env_file)]) == 1
    out = capsys.readouterr()
    assert "line 1: POSTGRES_PASSWORD" in out.err
    assert "LEAKED" not in out.err + out.out


def test_crlf_files_are_read_like_lf_files(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.fly"
    values = secrets_cli.generate()
    env_file.write_bytes(
        "".join(f"{k}={v}\r\n" for k, v in values.items()).encode()
    )
    assert secrets_cli.parse_env(secrets_cli.read_env_file(env_file)) == values


def test_a_file_that_is_not_utf8_names_the_line_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    env_file.write_bytes(b"# comment\nPOSTGRES_PASSWORD=caf\xe9-secret\n")
    assert secrets_cli.main(["check", "--env-file", str(env_file)]) == 1
    err = capsys.readouterr().err
    assert "line 2: not UTF-8" in err
    assert "secret" not in err and "0xe9" not in err
