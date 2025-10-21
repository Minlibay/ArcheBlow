"""Centralised storage for external service API keys.

This module provides a minimal configuration layer so the desktop prototype can
obtain API credentials from environment variables while keeping a single source
of truth for the supported services.  Keys are resolved using the following
order of precedence:

1. Active process environment variables (e.g. ``BLOCKCYPHER_API_KEY``).
2. A local ``api_keys.env``/``.env`` file placed next to ``archeblow_desktop.py``
   or in the current working directory.  A custom path can be supplied through
   the ``ARCHEBLOW_API_KEYS_FILE`` environment variable.
3. The static defaults defined below.

Real deployments should replace this with an encrypted secrets manager, but for
the prototype we rely on in-memory constants and masking helpers to avoid
accidentally logging raw tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class APIServiceKey:
    """Describes how to resolve an API key for an external service."""

    service_id: str
    display_name: str
    env_var: str
    default_value: str | None = None

    def resolve(self) -> str | None:
        """Return the configured API key, preferring the environment variable."""

        value = os.getenv(self.env_var)
        if value:
            return value.strip() or None
        local_env = _load_local_env()
        file_value = local_env.get(self.env_var)
        if file_value:
            return file_value
        return self.default_value

    def masked(self) -> str:
        """Return the key with sensitive characters obfuscated for UI display."""

        value = self.resolve()
        if not value:
            return "—"
        if value.upper() == "N/A":
            return "N/A"
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


API_SERVICE_KEYS: Mapping[str, APIServiceKey] = {
    "blockcypher": APIServiceKey(
        service_id="blockcypher",
        display_name="BlockCypher API",
        env_var="BLOCKCYPHER_API_KEY",
        default_value=None,
    ),
    "blockchair": APIServiceKey(
        service_id="blockchair",
        display_name="Blockchair",
        env_var="BLOCKCHAIR_API_KEY",
        default_value=None,
    ),
    "chainz": APIServiceKey(
        service_id="chainz",
        display_name="Chainz",
        env_var="CHAINZ_API_KEY",
        default_value=None,
    ),
    "coingecko": APIServiceKey(
        service_id="coingecko",
        display_name="CoinGecko",
        env_var="COINGECKO_API_KEY",
        default_value=None,
    ),
    "ofac_watchlist": APIServiceKey(
        service_id="ofac_watchlist",
        display_name="OFAC Watchlist",
        env_var="OFAC_API_KEY",
        default_value="N/A",
    ),
    "heuristic_mixer": APIServiceKey(
        service_id="heuristic_mixer",
        display_name="Heuristic Mixer Watchlist",
        env_var="HEURISTIC_MIXER_TOKEN",
        default_value="N/A",
    ),
}


def get_api_key(service_id: str) -> str | None:
    """Return the raw API key for the requested service, if configured."""

    entry = API_SERVICE_KEYS.get(service_id)
    if not entry:
        return None
    return entry.resolve()


def get_masked_key(service_id: str) -> str:
    """Return a masked representation of the key for safe UI rendering."""

    entry = API_SERVICE_KEYS.get(service_id)
    if not entry:
        return "—"
    return entry.masked()


def _load_local_env() -> Mapping[str, str]:
    """Return cached key-value pairs loaded from optional local env files."""

    if hasattr(_load_local_env, "_cache"):
        return getattr(_load_local_env, "_cache")

    env_data: dict[str, str] = {}
    for path in _candidate_env_files():
        if not path.exists():
            continue
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in env_data:
                    continue
                env_data[key] = value.strip().strip('"').strip("'")
        except OSError:
            continue

    setattr(_load_local_env, "_cache", env_data)
    return env_data


def _candidate_env_files() -> list[Path]:
    """Return a list of files checked for local API key overrides."""

    base_dir = Path(__file__).resolve().parent
    project_root = base_dir
    for parent in base_dir.parents:
        if (parent / "archeblow_desktop.py").exists():
            project_root = parent
            break

    candidates = []
    override = os.getenv("ARCHEBLOW_API_KEYS_FILE")
    if override:
        override_path = Path(override).expanduser()
        candidates.append(override_path)

    candidates.extend(
        [
            project_root / "api_keys.env",
            project_root / ".env",
            base_dir / "api_keys.env",
            Path.cwd() / ".env",
        ]
    )
    # Remove duplicates while preserving order
    seen: set[Path] = set()
    unique_candidates: list[Path] = []
    for path in candidates:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        unique_candidates.append(path)
    return unique_candidates
