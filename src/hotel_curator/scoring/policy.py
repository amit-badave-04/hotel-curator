"""The tier policy: every number the scoring uses, loaded and validated.

Values live in ``config/tier_policy.yaml`` and are documented in ADR 006.
The loaded policy carries ``params_version``, which is stamped on every
hotel result and shown in the API, so any published tier can be traced to
the exact parameters that produced it.
"""

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hotel_curator.taxonomy import Attribute


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Prior(_Frozen):
    """Beta prior shared by every attribute."""

    alpha: float = Field(gt=0)
    beta: float = Field(gt=0)


class Lines(_Frozen):
    """Strong and Weak lines for one attribute family."""

    strong: float = Field(gt=0, lt=1)
    weak: float = Field(gt=0, lt=1)


class ExcellentGate(_Frozen):
    """Review-spread conditions for the top tier."""

    min_months: int = Field(ge=1)
    max_month_share: float = Field(gt=0, le=1)


class QuotePolicy(_Frozen):
    """When a review part may be quoted as evidence."""

    min_probability: float = Field(gt=0, lt=1)
    min_probability_negative: float = Field(gt=0, lt=1)
    max_chars: int = Field(ge=40)
    supporting: int = Field(ge=0)
    counter: int = Field(ge=0)


class TrendPolicy(_Frozen):
    """Recent-versus-baseline comparison windows."""

    recent_months: int = Field(ge=1)
    baseline_months: int = Field(ge=1)
    min_opinions: int = Field(ge=1)
    two_sided_shift: float = Field(gt=0, lt=1)
    must_be_shift: float = Field(gt=0, lt=1)
    probability: float = Field(gt=0.5, lt=1)
    draws: int = Field(ge=100)

    def shift(self, must_be: bool) -> float:
        """Smallest change that counts: in complaint rate or praise share."""
        return self.must_be_shift if must_be else self.two_sided_shift


class ComparePolicy(_Frozen):
    """Credible-difference rule for hotel comparisons."""

    probability: float = Field(gt=0.5, lt=1)
    draws: int = Field(ge=100)


class TierPolicy(_Frozen):
    """All tier parameters (ADR 006, section 6)."""

    params_version: str = Field(min_length=1)
    prior: Prior
    confirmation: float = Field(gt=0.5, lt=1)
    interval_level: float = Field(gt=0, lt=1)
    essentials: tuple[Attribute, ...]
    must_be: tuple[Attribute, ...]
    two_sided: Lines
    must_be_lines: Lines
    min_opinions: int = Field(ge=1)
    min_reviews: int = Field(ge=1)
    min_complaining_reviews: int = Field(ge=1)
    complaining_review_probability: float = Field(gt=0, lt=1)
    hard_label_probability: float = Field(gt=0, lt=1)
    materiality: float = Field(gt=0, lt=1)
    excellent: ExcellentGate
    quotes: QuotePolicy
    trends: TrendPolicy
    compare: ComparePolicy
    near_duplicate_min_words: int = Field(ge=1)
    seed: int

    @model_validator(mode="after")
    def _check_lines(self) -> Self:
        if self.two_sided.weak >= self.two_sided.strong:
            raise ValueError("two-sided weak line must be below strong line")
        if self.must_be_lines.strong >= self.must_be_lines.weak:
            raise ValueError("must-be strong rate must be below weak rate")
        if set(self.essentials) & set(self.must_be):
            raise ValueError("an essential cannot be a must-be attribute")
        if len(self.essentials) != len(set(self.essentials)):
            raise ValueError("duplicate essentials")
        return self

    def is_must_be(self, attribute: Attribute) -> bool:
        """True for complaint-rate attributes (noise, wifi)."""
        return attribute in self.must_be

    @classmethod
    def load(cls, path: Path) -> "TierPolicy":
        """Read and validate a policy YAML file.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
            pydantic.ValidationError: If a value is missing or out of range.
        """
        return cls.model_validate(yaml.safe_load(path.read_text()))
