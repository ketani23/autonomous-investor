# autonomous-investor

An AI systems engineering project that uses autonomous macro investing as substrate. See [`PROJECT_CHARTER.md`](PROJECT_CHARTER.md) for the full intent.

The deliverable is **not** a profitable trading system. It is a body of concrete observations about how to design, evaluate, and operate LLM-based agents that make consequential decisions in a domain with brutal, indifferent ground truth. Investing is chosen because the signal is unforgiving and the work maps to specific problems — calibration, memory, robustness, foreknowledge contamination, judgment under uncertainty — that matter across all serious agent applications.

## What's here

A daily-decision agent that runs the **Perceive → Hypothesize → Investigate → Synthesize → Learn** loop on real macro and equity-ETF data, produces a proposed target portfolio, has it evaluated by a deterministic risk gateway, and records the full reasoning trace. No order execution yet — that's Phase 2.

Architecturally, two principles are load-bearing:

1. **Bitemporal everywhere.** Every datum has both a `valid_from` (when the fact became true in the world) and an `as_of` (when the system learned it). Restatements are additive rows, never overwrites. A backtest at simulation date T queries with `as_of <= T`, so foreknowledge bias is prevented structurally rather than by convention. Critically, FRED's revisable macro series (CPI, payrolls, jobless claims, etc.) are ingested through ALFRED's vintage API — each release date becomes its own `as_of` row.

2. **Deterministic where it must be, agentic where it should be.** Risk limits, accounting, the trade gate, and bitemporal data access are deterministic Python. Hypothesis generation, research synthesis, causal reasoning, and decision framing are the agent's job. The boundary is non-negotiable and is tested before any agent connects.

## Phase 1 status

End-to-end loop works:

- Bitemporal substrate: 15 liquid ETFs (5y daily) + 18 FRED macro series, vintage-tracked where relevant
- Hand-rolled monolithic Claude Agent SDK agent (Architecture A), Sonnet 4.6 pinned, model version recorded into every trace
- Deterministic risk gateway with 7 invariants (per-position, gross/net exposure, daily loss, order frequency, instrument whitelist, token budget), 22 unit tests written before the agent was wired in
- Causal DAG over macro variables that the agent references in its hypothesize phase
- Trace + decision viewer (`aii` CLI) that pretty-prints any past run phase-by-phase

Phase 3 will add Architecture B (Planner → Supervisor → Specialists) in parallel against the same eval scenarios; the comparison between the two architectures is the project's central interview artifact.

## Setup

Requires Python 3.12+, Docker, and the Claude Code CLI authenticated against a Claude Max subscription (the Agent SDK inherits OAuth credentials — **do not set `ANTHROPIC_API_KEY`** in `.env` if you want subscription billing).

```bash
# Install deps
uv sync

# Configure secrets (FRED_API_KEY + DATABASE_URL only — no Anthropic key)
cp .env.example .env && $EDITOR .env

# Bring up Postgres
docker compose up -d

# Apply schema
uv run python scripts/run_migrations.py

# Backfill (resumable; FRED rate limits the macro pull to ~10 minutes)
uv run python scripts/backfill_prices.py --years 5
uv run python scripts/backfill_macro.py --years 5
```

## Running

```bash
# One daily run (idempotent — re-running same day is a no-op)
uv run python scripts/daily_run.py

# Inspect what happened
uv run aii trace  list --since 1d
uv run aii trace  show <trace_id>
uv run aii trace  walk <trace_id>
uv run aii decision list --recent 5
uv run aii decision show <decision_id>
```

In production this is wired to `cron` at 13:00 UTC weekdays on an always-on VM.

## Tests

```bash
uv run pytest                                # everything (38 tests)
uv run pytest substrate/risk_gateway/        # gateway invariants
uv run pytest tests/test_backtest_as_of.py   # the foreknowledge regression test
```

The `as_of` regression test is the structural proof of the substrate's central claim: at simulation date T, the backtest must only see data with `as_of <= T`.

## Layout

- `substrate/` — bitemporal store, FRED/ALFRED + price ingestion, `vectorbt` backtest wrapper, risk gateway. **Frozen after Phase 1.**
- `instrumentation/` — trace + decision Pydantic schema (also frozen), Postgres persistence, `aii` CLI viewer.
- `agents/monolithic/` — Architecture A: system prompt, MCP tool catalog (9 tools), one-loop agent.
- `config/` — TOML for settings, YAML for the causal DAG, all version-controlled.
- `migrations/` — bitemporal schema SQL.
- `scripts/` — `daily_run.py` (cron target), backfills, migration runner.
- `evals/` — empty in Phase 1; the eval harness is Phase 2 and the project's highest-priority artifact.

## Charter principles, restated for the impatient

- **Substrate is frozen after Phase 1.** Wanting to improve it is a sign to write a "known limitation" and move on.
- **Live P&L is a footnote, not a metric.** The risk gateway watches it; the author does not.
- **The agent eval harness is the highest-value artifact**, more than any individual architecture.
- **Two architectures done well beats four done shallowly.**
- **Commit history is part of the artifact.** Read the messages.
