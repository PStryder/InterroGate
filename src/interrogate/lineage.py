"""Lineage statistics from ReceiptGate.

Handles querying ReceiptGate for lineage information needed for admission decisions.
"""

import logging
from typing import Optional

import httpx

from .config import get_settings
from .models import LineageStats

logger = logging.getLogger(__name__)


class LineageClient:
    """Client for querying lineage statistics from ReceiptGate."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._settings = get_settings()
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

    async def get_lineage_stats(
        self,
        tenant_id: str,
        root_task_id: str,
        capability_id: Optional[str] = None,
    ) -> LineageStats:
        """Query ReceiptGate for lineage statistics."""
        endpoint = self._settings.receiptgate_url or self._settings.memorygate_url
        if not endpoint:
            logger.warning("ReceiptGate endpoint not configured, returning empty stats")
            return LineageStats(tenant_id=tenant_id, root_task_id=root_task_id)

        try:
            client = await self._get_client()
            normalized = self._normalize_mcp_endpoint(endpoint)
            headers = {}
            if self._settings.receiptgate_api_key:
                headers["Authorization"] = f"Bearer {self._settings.receiptgate_api_key}"

            payload = {
                "jsonrpc": "2.0",
                "id": "lineage",
                "method": "tools/call",
                "params": {
                    "name": "receiptgate.list_task_receipts",
                    "arguments": {
                        "task_id": root_task_id,
                        "sort": "asc",
                        "include_payload": True,
                    },
                },
            }
            response = await client.post(normalized, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                logger.error(f"ReceiptGate error: {data['error']}")
                return LineageStats(tenant_id=tenant_id, root_task_id=root_task_id)

            receipts = (data.get("result") or {}).get("receipts", [])
            return self._parse_lineage_from_receipts(
                tenant_id, root_task_id, capability_id, receipts
            )

        except httpx.RequestError as e:
            logger.error(f"ReceiptGate request failed: {e}")
            return LineageStats(tenant_id=tenant_id, root_task_id=root_task_id)

    def _parse_lineage_from_receipts(
        self,
        tenant_id: str,
        root_task_id: str,
        capability_id: Optional[str],
        receipts: list[dict],
    ) -> LineageStats:
        """Parse lineage stats from receipt payloads."""
        current_depth = len(receipts)
        total_descendants = max(0, current_depth - 1)

        ancestor_capability_ids: list[str] = []
        capability_repeat_count = 0

        for receipt in receipts:
            payload = receipt.get("payload") or {}
            cap_id = payload.get("capability_id") or payload.get("metadata", {}).get("capability_id")
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

    @staticmethod
    def _normalize_mcp_endpoint(endpoint: str) -> str:
        normalized = endpoint.rstrip("/")
        if not normalized.endswith("/mcp"):
            normalized = f"{normalized}/mcp"
        return normalized
