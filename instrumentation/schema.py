"""Frozen schema for agent traces and portfolio decisions.

Adding fields is allowed; renaming or repurposing is not. Anything that breaks
backward compatibility with persisted traces or decisions must be a new field
on the same shape, not a mutation of an existing one. See PROJECT_CHARTER.md §5
("Instrumentation precedes the thing being instrumented") and the Phase 1 prompt.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Phase(str, Enum):
    PERCEIVE = "perceive"
    HYPOTHESIZE = "hypothesize"
    INVESTIGATE = "investigate"
    SYNTHESIZE = "synthesize"
    LEARN = "learn"
    OTHER = "other"


class RiskGatewayResult(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUIRES_APPROVAL = "requires_approval"


class HumanDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    OVERRIDDEN = "overridden"


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    """One tool invocation inside a trace."""

    model_config = ConfigDict(extra="forbid")

    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class TraceRecord(BaseModel):
    """One agent invocation.

    In Phase 1 the monolithic agent emits ONE trace per daily run that covers
    all five phases. In Phase 3+ a planner/supervisor/specialist tree can emit
    one trace per call with parent_trace_id wiring them together.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: UUID = Field(default_factory=uuid4)
    parent_trace_id: UUID | None = None
    phase: Phase = Phase.OTHER
    agent_role: str  # 'monolithic' in phase 1
    model: str
    model_version_pinned_at: str

    started_at: datetime
    completed_at: datetime | None = None

    as_of: datetime  # bitemporal cutoff — what the agent knew the world to be

    input_summary: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)
    reasoning_text: str | None = None

    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float | None = None

    parent_decision_id: UUID | None = None


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


class PortfolioWeights(BaseModel):
    """{symbol: weight}; weights sum should equal 1.0 for a fully-invested book."""

    model_config = ConfigDict(extra="allow")  # symbol set is data, not schema

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> "PortfolioWeights":
        return cls(**d)

    def to_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.model_dump().items()}


class DecisionRecord(BaseModel):
    """One portfolio proposal."""

    model_config = ConfigDict(extra="forbid")

    decision_id: UUID = Field(default_factory=uuid4)
    proposed_at: datetime
    as_of: datetime

    proposed_portfolio: dict[str, float]  # {symbol: weight}
    rationale: str

    agent_trace_id: UUID | None = None

    risk_gateway_result: RiskGatewayResult
    risk_gateway_eval_id: UUID | None = None

    human_decision: HumanDecision | None = None
    human_decision_reasoning: str | None = None

    executed: bool = False  # always False in Phase 1


# ---------------------------------------------------------------------------
# Risk gateway evaluation record
# ---------------------------------------------------------------------------


class RiskGatewayEvaluation(BaseModel):
    """The persisted record of one gateway invocation."""

    model_config = ConfigDict(extra="forbid")

    eval_id: UUID = Field(default_factory=uuid4)
    decision_id: UUID | None = None
    evaluated_at: datetime

    input_proposal: dict[str, Any]
    input_state: dict[str, Any]
    input_config_hash: str

    result: RiskGatewayResult
    reasons: list[str] = Field(default_factory=list)
    blocking_checks: list[str] = Field(default_factory=list)
