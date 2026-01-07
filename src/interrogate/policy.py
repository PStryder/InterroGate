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
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0)
            )
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
        """Fetch policy from MetaGate."""
        if not self._settings.metagate_url:
            return None

        try:
            client = await self._get_client()
            url = f"{self._settings.metagate_url}/v1/admin/profiles/{policy_profile_id}"
            response = await client.get(
                url,
                params={"tenant_id": tenant_id, "surface_id": surface_id},
                headers={"X-Tenant-ID": tenant_id},
            )

            if response.status_code == 200:
                data = response.json()
                return PolicyProfile(**data)
            elif response.status_code == 404:
                logger.warning(f"Policy not found in MetaGate: {policy_profile_id}")
                return None
            else:
                logger.error(f"MetaGate error: {response.status_code}")
                return None

        except httpx.RequestError as e:
            logger.error(f"MetaGate request failed: {e}")
            return None

    def _get_default_policy(self, policy_profile_id: str) -> PolicyProfile:
        """Return default fallback policy."""
        return PolicyProfile(
            policy_profile_id=policy_profile_id,
            policy_version="default",
            max_spawn_depth=self._settings.default_max_spawn_depth,
            max_repeats_per_capability=self._settings.default_max_repeats_per_capability,
            forward_targets=[],
        )

    def invalidate(self, policy_profile_id: str) -> None:
        """Invalidate cached policy."""
        self._cache.invalidate(policy_profile_id)

    def clear_cache(self) -> None:
        """Clear all cached policies."""
        self._cache.clear()
