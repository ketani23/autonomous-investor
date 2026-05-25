"""Smoke tests for trace + decision round-trip through Postgres.

Requires DATABASE_URL pointing at a DB with the migration applied.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest

from . import store
from .schema import (
    DecisionRecord,
    Phase,
    RiskGatewayEvaluation,
    RiskGatewayResult,
    ToolCall,
    TokenUsage,
    TraceRecord,
)


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


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_trace_round_trip():
    trace_id = uuid4()
    started = _utcnow()
    t = TraceRecord(
        trace_id=trace_id,
        phase=Phase.PERCEIVE,
        agent_role="monolithic",
        model="claude-sonnet-4-6",
        model_version_pinned_at="claude-sonnet-4-6",
        started_at=started,
        completed_at=started,
        as_of=started,
        input_summary={"prompt": "hello"},
        tool_calls=[
            ToolCall(
                name="get_prices",
                input={"symbol": "SPY", "start": "2024-01-01", "end": "2024-12-31"},
                output={"rows": 252},
                latency_ms=12.5,
                tokens_in=120,
                tokens_out=40,
            )
        ],
        output={"hypothesis": "yields curve steepening"},
        reasoning_text="thinking text",
        token_usage=TokenUsage(input_tokens=500, output_tokens=200),
        cost_usd=0.0042,
    )
    store.write_trace(t)
    loaded = store.read_trace(trace_id)
    assert loaded is not None
    assert loaded.trace_id == trace_id
    assert loaded.agent_role == "monolithic"
    assert loaded.phase == Phase.PERCEIVE
    assert loaded.model_version_pinned_at == "claude-sonnet-4-6"
    assert len(loaded.tool_calls) == 1
    assert loaded.tool_calls[0].name == "get_prices"
    assert loaded.token_usage.input_tokens == 500


def test_evaluation_then_decision_round_trip():
    decision_id = uuid4()
    eval_id = uuid4()
    now = _utcnow()

    ev = RiskGatewayEvaluation(
        eval_id=eval_id,
        decision_id=decision_id,
        evaluated_at=now,
        input_proposal={"SPY": 0.6, "IEF": 0.4},
        input_state={"current": {"SPY": 0.5, "IEF": 0.5}},
        input_config_hash="abc123",
        result=RiskGatewayResult.APPROVED,
        reasons=[],
        blocking_checks=[],
    )
    # Decision must exist before we FK-reference it from evaluation OR vice versa.
    # We persist the decision first, then the evaluation, then update the decision.
    d = DecisionRecord(
        decision_id=decision_id,
        proposed_at=now,
        as_of=now,
        proposed_portfolio={"SPY": 0.6, "IEF": 0.4},
        rationale="baseline 60/40",
        risk_gateway_result=RiskGatewayResult.APPROVED,
    )
    store.write_decision(d)
    store.write_evaluation(ev)
    # Wire the eval id onto the decision
    d.risk_gateway_eval_id = eval_id
    store.write_decision(d)  # upsert

    loaded = store.read_decision(decision_id)
    assert loaded is not None
    assert loaded.proposed_portfolio == {"SPY": 0.6, "IEF": 0.4}
    assert loaded.risk_gateway_eval_id == eval_id
    assert loaded.executed is False

    loaded_eval = store.read_evaluation(eval_id)
    assert loaded_eval is not None
    assert loaded_eval.result == RiskGatewayResult.APPROVED


def test_decision_exists_for_day_idempotency_check():
    day = _utcnow()
    decision_id = uuid4()
    store.write_decision(DecisionRecord(
        decision_id=decision_id,
        proposed_at=day,
        as_of=day,
        proposed_portfolio={"SPY": 1.0},
        rationale="test",
        risk_gateway_result=RiskGatewayResult.APPROVED,
    ))
    assert store.decision_exists_for_day(day) is True
