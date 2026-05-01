"""Review-level predictions to hotel-level attribute signals.

Extracted from NB04 Sections 3-4.
"""

import numpy as np
import pandas as pd


def aggregate_hotel_signals(
    df_preds: pd.DataFrame,
    attributes: list[str],
    critical: tuple[str, ...] | list[str],
) -> pd.DataFrame:
    """Per-hotel per-attribute signed scores.

    Parameters
    ----------
    df_preds : pd.DataFrame
        Must contain hotel_id and pred_{attr}_{polarity} columns.
    attributes : list[str]
        The 8 attribute names.
    critical : tuple[str, ...] | list[str]
        Subset of attributes used for critical-floor gate.

    Returns
    -------
    pd.DataFrame
        Columns: hotel_id, n_reviews, {attr}_pos_rate,
        {attr}_neg_rate, {attr}_score, avg_score,
        min_critical_score.
    """
    rows: list[dict[str, object]] = []
    for hid, g in df_preds.groupby("hotel_id"):
        n = len(g)
        row: dict[str, object] = {
            "hotel_id": int(hid),
            "n_reviews": int(n),
        }
        attr_scores: list[float] = []
        for attr in attributes:
            pos_rate = float(
                g[f"pred_{attr}_positive"].mean()
            )
            neg_rate = float(
                g[f"pred_{attr}_negative"].mean()
            )
            score = pos_rate - neg_rate
            row[f"{attr}_pos_rate"] = pos_rate
            row[f"{attr}_neg_rate"] = neg_rate
            row[f"{attr}_score"] = score
            attr_scores.append(score)
        row["avg_score"] = float(np.mean(attr_scores))
        row["min_critical_score"] = float(min(
            row[f"{a}_score"] for a in critical
        ))
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        "avg_score", ascending=False,
    ).reset_index(drop=True)


def compute_mention_rates(
    df_preds: pd.DataFrame,
    attributes: list[str],
) -> pd.DataFrame:
    """Per-hotel per-attribute mention rate.

    mention_rate = fraction of reviews where either polarity
    was predicted for that attribute.

    Parameters
    ----------
    df_preds : pd.DataFrame
        Must contain hotel_id and pred_{attr}_{polarity} columns.
    attributes : list[str]
        The 8 attribute names.

    Returns
    -------
    pd.DataFrame
        Columns: hotel_id, {attr}_mention_rate.
    """
    rows: list[dict[str, object]] = []
    for hid, g in df_preds.groupby("hotel_id"):
        n = len(g)
        row: dict[str, object] = {"hotel_id": int(hid)}
        for attr in attributes:
            mentioned = (
                (g[f"pred_{attr}_positive"] == 1)
                | (g[f"pred_{attr}_negative"] == 1)
            ).sum()
            row[f"{attr}_mention_rate"] = float(
                mentioned / n
            )
        rows.append(row)
    return pd.DataFrame(rows)


def compute_weighted_avg_score(
    signals: pd.DataFrame,
    attributes: list[str],
) -> pd.Series:
    """Mention-rate weighted avg_score.

    Weight each attribute's score by its mention rate so
    hotels with sparse wifi/breakfast don't get dragged
    toward zero by unmentioned attributes.

    Parameters
    ----------
    signals : pd.DataFrame
        Must contain {attr}_score and {attr}_mention_rate.
    attributes : list[str]
        The 8 attribute names.

    Returns
    -------
    pd.Series
        Weighted avg_score per hotel.
    """
    weighted_sum = pd.Series(0.0, index=signals.index)
    weight_total = pd.Series(0.0, index=signals.index)
    for attr in attributes:
        mr = signals[f"{attr}_mention_rate"]
        sc = signals[f"{attr}_score"]
        weighted_sum += mr * sc
        weight_total += mr
    return weighted_sum / weight_total.clip(lower=1e-9)
