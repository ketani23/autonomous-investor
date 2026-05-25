"""Anti-foreknowledge regression test for the backtest harness.

The contract: at simulation date T, the harness must only see data with
`as_of <= T`. We verify this by:

1. Setting up a synthetic test symbol with two distinct as_of cohorts —
   an early ingestion and a later "restatement" that changes some prices.
2. Running the backtest twice on the same window:
   - Run A: known_as_of = early cutoff (restatement invisible)
   - Run B: known_as_of = late cutoff (restatement visible)
3. Verifying the equity curves DIFFER (proving the as_of restriction is
   actually being respected — if the harness ignored as_of, the two runs
   would produce identical results).

Also runs the live baseline 60/40 against real SPY/IEF data to validate
the harness end-to-end on the substrate.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from substrate.backtest.harness import (
    BASELINE_60_40_WEIGHTS,
    run_baseline_60_40,
    run_monthly_rebalance,
)
from substrate.bitemporal import query as bt


def _db_available() -> bool:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        with psycopg.connect(url, connect_timeout=2):
            pass
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not available")


# ---------------------------------------------------------------------------
# Synthetic as_of test
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_symbols():
    """Create two test symbols (AAA, BBB) with daily prices for ~60 trading days.
    Each price's as_of is the trading day itself (realistic: prices for day D
    become known on day D). Then insert a restatement (as_of = 2024-06-01) that
    bumps AAA prices by 20% for dates after Dec 15.

    Yields (start, end, mid, late_cutoff) where:
      - start ... end: the data window
      - mid: a cutoff before the restatement
      - late_cutoff: after the restatement
    """
    start = datetime(2023, 11, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    restate_at = datetime(2023, 12, 15, tzinfo=timezone.utc)
    late_as_of = datetime(2024, 6, 1, tzinfo=timezone.utc)
    mid = datetime(2023, 12, 14, tzinfo=timezone.utc)  # pre-restatement cutoff

    _wipe(["AAA", "BBB"])

    cur = start
    rows = []
    aaa_price = 100.0
    bbb_price = 50.0
    while cur <= end:
        if cur.weekday() < 5:
            aaa_price *= 1.001
            bbb_price *= 1.0005
            for sym, px in (("AAA", aaa_price), ("BBB", bbb_price)):
                rows.append({
                    "symbol": sym,
                    "valid_from": cur,
                    "as_of": cur,        # realistic: price for day D known on day D
                    "source": "synth",
                    "open": px, "high": px, "low": px,
                    "close": px, "adj_close": px,
                    "volume": 1000,
                })
        cur += timedelta(days=1)
    bt.bulk_insert_facts(bt.PRICES, rows)

    # Restatement: bump AAA prices by 20% with a later as_of for dates >= Dec 15.
    restated = [
        {**r, "adj_close": r["adj_close"] * 1.20, "close": r["close"] * 1.20, "as_of": late_as_of}
        for r in rows
        if r["symbol"] == "AAA" and r["valid_from"] >= restate_at
    ]
    bt.bulk_insert_facts(bt.PRICES, restated)

    yield (start, end, mid, late_as_of)

    _wipe(["AAA", "BBB"])


def _wipe(symbols: list[str]):
    with bt.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM bitemporal_market_prices WHERE symbol = ANY(%s)",
            (symbols,),
        )
        conn.commit()


def test_as_of_restriction_changes_backtest_results(synthetic_symbols):
    """If the harness respects as_of, a cutoff BEFORE the restatement
    publication date must NOT see the 20% bump, while a cutoff AFTER does.
    Their equity curves must therefore differ in the expected direction."""
    start, end, mid, late = synthetic_symbols
    weights = {"AAA": 0.8, "BBB": 0.2}

    # known_as_of = end-of-window (everything visible as known by end date,
    # but only original prices since restatement is at 2024-06-01 > end)
    run_pre_restatement = run_monthly_rebalance(weights, start, end, known_as_of=end)
    # known_as_of = late (after the restatement publication; restatement visible)
    run_post_restatement = run_monthly_rebalance(weights, start, end, known_as_of=late)

    # Pre-Dec 15 dates: both runs should be IDENTICAL (no restatement applies
    # to those dates' valid_from regardless of known_as_of).
    pre_dates = run_pre_restatement.equity_curve.index <= mid
    assert pre_dates.sum() > 0
    assert (
        run_pre_restatement.equity_curve[pre_dates].values
        == run_post_restatement.equity_curve[pre_dates].values
    ).all(), (
        "Pre-restatement equity curves must match — divergence here means the "
        "as_of cutoff is leaking restated prices into earlier valid_from dates."
    )

    # Final value: post-restatement run should be ~20% higher on the AAA
    # portion (80% of book), so ~16% higher overall — at minimum 10%.
    assert run_post_restatement.final_value > run_pre_restatement.final_value * 1.10, (
        f"Post-restatement terminal {run_post_restatement.final_value} should be "
        f">10% higher than pre-restatement {run_pre_restatement.final_value}."
    )


def test_as_of_run_a_run_b_invariant(synthetic_symbols):
    """The structural invariant from the plan:
       Run A: backtest full window with known_as_of = late (restatement visible).
       Run B: backtest shorter window with known_as_of = mid (restatement invisible).
       Up to mid, the two curves should match — both see the original prices
       (restatement only applies to valid_from >= Dec 15).
    """
    start, end, mid, late = synthetic_symbols
    weights = {"AAA": 0.8, "BBB": 0.2}

    run_a_full = run_monthly_rebalance(weights, start, end, known_as_of=late)
    run_b_short = run_monthly_rebalance(weights, start, mid, known_as_of=mid)

    common = run_a_full.equity_curve.index.intersection(run_b_short.equity_curve.index)
    assert len(common) > 0
    a_common = run_a_full.equity_curve.loc[common]
    b_common = run_b_short.equity_curve.loc[common]
    assert (a_common.values == b_common.values).all(), (
        "Run A and Run B must agree on dates <= mid — both see original "
        "(pre-restatement) prices there."
    )


# ---------------------------------------------------------------------------
# Live baseline 60/40 against real backfilled data
# ---------------------------------------------------------------------------


def _have_baseline_prices() -> bool:
    try:
        with bt.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM bitemporal_market_prices "
                "WHERE symbol IN ('SPY','IEF')"
            )
            (n,) = cur.fetchone()
        return n > 100
    except Exception:
        return False


@pytest.mark.skipif(not _have_baseline_prices(), reason="No backfilled SPY/IEF prices")
def test_baseline_60_40_produces_sensible_equity_curve():
    """Run the 60/40 baseline on real data. Sanity checks:
       - equity curve is positive throughout
       - final value is within a plausible range (not 10x or 0.1x of initial)
       - max drawdown is between -50% and 0
    """
    end = datetime.now(tz=timezone.utc)
    start = end.replace(year=end.year - 3)  # 3 years
    result = run_baseline_60_40(start, end, initial_cash=100_000.0)

    assert (result.equity_curve > 0).all()
    assert 50_000 < result.final_value < 300_000, (
        f"60/40 over 3y producing {result.final_value} is implausible — "
        f"check backfill quality."
    )
    assert -0.50 < result.max_drawdown <= 0
    assert result.n_rebalances >= 30  # ~12 per year × 3y
