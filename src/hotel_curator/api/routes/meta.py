"""What the service is serving: data, model, tier policy, labels."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from hotel_curator.api.deps import SessionDep, StateDep
from hotel_curator.db.models import DatasetInfo, Hotel
from hotel_curator.scoring.grades import family_of
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import ATTRIBUTE_INFO, Attribute

router = APIRouter(prefix="/v1", tags=["meta"])


class AttributeInfo(BaseModel):
    """One of the eight hotel attributes."""

    attribute: Attribute
    display_name: str
    description: str
    essential: bool
    family: str


class Interval(BaseModel):
    """A point estimate with a 95% bootstrap interval."""

    point: float
    low: float
    high: float


class Evaluation(BaseModel):
    """Headline gold-test numbers of the served model versus v1.

    For a judged ONNX package, macro_f1, difference and mean_review_ece
    are the package's own numbers; v1_macro_f1 is always v1's.
    """

    model: str
    v1_configuration: str
    macro_f1: Interval
    v1_macro_f1: Interval
    difference: Interval
    mean_review_ece: float
    rungs: list[dict[str, Any]]
    test_reviews: int | None = Field(
        default=None, description="Held-out test reviews behind the scores."
    )
    test_hotels: int | None = Field(
        default=None, description="Hotels those test reviews come from."
    )


MONTH_PATTERN = r"^\d{4}-\d{2}$"


class DataPeriod(BaseModel):
    """First and last month of the usable reviews behind the tiers."""

    first_month: str = Field(pattern=MONTH_PATTERN, examples=["2015-08"])
    last_month: str = Field(pattern=MONTH_PATTERN, examples=["2017-08"])


class Meta(BaseModel):
    """Service metadata shown on the "About the model" page."""

    params_version: str
    model: str | None
    hotels: int
    tiers: dict[str, int]
    attributes: list[AttributeInfo]
    policy: TierPolicy
    evaluation: Evaluation | None = None
    data_period: DataPeriod | None = Field(
        default=None, description="None until a seed has recorded it."
    )
    analyze_available: bool = Field(
        description="Whether POST /v1/analyze can answer: a model is loaded."
    )


def _interval(values: dict[str, Any]) -> Interval:
    return Interval(
        point=values["point"], low=values["low"], high=values["high"]
    )


def _rung_ci(row: dict[str, Any]) -> Interval:
    s = row["summary"]
    return Interval(
        point=s["test_review_macro_f1"],
        low=s["test_review_macro_f1_low"],
        high=s["test_review_macro_f1_high"],
    )


def _served_numbers(row: dict[str, Any]) -> tuple[Interval, Interval, float]:
    """Macro-F1, difference from v1 and ECE of what is actually served.

    The served package's own gold-test numbers when promotion judged it
    (they can differ slightly from the checkpoint's, e.g. with int8
    weights); the checkpoint's otherwise.
    """
    package, package_diff = row.get("package"), row.get("package_vs_v1")
    if package and package.get("current") and package_diff:
        return (
            _interval(package["test_review_macro_f1"]),
            _interval(package_diff),
            float(package["test_mean_review_ece"]),
        )
    ece = float(row["summary"]["test_mean_review_ece"])
    return _rung_ci(row), _interval(row["vs_v1"]), ece


def load_evaluation(evals_dir: Path, served: str | None) -> Evaluation | None:
    """Gold-test numbers of the model that produced the served results.

    Read from ``promotion.json`` (written by ``curator model compare``);
    ``None`` when no results are loaded or that model is not in it (e.g.
    a package chosen with ``CURATOR_MODEL_PACKAGE``), rather than showing
    another model's numbers.
    """
    path = evals_dir / "promotion.json"
    if served is None or not path.is_file():
        return None
    decision = json.loads(path.read_text())
    rows = {r["name"]: r for r in decision["rows"]}
    v1 = decision["v1_config"]
    if served not in rows or rows[served].get("vs_v1") is None:
        return None
    macro_f1, difference, ece = _served_numbers(rows[served])
    return Evaluation(
        model=served,
        v1_configuration=v1,
        macro_f1=macro_f1,
        v1_macro_f1=_rung_ci(rows[v1]),
        difference=difference,
        mean_review_ece=ece,
        test_reviews=decision.get("test_reviews"),
        test_hotels=decision.get("test_hotels"),
        rungs=[
            {
                "name": r["name"],
                "family": r["family"],
                "test_macro_f1": r["summary"]["test_review_macro_f1"],
                "low": r["summary"]["test_review_macro_f1_low"],
                "high": r["summary"]["test_review_macro_f1_high"],
            }
            for r in decision["rows"]
        ],
    )


@router.get("/meta", response_model=Meta, summary="Data, model and policy")
def meta(session: SessionDep, state: StateDep) -> Meta:
    """Loaded dataset, tier counts, attribute definitions, policy values."""
    policy = state.policy
    info = session.scalars(
        select(DatasetInfo).order_by(DatasetInfo.id.desc()).limit(1)
    ).first()
    tiers = dict(
        session.execute(
            select(Hotel.tier, func.count()).group_by(Hotel.tier)
        ).all()
    )
    return Meta(
        params_version=policy.params_version,
        model=info.model if info else None,
        hotels=sum(tiers.values()),
        tiers=tiers,
        attributes=[
            AttributeInfo(
                attribute=a,
                display_name=ATTRIBUTE_INFO[a].display_name,
                description=ATTRIBUTE_INFO[a].description,
                essential=a in policy.essentials,
                family=family_of(a, policy),
            )
            for a in Attribute
        ],
        policy=policy,
        evaluation=load_evaluation(
            state.settings.artifacts_dir / "evals", info.model if info else None
        ),
        data_period=_period(info),
        # Loaded (or found missing) at startup, so this never loads a model.
        analyze_available=state.model() is not None,
    )


def _period(info: DatasetInfo | None) -> DataPeriod | None:
    if info is None or info.first_month is None or info.last_month is None:
        return None
    return DataPeriod(first_month=info.first_month, last_month=info.last_month)
