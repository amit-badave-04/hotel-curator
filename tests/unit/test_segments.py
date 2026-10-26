"""Tests for "who this hotel suits" segment findings."""

from pathlib import Path

import pandas as pd

from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.segments import (
    SegmentFinding,
    finding_text,
    segment_findings,
)
from hotel_curator.taxonomy import Attribute

POLICY = TierPolicy.load(
    Path(__file__).parents[2] / "config" / "tier_policy.yaml"
)


def _signals(family_room_praise: float, n: int = 120) -> pd.DataFrame:
    rows = []
    for i in range(n):
        family = i % 2 == 0
        praise = family_room_praise if family else 0.9
        row = {
            "review_id": f"r{i}",
            "hotel_id": "h1",
            "traveler_type": "family_young_children" if family else "couple",
            "trip_type": "leisure",
            **{f"pos_{a.value}": 0.0 for a in Attribute},
            **{f"neg_{a.value}": 0.0 for a in Attribute},
        }
        liked = (i // 2) % 10 < praise * 10
        row["pos_room"] = 1.0 if liked else 0.0
        row["neg_room"] = 0.0 if liked else 1.0
        rows.append(row)
    return pd.DataFrame(rows)


def test_families_who_dislike_the_room_are_flagged() -> None:
    findings = segment_findings(_signals(0.3), POLICY)["h1"]
    families = [f for f in findings if f.segment == "families"]
    assert len(families) == 1
    finding = families[0]
    assert finding.attribute is Attribute.ROOM
    assert finding.direction == "worse"
    assert finding.probability >= 0.95
    text = finding_text(finding, "two_sided")
    assert text.startswith("Families praised room less than other guests")
    couples = [f for f in findings if f.segment == "couples"]
    assert couples and couples[0].direction == "better"


def _noise_signals(family_complaints: float, n: int = 120) -> pd.DataFrame:
    """Every review is about noise only; couples complain in 1 of 10."""
    frame = _signals(0.9, n)
    frame[["pos_room", "neg_room"]] = 0.0
    family = frame["traveler_type"] != "couple"
    rank = (frame.index.to_series() // 2) % 10
    rate = family.map({True: family_complaints, False: 0.1})
    frame["neg_noise"] = (rank < rate * 10).astype(float)
    return frame


def test_must_be_segments_are_oriented_by_complaints() -> None:
    findings = segment_findings(_noise_signals(0.6), POLICY)["h1"]
    by_segment = {f.segment: f for f in findings}
    families, couples = by_segment["families"], by_segment["couples"]
    assert families.attribute is Attribute.NOISE
    assert families.direction == "worse"
    assert families.segment_value > families.others_value
    assert couples.direction == "better"
    assert finding_text(families, "must_be").startswith(
        "Families complained about noise more often than other guests"
    )
    assert finding_text(couples, "must_be").startswith(
        "Couples complained about noise less often than other guests"
    )


def test_segments_that_are_not_people_read_as_guests() -> None:
    finding = SegmentFinding(
        "business trips", Attribute.ROOM, "worse", 0.6, 0.8, 40.0, 0.97
    )
    assert finding_text(finding, "two_sided").startswith(
        "Guests on business trips praised room less than other guests"
    )


def test_no_finding_without_a_real_difference() -> None:
    assert "h1" not in segment_findings(_signals(0.9), POLICY)


def test_no_finding_below_the_evidence_floor() -> None:
    assert "h1" not in segment_findings(_signals(0.3, n=14), POLICY)


def test_findings_do_not_depend_on_other_hotels_in_the_run() -> None:
    first = _signals(0.3)
    second = _signals(0.5).assign(
        hotel_id="h2", review_id=lambda f: "x" + f["review_id"]
    )
    alone = segment_findings(first, POLICY)["h1"]
    together = segment_findings(pd.concat([second, first]), POLICY)["h1"]
    assert alone == together
