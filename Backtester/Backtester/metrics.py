# metrics.py
import numpy as np
import pandas as pd


def cumulative_return(nav: pd.Series) -> float:
    nav = nav.dropna()
    if nav.empty:
        return float("nan")
    return float(nav.iloc[-1] / nav.iloc[0] - 1.0)


def max_drawdown(nav: pd.Series) -> float:
    nav = nav.dropna()
    if nav.empty:
        return float("nan")
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min())


def sharpe_ratio(returns: pd.Series, periods_per_year: int = 12, rf: float = 0.0) -> float:
    r = returns.dropna()
    if r.empty:
        return float("nan")
    excess = r - rf / periods_per_year
    vol = excess.std(ddof=1)
    if vol == 0 or np.isnan(vol):
        return float("nan")
    return float(np.sqrt(periods_per_year) * excess.mean() / vol)