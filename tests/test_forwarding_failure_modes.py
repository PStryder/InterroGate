"""Forwarding failure-mode tests for InterroGate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

# Allow running tests directly from repo without editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interrogate.config import get_settings
from interrogate.forwarder import RequestForwarder
from interrogate.models import AdmissionReceipt, Decision, EvaluationResult
from interrogate.telemetry import telemetry


def _allow_result(forward_targets: list[str], payload: dict[str, object]) -> EvaluationResult:
    receipt = AdmissionReceipt(
        phase="accepted",
        decision=Decision.ALLOW,
        tenant_id="tenant-a",
        surface_id="surface-a",
        policy_profile_id="profile-a",
        policy_version="1.0",
    )
    return EvaluationResult(
        decision=Decision.ALLOW,
        receipt=receipt,
        forward_targets=forward_targets,
        forwarded_payload=payload,
    )


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTERROGATE_API_KEY", "ig_test_forward")
    monkeypatch.setenv("INTERROGATE_ALLOW_INSECURE_DEV", "false")
    monkeypatch.setenv("INTERROGATE_FORWARD_RETRIES", "1")
    get_settings.cache_clear()
    telemetry.reset()
    yield
    telemetry.reset()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_forward_rejects_disallowed_target():
    result = _allow_result(
        forward_targets=["https://not-allowed.example::receiptgate.search_receipts"],
        payload={"arguments": {"root_task_id": "task-a"}},
    )
    forwarder = RequestForwarder()
    forward_results = await forwarder.forward(result)
    await forwarder.close()

    assert len(forward_results) == 1
    assert forward_results[0]["success"] is False
    assert forward_results[0]["status_code"] == 403


@pytest.mark.asyncio
async def test_forward_retries_on_server_error_then_succeeds(httpx_mock: HTTPXMock):
    target_url = "http://localhost:9100::receiptgate.search_receipts"
    endpoint = "http://localhost:9100/mcp"

    httpx_mock.add_response(url=endpoint, status_code=500)
    httpx_mock.add_response(
        url=endpoint,
        status_code=200,
        json={"jsonrpc": "2.0", "id": "forward-1", "result": {"ok": True}},
    )

    result = _allow_result(
        forward_targets=[target_url],
        payload={"arguments": {"root_task_id": "task-a"}},
    )
    forwarder = RequestForwarder()
    forward_results = await forwarder.forward(result)
    await forwarder.close()

    assert len(forward_results) == 1
    assert forward_results[0]["success"] is True
    assert forward_results[0]["response"] == {"ok": True}
    assert len(httpx_mock.get_requests()) == 2
    assert telemetry.snapshot()["forward_retry_total"] == 1
