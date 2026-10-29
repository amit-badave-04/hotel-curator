"""Native output during ONNX Runtime's start goes through logging."""

import logging
import os

import pytest

from hotel_curator.inference import onnx_predictor


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_native_stderr_lines_become_log_records(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A logger of its own: other tests may leave handlers on closed streams.
    collect = _Collect()
    logger = logging.getLogger("test_native_stderr")
    logger.propagate = False
    logger.handlers = [collect]
    monkeypatch.setattr(onnx_predictor, "logger", logger)
    # ONNX Runtime writes straight to file descriptor 2, in colour.
    warning = b"\x1b[0;93m[W:onnxruntime:Default, x.cc:1] no PCI id\x1b[m\n\n"
    with onnx_predictor.native_stderr_logged():
        os.write(2, warning)
    os.write(2, b"after\n")
    assert capfd.readouterr().err == "after\n"
    assert collect.messages == [
        "onnxruntime: [W:onnxruntime:Default, x.cc:1] no PCI id"
    ]
