"""scripts/push_fly_secrets.py and scripts/deploy.py, without flyctl."""

import json
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hotel_curator.config import Settings
from tests.helpers import load_script

secrets_cli = load_script("push_fly_secrets")
deploy_cli = load_script("deploy")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\noid sha256:ab\n"
LEAK_WINDOW = 16


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


def _pem(directory: Path, name: str, label: str, eol: str = "\n") -> Path:
    path = directory / name
    body = f"MIIBfake{name.replace('.', '')}"
    lines = [f"-----BEGIN {label}-----", body, f"-----END {label}-----", ""]
    path.write_bytes(eol.join(lines).encode())
    return path


def _self_signed(directory: Path, name: str) -> tuple[Path, Path]:
    """A real EC certificate and key, as OpenSSL (and nginx) load them."""
    cert, key = directory / f"{name}.pem", directory / f"{name}.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
            "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1",
            "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(cert),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return cert, key


def _origin_files(directory: Path, eol: str = "\n") -> list[str]:
    """--cert, --key and --ca for a matching origin pair and a pull CA."""
    folder = directory / f"pki-{len(list(directory.glob('pki-*')))}"
    folder.mkdir()
    cert, key = _self_signed(folder, "origin")
    ca, _ = _self_signed(folder, "pull-ca")
    if eol != "\n":
        for path in (cert, key, ca):
            path.write_bytes(path.read_bytes().replace(b"\n", eol.encode()))
    return ["--cert", str(cert), "--key", str(key), "--ca", str(ca)]


def _assert_nothing_secret_in(
    out: pytest.CaptureFixture[str], env_file: Path, *files: Path
) -> None:
    """No value from .env.fly and no PEM content in what was printed."""
    printed = "".join(out.readouterr())
    values = secrets_cli.parse_env(secrets_cli.read_env_file(env_file))
    bodies = [
        line
        for f in files
        for line in f.read_text().splitlines()
        if line and not line.startswith("-----")
    ]
    # A URL's secret part is its password, checked on its own; the rest
    # (host, app names) is printed legitimately.
    plain = [v for k, v in values.items() if not k.endswith("_URL")]
    secrets = [*plain, *bodies]
    # Any 16-character piece counts: a leak need not print a whole value.
    pieces = {
        secret[i : i + LEAK_WINDOW]
        for secret in secrets
        for i in range(max(1, len(secret) - LEAK_WINDOW + 1))
    }
    assert not [piece for piece in pieces if piece in printed]


def test_origin_tls_adds_the_web_secrets_and_push_sends_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    assert secrets_cli.main(["init", "--env-file", str(env_file)]) == 0
    capsys.readouterr()
    files = _origin_files(tmp_path)
    paths = [Path(p) for p in files[1::2]]
    args = ["--env-file", str(env_file), *files]
    assert secrets_cli.main(["origin-tls", *args]) == 0
    _assert_nothing_secret_in(capsys, env_file, *paths)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert secrets_cli.main(["origin-tls", *args]) == 1
    assert "already in .env.fly" in capsys.readouterr().err
    values = secrets_cli.parse_env(secrets_cli.read_env_file(env_file))
    calls: list[tuple[list[str], str]] = []
    secrets_cli.push(
        values,
        [secrets_cli.WEB_APP],
        flyctl="flyctl",
        dry_run=False,
        runner=lambda command, stdin: calls.append((command, stdin)),
    )
    _assert_nothing_secret_in(capsys, env_file, *paths)
    for action in (["check"], ["push", "--dry-run"]):
        assert secrets_cli.main([*action, "--env-file", str(env_file)]) == 0
        _assert_nothing_secret_in(capsys, env_file, *paths)
    ((command, stdin),) = calls
    assert command[command.index("--app") + 1] == "hotel-curator"
    assert [line.split("=", 1)[0] for line in stdin.splitlines()] == list(
        secrets_cli.ORIGIN_SECRETS
    )
    assert all(v in stdin for k, v in values.items() if "ORIGIN" in k)


def test_origin_tls_handles_hand_edited_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    secrets_cli.write_env_file(env_file, secrets_cli.generate())
    env_file.write_text(env_file.read_text().rstrip("\n"))  # no final \n
    crlf = _origin_files(tmp_path, eol="\r\n")
    origin = ["origin-tls", "--env-file", str(env_file), *crlf]
    assert secrets_cli.main(origin) == 0, capsys.readouterr().err
    assert secrets_cli.main(["check", "--env-file", str(env_file)]) == 0
    assert list(tmp_path.glob(".*.new")) == []
    shared = tmp_path / "shared.env"
    secrets_cli.write_env_file(shared, secrets_cli.generate())
    shared.chmod(0o644)
    origin = ["origin-tls", "--env-file", str(shared), *_origin_files(tmp_path)]
    assert secrets_cli.main(origin) == 1
    assert "readable by others" in capsys.readouterr().err
    assert "ORIGIN" not in shared.read_text()


def test_push_includes_the_web_app_only_with_its_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    secrets_cli.write_env_file(env_file, secrets_cli.generate())
    dry = ["push", "--dry-run", "--env-file", str(env_file)]
    assert secrets_cli.main(dry) == 0
    assert "--app hotel-curator " not in capsys.readouterr().out
    assert secrets_cli.main([*dry, "--app", "hotel-curator"]) == 1
    assert "no origin secrets" in capsys.readouterr().err
    origin = ["origin-tls", "--env-file", str(env_file)]
    assert secrets_cli.main([*origin, *_origin_files(tmp_path)]) == 0
    assert secrets_cli.main(dry) == 0
    assert "--app hotel-curator --stage" in capsys.readouterr().out


def test_origin_tls_refuses_swapped_or_partial_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    secrets_cli.write_env_file(env_file, secrets_cli.generate())
    before = env_file.read_text()
    files = _origin_files(tmp_path)
    files[1], files[3] = files[3], files[1]  # key given as the certificate
    assert (
        secrets_cli.main(["origin-tls", "--env-file", str(env_file), *files])
        == 1
    )
    err = capsys.readouterr().err
    assert "CURATOR_ORIGIN_CERT_B64: not a PEM certificate" in err
    assert "BEGIN" not in err
    assert env_file.read_text() == before
    other = _origin_files(tmp_path)
    mismatched = [*_origin_files(tmp_path)]
    mismatched[3] = other[3]  # a key from another certificate
    swapped = [*_origin_files(tmp_path)]
    swapped[1], swapped[5] = swapped[5], swapped[1]  # cert and CA swapped
    for files in (mismatched, swapped):
        origin = ["origin-tls", "--env-file", str(env_file), *files]
        assert secrets_cli.main(origin) == 1
        assert "does not belong to the certificate" in capsys.readouterr().err
    assert env_file.read_text() == before
    same = _origin_files(tmp_path)
    same[5] = same[1]  # the certificate given again as the CA
    origin = ["origin-tls", "--env-file", str(env_file), *same]
    assert secrets_cli.main(origin) == 1
    assert "are the same file" in capsys.readouterr().err
    partial = _with(CURATOR_ORIGIN_CERT_B64="bm90IGEgcGVt")
    assert secrets_cli.problems_in(partial) == [
        "the three origin secrets go together; missing "
        "CURATOR_ORIGIN_KEY_B64, CURATOR_ORIGIN_PULL_CA_B64"
    ]


def test_cloudflare_mode_deploys_the_cloudflare_config_for_one_domain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = ["deploy", "web", "--edge", "cloudflare", "--domain", "a.example"]
    assert deploy_cli.main([*args, "--dry-run"]) == 0
    line = capsys.readouterr().out.strip()
    root = deploy_cli.ROOT
    assert f"--config {root / 'web/fly.cloudflare.toml'} " in line
    assert " --env 'CURATOR_SERVER_NAMES=a.example www.a.example' " in line
    assert line.endswith("--build-arg VITE_PUBLIC_ORIGIN=https://a.example")
    for bad in (["--domain", "A.example"], ["--domain", "a.example; rm"]):
        with pytest.raises(SystemExit):
            deploy_cli.main([*args[:4], *bad, "--dry-run"])
    for bad_args in (
        ["deploy", "api", "--edge", "cloudflare", "--domain", "a.example"],
        ["deploy", "web", "--edge", "cloudflare"],
        ["deploy", "web", "--domain", "a.example"],
        ["status", "--edge", "fly"],
        ["setup", "--edge", "cloudflare", "--domain", "a.example"],
        ["deploy", "web", "--edge", "cloudflare", "--domain", "a" * 98 + ".io"],
    ):
        with pytest.raises(SystemExit):
            deploy_cli.main(bad_args)


CLOUDFLARE_MACHINES = {
    "machine list --app hotel-curator": [
        {"id": "m1", "config": {"env": {"CURATOR_EDGE": "cloudflare"}}}
    ]
}


def _deployed_configs(fake: FakeFly) -> list[str]:
    return [
        cmd[cmd.index("--config") + 1]
        for cmd in fake.commands
        if cmd[0] == "deploy"
    ]


def test_a_rollback_to_fly_mode_keeps_the_domain_in_the_build(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = ["deploy", "web", "--edge", "fly", "--domain", "a.example"]
    assert deploy_cli.main([*args, "--dry-run"]) == 0
    line = capsys.readouterr().out.strip()
    assert f"--config {deploy_cli.ROOT / 'web/fly.toml'} " in line
    assert "CURATOR_SERVER_NAMES" not in line
    assert " --env CURATOR_PUBLIC_HOST=a.example " in line
    assert line.endswith("--build-arg VITE_PUBLIC_ORIGIN=https://a.example")


def test_a_redeploy_after_a_rollback_keeps_the_domain_unless_told() -> None:
    fake = FakeFly(
        {
            "machine list --app hotel-curator": [
                {
                    "config": {
                        "env": {
                            "CURATOR_EDGE": "fly",
                            "CURATOR_PUBLIC_HOST": "a.example",
                        }
                    }
                }
            ]
        }
    )
    fly = deploy_cli.Fly("flyctl", dry_run=False, runner=fake)
    with pytest.raises(SystemExit, match=r"published at a\.example"):
        deploy_cli.deploy_apps(fly, ["web"], None, None)
    assert fake.commands == []
    deploy_cli.deploy_apps(fly, ["web"], "fly", "a.example")
    deploy_cli.deploy_apps(fly, ["web"], "fly", None)  # back to fly.dev
    # Machines that record their own fly.dev name deploy without flags.
    fake.listings["machine list --app hotel-curator"] = [
        {
            "config": {
                "env": {
                    "CURATOR_EDGE": "fly",
                    "CURATOR_PUBLIC_HOST": "hotel-curator.fly.dev",
                }
            }
        }
    ]
    deploy_cli.deploy_apps(fly, ["web"], None, None)
    origins = [c[c.index("--build-arg") + 1] for c in fake.commands]
    assert origins == [
        "VITE_PUBLIC_ORIGIN=https://a.example",
        "VITE_PUBLIC_ORIGIN=https://hotel-curator.fly.dev",
        "VITE_PUBLIC_ORIGIN=https://hotel-curator.fly.dev",
    ]


def test_a_redeploy_keeps_cloudflare_mode_unless_told_otherwise() -> None:
    fake = FakeFly(CLOUDFLARE_MACHINES)
    fly = deploy_cli.Fly("flyctl", dry_run=False, runner=fake)
    with pytest.raises(SystemExit, match="runs in Cloudflare mode"):
        deploy_cli.deploy_apps(fly, ["web"], None, None)
    assert fake.commands == []
    deploy_cli.deploy_apps(fly, ["web"], "cloudflare", "a.example")
    deploy_cli.deploy_apps(fly, ["web"], "fly", None)  # an explicit switch
    assert _deployed_configs(fake) == [
        str(deploy_cli.ROOT / "web/fly.cloudflare.toml"),
        str(deploy_cli.ROOT / "web/fly.toml"),
    ]


def test_fly_mode_and_new_apps_deploy_without_flags() -> None:
    for machines in (
        {"machine list --app hotel-curator": []},
        {
            "machine list --app hotel-curator": [
                {"config": {"env": {"CURATOR_EDGE": "fly"}}}
            ]
        },
    ):
        fake = FakeFly(machines)
        fly = deploy_cli.Fly("flyctl", dry_run=False, runner=fake)
        deploy_cli.deploy_apps(fly, ["web"], None, None)
        assert _deployed_configs(fake) == [
            str(deploy_cli.ROOT / "web/fly.toml")
        ]
        command = fake.commands[-1]
        build_arg = command[command.index("--build-arg") + 1]
        assert build_arg == "VITE_PUBLIC_ORIGIN=https://hotel-curator.fly.dev"
        env = command[command.index("--env") + 1]
        assert env == "CURATOR_PUBLIC_HOST=hotel-curator.fly.dev"


def test_a_refusal_comes_before_any_deploy(tmp_path: Path) -> None:
    fake = FakeFly(CLOUDFLARE_MACHINES)
    fly = deploy_cli.Fly("flyctl", dry_run=False, runner=fake)
    with pytest.raises(SystemExit, match="runs in Cloudflare mode"):
        deploy_cli.deploy_apps(fly, ["db", "web"], None, None)
    assert fake.commands == []
    with pytest.raises(SystemExit):
        deploy_cli.main(
            ["deploy", "--edge", "cloudflare", "--domain", "Example.com"]
        )


@pytest.mark.parametrize(
    "machine",
    [{}, {"config": None}, {"config": {"env": []}}, {"config": {"env": {}}}],
)
def test_a_machine_listing_of_another_shape_stops(
    machine: dict[str, object],
) -> None:
    fake = FakeFly({"machine list --app hotel-curator": [machine]})
    fly = deploy_cli.Fly("flyctl", dry_run=False, runner=fake)
    with pytest.raises(SystemExit, match="unexpected answer"):
        deploy_cli.deploy_apps(fly, ["web"], None, None)


def test_a_failing_flyctl_listing_stops_without_a_traceback() -> None:
    def failing(command: list[str]) -> str:
        raise subprocess.CalledProcessError(1, command)

    fly = deploy_cli.Fly("flyctl", dry_run=False, runner=failing)
    with pytest.raises(SystemExit, match="machine list --app hotel-curator"):
        deploy_cli.web_now(fly)


def test_machines_that_disagree_stop_the_deploy() -> None:
    fake = FakeFly(
        {
            "machine list --app hotel-curator": [
                {"config": {"env": {"CURATOR_EDGE": "fly"}}},
                {"config": {"env": {"CURATOR_EDGE": "cloudflare"}}},
            ]
        }
    )
    fly = deploy_cli.Fly("flyctl", dry_run=False, runner=fake)
    with pytest.raises(SystemExit, match="disagree"):
        deploy_cli.deploy_apps(fly, ["web"], None, None)


def test_origin_tls_refuses_links_strays_and_encrypted_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    secrets_cli.write_env_file(env_file, secrets_cli.generate())
    files = _origin_files(tmp_path)
    link = tmp_path / "link.env"
    link.symlink_to(env_file)
    hard = tmp_path / "hard.env"
    hard.hardlink_to(env_file)
    for path in (link, hard):
        origin = ["origin-tls", "--env-file", str(path), *files]
        assert secrets_cli.main(origin) == 1
        assert "is a link" in capsys.readouterr().err
    hard.unlink()  # the hard link also made .env.fly itself a link
    stray = tmp_path / "..env.fly.new"  # what a crashed run leaves
    stray.write_text("")
    origin = ["origin-tls", "--env-file", str(env_file), *files]
    assert secrets_cli.main(origin) == 1
    assert "in progress" in capsys.readouterr().err
    assert stray.exists()
    stray.unlink()
    encrypted = _pem(tmp_path, "enc.key", "ENCRYPTED PRIVATE KEY")
    openssh = _pem(tmp_path, "ssh.key", "OPENSSH PRIVATE KEY")
    origin = ["origin-tls", "--env-file", str(env_file)]
    for key, message in ((encrypted, "encrypted"), (openssh, "not a PEM")):
        files[3] = str(key)
        assert secrets_cli.main([*origin, *files]) == 1
        assert message in capsys.readouterr().err
    files = _origin_files(tmp_path)
    folder = tmp_path / "folder.env"
    folder.mkdir()
    folder_args = ["origin-tls", "--env-file", str(folder), *files]
    assert secrets_cli.main(folder_args) == 1
    assert "not a regular file" in capsys.readouterr().err
    assert "ORIGIN" not in env_file.read_text()


def test_the_ca_may_not_be_the_certificate_in_other_line_endings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    secrets_cli.write_env_file(env_file, secrets_cli.generate())
    cert = _pem(tmp_path, "c.pem", "CERTIFICATE")
    ca = tmp_path / "ca.pem"
    ca.write_bytes(cert.read_bytes().replace(b"\n", b"\r\n"))
    key = _pem(tmp_path, "k.pem", "PRIVATE KEY")
    origin = ["origin-tls", "--env-file", str(env_file)]
    files = ["--cert", str(cert), "--key", str(key), "--ca", str(ca)]
    assert secrets_cli.main([*origin, *files]) == 1
    assert "are the same file" in capsys.readouterr().err


def test_cloudflare_setup_adds_a_dedicated_ipv4_once() -> None:
    shared = FakeFly({"ips list --app hotel-curator": [{"Type": "shared_v4"}]})
    deploy_cli.setup_cloudflare(
        deploy_cli.Fly("flyctl", dry_run=False, runner=shared)
    )
    assert shared.commands == [
        ["ips", "allocate-v4", "--app", "hotel-curator", "--yes"]
    ]
    dedicated = FakeFly({"ips list --app hotel-curator": [{"Type": "v4"}]})
    deploy_cli.setup_cloudflare(
        deploy_cli.Fly("flyctl", dry_run=False, runner=dedicated)
    )
    assert dedicated.commands == []


def test_origin_tls_replace_swaps_the_secrets_in_place(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / ".env.fly"
    secrets_cli.write_env_file(env_file, secrets_cli.generate())
    origin = ["origin-tls", "--env-file", str(env_file)]
    assert secrets_cli.main([*origin, *_origin_files(tmp_path)]) == 0
    first = secrets_cli.parse_env(secrets_cli.read_env_file(env_file))
    again = [*origin, *_origin_files(tmp_path)]
    assert secrets_cli.main(again) == 1
    assert "--replace swaps them" in capsys.readouterr().err
    assert secrets_cli.main([*again, "--replace"]) == 0
    second = secrets_cli.parse_env(secrets_cli.read_env_file(env_file))
    assert len(second) == len(first)
    changed = {k for k in first if first[k] != second[k]}
    assert changed == set(secrets_cli.ORIGIN_SECRETS)
    assert secrets_cli.main(["check", "--env-file", str(env_file)]) == 0
