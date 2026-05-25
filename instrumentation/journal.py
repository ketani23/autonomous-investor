"""Decision journal — what the agent reads at the start of each run.

In Phase 1 this is just 'the last N decisions with their rationales'. Phase 2
expands to curated principles and a strategy graveyard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from .store import connection


def read_recent_decisions(n: int = 10) -> list[dict[str, Any]]:
    """Returns recent decision summaries the agent can read at run start.
    Excludes the full proposed_portfolio jsonb for brevity — the agent gets
    decision id + rationale + gateway result, which is the most useful prior
    context. It can drill into full details via a separate tool if needed.
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT decision_id, proposed_at, as_of, proposed_portfolio,
                   rationale, risk_gateway_result, executed
            FROM portfolio_decisions
            ORDER BY proposed_at DESC
            LIMIT %s
            """,
            (n,),
        )
        rows = cur.fetchall()

    # Convert UUIDs/datetimes to strings for safe agent consumption.
    out = []
    for r in rows:
        out.append({
            "decision_id": str(r["decision_id"]),
            "proposed_at": r["proposed_at"].isoformat() if r["proposed_at"] else None,
            "as_of": r["as_of"].isoformat() if r["as_of"] else None,
            "proposed_portfolio": r["proposed_portfolio"],
            "rationale": r["rationale"],
            "risk_gateway_result": r["risk_gateway_result"],
            "executed": r["executed"],
        })
    return out
