"""Pinned raw data sources: every input file has a URL and a SHA-256.

The primary source is Booking.com "515K Hotel Reviews Data in Europe"
(Kaggle ``jiashenliu/515k-hotel-reviews-data-in-europe``, CC0). Kaggle needs
an account and is blocked in the build environment, so v2 reads a public
GitHub copy, pinned to one commit and to the digest of each of its 11
chunks. The chunks re-assemble into exactly the documented shape of the
original (515,738 rows x 17 columns, 1,492 hotel names); the build checks
this. ``content_fingerprint`` gives a line-ending- and chunking-independent
digest so the mirror can be compared with the Kaggle original
(``make data-verify-kaggle``).
"""

import hashlib
from dataclasses import dataclass

import pandas as pd

MIRROR_REPO = "raghavendranhp/Dynamic-Hotel-Recommendation-System-Using-NLP"
MIRROR_COMMIT = "903600f2ed2dbd761f2f435564c7e630f79e74b7"
_RAW_BASE = (
    f"https://raw.githubusercontent.com/{MIRROR_REPO}/{MIRROR_COMMIT}/Datas"
)


@dataclass(frozen=True)
class SourceFile:
    """One pinned input file."""

    name: str
    url: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Source:
    """A dataset made of one or more pinned files."""

    key: str
    title: str
    license: str
    homepage: str
    files: tuple[SourceFile, ...]


_CHUNKS = (
    (
        "8b6a682ee59a2d535e21f4f62a82953e38e942fc20bece21a068f2398c9cd5a1",
        23308340,
    ),
    (
        "08c6f604edca3b56e48c399d2bd5af28db933e8aa1e59fa5d4c7b91c2cc7c66a",
        23260577,
    ),
    (
        "557b91abd7127d69e27d762849dc43ba6c8e3c7c857255cde4ccb4d22993bfa2",
        23356191,
    ),
    (
        "d5418a95170c917c3d7126d26ac2d5f7c8b448c23f3d5ec8291aa2072538f5c2",
        22962842,
    ),
    (
        "f32556340d1927bf3f167d1b4dbc6296f5212a5cc7c6adfc56bb7b4183f6a33f",
        23276970,
    ),
    (
        "a309244c18be676e5495191232c72adb098a721e78dc0df46ed3496bef0c74b2",
        23043737,
    ),
    (
        "46c7238b84d7eb55856d7b5df7de0f04f1979c915622b796163b244f5163d6f4",
        23158383,
    ),
    (
        "90d4c87378fd7cc800c35bc9d2f703e8db2e9b41caba4958e42692d67ff41157",
        22972757,
    ),
    (
        "d2c6518f5304bf162055c2c1b1bbd963bfcfab146694179761d6f89f1e11fb88",
        23118991,
    ),
    (
        "7d85b03b9da4c8c28ebb2133a847f8b070fcc3bc09affa8c864588e216886cbc",
        22624366,
    ),
    (
        "ce6783ec5d831a5084140eb1e0030e9bb1847bc8564b264be8a8fb0df4d450d4",
        7408697,
    ),
)

BOOKING_515K = Source(
    key="booking515k",
    title="515K Hotel Reviews Data in Europe (Booking.com, 2015-2017)",
    license="CC0-1.0 (as declared by the Kaggle uploader)",
    homepage="https://www.kaggle.com/datasets/jiashenliu/515k-hotel-reviews-data-in-europe",
    files=tuple(
        SourceFile(
            name=f"output_{i}.csv",
            url=f"{_RAW_BASE}/output_{i}.csv",
            sha256=digest,
            size_bytes=size,
        )
        for i, (digest, size) in enumerate(_CHUNKS)
    ),
)


def content_fingerprint(raw: pd.DataFrame) -> str:
    """Order-, chunking- and line-ending-independent digest of a raw table.

    Each row's fields are joined with a unit separator and hashed; the
    sorted row hashes are hashed again together with the column names.
    """
    columns = "\x1e".join(raw.columns)
    rows = sorted(
        hashlib.sha256("\x1f".join(map(str, row)).encode()).hexdigest()
        for row in raw.itertuples(index=False)
    )
    digest = hashlib.sha256(columns.encode())
    for row_hash in rows:
        digest.update(row_hash.encode())
    return digest.hexdigest()
