"""Logged tracebacks keep the stack but never local variables."""

import json
import logging

import pytest

from hotel_curator.logging import configure_logging

SECRET = "The room was DIRTY, ask for Maria"


def _fail_with_secret() -> None:
    review_text = SECRET  # a local the traceback must not show
    raise ValueError(f"bad input of length {len(review_text)}")


@pytest.mark.parametrize("fmt", ["json", "console"])
def test_tracebacks_have_no_locals(
    fmt: str, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO", fmt)
    try:
        _fail_with_secret()
    except ValueError:
        logging.getLogger("test").exception("boom")
    err = capsys.readouterr().err
    assert "_fail_with_secret" in err  # the stack is there
    assert SECRET not in err
    if fmt == "json":
        record = json.loads(err.strip().splitlines()[-1])
        assert record["exception"][0]["exc_type"] == "ValueError"
