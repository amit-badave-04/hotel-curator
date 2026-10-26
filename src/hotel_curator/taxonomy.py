"""The label taxonomy: eight hotel attributes, two polarities, 16 labels.

This module is the single source of truth for attribute names, their
one-letter codes used by annotators and how v1 label names map to v2.
Which attributes are essential for the tiers is policy, not taxonomy: see
``essentials`` in ``config/tier_policy.yaml``. Every other module imports these
constants instead of spelling attribute names as strings.

The compact code format ("S+ C+ R-") is what annotators return; see
``docs/labeling/GUIDELINES.md`` for what each attribute covers.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Attribute(StrEnum):
    """A hotel quality attribute that guests judge in reviews."""

    CLEANLINESS = "cleanliness"
    ROOM = "room"
    STAFF = "staff"
    LOCATION = "location"
    NOISE = "noise"
    WIFI = "wifi"
    VALUE = "value"
    FOOD = "food"


class Polarity(StrEnum):
    """Direction of a guest's judgement about an attribute."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclass(frozen=True)
class AttributeInfo:
    """Human-facing metadata for one attribute.

    Attributes:
        code: One-letter annotation code.
        display_name: Name shown in the UI and API docs.
        description: One-sentence plain-language scope.
    """

    code: str
    display_name: str
    description: str


ATTRIBUTE_INFO: dict[Attribute, AttributeInfo] = {
    Attribute.CLEANLINESS: AttributeInfo(
        "C", "Cleanliness", "Hygiene and tidiness anywhere in the hotel."
    ),
    Attribute.ROOM: AttributeInfo(
        "R",
        "Room",
        "Size, beds, condition, bathroom fixtures, temperature, view.",
    ),
    Attribute.STAFF: AttributeInfo(
        "S",
        "Staff",
        "Friendliness, helpfulness and handling of requests.",
    ),
    Attribute.LOCATION: AttributeInfo(
        "L",
        "Location",
        "Closeness to sights and transport, the neighbourhood.",
    ),
    Attribute.NOISE: AttributeInfo(
        "N",
        "Noise",
        "Quiet rooms versus street, neighbour or wall noise.",
    ),
    Attribute.WIFI: AttributeInfo(
        "W", "Wifi", "Internet availability, speed and cost."
    ),
    Attribute.VALUE: AttributeInfo(
        "V", "Value", "Price relative to what was received, fees."
    ),
    Attribute.FOOD: AttributeInfo(
        "F",
        "Breakfast & food",
        "Breakfast and any food or drink served by the hotel.",
    ),
}

_SIGN_TO_POLARITY: dict[str, Polarity] = {
    "+": Polarity.POSITIVE,
    "-": Polarity.NEGATIVE,
}
_POLARITY_TO_SIGN: dict[Polarity, str] = {
    p: s for s, p in _SIGN_TO_POLARITY.items()
}
_CODE_TO_ATTRIBUTE: dict[str, Attribute] = {
    info.code: a for a, info in ATTRIBUTE_INFO.items()
}


def label_name(attribute: Attribute, polarity: Polarity) -> str:
    """Return the canonical label name, e.g. ``"wifi_negative"``."""
    return f"{attribute.value}_{polarity.value}"


LABELS: tuple[str, ...] = tuple(
    label_name(a, p) for a in Attribute for p in Polarity
)
"""All 16 labels in attribute-major order (same order as v1)."""

_LABEL_PARTS: dict[str, tuple[Attribute, Polarity]] = {
    label_name(a, p): (a, p) for a in Attribute for p in Polarity
}

V1_TO_V2_LABEL: dict[str, str] = {
    label.replace("food_", "breakfast_"): label for label in LABELS
}
"""Map v1 label names to v2 names. v1 called the food attribute
"breakfast" but its gold prompt already defined it as all food and drink."""


def split_label(label: str) -> tuple[Attribute, Polarity]:
    """Return ``(attribute, polarity)`` for a canonical label name.

    Raises:
        KeyError: If ``label`` is not one of :data:`LABELS`.
    """
    return _LABEL_PARTS[label]


def parse_codes(codes: str) -> set[str]:
    """Parse annotator codes such as ``"S+ C+ R-"`` into label names.

    Args:
        codes: Space-separated tokens, each an attribute code followed by
            ``+`` or ``-``. Empty or whitespace-only means no labels.

    Returns:
        The set of canonical label names.

    Raises:
        ValueError: If any token is not a valid label code.
    """
    labels: set[str] = set()
    for token in codes.split():
        attribute = _CODE_TO_ATTRIBUTE.get(token[:1])
        polarity = _SIGN_TO_POLARITY.get(token[1:])
        if len(token) != 2 or attribute is None or polarity is None:
            raise ValueError(f"Invalid label code {token!r} in {codes!r}")
        labels.add(label_name(attribute, polarity))
    return labels


def format_codes(labels: Iterable[str]) -> str:
    """Format label names as canonical, ordered annotator codes.

    Raises:
        KeyError: If a label is not one of :data:`LABELS`.
    """
    chosen = set(labels)
    unknown = chosen - set(LABELS)
    if unknown:
        raise KeyError(f"Unknown labels: {sorted(unknown)}")
    tokens = []
    for label in LABELS:
        if label in chosen:
            attribute, polarity = _LABEL_PARTS[label]
            code = ATTRIBUTE_INFO[attribute].code
            tokens.append(f"{code}{_POLARITY_TO_SIGN[polarity]}")
    return " ".join(tokens)
