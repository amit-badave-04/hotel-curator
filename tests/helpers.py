"""Shared test helpers: tiny models, the CLI runner, toy label sets."""

from pathlib import Path

import numpy as np
import pandas as pd

from hotel_curator.taxonomy import LABELS

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
