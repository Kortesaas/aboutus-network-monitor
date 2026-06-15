from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "network.yaml"
CONFIG_ENV_VAR = "ABOUTUS_MONITOR_CONFIG"


class ConfigError(RuntimeError):
    """Raised when the monitor configuration cannot be loaded."""


def get_config_path() -> Path:
    configured_path = os.getenv(CONFIG_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()
    return DEFAULT_CONFIG_PATH


def load_config() -> dict[str, Any]:
    config_path = get_config_path()
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    if not isinstance(loaded, dict):
        raise ConfigError("Configuration root must be a mapping.")

    return loaded
