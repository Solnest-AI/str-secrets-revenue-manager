from turno_mcp.config import (
    PRODUCTION_BASE_URL,
    SANDBOX_BASE_URL,
    ConfigError,
    load_config,
)

import pytest

BASE_ENV = {"TURNO_API_TOKEN": "tok", "TURNO_PARTNER_ID": "pid"}


def test_defaults_to_sandbox():
    cfg = load_config(dict(BASE_ENV))
    assert cfg.env == "sandbox"
    assert cfg.base_url == SANDBOX_BASE_URL
    assert cfg.api_prefix == "/v2"
    assert cfg.root_url == "https://sandbox.turnoverbnb.com/v2"
    assert cfg.is_production is False


def test_production_env_uses_production_base():
    cfg = load_config({**BASE_ENV, "TURNO_ENV": "production"})
    assert cfg.is_production is True
    assert cfg.base_url == PRODUCTION_BASE_URL
    assert cfg.root_url == "https://api.turnoverbnb.com/v2"


def test_missing_token_raises():
    with pytest.raises(ConfigError) as ei:
        load_config({"TURNO_PARTNER_ID": "pid"})
    assert "TURNO_API_TOKEN" in str(ei.value)


def test_missing_partner_raises():
    with pytest.raises(ConfigError) as ei:
        load_config({"TURNO_API_TOKEN": "tok"})
    assert "TURNO_PARTNER_ID" in str(ei.value)


def test_invalid_env_raises():
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, "TURNO_ENV": "staging"})


def test_base_url_override_strips_trailing_slash():
    cfg = load_config({**BASE_ENV, "TURNO_BASE_URL": "https://example.com/"})
    assert cfg.base_url == "https://example.com"
    assert cfg.root_url == "https://example.com/v2"


def test_api_prefix_override_normalizes_leading_slash():
    cfg = load_config({**BASE_ENV, "TURNO_API_PREFIX": "api/v2"})
    assert cfg.api_prefix == "/api/v2"
    assert cfg.root_url.endswith("/api/v2")
