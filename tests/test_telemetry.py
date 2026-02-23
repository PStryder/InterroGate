"""Telemetry counter tests for InterroGate MCP flow."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Allow running tests directly from repo without editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interrogate.config import get_settings
from interrogate.mcp import app
from interrogate.telemetry import telemetry


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTERROGATE_API_KEY", "ig_test_telemetry")
    monkeypatch.setenv("INTERROGATE_ALLOW_INSECURE_DEV", "false")
    monkeypatch.setenv("INTERROGATE_RATE_LIMIT_ENABLED", "false")
    telemetry.reset()
    get_settings.cache_clear()
    yield
    telemetry.reset()
    get_settings.cache_clear()


def test_telemetry_records_jsonrpc_method_errors():
    request = {"jsonrpc": "2.0", "id": "bad-method", "method": "ping", "params": {}}

    with TestClient(app) as client:
        response = client.post("/mcp", json=request)

    assert response.status_code == 200
    snapshot = telemetry.snapshot()
    assert snapshot["request_total"] == 1
    assert snapshot["request_method_counts"]["ping"] == 1
    assert snapshot["tools_call_error_code_counts"]["-32601"] == 1


def test_telemetry_records_deny_reason():
    request = {
        "jsonrpc": "2.0",
        "id": "deny-1",
        "method": "tools/call",
        "params": {
            "name": "interrogate.check",
            "arguments": {
                "envelope": {
                    "tenant_id": "tenant-a",
                    "surface_id": "surface-a",
                    "policy_profile_id": "profile-a",
                    "payload_kind": "work_order",
                    "payload": {},
                }
            },
        },
    }

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=request,
            headers={"Authorization": "Bearer ig_test_telemetry"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["decision"] == "deny"

    snapshot = telemetry.snapshot()
    assert snapshot["request_method_counts"]["tools/call"] == 1
    assert snapshot["deny_reason_counts"]["missing_causality"] == 1
