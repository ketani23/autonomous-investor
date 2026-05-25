"""Data shapes for the risk gateway. Pure dataclasses, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class PortfolioProposal:
    """What the agent (or any source) wants the portfolio to look like."""

    target_weights: dict[str, float]
    # Aggregate tokens the agent spent producing this proposal.
    # The gateway uses this for the token-budget invariant.
    agent_token_usage: int = 0


@dataclass(frozen=True)
class PortfolioState:
    """Where the portfolio is RIGHT NOW. The caller is responsible for
    reconstructing this from prior approved decisions (Phase 1) or from the
    broker (Phase 2+).
    """

    current_weights: dict[str, float] = field(default_factory=dict)
    orders_today_count: int = 0
    orders_this_week_count: int = 0
    # Day-over-day portfolio PnL since the last settled position (signed
    # fraction, e.g. -0.07 = -7% loss). None if unknown.
    last_settled_pnl_pct: float | None = None


class DecisionType(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUIRES_APPROVAL = "requires_approval"


@dataclass(frozen=True)
class Decision:
    result: DecisionType
    reasons: list[str] = field(default_factory=list)
    blocking_checks: list[str] = field(default_factory=list)


def order_count(current: dict[str, float], target: dict[str, float], epsilon: float = 1e-4) -> int:
    """Number of symbols whose weight changes by more than epsilon — i.e. orders generated."""
    symbols = set(current) | set(target)
    return sum(1 for s in symbols if abs(target.get(s, 0.0) - current.get(s, 0.0)) > epsilon)
