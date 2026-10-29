"""Serving-time classifier: ONNX Runtime + a fast tokenizer, no PyTorch.

The API server loads a model *package* directory produced by training:

* ``model.onnx``: the exported encoder with its 16-way head,
* ``tokenizer.json``: the matching Hugging Face fast tokenizer,
* ``metadata.json``: label order, max sequence length and pad token.

Keeping PyTorch out of the server makes the container several GB smaller
and starts faster; the outputs match the PyTorch model to within 1e-4
(checked by a unit test).
"""

import functools
import json
import logging
import os
import re
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import numpy as np
import numpy.typing as npt
from tokenizers import Tokenizer

from hotel_curator.text import format_input

FloatMatrix = npt.NDArray[np.float64]
DEFAULT_BATCH = 64
STDERR_FD = 2
ORT_ERROR = 3
"""ONNX Runtime's own severity scale: 0 verbose ... 3 error, 4 fatal."""
ANSI_COLOUR = re.compile(r"\x1b\[[0-9;]*m")

logger = logging.getLogger(__name__)


@contextmanager
def native_stderr_logged() -> Iterator[None]:
    """Pass what native code writes to stderr meanwhile through logging.

    ONNX Runtime prints its own plain-text lines (on some machines, a warning
    about their devices) straight to file descriptor 2, which would break the
    server's one-JSON-object-per-line log stream.
    """
    sys.stderr.flush()
    with tempfile.TemporaryFile() as captured:
        saved = os.dup(STDERR_FD)
        os.dup2(captured.fileno(), STDERR_FD)
        try:
            yield
        finally:
            os.dup2(saved, STDERR_FD)
            os.close(saved)
        captured.seek(0)
        text = captured.read().decode("utf-8", "replace")
    for line in ANSI_COLOUR.sub("", text).splitlines():
        if line.strip():
            logger.warning("onnxruntime: %s", line.strip())


@functools.cache
def onnxruntime() -> ModuleType:
    """Import ONNX Runtime with its start-up output in our logs.

    Its device warnings are printed while the module is imported, before any
    setting can apply, so the import is captured; from then on it prints
    only errors.
    """
    with native_stderr_logged():
        import onnxruntime as ort

        ort.set_default_logger_severity(ORT_ERROR)
    module: ModuleType = ort  # the package has no type hints
    return module


class OnnxPredictor:
    """Predict 16 label probabilities for review parts with ONNX Runtime."""

    def __init__(self, package_dir: Path, *, num_threads: int = 0) -> None:
        """Load the package.

        Args:
            package_dir: Directory with ``model.onnx``, ``tokenizer.json``
                and ``metadata.json``.
            num_threads: Intra-op threads (0 lets ONNX Runtime decide).

        Raises:
            FileNotFoundError: If a package file is missing.
        """
        for name in ("model.onnx", "tokenizer.json", "metadata.json"):
            if not (package_dir / name).is_file():
                raise FileNotFoundError(f"{package_dir / name} is missing")
        meta = json.loads((package_dir / "metadata.json").read_text())
        self.name = f"onnx:{package_dir.name}"
        self.labels: list[str] = list(meta["labels"])
        self.max_length = int(meta["max_length"])
        self._tokenizer = Tokenizer.from_file(
            str(package_dir / "tokenizer.json")
        )
        self._tokenizer.enable_truncation(self.max_length)
        self._tokenizer.enable_padding(
            pad_id=int(meta["pad_id"]), pad_token=str(meta["pad_token"])
        )
        ort = onnxruntime()
        options = ort.SessionOptions()
        options.intra_op_num_threads = num_threads
        self._session = ort.InferenceSession(
            str(package_dir / "model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def predict_proba(
        self,
        texts: Sequence[str],
        parts: Sequence[str],
        *,
        batch_size: int = DEFAULT_BATCH,
    ) -> FloatMatrix:
        """Return ``(n, n_labels)`` probabilities in input order."""
        inputs = [format_input(t, p) for t, p in zip(texts, parts, strict=True)]
        out = np.zeros((len(inputs), len(self.labels)), dtype=np.float64)
        order = np.argsort([len(s) for s in inputs])
        for start in range(0, len(order), batch_size):
            idx = order[start : start + batch_size]
            encodings = self._tokenizer.encode_batch([inputs[i] for i in idx])
            feeds = {
                "input_ids": np.array(
                    [e.ids for e in encodings], dtype=np.int64
                ),
                "attention_mask": np.array(
                    [e.attention_mask for e in encodings], dtype=np.int64
                ),
            }
            (logits,) = self._session.run(["logits"], feeds)
            out[idx] = 1.0 / (1.0 + np.exp(-logits.astype(np.float64)))
        return out
