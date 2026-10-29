"""scripts/ops.py (park, resume, status) against a fake flyctl."""

import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.helpers import SCRIPTS, load_script

DB_MACHINE = "148e2d3c1a2b38"
WEB_ENV = {
    "CURATOR_EDGE": "fly",
    "CURATOR_PUBLIC_HOST": "hotel-curator.fly.dev",
}


@pytest.fixture(scope="module")
def ops() -> Iterator[ModuleType]:
    """scripts/ops.py, which imports its sibling deploy.py."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(SCRIPTS))
        yield load_script("ops")


class FakeFly:
    """flyctl stand-in: canned JSON listings, recorded commands."""

    def __init__(self, listings: dict[str, list[dict[str, Any]]]) -> None:
        self.listings = listings
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> str:
        if command[-1] == "--json":
            return json.dumps(self.listings.get(" ".join(command[1:-1]), []))
        self.commands.append(command[1:])
        return ""


def _db(state: str) -> dict[str, list[dict[str, Any]]]:
    machine = {"id": DB_MACHINE, "state": state}
    return {"machine list --app hotel-curator-db": [machine]}


def _fly(ops: ModuleType, fake: FakeFly, dry_run: bool = False) -> Any:
    return ops.Fly("flyctl", dry_run=dry_run, runner=fake)


def test_stop_scales_web_then_api_to_zero_then_stops_the_database(
    ops: ModuleType,
) -> None:
    fake = FakeFly(_db("started"))
    ops.stop(_fly(ops, fake))
    assert fake.commands == [
        ["scale", "count", "0", "--app", "hotel-curator", "--yes"],
        ["scale", "count", "0", "--app", "hotel-curator-api", "--yes"],
        ["machine", "stop", DB_MACHINE, "--app", "hotel-curator-db"],
    ]


def test_stop_leaves_a_stopped_database_alone(ops: ModuleType) -> None:
    fake = FakeFly(_db("stopped"))
    ops.stop(_fly(ops, fake))
    assert not any(c[:2] == ["machine", "stop"] for c in fake.commands)


def test_start_brings_back_database_api_web_and_checks_the_edge(
    ops: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    web = [{"id": "e784079b449483", "config": {"env": WEB_ENV}}]
    fake = FakeFly({**_db("stopped"), "machine list --app hotel-curator": web})
    ops.start(_fly(ops, fake))
    assert fake.commands == [
        ["machine", "start", DB_MACHINE, "--app", "hotel-curator-db"],
        ["scale", "count", "1", "--app", "hotel-curator-api",
         "--region", "lhr", "--yes"],
        ["scale", "count", "1", "--app", "hotel-curator",
         "--region", "lhr", "--yes"],
    ]  # fmt: skip
    out = capsys.readouterr().out
    assert "# hotel-curator restored: --edge fly\n" in out


def _web(env: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    machine = {"id": "e784079b449483", "config": {"env": env}}
    return {"machine list --app hotel-curator": [machine]}


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "no CURATOR_EDGE"),
        ({"CURATOR_EDGE": "fly"}, "has no CURATOR_PUBLIC_HOST"),
        ({"CURATOR_EDGE": "cloudflare"}, "has no CURATOR_SERVER_NAMES"),
        ({"CURATOR_EDGE": "other", "X": "y"}, "has no mode"),
    ],
)
def test_start_fails_when_the_web_machine_lost_its_deploy_settings(
    ops: ModuleType, env: dict[str, str], message: str
) -> None:
    fake = FakeFly({**_db("started"), **_web(env)})
    with pytest.raises(SystemExit, match=message):
        ops.start(_fly(ops, fake))


@pytest.mark.parametrize(
    ("env", "flags"),
    [
        (WEB_ENV, "--edge fly"),
        (
            {"CURATOR_EDGE": "fly", "CURATOR_PUBLIC_HOST": "example.org"},
            "--edge fly --domain example.org",
        ),
        (
            {
                "CURATOR_EDGE": "cloudflare",
                "CURATOR_SERVER_NAMES": "example.org www.example.org",
            },
            "--edge cloudflare --domain example.org",
        ),
    ],
)
def test_stop_first_prints_how_to_deploy_the_web_app_while_parked(
    ops: ModuleType,
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    flags: str,
) -> None:
    ops.stop(_fly(ops, FakeFly({**_db("started"), **_web(env)})))
    first = capsys.readouterr().out.splitlines()[0]
    assert first == (
        f"# while parked, deploy the web app with: "
        f"scripts/deploy.py deploy web {flags}"
    )


def test_a_dry_run_reads_listings_but_runs_nothing(
    ops: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeFly(_db("started"))
    ops.stop(_fly(ops, fake, dry_run=True))
    ops.start(_fly(ops, FakeFly({}), dry_run=True))
    assert fake.commands == []
    out = capsys.readouterr().out
    assert f"$ flyctl machine stop {DB_MACHINE}" in out
    assert "$ flyctl scale count 1 --app hotel-curator --region lhr" in out


@pytest.mark.parametrize(
    ("listing", "message"),
    [
        ([], "has no machines"),
        ([{"id": "x; rm -rf /", "state": "started"}], "unexpected machine id"),
        ([{"state": "started"}], "unexpected machine id"),
    ],
)
def test_odd_database_listings_stop_the_script(
    ops: ModuleType, listing: list[dict[str, Any]], message: str
) -> None:
    fake = FakeFly({"machine list --app hotel-curator-db": listing})
    with pytest.raises(SystemExit, match=message):
        ops.stop(_fly(ops, fake))
    assert fake.commands == []  # refused before anything changed


def _fake_flyctl(tmp_path: Path) -> Path:
    """A flyctl that logs where it ran and answers listings with []."""
    script = tmp_path / "flyctl"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$(pwd) $*" >> {tmp_path / "calls"}\n'
        'for a in "$@"; do last=$a; done\n'
        'if [ "$last" = "--json" ]; then echo "[]"; '
        'else echo "table for $*"; fi\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_status_runs_flyctl_outside_any_fly_toml_and_shows_its_output(
    ops: ModuleType,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_flyctl(tmp_path)
    monkeypatch.chdir(tmp_path)
    # Relative to the repository, as FLYCTL=scripts/flyctl_docker.sh is.
    relative = os.path.relpath(fake, ops.ROOT)
    assert ops.main(["status", "--flyctl", relative]) == 0
    assert not (ops.FLYCTL_DIR / "fly.toml").exists()
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls and all(c.startswith(f"{ops.FLYCTL_DIR} ") for c in calls)
    out = capfd.readouterr().out
    assert "table for status --app hotel-curator-api" in out
