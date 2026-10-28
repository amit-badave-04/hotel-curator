"""deploy/postgres/init/10-roles.sh: the exact pg_hba.conf it leaves behind.

A test against a running server sees only the rules that match its own
source address and database; this reads the file itself, so any extra or
looser rule fails here.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "deploy" / "postgres" / "init" / "10-roles.sh"
EXPECTED = [
    ["local", "all", "postgres", "peer"],
    ["hostssl", "curator", "curator_owner", "all", "scram-sha-256"],
    ["hostssl", "curator", "curator_app", "all", "scram-sha-256"],
]
LOCKED = [["local", "all", "postgres", "peer"]]


def test_an_empty_pgdata_writes_nothing(tmp_path: Path) -> None:
    done = subprocess.run(
        ["sh", str(ROLES)],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "PGDATA": ""},
        capture_output=True,
        check=False,
    )
    assert done.returncode != 0
    assert not any(tmp_path.iterdir())


def _hba_after(tmp_path: Path, **env: str) -> tuple[int, list[list[str]]]:
    """Run the script up to its first failure; return the rules it wrote.

    Without the role passwords it stops right after writing pg_hba.conf,
    before it would need a server.
    """
    (tmp_path / "pg_hba.conf").write_text("host all all all trust\n")
    done = subprocess.run(
        ["sh", str(ROLES)],
        env={"PATH": os.environ["PATH"], "PGDATA": str(tmp_path), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    lines = (tmp_path / "pg_hba.conf").read_text().splitlines()
    rules = [line.split() for line in lines if not line.startswith("#")]
    return done.returncode, [rule for rule in rules if rule]


@pytest.mark.parametrize("env", [{"POSTGRES_DB": "curator"}, {}])
def test_pg_hba_admits_exactly_two_roles_over_tls(
    tmp_path: Path, env: dict[str, str]
) -> None:
    code, rules = _hba_after(tmp_path, **env)
    assert code != 0  # stopped at the missing passwords, as intended here
    assert rules == EXPECTED


@pytest.mark.parametrize(
    "name",
    [
        "all",
        "replication",
        "sameuser",
        "samerole",
        "samegroup",
        "a,b",
        "Curator",
        "x y",
        "1x",
        "x" * 64,
    ],
)
def test_a_bad_database_name_leaves_only_the_superuser_peer_line(
    tmp_path: Path, name: str
) -> None:
    code, rules = _hba_after(tmp_path, POSTGRES_DB=name)
    assert code != 0
    assert rules == LOCKED
