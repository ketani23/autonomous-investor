"""Persistence for traces, decisions, and gateway evaluations."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .schema import (
    DecisionRecord,
    Phase,
    RiskGatewayEvaluation,
    TraceRecord,
)


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return url


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(_db_url(), autocommit=False) as conn:
        yield conn


def _to_json(value: Any) -> str:
    return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# Trace write/read
# ---------------------------------------------------------------------------


def write_trace(trace: TraceRecord) -> UUID:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_traces (
                trace_id, parent_trace_id, phase, agent_role, model,
                model_version_pinned_at, started_at, completed_at, as_of,
                input_summary, tool_calls, output, reasoning_text,
                token_usage, cost_usd, parent_decision_id
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s,
                %s::jsonb, %s, %s
            )
            ON CONFLICT (trace_id) DO UPDATE SET
                completed_at = EXCLUDED.completed_at,
                tool_calls   = EXCLUDED.tool_calls,
                output       = EXCLUDED.output,
                reasoning_text = EXCLUDED.reasoning_text,
                token_usage  = EXCLUDED.token_usage,
                cost_usd     = EXCLUDED.cost_usd
            """,
            (
                str(trace.trace_id),
                str(trace.parent_trace_id) if trace.parent_trace_id else None,
                trace.phase.value,
                trace.agent_role,
                trace.model,
                trace.model_version_pinned_at,
                trace.started_at,
                trace.completed_at,
                trace.as_of,
                _to_json(trace.input_summary),
                _to_json([tc.model_dump() for tc in trace.tool_calls]),
                _to_json(trace.output),
                trace.reasoning_text,
                _to_json(trace.token_usage.model_dump()),
                trace.cost_usd,
                str(trace.parent_decision_id) if trace.parent_decision_id else None,
            ),
        )
        conn.commit()
    return trace.trace_id


def read_trace(trace_id: UUID) -> TraceRecord | None:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM agent_traces WHERE trace_id = %s", (str(trace_id),))
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_trace(row)


def list_traces(since: datetime | None = None, limit: int = 50) -> list[TraceRecord]:
    if since is None:
        since = datetime.now(tz=timezone.utc) - timedelta(days=7)
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM agent_traces WHERE started_at >= %s "
            "ORDER BY started_at DESC LIMIT %s",
            (since, limit),
        )
        rows = cur.fetchall()
    return [_row_to_trace(r) for r in rows]


def _row_to_trace(row: dict[str, Any]) -> TraceRecord:
    return TraceRecord(
        trace_id=row["trace_id"],
        parent_trace_id=row["parent_trace_id"],
        phase=Phase(row["phase"]),
        agent_role=row["agent_role"],
        model=row["model"],
        model_version_pinned_at=row["model_version_pinned_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        as_of=row["as_of"],
        input_summary=row["input_summary"] or {},
        tool_calls=row["tool_calls"] or [],
        output=row["output"] or {},
        reasoning_text=row["reasoning_text"],
        token_usage=row["token_usage"] or {},
        cost_usd=float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        parent_decision_id=row["parent_decision_id"],
    )


# ---------------------------------------------------------------------------
# Decision write/read
# ---------------------------------------------------------------------------


def write_decision(decision: DecisionRecord) -> UUID:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO portfolio_decisions (
                decision_id, proposed_at, as_of, proposed_portfolio, rationale,
                agent_trace_id, risk_gateway_result, risk_gateway_eval_id,
                human_decision, human_decision_reasoning, executed
            ) VALUES (
                %s, %s, %s, %s::jsonb, %s,
                %s, %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (decision_id) DO UPDATE SET
                risk_gateway_result   = EXCLUDED.risk_gateway_result,
                risk_gateway_eval_id  = EXCLUDED.risk_gateway_eval_id,
                human_decision        = EXCLUDED.human_decision,
                human_decision_reasoning = EXCLUDED.human_decision_reasoning,
                executed              = EXCLUDED.executed
            """,
            (
                str(decision.decision_id),
                decision.proposed_at,
                decision.as_of,
                _to_json(decision.proposed_portfolio),
                decision.rationale,
                str(decision.agent_trace_id) if decision.agent_trace_id else None,
                decision.risk_gateway_result.value,
                str(decision.risk_gateway_eval_id) if decision.risk_gateway_eval_id else None,
                decision.human_decision.value if decision.human_decision else None,
                decision.human_decision_reasoning,
                decision.executed,
            ),
        )
        conn.commit()
    return decision.decision_id


def read_decision(decision_id: UUID) -> DecisionRecord | None:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM portfolio_decisions WHERE decision_id = %s", (str(decision_id),))
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_decision(row)


def list_decisions(limit: int = 20) -> list[DecisionRecord]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM portfolio_decisions ORDER BY proposed_at DESC LIMIT %s",
            (limit,),
        )
        rows = cur.fetchall()
    return [_row_to_decision(r) for r in rows]


def latest_approved_decision() -> DecisionRecord | None:
    """The most recent decision the gateway said yes to. Used to reconstruct
    'current portfolio state' in Phase 1 (no broker reconciliation yet)."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM portfolio_decisions "
            "WHERE risk_gateway_result = 'approved' "
            "ORDER BY proposed_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    return _row_to_decision(row) if row else None


def decision_exists_for_day(day_utc: datetime) -> bool:
    """Idempotency check for the daily run."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM portfolio_decisions "
            "WHERE proposed_at::date = %s::date LIMIT 1",
            (day_utc.date(),),
        )
        return cur.fetchone() is not None


def _row_to_decision(row: dict[str, Any]) -> DecisionRecord:
    from .schema import HumanDecision, RiskGatewayResult

    return DecisionRecord(
        decision_id=row["decision_id"],
        proposed_at=row["proposed_at"],
        as_of=row["as_of"],
        proposed_portfolio=row["proposed_portfolio"] or {},
        rationale=row["rationale"],
        agent_trace_id=row["agent_trace_id"],
        risk_gateway_result=RiskGatewayResult(row["risk_gateway_result"]),
        risk_gateway_eval_id=row["risk_gateway_eval_id"],
        human_decision=HumanDecision(row["human_decision"]) if row["human_decision"] else None,
        human_decision_reasoning=row["human_decision_reasoning"],
        executed=row["executed"],
    )


# ---------------------------------------------------------------------------
# Risk gateway evaluation write/read
# ---------------------------------------------------------------------------


def write_evaluation(ev: RiskGatewayEvaluation) -> UUID:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO risk_gateway_evaluations (
                eval_id, decision_id, evaluated_at,
                input_proposal, input_state, input_config_hash,
                result, reasons, blocking_checks
            ) VALUES (
                %s, %s, %s,
                %s::jsonb, %s::jsonb, %s,
                %s, %s::jsonb, %s::jsonb
            )
            """,
            (
                str(ev.eval_id),
                str(ev.decision_id) if ev.decision_id else None,
                ev.evaluated_at,
                _to_json(ev.input_proposal),
                _to_json(ev.input_state),
                ev.input_config_hash,
                ev.result.value,
                _to_json(ev.reasons),
                _to_json(ev.blocking_checks),
            ),
        )
        conn.commit()
    return ev.eval_id


def read_evaluation(eval_id: UUID) -> RiskGatewayEvaluation | None:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM risk_gateway_evaluations WHERE eval_id = %s",
            (str(eval_id),),
        )
        row = cur.fetchone()
    if not row:
        return None
    from .schema import RiskGatewayResult

    return RiskGatewayEvaluation(
        eval_id=row["eval_id"],
        decision_id=row["decision_id"],
        evaluated_at=row["evaluated_at"],
        input_proposal=row["input_proposal"] or {},
        input_state=row["input_state"] or {},
        input_config_hash=row["input_config_hash"],
        result=RiskGatewayResult(row["result"]),
        reasons=row["reasons"] or [],
        blocking_checks=row["blocking_checks"] or [],
    )
