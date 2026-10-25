"""Synthetic data shared by tests: hotels scored by the real pipeline."""

from pathlib import Path

import numpy as np
import pandas as pd

from hotel_curator.schemas import HotelDetail
from hotel_curator.scoring.evidence import Redactor
from hotel_curator.scoring.pipeline import ScoringInputs, score_hotels
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import LABELS

POLICY_PATH = Path(__file__).parents[1] / "config" / "tier_policy.yaml"

# (hotel id, name, city, reviews, cleanliness polarity, noisy)
HOTELS = [
    ("h_00000000a1", "Alpha Grand", "Paris", 120, "positive", False),
    ("h_00000000b2", "Bravo Inn", "Paris", 120, "negative", False),
    ("h_00000000c3", "Charlie House", "London", 120, "positive", True),
    ("h_00000000d4", "Delta Rooms", "London", 12, "positive", False),
    ("h_00000000e5", "Echo 100% Stay", "Vienna", 90, "positive", False),
]


class NoNames:
    """Fake spaCy pipeline that finds no entities."""

    class _Doc:
        def __init__(self, text: str) -> None:
            self.text = text
            self.ents: list[object] = []

    def pipe(self, texts: list[str], batch_size: int = 0) -> list["_Doc"]:
        return [self._Doc(t) for t in texts]


def synthetic_inputs() -> ScoringInputs:
    """Five hotels with clear-cut evidence."""
    rng = np.random.default_rng(0)
    hotels, reviews, parts = [], [], []
    for hotel_id, name, city, n, clean, noisy in HOTELS:
        hotels.append(
            {
                "hotel_id": hotel_id,
                "name": name,
                "address": f"1 Street {city}",
                "city": city,
                "country": {
                    "Paris": "France",
                    "London": "United Kingdom",
                    "Vienna": "Austria",
                }[city],
                "lat": 48.0,
                "lng": 2.0,
                "source_average_score": 8.0,
                "source_total_reviews": 1000,
                "n_reviews": n,
            }
        )
        for i in range(n):
            rid = f"r_{hotel_id[-2:]}{i:010d}"
            # In the noisy hotel, solo travellers (every third review) are
            # never bothered by noise: a "who it suits" finding.
            solo = noisy and i % 3 == 0
            complains = noisy and not solo
            reviews.append(
                {
                    "review_id": rid,
                    "hotel_id": hotel_id,
                    "review_date": pd.Timestamp("2015-09-01")
                    + pd.Timedelta(days=6 * i),
                    "liked_text": f"Lovely staff and comfortable room {i}",
                    "disliked_text": f"Street noise at night {i}"
                    if complains
                    else None,
                    "traveler_type": "solo" if solo else "couple",
                }
            )
            liked = dict.fromkeys(LABELS, 0.01)
            for label in (
                "staff_positive",
                "room_positive",
                "location_positive",
            ):
                liked[label] = 0.97
            liked[f"cleanliness_{clean}"] = 0.97
            liked["value_positive"] = float(rng.uniform(0.0, 0.9))
            parts.append({"review_id": rid, "part": "liked", **liked})
            if complains:
                bad = dict.fromkeys(LABELS, 0.01)
                bad["noise_negative"] = 0.96
                parts.append({"review_id": rid, "part": "disliked", **bad})
    return ScoringInputs(
        hotels=pd.DataFrame(hotels),
        reviews=pd.DataFrame(reviews),
        part_probs=pd.DataFrame(parts),
        training_review_ids=set(),
        model_name="test-model",
    )


def synthetic_results() -> list[HotelDetail]:
    """Hotel results for the synthetic hotels."""
    return score_hotels(
        synthetic_inputs(), TierPolicy.load(POLICY_PATH), Redactor(NoNames())
    )


TINY_VOCAB = [
    "[PAD]",
    "[UNK]",
    "[CLS]",
    "[SEP]",
    "[MASK]",
    "liked",
    "disliked",
    ":",
    "clean",
    "dirty",
    "friendly",
    "rude",
    "room",
    "staff",
    "the",
    "was",
    "very",
    "and",
]


def tiny_bert(out: Path) -> Path:
    """A 2-layer random BERT with a toy WordPiece vocabulary (no download)."""
    import torch
    from tokenizers import Tokenizer
    from tokenizers.models import WordPiece
    from tokenizers.normalizers import BertNormalizer
    from tokenizers.pre_tokenizers import BertPreTokenizer
    from tokenizers.processors import TemplateProcessing
    from transformers import BertConfig, BertModel, PreTrainedTokenizerFast

    vocab = {tok: i for i, tok in enumerate(TINY_VOCAB)}
    tok = Tokenizer(WordPiece(vocab, unk_token="[UNK]"))
    tok.normalizer = BertNormalizer(lowercase=True)
    tok.pre_tokenizer = BertPreTokenizer()
    tok.post_processor = TemplateProcessing(
        single="[CLS] $A [SEP]",
        special_tokens=[("[CLS]", vocab["[CLS]"]), ("[SEP]", vocab["[SEP]"])],
    )
    PreTrainedTokenizerFast(
        tokenizer_object=tok,
        unk_token="[UNK]",
        pad_token="[PAD]",
        cls_token="[CLS]",
        sep_token="[SEP]",
        mask_token="[MASK]",
    ).save_pretrained(out)
    config = BertConfig(
        vocab_size=len(TINY_VOCAB),
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=64,
        max_position_embeddings=64,
    )
    torch.manual_seed(0)
    BertModel(config).save_pretrained(out)
    return out


def invoke_cli(*args: str) -> str:
    """Run ``curator <args>`` in-process; fail the test on a non-zero exit."""
    from typer.testing import CliRunner

    from hotel_curator.cli.main import app

    result = CliRunner().invoke(app, list(args))
    if result.exit_code != 0 and result.exc_info is not None:
        raise result.exc_info[1].with_traceback(result.exc_info[2])
    assert result.exit_code == 0, result.output
    return result.output


def toy_label_set(prefix: str, n: int, seed: int) -> pd.DataFrame:
    """A label table of "the room was clean/dirty" parts over 10 hotels.

    Clean parts praise cleanliness and the room; dirty parts complain about
    both, so every label the tier validation reads has positives.
    """
    rng = np.random.default_rng(seed)
    clean = rng.random(n) < 0.5
    frame = pd.DataFrame(
        {
            "review_id": [f"r_{prefix}{i:04d}" for i in range(n)],
            "hotel_id": [f"h_{prefix}{i % 10:03d}" for i in range(n)],
            "part": "liked",
            "text": np.where(clean, "the room was clean", "the room was dirty"),
            "stratum": "uniform",
            "sampling_weight": 1.0,
            **dict.fromkeys(LABELS, False),
        }
    )
    for attribute in ("cleanliness", "room"):
        frame[f"{attribute}_positive"] = clean
        frame[f"{attribute}_negative"] = ~clean
    return frame
