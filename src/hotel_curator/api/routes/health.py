"""Health check endpoints."""

from fastapi import APIRouter, Depends

from hotel_curator.api.deps import get_device, get_model_bundle
from hotel_curator.core.classifier import ModelBundle
from hotel_curator.schema import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(
    bundle: ModelBundle = Depends(get_model_bundle),
    device: str = Depends(get_device),
) -> HealthResponse:
    """Health check: confirms model is loaded and ready."""
    _, _, label_names, _ = bundle
    return HealthResponse(
        status="ok",
        model_loaded=True,
        num_labels=len(label_names),
        device=device,
    )
