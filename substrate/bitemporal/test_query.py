"""Invariant tests for the bitemporal predicate.

Requires a running Postgres with the migration applied. Skipped if DATABASE_URL
is unset or the DB is unreachable.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest

from . import query as q


def _db_available() -> bool:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        with psycopg.connect(url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not available")


@pytest.fixture(autouse=True)
def _clean_test_macro():
    """Wipe the test series before and after each test."""
    series_id = "TEST_PIT_SERIES"
    with q.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM bitemporal_macro_observations WHERE series_id = %s", (series_id,))
        conn.commit()
    yield
    with q.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM bitemporal_macro_observations WHERE series_id = %s", (series_id,))
        conn.commit()


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_returns_only_known_as_of_rows():
    """A query at known_as_of=T must never include rows whose as_of > T."""
    series = "TEST_PIT_SERIES"
    # First release of May 2024 value, published 2024-06-12
    q.insert_fact(
        q.MACRO, series, _ts("2024-05-31"), _ts("2024-06-12"),
        values={"value": 3.3}, source="test",
    )
    # Revised on 2024-08-14
    q.insert_fact(
        q.MACRO, series, _ts("2024-05-31"), _ts("2024-08-14"),
        values={"value": 3.4}, source="test",
    )
    # Query as of 2024-07-01: should see only the 3.3 value.
    rows = q.point_in_time(
        q.MACRO, series,
        world_from=_ts("2024-05-01"),
        world_to=_ts("2024-12-31"),
        known_as_of=_ts("2024-07-01"),
    )
    assert len(rows) == 1
    assert rows[0]["value"] == 3.3

    # Query as of 2024-09-01: revision is visible.
    rows = q.point_in_time(
        q.MACRO, series,
        world_from=_ts("2024-05-01"),
        world_to=_ts("2024-12-31"),
        known_as_of=_ts("2024-09-01"),
    )
    assert len(rows) == 1
    assert rows[0]["value"] == 3.4


def test_returns_only_world_at_rows():
    """A query for world_to=T must never include facts with valid_from > T."""
    series = "TEST_PIT_SERIES"
    q.insert_fact(q.MACRO, series, _ts("2024-01-31"), _ts("2024-02-15"),
                  values={"value": 1.0}, source="test")
    q.insert_fact(q.MACRO, series, _ts("2024-02-29"), _ts("2024-03-15"),
                  values={"value": 2.0}, source="test")
    q.insert_fact(q.MACRO, series, _ts("2024-03-31"), _ts("2024-04-15"),
                  values={"value": 3.0}, source="test")

    rows = q.point_in_time(
        q.MACRO, series,
        world_from=_ts("2024-01-01"),
        world_to=_ts("2024-03-01"),  # excludes the March observation
        known_as_of=_ts("2024-12-31"),
    )
    values = sorted(r["value"] for r in rows)
    assert values == [1.0, 2.0]


def test_latest_vintage_wins_within_period():
    """If three revisions exist for the same valid_from, the latest as_of <= known_as_of wins."""
    series = "TEST_PIT_SERIES"
    q.insert_fact(q.MACRO, series, _ts("2024-05-31"), _ts("2024-06-12"),
                  values={"value": 3.0}, source="test")
    q.insert_fact(q.MACRO, series, _ts("2024-05-31"), _ts("2024-08-14"),
                  values={"value": 3.2}, source="test")
    q.insert_fact(q.MACRO, series, _ts("2024-05-31"), _ts("2024-11-08"),
                  values={"value": 3.4}, source="test")

    rows = q.point_in_time(
        q.MACRO, series,
        world_from=_ts("2024-01-01"),
        world_to=_ts("2024-12-31"),
        known_as_of=_ts("2024-10-01"),
    )
    assert len(rows) == 1
    assert rows[0]["value"] == 3.2  # most recent vintage <= 2024-10-01


def test_empty_when_no_data_yet():
    """Query before any rows exist returns empty (handles ETF inception, pre-publication, etc.)."""
    rows = q.point_in_time(
        q.MACRO, "TEST_PIT_SERIES",
        world_from=_ts("2020-01-01"),
        world_to=_ts("2020-12-31"),
        known_as_of=_ts("2026-01-01"),
    )
    assert rows == []
