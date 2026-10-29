"""File checksums shared by the data pipeline and the serving image.

Kept free of third-party imports so the API image (core dependencies only)
can verify its model package at build time.
"""

import hashlib
from pathlib import Path

CHUNK_SIZE = 1 << 20


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
