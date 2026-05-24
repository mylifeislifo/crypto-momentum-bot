from pathlib import Path

import yaml

from .schema import AppConfig, Secrets


def load_config(
    config_path: str = "config/default.yaml",
    override_path: str | None = None,
    env_file: str = ".env",
) -> tuple[AppConfig, Secrets]:
    """Load config from YAML (with optional override) and secrets from .env.

    Returns (AppConfig, Secrets) so callers can access secrets without
    embedding them into AppConfig (which gets logged/serialized).
    """
    base = _load_yaml(config_path)

    if override_path:
        override = _load_yaml(override_path)
        base = _deep_merge(base, override)

    config = AppConfig.model_validate(base)
    secrets = Secrets(_env_file=env_file)  # type: ignore[call-arg]

    return config, secrets


def _load_yaml(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with p.open() as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
