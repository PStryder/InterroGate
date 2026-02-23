"""Snapshot test for InterroGate MCP tools/list contract."""

from __future__ import annotations

import json
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
    monkeypatch.setenv("INTERROGATE_API_KEY", "ig_test_snapshot")
    monkeypatch.setenv("INTERROGATE_ALLOW_INSECURE_DEV", "false")
    monkeypatch.setenv("INTERROGATE_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_mcp_tools_snapshot_contract():
    request = {"jsonrpc": "2.0", "id": "snapshot", "method": "tools/list", "params": {}}

    with TestClient(app) as client:
        response = client.post("/mcp", json=request)

    assert response.status_code == 200
    payload = response.json()

    snapshot_path = Path(__file__).resolve().parents[1] / "contracts" / "mcp_tools.snapshot.json"
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))

    actual = {
        "service": "InterroGate",
        "snapshot_type": "tools/list",
        "tools": payload["result"]["tools"],
    }
    assert actual == expected
