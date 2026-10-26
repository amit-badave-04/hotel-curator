"""Plain-language "why this tier" text, generated only from result fields.

Nothing here computes a number: every figure in the text is formatted from
the grade or blocker it describes, so the text cannot disagree with the API
fields (a unit test checks this). Wording is a dated statement about
reviews, not a claim about the business.
"""

from dataclasses import dataclass, field

from hotel_curator.scoring.grades import AttributeGrade, Grade, Lean
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import (
    GOOD_MAX_CATCHES,
    Blocker,
    BlockerKind,
    Tier,
    TierResult,
)
from hotel_curator.taxonomy import ATTRIBUTE_INFO, Attribute

TIER_LABELS = {
    Tier.EXCELLENT: "Excellent",
    Tier.GOOD: "Good",
    Tier.FAIR: "Fair",
    Tier.CAUTION: "Caution",
    Tier.NOT_ENOUGH_EVIDENCE: "Not enough evidence",
    Tier.NOT_ENOUGH_REVIEWS: "Not enough reviews",
}
CATCH_WORDS = {
    Attribute.NOISE: "noisy",
    Attribute.WIFI: "weak wifi",
    Attribute.LOCATION: "location complaints",
    Attribute.VALUE: "poor value",
    Attribute.FOOD: "disappointing food",
}
FOR_YOU_WORDS = {
    Attribute.NOISE: "noise",
    Attribute.WIFI: "the wifi",
    Attribute.LOCATION: "the location",
    Attribute.VALUE: "the price",
    Attribute.FOOD: "the food",
}
GRADE_WORDS = {
    Grade.STRONG: "Strong",
    Grade.WEAK: "Weak",
    Grade.OK: "OK",
    Grade.TOO_FEW: "Too few opinions",
}
LEAN_WORDS = {
    Lean.TRENDING_STRONG: "trending strong",
    Lean.TRENDING_WEAK: "trending weak",
}


def _name(attribute: Attribute) -> str:
    return ATTRIBUTE_INFO[attribute].display_name.lower()


def _join(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _pct(value: float) -> str:
    return f"{100 * value:.0f}%"


def _pct_beside(value: float, line: float, places: int = 0) -> str:
    """``value`` as a percent that never reads as the ``line`` beside it.

    ``places`` decimals, unless that prints the line's own figure for a
    value off the line (79.6% against 80% would read "80%"); then one more
    decimal, kept at least one step on the value's side of the line
    ("79.6%", and "79.9%" for 79.96%). A value equal to the line prints as
    the line.
    """
    shown = f"{100 * value:.{places}f}"
    if value == line or shown != f"{100 * line:.{places}f}":
        return f"{shown}%"
    finer = places + 1
    scale = 10**finer
    steps = round(100 * value * scale)
    line_steps = round(100 * line * scale)
    if value < line:
        steps = min(steps, line_steps - 1)
    else:
        steps = max(steps, line_steps + 1)
    return f"{steps / scale:.{finer}f}%"


DECIMAL_BELOW = 20
"""Expected counts under this are shown with one decimal, so a count just
under a threshold (9.6 opinions against a minimum of 10) never reads as the
threshold itself."""


def _count(value: float, noun: str) -> str:
    """Counted noun: 1 review, 3 reviews, about 9.6 opinions."""
    if float(value).is_integer():
        n = int(value)
        return f"{n:,} {noun if n == 1 else noun + 's'}"
    shown = f"{value:.1f}" if value < DECIMAL_BELOW else f"{value:,.0f}"
    return f"about {shown} {noun}s"


def _more(n: int | None, noun: str) -> tuple[str, str]:
    """("1 more review", "is") or ("3 more reviews", "are")."""
    count = n if n is not None else 0
    return _count(count, f"more {noun}"), "is" if count == 1 else "are"


def tier_label(result: TierResult) -> str:
    """Short label, naming catches: "Good, but noisy"."""
    label = TIER_LABELS[result.tier]
    words = [CATCH_WORDS.get(a, _name(a)) for a in result.catches]
    ranked = result.tier in (Tier.EXCELLENT, Tier.GOOD, Tier.FAIR)
    if words and ranked:
        return f"{label}, but {_join(words)}"
    return label


def observed_share(grade: AttributeGrade) -> float:
    """Raw share of praise (two-sided) or complaint rate (must-be)."""
    counts = grade.counts
    if grade.family == "two_sided":
        return counts.positive / counts.opinions if counts.opinions else 0.0
    return counts.complaint_rate


def grade_line(grade: AttributeGrade) -> str:
    """One line per attribute: grade, measure, evidence size, likely range."""
    head = f"{ATTRIBUTE_INFO[grade.attribute].display_name}: "
    head += GRADE_WORDS[grade.grade]
    if grade.lean is not None:
        head += f" ({LEAN_WORDS[grade.lean]})"
    counts = grade.counts
    share = _pct(observed_share(grade))
    likely = f"likely {_pct(grade.low)} to {_pct(grade.high)}"
    if grade.family == "two_sided":
        opinions = _count(counts.opinions, "opinion")
        body = f"{share} of {opinions} were praise ({likely})"
    else:
        guests = _count(counts.reviews, "guest")
        body = f"{share} of {guests} complained ({likely})"
    return f"{head}. {body}."


def blocker_line(
    blocker: Blocker, result: TierResult, policy: TierPolicy
) -> str:
    """What would have to change, in guest terms."""
    a = blocker.attribute
    name = _name(a) if a else ""
    grade = result.grades.get(a) if a else None
    if blocker.kind is BlockerKind.MORE_REVIEWS:
        more, verb = _more(blocker.more, "review")
        return f"{more.capitalize()} {verb} needed before a tier is given."
    if blocker.kind is BlockerKind.MORE_OPINIONS:
        more, verb = _more(blocker.more, "opinion")
        return f"{more.capitalize()} about {name} {verb} needed to grade it."
    if blocker.kind is BlockerKind.CONFIRM_STRONG:
        more, _ = _more(blocker.more, "opinion")
        return (
            f"{name.capitalize()} would be confirmed as a strength after about "
            f"{more} at the current rate."
        )
    if blocker.kind is BlockerKind.SHARE_TOO_LOW and grade is not None:
        return _share_line(name, grade, policy)
    if blocker.kind is BlockerKind.WEAK_ESSENTIAL:
        return (
            f"Complaints about {name} outnumber praise; this has to change "
            "first."
        )
    if blocker.kind is BlockerKind.MATERIAL_WEAK and grade is not None:
        return (
            f"Complaints about {name} ({_pct(grade.counts.complaint_rate)} of "
            "guests) hold it back."
        )
    return _spread_line(result, policy)


def _share_line(name: str, grade: AttributeGrade, policy: TierPolicy) -> str:
    """Why more of the same opinions would not confirm a strength.

    The share is printed so that it never reads as the line it is compared
    with ("now 79.5%; the line is 80%", not "now 80%").
    """
    line = (
        policy.two_sided.strong
        if grade.family == "two_sided"
        else policy.must_be_lines.strong
    )
    share = observed_share(grade)
    if share > line:
        return (
            f"{name.capitalize()} is just past the line for a strength "
            f"({_pct_beside(share, line, places=1)} against {_pct(line)}), "
            "too close to confirm; it needs a clearly higher share."
        )
    return (
        f"{name.capitalize()} needs more praise to count as a strength "
        f"(now {_pct_beside(share, line)}; the line is {_pct(line)})."
    )


def _spread_line(result: TierResult, policy: TierPolicy) -> str:
    """Which part of the Excellent spread rule the reviews fail."""
    rule = policy.excellent
    spread = result.spread
    if spread is not None and spread.distinct_months >= rule.min_months:
        busiest = _pct(spread.max_month_share)
        return (
            f"Too many reviews come from one month ({busiest}); Excellent "
            f"needs no month above {_pct(rule.max_month_share)}."
        )
    months = "" if spread is None else f" (now {spread.distinct_months})"
    return (
        "Reviews are concentrated in a few months; Excellent needs reviews "
        f"spread over at least {rule.min_months} months{months}."
    )


@dataclass
class Explanation:
    """The text blocks the UI shows for a hotel."""

    label: str
    verdict: str
    essentials: list[str] = field(default_factory=list)
    what_would_change: list[str] = field(default_factory=list)
    for_you: list[str] = field(default_factory=list)


def _verdict(result: TierResult, period: str, policy: TierPolicy) -> str:
    essentials = list(policy.essentials)
    strong = [
        _name(a) for a in essentials if result.grades[a].grade is Grade.STRONG
    ]
    intro = f"In reviews from {period}, "
    if result.tier is Tier.NOT_ENOUGH_REVIEWS:
        return (
            f"{intro}there are too few reviews to give a tier "
            f"(at least {policy.min_reviews} are needed)."
        )
    if result.tier is Tier.NOT_ENOUGH_EVIDENCE:
        thin = [
            _name(a)
            for a in essentials
            if result.grades[a].grade is Grade.TOO_FEW
        ]
        return f"{intro}too few guests commented on {_join(thin)} to judge it."
    if result.tier is Tier.CAUTION:
        weak = [_name(b.attribute) for b in result.blockers if b.attribute]
        return (
            f"{intro}complaints about {_join(weak)} outnumbered praise "
            f"(at least {_pct(policy.confirmation)} sure)."
        )
    sure = f"at least {_pct(policy.confirmation)} sure"
    if result.tier is Tier.EXCELLENT:
        return f"{intro}guests praised {_join(strong)} ({sure} each)."
    held_back = _held_back(result)
    if len(strong) == len(essentials):
        return (
            f"{intro}guests praised {_join(strong)} ({sure} each), "
            f"but {held_back}."
        )
    others = [_name(a) for a in essentials if _name(a) not in strong]
    unconfirmed = (
        "is not a confirmed strength"
        if len(others) == 1
        else "are not confirmed strengths"
    )
    if strong:
        text = f"{intro}guests praised {_join(strong)} ({sure}); "
        text += f"{_join(others)} {unconfirmed}"
        if result.tier is Tier.FAIR and len(result.catches) > GOOD_MAX_CATCHES:
            text += f", and {_held_back(result)}"
        return text + "."
    return f"{intro}none of {_join(others)} is a confirmed strength."


def _held_back(result: TierResult) -> str:
    """Why a hotel with confirmed essentials is not in a higher tier."""
    if result.catches:
        names = _join([_name(a) for a in result.catches])
        return f"complaints about {names} hold it back"
    spread = result.spread
    if spread is not None and spread.distinct_months > 0:
        return (
            f"its reviews are concentrated in time ({spread.distinct_months} "
            f"months, the busiest with {_pct(spread.max_month_share)})"
        )
    return "its reviews are concentrated in a few months"


def explain(result: TierResult, period: str, policy: TierPolicy) -> Explanation:
    """Build the explanation for one hotel.

    Args:
        result: The hotel's tier result.
        period: Human-readable data period, e.g. "Aug 2015 to Aug 2017".
        policy: Tier policy (for the lines quoted in the text).
    """
    return Explanation(
        label=tier_label(result),
        verdict=_verdict(result, period, policy),
        essentials=[grade_line(result.grades[a]) for a in policy.essentials],
        what_would_change=[
            blocker_line(b, result, policy) for b in result.blockers
        ],
        for_you=[
            f"If {FOR_YOU_WORDS.get(a, _name(a))} doesn't bother you, "
            f"this hotel would be {TIER_LABELS[tier]}."
            for a, tier in result.if_ignored.items()
        ],
    )
