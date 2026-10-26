"""FastAPI dependencies: state, database session."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from hotel_curator.api.state import AppState


def get_state(request: Request) -> AppState:
    """The application state created in the lifespan."""
    state: AppState = request.app.state.curator
    return state


def get_session(
    state: Annotated[AppState, Depends(get_state)],
) -> Iterator[Session]:
    """A read-only session per request."""
    with state.sessions() as session:
        yield session


StateDep = Annotated[AppState, Depends(get_state)]
SessionDep = Annotated[Session, Depends(get_session)]
