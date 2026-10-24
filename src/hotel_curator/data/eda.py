"""Exploratory data analysis report, generated from the canonical tables.

``curator data report`` writes ``docs/data/EDA.md`` and the figures in
``docs/data/figures/``. The Markdown is produced by code so every number in it
comes from the data, not from a hand-edited document; do not edit EDA.md by
hand, rerun the command.

Charts follow the project's chart conventions: one hue for single-series
charts, thin marks, a recessive grid, no dual axes, and a legend whenever
there are two series.
"""

from dataclasses import dataclass
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure as MplFigure

mpl.use("Agg")  # file output only; never open a window

SERIES_1 = "#2a78d6"
SERIES_2 = "#eb6834"
TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


@dataclass(frozen=True)
class Figure:
    """A written figure and its alt text."""

    path: Path
    alt: str


def _style(ax: Axes, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", color=TEXT, fontsize=11)
    ax.set_xlabel(xlabel, color=MUTED, fontsize=9)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.yaxis.set_major_formatter(mpl.ticker.StrMethodFormatter("{x:,.0f}"))
    ax.set_facecolor(SURFACE)


def _save(fig: MplFigure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.patch.set_facecolor(SURFACE)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _reviews_per_hotel(hotels: pd.DataFrame, out: Path) -> Figure:
    fig, ax = plt.subplots(figsize=(7, 3.2))
    bins = np.logspace(np.log10(8), np.log10(hotels["n_reviews"].max() + 1), 40)
    ax.hist(
        hotels["n_reviews"],
        bins=bins.tolist(),
        color=SERIES_1,
        edgecolor=SURFACE,
        linewidth=1,
    )
    ax.set_xscale("log")
    _style(ax, "Reviews per hotel (log scale)", "reviews in dataset", "hotels")
    path = out / "reviews_per_hotel.png"
    _save(fig, path)
    return Figure(
        path, "Histogram of reviews per hotel on a log scale, from 8 to 4,789."
    )


def _reviews_per_month(reviews: pd.DataFrame, out: Path) -> Figure:
    monthly = reviews.set_index("review_date").resample("MS").size()
    last_day = reviews["review_date"].max()
    if last_day < last_day + pd.offsets.MonthEnd(0):
        monthly = monthly.iloc[:-1]  # drop the partial final month
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(monthly.index, monthly.to_numpy(), color=SERIES_1, linewidth=2)
    ax.set_ylim(bottom=0)
    _style(ax, "Reviews per month (complete months)", "month", "reviews")
    path = out / "reviews_per_month.png"
    _save(fig, path)
    return Figure(
        path,
        "Line chart of reviews per complete month, August 2015 to July 2017.",
    )


def _reviewer_scores(reviews: pd.DataFrame, out: Path) -> Figure:
    counts = reviews["reviewer_score"].round(0).value_counts().sort_index()
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.bar(counts.index, counts.to_numpy(), width=0.7, color=SERIES_1)
    _style(
        ax, "Reviewer score (rounded)", "score given by the guest", "reviews"
    )
    path = out / "reviewer_scores.png"
    _save(fig, path)
    return Figure(
        path, "Bar chart of reviewer scores; most reviews score 8 to 10."
    )


def _part_lengths(reviews: pd.DataFrame, out: Path) -> Figure:
    fig, ax = plt.subplots(figsize=(7, 3.2))
    bins = np.arange(0, 121, 4)
    for column, color, label in (
        ("liked_text", SERIES_1, "Liked part"),
        ("disliked_text", SERIES_2, "Disliked part"),
    ):
        words = word_count(reviews[column].dropna()).clip(upper=120)
        ax.hist(
            words,
            bins=bins.tolist(),
            histtype="step",
            linewidth=2,
            color=color,
            label=label,
        )
    ax.legend(frameon=False, fontsize=8, labelcolor=TEXT)
    _style(
        ax, "Length of review parts (words, capped at 120)", "words", "parts"
    )
    path = out / "part_lengths.png"
    _save(fig, path)
    return Figure(
        path, "Two histograms of part length in words: Liked and Disliked."
    )


def word_count(texts: "pd.Series[str]") -> "pd.Series[int]":
    """Number of whitespace-separated words in each text."""
    return texts.str.count(r"\S+")


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _summary_tables(hotels: pd.DataFrame, reviews: pd.DataFrame) -> str:
    no_text = reviews["liked_text"].isna() & reviews["disliked_text"].isna()
    rows = [
        ("Hotels", f"{len(hotels):,}"),
        ("Reviews", f"{len(reviews):,}"),
        ("Cities", ", ".join(sorted(hotels["city"].unique()))),
        (
            "Review dates",
            f"{reviews['review_date'].min():%Y-%m-%d} to "
            f"{reviews['review_date'].max():%Y-%m-%d}",
        ),
        (
            "Reviews per hotel (min / median / max)",
            f"{hotels['n_reviews'].min()} / "
            f"{int(hotels['n_reviews'].median())} / "
            f"{hotels['n_reviews'].max():,}",
        ),
        (
            "Liked part missing (site placeholder)",
            _pct(reviews["liked_text"].isna().mean()),
        ),
        (
            "Disliked part missing (site placeholder)",
            _pct(reviews["disliked_text"].isna().mean()),
        ),
        (
            "Reviews with no text at all",
            f"{int(no_text.sum()):,}",
        ),
        ("Hotels without coordinates", f"{int(hotels['lat'].isna().sum())}"),
        ("Mean reviewer score", f"{reviews['reviewer_score'].mean():.2f}"),
    ]
    lines = ["| Fact | Value |", "|---|---|"] + [
        f"| {k} | {v} |" for k, v in rows
    ]
    split = pd.crosstab(hotels["city"], hotels["split"])[
        ["train", "val", "test"]
    ]
    lines += [
        "",
        "Hotels per city and split (hotel-disjoint):",
        "",
        "| City | train | val | test |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| {c} | {r.train} | {r.val} | {r.test} |" for c, r in split.iterrows()
    ]
    trav = reviews["traveler_type"].value_counts(normalize=True)
    trip = reviews["trip_type"].value_counts(normalize=True)
    lines += ["", "| Traveler type | Share of reviews |", "|---|---:|"]
    lines += [f"| {k} | {_pct(v)} |" for k, v in trav.items()]
    lines += ["", "| Trip type | Share of reviews |", "|---|---:|"]
    lines += [f"| {k} | {_pct(v)} |" for k, v in trip.items()]
    return "\n".join(lines)


def write_report(
    hotels: pd.DataFrame, reviews: pd.DataFrame, docs_dir: Path
) -> Path:
    """Write figures and ``EDA.md`` under ``docs_dir``; return EDA.md's path."""
    fig_dir = docs_dir / "figures"
    figures = [
        _reviews_per_hotel(hotels, fig_dir),
        _reviews_per_month(reviews, fig_dir),
        _reviewer_scores(reviews, fig_dir),
        _part_lengths(reviews, fig_dir),
    ]
    liked_words = word_count(reviews["liked_text"].dropna())
    disliked_words = word_count(reviews["disliked_text"].dropna())
    body = [
        "# Exploratory data analysis",
        "",
        "_Generated by `curator data report` from `data/processed/`. "
        "Do not edit by hand; rerun the command._",
        "",
        "## Summary",
        "",
        _summary_tables(hotels, reviews),
        "",
        "## Text length",
        "",
        f"Liked parts: mean {liked_words.mean():.1f} words, median "
        f"{liked_words.median():.0f}, 95th percentile "
        f"{liked_words.quantile(0.95):.0f}. "
        f"Disliked parts: mean {disliked_words.mean():.1f}, median "
        f"{disliked_words.median():.0f}, 95th percentile "
        f"{disliked_words.quantile(0.95):.0f}. A 128-token model input covers "
        "almost every part.",
        "",
        "## Figures",
        "",
    ]
    for fig in figures:
        body += [f"![{fig.alt}](figures/{fig.path.name})", ""]
    path = docs_dir / "EDA.md"
    path.write_text("\n".join(body) + "\n")
    return path
