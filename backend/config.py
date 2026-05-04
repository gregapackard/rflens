from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT_DIR / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "config.example.yaml"


def resolve_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return ROOT_DIR / candidate


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        config_path = EXAMPLE_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def get_database_path(config: dict[str, Any] | None = None) -> Path:
    cfg = config or load_config()
    return resolve_path(cfg.get("database_path", "./data/rflens.db"))


def source_config(name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    return cfg.get("sources", {}).get(name, {}) or {}
