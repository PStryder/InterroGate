"""Minimal in-process telemetry counters for InterroGate."""

from __future__ import annotations

import logging
from collections import Counter
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


class Telemetry:
    """Thread-safe counters for request/error/retry observability."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._request_total = 0
        self._request_method_counts: Counter[str] = Counter()
        self._deny_reason_counts: Counter[str] = Counter()
        self._tools_call_error_code_counts: Counter[str] = Counter()
        self._forward_retry_total = 0

    def reset(self) -> None:
        with self._lock:
            self._request_total = 0
            self._request_method_counts.clear()
            self._deny_reason_counts.clear()
            self._tools_call_error_code_counts.clear()
            self._forward_retry_total = 0

    def record_request(self, method: str) -> None:
        with self._lock:
            self._request_total += 1
            self._request_method_counts[method] += 1

    def record_deny_reason(self, reason_code: str) -> None:
        with self._lock:
            self._deny_reason_counts[reason_code] += 1
            count = self._deny_reason_counts[reason_code]
        logger.info(
            "interrogate_metric event=deny_reason code=%s count=%s",
            reason_code,
            count,
        )

    def record_error_code(self, code: str) -> None:
        with self._lock:
            self._tools_call_error_code_counts[code] += 1
            count = self._tools_call_error_code_counts[code]
        logger.info(
            "interrogate_metric event=tools_call_error code=%s count=%s",
            code,
            count,
        )

    def record_forward_retry(self, target: str, attempt: int, reason: str) -> None:
        with self._lock:
            self._forward_retry_total += 1
            total = self._forward_retry_total
        logger.warning(
            "interrogate_metric event=forward_retry target=%s attempt=%s reason=%s total=%s",
            target,
            attempt,
            reason,
            total,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "request_total": self._request_total,
                "request_method_counts": dict(self._request_method_counts),
                "deny_reason_counts": dict(self._deny_reason_counts),
                "tools_call_error_code_counts": dict(self._tools_call_error_code_counts),
                "forward_retry_total": self._forward_retry_total,
            }


telemetry = Telemetry()
