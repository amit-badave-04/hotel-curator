"""Evidence retrieval: top-K reviews per (hotel, attribute, polarity).

Extracted from NB04 Section 6.
"""

import pandas as pd


def build_evidence(
    df_preds: pd.DataFrame,
    df_reviews: pd.DataFrame,
    attributes: list[str],
    label_names: list[str],
    *,
    top_k: int = 3,
) -> dict[int, dict[str, list[dict]]]:
    """Build evidence index: top-K reviews per hotel-attribute cell.

    For each (hotel, attribute, polarity), find the top_k reviews
    with the highest predicted probability among reviews where
    that label was predicted positive.

    Parameters
    ----------
    df_preds : pd.DataFrame
        Must contain review_id, hotel_id, prob_{lbl}, pred_{lbl}.
    df_reviews : pd.DataFrame
        Must contain review_id, text.
    attributes : list[str]
        The 8 attribute names.
    label_names : list[str]
        All 16 label names.
    top_k : int
        Number of evidence reviews per cell.

    Returns
    -------
    dict[int, dict[str, list[dict]]]
        {hotel_id: {label: [{review_id, text, prob}, ...]}}.
    """
    text_map = dict(zip(
        df_reviews["review_id"], df_reviews["text"], strict=True,
    ))

    evidence: dict[int, dict[str, list[dict]]] = {}
    for hid, g in df_preds.groupby("hotel_id"):
        hid_int = int(hid)
        evidence[hid_int] = {}
        for lbl in label_names:
            positives = g[g[f"pred_{lbl}"] == 1]
            if len(positives) == 0:
                continue
            top = positives.nlargest(top_k, f"prob_{lbl}")
            snippets = []
            for _, row in top.iterrows():
                rid = int(row["review_id"])
                snippets.append({
                    "review_id": rid,
                    "text": text_map.get(rid, ""),
                    "prob": float(row[f"prob_{lbl}"]),
                })
            evidence[hid_int][lbl] = snippets
    return evidence
