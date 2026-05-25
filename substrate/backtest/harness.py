"""Backtest harness — a thin wrapper over vectorbt that enforces as_of correctness.

The harness's contract with the rest of the system: at simulation date T,
the backtest sees only data with `as_of <= T`. This is the structural
anti-foreknowledge guarantee. All price reads go through the bitemporal
point_in_time query.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

import numpy as np
import pandas as pd

from substrate.bitemporal import query as bt


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series        # indexed by date, value in dollars (initial = initial_cash)
    daily_returns: pd.Series
    sharpe: float
    max_drawdown: float
    final_value: float
    initial_value: float
    n_rebalances: int


def load_price_panel(
    symbols: list[str],
    start: datetime,
    end: datetime,
    known_as_of: datetime,
    field: str = "adj_close",
) -> pd.DataFrame:
    """Load a wide price panel via the bitemporal predicate.

    `field` defaults to adj_close so backtests are dividend/split-adjusted.
    Returns a DataFrame indexed by date (UTC midnight) with one column per symbol.
    Rows where ANY symbol is NaN are kept — the caller decides how to handle them.
    """
    frames = []
    for sym in symbols:
        rows = bt.point_in_time(
            bt.PRICES, sym,
            world_from=start, world_to=end, known_as_of=known_as_of,
        )
        if not rows:
            continue
        s = pd.Series(
            {r["valid_from"]: r[field] for r in rows},
            name=sym,
        )
        frames.append(s)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, axis=1).sort_index()
    df.index = pd.DatetimeIndex(df.index)
    return df


def run_monthly_rebalance(
    weights: Mapping[str, float],
    start: datetime,
    end: datetime,
    known_as_of: datetime,
    initial_cash: float = 100_000.0,
) -> BacktestResult:
    """Buy-and-hold monthly rebalance. At the first trading day of each month,
    re-set holdings to the target weights. Between rebalances the book drifts
    with market moves.

    Hand-rolled rather than dropping into vectorbt for clarity in Phase 1 —
    the substrate is meant to be adequate and readable, not impressive.
    The vectorbt dependency is reserved for richer strategies later.
    """
    symbols = list(weights.keys())
    panel = load_price_panel(symbols, start, end, known_as_of, field="adj_close")
    if panel.empty:
        raise ValueError("No price data in the requested window")

    panel = panel.dropna(how="any")  # require all symbols available
    if panel.empty:
        raise ValueError(
            f"After dropping NaNs, no rows remain. Some symbols may not have "
            f"prices in this window with known_as_of {known_as_of.isoformat()}."
        )

    target = pd.Series(weights, dtype=float)
    target = target.reindex(panel.columns).fillna(0.0)

    # Rebalance dates = first available trading day of each (year, month).
    rebal_dates = (
        panel.index.to_series()
        .groupby([panel.index.year, panel.index.month])
        .first()
        .tolist()
    )

    cash = 0.0
    shares = pd.Series(0.0, index=panel.columns)
    equity = []

    for date in panel.index:
        prices = panel.loc[date]
        if date in rebal_dates:
            position_value = (shares * prices).sum() + cash
            target_dollars = target * position_value
            shares = target_dollars / prices
            cash = position_value - (shares * prices).sum()
            if not equity:  # first ever rebalance — initial deposit
                shares = (target * initial_cash) / prices
                cash = initial_cash - (shares * prices).sum()
        portfolio_value = (shares * prices).sum() + cash
        equity.append(portfolio_value)

    equity_curve = pd.Series(equity, index=panel.index, name="equity")
    daily_returns = equity_curve.pct_change().fillna(0.0)
    return BacktestResult(
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        sharpe=_sharpe(daily_returns),
        max_drawdown=_max_drawdown(equity_curve),
        final_value=float(equity_curve.iloc[-1]),
        initial_value=float(initial_cash),
        n_rebalances=len(rebal_dates),
    )


def _sharpe(daily_returns: pd.Series, periods_per_year: int = 252) -> float:
    if daily_returns.std() == 0 or len(daily_returns) < 2:
        return 0.0
    return float(
        np.sqrt(periods_per_year) * daily_returns.mean() / daily_returns.std()
    )


def _max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min())


# ---------------------------------------------------------------------------
# Convenience: the prompt's baseline
# ---------------------------------------------------------------------------


BASELINE_60_40_WEIGHTS = {"SPY": 0.60, "IEF": 0.40}


def run_baseline_60_40(
    start: datetime,
    end: datetime,
    known_as_of: datetime | None = None,
    initial_cash: float = 100_000.0,
) -> BacktestResult:
    """SPY/IEF 60/40 monthly rebalance. Defaults known_as_of to `end` (i.e.
    'with everything we knew by the end of the backtest window')."""
    if known_as_of is None:
        known_as_of = end
    return run_monthly_rebalance(
        BASELINE_60_40_WEIGHTS, start, end, known_as_of, initial_cash=initial_cash
    )
