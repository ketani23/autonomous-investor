"""Run the monolithic agent through one daily decision loop.

The orchestrator:
  1. Builds a fresh RunContext (trace_id, as_of) and sets it on the module.
  2. Spins up an in-process MCP server with the tool catalog.
  3. Invokes `query()` with the system prompt + a kickoff user message.
  4. Iterates the message stream, capturing tokens/cost/reasoning.
  5. Returns the populated TraceRecord and the parsed proposal (if any).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
)

from agents.monolithic import context as runctx
from agents.monolithic.system_prompt import SYSTEM_PROMPT
from agents.monolithic.tools.catalog import ALL_TOOLS
from instrumentation.schema import Phase, TokenUsage, TraceRecord
from substrate.config import load_model_config


@dataclass
class AgentRun:
    trace: TraceRecord
    proposal: dict[str, Any] | None   # {weights, rationale, submitted_at} or None


KICKOFF_USER_MSG = """\
Run the daily decision loop. Begin with PERCEIVE: read the journal, the
universe, the macro series catalog, the current portfolio, and the DAG.
Then proceed through HYPOTHESIZE, INVESTIGATE, SYNTHESIZE, LEARN, and
finally call propose_portfolio.
"""


def _tool_full_name(short_name: str, server_name: str = "monolithic") -> str:
    # MCP tools registered via SDK servers are exposed to the agent as
    # mcp__<server_name>__<short_name>.
    return f"mcp__{server_name}__{short_name}"


def _build_options(model: str, model_pin: str) -> ClaudeAgentOptions:
    server = create_sdk_mcp_server(name="monolithic", version="0.1.0", tools=ALL_TOOLS)
    tool_names = [_tool_full_name(t.name) for t in ALL_TOOLS]
    return ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"monolithic": server},
        allowed_tools=tool_names,
        permission_mode="bypassPermissions",
        max_turns=40,
    )


async def _run_async(as_of: datetime) -> AgentRun:
    cfg = load_model_config("monolithic")
    trace_id = uuid4()
    ctx = runctx.RunContext(trace_id=trace_id, as_of=as_of)
    runctx.set_current(ctx)

    started_at = datetime.now(tz=timezone.utc)
    options = _build_options(cfg.model, cfg.model_version_pinned_at)

    reasoning_chunks: list[str] = []
    token_usage = TokenUsage()
    cost_usd: float | None = None

    try:
        async for msg in query(prompt=KICKOFF_USER_MSG, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        reasoning_chunks.append(block.text)
                    elif isinstance(block, ThinkingBlock):
                        reasoning_chunks.append(f"[thinking]\n{block.thinking}")
            elif isinstance(msg, ResultMessage):
                usage = getattr(msg, "usage", None) or {}
                # ResultMessage.usage is dict-like with input_tokens, output_tokens, etc.
                token_usage = TokenUsage(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                )
                cost_usd = getattr(msg, "total_cost_usd", None)
            elif isinstance(msg, (SystemMessage, UserMessage)):
                pass  # not useful for the trace
    finally:
        completed_at = datetime.now(tz=timezone.utc)
        runctx.clear()

    proposal: dict[str, Any] | None = getattr(ctx, "_proposal", None)

    trace = TraceRecord(
        trace_id=trace_id,
        phase=Phase.OTHER,  # monolithic = one trace covers all phases
        agent_role="monolithic",
        model=cfg.model,
        model_version_pinned_at=cfg.model_version_pinned_at,
        started_at=started_at,
        completed_at=completed_at,
        as_of=as_of,
        input_summary={"kickoff": KICKOFF_USER_MSG.strip()[:500]},
        tool_calls=ctx.tool_calls,
        output={"proposal": proposal, "dag_snapshot_id": str(ctx.dag_snapshot_id) if ctx.dag_snapshot_id else None},
        reasoning_text="\n\n".join(reasoning_chunks) if reasoning_chunks else None,
        token_usage=token_usage,
        cost_usd=cost_usd,
    )
    return AgentRun(trace=trace, proposal=proposal)


def run(as_of: datetime | None = None) -> AgentRun:
    as_of = as_of or datetime.now(tz=timezone.utc)
    return asyncio.run(_run_async(as_of))
