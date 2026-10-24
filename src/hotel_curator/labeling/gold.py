"""Turn an annotation set plus panel answers into part-level label tables.

Output rows are review *parts* that have text (a Liked or Disliked part that
is not the site placeholder), with the 16 label columns. For a set annotated
by an odd number of annotators the label is the majority vote; for a
single-annotator set it is that annotator's answer. Per-label agreement
(Fleiss' kappa, pairwise Cohen's kappa) is computed whenever there are at
least two annotators.

``human_audit_sheet`` draws reviews for the owner to label by hand, with the
panel's labels hidden, so human-vs-panel agreement can be measured later
(BLOCKERS.md, B3).
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd

from hotel_curator.labeling.agreement import LabelAgreement, agreement_report
from hotel_curator.labeling.batches import load_answers, read_set
from hotel_curator.labeling.panel import ParsedAnnotation, merge_panel
from hotel_curator.taxonomy import LABELS

PARTS = ("liked", "disliked")


@dataclass
class PanelResult:
    """Part-level labels and agreement for one annotation set."""

    parts: pd.DataFrame
    agreement: list[LabelAgreement] = field(default_factory=list)
    n_annotators: int = 1


def _load_raters(
    set_dir: Path, annotators: Sequence[str], ids: set[str]
) -> list[ParsedAnnotation]:
    raters = []
    for annotator in annotators:
        path = set_dir / "answers" / f"{annotator}.jsonl"
        parsed, _ = load_answers(path, ids)
        if parsed.errors:
            raise ValueError(
                f"annotator {annotator}: {len(parsed.errors)} invalid answers, "
                f"e.g. {list(parsed.errors.items())[:2]}"
            )
        raters.append(parsed)
    return raters


def _part_row(
    sample: pd.DataFrame,
    review_id: str,
    part: str,
    text: str,
    labels: npt.NDArray[np.bool_],
) -> dict[str, object]:
    """One labelled part with the sample's hotel, stratum and weight."""
    return {
        "review_id": review_id,
        "hotel_id": sample.at[review_id, "hotel_id"],
        "part": part,
        "text": text,
        "stratum": sample.at[review_id, "stratum"],
        "sampling_weight": sample.at[review_id, "sampling_weight"],
        **dict(zip(LABELS, labels.tolist(), strict=True)),
    }


def merge_set(
    set_dir: Path,
    annotators: Sequence[str],
    *,
    agreement_reviews: Collection[str] | None = None,
) -> PanelResult:
    """Merge one annotation set's answers into part-level labels.

    Args:
        set_dir: The annotation set directory.
        annotators: Annotator ids whose answers to merge.
        agreement_reviews: Reviews that count towards agreement; all when
            ``None`` (used to leave out reviews moved to another set).

    Raises:
        ValueError: If any annotator has invalid or missing answers.
    """
    aset = read_set(set_dir)
    sample = pd.read_parquet(set_dir / "sample.parquet").set_index("review_id")
    review_ids = aset.review_ids
    raters = _load_raters(set_dir, annotators, set(review_ids))
    if len(raters) == 1:
        raters = raters * 3  # majority of identical copies = the answer
    panel = merge_panel(raters, review_ids, LABELS)
    counted = np.array(
        [
            agreement_reviews is None or rid in agreement_reviews
            for rid in review_ids
        ]
    )
    rows = []
    agreements: list[LabelAgreement] = []
    stacked_ratings = []
    for p, part in enumerate(PARTS):
        majority = panel.liked_majority if p == 0 else panel.disliked_majority
        ratings = panel.liked_ratings if p == 0 else panel.disliked_ratings
        texts = sample.loc[review_ids, f"{part}_text"]
        has_text = texts.notna().to_numpy()
        stacked_ratings.append(ratings[has_text & counted])
        rows += [
            _part_row(sample, review_ids[i], part, texts.iloc[i], majority[i])
            for i in np.flatnonzero(has_text)
        ]
    if len(annotators) > 1:
        agreements = agreement_report(np.concatenate(stacked_ratings), LABELS)
    parts = pd.DataFrame(rows).sort_values(["review_id", "part"])
    return PanelResult(
        parts.reset_index(drop=True), agreements, n_annotators=len(annotators)
    )


def build_label_table(
    parts: pd.DataFrame, hotels: pd.DataFrame
) -> pd.DataFrame:
    """Attach hotel metadata (city) used for evaluation slices."""
    return parts.merge(hotels[["hotel_id", "city"]], on="hotel_id", how="left")


def human_audit_sheet(
    sample: pd.DataFrame, *, n: int, seed: int
) -> pd.DataFrame:
    """Random reviews with empty code columns for a human to fill in."""
    drawn = sample.sample(n=min(n, len(sample)), random_state=seed)
    sheet = drawn[["review_id", "liked_text", "disliked_text"]].copy()
    sheet["liked_codes"] = ""
    sheet["disliked_codes"] = ""
    return sheet.reset_index(drop=True)
