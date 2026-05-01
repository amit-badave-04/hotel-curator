"""Review classification endpoints."""

from fastapi import APIRouter, Depends

from hotel_curator.api.deps import get_device, get_model_bundle
from hotel_curator.core.classifier import (
    ModelBundle,
    apply_thresholds,
    infer_probs,
)
from hotel_curator.schema import (
    BatchClassifyRequest,
    ClassifyRequest,
    LabelPrediction,
    ReviewClassification,
)
from hotel_curator.settings import settings

router = APIRouter(prefix="/v1/reviews", tags=["reviews"])


@router.post("/classify", response_model=ReviewClassification)
def classify_review(
    req: ClassifyRequest,
    bundle: ModelBundle = Depends(get_model_bundle),
    device: str = Depends(get_device),
) -> ReviewClassification:
    """Classify a single review into 16 attribute-polarity labels."""
    model, tokenizer, label_names, thresholds = bundle
    probs = infer_probs(
        model, tokenizer, [req.text],
        max_length=settings.max_seq_length,
        batch_size=1,
        device=device,
        use_bf16=settings.use_bf16,
    )
    preds = apply_thresholds(probs, thresholds, label_names)
    labels = {
        lbl: LabelPrediction(
            prob=float(probs[0, i]),
            predicted=bool(preds[0, i]),
        )
        for i, lbl in enumerate(label_names)
    }
    return ReviewClassification(text=req.text, labels=labels)


@router.post("/batch")
def classify_batch(
    req: BatchClassifyRequest,
    bundle: ModelBundle = Depends(get_model_bundle),
    device: str = Depends(get_device),
) -> list[ReviewClassification]:
    """Classify a batch of reviews (max 500)."""
    model, tokenizer, label_names, thresholds = bundle
    probs = infer_probs(
        model, tokenizer, req.texts,
        max_length=settings.max_seq_length,
        batch_size=settings.inference_batch_size,
        device=device,
        use_bf16=settings.use_bf16,
    )
    preds = apply_thresholds(probs, thresholds, label_names)
    results = []
    for j, text in enumerate(req.texts):
        labels = {
            lbl: LabelPrediction(
                prob=float(probs[j, i]),
                predicted=bool(preds[j, i]),
            )
            for i, lbl in enumerate(label_names)
        }
        results.append(
            ReviewClassification(text=text, labels=labels)
        )
    return results
