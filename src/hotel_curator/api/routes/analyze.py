"""Classify a review the user types in (the model, live)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from hotel_curator.api.deps import StateDep
from hotel_curator.taxonomy import split_label
from hotel_curator.text import normalize_text

router = APIRouter(prefix="/v1", tags=["analyze"])

PART_CHARS_HARD_LIMIT = 20_000


class AnalyzeRequest(BaseModel):
    """A review split the way the booking site splits it."""

    liked: Annotated[str, Field(max_length=PART_CHARS_HARD_LIMIT)] = ""
    disliked: Annotated[str, Field(max_length=PART_CHARS_HARD_LIMIT)] = ""

    @field_validator("liked", "disliked")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class LabelOut(BaseModel):
    """One label the model assigned to a part."""

    label: str
    attribute: str
    polarity: str
    probability: float = Field(description="Calibrated probability.")


class PartOut(BaseModel):
    """Labels for one part; the text itself is not echoed back."""

    part: str
    characters: int
    labels: list[LabelOut]


class AnalyzeResponse(BaseModel):
    """Model output for the submitted review."""

    model: str
    parts: list[PartOut]


def _usable_parts(body: AnalyzeRequest, limit: int) -> list[tuple[str, str]]:
    """Non-empty parts within the length limit that keep some text.

    The model reads only what normalisation keeps (letters, digits, spaces,
    like the training data); a part with nothing left, e.g. emoji only or
    another script, is left out rather than scored as an empty review.

    Raises:
        HTTPException: 422 if a part is too long or no part has usable text.
    """
    given = [(name, getattr(body, name)) for name in ("liked", "disliked")]
    given = [(name, text) for name, text in given if text]
    if any(len(text) > limit for _, text in given):
        raise HTTPException(
            status_code=422,
            detail=f"Each part may have at most {limit} characters.",
        )
    usable = [(name, text) for name, text in given if normalize_text(text)]
    if not usable:
        raise HTTPException(
            status_code=422,
            detail="Give a liked or disliked text with English words.",
        )
    return usable


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Label a review with the served model",
    responses={503: {"description": "Model not loaded"}},
)
def analyze(body: AnalyzeRequest, state: StateDep) -> AnalyzeResponse:
    """Run the classifier on a Liked and/or Disliked text.

    Nothing is stored and the text is not returned. Each part is capped at
    ``CURATOR_MAX_PART_CHARS`` characters.
    """
    parts = _usable_parts(body, state.settings.max_part_chars)
    model = state.model()
    if model is None:
        raise HTTPException(status_code=503, detail="The model is not loaded.")
    texts = [normalize_text(text) for _, text in parts]
    prediction = model.predict(texts, [name for name, _ in parts])
    out = []
    for i, (name, text) in enumerate(parts):
        labels = []
        for k, label in enumerate(model.labels):
            if prediction.labels[i, k]:
                attribute, polarity = split_label(label)
                labels.append(
                    LabelOut(
                        label=label,
                        attribute=attribute.value,
                        polarity=polarity.value,
                        probability=float(prediction.probabilities[i, k]),
                    )
                )
        labels.sort(key=lambda x: -x.probability)
        out.append(PartOut(part=name, characters=len(text), labels=labels))
    return AnalyzeResponse(model=model.name, parts=out)
