"""Configuration: credentials + environment / base-URL resolution.

All configuration comes from environment variables (see ``.env.example``):

- ``TURNO_API_TOKEN``   — partner Bearer JWT (required)
- ``TURNO_PARTNER_ID``  — partner UUID for the ``TBNB-Partner-ID`` header (required)
- ``TURNO_ENV``         — ``sandbox`` (default) or ``production``
- ``TURNO_BASE_URL``    — optional host override
- ``TURNO_API_PREFIX``  — optional path prefix override (default ``/v2``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SANDBOX_BASE_URL = "https://sandbox.turnoverbnb.com"
PRODUCTION_BASE_URL = "https://api.turnoverbnb.com"
DEFAULT_API_PREFIX = "/v2"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class TurnoConfig:
    api_token: str
    partner_id: str
    env: str  # "sandbox" | "production"
    base_url: str
    api_prefix: str

    @property
    def root_url(self) -> str:
        """Base URL joined with the version prefix, no trailing slash."""
        return f"{self.base_url.rstrip('/')}{self.api_prefix}"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def _resolve_base_url(env: str, override: str | None) -> str:
    if override:
        return override.rstrip("/")
    return PRODUCTION_BASE_URL if env == "production" else SANDBOX_BASE_URL


def _load_dotenv_once() -> None:
    """Best-effort load of a local .env into os.environ (does not override existing vars)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def load_config(environ: dict[str, str] | None = None) -> TurnoConfig:
    """Build a :class:`TurnoConfig` from environment variables.

    Accepts an explicit ``environ`` mapping for testing; defaults to ``os.environ``
    (loading a local ``.env`` first, without overriding already-set variables).
    Raises :class:`ConfigError` with an actionable message on missing/invalid input.
    """
    if environ is None:
        _load_dotenv_once()
        e = os.environ
    else:
        e = environ

    token = (e.get("TURNO_API_TOKEN") or "").strip()
    partner = (e.get("TURNO_PARTNER_ID") or "").strip()
    env = (e.get("TURNO_ENV") or "sandbox").strip().lower()

    if env not in ("sandbox", "production"):
        raise ConfigError(f"TURNO_ENV must be 'sandbox' or 'production', got {env!r}")

    missing = [
        name
        for name, value in (("TURNO_API_TOKEN", token), ("TURNO_PARTNER_ID", partner))
        if not value
    ]
    if missing:
        raise ConfigError(
            "Missing required env var(s): "
            + ", ".join(missing)
            + ". Set them in your environment or .env (see .env.example)."
        )

    base_url = _resolve_base_url(env, (e.get("TURNO_BASE_URL") or "").strip() or None)

    api_prefix = (e.get("TURNO_API_PREFIX") or DEFAULT_API_PREFIX).strip()
    if not api_prefix.startswith("/"):
        api_prefix = "/" + api_prefix
    api_prefix = api_prefix.rstrip("/")

    return TurnoConfig(
        api_token=token,
        partner_id=partner,
        env=env,
        base_url=base_url,
        api_prefix=api_prefix,
    )
