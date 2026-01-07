"""InterroGate data models.

Based on SPEC-IG-0000 (v0).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
import ulid


class PayloadKind(str, Enum):
    """Types of payloads that can be evaluated."""
    WORK_ORDER = "work_order"
    PLAN_REQUEST = "plan_request"
    LEASE = "lease"
    TASK = "task"


class Decision(str, Enum):
    """InterroGate decision outcomes."""
    ALLOW = "allow"
    DENY = "deny"


class RejectionReason(str, Enum):
    """Enumerated rejection reason codes."""
    MISSING_FIELDS = "missing_fields"
    MISSING_CAUSALITY = "missing_causality"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEPTH_EXCEEDED = "depth_exceeded"
    REPEATS_EXCEEDED = "repeats_exceeded"
    ANCESTOR_WINDOW_VIOLATED = "ancestor_window_violated"
    INVARIANT_VIOLATION = "invariant_violation"
    CAPABILITY_DENIED = "capability_denied"
    POLICY_NOT_FOUND = "policy_not_found"


class CausalityFields(BaseModel):
    """Required causality fields for requests that can spawn/chain work."""

    root_task_id: str = Field(..., description="Root task identifier")
    parent_task_id: Optional[str] = Field(None, description="Parent task (null for root)")
    caused_by_receipt_id: Optional[str] = Field(None, description="Provenance chain link")
    spawn_depth: int = Field(..., ge=0, description="Current spawn depth")
    capability_id: str = Field(..., description="Stable capability identifier")
    recursion_budget_remaining: Optional[int] = Field(
        None, ge=0, description="Remaining recursion budget (optional in v0)"
    )


class RequestEnvelope(BaseModel):
    """Request envelope for InterroGate evaluation.

    Per spec section 3.1.
    """

    tenant_id: str = Field(..., description="Tenant identifier")
    surface_id: str = Field(..., description="Channel/boundary identifier")
    policy_profile_id: str = Field(..., description="Policy ruleset identifier")
    payload_kind: PayloadKind = Field(..., description="Type of payload")
    payload: dict[str, Any] = Field(default_factory=dict, description="Opaque payload")

    # Causality fields (required if payload can spawn work)
    causality: Optional[CausalityFields] = Field(
        None, description="Causality fields for spawn-capable payloads"
    )


class PolicyProfile(BaseModel):
    """Policy profile defining rules for a domain.

    Per spec section 4.
    """

    policy_profile_id: str = Field(..., description="Unique profile identifier")
    policy_version: str = Field(default="1.0", description="Policy version")
    policy_hash: Optional[str] = Field(None, description="Content hash for cache validation")

    # Hard limits (section 4.1)
    max_spawn_depth: int = Field(..., ge=1, description="Maximum spawn depth (required)")
    max_total_descendants: Optional[int] = Field(None, ge=1, description="Max total descendants")
    max_repeats_per_capability: Optional[int] = Field(None, ge=1, description="Max repeats per capability")
    max_repeats_in_ancestor_window: Optional[int] = Field(
        None, ge=1, description="Max capability repeats in ancestor window"
    )
    ancestor_window_size: Optional[int] = Field(
        None, ge=1, description="Size of ancestor window to check"
    )

    # Optional invariant checks (section 4.3)
    capability_allowlist: Optional[list[str]] = Field(None, description="Allowed capabilities")
    capability_denylist: Optional[list[str]] = Field(None, description="Denied capabilities")
    allowed_targets: Optional[list[str]] = Field(None, description="Allowed forwarding targets")
    can_spawn: bool = Field(default=True, description="Whether spawning is permitted")
    max_artifact_size_bytes: Optional[int] = Field(None, description="Max artifact size if declared")

    # Forwarding configuration (section 8)
    forward_targets: list[str] = Field(default_factory=list, description="Static forward targets")

    # TTL for caching
    cache_ttl_seconds: int = Field(default=300, description="Cache TTL in seconds")


class LineageStats(BaseModel):
    """Lineage statistics from MemoryGate.

    Per spec section 5 and 6.
    """

    tenant_id: str
    root_task_id: str
    current_depth: int = Field(default=0, description="Current spawn depth")
    total_descendants: int = Field(default=0, description="Total descendant count")
    capability_repeat_count: int = Field(default=0, description="Times this capability appeared")
    ancestor_capability_ids: list[str] = Field(
        default_factory=list, description="Capability IDs in ancestor chain"
    )


class ObservedCounters(BaseModel):
    """Counters observed during decision."""

    depth: int
    repeats: int
    budget_remaining: Optional[int]
    total_descendants: Optional[int]


class AdmissionReceipt(BaseModel):
    """Receipt emitted by InterroGate per decision.

    Per spec section 7.
    """

    receipt_id: str = Field(
        default_factory=lambda: str(ulid.new()),
        description="Receipt ULID"
    )
    phase: str = Field(..., description="'accepted' or 'rejected'")
    decision: Decision = Field(..., description="ALLOW or DENY")

    # Domain context
    tenant_id: str
    surface_id: str
    policy_profile_id: str
    policy_version: str
    policy_hash: Optional[str] = None

    # Causality (if present in request)
    root_task_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    caused_by_receipt_id: Optional[str] = None
    capability_id: Optional[str] = None

    # Observed counters
    observed_counters: Optional[ObservedCounters] = None

    # Rejection info (if denied)
    rejection_reason_code: Optional[RejectionReason] = None
    rejection_detail: Optional[str] = Field(None, max_length=500)

    # Timestamps
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)


class EvaluationResult(BaseModel):
    """Result of an InterroGate evaluation."""

    decision: Decision
    receipt: AdmissionReceipt
    forward_targets: list[str] = Field(default_factory=list)
    decremented_budget: Optional[int] = None

    # Original payload with any normalization applied
    forwarded_payload: Optional[dict[str, Any]] = None


class ForwardRequest(BaseModel):
    """Request to be forwarded to downstream target."""

    target_url: str
    payload: dict[str, Any]
    admission_receipt_id: str
    headers: dict[str, str] = Field(default_factory=dict)
