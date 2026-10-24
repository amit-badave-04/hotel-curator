"""Tests for multi-label metrics and cluster-bootstrap confidence intervals."""

import numpy as np
import pytest
from sklearn.metrics import f1_score

from hotel_curator.ml.metrics import (
    bootstrap_macro_f1,
    label_counts,
    macro_f1,
    paired_bootstrap_macro_f1_diff,
    per_label_scores,
)


@pytest.fixture
def toy() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    y_true = rng.random((400, 5)) < 0.3
    noise = rng.random((400, 5)) < 0.15
    y_pred = np.logical_xor(y_true, noise)
    return y_true, y_pred


def test_macro_f1_matches_sklearn(toy: tuple[np.ndarray, np.ndarray]) -> None:
    y_true, y_pred = toy
    expected = f1_score(y_true, y_pred, average="macro", zero_division=0)
    assert macro_f1(y_true, y_pred) == pytest.approx(expected)


def test_per_label_scores_values() -> None:
    y_true = np.array([[1, 0], [1, 0], [0, 1], [0, 0]], dtype=bool)
    y_pred = np.array([[1, 0], [0, 0], [1, 1], [0, 0]], dtype=bool)
    scores = per_label_scores(y_true, y_pred, ["a", "b"])
    a, b = scores
    assert (a.label, a.support, a.tp, a.fp, a.fn) == ("a", 2, 1, 1, 1)
    assert a.precision == pytest.approx(0.5)
    assert a.recall == pytest.approx(0.5)
    assert a.f1 == pytest.approx(0.5)
    assert b.f1 == pytest.approx(1.0)


def test_label_without_support_or_predictions_is_excluded() -> None:
    y_true = np.array([[1, 0], [0, 0]], dtype=bool)
    y_pred = np.array([[1, 0], [0, 0]], dtype=bool)
    scores = per_label_scores(y_true, y_pred, ["a", "b"])
    assert np.isnan(scores[1].f1)
    assert macro_f1(y_true, y_pred) == pytest.approx(1.0)


def test_label_counts_are_tp_fp_fn_per_label() -> None:
    y_true = np.array([[1, 0, 1], [0, 1, 1]], dtype=bool)
    y_pred = np.array([[1, 1, 0], [0, 1, 1]], dtype=bool)
    counts = label_counts(y_true, y_pred)
    assert counts.shape == (3, 3)
    assert counts.tolist() == [[1, 0, 0], [1, 1, 0], [1, 0, 1]]


def test_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="shape"):
        macro_f1(np.zeros((2, 3), bool), np.zeros((2, 4), bool))


def test_bootstrap_ci_brackets_point_and_is_seeded(
    toy: tuple[np.ndarray, np.ndarray],
) -> None:
    y_true, y_pred = toy
    clusters = np.repeat(np.arange(40), 10)
    first = bootstrap_macro_f1(y_true, y_pred, clusters, n_boot=500, seed=1)
    second = bootstrap_macro_f1(y_true, y_pred, clusters, n_boot=500, seed=1)
    assert first == second
    assert first.low < first.point < first.high
    assert first.point == pytest.approx(macro_f1(y_true, y_pred))


def test_cluster_bootstrap_is_wider_than_item_bootstrap() -> None:
    rng = np.random.default_rng(3)
    n_clusters, per_cluster = 30, 20
    # Strongly clustered errors: whole clusters are either right or wrong.
    cluster_bad = rng.random(n_clusters) < 0.3
    y_true = rng.random((n_clusters * per_cluster, 3)) < 0.5
    flip = np.repeat(cluster_bad, per_cluster)[:, None] & (
        rng.random((n_clusters * per_cluster, 3)) < 0.8
    )
    y_pred = np.logical_xor(y_true, flip)
    clusters = np.repeat(np.arange(n_clusters), per_cluster)
    clustered = bootstrap_macro_f1(y_true, y_pred, clusters, n_boot=800)
    naive = bootstrap_macro_f1(y_true, y_pred, None, n_boot=800)
    assert (clustered.high - clustered.low) > (naive.high - naive.low)


def test_paired_diff_of_identical_models_is_zero(
    toy: tuple[np.ndarray, np.ndarray],
) -> None:
    y_true, y_pred = toy
    diff = paired_bootstrap_macro_f1_diff(
        y_true, y_pred, y_pred, None, n_boot=300
    )
    assert diff.point == 0.0
    assert diff.low == 0.0 and diff.high == 0.0
    assert diff.p_value == pytest.approx(1.0)


def test_paired_diff_detects_better_model(
    toy: tuple[np.ndarray, np.ndarray],
) -> None:
    y_true, y_pred = toy
    diff = paired_bootstrap_macro_f1_diff(
        y_true, y_true, y_pred, None, n_boot=300
    )
    assert diff.point > 0
    assert diff.low > 0
    assert diff.p_value < 0.01


def test_bootstrap_memory_is_chunked(monkeypatch: pytest.MonkeyPatch) -> None:
    import hotel_curator.ml.metrics as m

    seen: list[tuple[int, int]] = []
    original = m._resample_weights

    def spy(
        n_clusters: int, n_boot: int, rng: np.random.Generator
    ) -> np.ndarray:
        seen.append((n_boot, n_clusters))
        return original(n_clusters, n_boot, rng)

    monkeypatch.setattr(m, "_resample_weights", spy)
    y = np.random.default_rng(0).random((3000, 2)) < 0.3
    m.bootstrap_macro_f1(y, y, None, n_boot=1000)
    assert max(b for b, _ in seen) <= m.BOOT_CHUNK
    assert sum(b for b, _ in seen) == 1000
