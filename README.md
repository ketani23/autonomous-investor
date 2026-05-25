# autonomous-investor

AI systems engineering project that uses autonomous macro investing as substrate. See [`PROJECT_CHARTER.md`](PROJECT_CHARTER.md) for intent and constraints.

The deliverable is **not** a profitable trading system. It is a set of concrete observations about how to design, evaluate, and operate LLM agents that make consequential decisions with brutal ground truth.

## Phase 1 status

End-to-end loop: bitemporal substrate (15 ETFs, 5y prices + FRED/ALFRED macro vintages), deterministic risk gateway, monolithic Claude Agent SDK agent, daily run script, trace viewer. **No order execution** — that's Phase 2.

## Setup

```bash
# Install Python deps
uv sync

# Bring up Postgres
docker compose up -d postgres

# Apply schema
uv run python scripts/run_migrations.py

# Configure secrets
cp .env.example .env  # fill in FRED_API_KEY, ANTHROPIC_API_KEY

# Backfill 5y of data
uv run python scripts/backfill_prices.py --years 5
uv run python scripts/backfill_macro.py --years 5
```

## Running

```bash
# One-shot daily run (idempotent)
uv run python scripts/daily_run.py

# Inspect what happened
uv run aii trace list --since 1d
uv run aii trace walk <trace_id>
uv run aii decision list --recent 5
```

## Tests

```bash
uv run pytest                                  # everything
uv run pytest substrate/risk_gateway/          # gateway invariants
uv run pytest tests/test_backtest_as_of.py     # lookahead regression
```

## Layout

- `substrate/` — bitemporal store, ingestion, backtest, risk gateway. **Frozen after Phase 1.**
- `instrumentation/` — trace + decision schema (also frozen), persistence, CLI viewer (`aii`).
- `agents/monolithic/` — Architecture A.
- `config/` — TOML/YAML, version-controlled.
- `migrations/` — schema SQL.
- `scripts/` — daily run, backfills.
- `evals/` — Phase 2.
