"""MCP contract tests for InterroGate."""

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
    """Set deterministic auth/rate-limit config for MCP contract tests."""
    monkeypatch.setenv("INTERROGATE_API_KEY", "ig_test_contract_key")
    monkeypatch.setenv("INTERROGATE_ALLOW_INSECURE_DEV", "false")
    monkeypatch.setenv("INTERROGATE_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_tools_list_available_without_auth():
    request = {"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}}

    with TestClient(app) as client:
        response = client.post("/mcp", json=request)

    assert response.status_code == 200
    payload = response.json()
    tools = payload["result"]["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert "interrogate.health" in tool_names
    assert "interrogate.evaluate" in tool_names


def test_unknown_jsonrpc_method_returns_method_not_found():
    request = {"jsonrpc": "2.0", "id": "bad-method", "method": "ping", "params": {}}

    with TestClient(app) as client:
        response = client.post("/mcp", json=request)

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32601
    assert "Method not found" in payload["error"]["message"]


def test_tools_call_requires_auth():
    request = {
        "jsonrpc": "2.0",
        "id": "auth-missing",
        "method": "tools/call",
        "params": {"name": "interrogate.health", "arguments": {}},
    }

    with TestClient(app) as client:
        response = client.post("/mcp", json=request)

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "auth-missing"
    assert "error" in payload
    assert payload["error"]["code"] == "ERROR"


def test_tools_call_health_with_bearer_auth():
    request = {
        "jsonrpc": "2.0",
        "id": "health-1",
        "method": "tools/call",
        "params": {"name": "interrogate.health", "arguments": {}},
    }
    headers = {"Authorization": "Bearer ig_test_contract_key"}

    with TestClient(app) as client:
        response = client.post("/mcp", json=request, headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["status"] == "healthy"
    assert payload["result"]["service"] == "InterroGate"
