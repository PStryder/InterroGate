"""Tests for receipting admission evaluations.

The modelling claim under test: an admission decision is not a new receipt
phase, it is an ordinary bounded obligation. InterroGate accepts responsibility
for "determine whether request X is admissible under policy Y" and completes
that obligation with ALLOW or DENY in the body.

Most of these guard semantics rather than plumbing, because the semantics are
what make the ledger answer "who owes what?" instead of "what happened?".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from interrogate.legivellum_receipts import (
    ReceiptGateEmitter,
    build_admission_receipts,
)
from interrogate.models import (
    AdmissionReceipt,
    CausalityFields,
    Decision,
    PayloadKind,
    RejectionReason,
    RequestEnvelope,
)


def _envelope(**overrides) -> RequestEnvelope:
    base = dict(
        tenant_id="default",
        surface_id="public-api",
        policy_profile_id="default-policy",
        payload_kind=PayloadKind.TASK,
        payload={"intent": "do a thing"},
        causality=CausalityFields(
            root_task_id="root-1",
            parent_task_id="parent-1",
            caused_by_receipt_id="upstream-receipt-1",
            spawn_depth=1,
            capability_id="cap.demo",
        ),
    )
    base.update(overrides)
    return RequestEnvelope(**base)


def _admission(decision: Decision = Decision.ALLOW, **overrides) -> AdmissionReceipt:
    base = dict(
        phase="accepted" if decision is Decision.ALLOW else "rejected",
        decision=decision,
        tenant_id="default",
        surface_id="public-api",
        policy_profile_id="default-policy",
        policy_version="1",
        policy_hash="abc123",
        evaluated_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return AdmissionReceipt(**base)


class TestObligationShape:
    def test_evaluation_is_an_accepted_complete_pair(self) -> None:
        accepted, complete = build_admission_receipts(_envelope(), _admission())
        assert accepted["phase"] == "accepted"
        assert complete["phase"] == "complete"

    def test_both_receipts_describe_one_obligation(self) -> None:
        accepted, complete = build_admission_receipts(_envelope(), _admission())
        assert accepted["task_id"] == complete["task_id"]
        assert accepted["task_type"] == "admission.evaluate"

    def test_complete_is_caused_by_accepted(self) -> None:
        """The pair must form a chain, not two unrelated receipts."""
        accepted, complete = build_admission_receipts(_envelope(), _admission())
        assert complete["caused_by_receipt_id"] == accepted["receipt_id"]

    def test_accepted_links_to_the_request_that_prompted_it(self) -> None:
        accepted, _ = build_admission_receipts(_envelope(), _admission())
        assert accepted["caused_by_receipt_id"] == "upstream-receipt-1"

    def test_accepted_opens_the_obligation_without_a_result(self) -> None:
        accepted, _ = build_admission_receipts(_envelope(), _admission())
        assert accepted["status"] == "NA"
        assert accepted["outcome_kind"] == "NA"
        assert accepted["completed_at"] is None
        assert accepted["body"] == {}


class TestDecisionSemantics:
    def test_deny_is_a_successful_completion(self) -> None:
        """A DENY means the evaluation worked and the answer was no.

        status=failure would claim the evaluation itself broke, and would show
        up as a problem in anything counting failed obligations.
        """
        _, complete = build_admission_receipts(
            _envelope(),
            _admission(
                Decision.DENY,
                rejection_reason_code=RejectionReason.DEPTH_EXCEEDED,
                rejection_detail="spawn depth 6 exceeds limit 5",
            ),
        )
        assert complete["status"] == "success"
        assert complete["body"]["result"]["decision"] == "DENY"

    def test_allow_is_a_successful_completion(self) -> None:
        _, complete = build_admission_receipts(_envelope(), _admission(Decision.ALLOW))
        assert complete["status"] == "success"
        assert complete["body"]["result"]["decision"] == "ALLOW"

    def test_deny_records_why(self) -> None:
        """The reason is the entire value of a DENY receipt."""
        _, complete = build_admission_receipts(
            _envelope(),
            _admission(
                Decision.DENY,
                rejection_reason_code=RejectionReason.BUDGET_EXHAUSTED,
                rejection_detail="recursion budget exhausted",
            ),
        )
        result = complete["body"]["result"]
        assert result["rejection_reason_code"] == "budget_exhausted"
        assert result["rejection_detail"] == "recursion budget exhausted"

    def test_allow_does_not_carry_rejection_fields(self) -> None:
        _, complete = build_admission_receipts(_envelope(), _admission(Decision.ALLOW))
        assert "rejection_reason_code" not in complete["body"]["result"]

    def test_policy_identity_is_recorded(self) -> None:
        """A decision is only auditable if you know which policy produced it."""
        _, complete = build_admission_receipts(_envelope(), _admission())
        result = complete["body"]["result"]
        assert result["policy_profile_id"] == "default-policy"
        assert result["policy_version"] == "1"

    def test_evaluated_request_is_identified(self) -> None:
        _, complete = build_admission_receipts(_envelope(), _admission())
        evaluated = complete["body"]["result"]["evaluated_request"]
        assert evaluated["root_task_id"] == "root-1"
        assert evaluated["capability_id"] == "cap.demo"


class TestAuthorityBoundaries:
    def test_admission_never_escalates(self) -> None:
        """An admission gate answers; it does not transfer responsibility."""
        for decision in (Decision.ALLOW, Decision.DENY):
            _, complete = build_admission_receipts(_envelope(), _admission(decision))
            assert complete["phase"] != "escalate"
            assert complete["escalation_class"] == "NA"
            assert complete["escalation_to"] == "NA"

    def test_allow_does_not_mint_downstream_work(self) -> None:
        """ALLOW completes an admission check; it does not create the work.

        Whoever holds authority mints the downstream obligation, linking back
        via caused_by_receipt_id. Only two receipts may come from here.
        """
        receipts = build_admission_receipts(_envelope(), _admission(Decision.ALLOW))
        assert len(receipts) == 2
        assert {r["task_type"] for r in receipts} == {"admission.evaluate"}

    def test_interrogate_owns_the_admission_obligation(self) -> None:
        accepted, complete = build_admission_receipts(_envelope(), _admission())
        for receipt in (accepted, complete):
            assert receipt["recipient_ai"] == "svc:interrogate"
            assert receipt["source_system"] == "interrogate"

    def test_admission_is_a_sibling_of_the_work_it_gates(self) -> None:
        """InterroGate does not own the admitted work, so it is not its parent."""
        accepted, _ = build_admission_receipts(_envelope(), _admission())
        assert accepted["parent_task_id"] == "parent-1"


class TestChainTermination:
    def test_denied_chain_leaves_no_open_obligation(self) -> None:
        """A DENY terminates: complete closes what accepted opened."""
        accepted, complete = build_admission_receipts(_envelope(), _admission(Decision.DENY))
        assert accepted["phase"] == "accepted"
        assert complete["phase"] == "complete"
        assert complete["completed_at"] is not None

    def test_receipts_are_json_serializable(self) -> None:
        """They cross an MCP boundary, so datetimes must already be encoded."""
        for receipt in build_admission_receipts(_envelope(), _admission()):
            json.dumps(receipt)


class TestEmissionIsBestEffort:
    def test_disabled_without_an_endpoint(self) -> None:
        assert ReceiptGateEmitter(None).enabled is False

    @pytest.mark.asyncio
    async def test_emit_without_endpoint_is_a_noop(self) -> None:
        emitter = ReceiptGateEmitter(None)
        assert await emitter.emit(_envelope(), _admission()) is False

    @pytest.mark.asyncio
    async def test_unreachable_ledger_does_not_raise(self) -> None:
        """Admission is on the request path.

        A slow or missing ledger must not become a gate that refuses traffic.
        """
        emitter = ReceiptGateEmitter("http://127.0.0.1:9", timeout_seconds=0.25)
        assert await emitter.emit(_envelope(), _admission()) is False


def test_envelope_without_causality_still_receipts() -> None:
    """Not every admissible request carries provenance."""
    accepted, complete = build_admission_receipts(
        _envelope(causality=None), _admission()
    )
    assert accepted["caused_by_receipt_id"] == "NA"
    assert accepted["parent_task_id"] == "NA"
    assert "evaluated_request" not in complete["body"]["result"]


# --- canonical schema conformance -------------------------------------------
#
# Admission receipts enter the same ledger as every other receipt, so they must
# satisfy docs/canonical/receipt.schema.v1.json. Resolved from a sibling
# LegiVellum checkout, as AsyncGate's contract tests do; skipped rather than
# silently passing when it is not present.

import os
from pathlib import Path


def _canonical_schema():
    override = os.environ.get("LEGIVELLUM_RECEIPT_SCHEMA")
    candidate = (
        Path(override)
        if override
        else Path(__file__).resolve().parents[2]
        / "LegiVellum" / "docs" / "canonical" / "receipt.schema.v1.json"
    )
    if not candidate.is_file():
        pytest.skip(
            "Canonical receipt schema not found. Set LEGIVELLUM_RECEIPT_SCHEMA or "
            "check out LegiVellum as a sibling directory."
        )
    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema.Draft202012Validator(json.loads(candidate.read_text(encoding="utf-8")))


@pytest.mark.parametrize("decision", [Decision.ALLOW, Decision.DENY])
def test_admission_receipts_match_the_canonical_schema(decision: Decision) -> None:
    validator = _canonical_schema()
    extra = (
        {"rejection_reason_code": RejectionReason.DEPTH_EXCEEDED, "rejection_detail": "too deep"}
        if decision is Decision.DENY
        else {}
    )
    for receipt in build_admission_receipts(_envelope(), _admission(decision, **extra)):
        errors = sorted(validator.iter_errors(receipt), key=lambda e: str(e.path))
        assert not errors, (
            f"{decision.value} {receipt['phase']} receipt is invalid:\n  "
            + "\n  ".join(f"{list(e.path) or '<root>'}: {e.message}" for e in errors)
        )


def test_admission_receipts_carry_every_required_field() -> None:
    """Guards against a required field being added to the schema and never emitted."""
    validator = _canonical_schema()
    required = validator.schema.get("required", [])
    for receipt in build_admission_receipts(_envelope(), _admission()):
        missing = [field for field in required if field not in receipt]
        assert not missing, f"{receipt['phase']} receipt omits: {missing}"
