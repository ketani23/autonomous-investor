"""FRED + ALFRED ingestion.

For series flagged `vintage_tracked = false`: a single fetch with the current
view; as_of = ingestion timestamp.

For series flagged `vintage_tracked = true`: walk ALFRED vintages via FRED's
`/series/observations` endpoint with realtime_start/realtime_end. Each
distinct (observation_date, release_date) tuple becomes its own bitemporal
row. The earliest release we capture is bounded by the series' realtime
availability on FRED (varies — typically a few decades back).

API docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import requests

from substrate.bitemporal import query as bt

FRED_BASE = "https://api.stlouisfed.org/fred"


def _api_key() -> str:
    k = os.environ.get("FRED_API_KEY")
    if not k:
        raise RuntimeError("FRED_API_KEY not set")
    return k


def _get_observations(
    series_id: str,
    observation_start: str,
    observation_end: str,
    realtime_start: str | None = None,
    realtime_end: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch observations from FRED. If realtime_* are set, returns the vintage
    snapshot as it existed during that realtime window. If unset, FRED defaults
    to today's view.
    """
    params: dict[str, Any] = {
        "series_id": series_id,
        "api_key": _api_key(),
        "file_type": "json",
        "observation_start": observation_start,
        "observation_end": observation_end,
    }
    if realtime_start:
        params["realtime_start"] = realtime_start
    if realtime_end:
        params["realtime_end"] = realtime_end

    resp = requests.get(f"{FRED_BASE}/series/observations", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("observations", [])


# ---------------------------------------------------------------------------
# Non-vintage ingestion: current view, single fetch
# ---------------------------------------------------------------------------


def ingest_latest(series_id: str, years: int = 5, as_of: datetime | None = None) -> int:
    end = datetime.now(tz=timezone.utc)
    start = end.replace(year=end.year - years)
    as_of = as_of or end
    obs = _get_observations(
        series_id,
        observation_start=start.strftime("%Y-%m-%d"),
        observation_end=end.strftime("%Y-%m-%d"),
    )
    rows = _obs_to_rows(series_id, obs, as_of_for_all=as_of, source="fred")
    n = bt.bulk_insert_facts(bt.MACRO, rows)
    print(f"  {series_id}: {n} rows (latest)")
    return n


# ---------------------------------------------------------------------------
# Vintage ingestion: walk every release date in the window
# ---------------------------------------------------------------------------


def ingest_vintages(series_id: str, years: int = 5) -> int:
    """Capture each vintage (release) in the window. We discover release dates
    via FRED's `/series/vintagedates` endpoint, then snapshot each one."""
    end = datetime.now(tz=timezone.utc)
    start = end.replace(year=end.year - years)

    # 1. Discover release dates
    resp = requests.get(
        f"{FRED_BASE}/series/vintagedates",
        params={
            "series_id": series_id,
            "api_key": _api_key(),
            "file_type": "json",
            "realtime_start": start.strftime("%Y-%m-%d"),
            "realtime_end": end.strftime("%Y-%m-%d"),
        },
        timeout=30,
    )
    resp.raise_for_status()
    vintage_dates = resp.json().get("vintage_dates", [])
    if not vintage_dates:
        print(f"  {series_id}: no vintages in window")
        return 0

    total = 0
    # 2. For each release, snapshot what the series looked like at that point.
    # We use the realtime_start=realtime_end=release_date trick so we get
    # exactly the snapshot as it was on that release date.
    for vd in vintage_dates:
        obs = _get_observations(
            series_id,
            observation_start=start.strftime("%Y-%m-%d"),
            observation_end=end.strftime("%Y-%m-%d"),
            realtime_start=vd,
            realtime_end=vd,
        )
        # The as_of for this vintage is the release date (vd) at noon UTC —
        # FRED publishes during US business hours so this is a reasonable
        # standin for "when the system would have learned this value."
        as_of_ts = datetime.strptime(vd, "%Y-%m-%d").replace(
            hour=12, tzinfo=timezone.utc
        )
        rows = _obs_to_rows(
            series_id,
            obs,
            as_of_for_all=as_of_ts,
            source="alfred",
            vintage_date=date.fromisoformat(vd),
        )
        n = bt.bulk_insert_facts(bt.MACRO, rows)
        total += n
    print(f"  {series_id}: {total} rows across {len(vintage_dates)} vintages")
    return total


# ---------------------------------------------------------------------------
# Dispatcher driven by config
# ---------------------------------------------------------------------------


def ingest_macro_universe(years: int = 5) -> int:
    """Iterate over config/macro_series.toml and dispatch each series to the
    right ingestion path based on its `vintage_tracked` flag."""
    from substrate.config import load_macro_series

    total = 0
    for s in load_macro_series():
        if s.vintage_tracked:
            total += ingest_vintages(s.id, years=years)
        else:
            total += ingest_latest(s.id, years=years)
    return total


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _obs_to_rows(
    series_id: str,
    obs: list[dict[str, Any]],
    as_of_for_all: datetime,
    source: str,
    vintage_date: date | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for o in obs:
        if o.get("value") in (".", "", None):
            continue  # FRED uses '.' for missing values
        try:
            v = float(o["value"])
        except (TypeError, ValueError):
            continue
        valid_from = datetime.strptime(o["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        rows.append({
            "series_id": series_id,
            "valid_from": valid_from,
            "as_of": as_of_for_all,
            "source": source,
            "value": v,
            "vintage_date": vintage_date,
        })
    return rows
