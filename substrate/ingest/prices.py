"""Daily OHLCV ingestion via yfinance.

Each row written to `bitemporal_market_prices` has:
    valid_from = trading day (UTC midnight)
    as_of      = ingestion timestamp

Equity prices don't get materially revised post-adjustment (splits/dividends
fold into adj_close on the day they happen), so for Phase 1 we ingest the
current view once and treat new ingestions of the same trading day as new
rows with later as_of. The point-in-time query picks the latest as_of <= cutoff.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import yfinance as yf

from substrate.bitemporal import query as bt


def fetch_prices(symbol: str, start: str, end: str | None = None) -> pd.DataFrame:
    """Fetch daily OHLCV for one symbol from yfinance. Returns a DataFrame
    indexed by date with columns Open/High/Low/Close/Adj Close/Volume.
    Empty frame if nothing returned.
    """
    df = yf.download(
        symbol,
        start=start,
        end=end,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    # yfinance now returns a MultiIndex on columns when threads=True or by default.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def ingest_symbol(
    symbol: str,
    start: str,
    end: str | None = None,
    as_of: datetime | None = None,
) -> int:
    """Ingest one symbol's prices. Returns number of rows inserted."""
    as_of = as_of or datetime.now(tz=timezone.utc)
    df = fetch_prices(symbol, start, end)
    if df.empty:
        print(f"  {symbol}: no data returned")
        return 0

    rows = []
    for ts, r in df.iterrows():
        valid_from = (
            ts.to_pydatetime().replace(tzinfo=timezone.utc)
            if hasattr(ts, "to_pydatetime")
            else datetime.combine(ts, datetime.min.time()).replace(tzinfo=timezone.utc)
        )
        rows.append({
            "symbol": symbol,
            "valid_from": valid_from,
            "as_of": as_of,
            "source": "yfinance",
            "open": _nullable_float(r.get("Open")),
            "high": _nullable_float(r.get("High")),
            "low": _nullable_float(r.get("Low")),
            "close": _nullable_float(r.get("Close")),
            "adj_close": _nullable_float(r.get("Adj Close")),
            "volume": _nullable_int(r.get("Volume")),
        })
    n = bt.bulk_insert_facts(bt.PRICES, rows)
    print(f"  {symbol}: {n} rows")
    return n


def ingest_universe(symbols: Iterable[str], years: int = 5) -> int:
    """Backfill `years` of history for every symbol."""
    end = datetime.now(tz=timezone.utc)
    start = (end.replace(year=end.year - years)).strftime("%Y-%m-%d")
    total = 0
    for s in symbols:
        total += ingest_symbol(s, start=start)
    return total


def _nullable_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _nullable_int(v) -> int | None:
    f = _nullable_float(v)
    return int(f) if f is not None else None
