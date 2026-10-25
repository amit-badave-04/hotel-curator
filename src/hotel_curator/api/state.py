"""Process-wide API state built in the lifespan and shared by routes."""

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from limits.strategies import MovingWindowRateLimiter
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from hotel_curator.config import Settings
from hotel_curator.inference.package import ModelPackage
from hotel_curator.scoring.policy import TierPolicy

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    """Settings, database and the (lazily loaded) model package."""

    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    limiter: MovingWindowRateLimiter
    policy: TierPolicy
    model_dir: Path | None
    _model: ModelPackage | None = None
    _model_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def model(self) -> ModelPackage | None:
        """Load the model package on first use; ``None`` if unavailable."""
        if self._model is not None or self._model_error is not None:
            return self._model
        with self._lock:
            if self._model is None and self._model_error is None:
                if self.model_dir is None:
                    # The reason was logged when the directory was resolved.
                    self._model_error = "no model package configured"
                else:
                    try:
                        self._model = ModelPackage(self.model_dir)
                    # Any failure (missing file, Git LFS pointer instead of
                    # weights, ONNX Runtime or tokenizer error) means "model
                    # unavailable": recorded once, never retried per request.
                    except Exception as exc:
                        self._model_error = f"{type(exc).__name__}: {exc}"
                        logger.warning("Model unavailable: %s", exc)
        return self._model

    @property
    def model_name(self) -> str | None:
        """Name of the loaded model package, without loading it."""
        return self._model.name if self._model is not None else None
