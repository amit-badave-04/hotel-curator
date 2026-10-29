"""``curator model``: fetch base encoders, run the ladder, compare, package.

Every ``run-*`` command trains on ``data/labels/train``, tunes thresholds and
calibration on gold-dev, scores gold-dev and gold-test with the shared
harness, and writes ``artifacts/evals/<rung>/`` (see ``ml.experiment``).
"""

import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from hotel_curator.config import Settings, get_settings

if TYPE_CHECKING:
    import pandas as pd

    from hotel_curator.ml.data import LabeledParts
    from hotel_curator.ml.encoder import EncoderConfig, TrainResult
    from hotel_curator.ml.evaluate import EvalConfig
    from hotel_curator.ml.experiment import FloatMatrix, RungOutcome
    from hotel_curator.ml.v1 import V1Predictor

ReviewScores = tuple[list[str], "FloatMatrix"]
"""Review ids and their raw scores, same order."""

app = typer.Typer(help="Model ladder: fetch, train, evaluate and package.")

ENCODER_DEFAULTS: dict[str, dict[str, float | int]] = {
    "roberta-base": {"learning_rate": 3e-5, "epochs": 4, "batch_size": 32},
    "electra-small": {"learning_rate": 1e-4, "epochs": 5, "batch_size": 32},
    "mmbert-small": {"learning_rate": 5e-5, "epochs": 4, "batch_size": 32},
}
"""Pre-specified hyperparameters per base model (from the models' papers'
fine-tuning recipes, not tuned on gold data)."""


ExtraTrain = Annotated[
    bool,
    typer.Option(
        help="Also train on the second sample (train_2); the rung name gets"
        " the suffix -train2."
    ),
]
BaseKey = Annotated[str, typer.Argument(help="Base model key.")]
RungName = Annotated[
    str | None,
    typer.Option(
        help="Rung name. Default: <base>-ft, a tag for each setting that"
        " differs from its default (-frac0.5, -seed7, -lr2e-05, -ep3), then"
        " -train2."
    ),
]
Seed = Annotated[int | None, typer.Option(help="Training seed.")]
TrainFraction = Annotated[
    float, typer.Option(help="Share of training reviews (learning curve).")
]
BaseDir = Annotated[
    Path | None,
    typer.Option(
        help="Local Hugging Face model directory, for a base model that is "
        "not in the pinned registry (owner action, BLOCKERS B1)."
    ),
]
LearningRate = Annotated[
    float | None, typer.Option(help="Override the learning rate.")
]
Epochs = Annotated[int | None, typer.Option(help="Override the epochs.")]


EXTRA_TRAIN_SUFFIX = "-train2"
VARIANT_TAGS = (
    ("train_fraction", "frac"),
    ("seed", "seed"),
    ("learning_rate", "lr"),
    ("epochs", "ep"),
)
"""Encoder run settings that change the default rung name, and their tags."""


def rung_name(base_name: str, *, extra: bool) -> str:
    """A rung's name; runs that also used ``train_2`` get a suffix.

    Keeps a run on more data from overwriting the same model's results on
    the first training sample.
    """
    return f"{base_name}{EXTRA_TRAIN_SUFFIX}" if extra else base_name


def encoder_rung_name(
    base: str,
    run: dict[str, float | int],
    defaults: dict[str, float | int],
    *,
    extra: bool,
) -> str:
    """Default name of an encoder rung: one tag per non-default setting.

    ``<base>-ft``, then ``-<tag><value>`` for every setting of
    :data:`VARIANT_TAGS` whose ``run`` value differs from ``defaults``, then
    the ``train_2`` suffix: ``roberta-base-ft-frac0.5-lr2e-05-train2``. A
    learning-curve point or a hyperparameter probe therefore never
    overwrites the full run's results, checkpoint and package measurements.
    """
    tags = "".join(
        f"-{tag}{run[key]}"
        for key, tag in VARIANT_TAGS
        if run[key] != defaults[key]
    )
    return rung_name(f"{base}-ft{tags}", extra=extra)


def _eval_config(settings: Settings) -> "EvalConfig":
    from hotel_curator.ml.evaluate import EvalConfig

    return EvalConfig(seed=settings.seed)


def _finish(
    settings: Settings,
    outcome: "RungOutcome",
    predictions: "dict[str, pd.DataFrame]",
) -> None:
    """Save a rung's outcome, log it to MLflow and print the headline."""
    from hotel_curator.ml.experiment import save_outcome
    from hotel_curator.ml.tracking import log_run

    out_dir = settings.artifacts_dir / "evals" / outcome.name
    report = save_outcome(outcome, out_dir, predictions)
    summary = outcome.summary()
    log_run(
        settings.work_dir,
        outcome.name,
        {**outcome.params, "family": outcome.family},
        summary,
        [report, out_dir / "thresholds.json"],
    )
    test = outcome.test.review_macro_f1
    typer.echo(
        f"{outcome.name}: dev macro-F1 {outcome.dev.review_macro_f1.point:.4f}"
        f" | test {test.point:.4f} [{test.low:.4f}, {test.high:.4f}]"
        f" | test ECE {outcome.test.mean_review_ece:.4f} -> {report}"
    )


@app.command("fetch-base")
def fetch_base_cmd(
    names: Annotated[
        list[str] | None,
        typer.Argument(help="Base model keys; all when omitted."),
    ] = None,
) -> None:
    """Download and verify pinned pretrained encoders (ADR 005)."""
    from hotel_curator.ml.bases import BASE_MODELS, fetch_base

    settings = get_settings()
    keys = names or list(BASE_MODELS)
    unknown = sorted(set(keys) - set(BASE_MODELS))
    if unknown:
        raise typer.BadParameter(
            f"unknown base models {unknown}; known: {sorted(BASE_MODELS)}"
        )
    for key in keys:
        path = fetch_base(BASE_MODELS[key], settings.base_models_dir)
        typer.echo(f"{key}: {path}")


@app.command("run-tfidf")
def run_tfidf_cmd(
    extra_train: ExtraTrain = False,
) -> None:
    """Rung (a): TF-IDF + logistic regression, TF-IDF + LightGBM."""
    from hotel_curator.ml.baselines import TfidfLightGBM, TfidfLogReg
    from hotel_curator.ml.data import load_all, training_set
    from hotel_curator.ml.experiment import assess_parts, part_predictions
    from hotel_curator.taxonomy import LABELS

    settings = get_settings()
    sets = load_all(settings.labels_dir)
    train = training_set(sets, extra=extra_train)
    dev, test = sets["gold_dev"], sets["gold_test"]
    for model_cls in (TfidfLogReg, TfidfLightGBM):
        started = time.perf_counter()
        model = model_cls(LABELS, seed=settings.seed)
        model.fit(train.texts, train.parts, train.y)
        fitted = time.perf_counter()
        dev_raw = model.predict_proba(dev.texts, dev.parts)
        test_raw = model.predict_proba(test.texts, test.parts)
        outcome = assess_parts(
            rung_name(model.name, extra=extra_train),
            "tfidf",
            dev,
            test,
            dev_raw,
            test_raw,
            _eval_config(settings),
        )
        outcome.params = {
            "seed": settings.seed,
            "n_train_parts": len(train.y),
            "train_sets": train.name,
        }
        outcome.timings = {
            "train": fitted - started,
            "predict_gold": time.perf_counter() - fitted,
        }
        _finish(
            settings,
            outcome,
            part_predictions(dev, test, dev_raw, test_raw),
        )


@app.command("run-embeddings")
def run_embeddings_cmd(
    extra_train: ExtraTrain = False,
) -> None:
    """Rung (b): frozen bge-small embeddings + logistic heads."""
    from hotel_curator.ml.data import load_all, training_set
    from hotel_curator.ml.embeddings import EmbeddingLogReg
    from hotel_curator.ml.experiment import assess_parts, part_predictions
    from hotel_curator.taxonomy import LABELS

    settings = get_settings()
    sets = load_all(settings.labels_dir)
    train = training_set(sets, extra=extra_train)
    dev, test = sets["gold_dev"], sets["gold_test"]
    started = time.perf_counter()
    features = _embed_splits(
        settings, {"train": train, "dev": dev, "test": test}
    )
    embedded = time.perf_counter()
    model = EmbeddingLogReg(LABELS, seed=settings.seed).fit(
        features["train"], train.y
    )
    fitted = time.perf_counter()
    dev_raw = model.predict_proba(features["dev"])
    test_raw = model.predict_proba(features["test"])
    outcome = assess_parts(
        rung_name(model.name, extra=extra_train),
        "embedding",
        dev,
        test,
        dev_raw,
        test_raw,
        _eval_config(settings),
    )
    outcome.params = {
        "base": "bge-small",
        "C": model.c,
        "seed": settings.seed,
        "n_train_parts": len(train.y),
        "train_sets": train.name,
    }
    outcome.timings = {
        "embed_all_sets": embedded - started,
        "train": fitted - embedded,
    }
    _finish(settings, outcome, part_predictions(dev, test, dev_raw, test_raw))


def _embed_splits(
    settings: Settings, splits: "dict[str, LabeledParts]"
) -> "dict[str, FloatMatrix]":
    """bge-small part features for each named label set."""
    from hotel_curator.ml.bases import BASE_MODELS, fetch_base
    from hotel_curator.ml.embeddings import SentenceEmbedder, part_features

    base = fetch_base(BASE_MODELS["bge-small"], settings.base_models_dir)
    embedder = SentenceEmbedder(base)
    return {
        name: part_features(embedder, split.texts, split.parts)
        for name, split in splits.items()
    }


@app.command("run-encoder")
def run_encoder_cmd(
    base: BaseKey,
    name: RungName = None,
    seed: Seed = None,
    train_fraction: TrainFraction = 1.0,
    extra_train: ExtraTrain = False,
    base_dir: BaseDir = None,
    learning_rate: LearningRate = None,
    epochs: Epochs = None,
) -> None:
    """Rung (c): fine-tune an encoder, select on gold-dev, score both splits."""
    from hotel_curator.ml.data import load_all, training_set
    from hotel_curator.ml.encoder import train_encoder
    from hotel_curator.taxonomy import LABELS

    _check_encoder_args(base, base_dir, train_fraction)
    settings = get_settings()
    sets = load_all(settings.labels_dir)
    dev = sets["gold_dev"]
    run_seed = settings.seed if seed is None else seed
    train = _subsample(
        training_set(sets, extra=extra_train), train_fraction, run_seed
    )
    config = _encoder_config(
        base, base_dir, learning_rate, epochs, run_seed, settings
    )
    rung = name or _default_rung(
        base, config, train_fraction, settings.seed, extra=extra_train
    )
    checkpoint = settings.runs_dir / rung
    result = train_encoder(
        (train.texts, train.parts, train.y),
        (dev.texts, dev.parts, dev.y),
        LABELS,
        config,
        checkpoint,
    )
    params = _encoder_params(config, base, train_fraction, train, result)
    _assess_encoder(settings, rung, checkpoint, config, params, result.seconds)


def _default_rung(
    base: str,
    config: "EncoderConfig",
    train_fraction: float,
    default_seed: int,
    *,
    extra: bool,
) -> str:
    """:func:`encoder_rung_name` of a run, against ``base``'s defaults."""
    run: dict[str, float | int] = {
        "train_fraction": train_fraction,
        "seed": config.seed,
        "learning_rate": config.learning_rate,
        "epochs": config.epochs,
    }
    defaults: dict[str, float | int] = {
        "train_fraction": 1.0,
        "seed": default_seed,
        **_default_hyper(base),
    }
    return encoder_rung_name(base, run, defaults, extra=extra)


def _assess_encoder(
    settings: Settings,
    rung: str,
    checkpoint: Path,
    config: "EncoderConfig",
    params: dict[str, Any],
    train_seconds: float,
) -> None:
    """Score a trained checkpoint on both gold splits and save the rung."""
    from hotel_curator.ml.data import load_all
    from hotel_curator.ml.experiment import (
        assess_parts,
        clear_package_outputs,
        part_predictions,
    )

    sets = load_all(settings.labels_dir)
    dev, test = sets["gold_dev"], sets["gold_test"]
    dev_raw, test_raw, seconds = _predict_gold(checkpoint, config, dev, test)
    outcome = assess_parts(
        rung, "encoder", dev, test, dev_raw, test_raw, _eval_config(settings)
    )
    outcome.params = params
    outcome.timings = {
        "train": train_seconds,
        "predict_gold": seconds,
        "gold_parts_per_second": (len(dev.y) + len(test.y)) / seconds,
    }
    # A retrained checkpoint invalidates the old package measurements.
    clear_package_outputs(settings.artifacts_dir / "evals" / rung)
    _finish(settings, outcome, part_predictions(dev, test, dev_raw, test_raw))


def _check_encoder_args(
    base: str, base_dir: Path | None, train_fraction: float
) -> None:
    if base not in ENCODER_DEFAULTS and base_dir is None:
        raise typer.BadParameter(
            f"choose one of {sorted(ENCODER_DEFAULTS)} or pass --base-dir"
        )
    if not 0.0 < train_fraction <= 1.0:
        raise typer.BadParameter("train_fraction must be in (0, 1]")


def _predict_gold(
    checkpoint: Path,
    config: "EncoderConfig",
    dev: "LabeledParts",
    test: "LabeledParts",
) -> tuple["FloatMatrix", "FloatMatrix", float]:
    """Raw scores of a trained checkpoint on gold-dev and gold-test, timed."""
    from hotel_curator.ml.encoder import EncoderPredictor

    started = time.perf_counter()
    predictor = EncoderPredictor(checkpoint, max_length=config.max_length)
    dev_raw = predictor.predict_proba(dev.texts, dev.parts)
    test_raw = predictor.predict_proba(test.texts, test.parts)
    return dev_raw, test_raw, time.perf_counter() - started


def _encoder_params(
    config: "EncoderConfig",
    base: str,
    train_fraction: float,
    train: "LabeledParts",
    result: "TrainResult",
) -> dict[str, Any]:
    """What an encoder run logs: its config, its data and its training.

    ``n_parameters`` is the model's size for rule-2 ties when its base is
    not in ``ml.compare.SIZE_MILLIONS`` (a listed base keeps that value).
    """
    return {
        **asdict(config),
        "base_model": base,
        "train_fraction": train_fraction,
        "n_train_parts": len(train.y),
        "n_train_reviews": int(train.frame["review_id"].nunique()),
        "n_train_hotels": int(train.frame["hotel_id"].nunique()),
        "train_sets": train.name,
        "best_epoch": result.best_epoch,
        "history": result.history,
        "n_parameters": result.n_parameters,
    }


def _subsample(
    train: "LabeledParts", fraction: float, seed: int
) -> "LabeledParts":
    """A seeded share of the training reviews (all parts of each review)."""
    if fraction >= 1.0:
        return train
    import numpy as np

    reviews = np.sort(train.frame["review_id"].unique())
    keep = np.random.default_rng(seed).choice(
        reviews, round(fraction * len(reviews)), replace=False
    )
    return train.subset(train.frame["review_id"].isin(keep).to_numpy())


def _default_hyper(base: str) -> dict[str, float | int]:
    """``base``'s pre-specified learning rate, epochs and batch size.

    :data:`ENCODER_DEFAULTS` for a known base, else ``EncoderConfig``'s.
    """
    from hotel_curator.ml.encoder import EncoderConfig

    fallback = EncoderConfig(base_model=base)
    return {
        "learning_rate": fallback.learning_rate,
        "epochs": fallback.epochs,
        "batch_size": fallback.batch_size,
        **ENCODER_DEFAULTS.get(base, {}),
    }


def _encoder_config(
    base: str,
    base_dir: Path | None,
    learning_rate: float | None,
    epochs: int | None,
    seed: int,
    settings: Settings,
) -> "EncoderConfig":
    """Pre-specified hyperparameters per base model, with explicit overrides."""
    from hotel_curator.ml.bases import BASE_MODELS, fetch_base
    from hotel_curator.ml.encoder import EncoderConfig

    hyper = _default_hyper(base)
    if learning_rate is not None:
        hyper["learning_rate"] = learning_rate
    if epochs is not None:
        hyper["epochs"] = epochs
    source = base_dir or fetch_base(BASE_MODELS[base], settings.base_models_dir)
    return EncoderConfig(
        base_model=str(source),
        seed=seed,
        learning_rate=float(hyper["learning_rate"]),
        epochs=int(hyper["epochs"]),
        batch_size=int(hyper["batch_size"]),
    )


@app.command("eval-v1")
def eval_v1_cmd(
    model_dir: Annotated[
        Path | None, typer.Option(help="v1 checkpoint directory.")
    ] = None,
) -> None:
    """Score the frozen v1 checkpoint in four configurations.

    Two inputs (whole review, each part; both lowercased, see ``ml.v1``) x
    two threshold sets (v1's shipped, tuned on gold-dev); ``curator model
    compare`` picks v1's configuration on gold-dev, exactly as it picks
    among v2 rungs.
    """
    from hotel_curator.ml.data import load_all
    from hotel_curator.ml.experiment import (
        FIXED_THRESHOLDS,
        part_predictions,
        review_predictions,
    )
    from hotel_curator.ml.v1 import V1Predictor, v1_thresholds

    settings = get_settings()
    directory = model_dir or settings.models_dir / "v1-roberta-base"
    sets = load_all(settings.labels_dir)
    dev, test = sets["gold_dev"], sets["gold_test"]
    started = time.perf_counter()
    review_scores, part_raw = _v1_scores(V1Predictor(directory), dev, test)
    timings = {"predict_gold_all_modes": time.perf_counter() - started}
    shipped = v1_thresholds(directory)
    for label, fixed in ((FIXED_THRESHOLDS, shipped), ("dev-thresholds", None)):
        outcome = _v1_review_outcome(label, fixed, dev, test, review_scores)
        outcome.timings = timings
        _finish(settings, outcome, review_predictions(review_scores))
        outcome = _v1_part_outcome(label, fixed, dev, test, part_raw)
        outcome.timings = timings
        predictions = part_predictions(
            dev, test, part_raw["gold_dev"], part_raw["gold_test"]
        )
        _finish(settings, outcome, predictions)


def _v1_scores(
    predictor: "V1Predictor", dev: "LabeledParts", test: "LabeledParts"
) -> tuple[dict[str, ReviewScores], "dict[str, FloatMatrix]"]:
    """v1's raw scores on gold-dev and gold-test, per review and per part."""
    from hotel_curator.ml.v1 import v1_review_texts

    splits = (("gold_dev", dev), ("gold_test", test))
    review_scores = {}
    for name, split in splits:
        ids, texts = v1_review_texts(split.frame)
        review_scores[name] = (ids, predictor.predict_texts(texts))
    part_raw = {
        name: predictor.predict_proba(split.texts, split.parts)
        for name, split in splits
    }
    return review_scores, part_raw


def _v1_review_outcome(
    label: str,
    fixed: dict[str, float] | None,
    dev: "LabeledParts",
    test: "LabeledParts",
    review_scores: dict[str, ReviewScores],
) -> "RungOutcome":
    """v1 scored on whole reviews, with shipped or dev-tuned thresholds."""
    from hotel_curator.ml.experiment import assess_reviews

    outcome = assess_reviews(
        f"v1-review-{label}",
        "v1",
        dev,
        test,
        review_scores["gold_dev"],
        review_scores["gold_test"],
        _eval_config(get_settings()),
        thresholds=fixed,
    )
    outcome.params = {"input": "review", "thresholds": label}
    return outcome


def _v1_part_outcome(
    label: str,
    fixed: dict[str, float] | None,
    dev: "LabeledParts",
    test: "LabeledParts",
    part_raw: "dict[str, FloatMatrix]",
) -> "RungOutcome":
    """v1 scored on review parts, with shipped or dev-tuned thresholds."""
    from hotel_curator.ml.experiment import (
        assess_parts,
        rescore_with_thresholds,
    )

    outcome = assess_parts(
        f"v1-parts-{label}",
        "v1",
        dev,
        test,
        part_raw["gold_dev"],
        part_raw["gold_test"],
        _eval_config(get_settings()),
    )
    if fixed is not None:
        outcome = rescore_with_thresholds(
            outcome,
            fixed,
            (dev, test),
            (part_raw["gold_dev"], part_raw["gold_test"]),
            _eval_config(get_settings()),
        )
    outcome.params = {"input": "parts", "thresholds": label}
    return outcome


@app.command("reassess")
def reassess_cmd(
    names: Annotated[
        list[str] | None,
        typer.Argument(help="Rungs to re-score; every saved rung if omitted."),
    ] = None,
) -> None:
    """Re-score saved rungs with the current evaluation harness.

    Uses the raw gold-dev and gold-test scores each rung saved, so a change
    to the harness reaches every rung without retraining anything. Package
    measurements are dropped: a package embeds the old thresholds and
    calibration and was judged by the old harness, so it must be built
    again (``curator model package``) before it can be promoted.
    """
    from hotel_curator.ml.data import load_all
    from hotel_curator.ml.experiment import (
        clear_package_outputs,
        reassess,
        save_outcome,
    )

    settings = get_settings()
    sets = load_all(settings.labels_dir)
    evals = settings.artifacts_dir / "evals"
    rungs = names or sorted(p.parent.name for p in evals.glob("*/report.json"))
    for rung in rungs:
        outcome = reassess(
            evals / rung,
            sets["gold_dev"],
            sets["gold_test"],
            _eval_config(settings),
        )
        save_outcome(outcome, evals / rung)
        clear_package_outputs(evals / rung)
        test = outcome.test.review_macro_f1
        typer.echo(
            f"{rung}: test {test.point:.4f} [{test.low:.4f}, {test.high:.4f}]"
            f" | ECE {outcome.test.mean_review_ece:.4f}"
        )


THROUGHPUT_SAMPLE = 20_000


@app.command("package")
def package_cmd(
    name: Annotated[str, typer.Argument(help="Rung to package.")],
) -> None:
    """Export a trained rung to ONNX, pick fp32/int8, measure throughput.

    Writes ``var/packages/<rung>`` (int8 if it loses at most 0.005
    review-level gold-dev macro-F1), the package's gold scores and report
    (promotion re-checks rules 1 and 3 on them) and ``throughput.json``
    (rule 4).
    """
    import json

    from hotel_curator.ml.data import load_all
    from hotel_curator.ml.encoder import EncoderConfig
    from hotel_curator.ml.experiment import clear_package_outputs
    from hotel_curator.ml.package import build_package, score_package

    settings = get_settings()
    eval_dir = _evaluated_rung(settings, name)
    report = json.loads((eval_dir / "report.json").read_text())
    # A failure below must never leave new and old measurements side by side.
    clear_package_outputs(eval_dir)
    sets = load_all(settings.labels_dir)
    dev, test = sets["gold_dev"], sets["gold_test"]
    out_dir = settings.work_dir / "packages" / name
    metadata = _package_metadata(name, eval_dir, report)
    package_report = build_package(
        settings.runs_dir / name,
        out_dir,
        metadata=metadata,
        max_length=int(
            report["params"].get("max_length", EncoderConfig.max_length)
        ),
        dev=dev,
    )
    reports, raw = score_package(
        out_dir, {"gold_dev": dev, "gold_test": test}, settings.seed
    )
    _write_package_scores(eval_dir, dev, test, raw)
    package_report |= reports | _package_fingerprints(out_dir, metadata)
    (eval_dir / "package_report.json").write_text(
        json.dumps(package_report, indent=1, default=float)
    )
    throughput = _package_throughput(settings, out_dir)
    (eval_dir / "throughput.json").write_text(json.dumps(throughput, indent=1))
    typer.echo(
        f"{name}: {package_report['weights']} weights, "
        f"{throughput['parts_per_second']:.0f} parts/s, corpus "
        f"{throughput['corpus_hours']:.2f} h -> {out_dir}"
    )


def _evaluated_rung(settings: Settings, name: str) -> Path:
    """``artifacts/evals/<name>``, which must hold the rung's report.

    Raises:
        typer.BadParameter: If ``name`` was never evaluated, e.g. because a
            run with non-default settings got a tagged default name
            (``roberta-base-ft-lr2e-05``); the message lists the rungs that
            were.
    """
    evals = settings.artifacts_dir / "evals"
    if not (evals / name / "report.json").is_file():
        known = sorted(p.parent.name for p in evals.glob("*/report.json"))
        raise typer.BadParameter(
            f"no evaluation of rung {name!r}; evaluated rungs: "
            + (", ".join(known) or "none")
        )
    return evals / name


def _write_package_scores(
    eval_dir: Path,
    dev: "LabeledParts",
    test: "LabeledParts",
    raw: "dict[str, FloatMatrix]",
) -> None:
    """The package's raw gold scores, for promotion's re-check of 1 and 3."""
    from hotel_curator.ml.experiment import PACKAGE_SCORES, part_predictions

    scores = part_predictions(dev, test, raw["gold_dev"], raw["gold_test"])
    for split, frame in scores.items():
        frame.to_parquet(
            eval_dir / f"{PACKAGE_SCORES}_{split}.parquet", index=False
        )


def _package_fingerprints(
    out_dir: Path, metadata: dict[str, Any]
) -> dict[str, Any]:
    """What the package was judged as: its rules and its exact files.

    ``rules_sha256`` lets promotion notice a package built with other
    thresholds or calibration; ``files_sha256`` lets ``publish`` refuse to
    ship files that promotion never judged.
    """
    from hotel_curator.data.download import sha256_file
    from hotel_curator.inference.package import PACKAGE_FILES
    from hotel_curator.ml.experiment import scoring_rules_sha256

    return {
        "rules_sha256": scoring_rules_sha256(
            metadata["thresholds"], metadata["calibration"]
        ),
        # A list, not a {file: hash} map: "tokenizer.json": "<hex>" reads as
        # a credential to secret scanners.
        "files_sha256": [
            {"file": name, "sha256": sha256_file(out_dir / name)}
            for name in PACKAGE_FILES
        ],
    }


def _package_metadata(
    name: str, eval_dir: Path, report: dict[str, Any]
) -> dict[str, Any]:
    """What a package carries besides weights: thresholds, calibration."""
    import json

    return {
        "name": name,
        "thresholds": json.loads((eval_dir / "thresholds.json").read_text()),
        "calibration": json.loads((eval_dir / "calibration.json").read_text()),
        "base_model": report["params"].get("base_model"),
        "evaluation": report["summary"],
        "label_manifest": "data/manifests/labels.json",
    }


def _package_throughput(settings: Settings, out_dir: Path) -> dict[str, Any]:
    """Time the packaged model on a seeded sample of corpus parts."""
    import numpy as np
    import pandas as pd

    from hotel_curator.data.parts import corpus_parts
    from hotel_curator.inference.onnx_predictor import OnnxPredictor
    from hotel_curator.ml.package import THROUGHPUT_THREADS, measure_throughput

    parts = corpus_parts(
        pd.read_parquet(
            settings.processed_dir / "reviews.parquet",
            columns=["liked_text", "disliked_text"],
        )
    )
    sample = parts.sample(
        min(THROUGHPUT_SAMPLE, len(parts)),
        random_state=np.random.RandomState(settings.seed),
    )
    return measure_throughput(
        OnnxPredictor(out_dir, num_threads=THROUGHPUT_THREADS),
        sample["text"].tolist(),
        sample["part"].tolist(),
        len(parts),
        threads=THROUGHPUT_THREADS,
    )


@app.command("compare")
def compare_cmd() -> None:
    """Apply the promotion rule and write docs/model/COMPARISON.md."""
    import json

    from hotel_curator.ml.compare import comparison_markdown, decide
    from hotel_curator.ml.data import load_label_set
    from hotel_curator.ml.metrics import DEFAULT_N_BOOT

    settings = get_settings()
    evals_dir = settings.artifacts_dir / "evals"
    gold_test = load_label_set(settings.labels_dir, "gold_test").frame
    decision = decide(
        evals_dir, gold_test, n_boot=DEFAULT_N_BOOT, seed=settings.seed
    )
    (evals_dir / "promotion.json").write_text(
        json.dumps(decision.to_dict(), indent=1, default=float)
    )
    doc = settings.home / "docs" / "model" / "COMPARISON.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(comparison_markdown(decision, evals_dir))
    typer.echo(f"v1 configuration: {decision.v1_config}")
    typer.echo(f"winner: {decision.winner}; eligible: {decision.eligible}")
    typer.echo(f"wrote {doc}")


@app.command("report")
def report_cmd(
    name: Annotated[
        str | None, typer.Argument(help="Rung; the promoted one by default.")
    ] = None,
) -> None:
    """Write docs/model/ERRORS.md and the package's MODEL_CARD.md."""
    import json

    from hotel_curator.ml.reports import CardInputs, load_json, model_card

    settings = get_settings()
    evals_dir = settings.artifacts_dir / "evals"
    promotion = json.loads((evals_dir / "promotion.json").read_text())
    rung = name or promotion["winner"]
    if not rung:
        raise typer.BadParameter("no promoted model; pass a rung name")
    eval_dir = evals_dir / rung
    errors = _write_errors(settings, rung, eval_dir)
    report = json.loads((eval_dir / "report.json").read_text())
    _backfill_training_params(settings, report["params"])
    base = report["params"].get("base_model")
    card = model_card(
        CardInputs(
            rung,
            report,
            load_json(eval_dir / "package_report.json"),
            load_json(eval_dir / "throughput.json"),
            load_json(settings.base_models_dir / str(base) / "PROVENANCE.json"),
            promotion,
        )
    )
    out = settings.models_dir / rung / "MODEL_CARD.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(card)
    typer.echo(f"wrote {errors} and {out}")


def _write_errors(settings: Settings, rung: str, eval_dir: Path) -> Path:
    """Write docs/model/ERRORS.md for one rung's gold-test predictions."""
    import json

    import pandas as pd

    from hotel_curator.ml.data import load_label_set
    from hotel_curator.ml.reports import error_table, errors_markdown

    gold = load_label_set(settings.labels_dir, "gold_test").frame
    scores = pd.read_parquet(eval_dir / "scores_gold_test.parquet")
    thresholds = json.loads((eval_dir / "thresholds.json").read_text())
    out = settings.home / "docs" / "model" / "ERRORS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(errors_markdown(rung, error_table(gold, scores, thresholds)))
    return out


def _backfill_training_params(
    settings: Settings, params: dict[str, Any]
) -> None:
    """Add training-set sizes to runs logged before those fields existed."""
    if "n_train_reviews" in params:
        return
    from hotel_curator.ml.data import load_all, training_set

    extra = "train_2" in str(params.get("train_sets", ""))
    train = training_set(load_all(settings.labels_dir), extra=extra)
    params["n_train_reviews"] = int(train.frame["review_id"].nunique())
    params["n_train_hotels"] = int(train.frame["hotel_id"].nunique())
    params.setdefault("train_sets", train.name)


@app.command("publish")
def publish_cmd() -> None:
    """Copy the promoted rung's package into artifacts/models/<rung>.

    Refuses unless the package files are exactly the ones promotion judged
    (``files_sha256`` in ``promotion.json``): a package rebuilt after
    ``curator model compare`` must be compared again.
    """
    import json
    import shutil

    from hotel_curator.inference.package import PACKAGE_FILES

    settings = get_settings()
    promotion = json.loads(
        (settings.artifacts_dir / "evals" / "promotion.json").read_text()
    )
    winner = promotion["winner"]
    if not winner:
        raise typer.BadParameter("the promotion rule promoted no model")
    source = settings.work_dir / "packages" / winner
    if not (source / "model.onnx").is_file():
        raise typer.BadParameter(f"{source} missing; run curator model package")
    row = next(r for r in promotion["rows"] if r["name"] == winner)
    _check_judged_files(source, row["package"]["files_sha256"])
    target = settings.models_dir / winner
    target.mkdir(parents=True, exist_ok=True)
    for name in PACKAGE_FILES:
        shutil.copyfile(source / name, target / name)
    _check_judged_files(target, row["package"]["files_sha256"])
    typer.echo(f"published {winner} -> {target}")


def _check_judged_files(
    directory: Path, files_sha256: list[dict[str, str]]
) -> None:
    """Raise unless every package file has the hash promotion recorded."""
    from hotel_curator.inference.verify import judged_hashes, mismatched_files

    problems = mismatched_files(directory, judged_hashes(files_sha256))
    if problems:
        raise typer.BadParameter(
            f"{directory}: {'; '.join(problems)}; the package must be the "
            "one promotion judged (run curator model compare again)"
        )


@app.command("verify-served")
def verify_served_cmd() -> None:
    """Check the served package against the promotion record.

    Exits non-zero when model.onnx, tokenizer.json or metadata.json in
    ``artifacts/models/<winner>`` is missing, is a Git LFS pointer, or
    differs from ``files_sha256`` in ``promotion.json``. The API image runs
    this when built with ``REQUIRE_MODEL=1``.
    """
    from hotel_curator.inference.verify import check_served_package

    try:
        served = check_served_package(get_settings().artifacts_dir)
    except (OSError, ValueError, KeyError) as exc:
        typer.echo(f"cannot verify the served package: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not served.ok:
        for problem in served.problems:
            typer.echo(f"{served.directory}/{problem}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{served.winner}: every file matches the promotion record")
