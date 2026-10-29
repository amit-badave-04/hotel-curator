"""Load hotel results (the committed demo file) into the database.

Idempotent: the file's SHA-256 is recorded in ``dataset_info``; seeding the
same file again is a no-op, seeding a different file replaces all hotels in
one transaction. Every record is validated against
:class:`~hotel_curator.schemas.HotelDetail` before anything is written.
"""

import gzip
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hotel_curator.db.models import AttributeScore, DatasetInfo, Hotel
from hotel_curator.schemas import HotelDetail, HotelSummary
from hotel_curator.scoring.tiers import Tier, tier_rank

logger = logging.getLogger(__name__)

UNRANKED = 9
"""Sort position of the "not enough" tiers, after every ranked tier."""


def read_results(path: Path) -> tuple[list[HotelDetail], str]:
    """Parse and validate a results file (``.json`` or ``.json.gz``).

    Returns:
        The hotel results and the file's SHA-256.

    Raises:
        pydantic.ValidationError: If any record does not match the schema.
    """
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = gzip.decompress(raw) if path.suffix == ".gz" else raw
    records = json.loads(text)
    return [HotelDetail.model_validate(r) for r in records], digest


def write_results(path: Path, results: list[HotelDetail]) -> None:
    """Write hotel results as gzipped JSON that :func:`read_results` reads.

    Keys are sorted and the gzip header carries no timestamp, so the same
    results always give the same bytes.
    """
    payload = [r.model_dump(mode="json") for r in results]
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))


def _rank(tier: Tier) -> int:
    rank = tier_rank(tier)
    return UNRANKED if rank is None else rank


def to_rows(result: HotelDetail) -> Hotel:
    """ORM rows for one hotel."""
    summary = HotelSummary.model_validate(
        result.model_dump(include=set(HotelSummary.model_fields))
    )
    return Hotel(
        hotel_id=result.hotel_id,
        name=result.name,
        name_lower=result.name.lower(),
        city=result.city,
        country=result.country,
        tier=result.tier.value,
        tier_rank=_rank(result.tier),
        sort_score=result.sort_score,
        reviews_eligible=result.reviews_eligible,
        summary=summary.model_dump(mode="json"),
        detail=result.model_dump(mode="json"),
        attributes=[
            AttributeScore(
                attribute=a.attribute.value,
                grade=a.grade.value,
                observed=a.observed,
                low=a.low,
                high=a.high,
            )
            for a in result.attributes
        ],
    )


def review_period(results: list[HotelDetail]) -> tuple[str, str]:
    """First and last month of the usable reviews over all hotels."""
    return (
        min(r.first_month for r in results),
        max(r.last_month for r in results),
    )


def seed(session: Session, path: Path) -> int:
    """Load ``path`` unless it is already the loaded dataset.

    Returns:
        Number of hotels loaded (0 when nothing changed).

    Raises:
        ValueError: If the file holds no hotels (loading it would empty the
            database and look like "nothing changed").
    """
    results, digest = read_results(path)
    if not results:
        raise ValueError(f"{path} holds no hotels; refusing to load it")
    current = session.scalars(
        select(DatasetInfo).order_by(DatasetInfo.id.desc()).limit(1)
    ).first()
    first_month, last_month = review_period(results)
    if current is not None and current.results_sha256 == digest:
        if current.first_month is None:  # seeded before migration 0002
            current.first_month, current.last_month = first_month, last_month
            session.flush()
            logger.info("Dataset %s: review period filled in", digest[:12])
        else:
            logger.info("Dataset %s already loaded", digest[:12])
        return 0
    session.execute(delete(AttributeScore))
    session.execute(delete(Hotel))
    session.add_all(to_rows(r) for r in results)
    first = results[0].provenance
    session.add(
        DatasetInfo(
            results_sha256=digest,
            params_version=first.params_version,
            model=first.model,
            hotels=len(results),
            loaded_at=datetime.now(UTC),
            first_month=first_month,
            last_month=last_month,
        )
    )
    session.flush()
    logger.info("Loaded %d hotels from %s", len(results), path)
    return len(results)
