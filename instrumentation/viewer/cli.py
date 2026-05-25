"""`aii` — CLI to inspect agent traces and decisions."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from instrumentation import store
from instrumentation.schema import TraceRecord

console = Console()


def _ago(dt: datetime) -> str:
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)}m ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    return f"{delta.days}d ago"


@click.group()
def main():
    """aii — Autonomous Investor inspection CLI."""
    load_dotenv()


# =============================================================================
# trace subcommands
# =============================================================================


@main.group()
def trace():
    """Inspect agent traces."""


@trace.command("list")
@click.option("--since", default="7d", help="e.g. 1d, 12h, 7d")
@click.option("--limit", default=20, type=int)
def trace_list(since: str, limit: int):
    seconds = _parse_since(since)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)
    traces = store.list_traces(since=cutoff, limit=limit)
    if not traces:
        console.print("[dim]No traces in that window.[/dim]")
        return
    table = Table(title=f"Traces since {since}")
    table.add_column("trace_id", no_wrap=True)
    table.add_column("started")
    table.add_column("role")
    table.add_column("model")
    table.add_column("tool calls", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("cost", justify="right")
    for t in traces:
        table.add_row(
            str(t.trace_id)[:8] + "…",
            _ago(t.started_at),
            t.agent_role,
            t.model,
            str(len(t.tool_calls)),
            str(t.token_usage.total) if hasattr(t.token_usage, "total") else "?",
            f"${t.cost_usd:.4f}" if t.cost_usd is not None else "—",
        )
    console.print(table)


@trace.command("show")
@click.argument("trace_id")
def trace_show(trace_id: str):
    t = _resolve_trace(trace_id)
    _render_header(t)
    _render_input(t)
    _render_tool_calls(t)
    _render_reasoning(t)
    _render_output(t)


@trace.command("walk")
@click.argument("trace_id")
def trace_walk(trace_id: str):
    """Walk a trace tool-call-by-tool-call with a pause between each."""
    t = _resolve_trace(trace_id)
    _render_header(t)
    _render_input(t)
    console.print("\n[bold yellow]Tool calls (press Enter between each):[/bold yellow]\n")
    for i, tc in enumerate(t.tool_calls, 1):
        _render_one_tool_call(i, tc)
        try:
            input(f"  -- step {i}/{len(t.tool_calls)} -- ENTER for next, ^C to stop -- ")
        except (KeyboardInterrupt, EOFError):
            break
    _render_reasoning(t)
    _render_output(t)


def _resolve_trace(trace_id_str: str) -> TraceRecord:
    """Allow prefix matching for convenience."""
    try:
        uid = UUID(trace_id_str)
        t = store.read_trace(uid)
        if t:
            return t
    except ValueError:
        pass
    # Prefix match against recent traces
    recent = store.list_traces(
        since=datetime.now(tz=timezone.utc) - timedelta(days=30),
        limit=200,
    )
    matches = [t for t in recent if str(t.trace_id).startswith(trace_id_str)]
    if not matches:
        console.print(f"[red]No trace matching {trace_id_str}[/red]")
        sys.exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous prefix {trace_id_str} — {len(matches)} matches[/red]")
        sys.exit(1)
    return matches[0]


def _render_header(t: TraceRecord):
    body = (
        f"[bold]trace_id[/bold]    {t.trace_id}\n"
        f"[bold]agent_role[/bold]  {t.agent_role}\n"
        f"[bold]model[/bold]       {t.model}  [dim](pinned: {t.model_version_pinned_at})[/dim]\n"
        f"[bold]started[/bold]     {t.started_at.isoformat()}  ({_ago(t.started_at)})\n"
        f"[bold]as_of[/bold]       {t.as_of.isoformat()}\n"
        f"[bold]tool calls[/bold]  {len(t.tool_calls)}\n"
        f"[bold]tokens[/bold]      in={getattr(t.token_usage, 'input_tokens', '?')}  "
        f"out={getattr(t.token_usage, 'output_tokens', '?')}  "
        f"cache_r={getattr(t.token_usage, 'cache_read_tokens', '?')}  "
        f"cache_w={getattr(t.token_usage, 'cache_write_tokens', '?')}\n"
        f"[bold]cost_usd[/bold]    {t.cost_usd if t.cost_usd is not None else '—'}\n"
        f"[bold]parent_decision_id[/bold]  {t.parent_decision_id or '—'}"
    )
    console.print(Panel(body, title="Trace", border_style="cyan"))


def _render_input(t: TraceRecord):
    if t.input_summary:
        console.print(Panel(_pretty_dict(t.input_summary), title="Input summary", border_style="dim"))


def _render_tool_calls(t: TraceRecord):
    if not t.tool_calls:
        console.print("[dim]No tool calls.[/dim]")
        return
    table = Table(title="Tool calls", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("name")
    table.add_column("input (truncated)", overflow="fold")
    table.add_column("latency", justify="right")
    table.add_column("err")
    for i, tc in enumerate(t.tool_calls, 1):
        name = tc["name"] if isinstance(tc, dict) else tc.name
        inp = tc["input"] if isinstance(tc, dict) else tc.input
        lat = tc["latency_ms"] if isinstance(tc, dict) else tc.latency_ms
        err = tc.get("error") if isinstance(tc, dict) else tc.error
        table.add_row(
            str(i),
            name,
            _short_repr(inp, 80),
            f"{lat:.1f}ms" if lat else "—",
            "✗" if err else "",
        )
    console.print(table)


def _render_one_tool_call(idx: int, tc):
    name = tc["name"] if isinstance(tc, dict) else tc.name
    inp = tc["input"] if isinstance(tc, dict) else tc.input
    out = tc["output"] if isinstance(tc, dict) else tc.output
    lat = tc["latency_ms"] if isinstance(tc, dict) else tc.latency_ms
    err = tc.get("error") if isinstance(tc, dict) else tc.error
    body = (
        f"[bold]name[/bold]    {name}\n"
        f"[bold]input[/bold]   {_pretty(inp)}\n"
        f"[bold]output[/bold]  {_pretty(out)}\n"
        f"[bold]latency[/bold] {lat:.1f}ms" if lat else ""
    )
    if err:
        body += f"\n[red]error: {err}[/red]"
    console.print(Panel(body, title=f"Tool call {idx}", border_style="yellow" if err else "green"))


def _render_reasoning(t: TraceRecord):
    if t.reasoning_text:
        console.print(Panel(t.reasoning_text, title="Reasoning", border_style="magenta"))


def _render_output(t: TraceRecord):
    if t.output:
        console.print(Panel(_pretty_dict(t.output), title="Output", border_style="cyan"))


def _pretty(v) -> str:
    if isinstance(v, dict):
        return _pretty_dict(v)
    return str(v)[:1500]


def _pretty_dict(d: dict) -> str:
    import json
    try:
        return json.dumps(d, indent=2, default=str)[:2000]
    except Exception:
        return str(d)[:2000]


def _short_repr(v, n: int = 80) -> str:
    s = _pretty(v) if isinstance(v, dict) else str(v)
    return s if len(s) <= n else s[:n] + "…"


def _parse_since(spec: str) -> int:
    if spec.endswith("d"):
        return int(spec[:-1]) * 86400
    if spec.endswith("h"):
        return int(spec[:-1]) * 3600
    if spec.endswith("m"):
        return int(spec[:-1]) * 60
    return int(spec)


# =============================================================================
# decision subcommands
# =============================================================================


@main.group()
def decision():
    """Inspect portfolio decisions."""


@decision.command("list")
@click.option("--recent", default=10, type=int)
def decision_list(recent: int):
    ds = store.list_decisions(limit=recent)
    if not ds:
        console.print("[dim]No decisions yet.[/dim]")
        return
    table = Table(title=f"Last {len(ds)} decisions")
    table.add_column("decision_id", no_wrap=True)
    table.add_column("proposed")
    table.add_column("result")
    table.add_column("symbols", justify="right")
    table.add_column("rationale (head)", overflow="fold")
    for d in ds:
        result_color = {
            "approved": "green",
            "requires_approval": "yellow",
            "rejected": "red",
        }.get(d.risk_gateway_result.value, "white")
        table.add_row(
            str(d.decision_id)[:8] + "…",
            _ago(d.proposed_at),
            f"[{result_color}]{d.risk_gateway_result.value}[/{result_color}]",
            str(len(d.proposed_portfolio or {})),
            (d.rationale[:60] + "…") if len(d.rationale) > 60 else d.rationale,
        )
    console.print(table)


@decision.command("show")
@click.argument("decision_id")
def decision_show(decision_id: str):
    try:
        uid = UUID(decision_id)
        d = store.read_decision(uid)
    except ValueError:
        recent = store.list_decisions(limit=200)
        matches = [d for d in recent if str(d.decision_id).startswith(decision_id)]
        if len(matches) != 1:
            console.print(f"[red]{len(matches)} matches for {decision_id}[/red]")
            sys.exit(1)
        d = matches[0]

    if d is None:
        console.print(f"[red]No decision {decision_id}[/red]")
        sys.exit(1)

    body = (
        f"[bold]decision_id[/bold]   {d.decision_id}\n"
        f"[bold]proposed_at[/bold]   {d.proposed_at.isoformat()}\n"
        f"[bold]as_of[/bold]         {d.as_of.isoformat()}\n"
        f"[bold]gateway[/bold]       {d.risk_gateway_result.value}\n"
        f"[bold]trace_id[/bold]      {d.agent_trace_id or '—'}\n"
        f"[bold]executed[/bold]      {d.executed}\n"
    )
    console.print(Panel(body, title="Decision", border_style="cyan"))
    console.print(Panel(_pretty_dict(d.proposed_portfolio or {}), title="Proposed portfolio"))
    console.print(Panel(d.rationale, title="Rationale", border_style="magenta"))
    if d.risk_gateway_eval_id:
        ev = store.read_evaluation(d.risk_gateway_eval_id)
        if ev:
            body = (
                f"[bold]result[/bold]   {ev.result.value}\n"
                f"[bold]reasons[/bold]  {ev.reasons or '—'}\n"
                f"[bold]checks[/bold]   {ev.blocking_checks or '—'}\n"
                f"[bold]hash[/bold]     {ev.input_config_hash}"
            )
            console.print(Panel(body, title="Risk gateway evaluation", border_style="yellow"))


if __name__ == "__main__":
    main()
