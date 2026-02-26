# allocation.py
from __future__ import annotations
from typing import List
import numpy as np
import pandas as pd

def momentum_12_1_long_short_eqw(
    prices: pd.DataFrame,
    members: List[str],
    asof_date: pd.Timestamp,
    top_frac: float = 0.2,
    bottom_frac: float = 0.2,
    gross: float = 1.0,
) -> pd.Series:
    """
    Momentum 12-1 mensuel
    Long top 2 déciles / short bottom 2 déciles en EQW.
    """
    t = pd.Timestamp(asof_date)
    if t not in prices.index:
        return pd.Series(dtype=float)

    pos = prices.index.get_loc(t)
    if pos < 13:
        return pd.Series(dtype=float)

    p_t2 = prices.iloc[pos - 1]    ###
    p_t13 = prices.iloc[pos - 12]   ###

    cols = [c for c in members if c in prices.columns]
    if not cols:
        return pd.Series(dtype=float)

    score = (p_t2[cols] / p_t13[cols] - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
    if score.empty:
        return pd.Series(dtype=float)

    n = len(score)
    n_long = max(1, int(np.floor(top_frac * n)))
    n_short = max(1, int(np.floor(bottom_frac * n)))

    winners = score.sort_values(ascending=False).head(n_long).index
    losers = score.sort_values(ascending=True).head(n_short).index

    long_total = gross / 2.0
    short_total = gross / 2.0

    w = pd.Series(0.0, index=prices.columns)
    w.loc[winners] = + long_total / len(winners)
    w.loc[losers] = - short_total / len(losers)

    return w[w != 0.0]