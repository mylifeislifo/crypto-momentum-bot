"""YAML config loader with .env secret injection."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .schema import AppConfig


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be mapping: {path}")
    return data


def load_config(
    primary: str | Path,
    *overrides: str | Path,
    env_path: str | Path | None = ".env",
) -> AppConfig:
    """Load default.yaml + optional overrides + .env secrets into AppConfig."""
    if env_path and Path(env_path).exists():
        load_dotenv(env_path)

    merged: dict[str, Any] = {}
    for p in (primary, *overrides):
        merged = _deep_merge(merged, load_yaml(p))

    merged.setdefault("upbit_access_key", os.getenv("UPBIT_ACCESS_KEY"))
    merged.setdefault("upbit_secret_key", os.getenv("UPBIT_SECRET_KEY"))

    return AppConfig.model_validate(merged)
