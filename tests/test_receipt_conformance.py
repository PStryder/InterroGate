"""Every receipt InterroGate emits must validate against the canonical schema.

There was no such test in five of the six emitters, which is how the stack
shipped a builder set with no `accepted` phase, a 100%-rejection artifact bug,
and receipts carrying a `task_id` that meant something different in each
component. The rejections were logged at WARNING and dropped, so nothing
failed and nothing was noticed.

This asserts the property directly: build the receipts the way production
builds them, and run the canonical validator over them.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from legivellum.validation import validate_receipt

from interrogate.legivellum_receipts import build_admission_receipts
from interrogate.models import (
    AdmissionReceipt,
    Decision,
    PayloadKind,
    RequestEnvelope,
)


def _envelope() -> RequestEnvelope:
    return RequestEnvelope(
        tenant_id="default",
        surface_id="surface-1",
        policy_profile_id="profile-1",
        payload_kind=PayloadKind.TASK,
    )


def _admission(decision: Decision) -> AdmissionReceipt:
    return AdmissionReceipt(
        phase="complete",
        decision=decision,
        tenant_id="default",
        surface_id="surface-1",
        policy_profile_id="profile-1",
        policy_version="1",
        policy_hash="deadbeef",
        evaluated_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize("decision", [Decision.ALLOW, Decision.DENY])
def test_admission_pair_validates(decision):
    accepted, complete = build_admission_receipts(_envelope(), _admission(decision))
    for name, receipt in (("accepted", accepted), ("complete", complete)):
        errors = validate_receipt(receipt)
        assert errors == [], f"{name} receipt rejected: {[e.message for e in errors]}"


@pytest.mark.parametrize("decision", [Decision.ALLOW, Decision.DENY])
def test_both_receipts_name_the_same_obligation(decision):
    """The pair is one obligation: accepted opens it, complete closes it."""
    accepted, complete = build_admission_receipts(_envelope(), _admission(decision))
    assert accepted["obligation_id"] == complete["obligation_id"]
    assert accepted["obligation_id"] not in ("", "NA", "TBD")


def test_separate_evaluations_get_separate_obligations():
    """Two admission checks are two responsibilities, not one.

    If they shared an obligation_id, completing one would discharge the other
    -- which is the fan-out defect that motivated obligation_id existing.
    """
    first, _ = build_admission_receipts(_envelope(), _admission(Decision.ALLOW))
    second, _ = build_admission_receipts(_envelope(), _admission(Decision.ALLOW))
    assert first["obligation_id"] != second["obligation_id"]


def test_deny_is_a_successful_completion():
    """A DENY is not a failure: the evaluation did what it accepted."""
    _, complete = build_admission_receipts(_envelope(), _admission(Decision.DENY))
    assert complete["status"] == "success"
    assert complete["body"]["result"]["decision"] == "DENY"
