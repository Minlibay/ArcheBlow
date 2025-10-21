"""Centralised storage for external service API keys.

This module provides a minimal configuration layer so the desktop prototype can
obtain API credentials from environment variables while keeping a single source
of truth for the supported services.  Real deployments should replace this with
an encrypted secrets manager, but for the prototype we rely on in-memory
constants and masking helpers to avoid accidentally logging raw tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
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
