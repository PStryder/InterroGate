"""Receipt the admission evaluations InterroGate performs.

InterroGate decides ALLOW or DENY and, until now, left nothing in the ledger.
For a substrate whose claim is that responsibility cannot move silently, a gate
that makes gating decisions off-ledger is a hole in the global narrative --
particularly for DENY, which produces no downstream obligation and so vanishes
entirely unless recorded here.

The modelling question was which receipt phase an admission decision is, and
the answer is that it is not a new one. *The evaluation itself is the
obligation*:

    "Determine whether request X is admissible under policy Y."

That has an ordinary accepted -> complete lifecycle, so admission needs no new
verb. Concretely:

  accepted  InterroGate takes responsibility for evaluating the request.
  complete  It finished. status is success either way; the answer lives in
            body.result.decision as ALLOW or DENY.

Two consequences worth stating, because both are easy to get wrong:

A DENY is not a failure. InterroGate did exactly what it accepted -- the answer
was no. Emitting status=failure would claim the evaluation broke, and would put
a spurious problem in every dashboard that counts failures.

An ALLOW does not make InterroGate responsible for the admitted work. It
completed an admission check whose result happened to be ALLOW. The downstream
obligation is minted by whoever holds authority -- a Principal or DeleGate --
linking back with caused_by_receipt_id. InterroGate never mints it, which is
what keeps it an admission gate rather than an orchestrator.

Chain traversal falls out of this cleanly. An ALLOW chain continues into the
work obligation; a DENY chain simply terminates, with no open obligation, no
synthetic escalation, and no phantom inbox entry.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import httpx

from legivellum.ulid import new_ulid

from .models import AdmissionReceipt, Decision, RequestEnvelope

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "interrogate"
SERVICE_PRINCIPAL_ID = "svc:interrogate"
TASK_TYPE = "admission.evaluate"


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _base_receipt(
    *,
    receipt_id: str,
    task_id: str,
    obligation_id: str,
    tenant_id: str,
    recipient_ai: str,
    envelope: RequestEnvelope,
    admission: AdmissionReceipt,
    phase: str,
    status: str,
    caused_by_receipt_id: str,
    body: dict[str, Any],
    created_at: str,
    completed_at: Optional[str],
) -> dict[str, Any]:
    """Build a canonical receipt for one step of an admission obligation."""
    causality = envelope.causality
    return {
        "schema_version": "1.0",
        "tenant_id": tenant_id,
        "receipt_id": receipt_id,
        "task_id": task_id,
        # Both receipts of the pair name the same obligation: the accepted one
        # opens it, the complete one closes it. Only a receipt naming this
        # obligation_id may close it, so an unrelated completion sharing the
        # task lineage cannot discharge an admission evaluation.
        "obligation_id": obligation_id,
        # The admission obligation is a sibling of the work it gates, not its
        # parent: InterroGate does not own the admitted work.
        "parent_task_id": (causality.parent_task_id if causality and causality.parent_task_id else "NA"),
        "caused_by_receipt_id": caused_by_receipt_id,
        "dedupe_key": f"admission:{task_id}:{phase}",
        "attempt": 0,
        "from_principal": recipient_ai,
        "for_principal": recipient_ai,
        "source_system": SOURCE_SYSTEM,
        "recipient_ai": recipient_ai,
        "trust_domain": envelope.surface_id or "default",
        "phase": phase,
        "status": status,
        "realtime": True,
        "task_type": TASK_TYPE,
        "task_summary": f"Admission evaluation for {envelope.payload_kind}",
        "task_body": (
            f"Determine whether a {envelope.payload_kind} request on surface "
            f"{envelope.surface_id} is admissible under policy "
            f"{envelope.policy_profile_id}."
        ),
        "inputs": {
            "surface_id": envelope.surface_id,
            "policy_profile_id": envelope.policy_profile_id,
            "payload_kind": str(envelope.payload_kind),
        },
        "expected_outcome_kind": "response_text",
        "expected_artifact_mime": "NA",
        "outcome_kind": "response_text" if phase == "complete" else "NA",
        "outcome_text": (admission.decision.value.upper() if phase == "complete" else "NA"),
        "artifact_location": "NA",
        "artifact_pointer": "NA",
        "artifact_checksum": "NA",
        "artifact_size_bytes": 0,
        "artifact_mime": "NA",
        # An admission gate never escalates: it answers, it does not transfer
        # responsibility for the thing it was asked about.
        "escalation_class": "NA",
        "escalation_reason": "NA",
        "escalation_to": "NA",
        "retry_requested": False,
        "body": body,
        "created_at": created_at,
        "stored_at": None,
        "started_at": created_at,
        "completed_at": completed_at,
        "read_at": None,
        "archived_at": None,
        "metadata": {
            "admission_receipt_id": admission.receipt_id,
            "policy_version": admission.policy_version,
            "policy_hash": admission.policy_hash,
        },
    }


def build_admission_receipts(
    envelope: RequestEnvelope,
    admission: AdmissionReceipt,
    *,
    recipient_ai: str = SERVICE_PRINCIPAL_ID,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the accepted/complete pair for one admission evaluation.

    Returned rather than emitted so the shape can be tested without a ledger.
    """
    task_id = f"admission-{uuid4()}"
    # One obligation -- "determine whether request X is admissible" -- carried
    # by both receipts of the pair.
    obligation_id = new_ulid()
    accepted_id = str(uuid4())
    complete_id = str(uuid4())
    tenant_id = envelope.tenant_id or "default"
    evaluated_at = _iso(admission.evaluated_at) or datetime.now(timezone.utc).isoformat()

    causality = envelope.causality
    # The request that prompted the evaluation, if the caller supplied
    # provenance. This is what makes the admission decision reachable from the
    # chain that produced the request.
    upstream_cause = (
        causality.caused_by_receipt_id
        if causality and causality.caused_by_receipt_id
        else "NA"
    )

    accepted = _base_receipt(
        receipt_id=accepted_id,
        task_id=task_id,
        obligation_id=obligation_id,
        tenant_id=tenant_id,
        recipient_ai=recipient_ai,
        envelope=envelope,
        admission=admission,
        phase="accepted",
        status="NA",
        caused_by_receipt_id=upstream_cause,
        body={},
        created_at=evaluated_at,
        completed_at=None,
    )

    result: dict[str, Any] = {
        "decision": admission.decision.value.upper(),
        "policy_profile_id": admission.policy_profile_id,
        "policy_version": admission.policy_version,
        "summary": f"Admission {admission.decision.value.upper()}",
    }
    if admission.decision is Decision.DENY:
        # Why it was refused is the entire value of a DENY receipt.
        result["rejection_reason_code"] = (
            admission.rejection_reason_code.value if admission.rejection_reason_code else None
        )
        result["rejection_detail"] = admission.rejection_detail
    if causality is not None:
        result["evaluated_request"] = {
            "root_task_id": causality.root_task_id,
            "capability_id": causality.capability_id,
            "spawn_depth": causality.spawn_depth,
        }

    complete = _base_receipt(
        receipt_id=complete_id,
        task_id=task_id,
        obligation_id=obligation_id,
        tenant_id=tenant_id,
        recipient_ai=recipient_ai,
        envelope=envelope,
        admission=admission,
        phase="complete",
        # A DENY is a successfully completed evaluation whose answer was no.
        # status=failure would claim the evaluation itself broke.
        status="success",
        caused_by_receipt_id=accepted_id,
        body={"result": result},
        created_at=evaluated_at,
        completed_at=evaluated_at,
    )
    return accepted, complete


class ReceiptGateEmitter:
    """Best-effort emission of admission receipts to ReceiptGate.

    Admission decisions are made on the request path. A ledger that is slow or
    unreachable must not turn into a gate that refuses traffic, so emission
    failures are logged and swallowed -- the same reasoning that keeps MetaGate
    bootstrap non-blocking.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        *,
        api_key: Optional[str] = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self._endpoint)

    async def _submit(self, client: httpx.AsyncClient, receipt: dict[str, Any]) -> None:
        endpoint = self._endpoint
        if not endpoint:
            raise RuntimeError("ReceiptGate endpoint is not configured")
        url = endpoint if endpoint.endswith("/mcp") else f"{endpoint.rstrip('/')}/mcp"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        response = await client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "receiptgate.submit_receipt",
                    "arguments": {"receipt": receipt},
                },
            },
            headers=headers,
        )
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise RuntimeError(f"receiptgate.submit_receipt: {body['error']}")

    async def emit(self, envelope: RequestEnvelope, admission: AdmissionReceipt) -> bool:
        """Emit the accepted/complete pair. Returns True if both landed."""
        if not self.enabled:
            return False

        accepted, complete = build_admission_receipts(envelope, admission)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # Order matters: complete references accepted as its cause, so
                # a ledger that enforces causality must see accepted first.
                await self._submit(client, accepted)
                await self._submit(client, complete)
        except Exception as exc:  # noqa: BLE001 - never block an admission decision
            logger.warning(
                "admission_receipt_emit_failed decision=%s policy=%s error=%s",
                admission.decision.value,
                admission.policy_profile_id,
                exc,
            )
            return False

        logger.info(
            "admission_receipted decision=%s task_id=%s policy=%s",
            admission.decision.value,
            accepted["task_id"],
            admission.policy_profile_id,
        )
        return True


def emitter_from_settings(settings: Any) -> ReceiptGateEmitter:
    """Build an emitter from configuration."""
    endpoint = getattr(settings, "receiptgate_url", None)
    api_key = getattr(settings, "receiptgate_api_key", None) or os.environ.get(
        "INTERROGATE_RECEIPTGATE_API_KEY"
    )
    return ReceiptGateEmitter(endpoint, api_key=api_key)
