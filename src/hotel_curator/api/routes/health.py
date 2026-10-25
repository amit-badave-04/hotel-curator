"""Liveness and readiness probes."""

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from hotel_curator.api.deps import SessionDep, StateDep
from hotel_curator.db.models import Hotel

router = APIRouter(tags=["health"])


class Health(BaseModel):
    """Probe result."""

    status: str
    hotels: int | None = None
    model: str | None = None


@router.get(
    "/healthz",
    response_model=Health,
    response_model_exclude_none=True,
    summary="Liveness probe",
)
def healthz() -> Health:
    """The process is up."""
    return Health(status="ok")


@router.get(
    "/readyz",
    response_model=Health,
    response_model_exclude_none=True,
    summary="Readiness probe",
)
def readyz(response: Response, session: SessionDep, state: StateDep) -> Health:
    """Ready when the database answers and hotel data is loaded.

    The model is reported but not required: search and details work without
    it; only ``/v1/analyze`` needs it.
    """
    try:
        hotels = session.scalar(select(func.count()).select_from(Hotel)) or 0
    except SQLAlchemyError:
        response.status_code = 503
        return Health(status="database unavailable")
    if hotels == 0:
        response.status_code = 503
        return Health(status="no hotel data loaded", hotels=0)
    return Health(status="ready", hotels=hotels, model=state.model_name)
