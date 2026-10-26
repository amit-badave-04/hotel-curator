"""Convert the output of an annotation run into answer files.

The v2 gold and training labels were produced by an LLM annotator panel
(see docs/decisions/004-labels-from-llm-panel.md). Each
annotation run returns a JSON object whose ``result.results`` lists
every job (set, batch, annotator, prompt path, answers). This script copies
those answers into the committed, tool-neutral format
``<set dir>/answers/<annotator>.jsonl`` and is only needed by whoever
re-runs the panel. Usage::

    python scripts/collect_panel_answers.py OUTPUT.json [OUTPUT.json ...]
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

from hotel_curator.labeling.batches import read_set, write_answers

ROLES = {
    "A": "build-session default model, reviews in forward order",
    "B": "build-session default model, reviews in reverse order",
    "C": "a different, smaller model, reviews in forward order",
}


def main(outputs: list[Path]) -> None:
    grouped: dict[tuple[Path, str], dict[int, list[dict[str, str]]]] = (
        defaultdict(dict)
    )
    for output in outputs:
        workflow = json.loads(output.read_text())
        for job in workflow["result"]["results"]:
            set_dir = Path(job["path"]).parent.parent
            grouped[(set_dir, job["annotator"])][job["batch"]] = job["answers"]
    for (set_dir, annotator), batches in sorted(grouped.items()):
        aset = read_set(set_dir)
        order = "reverse" if annotator == "B" else "forward"
        answers = [a for b in sorted(batches) for a in batches[b]]
        meta = {
            "role": ROLES[annotator],
            "batches": sorted(batches),
            "prompt_sha256": {
                str(b["batch"]): b["prompts"][order]["sha256"]
                for b in aset.batches
                if b["batch"] in batches
            },
            "guideline_sha256": aset.batches[0]["guideline_sha256"],
        }
        path = set_dir / "answers" / f"{annotator}.jsonl"
        write_answers(answers, path, annotator=annotator, meta=meta)
        print(f"{path}: {len(answers)} answers from {len(batches)} batches")


if __name__ == "__main__":
    main([Path(p) for p in sys.argv[1:]])
