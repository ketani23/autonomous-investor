"""Typed config loaders.

All Phase 1 settings live in config/ as TOML (+ one YAML for the DAG). These
loaders parse and validate them and are called fresh on each invocation so
config edits take effect on the next agent run without restarts.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Symbol:
    ticker: str
    sleeve: str
    description: str


def load_universe(path: Path | None = None) -> list[Symbol]:
    p = path or (CONFIG_DIR / "universe.toml")
    data = tomllib.loads(p.read_text())
    return [Symbol(**s) for s in data["symbol"]]


def universe_tickers(path: Path | None = None) -> list[str]:
    return [s.ticker for s in load_universe(path)]


# ---------------------------------------------------------------------------
# Macro series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MacroSeries:
    id: str
    sleeve: str
    vintage_tracked: bool
    description: str


def load_macro_series(path: Path | None = None) -> list[MacroSeries]:
    p = path or (CONFIG_DIR / "macro_series.toml")
    data = tomllib.loads(p.read_text())
    return [MacroSeries(**s) for s in data["series"]]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelConfig:
    model: str
    model_version_pinned_at: str
    max_tokens: int


def load_model_config(role: str = "monolithic", path: Path | None = None) -> ModelConfig:
    p = path or (CONFIG_DIR / "models.toml")
    data = tomllib.loads(p.read_text())
    role_data = data[role]
    return ModelConfig(
        model=role_data["model"],
        model_version_pinned_at=role_data["model_version_pinned_at"],
        max_tokens=role_data.get("max_tokens", 8000),
    )


# ---------------------------------------------------------------------------
# Risk gateway
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskGatewayConfig:
    max_position_weight: float
    max_gross_exposure: float
    max_net_exposure: float
    min_net_exposure: float
    daily_loss_circuit_breaker: float
    max_orders_per_day: int
    max_orders_per_week: int
    max_tokens_per_decision: int
    whitelist: tuple[str, ...]
    config_hash: str

    @classmethod
    def from_dict(cls, raw_text: str, data: dict[str, Any], whitelist: tuple[str, ...]) -> "RiskGatewayConfig":
        return cls(
            max_position_weight=float(data["position"]["max_position_weight"]),
            max_gross_exposure=float(data["exposure"]["max_gross_exposure"]),
            max_net_exposure=float(data["exposure"]["max_net_exposure"]),
            min_net_exposure=float(data["exposure"]["min_net_exposure"]),
            daily_loss_circuit_breaker=float(data["loss"]["daily_loss_circuit_breaker"]),
            max_orders_per_day=int(data["frequency"]["max_orders_per_day"]),
            max_orders_per_week=int(data["frequency"]["max_orders_per_week"]),
            max_tokens_per_decision=int(data["tokens"]["max_tokens_per_decision"]),
            whitelist=whitelist,
            config_hash=hashlib.sha256(raw_text.encode()).hexdigest()[:16],
        )


def load_risk_gateway_config(path: Path | None = None) -> RiskGatewayConfig:
    p = path or (CONFIG_DIR / "risk_gateway.toml")
    raw = p.read_text()
    data = tomllib.loads(raw)
    whitelist = tuple(universe_tickers())  # whitelist sourced from universe.toml
    return RiskGatewayConfig.from_dict(raw, data, whitelist)


# ---------------------------------------------------------------------------
# Causal DAG
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CausalDAG:
    content: dict[str, Any]
    source_file_hash: str
    raw_yaml: str


def load_causal_dag(path: Path | None = None) -> CausalDAG:
    p = path or (CONFIG_DIR / "dag" / "macro.yaml")
    raw = p.read_text()
    return CausalDAG(
        content=yaml.safe_load(raw),
        source_file_hash=hashlib.sha256(raw.encode()).hexdigest()[:16],
        raw_yaml=raw,
    )
