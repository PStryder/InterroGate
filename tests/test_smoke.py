"""Smoke tests for basic InterroGate packaging and startup behavior."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Allow running tests directly from repo without editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interrogate.config import get_settings
from interrogate.mcp import app


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch):
    """Provide deterministic local auth/rate-limit config for smoke tests."""
    monkeypatch.setenv("INTERROGATE_API_KEY", "ig_test_smoke_key")
    monkeypatch.setenv("INTERROGATE_ALLOW_INSECURE_DEV", "false")
    monkeypatch.setenv("INTERROGATE_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_import_and_validation_smoke():
    """Settings should import and validate with required API key configured."""
    settings = get_settings()
    assert settings.api_key == "ig_test_smoke_key"
    assert settings.allow_insecure_dev is False


def test_mcp_health_smoke():
    """MCP entrypoint should respond with healthy status under normal startup."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "interrogate.health", "arguments": {}},
    }

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=request,
            headers={"Authorization": "Bearer ig_test_smoke_key"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["status"] == "healthy"
    assert payload["result"]["service"] == "InterroGate"
