"""A loaded model package: raw scores, labels and calibrated probabilities.

Wraps :class:`~hotel_curator.inference.onnx_predictor.OnnxPredictor` with
the thresholds and isotonic calibration stored in the package's
``metadata.json``. Labels use the raw-score thresholds tuned on gold-dev;
probabilities are calibrated (what hotel scores add up and what the API
shows). No PyTorch or scikit-learn is needed at serving time.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from hotel_curator.inference.onnx_predictor import OnnxPredictor
from hotel_curator.ml.calibration import IsotonicCalibrator

FloatMatrix = npt.NDArray[np.float64]
PACKAGE_FILES = ("model.onnx", "tokenizer.json", "metadata.json")
"""Everything a package directory holds; publishing copies exactly these."""


@dataclass(frozen=True)
class Prediction:
    """Per-part output of a model package."""

    labels: npt.NDArray[np.bool_]
    probabilities: FloatMatrix


class ModelPackage:
    """Load a package directory once and score review parts."""

    def __init__(self, package_dir: Path, *, num_threads: int = 0) -> None:
        """Load model, tokenizer, thresholds and calibration.

        Raises:
            FileNotFoundError: If a package file is missing.
            KeyError: If metadata lacks thresholds or calibration.
        """
        self.directory = package_dir
        self.predictor = OnnxPredictor(package_dir, num_threads=num_threads)
        meta = json.loads((package_dir / "metadata.json").read_text())
        self.metadata = meta
        self.name = str(meta.get("name", package_dir.name))
        self.labels = self.predictor.labels
        self._cut = np.array(
            [float(meta["thresholds"][label]) for label in self.labels]
        )
        self._calibrator = IsotonicCalibrator.from_dict(meta["calibration"])
        if self._calibrator.labels != self.labels:
            raise KeyError("calibration labels differ from model labels")

    def calibrate(self, raw: FloatMatrix) -> FloatMatrix:
        """Calibrated probabilities for raw model scores."""
        return self._calibrator.transform(raw)

    def predict(self, texts: Sequence[str], parts: Sequence[str]) -> Prediction:
        """Labels (raw score >= threshold) and calibrated probabilities."""
        raw = self.predictor.predict_proba(texts, parts)
        return Prediction(
            labels=raw >= self._cut[None, :], probabilities=self.calibrate(raw)
        )
