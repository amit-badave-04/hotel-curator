"""Learning-curve probe: does more training data still help?

Fits TF-IDF + logistic regression (the cheapest rung) on 25/50/75/100% of
the first training sample's reviews, three seeds each, and scores
review-level macro-F1 on gold-dev at gold-dev-tuned thresholds. gold-test is
not touched. The plan (section 3.2) says to label more training reviews if
the curve has not flattened. Usage::

    python scripts/learning_curve.py
"""

import json
from pathlib import Path

import numpy as np

from hotel_curator.ml.baselines import TfidfLogReg
from hotel_curator.ml.data import load_all
from hotel_curator.ml.evaluate import review_level
from hotel_curator.ml.metrics import macro_f1
from hotel_curator.ml.thresholds import apply_thresholds, tune_thresholds
from hotel_curator.taxonomy import LABELS

ROOT = Path(__file__).resolve().parents[1]
FRACTIONS = (0.25, 0.5, 0.75, 1.0)
SEEDS = (1, 2, 3)


def main() -> None:
    """Run the probe and write ``artifacts/evals/learning_curve.json``."""
    sets = load_all(ROOT / "data" / "labels")
    train, dev = sets["train"], sets["gold_dev"]
    reviews = np.sort(train.frame["review_id"].unique())
    _, dev_true = review_level(dev.frame["review_id"], dev.y)
    rows = []
    for fraction in FRACTIONS:
        n = round(fraction * len(reviews))
        scores = []
        for seed in SEEDS:
            keep = np.random.default_rng(seed).choice(reviews, n, replace=False)
            sub = train.subset(train.frame["review_id"].isin(keep).to_numpy())
            model = TfidfLogReg(LABELS, seed=seed).fit(
                sub.texts, sub.parts, sub.y
            )
            probs = model.predict_proba(dev.texts, dev.parts)
            cut = tune_thresholds(dev.y, probs, LABELS)
            _, pred = review_level(
                dev.frame["review_id"], apply_thresholds(probs, cut, LABELS)
            )
            scores.append(macro_f1(dev_true, pred))
        rows.append(
            {
                "fraction": fraction,
                "train_reviews": n,
                "dev_macro_f1_mean": float(np.mean(scores)),
                "dev_macro_f1_sd": float(np.std(scores)),
            }
        )
        print(rows[-1])
    out = ROOT / "artifacts" / "evals" / "learning_curve.json"
    out.write_text(
        json.dumps({"model": "tfidf-logreg", "rows": rows}, indent=1)
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
