"""Search, list and read hotels; compare hotels; list cities."""

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, func, select

from hotel_curator.api.deps import SessionDep, StateDep
from hotel_curator.config import MAX_PAGE_SIZE
from hotel_curator.db.models import AttributeScore, Hotel
from hotel_curator.schemas import HotelDetail, HotelSummary
from hotel_curator.scoring.compare import prob_better
from hotel_curator.scoring.grades import AttributeCounts, Grade
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import Tier
from hotel_curator.taxonomy import Attribute

router = APIRouter(prefix="/v1", tags=["hotels"])

HOTEL_ID = r"^h_[0-9a-f]{10}$"
CITY = r"^[A-Za-z][A-Za-z .'-]{0,59}$"
MAX_QUERY = 80
QUERY = r"^[^\x00-\x1f\x7f]+$"
"""No control characters: Postgres refuses NUL in text, which would be a 500."""
MAX_PAGE = 10_000
MAX_COMPARE = 4


class SortOrder(StrEnum):
    """Allow-listed sort orders (SR-14)."""

    BEST = "best"
    NAME = "name"
    REVIEWS = "reviews"


class HotelPage(BaseModel):
    """One page of search results."""

    items: list[HotelSummary]
    total: int = Field(description="Matches across all pages.")
    page: int
    page_size: int


class City(BaseModel):
    """A city with hotels."""

    city: str
    country: str
    hotels: int


def _filtered(
    city: str | None,
    tiers: list[Tier] | None,
    strong: list[Attribute] | None,
    avoid_weak: list[Attribute] | None,
    q: str | None,
) -> Select[Hotel]:
    stmt = select(Hotel)
    if city:
        stmt = stmt.where(Hotel.city == city)
    if tiers:
        stmt = stmt.where(Hotel.tier.in_([t.value for t in tiers]))
    if q:
        escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%")
        escaped = escaped.replace("_", "\\_")
        stmt = stmt.where(Hotel.name_lower.like(f"%{escaped}%", escape="\\"))
    for attribute in strong or []:
        stmt = stmt.where(
            Hotel.attributes.any(
                and_(
                    AttributeScore.attribute == attribute.value,
                    AttributeScore.grade == Grade.STRONG.value,
                )
            )
        )
    for attribute in avoid_weak or []:
        stmt = stmt.where(
            ~Hotel.attributes.any(
                and_(
                    AttributeScore.attribute == attribute.value,
                    AttributeScore.grade == Grade.WEAK.value,
                )
            )
        )
    return stmt


ORDER = {
    SortOrder.BEST: (Hotel.tier_rank, Hotel.sort_score.desc(), Hotel.hotel_id),
    SortOrder.NAME: (Hotel.name_lower, Hotel.hotel_id),
    SortOrder.REVIEWS: (Hotel.reviews_eligible.desc(), Hotel.hotel_id),
}


@router.get("/hotels", response_model=HotelPage, summary="Search hotels")
def list_hotels(
    session: SessionDep,
    state: StateDep,
    city: Annotated[str | None, Query(pattern=CITY)] = None,
    tier: Annotated[list[Tier] | None, Query(max_length=6)] = None,
    strong: Annotated[
        list[Attribute] | None,
        Query(max_length=8, description="Only hotels Strong in all of these."),
    ] = None,
    avoid_weak: Annotated[
        list[Attribute] | None,
        Query(max_length=8, description="Exclude hotels Weak in any of these."),
    ] = None,
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=MAX_QUERY,
            pattern=QUERY,
            description="Name contains",
        ),
    ] = None,
    sort: SortOrder = SortOrder.BEST,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> HotelPage:
    """Hotels matching the filters, best tier first by default.

    "Best" sorts by tier, then by a cautious score (the lowest 5th
    percentile of the essentials' share of praise), so hotels with more
    evidence rank above hotels with the same share but fewer reviews.
    """
    size = min(page_size, state.settings.max_page_size)
    stmt = _filtered(city, tier, strong, avoid_weak, q)
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = session.scalars(
        stmt.order_by(*ORDER[sort]).offset((page - 1) * size).limit(size)
    ).all()
    return HotelPage(
        items=[HotelSummary.model_validate(h.summary) for h in rows],
        total=total or 0,
        page=page,
        page_size=size,
    )


@router.get(
    "/hotels/{hotel_id}",
    response_model=HotelDetail,
    summary="One hotel with grades, explanation and evidence",
    responses={404: {"description": "Unknown hotel"}},
)
def get_hotel(
    session: SessionDep,
    hotel_id: Annotated[str, Path(pattern=HOTEL_ID)],
) -> HotelDetail:
    """Everything behind one hotel's tier."""
    hotel = session.get(Hotel, hotel_id)
    if hotel is None:
        raise HTTPException(status_code=404, detail="Hotel not found.")
    return HotelDetail.model_validate(hotel.detail)


class CompareValue(BaseModel):
    """One hotel's numbers on one attribute."""

    hotel_id: str
    grade: Grade
    observed: float
    low: float
    high: float
    opinions: float


class CompareRow(BaseModel):
    """One attribute across the compared hotels."""

    attribute: Attribute
    values: list[CompareValue]
    leader: str | None = Field(
        description="Hotel credibly better than every other one on this "
        "attribute (posterior probability >= 0.95), if any."
    )
    leader_probability: float | None


class Comparison(BaseModel):
    """Side-by-side comparison of 2-4 hotels."""

    hotels: list[HotelSummary]
    attributes: list[CompareRow]


def _counts(detail: HotelDetail, attribute: Attribute) -> AttributeCounts:
    a = next(x for x in detail.attributes if x.attribute == attribute)
    return AttributeCounts(
        attribute,
        a.positive,
        a.negative,
        a.reviews,
        a.complaining_reviews,
        0,
        0,
    )


def _mean(detail: HotelDetail, attribute: Attribute) -> float:
    return next(a.mean for a in detail.attributes if a.attribute == attribute)


def compare_details(
    details: list[HotelDetail], policy: TierPolicy
) -> list[CompareRow]:
    """Per attribute, the credible leader among the hotels (if any)."""
    rows = []
    for attribute in Attribute:
        values = []
        for d in details:
            a = next(x for x in d.attributes if x.attribute == attribute)
            values.append(
                CompareValue(
                    hotel_id=d.hotel_id,
                    grade=a.grade,
                    observed=a.observed,
                    low=a.low,
                    high=a.high,
                    opinions=a.opinions
                    if a.family == "two_sided"
                    else a.reviews,
                )
            )
        sign = -1.0 if policy.is_must_be(attribute) else 1.0
        best = max(details, key=lambda d: sign * _mean(d, attribute))
        probs = [
            prob_better(_counts(best, attribute), _counts(o, attribute), policy)
            for o in details
            if o.hotel_id != best.hotel_id
        ]
        sure = min(probs)
        leader = best.hotel_id if sure >= policy.compare.probability else None
        rows.append(
            CompareRow(
                attribute=attribute,
                values=values,
                leader=leader,
                leader_probability=sure if leader else None,
            )
        )
    return rows


@router.get("/compare", response_model=Comparison, summary="Compare hotels")
def compare(
    session: SessionDep,
    state: StateDep,
    ids: Annotated[
        list[Annotated[str, Field(pattern=HOTEL_ID)]],
        Query(min_length=2, max_length=MAX_COMPARE),
    ],
) -> Comparison:
    """Compare 2-4 hotels; differences are flagged only when credible."""
    unique = list(dict.fromkeys(ids))
    if len(unique) < 2:
        raise HTTPException(status_code=422, detail="Give two distinct hotels.")
    hotels = {
        h.hotel_id: h
        for h in session.scalars(
            select(Hotel).where(Hotel.hotel_id.in_(unique))
        )
    }
    missing = [i for i in unique if i not in hotels]
    if missing:
        raise HTTPException(status_code=404, detail="Hotel not found.")
    details = [HotelDetail.model_validate(hotels[i].detail) for i in unique]
    policy = state.policy
    return Comparison(
        hotels=[HotelSummary.model_validate(hotels[i].summary) for i in unique],
        attributes=compare_details(details, policy),
    )


@router.get("/cities", response_model=list[City], summary="Cities with hotels")
def cities(session: SessionDep) -> list[City]:
    """Cities in the data, with hotel counts."""
    rows = session.execute(
        select(Hotel.city, Hotel.country, func.count())
        .group_by(Hotel.city, Hotel.country)
        .order_by(Hotel.city)
    ).all()
    return [City(city=c, country=k, hotels=n) for c, k, n in rows]
