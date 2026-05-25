"""Daily run script.

Steps:
  1. Idempotency check — skip if today already has a decision.
  2. Snapshot as_of = now() UTC.
  3. Run the monolithic agent, capture the trace.
  4. Persist the trace.
  5. If the agent produced a proposal: build PortfolioProposal, load current
     state, call risk gateway, persist evaluation + decision.
     If no proposal: persist a "no proposal" decision marker so the day's
     idempotency check works correctly.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv

from agents.monolithic.agent import run as run_agent
from instrumentation import store
from instrumentation.schema import (
    DecisionRecord,
    RiskGatewayEvaluation,
    RiskGatewayResult,
)
from substrate.config import load_risk_gateway_config
from substrate.risk_gateway.gateway import evaluate
from substrate.risk_gateway.types import (
    PortfolioProposal,
    PortfolioState,
)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Run agent but don't persist anything.")
    parser.add_argument("--force", action="store_true", help="Run even if today already has a decision.")
    args = parser.parse_args()

    load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set in .env", file=sys.stderr)
        return 1
    # The Claude Agent SDK uses the local `claude` CLI's auth (subscription) if
    # ANTHROPIC_API_KEY is unset. If neither is present, the SDK will error
    # with a clear message — let it surface naturally.

    now = _utcnow()

    if not args.force and store.decision_exists_for_day(now):
        print(f"Decision already exists for {now.date()} — skipping (use --force to override).")
        return 0

    print(f"[{now.isoformat()}] Starting daily run, as_of={now.isoformat()}")
    agent_run = run_agent(as_of=now)
    trace = agent_run.trace
    proposal = agent_run.proposal

    print(f"  agent finished: {len(trace.tool_calls)} tool calls, "
          f"{trace.token_usage.total} tokens, "
          f"${trace.cost_usd or 0:.4f}")

    if args.dry_run:
        print("Dry run — skipping persistence.")
        if proposal:
            print(f"Proposed weights: {proposal.get('weights')}")
            print(f"Rationale: {proposal.get('rationale', '')[:300]}...")
        return 0

    # Persist trace
    store.write_trace(trace)

    # If no proposal, record a marker decision so the day is "done"
    if not proposal or not proposal.get("weights"):
        d = DecisionRecord(
            proposed_at=now,
            as_of=now,
            proposed_portfolio={},
            rationale="(no proposal produced this run)",
            agent_trace_id=trace.trace_id,
            risk_gateway_result=RiskGatewayResult.REJECTED,
        )
        store.write_decision(d)
        # also write a rejection evaluation marker for queryability
        ev = RiskGatewayEvaluation(
            decision_id=d.decision_id,
            evaluated_at=now,
            input_proposal={},
            input_state={},
            input_config_hash="no-eval",
            result=RiskGatewayResult.REJECTED,
            reasons=["agent produced no proposal"],
            blocking_checks=["no_proposal"],
        )
        store.write_evaluation(ev)
        d.risk_gateway_eval_id = ev.eval_id
        store.write_decision(d)
        print(f"  no proposal — wrote rejection marker decision {d.decision_id}")
        return 0

    # Risk gateway evaluation
    gateway_config = load_risk_gateway_config()
    weights = {k: float(v) for k, v in proposal["weights"].items()}
    portfolio_proposal = PortfolioProposal(
        target_weights=weights,
        agent_token_usage=trace.token_usage.total,
    )

    prior = store.latest_approved_decision()
    current_state = PortfolioState(
        current_weights=prior.proposed_portfolio if prior else {},
        orders_today_count=0,
        orders_this_week_count=0,
        last_settled_pnl_pct=None,    # Phase 2 wires real PnL reconciliation
    )

    decision = evaluate(portfolio_proposal, current_state, gateway_config)

    # Build and persist decision + evaluation
    decision_id = uuid4()
    eval_id = uuid4()
    ev = RiskGatewayEvaluation(
        eval_id=eval_id,
        decision_id=decision_id,
        evaluated_at=now,
        input_proposal={"weights": weights, "agent_token_usage": trace.token_usage.total},
        input_state={"current_weights": current_state.current_weights, "orders_today": current_state.orders_today_count},
        input_config_hash=gateway_config.config_hash,
        result=RiskGatewayResult(decision.result.value),
        reasons=list(decision.reasons),
        blocking_checks=list(decision.blocking_checks),
    )
    # Two-step persist to satisfy the FK between decisions and evaluations:
    # write the decision first (no eval_id), then the evaluation (FK depends
    # on decision_id), then upsert the decision with eval_id set.
    d = DecisionRecord(
        decision_id=decision_id,
        proposed_at=now,
        as_of=now,
        proposed_portfolio=weights,
        rationale=proposal.get("rationale", ""),
        agent_trace_id=trace.trace_id,
        risk_gateway_result=RiskGatewayResult(decision.result.value),
    )
    store.write_decision(d)
    store.write_evaluation(ev)
    d.risk_gateway_eval_id = eval_id
    store.write_decision(d)

    # Re-link trace to decision
    trace.parent_decision_id = decision_id
    store.write_trace(trace)

    print(f"  decision {decision_id} -> {decision.result.value}")
    if decision.reasons:
        for r in decision.reasons:
            print(f"     reason: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
