-- Phase 1 bitemporal schema. Frozen at end of step 2.
-- Common pattern: every fact table has (valid_from, valid_to, as_of, superseded_by).
--   valid_from / valid_to  — when the fact is true in the world
--   as_of                  — when the system learned the fact
--   superseded_by          — uuid of the row that replaced this row's view (NULL = current)
--
-- Bitemporal predicate: WHERE valid_from <= world_at
--                         AND (valid_to IS NULL OR valid_to > world_at)
--                         AND as_of <= known_as_of

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- =============================================================================
-- Market prices
-- =============================================================================
CREATE TABLE bitemporal_market_prices (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          text        NOT NULL,
    valid_from      timestamptz NOT NULL,
    valid_to        timestamptz NULL,
    as_of           timestamptz NOT NULL,
    superseded_by   uuid        NULL REFERENCES bitemporal_market_prices(id),
    source          text        NOT NULL,
    open            double precision NULL,
    high            double precision NULL,
    low             double precision NULL,
    close           double precision NULL,
    adj_close       double precision NULL,
    volume          bigint           NULL
);

CREATE INDEX ix_prices_symbol_asof_validfrom
    ON bitemporal_market_prices (symbol, as_of, valid_from);
CREATE INDEX ix_prices_current
    ON bitemporal_market_prices (symbol, valid_from)
    WHERE valid_to IS NULL;

-- =============================================================================
-- Macro observations (FRED + ALFRED vintages)
-- =============================================================================
CREATE TABLE bitemporal_macro_observations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    series_id       text        NOT NULL,
    valid_from      timestamptz NOT NULL,        -- observation period start
    valid_to        timestamptz NULL,
    as_of           timestamptz NOT NULL,        -- release / vintage date
    superseded_by   uuid        NULL REFERENCES bitemporal_macro_observations(id),
    source          text        NOT NULL,        -- 'fred' or 'alfred'
    value           double precision NULL,
    vintage_date    date        NULL             -- ALFRED's realtime_start, redundant with as_of
);

CREATE INDEX ix_macro_series_asof_validfrom
    ON bitemporal_macro_observations (series_id, as_of, valid_from);
CREATE INDEX ix_macro_current
    ON bitemporal_macro_observations (series_id, valid_from)
    WHERE valid_to IS NULL;

-- =============================================================================
-- Agent traces (frozen schema after this migration)
-- =============================================================================
CREATE TABLE agent_traces (
    trace_id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_trace_id           uuid NULL REFERENCES agent_traces(trace_id),
    phase                     text NOT NULL,
        -- one of: perceive, hypothesize, investigate, synthesize, learn, other
    agent_role                text NOT NULL,
        -- 'monolithic' in phase 1; specialist names later
    model                     text NOT NULL,
    model_version_pinned_at   text NOT NULL,
    started_at                timestamptz NOT NULL,
    completed_at              timestamptz NULL,
    as_of                     timestamptz NOT NULL,
    input_summary             jsonb NOT NULL DEFAULT '{}'::jsonb,
    tool_calls                jsonb NOT NULL DEFAULT '[]'::jsonb,
    output                    jsonb NOT NULL DEFAULT '{}'::jsonb,
    reasoning_text            text NULL,
    token_usage               jsonb NOT NULL DEFAULT '{}'::jsonb,
    cost_usd                  numeric(12, 6) NULL,
    parent_decision_id        uuid NULL
);

CREATE INDEX ix_traces_started_at_desc ON agent_traces (started_at DESC);
CREATE INDEX ix_traces_parent_decision ON agent_traces (parent_decision_id);
CREATE INDEX ix_traces_parent_trace    ON agent_traces (parent_trace_id);

-- =============================================================================
-- Portfolio decisions
-- =============================================================================
CREATE TABLE portfolio_decisions (
    decision_id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposed_at                 timestamptz NOT NULL,
    as_of                       timestamptz NOT NULL,
    proposed_portfolio          jsonb NOT NULL,        -- {symbol: weight}
    rationale                   text  NOT NULL,
    agent_trace_id              uuid  NULL REFERENCES agent_traces(trace_id),
    risk_gateway_result         text  NOT NULL,        -- approved | rejected | requires_approval
    risk_gateway_eval_id        uuid  NULL,            -- FK set after evaluation row exists
    human_decision              text  NULL,            -- approved | rejected | overridden
    human_decision_reasoning    text  NULL,
    executed                    boolean NOT NULL DEFAULT false
);

CREATE INDEX ix_decisions_proposed_at_desc ON portfolio_decisions (proposed_at DESC);
CREATE INDEX ix_decisions_as_of            ON portfolio_decisions (as_of);

-- =============================================================================
-- Risk gateway evaluations
-- =============================================================================
CREATE TABLE risk_gateway_evaluations (
    eval_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id          uuid NULL REFERENCES portfolio_decisions(decision_id),
    evaluated_at         timestamptz NOT NULL,
    input_proposal       jsonb NOT NULL,
    input_state          jsonb NOT NULL,
    input_config_hash    text  NOT NULL,
    result               text  NOT NULL,            -- approved | rejected | requires_approval
    reasons              jsonb NOT NULL DEFAULT '[]'::jsonb,
    blocking_checks      jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX ix_rge_decision     ON risk_gateway_evaluations (decision_id);
CREATE INDEX ix_rge_evaluated_at ON risk_gateway_evaluations (evaluated_at DESC);

-- Wire the FK from decisions back to its evaluation (deferred because of mutual dep)
ALTER TABLE portfolio_decisions
    ADD CONSTRAINT fk_decision_rge
    FOREIGN KEY (risk_gateway_eval_id) REFERENCES risk_gateway_evaluations(eval_id);

-- =============================================================================
-- Causal DAG snapshots — what the agent saw when it read the DAG
-- =============================================================================
CREATE TABLE causal_dag_snapshots (
    snapshot_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    valid_from         timestamptz NOT NULL,
    as_of              timestamptz NOT NULL,
    content            jsonb NOT NULL,
    source_file_hash   text  NOT NULL
);

CREATE INDEX ix_dag_as_of_desc ON causal_dag_snapshots (as_of DESC);
CREATE UNIQUE INDEX ux_dag_hash_asof
    ON causal_dag_snapshots (source_file_hash, as_of);

-- =============================================================================
-- Decision journal entries
-- =============================================================================
CREATE TABLE decision_journal_entries (
    entry_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id  uuid NULL REFERENCES portfolio_decisions(decision_id),
    entry_text   text NOT NULL,
    created_at   timestamptz NOT NULL
);

CREATE INDEX ix_journal_created_at ON decision_journal_entries (created_at DESC);

-- Migration bookkeeping is created by scripts/run_migrations.py itself.
-- Recording this migration's application happens there too, so no INSERT here.
