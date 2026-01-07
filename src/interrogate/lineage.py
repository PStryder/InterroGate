"""Lineage statistics from MemoryGate.

Handles querying MemoryGate for lineage information needed for admission decisions.
"""

import logging
from typing import Optional

import httpx

from .config import get_settings
from .models import LineageStats

logger = logging.getLogger(__name__)


class LineageClient:
    """Client for querying lineage statistics from MemoryGate."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._settings = get_settings()
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

    async def get_lineage_stats(
        self,
        tenant_id: str,
        root_task_id: str,
        capability_id: Optional[str] = None,
    ) -> LineageStats:
        """Query MemoryGate for lineage statistics.

        Args:
            tenant_id: Tenant identifier
            root_task_id: Root task identifier
            capability_id: Optional capability to count repeats for

        Returns:
            LineageStats with counts and depths
        """
        if not self._settings.memorygate_url:
            logger.warning("MemoryGate URL not configured, returning empty stats")
            return LineageStats(
                tenant_id=tenant_id,
                root_task_id=root_task_id,
            )

        try:
            client = await self._get_client()

            # Query receipt chain for this root_task_id
            url = f"{self._settings.memorygate_url}/v1/receipts/chain/{root_task_id}"
            response = await client.get(
                url,
                params={"tenant_id": tenant_id},
                headers={"X-Tenant-ID": tenant_id},
            )

            if response.status_code == 200:
                data = response.json()
                return self._parse_lineage_stats(
                    tenant_id, root_task_id, capability_id, data
                )
            elif response.status_code == 404:
                # No chain exists yet (root task)
                return LineageStats(
                    tenant_id=tenant_id,
                    root_task_id=root_task_id,
                )
            else:
                logger.error(f"MemoryGate error: {response.status_code}")
                return LineageStats(
                    tenant_id=tenant_id,
                    root_task_id=root_task_id,
                )

        except httpx.RequestError as e:
            logger.error(f"MemoryGate request failed: {e}")
            return LineageStats(
                tenant_id=tenant_id,
                root_task_id=root_task_id,
            )

    def _parse_lineage_stats(
        self,
        tenant_id: str,
        root_task_id: str,
        capability_id: Optional[str],
        data: dict,
    ) -> LineageStats:
        """Parse lineage stats from MemoryGate response."""
        chain = data.get("chain", [])

        # Calculate depth as length of chain
        current_depth = len(chain)

        # Count total descendants (all receipts in chain minus root)
        total_descendants = max(0, current_depth - 1)

        # Extract capability IDs from ancestor chain
        ancestor_capability_ids = []
        capability_repeat_count = 0

        for receipt in chain:
            # Look for capability_id in receipt metadata or dedicated field
            cap_id = receipt.get("capability_id") or receipt.get(
                "metadata", {}
            ).get("capability_id")
            if cap_id:
                ancestor_capability_ids.append(cap_id)
                if capability_id and cap_id == capability_id:
                    capability_repeat_count += 1

        return LineageStats(
            tenant_id=tenant_id,
            root_task_id=root_task_id,
            current_depth=current_depth,
            total_descendants=total_descendants,
            capability_repeat_count=capability_repeat_count,
            ancestor_capability_ids=ancestor_capability_ids,
        )
