# main.py
import datetime as dt
import pandas as pd
import numpy as np
 
from bloomberg import (
    fetch_bbg_data,
    export_portfolio_weights_to_import_in_bbg
)
from allocation import momentum_12_1_long_short_eqw
from metrics import sharpe_ratio, max_drawdown, cumulative_return
from visualisations import plot_navs
 
 
def _period_return(prices: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp) -> pd.Series:
    p0 = prices.loc[t0]
    p1 = prices.loc[t1]
    r = (p1 / p0 - 1.0).replace([np.inf, -np.inf], np.nan)
    return r
 
 
def _apply_weights_on_period(weights: pd.Series, period_ret: pd.Series) -> float:

    w0 = weights.copy()
    r0 = period_ret.reindex(w0.index)
 
    valid = r0.notna()
    w = w0[valid]
    r = r0[valid]
 
    if w.empty:
        return 0.0
 
    target_pos = w0[w0 > 0].sum() 
    target_neg = w0[w0 < 0].sum() 
 
    w_pos = w[w > 0]
    w_neg = w[w < 0]
 
    # Si une jambe disparaît, on skip le mois pour ne pas casser la neutralité
    if w_pos.empty or w_neg.empty:
        return 0.0
 
    if w_pos.sum() != 0:
        w_pos = w_pos / w_pos.sum() * target_pos
    if w_neg.sum() != 0:
        w_neg = w_neg / w_neg.sum() * target_neg
 
    w2 = pd.concat([w_pos, w_neg])
    r2 = r.reindex(w2.index)
    return float((w2 * r2).sum())
 
 
def long_short_leg_returns(w: pd.Series, r_period: pd.Series) -> tuple[float, float, float]:

    w = w.dropna()
    r = r_period.reindex(w.index).dropna()
    w = w.reindex(r.index)
 
    w_pos = w[w > 0]
    w_neg = w[w < 0]
 
    if w_pos.empty or w_neg.empty:
        return 0.0, 0.0, 0.0
 
    w_pos_norm = w_pos / w_pos.sum()
    r_long_leg = float((w_pos_norm * r.reindex(w_pos_norm.index)).sum())
 
    w_neg_abs = (-w_neg)
    w_neg_norm = w_neg_abs / w_neg_abs.sum()
    r_short_leg = float((w_neg_norm * r.reindex(w_neg_norm.index)).sum())
 
    long_total = float(w_pos.sum())
    short_total = float((-w_neg).sum())
 
    # Perf L/S = + long_total * long_leg  - short_total * short_leg
    r_ls = long_total * r_long_leg - short_total * r_short_leg
 
    return r_long_leg, r_short_leg, r_ls
 
 
def backtest_momentum_ls(
    prices: pd.DataFrame,
    members_by_month: dict[pd.Timestamp, list[str]],
    gross: float = 1.0,
    bt_start: pd.Timestamp | None = None,
    debug_check: bool = False,
) -> tuple[pd.Series, pd.Series, dict[pd.Timestamp, pd.Series], pd.Series, pd.Series]:

    dates = sorted([d for d in members_by_month.keys() if d in prices.index])
    if len(dates) < 15:
        empty = pd.Series(dtype=float)
        return empty, empty, {}, empty, empty
 
    nav_index = []
    rets = []
    long_leg_rets = []
    short_leg_rets = []
    weights_by_date = {}
 
    for i in range(13, len(dates) - 1):
        t = dates[i]
        t1 = dates[i + 1]
 
        if bt_start is not None and t < bt_start:
            continue
 
        w = momentum_12_1_long_short_eqw(
            prices=prices,
            members=members_by_month[t],
            asof_date=t,
            top_frac=0.2,
            bottom_frac=0.2,
            gross=gross,
        )
        if w.empty:
            continue
 
        weights_by_date[t] = w
 
        r_period = _period_return(prices, t, t1)
 
        r_long_leg, r_short_leg, rp_check = long_short_leg_returns(w, r_period)
        long_leg_rets.append(r_long_leg)
        short_leg_rets.append(r_short_leg)
 
        rp = _apply_weights_on_period(w, r_period)
 
        if debug_check and abs(rp - rp_check) > 1e-8:
            print(
                f"[WARN] {pd.Timestamp(t).date()} rp={rp:.6f} rp_check={rp_check:.6f} "
                f"long_leg={r_long_leg:.6f} short_leg={r_short_leg:.6f}"
            )
 
        nav_index.append(t1)
        rets.append(rp)
 
    ret_s = pd.Series(rets, index=pd.to_datetime(nav_index), name="RET_MOM_LS")
    nav_s = (1.0 + ret_s).cumprod()
    nav_s.name = "NAV_MOM_LS"
    if not nav_s.empty:
        nav_s = nav_s / nav_s.iloc[0]
 
    ret_long = pd.Series(long_leg_rets, index=pd.to_datetime(nav_index), name="RET_LONG_LEG")
    ret_short = pd.Series(short_leg_rets, index=pd.to_datetime(nav_index), name="RET_SHORT_LEG")
 
    return nav_s, ret_s, weights_by_date, ret_long, ret_short
 
 
def benchmark_nav(prices: pd.DataFrame, benchmark_col: str = "SPX Index") -> tuple[pd.Series, pd.Series]:
    px = prices[benchmark_col].dropna()
    r = px.pct_change().dropna()
    nav = (1.0 + r).cumprod()
    nav.name = "NAV_BENCH"
    r.name = "RET_BENCH"
    if not nav.empty:
        nav = nav / nav.iloc[0]
    return nav, r
 
 
def main():
    today = dt.date.today()
    data_start = dt.date(today.year - 12, today.month, 28)
    data_end = today
 
    prices, members_by_month = fetch_bbg_data(
        start=data_start,
        end=data_end,
        index_ticker="SPX Index",
        benchmark_ticker="SPX Index",
        anchor_day=28,
        cache_dir="./cache",
        batch_size=150,
    )
 
    # Fenêtre commune (10 ans)
    bt_end = prices.index.max()
    target_date = bt_end - pd.DateOffset(years=10)
    bt_start = prices.index[prices.index >= target_date].min()
 
    # Benchmark sur la même fenêtre
    nav_b, ret_b = benchmark_nav(prices, benchmark_col="SPX Index")
    nav_b = nav_b.loc[nav_b.index >= bt_start]
    ret_b = ret_b.loc[ret_b.index >= bt_start]
 
    # Backtest momentum + legs
    gross = 1.0
    nav_mom, ret_mom, w_mom, ret_long_leg, ret_short_leg = backtest_momentum_ls(
        prices=prices,
        members_by_month=members_by_month,
        gross=gross,
        bt_start=bt_start,
        debug_check=False, 
    )
 
    # Alignement 
    common_idx = nav_b.index.intersection(nav_mom.index)
    nav_b = nav_b.reindex(common_idx)
    ret_b = ret_b.reindex(common_idx)
    nav_mom = nav_mom.reindex(common_idx)
    ret_mom = ret_mom.reindex(common_idx)
    ret_long_leg = ret_long_leg.reindex(common_idx)
    ret_short_leg = ret_short_leg.reindex(common_idx)
 
   
    if not nav_b.empty:
        nav_b = nav_b / nav_b.iloc[0]
    if not nav_mom.empty:
        nav_mom = nav_mom / nav_mom.iloc[0]
 
    # --- Contribution des legs au portefeuille ---
    long_total = gross / 2.0   # gross=1 => 0.5
    short_total = gross / 2.0  # gross=1 => 0.5
 
    ret_long_contrib = long_total * ret_long_leg
    ret_short_contrib = -short_total * ret_short_leg  
 
    # NAV diagnostics
    nav_long_leg = (1.0 + ret_long_leg.dropna()).cumprod()
    if not nav_long_leg.empty:
        nav_long_leg = nav_long_leg / nav_long_leg.iloc[0]
 
    nav_short_underlying = (1.0 + ret_short_leg.dropna()).cumprod()
    if not nav_short_underlying.empty:
        nav_short_underlying = nav_short_underlying / nav_short_underlying.iloc[0]
 
    nav_long_contrib = (1.0 + ret_long_contrib.dropna()).cumprod()
    if not nav_long_contrib.empty:
        nav_long_contrib = nav_long_contrib / nav_long_contrib.iloc[0]
 
    nav_short_contrib = (1.0 + ret_short_contrib.dropna()).cumprod()
    if not nav_short_contrib.empty:
        nav_short_contrib = nav_short_contrib / nav_short_contrib.iloc[0]
 
    # Print metrics
    print("\n=== Momentum L/S (10y window) ===")
    print("Cumulative return:", cumulative_return(nav_mom))
    print("Max drawdown:", max_drawdown(nav_mom))
    print("Sharpe:", sharpe_ratio(ret_mom))
 
    print("\n=== Long leg (winners, 100%) ===")
    print("Cumulative return:", cumulative_return(nav_long_leg))
    print("Max drawdown:", max_drawdown(nav_long_leg))
    print("Sharpe:", sharpe_ratio(ret_long_leg))
 
    print("\n=== Short underlying (losers, 100% long-equivalent) ===")
    print("Cumulative return:", cumulative_return(nav_short_underlying))
    print("Max drawdown:", max_drawdown(nav_short_underlying))
    print("Sharpe:", sharpe_ratio(ret_short_leg))
 
    print("\n=== Benchmark SPX (10y window) ===")
    print("Cumulative return:", cumulative_return(nav_b))
    print("Max drawdown:", max_drawdown(nav_b))
    print("Sharpe:", sharpe_ratio(ret_b))
 
    # Plots
    plot_navs(
        {
            "Momentum L/S": nav_mom,
            "SPX": nav_b,
        },
        title="NAV Performance Comparison for the last 10 years",
    )
 
    # Plot diagnostic legs + contributions
    plot_navs(
        {
            "Long leg (100%)": nav_long_leg,
            "Short underlying (100%)": nav_short_underlying,
            "Long contrib (+gross/2)": nav_long_contrib,
            "Short contrib (-gross/2)": nav_short_contrib,
            "Total L/S": nav_mom,
        },
        title="Momentum legs & contributions (diagnostic)",
    )
 
    # Export pour BBU 
    export_portfolio_weights_to_import_in_bbg(
        weights_by_date=w_mom,
        portfolio_name="PORT_MOM_LS",
        filepath="./PORT_MOM_LS_BBU_HISTORY.xlsx",
        sheet_name="MOM_LS",
        start=bt_start,
        end=bt_end,
    )
 
    print("Portfolio history exports: PORT_MOM_LS_BBU_HISTORY.xlsx")
 
 
if __name__ == "__main__":
    main()