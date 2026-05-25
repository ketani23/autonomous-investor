"""Tool catalog for the monolithic agent.

Each tool wraps a substrate function and bridges Claude Agent SDK's MCP
content-block protocol. Tools implicitly use the active RunContext's `as_of`
so the agent cannot leak data forward in time.

Tool list (and which loop phase typically uses each):
    list_universe         — Perceive
    get_prices            — Perceive, Investigate
    list_macro_series     — Perceive
    get_macro_series      — Perceive, Investigate
    read_causal_dag       — Perceive, Investigate
    read_decision_journal — Perceive
    read_current_portfolio — Synthesize
    run_backtest          — Investigate
    propose_portfolio     — Synthesize (terminal)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from claude_agent_sdk import tool

from agents.monolithic import context as runctx
from instrumentation.dag import upsert_dag_snapshot
from instrumentation.journal import read_recent_decisions
from instrumentation.schema import ToolCall
from substrate.backtest.harness import run_monthly_rebalance
from substrate.bitemporal import query as bt
from substrate.config import (
    load_causal_dag,
    load_macro_series,
    load_universe,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _text_content(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _record(name: str, input_: dict[str, Any], output: Any, started: float, error: str | None = None) -> None:
    """Append a ToolCall to the active RunContext."""
    ctx = runctx.get_current()
    ctx.tool_calls.append(
        ToolCall(
            name=name,
            input=input_,
            output=output if not isinstance(output, str) else {"text": output[:2000]},
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=error,
        )
    )


def _parse_date(s: str | None, default: datetime) -> datetime:
    if not s:
        return default
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if "T" not in s else datetime.fromisoformat(s).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# list_universe
# ---------------------------------------------------------------------------


@tool(
    "list_universe",
    "List the ETFs the agent may consider for portfolio construction. Returns ticker, sleeve, description.",
    {},
)
async def list_universe(args: dict) -> dict:
    started = time.perf_counter()
    syms = load_universe()
    payload = [{"ticker": s.ticker, "sleeve": s.sleeve, "description": s.description} for s in syms]
    text = json.dumps(payload, indent=2)
    _record("list_universe", {}, payload, started)
    return _text_content(text)


# ---------------------------------------------------------------------------
# get_prices
# ---------------------------------------------------------------------------


@tool(
    "get_prices",
    "Fetch daily adjusted close prices for a single symbol over a date range. "
    "Dates are ISO format (YYYY-MM-DD). The query respects the bitemporal as_of cutoff "
    "of the current run, so the agent cannot see prices published after its decision time.",
    {"symbol": str, "start": str, "end": str},
)
async def get_prices(args: dict) -> dict:
    started = time.perf_counter()
    ctx = runctx.get_current()
    symbol = args["symbol"]
    start = _parse_date(args.get("start"), ctx.as_of - timedelta(days=365))
    end = _parse_date(args.get("end"), ctx.as_of)
    try:
        rows = bt.point_in_time(
            bt.PRICES, symbol,
            world_from=start, world_to=end, known_as_of=ctx.as_of,
        )
    except Exception as e:
        _record("get_prices", args, None, started, error=str(e))
        return _text_content(f"Error: {e}")
    out = [{"date": r["valid_from"].date().isoformat(), "adj_close": r["adj_close"]} for r in rows]
    summary = {"symbol": symbol, "n_rows": len(out), "first": out[0] if out else None, "last": out[-1] if out else None}
    _record("get_prices", args, summary, started)
    text = json.dumps({"summary": summary, "data": out[-30:]}, indent=2)  # last 30 days in detail
    return _text_content(text)


# ---------------------------------------------------------------------------
# list_macro_series
# ---------------------------------------------------------------------------


@tool(
    "list_macro_series",
    "List the FRED macro series the agent can pull. Returns id, sleeve, description, and whether vintage-tracked.",
    {},
)
async def list_macro_series(args: dict) -> dict:
    started = time.perf_counter()
    series = load_macro_series()
    payload = [
        {
            "id": s.id,
            "sleeve": s.sleeve,
            "vintage_tracked": s.vintage_tracked,
            "description": s.description,
        }
        for s in series
    ]
    _record("list_macro_series", {}, payload, started)
    return _text_content(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# get_macro_series
# ---------------------------------------------------------------------------


@tool(
    "get_macro_series",
    "Fetch a FRED/ALFRED macro series over a date range, with bitemporal as_of "
    "respect. For vintage-tracked series, the value returned is the one that was "
    "PUBLISHED on or before the current run's as_of (not the latest revision).",
    {"series_id": str, "start": str, "end": str},
)
async def get_macro_series(args: dict) -> dict:
    started = time.perf_counter()
    ctx = runctx.get_current()
    sid = args["series_id"]
    start = _parse_date(args.get("start"), ctx.as_of - timedelta(days=730))
    end = _parse_date(args.get("end"), ctx.as_of)
    try:
        rows = bt.point_in_time(
            bt.MACRO, sid,
            world_from=start, world_to=end, known_as_of=ctx.as_of,
        )
    except Exception as e:
        _record("get_macro_series", args, None, started, error=str(e))
        return _text_content(f"Error: {e}")
    if not rows:
        msg = f"No data for {sid} in window {start.date()}..{end.date()} with as_of={ctx.as_of.isoformat()}. The macro store may not yet be backfilled (run scripts/backfill_macro.py)."
        _record("get_macro_series", args, {"empty": True}, started)
        return _text_content(msg)
    out = [{"date": r["valid_from"].date().isoformat(), "value": r["value"]} for r in rows]
    summary = {"series_id": sid, "n_rows": len(out), "first": out[0], "last": out[-1]}
    _record("get_macro_series", args, summary, started)
    return _text_content(json.dumps({"summary": summary, "data": out[-24:]}, indent=2))


# ---------------------------------------------------------------------------
# read_causal_dag
# ---------------------------------------------------------------------------


@tool(
    "read_causal_dag",
    "Read the project's causal DAG of macro variables. The DAG content the agent "
    "sees is snapshotted into the trace for deterministic replay. Use this when "
    "forming hypotheses — the agent's system prompt expects at least one DAG edge "
    "to be referenced when reasoning.",
    {},
)
async def read_causal_dag(args: dict) -> dict:
    started = time.perf_counter()
    ctx = runctx.get_current()
    dag = load_causal_dag()
    snap_id = upsert_dag_snapshot(dag.content, dag.source_file_hash, ctx.as_of)
    ctx.dag_snapshot_id = snap_id
    payload = {
        "snapshot_id": str(snap_id),
        "source_file_hash": dag.source_file_hash,
        "content": dag.content,
    }
    _record("read_causal_dag", {}, {"snapshot_id": str(snap_id), "nodes": len(dag.content.get("nodes", [])), "edges": len(dag.content.get("edges", []))}, started)
    return _text_content(dag.raw_yaml + f"\n\n# snapshot_id: {snap_id}")


# ---------------------------------------------------------------------------
# read_decision_journal
# ---------------------------------------------------------------------------


@tool(
    "read_decision_journal",
    "Read the N most recent portfolio decisions with rationale and gateway result. "
    "Use at the start of the run to ground reasoning in what was decided previously.",
    {"n_recent": int},
)
async def read_decision_journal(args: dict) -> dict:
    started = time.perf_counter()
    ctx = runctx.get_current()
    n = int(args.get("n_recent") or 10)
    entries = read_recent_decisions(n=n)
    ctx.journal_read = entries
    _record("read_decision_journal", {"n_recent": n}, {"n_returned": len(entries)}, started)
    return _text_content(json.dumps(entries, indent=2, default=str))


# ---------------------------------------------------------------------------
# read_current_portfolio
# ---------------------------------------------------------------------------


@tool(
    "read_current_portfolio",
    "Read the portfolio's current weights (reconstructed from the last APPROVED decision in Phase 1). "
    "Returns 100% cash if no prior approved decision exists.",
    {},
)
async def read_current_portfolio(args: dict) -> dict:
    started = time.perf_counter()
    from instrumentation.store import latest_approved_decision
    d = latest_approved_decision()
    if d is None:
        payload = {"weights": {}, "note": "no prior approved decision; portfolio is 100% cash"}
    else:
        payload = {
            "weights": d.proposed_portfolio,
            "decision_id": str(d.decision_id),
            "approved_at": d.proposed_at.isoformat(),
        }
    _record("read_current_portfolio", {}, payload, started)
    return _text_content(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# run_backtest
# ---------------------------------------------------------------------------


@tool(
    "run_backtest",
    "Run a monthly-rebalance backtest of a target weight set over a date range. "
    "Weights is a JSON object mapping symbol -> weight (e.g. '{\"SPY\": 0.6, \"IEF\": 0.4}'). "
    "Returns sharpe, max drawdown, and final/initial portfolio values. The backtest "
    "respects the run's as_of cutoff (no lookahead).",
    {"weights_json": str, "start": str, "end": str},
)
async def run_backtest(args: dict) -> dict:
    started = time.perf_counter()
    ctx = runctx.get_current()
    try:
        weights = json.loads(args["weights_json"])
        if not isinstance(weights, dict):
            raise ValueError("weights_json must decode to an object")
        start = _parse_date(args.get("start"), ctx.as_of - timedelta(days=365 * 3))
        end = _parse_date(args.get("end"), ctx.as_of)
        result = run_monthly_rebalance(weights, start, end, known_as_of=ctx.as_of)
        payload = {
            "sharpe": round(result.sharpe, 3),
            "max_drawdown": round(result.max_drawdown, 4),
            "final_value": round(result.final_value, 2),
            "initial_value": round(result.initial_value, 2),
            "total_return_pct": round((result.final_value / result.initial_value - 1) * 100, 2),
            "n_rebalances": result.n_rebalances,
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
        }
    except Exception as e:
        _record("run_backtest", args, None, started, error=str(e))
        return _text_content(f"Error: {e}")
    _record("run_backtest", args, payload, started)
    return _text_content(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# propose_portfolio (terminal)
# ---------------------------------------------------------------------------


@tool(
    "propose_portfolio",
    "Submit the agent's final target weights and rationale. This is the TERMINAL "
    "tool for the daily loop — after calling it the agent should stop. Weights is "
    "a JSON object mapping symbol -> weight; weights should sum to ~1.0. Rationale "
    "should be a paragraph or two explaining the proposal grounded in the agent's "
    "investigation.",
    {"weights_json": str, "rationale": str},
)
async def propose_portfolio(args: dict) -> dict:
    started = time.perf_counter()
    ctx = runctx.get_current()
    try:
        weights = json.loads(args["weights_json"])
        if not isinstance(weights, dict):
            raise ValueError("weights_json must decode to an object")
    except Exception as e:
        _record("propose_portfolio", args, None, started, error=str(e))
        return _text_content(f"Error parsing weights: {e}")

    # Persist the proposal as a structured field on the trace's output for
    # the daily-run orchestrator to consume.
    payload = {
        "weights": {k: float(v) for k, v in weights.items()},
        "rationale": args["rationale"],
        "submitted_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    ctx_output: dict = getattr(ctx, "_proposal", {}) or {}
    ctx_output.update(payload)
    ctx._proposal = payload  # type: ignore[attr-defined]
    _record("propose_portfolio", args, payload, started)
    return _text_content(
        "Proposal recorded. The daily-run orchestrator will pass it through "
        "the risk gateway. Stop here — you are done."
    )


# ---------------------------------------------------------------------------
# Catalog (exported for the agent loop)
# ---------------------------------------------------------------------------


ALL_TOOLS = [
    list_universe,
    get_prices,
    list_macro_series,
    get_macro_series,
    read_causal_dag,
    read_decision_journal,
    read_current_portfolio,
    run_backtest,
    propose_portfolio,
]
