"""Evidence quotes: which review parts back a grade, and how they are shown.

Rules (ADR 006, section 4):

* a quote is a whole review part whose calibrated probability for the cited
  label is at least 0.8, or at least 0.9 when it backs a Weak grade;
* up to two supporting quotes and one counter-quote per attribute, chosen
  deterministically (highest probability, then most recent, then id);
* parts that probably name a person the NER model would miss are not
  quoted (``scoring.names``); in the rest, person names (spaCy NER), long
  digit runs and e-mail-like tokens are redacted, and the text is cut at a
  word boundary to 200 characters;
* only month-year and traveler type are shown with a quote, never the
  reviewer's nationality, room type or length of stay.

The source dataset stripped punctuation from reviews; the API says so next to
every quote.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from hotel_curator.scoring.grades import AttributeGrade, Grade, Lean
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import Attribute, Polarity

PartName = Literal["liked", "disliked"]
PickedPart = tuple[Attribute, pd.Series, Polarity, bool]
"""(attribute, part row, polarity, supports the grade)."""
NAME_MASK = "[name]"
NUMBER_MASK = "[number]"
EMAIL_MASK = "[email]"
_DIGITS = re.compile(r"\d[\d ]{4,}\d")
_EMAIL = re.compile(r"\S+@\S+")
_PERSON_LABELS = frozenset({"PERSON"})
CANDIDATE_FACTOR = 5
"""Candidates looked at per quote, so skipped ones can be replaced."""


@dataclass(frozen=True)
class Quote:
    """A redacted, truncated review part shown as evidence."""

    review_id: str
    part: PartName
    text: str
    month: str
    traveler_type: str | None
    probability: float
    polarity: Polarity
    supports: bool


class Redactor:
    """Replace person names, digit runs and e-mail-like tokens.

    The spaCy pipeline is loaded on first use, so importing this module is
    cheap and tests can inject a fake ``nlp``.
    """

    def __init__(self, nlp: Any | None = None) -> None:
        self._nlp = nlp

    def _pipeline(self) -> Any:
        if self._nlp is None:
            import spacy

            self._nlp = spacy.load(
                "en_core_web_sm",
                disable=["parser", "lemmatizer", "tagger", "attribute_ruler"],
            )
        return self._nlp

    def redact(self, texts: Sequence[str]) -> list[str]:
        """Redacted copies of ``texts``, in order."""
        out = []
        for doc in self._pipeline().pipe(list(texts), batch_size=256):
            text = doc.text
            for ent in reversed(doc.ents):
                if ent.label_ in _PERSON_LABELS:
                    text = (
                        text[: ent.start_char]
                        + NAME_MASK
                        + text[ent.end_char :]
                    )
            text = _EMAIL.sub(EMAIL_MASK, text)
            out.append(_DIGITS.sub(NUMBER_MASK, text))
        return out


def truncate(text: str, max_chars: int) -> str:
    """Cut at the last word boundary within ``max_chars`` and add "..."."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 3]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip() + "..."


def supporting_polarity(grade: AttributeGrade) -> Polarity:
    """Praise backs Strong and OK grades; complaints back Weak ones."""
    leaning_weak = grade.grade is Grade.OK and grade.lean is Lean.TRENDING_WEAK
    if grade.grade is Grade.WEAK or leaning_weak:
        return Polarity.NEGATIVE
    return Polarity.POSITIVE


def _candidates(
    parts: pd.DataFrame,
    label: str,
    min_probability: float,
    count: int,
    names_someone: Callable[[str], bool] | None,
) -> list[pd.Series]:
    """The first ``count`` parts in quote order that name no one.

    Order: highest probability, then most recent, then review id.
    """
    chosen = parts[parts[label] >= min_probability]
    rows = chosen.sort_values(
        [label, "review_date", "review_id"], ascending=[False, False, True]
    ).head(count * CANDIDATE_FACTOR)
    kept = [
        row
        for _, row in rows.iterrows()
        if names_someone is None or not names_someone(str(row["text"]))
    ]
    return kept[:count]


def select_quotes(
    parts: pd.DataFrame,
    grades: dict[Attribute, AttributeGrade],
    policy: TierPolicy,
    names_someone: Callable[[str], bool] | None = None,
) -> list[PickedPart]:
    """Pick the parts to quote for every graded attribute of one hotel.

    Args:
        parts: The hotel's eligible parts with ``review_id``, ``part``,
            ``text``, ``review_date``, ``traveler_type`` and label columns.
        grades: The hotel's attribute grades.
        policy: Quote rules.
        names_someone: Skips candidates for which this is true (parts that
            probably name a person; see ``scoring.names``); the next
            candidate in order takes their place.

    Returns:
        ``(attribute, part row, polarity, supports)`` tuples; texts are not
        yet redacted.
    """
    rules = policy.quotes
    picked: list[PickedPart] = []
    for attribute, grade in grades.items():
        if grade.grade is Grade.TOO_FEW:
            continue
        support = supporting_polarity(grade)
        counter = (
            Polarity.POSITIVE
            if support is Polarity.NEGATIVE
            else Polarity.NEGATIVE
        )
        for polarity, count, supports in (
            (support, rules.supporting, True),
            (counter, rules.counter, False),
        ):
            strict = supports and grade.grade is Grade.WEAK
            threshold = (
                rules.min_probability_negative
                if strict
                else rules.min_probability
            )
            label = f"{attribute.value}_{polarity.value}"
            rows = _candidates(parts, label, threshold, count, names_someone)
            picked.extend((attribute, row, polarity, supports) for row in rows)
    return picked


TRAVELER_WORDS = {
    "couple": "Couple",
    "solo": "Solo traveller",
    "family_young_children": "Family with young children",
    "family_older_children": "Family with older children",
    "group": "Group",
    "friends": "Friends",
}
"""Display words for the canonical traveler types; "unknown" has none."""


def traveler_words(code: object) -> str | None:
    """Words to show for a traveler-type code, ``None`` when unknown."""
    if code is None or (isinstance(code, float) and pd.isna(code)):
        return None
    return TRAVELER_WORDS.get(str(code))


def build_quotes(
    picked: Sequence[PickedPart],
    redacted_texts: Sequence[str],
    policy: TierPolicy,
) -> list[tuple[Attribute, Quote]]:
    """Package selected parts as quotes, in input order.

    Args:
        picked: Output of :func:`select_quotes`.
        redacted_texts: The parts' texts after :meth:`Redactor.redact`.
        policy: Supplies the maximum quote length.

    Raises:
        ValueError: If the two sequences differ in length.
    """
    if len(picked) != len(redacted_texts):
        raise ValueError("one redacted text per picked part is required")
    out = []
    for (attribute, row, polarity, supports), text in zip(
        picked, redacted_texts, strict=True
    ):
        label = f"{attribute.value}_{polarity.value}"
        quote = Quote(
            review_id=str(row["review_id"]),
            part=row["part"],
            text=truncate(text, policy.quotes.max_chars),
            month=pd.Timestamp(row["review_date"]).strftime("%b %Y"),
            traveler_type=traveler_words(row.get("traveler_type")),
            probability=float(row[label]),
            polarity=polarity,
            supports=supports,
        )
        out.append((attribute, quote))
    return out
