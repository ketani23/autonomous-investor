"""Config loaders should round-trip cleanly and produce stable hashes."""

from substrate import config as c


def test_universe_has_15_symbols():
    universe = c.load_universe()
    assert len(universe) == 15
    tickers = {s.ticker for s in universe}
    assert {"SPY", "TLT", "GLD", "UUP", "VNQ"}.issubset(tickers)


def test_macro_series_marks_revising_series():
    series = c.load_macro_series()
    by_id = {s.id: s for s in series}
    assert by_id["CPIAUCSL"].vintage_tracked is True
    assert by_id["PAYEMS"].vintage_tracked is True
    assert by_id["FEDFUNDS"].vintage_tracked is False
    assert by_id["DGS10"].vintage_tracked is False


def test_model_config_loads_pinned_sonnet():
    m = c.load_model_config("monolithic")
    assert m.model == "claude-sonnet-4-6"
    assert m.model_version_pinned_at == "claude-sonnet-4-6"


def test_risk_gateway_config_has_stable_hash():
    cfg = c.load_risk_gateway_config()
    again = c.load_risk_gateway_config()
    assert cfg.config_hash == again.config_hash
    assert len(cfg.config_hash) == 16


def test_risk_gateway_whitelist_matches_universe():
    cfg = c.load_risk_gateway_config()
    assert set(cfg.whitelist) == set(c.universe_tickers())


def test_dag_loads_with_expected_nodes_and_edges():
    dag = c.load_causal_dag()
    node_ids = {n["id"] for n in dag.content["nodes"]}
    assert {"fed_funds_rate", "yield_curve_slope", "inflation", "real_growth"}.issubset(node_ids)
    edges = dag.content["edges"]
    assert any(e["from"] == "fed_funds_rate" and e["to"] == "yield_curve_slope" for e in edges)
    assert len(dag.source_file_hash) == 16
