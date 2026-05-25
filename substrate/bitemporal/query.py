"""Bitemporal query helpers.

Predicate (the contract):
    valid_from <= world_at
    AND (valid_to IS NULL OR valid_to > world_at)
    AND as_of <= known_as_of

`world_at` answers "what was true about X at time T?"
`known_as_of` answers "with information available as of T'?"

A backtest at simulation date T calls with known_as_of=T.
The agent at decision time calls with known_as_of=now().
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return url


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(_db_url(), autocommit=False) as conn:
        yield conn


@dataclass(frozen=True)
class BitemporalKey:
    table: str
    primary_dim: str  # column name: 'symbol' for prices, 'series_id' for macro
    value_columns: tuple[str, ...]


PRICES = BitemporalKey(
    table="bitemporal_market_prices",
    primary_dim="symbol",
    value_columns=("open", "high", "low", "close", "adj_close", "volume"),
)

MACRO = BitemporalKey(
    table="bitemporal_macro_observations",
    primary_dim="series_id",
    value_columns=("value",),
)


def point_in_time(
    key: BitemporalKey,
    dim_value: str,
    world_from: datetime,
    world_to: datetime,
    known_as_of: datetime,
) -> list[dict[str, Any]]:
    """Return rows for `dim_value` between [world_from, world_to], known by `known_as_of`.

    For each (dim, valid_from) we take the LATEST as_of <= known_as_of (so a revision
    published before known_as_of supersedes the original; a revision published AFTER
    is invisible). Ordered by valid_from ascending.
    """
    cols = ", ".join(("valid_from", "as_of", *key.value_columns))
    sql = f"""
        WITH eligible AS (
            SELECT {cols},
                   row_number() OVER (
                       PARTITION BY valid_from
                       ORDER BY as_of DESC
                   ) AS rn
            FROM {key.table}
            WHERE {key.primary_dim} = %(dim)s
              AND valid_from >= %(world_from)s
              AND valid_from <= %(world_to)s
              AND as_of <= %(known_as_of)s
        )
        SELECT {cols}
        FROM eligible
        WHERE rn = 1
        ORDER BY valid_from ASC
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql,
            {
                "dim": dim_value,
                "world_from": world_from,
                "world_to": world_to,
                "known_as_of": known_as_of,
            },
        )
        return list(cur.fetchall())


def insert_fact(
    key: BitemporalKey,
    dim_value: str,
    valid_from: datetime,
    as_of: datetime,
    values: dict[str, Any],
    source: str,
    extras: dict[str, Any] | None = None,
) -> None:
    """Insert one bitemporal row. No supersession bookkeeping in Phase 1 — we rely
    on the `as_of DESC` selection in `point_in_time` to pick the latest known view.
    (Restatements are additive rows, not in-place updates.)
    """
    extras = extras or {}
    cols = [key.primary_dim, "valid_from", "as_of", "source", *key.value_columns, *extras.keys()]
    placeholders = ["%s"] * len(cols)
    args: list[Any] = [
        dim_value,
        valid_from,
        as_of,
        source,
        *(values.get(c) for c in key.value_columns),
        *extras.values(),
    ]
    sql = (
        f"INSERT INTO {key.table} ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)})"
    )
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()


def bulk_insert_facts(
    key: BitemporalKey,
    rows: list[dict[str, Any]],
) -> int:
    """Bulk insert. Each row must contain primary_dim, valid_from, as_of, source,
    plus the value columns and any extras supported by the table.
    """
    if not rows:
        return 0
    # Use a stable column order from the first row's keys.
    cols = list(rows[0].keys())
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO {key.table} ({', '.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        n = cur.rowcount
        conn.commit()
    return n
