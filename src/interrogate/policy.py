"""Policy management for InterroGate.

Handles policy profile loading from MetaGate with caching.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from .config import get_settings
from .models import PolicyProfile

logger = logging.getLogger(__name__)


class PolicyCache:
    """In-memory policy cache with TTL."""

    def __init__(self, max_size: int = 100):
        self._cache: dict[str, tuple[PolicyProfile, datetime]] = {}
        self._max_size = max_size

    def get(self, policy_profile_id: str) -> Optional[PolicyProfile]:
        """Get policy from cache if not expired."""
        if policy_profile_id not in self._cache:
            return None

        policy, cached_at = self._cache[policy_profile_id]
        ttl = timedelta(seconds=policy.cache_ttl_seconds)

        if datetime.utcnow() - cached_at > ttl:
            del self._cache[policy_profile_id]
            return None

        return policy

    def set(self, policy: PolicyProfile) -> None:
        """Add policy to cache."""
        # Evict oldest if at capacity
        if len(self._cache) >= self._max_size:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]

        self._cache[policy.policy_profile_id] = (policy, datetime.utcnow())

    def invalidate(self, policy_profile_id: str) -> None:
        """Remove policy from cache."""
        self._cache.pop(policy_profile_id, None)

    def clear(self) -> None:
        """Clear all cached policies."""
        self._cache.clear()


class PolicyManager:
    """Manages policy retrieval from MetaGate with caching."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._settings = get_settings()
        self._cache = PolicyCache(max_size=self._settings.policy_cache_max_size)
        self._http_client = http_client
        self._owns_client = http_client is None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client if we own it."""
        if self._owns_client and self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def get_policy(
        self,
        tenant_id: str,
        surface_id: str,
        policy_profile_id: str,
    ) -> Optional[PolicyProfile]:
        """Get policy profile, from cache or MetaGate.

        Args:
            tenant_id: Tenant identifier
            surface_id: Surface/channel identifier
            policy_profile_id: Policy profile identifier

        Returns:
            PolicyProfile if found, None otherwise
        """
        # Check cache first
        cache_key = f"{tenant_id}:{surface_id}:{policy_profile_id}"
        cached = self._cache.get(cache_key)
        if cached:
            logger.debug(f"Policy cache hit: {cache_key}")
            return cached

        # Try MetaGate
        if self._settings.metagate_url:
            policy = await self._fetch_from_metagate(
                tenant_id, surface_id, policy_profile_id
            )
            if policy:
                # Store with composite key
                policy_with_key = policy.model_copy()
                self._cache._cache[cache_key] = (policy_with_key, datetime.utcnow())
                return policy

        # Return default fallback policy
        logger.warning(f"Using default policy for {cache_key}")
        return self._get_default_policy(policy_profile_id)

    async def _fetch_from_metagate(
        self,
        tenant_id: str,
        surface_id: str,
        policy_profile_id: str,
    ) -> Optional[PolicyProfile]:
        """Fetch policy from MetaGate via MCP."""
        endpoint = self._settings.metagate_url
        if not endpoint:
            return None

        try:
            client = await self._get_client()
            normalized = self._normalize_mcp_endpoint(endpoint)
            headers = {}
            if self._settings.metagate_api_key:
                headers["Authorization"] = f"Bearer {self._settings.metagate_api_key}"

            payload = {
                "jsonrpc": "2.0",
                "id": "policy-fetch",
                "method": "tools/call",
                "params": {
                    "name": "metagate.admin_profiles",
                    "arguments": {
                        "action": "get",
                        "profile_id": policy_profile_id,
                        "profile_key": policy_profile_id,
                        "tenant_key": tenant_id,
                    },
                },
            }
            response = await client.post(normalized, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                if data["error"].get("code") == "not_found":
                    logger.warning(f"Policy not found in MetaGate: {policy_profile_id}")
                    return None
                logger.error(f"MetaGate error: {data['error']}")
                return None

            result = data.get("result") or {}
            policy_payload = result.get("policy") or {}
            profile_key = result.get("profile_key") or policy_profile_id

            if "policy_profile_id" not in policy_payload:
                policy_payload["policy_profile_id"] = profile_key
            if "policy_version" not in policy_payload:
                policy_payload["policy_version"] = policy_payload.get("version", "1.0")
            if "cache_ttl_seconds" not in policy_payload:
                policy_payload["cache_ttl_seconds"] = self._settings.policy_cache_ttl_seconds
            if "max_spawn_depth" not in policy_payload:
                policy_payload["max_spawn_depth"] = self._settings.default_max_spawn_depth
            if "max_repeats_per_capability" not in policy_payload:
                policy_payload["max_repeats_per_capability"] = self._settings.default_max_repeats_per_capability

            return PolicyProfile(**policy_payload)

        except httpx.RequestError as e:
            logger.error(f"MetaGate request failed: {e}")
            return None

    @staticmethod
    def _normalize_mcp_endpoint(endpoint: str) -> str:
        normalized = endpoint.rstrip("/")
        if not normalized.endswith("/mcp"):
            normalized = f"{normalized}/mcp"
        return normalized

    def _get_default_policy(self, policy_profile_id: str) -> PolicyProfile:
        """Return default fallback policy."""
        return PolicyProfile(
            policy_profile_id=policy_profile_id,
            policy_version="default",
            policy_hash=None,
            max_spawn_depth=self._settings.default_max_spawn_depth,
            max_total_descendants=None,
            max_repeats_per_capability=self._settings.default_max_repeats_per_capability,
            max_repeats_in_ancestor_window=None,
            ancestor_window_size=None,
            capability_allowlist=None,
            capability_denylist=None,
            allowed_targets=None,
            can_spawn=True,
            max_artifact_size_bytes=None,
            forward_targets=[],
            cache_ttl_seconds=self._settings.policy_cache_ttl_seconds,
        )

    def invalidate(self, policy_profile_id: str) -> None:
        """Invalidate cached policy."""
        self._cache.invalidate(policy_profile_id)

    def clear_cache(self) -> None:
        """Clear all cached policies."""
        self._cache.clear()
