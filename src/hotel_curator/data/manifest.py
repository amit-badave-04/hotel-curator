"""Data manifests: what was built, from what, by which code.

A manifest is a small JSON file committed to git next to the (gitignored)
data it describes. It records each file's SHA-256 and size, row counts, the
source digests, and the git commit of the code that produced it. Rebuilding
on a fresh machine and comparing manifests proves the data is identical;
see ``docs/decisions/003-data-versioning.md`` for why this is used instead
of a data-versioning service.
"""

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hotel_curator.data.download import sha256_file


@dataclass
class FileRecord:
    """One output file in a manifest."""

    path: str
    sha256: str
    size_bytes: int
    rows: int | None = None


@dataclass
class Manifest:
    """Provenance of one pipeline stage's outputs."""

    stage: str
    created_at: str
    code_commit: str
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: list[FileRecord] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def write(self, path: Path) -> None:
        """Write as pretty JSON with a trailing newline."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"
        )

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        """Load a manifest written by :meth:`write`."""
        data = json.loads(path.read_text())
        data["outputs"] = [FileRecord(**rec) for rec in data.get("outputs", [])]
        return cls(**data)


def git_commit(repo: Path) -> str:
    """Current commit hash, with ``-dirty`` if the tree has changes."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{head}-dirty" if dirty else head


def file_record(path: Path, root: Path, rows: int | None = None) -> FileRecord:
    """Describe ``path`` relative to ``root``."""
    return FileRecord(
        path=path.relative_to(root).as_posix(),
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
        rows=rows,
    )


def new_manifest(stage: str, repo: Path) -> Manifest:
    """Start a manifest for ``stage`` stamped with time and code commit."""
    return Manifest(
        stage=stage,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        code_commit=git_commit(repo),
    )
