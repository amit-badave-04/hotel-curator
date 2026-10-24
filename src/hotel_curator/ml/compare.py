"""Compare every rung with v1 and apply the written promotion rule.

Inputs are the committed ``artifacts/evals/<rung>/`` directories, so the
comparison can be rebuilt without retraining anything. The rule (plan §3.6,
with its clarifications) is applied mechanically:

1. review-level macro-F1 on gold-test beats v1, and the 95% interval of
   the paired hotel-cluster bootstrap difference lies above 0;
2. among the rungs that pass rule 1, the best gold-dev macro-F1 is the
   candidate; rungs within 0.01 of the best count as tied and the smallest
   of them is the candidate;
3. the candidate's mean review-level ECE on gold-test is at most 0.05;
4. the candidate's package scores the corpus in under 4 hours (measured by
   ``curator model package``).

The candidate ships only if it also passes rules 3 and 4; otherwise no model
is promoted (the plan's rules are conjunctive). What ships is the candidate's
ONNX package (possibly int8), so rules 1 and 3 are checked again on the
package's own gold-test scores ("package holds"); this check can only block
a promotion. It was added before any package had been scored. v1's own
configuration is the v1 run with the best gold-dev macro-F1.
"""

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from hotel_curator.ml.experiment import (
    PACKAGE_SCORES,
    load_review_level_predictions,
    rung_rules_sha256,
)
from hotel_curator.ml.metrics import PairedDiff, paired_bootstrap_macro_f1_diff
from hotel_curator.taxonomy import LABELS

MAX_MEAN_ECE = 0.05
TIE_WINDOW = 0.01
MAX_CORPUS_HOURS = 4.0
SIZE_MILLIONS = {
    "tfidf": 1,
    "embedding": 33,
    "v1": 125,
    "electra-small": 14,
    "roberta-base": 125,
    "mmbert-small": 140,
}
"""Approximate parameter counts used only to break rule-2 ties."""
RUN_NOTES = "NOTES.md"
"""Optional hand-written note in a rung's eval directory, shown verbatim
(e.g. how a run differed from the others)."""


@dataclass
class RungRow:
    """One line of the comparison table."""

    name: str
    family: str
    size_millions: int
    summary: dict[str, Any]
    train_sets: str = "train"
    notes: str = ""
    vs_v1: PairedDiff | None = None
    corpus_hours: float | None = None
    package: dict[str, Any] | None = None
    """Weights and gold-test metrics of the packaged model, once packaged."""
    package_vs_v1: PairedDiff | None = None
    rules: dict[str, bool | None] = field(default_factory=dict)
    """Rule outcomes; ``None`` when not measured (rule 4 and the package
    check before packaging)."""

    @property
    def dev_f1(self) -> float:
        """Gold-dev review-level macro-F1 (rule 2)."""
        return float(self.summary["dev_review_macro_f1"])


@dataclass
class Decision:
    """Outcome of the promotion rule."""

    v1_config: str
    winner: str | None
    eligible: list[str]
    """Rungs passing rule 1: the pool rule 2 picks from."""
    rows: list[RungRow]
    notes: list[str] = field(default_factory=list)
    test_reviews: int = 0
    test_hotels: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible view."""
        return {
            "v1_config": self.v1_config,
            "winner": self.winner,
            "eligible": self.eligible,
            "notes": self.notes,
            "test_reviews": self.test_reviews,
            "test_hotels": self.test_hotels,
            "rows": [
                {
                    **asdict(row),
                    "vs_v1": asdict(row.vs_v1) if row.vs_v1 else None,
                    "package_vs_v1": asdict(row.package_vs_v1)
                    if row.package_vs_v1
                    else None,
                }
                for row in self.rows
            ],
        }


def _size(summary: dict[str, Any], params: dict[str, Any]) -> int:
    base = params.get("base_model")
    if isinstance(base, str) and base in SIZE_MILLIONS:
        return SIZE_MILLIONS[base]
    return SIZE_MILLIONS.get(str(summary["family"]), 1000)


def load_rows(evals_dir: Path) -> list[RungRow]:
    """Every rung with a ``report.json`` under ``evals_dir``."""
    rows = []
    for report in sorted(evals_dir.glob("*/report.json")):
        payload = json.loads(report.read_text())
        summary = payload["summary"]
        throughput = report.parent / "throughput.json"
        hours = (
            float(json.loads(throughput.read_text())["corpus_hours"])
            if throughput.is_file()
            else None
        )
        rows.append(
            RungRow(
                name=summary["name"],
                family=summary["family"],
                size_millions=_size(summary, payload.get("params", {})),
                summary=summary,
                train_sets=str(
                    payload.get("params", {}).get(
                        "train_sets",
                        "v1 data" if summary["family"] == "v1" else "train",
                    )
                ),
                notes=_read_notes(report.parent / RUN_NOTES),
                corpus_hours=hours,
                package=_package_summary(report.parent),
            )
        )
    return rows


def _read_notes(path: Path) -> str:
    return path.read_text().strip() if path.is_file() else ""


def _package_summary(rung_dir: Path) -> dict[str, Any] | None:
    """Weights, gold-test headline and fingerprints of a packaged rung.

    ``current`` is False when the package embeds other thresholds or
    calibration than the rung's saved ones (e.g. after ``reassess``): such
    a package is not judged, so it cannot be promoted.
    """
    path = rung_dir / "package_report.json"
    if not path.is_file():
        return None
    report = json.loads(path.read_text())
    test = report["gold_test"]
    return {
        "weights": report["weights"],
        "dev_review_macro_f1": report["dev_review_macro_f1"],
        "max_quantization_loss": report.get("max_quantization_loss"),
        "test_review_macro_f1": test["review_macro_f1"],
        "test_mean_review_ece": float(
            np.mean(list(test["review_ece"].values()))
        ),
        "files_sha256": report.get("files_sha256", []),
        "current": report.get("rules_sha256") == rung_rules_sha256(rung_dir),
    }


def gold_reviews(gold_parts: pd.DataFrame) -> tuple[pd.Index, np.ndarray]:
    """Gold review ids (sorted) and their hotel ids."""
    hotels = gold_parts.groupby("review_id")["hotel_id"].first().sort_index()
    return hotels.index, hotels.to_numpy()


def _test_predictions(
    evals_dir: Path, review_ids: pd.Index, scores: str = "scores"
) -> Callable[[str], np.ndarray]:
    """Loader of a rung's review-level gold-test labels in a fixed order.

    ``scores`` selects the rung's own scores or its package's.
    """

    def load(name: str) -> np.ndarray:
        ids, labels, _ = load_review_level_predictions(
            evals_dir / name, "gold_test", scores=scores
        )
        order = pd.Index(ids).get_indexer(review_ids)
        if (order < 0).any():
            raise ValueError(
                f"{name}: {int((order < 0).sum())} gold-test reviews have no "
                "prediction"
            )
        return labels[order]

    return load


def _beats_v1(diff: PairedDiff | None) -> bool:
    """Rule 1: better than v1, with the paired interval above 0."""
    return diff is not None and diff.point > 0 and diff.low > 0


def _rules(row: RungRow) -> dict[str, bool | None]:
    """Rules 1, 3 and 4, and rules 1 and 3 again on the package.

    Rule 4 and the package check are ``None`` until the rung is packaged;
    the package check is also ``None`` for a stale package (other
    thresholds or calibration than the rung's).
    """
    hours = row.corpus_hours
    package = row.package
    package_holds: bool | None = None
    if package is not None and package["current"] and row.package_vs_v1:
        package_holds = (
            _beats_v1(row.package_vs_v1)
            and package["test_mean_review_ece"] <= MAX_MEAN_ECE
        )
    return {
        "1_beats_v1": _beats_v1(row.vs_v1),
        "3_calibrated": row.summary["test_mean_review_ece"] <= MAX_MEAN_ECE,
        "4_fast_enough": None if hours is None else hours < MAX_CORPUS_HOURS,
        "package_holds": package_holds,
    }


def _pick_winner(pool: list[RungRow], decision: Decision) -> None:
    """Rule 2 among rule-1 passers, then rules 3 and 4 for the candidate."""
    best = max(r.dev_f1 for r in pool)
    tied = [r for r in pool if r.dev_f1 >= best - TIE_WINDOW]
    candidate = min(tied, key=lambda r: (r.size_millions, -r.dev_f1))
    if len(tied) > 1:
        decision.notes.append(
            f"Rule 2 tie within {TIE_WINDOW}: "
            + ", ".join(f"{r.name} ({r.dev_f1:.4f})" for r in tied)
            + f"; {candidate.name} is the candidate (smallest, then best "
            "on gold-dev)."
        )
        decision.notes.extend(_speed_note(tied, candidate))
    failed = {
        rule: "not measured" if candidate.rules[rule] is None else "no"
        for rule in ("3_calibrated", "4_fast_enough", "package_holds")
        if not candidate.rules[rule]
    }
    if failed:
        listed = " and ".join(f"{k} ({v})" for k, v in failed.items())
        decision.notes.append(
            f"Rule 2 picks {candidate.name}, which does not pass {listed}, "
            "so no model is promoted."
        )
    else:
        decision.winner = candidate.name


def _speed_note(tied: list[RungRow], winner: RungRow) -> list[str]:
    """Say so when "faster" would break the tie differently from "smaller".

    The plan says ties go to "the smaller, faster model"; the rule as coded
    before any result uses parameter count. The decision stands either way;
    this only makes a disagreement between the two readings visible.
    """
    timed = [r for r in tied if r.corpus_hours is not None]
    if len(timed) < 2:
        return []
    fastest = min(timed, key=lambda r: float(r.corpus_hours or 0.0))
    if fastest.name == winner.name:
        return []
    hours = ", ".join(f"{r.name} {r.corpus_hours:.2f} h" for r in timed)
    return [
        f"By measured speed ({hours} for the corpus) {fastest.name} would "
        f"win the tie; by parameter count ({winner.size_millions}M vs "
        f"{fastest.size_millions}M) {winner.name} does. The rule as coded "
        "before results breaks ties by parameter count."
    ]


def decide(
    evals_dir: Path, gold_test: pd.DataFrame, *, n_boot: int, seed: int
) -> Decision:
    """Apply the promotion rule to every rung in ``evals_dir``.

    Raises:
        ValueError: If no v1 run is present.
    """
    rows = load_rows(evals_dir)
    v1_rows = [r for r in rows if r.family == "v1"]
    if not v1_rows:
        raise ValueError("no v1 evaluation found; run `curator model eval-v1`")
    v1 = max(v1_rows, key=lambda r: r.dev_f1)
    _paired_against_v1(evals_dir, gold_test, v1.name, rows, n_boot, seed)
    pool = [r for r in rows if r.rules.get("1_beats_v1")]
    decision = Decision(
        v1.name,
        None,
        [r.name for r in pool],
        rows,
        test_reviews=int(gold_test["review_id"].nunique()),
        test_hotels=int(gold_test["hotel_id"].nunique()),
    )
    decision.notes.extend(
        f"The package of {r.name} embeds other thresholds or calibration "
        "than its current evaluation, so it is not judged; run `curator "
        f"model package {r.name}` again."
        for r in rows
        if r.package is not None and not r.package["current"]
    )
    if pool:
        _pick_winner(pool, decision)
    return decision


def _paired_against_v1(
    evals_dir: Path,
    gold_test: pd.DataFrame,
    v1_name: str,
    rows: list[RungRow],
    n_boot: int,
    seed: int,
) -> None:
    """Paired bootstrap of every rung, and of its package, against v1."""
    review_ids, hotels = gold_reviews(gold_test)
    truth = (
        gold_test.groupby("review_id")[list(LABELS)]
        .any()
        .reindex(review_ids)
        .to_numpy(dtype=bool)
    )
    own = _test_predictions(evals_dir, review_ids)
    packaged = _test_predictions(evals_dir, review_ids, PACKAGE_SCORES)
    v1_pred = own(v1_name)

    def against_v1(pred: np.ndarray) -> PairedDiff:
        return paired_bootstrap_macro_f1_diff(
            truth, pred, v1_pred, hotels, n_boot=n_boot, seed=seed
        )

    for row in (r for r in rows if r.family != "v1"):
        row.vs_v1 = against_v1(own(row.name))
        scored = evals_dir / row.name / f"{PACKAGE_SCORES}_gold_test.parquet"
        package = row.package
        if package is not None and package["current"] and scored.is_file():
            row.package_vs_v1 = against_v1(packaged(row.name))
        row.rules = _rules(row)


def _number(value: float, spec: str) -> str:
    """A table cell; "-" where the value is undefined (NaN)."""
    return "-" if math.isnan(value) else format(value, spec)


def _ci(point: float, low: float, high: float) -> str:
    return f"{point:.3f} [{low:.3f}, {high:.3f}]"


def _main_table(decision: Decision) -> list[str]:
    lines = [
        "| Rung | Family | Trained on | Dev macro-F1 | "
        "Test macro-F1 [95% CI] | Test minus v1 [95% CI] | Micro-F1 | "
        "Review ECE | Cross-polarity F1 | Train s | Rules 1/3/4/P |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    ordered = sorted(
        decision.rows, key=lambda r: -r.summary["test_review_macro_f1"]
    )
    for row in ordered:
        s = row.summary
        diff = (
            _ci(row.vs_v1.point, row.vs_v1.low, row.vs_v1.high)
            if row.vs_v1
            else ("reference" if row.name == decision.v1_config else "-")
        )
        rules = (
            "/".join(
                "-" if v is None else "yes" if v else "no"
                for v in row.rules.values()
            )
            if row.rules
            else "-"
        )
        name = f"**{row.name}**" if row.name == decision.winner else row.name
        lines.append(
            f"| {name} | {row.family} | {_trained_on(row)} | "
            f"{row.dev_f1:.3f} | "
            + _ci(
                s["test_review_macro_f1"],
                s["test_review_macro_f1_low"],
                s["test_review_macro_f1_high"],
            )
            + f" | {diff} | {s['test_review_micro_f1']:.3f} | "
            f"{s['test_mean_review_ece']:.3f} | "
            f"{_number(s['test_cross_polarity_f1'], '.3f')} | "
            f"{_number(s.get('seconds_train', math.nan), '.0f')} | {rules} |"
        )
    return lines


def _per_label_table(evals_dir: Path, names: list[str]) -> list[str]:
    reports = {
        n: json.loads((evals_dir / n / "report.json").read_text())["test"]
        for n in names
    }
    lines = [
        "| Label | Support | " + " | ".join(names) + " |",
        "|---|---:|" + "---:|" * len(names),
    ]
    for k, label in enumerate(LABELS):
        support = reports[names[0]]["per_label"][k]["support"]
        cells = [f"{reports[n]['per_label'][k]['f1']:.3f}" for n in names]
        lines.append(f"| {label} | {support} | " + " | ".join(cells) + " |")
    return lines


def _slice_table(evals_dir: Path, name: str, v1: str) -> list[str]:
    def slices(rung: str) -> dict[str, Any]:
        test = json.loads((evals_dir / rung / "report.json").read_text())
        return {s["name"]: s for s in test["test"]["slices"]}

    ours, theirs = slices(name), slices(v1)
    lines = [
        f"| Slice | Items | {name} | {v1} |",
        "|---|---:|---:|---:|",
    ]
    for key, item in ours.items():
        other = theirs.get(key)
        mf = item["macro_f1"]
        lines.append(
            f"| {key} | {item['n_items']} | "
            f"{_ci(mf['point'], mf['low'], mf['high'])} | "
            + (
                _ci(
                    other["macro_f1"]["point"],
                    other["macro_f1"]["low"],
                    other["macro_f1"]["high"],
                )
                if other
                else "-"
            )
            + " |"
        )
    return lines


def _calibration_table(evals_dir: Path, name: str) -> list[str]:
    test = json.loads((evals_dir / name / "report.json").read_text())["test"]
    lines = [
        "| Label | Review ECE | Predicted share | Observed share |",
        "|---|---:|---:|---:|",
    ]
    for label in LABELS:
        expected, observed = test["calibration_in_the_large"][label]
        lines.append(
            f"| {label} | {test['review_ece'][label]:.3f} | "
            f"{expected:.1%} | {observed:.1%} |"
        )
    return lines


def _trained_on(row: RungRow) -> str:
    if row.family == "v1":
        return "v1 data"
    return "train + train_2" if "train_2" in row.train_sets else "train"


def _comparison_intro(decision: Decision) -> list[str]:
    verdict = (
        f"promote `{decision.winner}`"
        if decision.winner
        else "no rung is promoted"
    )
    return [
        "# Model comparison and promotion decision",
        "",
        "_Generated by `curator model compare` from `artifacts/evals/`. "
        "Do not edit by hand._",
        "",
        "Every rung was trained on the label sets shown in the table "
        "(`train`: the first training sample; `train_2`: the second sample, "
        "plan section 3.2), tuned and calibrated on gold-dev (the "
        "`v1-*-v1-thresholds` rows keep v1's shipped thresholds; only their "
        "calibration is fitted on gold-dev), and scored once "
        f"on gold-test ({decision.test_reviews:,} reviews from "
        f"{decision.test_hotels:,} hotels the models never saw). "
        'Intervals are 95% hotel-cluster bootstrap intervals; "test minus '
        "v1\" is a paired bootstrap against v1's best configuration on "
        f"gold-dev (`{decision.v1_config}`). Rules are those of the plan, "
        "§3.6, applied together: 1 beats v1, with the paired interval above "
        "0; 2 among the rungs passing 1, the best gold-dev macro-F1 is the "
        f"candidate (ties within {TIE_WINDOW} go to the smaller model); the "
        "candidate ships only if it also passes 3, mean review-level ECE <= "
        f"{MAX_MEAN_ECE}, and 4, scoring the corpus in under "
        f"{MAX_CORPUS_HOURS:g} hours. Rule 4 is measured on the packaged ONNX "
        "model (`curator model package`); P re-checks rules 1 and 3 on that "
        "package's own gold-test scores, since the package is what is "
        "served. `-` in the rules column means not measured.",
        "",
        f"**Decision: {verdict}.**",
        "",
        *[f"- {note}" for note in decision.notes],
        "",
        "## All rungs (gold-test, review level)",
        "",
        *_main_table(decision),
        "",
        *_package_table(decision),
    ]


def _package_table(decision: Decision) -> list[str]:
    packaged = [(r, r.package) for r in decision.rows if r.package is not None]
    if not packaged:
        return []
    tolerance = packaged[0][1]["max_quantization_loss"]
    return [
        "## Packaged models (gold-test, review level)",
        "",
        "The ONNX package of each packaged rung, scored by the same harness. "
        "int8 weights ship only if they lose at most "
        f"{tolerance} review-level gold-dev macro-F1 against fp32 (the rule "
        "uses unrounded scores; the loss column shows them). A "
        "package's gold-dev score can sit slightly below its rung's: each "
        "threshold was tuned on gold-dev and equals one gold-dev score "
        "exactly, so differences of about 1e-5 between ONNX Runtime and "
        "PyTorch flip those ties. Gold-test has no such ties.",
        "",
        "| Rung | Weights | Dev macro-F1 fp32 / int8 | int8 loss | "
        "Test macro-F1 [95% CI] | Test minus v1 [95% CI] | Review ECE | "
        "Corpus hours |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
        *(
            _package_line(row, package)
            for row, package in sorted(packaged, key=lambda rp: rp[0].name)
        ),
        "",
    ]


def _package_line(row: RungRow, package: dict[str, Any]) -> str:
    dev = package["dev_review_macro_f1"]
    f1 = package["test_review_macro_f1"]
    d = row.package_vs_v1
    diff = "-" if d is None else _ci(d.point, d.low, d.high)
    hours = "-" if row.corpus_hours is None else f"{row.corpus_hours:.2f}"
    weights = package["weights"] + ("" if package["current"] else " (stale)")
    return (
        f"| {row.name} | {weights} | "
        f"{dev['fp32']:.3f} / {dev['int8']:.3f} | "
        f"{dev['fp32'] - dev['int8']:.5f} | "
        f"{_ci(f1['point'], f1['low'], f1['high'])} | {diff} | "
        f"{package['test_mean_review_ece']:.3f} | {hours} |"
    )


def _winner_sections(decision: Decision, evals_dir: Path) -> list[str]:
    winner = decision.winner
    if not winner:
        return []
    tfidf = "tfidf-logreg"
    names = [winner, decision.v1_config]
    if (evals_dir / tfidf).is_dir() and tfidf != winner:
        names.append(tfidf)
    return [
        "## Per-label F1 (gold-test, review level)",
        "",
        *_per_label_table(evals_dir, names),
        "",
        "## Slices (gold-test, macro-F1 [95% CI])",
        "",
        "`part=` rows are scored per part (each Liked or Disliked text is one "
        "item); the other rows are per review. Within a part slice, labels "
        "of the other polarity have almost no gold positives, so these rows "
        "are not comparable with the review-level headline. As everywhere in "
        "this report, a label with no gold positives in a slice counts "
        "towards macro-F1 only if the model predicts it (F1 0), so a model "
        "that predicts absent labels is penalised.",
        "",
        *_slice_table(evals_dir, winner, decision.v1_config),
        "",
        f"## Calibration of `{winner}` (gold-test)",
        "",
        "Review-level probabilities are the noisy-OR of calibrated part "
        "probabilities. Predicted and observed shares are weighted by each "
        "review's inverse-inclusion sampling weight, so both estimate the "
        "corpus rate although gold-test over-samples rare topics.",
        "",
        *_calibration_table(evals_dir, winner),
        "",
    ]


def _notes_section(decision: Decision) -> list[str]:
    noted = [r for r in decision.rows if r.notes]
    if not noted:
        return []
    lines = ["## Run notes", ""]
    for row in sorted(noted, key=lambda r: r.name):
        lines += [f"- `{row.name}`: {row.notes}"]
    return [*lines, ""]


def comparison_markdown(decision: Decision, evals_dir: Path) -> str:
    """The committed ``docs/model/COMPARISON.md``."""
    lines = (
        _comparison_intro(decision)
        + _notes_section(decision)
        + _winner_sections(decision, evals_dir)
    )
    return "\n".join(lines)
