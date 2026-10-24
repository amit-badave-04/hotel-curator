"""Write annotation sets to disk and read the panel's answers back.

An *annotation set* (``gold_test``, ``gold_dev``, ``train``) is a directory:

```
data/labels/<set>/
  sample.parquet            the sampled reviews (+ stratum, sampling weight)
  index.json                batches, review ids and prompt SHA-256s
  prompts/batch_000_forward.txt   full prompt: guideline + reviews in order
  prompts/batch_000_reverse.txt   same reviews in reverse order
  answers/<annotator>.jsonl       one JSON answer per line (+ a meta line)
```

Annotators read a prompt file verbatim, so the prompt's SHA-256 identifies
exactly what each annotator saw. Prompts are not committed: they are
rebuilt from the sample and the guideline (:func:`rewrite_prompts`) and must
match the SHA-256s in ``index.json``. Two orders exist so that two annotators do
not see the reviews in the same sequence (position effects then do not
correlate between them).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from hotel_curator.labeling.panel import (
    AnnotationItem,
    ParsedAnnotation,
    parse_annotations,
)
from hotel_curator.labeling.prompt import build_prompt

MAX_PART_CHARS = 1500
"""Parts are cut to this length for annotation (affects <0.5% of parts)."""
DEFAULT_BATCH_SIZE = 100
ORDERS = ("forward", "reverse")


@dataclass
class AnnotationSet:
    """In-memory view of an annotation set's ``index.json``."""

    name: str
    root: Path
    batches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def review_ids(self) -> list[str]:
        """All review ids in batch order."""
        return [rid for batch in self.batches for rid in batch["review_ids"]]


def _clip(text: object, max_chars: int) -> str:
    if not isinstance(text, str):
        return ""
    return text[:max_chars]


def to_items(
    sample: pd.DataFrame, *, max_chars: int = MAX_PART_CHARS
) -> list[AnnotationItem]:
    """Convert sampled reviews to annotation items (missing part -> "")."""
    return [
        AnnotationItem(rid, _clip(liked, max_chars), _clip(disliked, max_chars))
        for rid, liked, disliked in zip(
            sample["review_id"],
            sample["liked_text"],
            sample["disliked_text"],
            strict=True,
        )
    ]


def write_set(
    sample: pd.DataFrame,
    name: str,
    root: Path,
    guideline: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_chars: int = MAX_PART_CHARS,
    extra_instructions: str = "",
) -> AnnotationSet:
    """Write the sample, one prompt file per batch and order, and the index."""
    set_dir = root / name
    (set_dir / "prompts").mkdir(parents=True, exist_ok=True)
    sample.to_parquet(set_dir / "sample.parquet", index=False)
    items = to_items(sample, max_chars=max_chars)
    aset = AnnotationSet(name=name, root=set_dir)
    for b, start in enumerate(range(0, len(items), batch_size)):
        chunk = items[start : start + batch_size]
        prompts = {}
        for order in ORDERS:
            ordered = chunk if order == "forward" else list(reversed(chunk))
            prompt = build_prompt(ordered, guideline, extra_instructions)
            rel = f"prompts/batch_{b:03d}_{order}.txt"
            (set_dir / rel).write_text(prompt.text, encoding="utf-8")
            prompts[order] = {"path": rel, "sha256": prompt.sha256}
        aset.batches.append(
            {
                "batch": b,
                "review_ids": [it.review_id for it in chunk],
                "prompts": prompts,
                "guideline_sha256": prompt.guideline_sha256,
            }
        )
    (set_dir / "index.json").write_text(
        json.dumps({"name": name, "batches": aset.batches}, indent=1) + "\n"
    )
    return aset


def rewrite_prompts(set_dir: Path, guideline: str) -> int:
    """Rebuild a set's prompt files from its sample and check their hashes.

    Uses the default part length and no extra instructions, as every
    committed set does.

    Returns:
        Number of prompt files written.

    Raises:
        ValueError: If a rebuilt prompt does not match ``index.json``
            (the sample, the guideline or the prompt template changed).
    """
    aset = read_set(set_dir)
    sample = pd.read_parquet(set_dir / "sample.parquet")
    by_id = dict(zip(sample["review_id"], to_items(sample), strict=True))
    (set_dir / "prompts").mkdir(parents=True, exist_ok=True)
    written = 0
    for batch in aset.batches:
        chunk = [by_id[rid] for rid in batch["review_ids"]]
        for order, meta in batch["prompts"].items():
            ordered = chunk if order == "forward" else list(reversed(chunk))
            prompt = build_prompt(ordered, guideline, "")
            if prompt.sha256 != meta["sha256"]:
                raise ValueError(
                    f"{set_dir.name} batch {batch['batch']} ({order}): "
                    "rebuilt prompt does not match index.json"
                )
            (set_dir / meta["path"]).write_text(prompt.text, encoding="utf-8")
            written += 1
    return written


def read_set(set_dir: Path) -> AnnotationSet:
    """Load an annotation set written by :func:`write_set`."""
    index = json.loads((set_dir / "index.json").read_text())
    return AnnotationSet(
        name=index["name"], root=set_dir, batches=index["batches"]
    )


def write_answers(
    answers: list[dict[str, str]],
    path: Path,
    *,
    annotator: str,
    meta: dict[str, Any],
) -> Path:
    """Write one annotator's answers as JSON Lines, first line = metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"_meta": {"annotator": annotator, **meta}})]
    lines += [json.dumps(a, ensure_ascii=False) for a in answers]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_answers(
    path: Path, expected_ids: set[str]
) -> tuple[ParsedAnnotation, dict[str, Any]]:
    """Parse an answers file strictly; returns parsed labels and metadata."""
    text = path.read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    meta = json.loads(first)["_meta"]
    return parse_annotations(rest, expected_ids), meta
