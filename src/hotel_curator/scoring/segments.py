"""Who a hotel suits: attributes by traveler segment (plan section 8).

For each hotel, segment and attribute, the segment's guests are compared
with the hotel's other guests. A difference is reported only when it is
both credible and large enough to matter, reusing pre-registered values
(no new parameters): the posterior probability that the segment is better
(or worse) by at least the trend shift (10 points of praise share, or 5
points of complaint rate for noise and wifi) must reach
``policy.compare.probability`` (0.95), and both groups need at least
``policy.min_opinions`` opinions (``min_reviews`` reviews for must-be
attributes). Segments never change a tier.
"""

import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from hotel_curator.scoring.aggregate import NEG, POS
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import Attribute

SEGMENTS: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "couples": lambda f: f["traveler_type"] == "couple",
    "families": lambda f: f["traveler_type"].isin(
        ["family_young_children", "family_older_children"]
    ),
    "groups and friends": lambda f: f["traveler_type"].isin(
        ["group", "friends"]
    ),
    "solo travellers": lambda f: f["traveler_type"] == "solo",
    "business trips": lambda f: f["trip_type"] == "business",
}


SUBJECTS = {"business trips": "Guests on business trips"}
"""Sentence subjects for segments that are not people ("a trip praised")."""


@dataclass(frozen=True)
class SegmentFinding:
    """A credible, material difference between a segment and the rest."""

    segment: str
    attribute: Attribute
    direction: Literal["better", "worse"]
    segment_value: float
    others_value: float
    segment_size: float
    probability: float


def _sums(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [f"{p}_{a.value}" for p in (POS, NEG) for a in Attribute]
    sums = frame.groupby("hotel_id")[columns].sum()
    sums["n"] = frame.groupby("hotel_id").size()
    return sums


def _row_rng(seed: int, *keys: str) -> np.random.Generator:
    """A generator that depends only on the seed and ``keys``.

    Seeding per (hotel, segment, attribute) makes a hotel's findings the
    same whichever other hotels are scored in the same run.
    """
    return np.random.default_rng(
        [seed, *(zlib.crc32(k.encode()) for k in keys)]
    )


@dataclass(frozen=True)
class _Group:
    """Beta posterior parameters and evidence size, one row per hotel."""

    a: np.ndarray
    b: np.ndarray
    size: np.ndarray


def _group(
    sums: pd.DataFrame, attribute: Attribute, policy: TierPolicy
) -> _Group:
    """Vectorised :func:`~hotel_curator.scoring.grades.posterior_params`."""
    pos = sums[f"{POS}_{attribute.value}"].to_numpy(dtype=float)
    neg = sums[f"{NEG}_{attribute.value}"].to_numpy(dtype=float)
    n = sums["n"].to_numpy(dtype=float)
    a0, b0 = policy.prior.alpha, policy.prior.beta
    if policy.is_must_be(attribute):
        return _Group(a0 + neg, b0 + n - neg, n)
    return _Group(a0 + pos, b0 + neg, pos + neg)


def _compare_groups(
    seg: _Group,
    rest: _Group,
    must_be: bool,
    policy: TierPolicy,
    row_keys: Sequence[tuple[str, ...]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rows with enough evidence, and P(better by shift), P(worse by shift).

    O(rows x draws); each row draws from its own seeded generator.
    """
    floor = policy.min_reviews if must_be else policy.min_opinions
    shift = policy.trends.shift(must_be)
    idx = np.flatnonzero((seg.size >= floor) & (rest.size >= floor))
    n_draws = policy.compare.draws
    better, worse = np.empty(len(idx)), np.empty(len(idx))
    for k, i in enumerate(idx):
        rng = _row_rng(policy.seed, *row_keys[int(i)])
        draws_seg = rng.beta(seg.a[i], seg.b[i], size=n_draws)
        draws_rest = rng.beta(rest.a[i], rest.b[i], size=n_draws)
        gain = draws_rest - draws_seg if must_be else draws_seg - draws_rest
        better[k], worse[k] = (gain >= shift).mean(), (gain <= -shift).mean()
    return idx, better, worse


def segment_findings(
    signals: pd.DataFrame, policy: TierPolicy
) -> dict[str, list[SegmentFinding]]:
    """Credible segment differences per hotel.

    Args:
        signals: Review signals with ``traveler_type`` and ``trip_type``.
        policy: Supplies evidence floors, shifts, probability and seed.
    """
    total = _sums(signals)
    hotel_ids = [str(h) for h in total.index]
    out: dict[str, list[SegmentFinding]] = {}
    for name, select in SEGMENTS.items():
        seg_sums = _sums(signals[select(signals)]).reindex(
            total.index, fill_value=0
        )
        for attribute in Attribute:
            seg = _group(seg_sums, attribute, policy)
            rest = _group(total - seg_sums, attribute, policy)
            keys = [(h, name, attribute.value) for h in hotel_ids]
            idx, better, worse = _compare_groups(
                seg, rest, policy.is_must_be(attribute), policy, keys
            )
            for k, i in enumerate(idx):
                for direction, prob in (
                    ("better", better[k]),
                    ("worse", worse[k]),
                ):
                    if prob < policy.compare.probability:
                        continue
                    out.setdefault(hotel_ids[i], []).append(
                        SegmentFinding(
                            segment=name,
                            attribute=attribute,
                            direction=direction,  # type: ignore[arg-type]
                            segment_value=float(
                                seg.a[i] / (seg.a[i] + seg.b[i])
                            ),
                            others_value=float(
                                rest.a[i] / (rest.a[i] + rest.b[i])
                            ),
                            segment_size=float(seg.size[i]),
                            probability=float(prob),
                        )
                    )
    return out


def finding_text(finding: SegmentFinding, family: str) -> str:
    """One plain sentence per finding.

    The shares are posterior means (they shrink small groups towards the
    prior), hence "an estimated".
    """
    attribute = finding.attribute.value
    who = SUBJECTS.get(finding.segment, finding.segment.capitalize())
    if family == "must_be":
        verb = "less" if finding.direction == "better" else "more"
        return (
            f"{who} complained about {attribute} "
            f"{verb} often than other guests "
            f"(an estimated {100 * finding.segment_value:.0f}% vs "
            f"{100 * finding.others_value:.0f}% of reviews)."
        )
    verb = "more" if finding.direction == "better" else "less"
    return (
        f"{who} praised {attribute} {verb} than other "
        f"guests (an estimated {100 * finding.segment_value:.0f}% vs "
        f"{100 * finding.others_value:.0f}% of opinions were praise)."
    )
