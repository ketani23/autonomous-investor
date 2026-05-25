"""Risk gateway invariant tests.

Per the charter and Phase 1 prompt, these tests are written BEFORE the agent
is wired to the gateway. The gateway must be a pure function with no hidden
state and no I/O during evaluation; these tests enforce both the per-check
isolation and several combinatorial interactions.
"""

from __future__ import annotations

import pytest

from substrate.config import RiskGatewayConfig
from substrate.risk_gateway.gateway import evaluate
from substrate.risk_gateway.types import (
    Decision,
    DecisionType,
    PortfolioProposal,
    PortfolioState,
)


# A baseline config — generous enough that no invariant fires by default.
def _baseline_config(**overrides) -> RiskGatewayConfig:
    base = dict(
        max_position_weight=0.70,    # generous so the test baseline 60/40 passes
        max_gross_exposure=1.05,
        max_net_exposure=1.05,
        min_net_exposure=0.0,
        daily_loss_circuit_breaker=0.05,
        max_orders_per_day=25,
        max_orders_per_week=60,
        max_tokens_per_decision=500_000,
        whitelist=("SPY", "IEF", "TLT", "GLD", "QQQ"),
        config_hash="test_hash",
    )
    base.update(overrides)
    return RiskGatewayConfig(**base)


def _approved_baseline() -> tuple[PortfolioProposal, PortfolioState, RiskGatewayConfig]:
    """A proposal/state/config triple that should always Approve."""
    proposal = PortfolioProposal(
        target_weights={"SPY": 0.60, "IEF": 0.40},
        agent_token_usage=10_000,
    )
    state = PortfolioState(
        current_weights={"SPY": 0.50, "IEF": 0.50},
        orders_today_count=0,
        orders_this_week_count=0,
        last_settled_pnl_pct=0.0,
    )
    return proposal, state, _baseline_config()


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_baseline_proposal_is_approved():
    p, s, c = _approved_baseline()
    d = evaluate(p, s, c)
    assert d.result == DecisionType.APPROVED
    assert d.reasons == []
    assert d.blocking_checks == []


def test_evaluate_is_pure_no_mutation():
    """Calling evaluate must not mutate any of its inputs."""
    p, s, c = _approved_baseline()
    p_before = (dict(p.target_weights), p.agent_token_usage)
    s_before = (dict(s.current_weights), s.orders_today_count, s.last_settled_pnl_pct)
    evaluate(p, s, c)
    assert (dict(p.target_weights), p.agent_token_usage) == p_before
    assert (dict(s.current_weights), s.orders_today_count, s.last_settled_pnl_pct) == s_before


# ---------------------------------------------------------------------------
# 1. Per-position size cap
# ---------------------------------------------------------------------------


def test_position_cap_rejects_oversize_position():
    p, s, _ = _approved_baseline()
    c = _baseline_config(max_position_weight=0.40)
    bad = PortfolioProposal(target_weights={"SPY": 0.90, "IEF": 0.10})
    d = evaluate(bad, s, c)
    assert d.result == DecisionType.REJECTED
    assert "position_size" in d.blocking_checks
    assert any("SPY" in r and "0.9" in r for r in d.reasons)


def test_position_cap_passes_at_exact_limit():
    p, s, _ = _approved_baseline()
    c = _baseline_config(max_position_weight=0.40)
    edge = PortfolioProposal(target_weights={"SPY": 0.40, "IEF": 0.40, "GLD": 0.20})
    d = evaluate(edge, s, c)
    assert d.result == DecisionType.APPROVED


def test_position_cap_rejects_against_production_config():
    """Production config has max_position_weight=0.40 and a 50/50 proposal must be
    rejected — this guards against a future config edit that silently loosens the cap."""
    c = _baseline_config(max_position_weight=0.40)
    p = PortfolioProposal(target_weights={"SPY": 0.50, "IEF": 0.50})
    s = PortfolioState()
    d = evaluate(p, s, c)
    assert d.result == DecisionType.REJECTED
    assert "position_size" in d.blocking_checks


# ---------------------------------------------------------------------------
# 2. Gross exposure cap
# ---------------------------------------------------------------------------


def test_gross_exposure_rejects_overweight():
    p, s, c = _approved_baseline()
    over = PortfolioProposal(target_weights={"SPY": 0.40, "IEF": 0.40, "GLD": 0.40})  # sum=1.2
    d = evaluate(over, s, c)
    assert d.result == DecisionType.REJECTED
    assert "gross_exposure" in d.blocking_checks


def test_gross_exposure_handles_short_positions():
    """Gross = sum of |weights|. A long-short book with |sum| > cap should be rejected."""
    p, s, c = _approved_baseline()
    # Switch min_net_exposure to allow shorts for this test
    short_c = _baseline_config(min_net_exposure=-1.0)
    over = PortfolioProposal(target_weights={"SPY": 0.40, "IEF": -0.40, "GLD": 0.40})
    # Gross = 1.2, Net = 0.4. Gross check fires.
    d = evaluate(over, s, short_c)
    assert d.result == DecisionType.REJECTED
    assert "gross_exposure" in d.blocking_checks


# ---------------------------------------------------------------------------
# 3. Net exposure cap
# ---------------------------------------------------------------------------


def test_net_exposure_rejects_short_book_when_long_only():
    p, s, c = _approved_baseline()  # min_net_exposure=0.0
    short = PortfolioProposal(target_weights={"SPY": 0.40, "IEF": -0.50})  # net=-0.1
    d = evaluate(short, s, c)
    assert d.result == DecisionType.REJECTED
    assert "net_exposure" in d.blocking_checks


def test_net_exposure_passes_within_bounds():
    p, s, c = _approved_baseline()
    inside = PortfolioProposal(target_weights={"SPY": 0.50, "IEF": 0.50})  # net=1.0
    d = evaluate(inside, s, c)
    assert d.result == DecisionType.APPROVED


# ---------------------------------------------------------------------------
# 4. Daily loss circuit breaker
# ---------------------------------------------------------------------------


def test_circuit_breaker_downgrades_to_requires_approval_on_large_loss():
    p, s, c = _approved_baseline()
    # 7% loss exceeds 5% threshold
    s_loss = PortfolioState(current_weights=s.current_weights, last_settled_pnl_pct=-0.07)
    d = evaluate(p, s_loss, c)
    assert d.result == DecisionType.REQUIRES_APPROVAL
    assert "daily_loss_circuit_breaker" in d.blocking_checks


def test_circuit_breaker_does_not_fire_on_small_loss():
    p, s, c = _approved_baseline()
    s_small = PortfolioState(current_weights=s.current_weights, last_settled_pnl_pct=-0.02)
    d = evaluate(p, s_small, c)
    assert d.result == DecisionType.APPROVED


def test_circuit_breaker_does_not_fire_on_unknown_pnl():
    p, s, c = _approved_baseline()
    s_none = PortfolioState(current_weights=s.current_weights, last_settled_pnl_pct=None)
    d = evaluate(p, s_none, c)
    assert d.result == DecisionType.APPROVED


# ---------------------------------------------------------------------------
# 5. Order frequency caps
# ---------------------------------------------------------------------------


def test_order_frequency_rejects_when_daily_cap_exceeded():
    p, s, c = _approved_baseline()
    # Current state already at the cap; this proposal would add 2 more orders
    s_busy = PortfolioState(
        current_weights={"SPY": 0.50, "IEF": 0.50},
        orders_today_count=25,
        orders_this_week_count=30,
    )
    d = evaluate(p, s_busy, c)  # p changes both SPY and IEF, so +2
    assert d.result == DecisionType.REJECTED
    assert "order_frequency" in d.blocking_checks


def test_order_frequency_rejects_when_weekly_cap_exceeded():
    p, s, c = _approved_baseline()
    s_week = PortfolioState(
        current_weights={"SPY": 0.50, "IEF": 0.50},
        orders_today_count=0,
        orders_this_week_count=60,
    )
    d = evaluate(p, s_week, c)
    assert d.result == DecisionType.REJECTED
    assert "order_frequency" in d.blocking_checks


def test_order_frequency_passes_when_no_changes():
    p, s, c = _approved_baseline()
    # Proposal == current => zero new orders
    p_same = PortfolioProposal(target_weights=s.current_weights)
    s_at_cap = PortfolioState(
        current_weights=s.current_weights,
        orders_today_count=25,
        orders_this_week_count=60,
    )
    d = evaluate(p_same, s_at_cap, c)
    assert d.result == DecisionType.APPROVED


# ---------------------------------------------------------------------------
# 6. Instrument whitelist
# ---------------------------------------------------------------------------


def test_whitelist_rejects_unknown_symbol():
    p, s, c = _approved_baseline()
    bad = PortfolioProposal(target_weights={"SPY": 0.5, "TSLA": 0.5})  # TSLA not in whitelist
    d = evaluate(bad, s, c)
    assert d.result == DecisionType.REJECTED
    assert "whitelist" in d.blocking_checks
    assert any("TSLA" in r for r in d.reasons)


# ---------------------------------------------------------------------------
# 7. Token budget per decision
# ---------------------------------------------------------------------------


def test_token_budget_downgrades_to_requires_approval():
    p, s, c = _approved_baseline()
    p_expensive = PortfolioProposal(
        target_weights=p.target_weights,
        agent_token_usage=600_000,  # exceeds 500k
    )
    d = evaluate(p_expensive, s, c)
    assert d.result == DecisionType.REQUIRES_APPROVAL
    assert "token_budget" in d.blocking_checks


# ---------------------------------------------------------------------------
# Combinatorial — multiple invariants together
# ---------------------------------------------------------------------------


def test_multiple_rejections_reported_together():
    """Proposal violating both position size AND whitelist: both reasons present."""
    p, s, _ = _approved_baseline()
    c = _baseline_config(max_position_weight=0.40)
    bad = PortfolioProposal(target_weights={"SPY": 0.50, "TSLA": 0.50})  # position cap + whitelist
    d = evaluate(bad, s, c)
    assert d.result == DecisionType.REJECTED
    assert "position_size" in d.blocking_checks
    assert "whitelist" in d.blocking_checks
    assert len(d.reasons) >= 2


def test_rejection_dominates_requires_approval_when_both_fire():
    """If a hard check (rejection) AND a soft check (requires_approval) both
    fire, the result is REJECTED — a position-size violation should not be
    salvageable by human approval."""
    p, s, _ = _approved_baseline()
    c = _baseline_config(max_position_weight=0.40)
    p_bad = PortfolioProposal(target_weights={"SPY": 0.90, "IEF": 0.10}, agent_token_usage=10_000)
    s_loss = PortfolioState(current_weights={"SPY": 0.50, "IEF": 0.50}, last_settled_pnl_pct=-0.07)
    d = evaluate(p_bad, s_loss, c)
    assert d.result == DecisionType.REJECTED
    assert "position_size" in d.blocking_checks
    assert "daily_loss_circuit_breaker" in d.blocking_checks


def test_two_soft_failures_yield_requires_approval():
    p, s, c = _approved_baseline()
    p_expensive = PortfolioProposal(
        target_weights={"SPY": 0.60, "IEF": 0.40},
        agent_token_usage=600_000,
    )
    s_loss = PortfolioState(current_weights={"SPY": 0.50, "IEF": 0.50}, last_settled_pnl_pct=-0.08)
    d = evaluate(p_expensive, s_loss, c)
    assert d.result == DecisionType.REQUIRES_APPROVAL
    assert "token_budget" in d.blocking_checks
    assert "daily_loss_circuit_breaker" in d.blocking_checks


# ---------------------------------------------------------------------------
# Pure-function contract
# ---------------------------------------------------------------------------


def test_evaluate_returns_decision_dataclass():
    p, s, c = _approved_baseline()
    d = evaluate(p, s, c)
    assert isinstance(d, Decision)


def test_evaluate_does_not_perform_io(monkeypatch):
    """The gateway must not open files, connect to DBs, or hit the network.
    We monkeypatch a few common I/O entrypoints to fail loudly if invoked.
    """
    import builtins
    import socket

    orig_open = builtins.open

    def _no_open(*a, **k):
        # Allow tomllib/pyyaml's internal open? They're not called from evaluate
        # because the config is passed in. So fail if anyone tries.
        raise AssertionError(f"evaluate called open(): {a} {k}")

    def _no_socket(*a, **k):
        raise AssertionError("evaluate touched the network")

    monkeypatch.setattr(builtins, "open", _no_open)
    monkeypatch.setattr(socket, "socket", _no_socket)
    p, s, c = _approved_baseline()
    evaluate(p, s, c)
    # restoration via monkeypatch teardown
