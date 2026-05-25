"""Run context — module-level state set by the daily-run orchestrator and
read by the tool implementations. Lets every tool implicitly respect the
trace's `as_of` cutoff without having to thread it through the SDK.

Scope: one daily run uses ONE RunContext. Tools that touch persistent state
(e.g. snapshotting the DAG, recording journal reads) take the RunContext as
their source of truth for trace_id and as_of.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from instrumentation.schema import ToolCall


@dataclass
class RunContext:
    trace_id: UUID
    as_of: datetime
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Snapshots created during the run, keyed by name. Useful for the daily
    # orchestrator to know which DAG snapshot the agent saw.
    dag_snapshot_id: UUID | None = None
    journal_read: list[dict[str, Any]] | None = None


_CURRENT: RunContext | None = None


def set_current(ctx: RunContext) -> None:
    global _CURRENT
    _CURRENT = ctx


def get_current() -> RunContext:
    if _CURRENT is None:
        raise RuntimeError(
            "No RunContext set. Tools must be invoked inside a daily-run scope."
        )
    return _CURRENT


def clear() -> None:
    global _CURRENT
    _CURRENT = None
