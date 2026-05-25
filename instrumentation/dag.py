"""Persist a causal DAG snapshot each time the agent reads it.

The snapshot is keyed by (source_file_hash, as_of) so re-reads on the same
day with the same file content reuse the existing snapshot rather than
inserting duplicates. Returns the snapshot_id which the trace records.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import psycopg

from .store import connection


def upsert_dag_snapshot(content: dict, source_file_hash: str, as_of: datetime) -> UUID:
    with connection() as conn, conn.cursor() as cur:
        # See if an identical snapshot already exists for the same day.
        cur.execute(
            """
            SELECT snapshot_id FROM causal_dag_snapshots
            WHERE source_file_hash = %s AND as_of::date = %s::date
            ORDER BY as_of DESC LIMIT 1
            """,
            (source_file_hash, as_of.date()),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            """
            INSERT INTO causal_dag_snapshots (valid_from, as_of, content, source_file_hash)
            VALUES (%s, %s, %s::jsonb, %s)
            RETURNING snapshot_id
            """,
            (as_of, as_of, json.dumps(content), source_file_hash),
        )
        (snap_id,) = cur.fetchone()
        conn.commit()
    return snap_id
