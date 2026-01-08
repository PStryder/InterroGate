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
        try:
            url = httpx.URL(target)
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
                    target,
                    json=payload,
                    headers=headers,
                )

                if response.status_code >= 200 and response.status_code < 300:
                    return response.json() if response.content else {}

                # Non-retryable error
                if response.status_code >= 400 and response.status_code < 500:
                    raise ForwardError(
                        f"Target rejected request: {response.status_code}",
                        target=target,
                        status_code=response.status_code,
                    )

                # Server error - retry
                last_error = ForwardError(
                    f"Target server error: {response.status_code}",
                    target=target,
                    status_code=response.status_code,
                )
                logger.warning(f"Retry {attempt + 1} for {target}: {response.status_code}")

            except httpx.RequestError as e:
                last_error = ForwardError(
                    f"Network error: {e}",
                    target=target,
                )
                logger.warning(f"Retry {attempt + 1} for {target}: {e}")

        raise last_error or ForwardError("Unknown error", target=target)
