"""Bootstrap 95% confidence intervals on hotel attr_score.

Extracted from NB04 Section 4.2.
"""

import numpy as np
import pandas as pd


def bootstrap_hotel_ci(
    df_preds: pd.DataFrame,
    attributes: list[str],
    *,
    n_boot: int = 1_000,
    seed: int = 42,
) -> pd.DataFrame:
    """Bootstrap 95% CI for each hotel's attr_score.

    For each hotel, resample reviews with replacement n_boot
    times, recompute attr_score = pos_rate - neg_rate, take
    2.5th and 97.5th percentiles.

    Parameters
    ----------
    df_preds : pd.DataFrame
        Must contain hotel_id and pred_{attr}_{polarity}.
    attributes : list[str]
        The 8 attribute names.
    n_boot : int
        Number of bootstrap resamples.
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    pd.DataFrame
        Columns: hotel_id, {attr}_score_ci_lo,
        {attr}_score_ci_hi.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for hid, g in df_preds.groupby("hotel_id"):
        n = len(g)
        row: dict[str, object] = {"hotel_id": int(hid)}
        for attr in attributes:
            pos_vals = g[f"pred_{attr}_positive"].values
            neg_vals = g[f"pred_{attr}_negative"].values
            boot_scores = np.empty(n_boot)
            for b in range(n_boot):
                idx = rng.integers(0, n, size=n)
                boot_scores[b] = (
                    pos_vals[idx].mean()
                    - neg_vals[idx].mean()
                )
            row[f"{attr}_score_ci_lo"] = float(
                np.percentile(boot_scores, 2.5)
            )
            row[f"{attr}_score_ci_hi"] = float(
                np.percentile(boot_scores, 97.5)
            )
        rows.append(row)
    return pd.DataFrame(rows)
