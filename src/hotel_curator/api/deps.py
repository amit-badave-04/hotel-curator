"""FastAPI dependency injection: singleton model + DB session."""

import logging
from functools import lru_cache
from pathlib import Path

import torch

from hotel_curator.core.classifier import (
    ModelBundle,
    load_model_bundle,
)
from hotel_curator.settings import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_model_bundle() -> ModelBundle:
    """Load model once at startup; cached for all requests."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(
        "Loading model from %s (device=%s, thresholds=%s)",
        settings.model_dir, device, settings.threshold_set,
    )
    return load_model_bundle(
        settings.model_dir,
        device=device,
        threshold_set=settings.threshold_set,
    )


def get_device() -> str:
    """Resolve device string."""
    return "cuda" if torch.cuda.is_available() else "cpu"
