"""Core ML logic -- zero framework dependencies."""

from hotel_curator.core.aggregator import (
    aggregate_hotel_signals,
    compute_mention_rates,
    compute_weighted_avg_score,
)
from hotel_curator.core.bootstrap import bootstrap_hotel_ci
from hotel_curator.core.classifier import (
    apply_thresholds,
    infer_probs,
    load_model_bundle,
)
from hotel_curator.core.evidence import build_evidence
from hotel_curator.core.tier import assign_tier

__all__ = [
    "aggregate_hotel_signals",
    "apply_thresholds",
    "assign_tier",
    "bootstrap_hotel_ci",
    "build_evidence",
    "compute_mention_rates",
    "compute_weighted_avg_score",
    "infer_probs",
    "load_model_bundle",
]
