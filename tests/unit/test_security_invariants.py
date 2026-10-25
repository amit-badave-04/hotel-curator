"""Source-level checks for security requirements (docs/specs/v2-plan.md 7.2).

These run in CI with the unit tests, so a regression fails the build.
"""

import re
from pathlib import Path

from hotel_curator.schemas import QuoteOut

SRC = Path(__file__).parents[2] / "src" / "hotel_curator"

UNSAFE_LOADING = {
    "pickle.load": re.compile(r"\bpickle\.loads?\("),
    "joblib.load": re.compile(r"\bjoblib\.load\("),
    "torch.load without weights_only=True": re.compile(
        r"torch\.load\((?![^)]*weights_only=True)"
    ),
    "trust_remote_code": re.compile(r"trust_remote_code\s*=\s*True"),
    "from_pretrained outside ml/hf.py": re.compile(r"\.from_pretrained\("),
    "yaml.load (unsafe)": re.compile(r"\byaml\.load\("),
    "eval/exec": re.compile(r"(?<![\w.])(eval|exec)\("),
}


def _sources() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "migrations" not in p.parts]


def test_sr8_no_unsafe_model_or_data_loading() -> None:
    problems = []
    for path in _sources():
        text = path.read_text()
        for name, pattern in UNSAFE_LOADING.items():
            if name.startswith("from_pretrained") and path.name == "hf.py":
                continue
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                problems.append(f"{path.relative_to(SRC)}:{line} {name}")
    assert not problems, problems


def test_sr9_quotes_never_carry_identifying_review_metadata() -> None:
    fields = set(QuoteOut.model_fields)
    for forbidden in (
        "nationality",
        "reviewer_nationality",
        "room_type",
        "nights",
        "reviewer",
        "author",
    ):
        assert forbidden not in fields
    assert {"month", "traveler_type"} <= fields


def test_sr10_downloads_are_https_only() -> None:
    text = (SRC / "data" / "download.py").read_text()
    assert 'url.startswith("https://")' in text
    for path in _sources():
        assert "http://" not in path.read_text().replace(
            "http://127.0.0.1", ""
        ).replace("http://localhost", ""), path
