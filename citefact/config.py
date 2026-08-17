"""User configuration file: ~/.config/citefact/config.toml.

This is the one deliberate exception to the "no hidden state" rule: an
explicit, documented file written ONLY when the user runs `citefact
setup`. It stores the LLM provider, API key, and optional model pin, so
first-time users are not sent off to edit shell profiles by hand.

Precedence stays: CLI flags > environment variables > this file > defaults.
`apply_config_to_env()` enforces that by never overriding variables that
are already set.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

_KEY_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "citefact" / "config.toml"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def save_config(data: dict[str, Any]) -> Path:
    """Write the config as TOML with owner-only permissions.

    The structure is flat sections of string values, so the writer is a
    few lines instead of a dependency.
    """
    lines: list[str] = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f'{key} = "{_toml_escape(str(value))}"')
        lines.append("")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # The file must be BORN owner-only: os.open creates it with 0600
    # atomically. A write-then-chmod sequence leaves a race window where
    # the key sits world-readable on disk. For a pre-existing looser file,
    # O_TRUNC empties it first and the explicit fchmod tightens it before
    # any content lands.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return path


def key_var_for(provider: str) -> str:
    return _KEY_VARS.get(provider, f"{provider.upper()}_API_KEY")


def apply_config_to_env() -> None:
    """Surface config values as environment variables, without ever
    overriding ones the user already set (env beats config)."""
    llm = load_config().get("llm", {})
    provider = llm.get("provider")
    api_key = llm.get("api_key")
    if provider and api_key:
        var = key_var_for(provider)
        os.environ.setdefault(var, api_key)
    model = llm.get("model")
    if model:
        os.environ.setdefault("CITEFACT_MODEL", model)
