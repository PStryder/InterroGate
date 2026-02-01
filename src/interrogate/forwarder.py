"""Request forwarder for InterroGate.

Handles forwarding allowed requests to downstream targets.
"""

import logging
from typing import Any, Optional

import httpx

from .config import get_settings
from .models import EvaluationResult, ForwardRequest

logger = logging.getLogger(__name__)


class ForwardError(Exception):
    """Error during forwarding."""

    def __init__(self, message: str, target: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.target = target
        self.status_code = status_code


class RequestForwarder:
    """Forwards allowed requests to downstream targets.

    Per spec section 8:
    - On ALLOW, forward to configured targets
    - Targets are static configuration
    - Never forward on DENY
    - Only add admission_receipt_id and decrement budget
    """

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._settings = get_settings()
        self._http_client = http_client
        self._owns_client = http_client is None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.forward_timeout_seconds)
            )
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client if we own it."""
        if self._owns_client and self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _is_target_allowed(self, target: str) -> bool:
        """Validate target against allowlist."""
        endpoint = self._normalize_mcp_endpoint(target)
        try:
            url = httpx.URL(endpoint)
        except Exception:
            return False

        if url.scheme not in self._settings.forward_allowed_schemes:
            return False

        host = (url.host or "").lower()
        if not host:
            return False

        allowed_hosts = [h.lower() for h in self._settings.forward_allowed_hosts]
        if not allowed_hosts:
            return False

        for allowed in allowed_hosts:
            if allowed == "*":
                return True
            if allowed.startswith("*.") and host.endswith(allowed[1:]):
                return True
            if host == allowed:
                return True

        return False

    def _normalize_mcp_endpoint(self, target: str) -> str:
        endpoint, _tool = self._split_target(target)
        normalized = endpoint.rstrip("/")
        if not normalized.endswith("/mcp"):
            normalized = f"{normalized}/mcp"
        return normalized

    @staticmethod
    def _split_target(target: str) -> tuple[str, Optional[str]]:
        if "::" in target:
            endpoint, tool_name = target.split("::", 1)
            return endpoint, tool_name or None
        if "#" in target:
            endpoint, tool_name = target.split("#", 1)
            return endpoint, tool_name or None
        return target, None

    @staticmethod
    def _resolve_tool_call(payload: dict[str, Any], tool_hint: Optional[str]) -> tuple[str, dict[str, Any]]:
        tool_name = tool_hint or payload.get("tool") or payload.get("name")
        if not tool_name:
            raise ValueError("Missing tool name for MCP forward")

        if isinstance(payload.get("arguments"), dict):
            arguments = dict(payload.get("arguments") or {})
        elif isinstance(payload.get("args"), dict):
            arguments = dict(payload.get("args") or {})
        else:
            arguments = {k: v for k, v in payload.items() if k not in ("tool", "name")}

        admission_receipt_id = payload.get("admission_receipt_id")
        if admission_receipt_id and "admission_receipt_id" not in arguments:
            arguments["admission_receipt_id"] = admission_receipt_id

        return tool_name, arguments

    async def forward(
        self,
        result: EvaluationResult,
        original_headers: Optional[dict[str, str]] = None,
    ) -> list[dict[str, Any]]:
        """Forward allowed request to all configured targets.

        Args:
            result: Evaluation result (must be ALLOW)
            original_headers: Original request headers to pass through

        Returns:
            List of forward results (one per target)

        Raises:
            ValueError: If decision is not ALLOW
        """
        if result.decision.value != "allow":
            raise ValueError("Cannot forward DENY result")

        if not result.forward_targets:
            logger.debug("No forward targets configured")
            return []

        if not result.forwarded_payload:
            logger.warning("No payload to forward")
            return []

        results = []
        for target in result.forward_targets:
            try:
                forward_result = await self._forward_to_target(
                    target=target,
                    payload=result.forwarded_payload,
                    admission_receipt_id=result.receipt.receipt_id,
                    original_headers=original_headers,
                )
                results.append({
                    "target": target,
                    "success": True,
                    "response": forward_result,
                })
            except ForwardError as e:
                logger.error(f"Forward to {target} failed: {e}")
                results.append({
                    "target": target,
                    "success": False,
                    "error": str(e),
                    "status_code": e.status_code,
                })

        return results

    async def _forward_to_target(
        self,
        target: str,
        payload: dict[str, Any],
        admission_receipt_id: str,
        original_headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Forward request to a single target with retries.

        Args:
            target: Target URL
            payload: Payload to forward
            admission_receipt_id: ID of admission receipt
            original_headers: Headers to pass through

        Returns:
            Response data from target

        Raises:
            ForwardError: If forwarding fails after retries
        """
        client = await self._get_client()
        if not self._is_target_allowed(target):
            raise ForwardError("Target not in allowlist", target=target, status_code=403)

        endpoint, tool_hint = self._split_target(target)
        mcp_endpoint = self._normalize_mcp_endpoint(endpoint)
        try:
            tool_name, arguments = self._resolve_tool_call(payload, tool_hint)
        except ValueError as exc:
            raise ForwardError(str(exc), target=target, status_code=400)

        # Build headers
        headers = {
            "Content-Type": "application/json",
            "X-InterroGate-Receipt-ID": admission_receipt_id,
        }

        # Pass through selected original headers
        if original_headers:
            normalized_headers = {k.lower(): v for k, v in original_headers.items()}
            for key in self._settings.forward_pass_headers:
                value = normalized_headers.get(key.lower())
                if value is not None:
                    headers[key] = value

        last_error: Optional[Exception] = None
        for attempt in range(self._settings.forward_retries + 1):
            try:
                response = await client.post(
                    mcp_endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "id": f"forward-{attempt}",
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    },
                    headers=headers,
                )

                response.raise_for_status()
                data = response.json()
                if data.get("error"):
                    raise ForwardError(
                        f"MCP error: {data['error']}",
                        target=target,
                        status_code=500,
                    )
                return data.get("result", {})

            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response else None
                if status_code and 400 <= status_code < 500:
                    raise ForwardError(
                        f"Target rejected request: {status_code}",
                        target=target,
                        status_code=status_code,
                    )
                last_error = ForwardError(
                    f"Target server error: {status_code}",
                    target=target,
                    status_code=status_code,
                )
                logger.warning(f"Retry {attempt + 1} for {target}: {status_code}")

            except httpx.RequestError as e:
                last_error = ForwardError(
                    f"Network error: {e}",
                    target=target,
                )
                logger.warning(f"Retry {attempt + 1} for {target}: {e}")

        raise last_error or ForwardError("Unknown error", target=target)
