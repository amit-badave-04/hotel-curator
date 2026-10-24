"""Annotation batches for the LLM annotator panel, and strict parsing.

Why this module exists: annotations are produced outside the Python process
(by independent LLM annotators that read ``docs/labeling/GUIDELINES.md``),
and every answer has to come back in a form we can trust. v1's parser turned
malformed or missing answers into "all labels false" and counted them as
successes; here every item is either parsed exactly, reported as an error,
or reported as missing. Nothing is silently filled in.

Input format sent to annotators (one JSON object per line)::

    {"id": "r1", "liked": "...", "disliked": "..."}

Expected answer format (one JSON object per line)::

    {"id": "r1", "liked": "S+ L+", "disliked": "R-"}
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from hotel_curator.labeling.agreement import majority_vote
from hotel_curator.taxonomy import parse_codes

PartLabels = tuple[set[str], set[str]]
"""(liked-part labels, disliked-part labels) for one review."""

_JSON_LINE = re.compile(r"\{.*\}")


@dataclass(frozen=True)
class AnnotationItem:
    """One review as shown to annotators."""

    review_id: str
    liked: str
    disliked: str


@dataclass
class ParsedAnnotation:
    """One annotator's parsed answers for one or more batches."""

    labels: dict[str, PartLabels] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    unexpected: set[str] = field(default_factory=set)

    def update(self, other: "ParsedAnnotation") -> None:
        """Merge a later batch's results into this one.

        A review parsed successfully later clears an earlier error or
        "missing" record for the same id (e.g. after a retry).
        """
        self.labels.update(other.labels)
        self.errors.update(other.errors)
        for review_id in self.labels:
            self.errors.pop(review_id, None)
        self.missing = (self.missing | other.missing) - set(self.labels)
        self.missing -= set(self.errors)
        self.unexpected |= other.unexpected


@dataclass(frozen=True)
class PanelLabels:
    """Per-part rating tensors and majority labels for a set of reviews.

    ``*_ratings`` have shape ``(n_items, n_raters, n_labels)``;
    ``*_majority`` have shape ``(n_items, n_labels)``.
    """

    review_ids: list[str]
    labels: list[str]
    liked_ratings: npt.NDArray[np.bool_]
    disliked_ratings: npt.NDArray[np.bool_]
    liked_majority: npt.NDArray[np.bool_]
    disliked_majority: npt.NDArray[np.bool_]


def render_batch(items: Sequence[AnnotationItem]) -> str:
    """Render items as JSON Lines for an annotator prompt."""
    return "\n".join(
        json.dumps(
            {"id": it.review_id, "liked": it.liked, "disliked": it.disliked},
            ensure_ascii=False,
        )
        for it in items
    )


def _parse_line(obj: object) -> tuple[str, PartLabels]:
    if not isinstance(obj, dict):
        raise ValueError("answer is not a JSON object")
    review_id, liked, disliked = (
        obj.get("id"),
        obj.get("liked"),
        obj.get("disliked"),
    )
    if not isinstance(review_id, str):
        raise ValueError("missing string 'id'")
    if not isinstance(liked, str) or not isinstance(disliked, str):
        raise ValueError(f"{review_id}: 'liked'/'disliked' must be strings")
    return review_id, (parse_codes(liked), parse_codes(disliked))


def parse_annotations(raw: str, expected_ids: set[str]) -> ParsedAnnotation:
    """Parse one annotator answer strictly.

    Args:
        raw: The annotator's raw text answer (may contain code fences).
        expected_ids: Review ids that were in the batch.

    Returns:
        Parsed labels plus per-item errors, missing and unexpected ids.
    """
    result = ParsedAnnotation()
    for line in raw.splitlines():
        match = _JSON_LINE.search(line)
        if not match:
            continue
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        item_id = obj.get("id") if isinstance(obj, dict) else None
        if isinstance(item_id, str) and item_id not in expected_ids:
            result.unexpected.add(item_id)
            continue
        try:
            review_id, parts = _parse_line(obj)
        except ValueError as exc:
            if isinstance(item_id, str):
                result.errors[item_id] = str(exc)
            continue
        if review_id in result.labels or review_id in result.errors:
            result.labels.pop(review_id, None)
            result.errors[review_id] = "duplicate answers for this id"
            continue
        result.labels[review_id] = parts
    result.missing = expected_ids - set(result.labels) - set(result.errors)
    return result


def _ratings(
    raters: Sequence[ParsedAnnotation],
    review_ids: Sequence[str],
    labels: Sequence[str],
    part: int,
) -> npt.NDArray[np.bool_]:
    index = {label: k for k, label in enumerate(labels)}
    out = np.zeros((len(review_ids), len(raters), len(labels)), dtype=bool)
    for r, rater in enumerate(raters):
        for i, review_id in enumerate(review_ids):
            for label in rater.labels[review_id][part]:
                out[i, r, index[label]] = True
    return out


def merge_panel(
    raters: Sequence[ParsedAnnotation],
    review_ids: Sequence[str],
    labels: Sequence[str],
) -> PanelLabels:
    """Combine an odd number of complete annotators by majority vote.

    Raises:
        ValueError: If any rater has no answer for a requested review.
    """
    for r, rater in enumerate(raters):
        absent = [rid for rid in review_ids if rid not in rater.labels]
        if absent:
            raise ValueError(
                f"rater {r} is missing {len(absent)} reviews, e.g. {absent[:3]}"
            )
    liked = _ratings(raters, review_ids, labels, part=0)
    disliked = _ratings(raters, review_ids, labels, part=1)
    return PanelLabels(
        review_ids=list(review_ids),
        labels=list(labels),
        liked_ratings=liked,
        disliked_ratings=disliked,
        liked_majority=majority_vote(liked),
        disliked_majority=majority_vote(disliked),
    )
