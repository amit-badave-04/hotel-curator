"""Is the served model package the one the promotion rule judged?

``promotion.json`` records the SHA-256 of every package file
(``files_sha256``), and ``curator model publish`` copies exactly those
files. This module checks them again where the package is about to be
served: when the API image is built with ``REQUIRE_MODEL=1`` and in
``curator model verify-served``. It imports only the standard library and
:mod:`hotel_curator.checksums`, so it runs in the serving image.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from hotel_curator.checksums import sha256_file

PACKAGE_FILES = ("model.onnx", "tokenizer.json", "metadata.json")
"""Everything a package directory holds; publishing copies exactly these."""
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"


@dataclass(frozen=True)
class ServedPackage:
    """The promoted package directory and what is wrong with it, if anything."""

    winner: str
    directory: Path
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when every package file matches the promotion record."""
        return not self.problems


def judged_hashes(files_sha256: Sequence[Mapping[str, str]]) -> dict[str, str]:
    """Map file name to SHA-256 from a promotion row's ``files_sha256``."""
    return {entry["file"]: entry["sha256"] for entry in files_sha256}


def mismatched_files(directory: Path, judged: Mapping[str, str]) -> list[str]:
    """Describe each package file that is missing, a pointer, or different."""
    problems = []
    for name in PACKAGE_FILES:
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif is_lfs_pointer(path):
            problems.append(f"{name}: a Git LFS pointer (run git lfs pull)")
        elif sha256_file(path) != judged.get(name):
            problems.append(f"{name}: SHA-256 differs from promotion.json")
    return problems


def check_served_package(artifacts_dir: Path) -> ServedPackage:
    """Check ``artifacts/models/<winner>`` against ``promotion.json``.

    Raises:
        FileNotFoundError: If there is no promotion record.
        ValueError: If the record names no winner or has no row for it.
    """
    path = artifacts_dir / "evals" / "promotion.json"
    record = json.loads(path.read_text())
    winner = record.get("winner")
    if not winner:
        raise ValueError("the promotion rule promoted no model")
    row = next((r for r in record["rows"] if r["name"] == winner), None)
    if row is None:
        raise ValueError(f"{path} has no row for {winner}")
    directory = artifacts_dir / "models" / str(winner)
    judged = judged_hashes(row["package"]["files_sha256"])
    problems = tuple(mismatched_files(directory, judged))
    return ServedPackage(str(winner), directory, problems)


def is_lfs_pointer(path: Path) -> bool:
    """True when the file is a Git LFS pointer rather than its content."""
    with path.open("rb") as handle:
        return handle.read(len(LFS_POINTER_PREFIX)) == LFS_POINTER_PREFIX
