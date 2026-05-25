"""Deterministic risk gateway.

Every proposed order — from backtest, from agent, from manual override —
passes through `evaluate`. The function is pure: no I/O, no hidden state,
no mutation of inputs. Persistence of the result is the caller's job.

Hard checks (REJECT on violation):
    1. position_size  — per-symbol cap
    2. gross_exposure — sum of |weights| cap
    3. net_exposure   — sum of signed weights, within [min, max] band
    4. whitelist      — every symbol must be in the configured universe
    5. order_frequency — daily and weekly order count caps

Soft checks (downgrade to REQUIRES_APPROVAL on violation):
    6. daily_loss_circuit_breaker — pause if portfolio dropped > threshold
    7. token_budget — agent cost-control

If any hard check fires, the result is REJECTED regardless of how many soft
checks also fire. If only soft checks fire, the result is REQUIRES_APPROVAL.
Otherwise APPROVED.
"""

from __future__ import annotations

from substrate.config import RiskGatewayConfig

from .types import (
    Decision,
    DecisionType,
    PortfolioProposal,
    PortfolioState,
    order_count,
)


def evaluate(
    proposal: PortfolioProposal,
    current_state: PortfolioState,
    config: RiskGatewayConfig,
) -> Decision:
    hard_reasons: list[str] = []
    soft_reasons: list[str] = []
    hard_checks: list[str] = []
    soft_checks: list[str] = []

    # ---- 1. Per-position size cap ---------------------------------------
    cap = config.max_position_weight
    over = [(s, w) for s, w in proposal.target_weights.items() if abs(w) > cap + 1e-9]
    if over:
        hard_checks.append("position_size")
        for s, w in over:
            hard_reasons.append(f"position_size: {s} weight {w} exceeds cap {cap}")

    # ---- 2. Gross exposure ----------------------------------------------
    gross = sum(abs(w) for w in proposal.target_weights.values())
    if gross > config.max_gross_exposure + 1e-9:
        hard_checks.append("gross_exposure")
        hard_reasons.append(
            f"gross_exposure: {gross:.4f} exceeds cap {config.max_gross_exposure}"
        )

    # ---- 3. Net exposure ------------------------------------------------
    net = sum(proposal.target_weights.values())
    if net > config.max_net_exposure + 1e-9:
        hard_checks.append("net_exposure")
        hard_reasons.append(
            f"net_exposure: {net:.4f} exceeds cap {config.max_net_exposure}"
        )
    if net < config.min_net_exposure - 1e-9:
        if "net_exposure" not in hard_checks:
            hard_checks.append("net_exposure")
        hard_reasons.append(
            f"net_exposure: {net:.4f} below floor {config.min_net_exposure}"
        )

    # ---- 4. Whitelist ---------------------------------------------------
    whitelist = set(config.whitelist)
    bad_symbols = [s for s in proposal.target_weights if s not in whitelist]
    if bad_symbols:
        hard_checks.append("whitelist")
        for s in bad_symbols:
            hard_reasons.append(f"whitelist: {s} not in configured universe")

    # ---- 5. Order frequency ---------------------------------------------
    new_orders = order_count(current_state.current_weights, proposal.target_weights)
    if new_orders + current_state.orders_today_count > config.max_orders_per_day:
        hard_checks.append("order_frequency")
        hard_reasons.append(
            f"order_frequency: {current_state.orders_today_count} prior + "
            f"{new_orders} new exceeds daily cap {config.max_orders_per_day}"
        )
    if new_orders + current_state.orders_this_week_count > config.max_orders_per_week:
        if "order_frequency" not in hard_checks:
            hard_checks.append("order_frequency")
        hard_reasons.append(
            f"order_frequency: {current_state.orders_this_week_count} prior this week + "
            f"{new_orders} new exceeds weekly cap {config.max_orders_per_week}"
        )

    # ---- 6. Daily loss circuit breaker (SOFT) ---------------------------
    pnl = current_state.last_settled_pnl_pct
    if pnl is not None and pnl < -abs(config.daily_loss_circuit_breaker):
        soft_checks.append("daily_loss_circuit_breaker")
        soft_reasons.append(
            f"daily_loss_circuit_breaker: prior pnl {pnl:.4f} exceeds "
            f"threshold -{config.daily_loss_circuit_breaker}"
        )

    # ---- 7. Token budget (SOFT) -----------------------------------------
    if proposal.agent_token_usage > config.max_tokens_per_decision:
        soft_checks.append("token_budget")
        soft_reasons.append(
            f"token_budget: {proposal.agent_token_usage} tokens exceeds "
            f"cap {config.max_tokens_per_decision}"
        )

    # ---- Resolve final result -------------------------------------------
    all_reasons = hard_reasons + soft_reasons
    all_checks = hard_checks + soft_checks

    if hard_checks:
        return Decision(
            result=DecisionType.REJECTED,
            reasons=all_reasons,
            blocking_checks=all_checks,
        )
    if soft_checks:
        return Decision(
            result=DecisionType.REQUIRES_APPROVAL,
            reasons=all_reasons,
            blocking_checks=all_checks,
        )
    return Decision(result=DecisionType.APPROVED, reasons=[], blocking_checks=[])
