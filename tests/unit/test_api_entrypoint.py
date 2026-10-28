"""docker/api-entrypoint.sh: what each process it starts can see."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "api-entrypoint.sh"
OWNER_URL = "postgresql+psycopg://curator_owner:owner-pw@db:5432/curator"
FAKE_CURATOR = """#!/bin/sh
owner=$(env | grep -i '^curator_migration_database_url=' || echo unset)
echo "$* | owner=${owner#*=}" >> "$CALLS"
"""


def _run(
    tmp_path: Path,
    *args: str,
    name: str | None = "CURATOR_MIGRATION_DATABASE_URL",
) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "curator"
    fake.write_text(FAKE_CURATOR)
    fake.chmod(0o755)
    calls = tmp_path / "calls.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        **({name: OWNER_URL} if name else {}),
    }
    subprocess.run(["sh", str(ENTRYPOINT), *args], env=env, check=True)
    return calls.read_text().splitlines()


def test_the_server_never_inherits_the_owner_url(tmp_path: Path) -> None:
    release, serve = _run(tmp_path)
    assert release == f"db release --if-enabled | owner={OWNER_URL}"
    assert serve.startswith("serve --host 0.0.0.0 --port 8000")
    assert serve.endswith("| owner=unset")


def test_every_spelling_of_the_owner_url_is_dropped(tmp_path: Path) -> None:
    for i, name in enumerate(
        ["curator_migration_database_url", "Curator_Migration_Database_Url"]
    ):
        home = tmp_path / str(i)
        home.mkdir()
        release, serve = _run(home, name=name)
        assert release.endswith(f"owner={OWNER_URL}")
        assert serve.endswith("| owner=unset")


def test_without_an_owner_url_both_steps_still_run(tmp_path: Path) -> None:
    release, serve = _run(tmp_path, name=None)
    assert release == "db release --if-enabled | owner=unset"
    assert serve.startswith("serve --host 0.0.0.0 --port 8000")


def test_the_image_makes_the_entrypoint_executable() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "chmod 0755 /usr/local/bin/api-entrypoint.sh" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/api-entrypoint.sh"]' in dockerfile


def test_fly_release_command_runs_with_the_owner_url(tmp_path: Path) -> None:
    (release,) = _run(tmp_path, "curator", "db", "release")
    assert release == f"db release | owner={OWNER_URL}"
