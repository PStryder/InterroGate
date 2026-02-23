"""Policy fallback behavior tests for InterroGate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow running tests directly from repo without editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interrogate.config import get_settings
from interrogate.policy import PolicyManager


@pytest.mark.asyncio
async def test_policy_manager_uses_default_when_metagate_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTERROGATE_API_KEY", "ig_test_policy")
    monkeypatch.setenv("INTERROGATE_ALLOW_INSECURE_DEV", "false")
    monkeypatch.delenv("INTERROGATE_METAGATE_URL", raising=False)
    get_settings.cache_clear()

    manager = PolicyManager()
    policy = await manager.get_policy(
        tenant_id="tenant-a",
        surface_id="surface-a",
        policy_profile_id="profile-a",
    )
    await manager.close()

    assert policy is not None
    assert policy.policy_profile_id == "profile-a"
    assert policy.policy_version == "default"
    assert policy.max_spawn_depth == get_settings().default_max_spawn_depth
    assert policy.max_repeats_per_capability == get_settings().default_max_repeats_per_capability

    get_settings.cache_clear()
