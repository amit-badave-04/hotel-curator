"""``curator score``: score the corpus with the promoted model, tier hotels.

``parts`` runs the packaged ONNX model over every review part in chunks
(resumable: finished chunks are kept under ``var/scores/``) and writes
calibrated probabilities to ``data/processed/part_scores.parquet``.
``hotels`` turns those into tiers, explanations and quotes for every hotel
and writes the committed demo data the API serves.
"""

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from hotel_curator.config import Settings, get_settings
from hotel_curator.inference.locate import package_name, promoted_package

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt
    import pandas as pd

    from hotel_curator.data.manifest import FileRecord
    from hotel_curator.inference.package import ModelPackage
    from hotel_curator.scoring.aggregate import HotelEvidence
    from hotel_curator.scoring.policy import TierPolicy
    from hotel_curator.scoring.tiers import TierResult
    from hotel_curator.scoring.tiers_report import TierValidation

app = typer.Typer(help="Score the corpus and tier every hotel.")

CHUNK_PARTS = 20_000
DEMO_RESULTS = "hotel_results.json.gz"
DEMO_SIGNALS = "subset_signals.parquet"
SUBSET_PER_CITY = 5
STABILITY_REPLICATES = 20
SIMULATION_VOLUMES = (30, 60, 120, 300, 800)
TRAINING_SETS = ("train", "train_2")
"""Label sets a model may have trained on; their reviews never count."""

ScorePool = tuple["npt.NDArray[np.float64]", "npt.NDArray[np.float64]"]
PolicyVariant = Callable[["TierPolicy"], "TierPolicy"]


def training_review_ids(labels_dir: Path) -> set[str]:
    """Reviews in any training label set: their scores are in-sample.

    Both training sets are excluded whichever one the promoted model used,
    so the eligible reviews do not depend on the model choice.

    Raises:
        FileNotFoundError: If the first training set is missing (scores
            would then silently include in-sample predictions).
    """
    import pandas as pd

    if not (labels_dir / TRAINING_SETS[0] / "labels.parquet").is_file():
        raise FileNotFoundError(
            f"{labels_dir / TRAINING_SETS[0]} has no labels.parquet; run "
            "`curator label merge` so training reviews can be excluded"
        )
    ids: set[str] = set()
    for name in TRAINING_SETS:
        path = labels_dir / name / "labels.parquet"
        if path.is_file():
            frame = pd.read_parquet(path, columns=["review_id"])
            ids |= set(frame["review_id"].astype(str))
    return ids


@app.command("parts")
def score_parts_cmd(
    threads: Annotated[int, typer.Option(help="ONNX Runtime threads.")] = 4,
) -> None:
    """Calibrated label probabilities for every review part in the corpus."""
    import pandas as pd

    from hotel_curator.data.download import sha256_file
    from hotel_curator.data.manifest import file_record
    from hotel_curator.data.parts import corpus_parts
    from hotel_curator.inference.package import ModelPackage

    settings = get_settings()
    package = ModelPackage(promoted_package(settings), num_threads=threads)
    reviews = pd.read_parquet(
        settings.processed_dir / "reviews.parquet",
        columns=["review_id", "liked_text", "disliked_text"],
    )
    parts = corpus_parts(reviews).sort_values(["review_id", "part"])
    parts = parts.reset_index(drop=True)
    # Chunks are reused only for the same weights and the same corpus.
    corpus = sha256_file(settings.processed_dir / "reviews.parquet")
    weights = sha256_file(package.directory / "model.onnx")
    key = f"{package.name}-{weights[:12]}-{corpus[:12]}"
    chunk_dir = settings.work_dir / "scores" / key
    started = time.perf_counter()
    paths = _score_chunks(package, parts, chunk_dir)
    seconds = time.perf_counter() - started
    scores = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    _check_same_parts(scores, parts)
    out = settings.processed_dir / "part_scores.parquet"
    scores.to_parquet(out, index=False)
    _write_scores_manifest(
        settings, package, file_record(out, settings.home, len(parts))
    )
    typer.echo(f"scored in {seconds:,.0f} s with {threads} threads")
    typer.echo(f"wrote {out} ({len(parts):,} parts)")


def _score_chunks(
    package: "ModelPackage", parts: "pd.DataFrame", chunk_dir: Path
) -> list[Path]:
    """Score parts chunk by chunk, skipping chunks already on disk."""
    import pandas as pd

    from hotel_curator.taxonomy import LABELS

    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for start in range(0, len(parts), CHUNK_PARTS):
        path = chunk_dir / f"chunk_{start // CHUNK_PARTS:04d}.parquet"
        paths.append(path)
        if path.is_file():
            continue
        chunk = parts.iloc[start : start + CHUNK_PARTS]
        probs = package.predict(
            chunk["text"].tolist(), chunk["part"].tolist()
        ).probabilities
        frame = chunk[["review_id", "part"]].reset_index(drop=True)
        scores = pd.DataFrame(probs.astype("float32"), columns=list(LABELS))
        pd.concat([frame, scores], axis=1).to_parquet(path, index=False)
        typer.echo(f"{path.name}: {start + len(chunk):,}/{len(parts):,}")
    return paths


def _check_same_parts(scores: "pd.DataFrame", parts: "pd.DataFrame") -> None:
    """Scored rows must be exactly the corpus parts, in order.

    Raises:
        ValueError: If a reused chunk does not match the current parts.
    """
    keys = ["review_id", "part"]
    if (
        not scores[keys]
        .reset_index(drop=True)
        .equals(parts[keys].reset_index(drop=True))
    ):
        raise ValueError(
            "scored parts differ from the corpus; delete the chunk directory"
        )


def _shown_path(path: Path, home: Path) -> str:
    """``path`` relative to the project home when inside it, else absolute."""
    resolved, root = path.resolve(), home.resolve()
    if resolved.is_relative_to(root):
        return str(resolved.relative_to(root))
    return str(resolved)


def _write_scores_manifest(
    settings: Settings, package: "ModelPackage", record: "FileRecord"
) -> None:
    # Timings stay out of the manifest: a resumed run times only the chunks
    # it scored, and the manifest must not change when nothing else did.
    from hotel_curator.data.manifest import new_manifest

    manifest = new_manifest("scores", settings.home)
    manifest.inputs = {
        "model_package": _shown_path(package.directory, settings.home),
        "model_weights": str(package.metadata.get("weights")),
    }
    manifest.outputs = [record]
    manifest.stats = {"parts": record.rows}
    manifest.write(settings.manifests_dir / "scores.json")


@dataclass(frozen=True)
class _Corpus:
    """The processed tables every hotel-level command reads."""

    policy: "TierPolicy"
    hotels: "pd.DataFrame"
    reviews: "pd.DataFrame"
    part_probs: "pd.DataFrame"
    training_ids: set[str]


def _load_corpus(settings: Settings) -> _Corpus:
    import pandas as pd

    from hotel_curator.scoring.policy import TierPolicy

    processed = settings.processed_dir
    return _Corpus(
        policy=TierPolicy.load(settings.resolved_tier_policy),
        hotels=pd.read_parquet(processed / "hotels.parquet"),
        reviews=pd.read_parquet(processed / "reviews.parquet"),
        part_probs=pd.read_parquet(processed / "part_scores.parquet"),
        training_ids=training_review_ids(settings.labels_dir),
    )


@app.command("hotels")
def score_hotels_cmd() -> None:
    """Tiers, explanations and quotes for every hotel; write demo data."""
    from hotel_curator.db.seed import write_results
    from hotel_curator.scoring.evidence import Redactor
    from hotel_curator.scoring.pipeline import ScoringInputs, score_hotels

    settings = get_settings()
    corpus = _load_corpus(settings)
    package = package_name(promoted_package(settings))
    started = time.perf_counter()
    results = score_hotels(
        ScoringInputs(
            hotels=corpus.hotels,
            reviews=corpus.reviews,
            part_probs=corpus.part_probs,
            training_review_ids=corpus.training_ids,
            model_name=package,
        ),
        corpus.policy,
        Redactor(),
    )
    seconds = time.perf_counter() - started
    settings.demo_dir.mkdir(parents=True, exist_ok=True)
    demo = settings.demo_dir / DEMO_RESULTS
    signals = settings.demo_dir / DEMO_SIGNALS
    write_results(demo, results)
    outputs = {
        demo: len(results),
        signals: _write_demo_signals(signals, corpus),
    }
    tiers = dict(Counter(r.tier.value for r in results))
    stats = {"hotels": len(results), "tiers": tiers, "seconds": seconds}
    _write_results_manifest(settings, corpus.policy, package, outputs, stats)
    typer.echo(f"wrote {demo}: {len(results)} hotels; tiers {tiers}")


def _write_demo_signals(path: Path, corpus: _Corpus) -> int:
    """Review signals for a small per-city hotel subset; returns rows."""
    from hotel_curator.scoring.aggregate import eligible_reviews, review_signals

    subset_ids = (
        corpus.hotels.sort_values("hotel_id")
        .groupby("city")
        .head(SUBSET_PER_CITY)["hotel_id"]
    )
    reviews = corpus.reviews[corpus.reviews["hotel_id"].isin(subset_ids)]
    eligible = eligible_reviews(reviews, corpus.training_ids, corpus.policy)
    signals = review_signals(eligible, corpus.part_probs, corpus.policy)
    signals["month"] = signals["month"].astype(str)
    signals.to_parquet(path, index=False)
    return len(signals)


def _write_results_manifest(
    settings: Settings,
    policy: "TierPolicy",
    model_name: str,
    outputs: dict[Path, int],
    stats: dict[str, object],
) -> None:
    from hotel_curator.data.manifest import file_record, new_manifest

    manifest = new_manifest("hotel_results", settings.home)
    manifest.inputs = {
        "params_version": policy.params_version,
        "model_package": model_name,
    }
    manifest.outputs = [
        file_record(path, settings.home, rows) for path, rows in outputs.items()
    ]
    manifest.stats = stats
    manifest.write(settings.manifests_dir / "hotel_results.json")


def _dev_pools(settings: Settings, attribute: str) -> dict[str, ScorePool]:
    """Calibrated review-level gold-dev scores split by gold label.

    Returns:
        Per polarity, the scores of reviews that do and do not carry the
        label, for the false-claim simulation.

    Raises:
        ValueError: If saved scores and gold labels cover different reviews.
    """
    import numpy as np
    import pandas as pd

    from hotel_curator.ml.calibration import IsotonicCalibrator
    from hotel_curator.ml.data import load_label_set
    from hotel_curator.ml.evaluate import noisy_or, review_level
    from hotel_curator.taxonomy import LABELS

    eval_dir = settings.artifacts_dir / "evals"
    eval_dir /= package_name(promoted_package(settings))
    scores = pd.read_parquet(eval_dir / "scores_gold_dev.parquet")
    calibrator = IsotonicCalibrator.from_dict(
        json.loads((eval_dir / "calibration.json").read_text())
    )
    dev = load_label_set(settings.labels_dir, "gold_dev").frame
    merged = dev[["review_id", "part"]].merge(scores, on=["review_id", "part"])
    ids, probs = noisy_or(
        merged["review_id"],
        calibrator.transform(merged[list(LABELS)].to_numpy()),
    )
    truth_ids, truth = review_level(
        dev["review_id"], dev[list(LABELS)].to_numpy(bool)
    )
    if not np.array_equal(ids, truth_ids):
        raise ValueError("gold-dev scores and labels cover different reviews")
    pools = {}
    for polarity in ("positive", "negative"):
        k = LABELS.index(f"{attribute}_{polarity}")
        pools[polarity] = (probs[truth[:, k], k], probs[~truth[:, k], k])
    return pools


def policy_variants() -> dict[str, PolicyVariant]:
    """The sensitivity variants ADR 006 reports (never used to tier)."""

    def strong_line(value: float) -> PolicyVariant:
        def change(p: "TierPolicy") -> "TierPolicy":
            two_sided = p.two_sided.model_copy(update={"strong": value})
            return p.model_copy(update={"two_sided": two_sided})

        return change

    def materiality(value: float) -> PolicyVariant:
        return lambda p: p.model_copy(update={"materiality": value})

    return {
        "strong line 0.75": strong_line(0.75),
        "strong line 0.85": strong_line(0.85),
        "materiality 2.5%": materiality(0.025),
        "materiality 10%": materiality(0.10),
    }


@app.command("validate")
def validate_cmd() -> None:
    """Run the ADR 006 validation and write docs/model/TIERS.md."""
    from hotel_curator.scoring.tiers_report import tiers_markdown

    settings = get_settings()
    validation = _run_validation(settings)
    doc = settings.home / "docs" / "model" / "TIERS.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(tiers_markdown(validation))
    typer.echo(f"wrote {doc}")


@dataclass(frozen=True)
class _Tiered:
    """The corpus run through the tier rules once."""

    eligible: "pd.DataFrame"
    signals: "pd.DataFrame"
    evidence: dict[str, "HotelEvidence"]
    results: dict[str, "TierResult"]


def _tier_corpus(c: _Corpus) -> _Tiered:
    from hotel_curator.scoring.aggregate import (
        eligible_reviews,
        hotel_evidence,
        review_signals,
    )
    from hotel_curator.scoring.tiers import assign_tier

    eligible = eligible_reviews(c.reviews, c.training_ids, c.policy)
    signals = review_signals(eligible, c.part_probs, c.policy)
    evidence = hotel_evidence(signals, c.policy)
    results = {
        h: assign_tier(ev.counts, ev.reviews, ev.spread, c.policy)
        for h, ev in evidence.items()
    }
    return _Tiered(eligible, signals, evidence, results)


def _run_validation(settings: Settings) -> "TierValidation":
    """Every ADR 006 check on the full corpus, timed."""
    import pandas as pd

    from hotel_curator.scoring.aggregate import NEG, POS
    from hotel_curator.scoring.tiers_report import TierValidation
    from hotel_curator.scoring.validation import (
        external_agreement_table,
        out_of_time,
        sensitivity,
        simulate_false_claims,
        stability,
    )

    started = time.perf_counter()
    c = _load_corpus(settings)
    t = _tier_corpus(c)
    test_ids = set(c.hotels.loc[c.hotels["split"] == "test", "hotel_id"])
    oot_rows, oot_table = out_of_time(t.signals, t.eligible, test_ids, c.policy)
    tiers = {h: r.tier for h, r in t.results.items()}
    room = t.signals[f"{POS}_room"] + t.signals[f"{NEG}_room"]
    mention = float((room >= c.policy.hard_label_probability).mean())
    pools = _dev_pools(settings, "room")
    volumes = list(SIMULATION_VOLUMES)
    return TierValidation(
        policy=c.policy,
        results=t.results,
        sizes=pd.Series({h: ev.reviews for h, ev in t.evidence.items()}),
        oot_rows=oot_rows,
        oot_table=oot_table,
        agreement=external_agreement_table(
            t.results, t.signals, t.eligible, c.hotels, c.policy
        ),
        stable=stability(
            t.signals, tiers, c.policy, replicates=STABILITY_REPLICATES
        ),
        simulation=simulate_false_claims(pools, volumes, mention, c.policy),
        mention_rate=mention,
        sensitivity=sensitivity(t.evidence, c.policy, policy_variants()),
        replicates=STABILITY_REPLICATES,
        seconds=time.perf_counter() - started,
        test_hotels=len(test_ids),
    )
