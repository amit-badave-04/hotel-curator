"""Tier assignment logic.

Extracted from NB04 Section 5.
"""

from hotel_curator.schema import DEFAULT_TIER_RULES, TierRule


def assign_tier(
    avg_score: float,
    min_critical_score: float,
    rules: list[TierRule] | None = None,
) -> str:
    """Top-down rule match. Returns 'FAIL' if no rule matches.

    Parameters
    ----------
    avg_score : float
        Mean attribute score across all attributes.
    min_critical_score : float
        Worst score among critical attributes.
    rules : list[TierRule] | None
        Ordered tier rules; defaults to DEFAULT_TIER_RULES.

    Returns
    -------
    str
        One of "ELITE", "SUPERIOR", "PREMIUM", "FAIL".
    """
    if rules is None:
        rules = DEFAULT_TIER_RULES
    for rule in rules:
        if (
            avg_score >= rule.min_avg_score
            and min_critical_score >= rule.min_critical_score
        ):
            return rule.name
    return "FAIL"
