"""InterroGate admission evaluator.

Implements the decision algorithm per SPEC-IG-0000 section 6.
"""

import logging
from typing import Optional

from .lineage import LineageClient
from .models import (
    AdmissionReceipt,
    Decision,
    EvaluationResult,
    LineageStats,
    ObservedCounters,
    PolicyProfile,
    RejectionReason,
    RequestEnvelope,
)
from .policy import PolicyManager

logger = logging.getLogger(__name__)


class EvaluationError(Exception):
    """Error during evaluation."""

    def __init__(self, message: str, reason: RejectionReason):
        super().__init__(message)
        self.reason = reason


class AdmissionEvaluator:
    """Evaluates admission requests per policy rules.

    Decision algorithm (section 6):
    1. Validate envelope fields
    2. Validate required causality fields (if applicable)
    3. Load policy profile
    4. Query MemoryGate for lineage stats
    5. Evaluate rules in order:
       - missing fields -> DENY
       - budget exhausted -> DENY
       - depth exceeded -> DENY
       - repeats exceeded -> DENY
       - ancestor window violated -> DENY
       - invariant violations -> DENY
    6. If all pass -> ALLOW

    InterroGate MUST be deterministic.
    """

    def __init__(
        self,
        policy_manager: PolicyManager,
        lineage_client: LineageClient,
    ):
        self._policy_manager = policy_manager
        self._lineage_client = lineage_client

    async def evaluate(self, envelope: RequestEnvelope) -> EvaluationResult:
        """Evaluate an admission request.

        Args:
            envelope: Request envelope to evaluate

        Returns:
            EvaluationResult with decision and receipt
        """
        # Step 1: Validate envelope (already done by Pydantic)

        # Step 2: Get policy
        policy = await self._policy_manager.get_policy(
            tenant_id=envelope.tenant_id,
            surface_id=envelope.surface_id,
            policy_profile_id=envelope.policy_profile_id,
        )

        if not policy:
            return self._deny(
                envelope=envelope,
                policy=None,
                reason=RejectionReason.POLICY_NOT_FOUND,
                detail=f"Policy profile not found: {envelope.policy_profile_id}",
            )

        # Step 3: Get lineage stats if we have causality
        lineage: Optional[LineageStats] = None
        if envelope.causality:
            lineage = await self._lineage_client.get_lineage_stats(
                tenant_id=envelope.tenant_id,
                root_task_id=envelope.causality.root_task_id,
                capability_id=envelope.causality.capability_id,
            )

        # Step 4: Evaluate rules in order
        try:
            self._check_causality_required(envelope, policy)
            self._check_budget(envelope, policy)
            self._check_depth(envelope, policy, lineage)
            self._check_repeats(envelope, policy, lineage)
            self._check_ancestor_window(envelope, policy, lineage)
            self._check_invariants(envelope, policy)

        except EvaluationError as e:
            return self._deny(
                envelope=envelope,
                policy=policy,
                lineage=lineage,
                reason=e.reason,
                detail=str(e),
            )

        # All checks passed -> ALLOW
        return self._allow(envelope, policy, lineage)

    def _check_causality_required(
        self,
        envelope: RequestEnvelope,
        policy: PolicyProfile,
    ) -> None:
        """Check if causality fields are required and present."""
        # For spawn-capable payloads, causality is required
        spawn_capable = envelope.payload_kind.value in ("work_order", "plan_request", "task")

        if spawn_capable and not envelope.causality:
            raise EvaluationError(
                "Causality fields required for spawn-capable payload",
                RejectionReason.MISSING_CAUSALITY,
            )

    def _check_budget(
        self,
        envelope: RequestEnvelope,
        policy: PolicyProfile,
    ) -> None:
        """Check recursion budget if present."""
        if envelope.causality and envelope.causality.recursion_budget_remaining is not None:
            if envelope.causality.recursion_budget_remaining <= 0:
                raise EvaluationError(
                    f"Recursion budget exhausted (remaining: {envelope.causality.recursion_budget_remaining})",
                    RejectionReason.BUDGET_EXHAUSTED,
                )

    def _check_depth(
        self,
        envelope: RequestEnvelope,
        policy: PolicyProfile,
        lineage: Optional[LineageStats],
    ) -> None:
        """Check spawn depth against policy limit."""
        if not envelope.causality:
            return

        current_depth = envelope.causality.spawn_depth
        if lineage:
            # Use lineage depth if available (more accurate)
            current_depth = max(current_depth, lineage.current_depth)

        if current_depth >= policy.max_spawn_depth:
            raise EvaluationError(
                f"Spawn depth {current_depth} exceeds max {policy.max_spawn_depth}",
                RejectionReason.DEPTH_EXCEEDED,
            )

    def _check_repeats(
        self,
        envelope: RequestEnvelope,
        policy: PolicyProfile,
        lineage: Optional[LineageStats],
    ) -> None:
        """Check capability repeat count against policy limit."""
        if not policy.max_repeats_per_capability:
            return

        if not envelope.causality or not lineage:
            return

        if lineage.capability_repeat_count >= policy.max_repeats_per_capability:
            raise EvaluationError(
                f"Capability {envelope.causality.capability_id} repeated "
                f"{lineage.capability_repeat_count} times, max is {policy.max_repeats_per_capability}",
                RejectionReason.REPEATS_EXCEEDED,
            )

    def _check_ancestor_window(
        self,
        envelope: RequestEnvelope,
        policy: PolicyProfile,
        lineage: Optional[LineageStats],
    ) -> None:
        """Check capability repeats in ancestor window."""
        if not policy.max_repeats_in_ancestor_window or not policy.ancestor_window_size:
            return

        if not envelope.causality or not lineage:
            return

        # Check last N ancestors
        window = lineage.ancestor_capability_ids[-policy.ancestor_window_size:]
        cap_id = envelope.causality.capability_id
        repeats_in_window = window.count(cap_id)

        if repeats_in_window >= policy.max_repeats_in_ancestor_window:
            raise EvaluationError(
                f"Capability {cap_id} appears {repeats_in_window} times in "
                f"last {policy.ancestor_window_size} ancestors, max is {policy.max_repeats_in_ancestor_window}",
                RejectionReason.ANCESTOR_WINDOW_VIOLATED,
            )

    def _check_invariants(
        self,
        envelope: RequestEnvelope,
        policy: PolicyProfile,
    ) -> None:
        """Check additional invariant constraints."""
        if not envelope.causality:
            return

        capability = envelope.causality.capability_id

        # Check capability denylist
        if policy.capability_denylist and capability in policy.capability_denylist:
            raise EvaluationError(
                f"Capability {capability} is denied by policy",
                RejectionReason.CAPABILITY_DENIED,
            )

        # Check capability allowlist (if present, capability must be in it)
        if policy.capability_allowlist and capability not in policy.capability_allowlist:
            raise EvaluationError(
                f"Capability {capability} not in policy allowlist",
                RejectionReason.CAPABILITY_DENIED,
            )

        # Check spawn permission
        if not policy.can_spawn and envelope.payload_kind.value in ("work_order", "task"):
            raise EvaluationError(
                "Spawning not permitted by policy",
                RejectionReason.INVARIANT_VIOLATION,
            )

    def _allow(
        self,
        envelope: RequestEnvelope,
        policy: PolicyProfile,
        lineage: Optional[LineageStats],
    ) -> EvaluationResult:
        """Create ALLOW result."""
        # Calculate decremented budget
        decremented_budget = None
        if envelope.causality and envelope.causality.recursion_budget_remaining is not None:
            decremented_budget = envelope.causality.recursion_budget_remaining - 1

        # Build observed counters
        observed = None
        if envelope.causality:
            observed = ObservedCounters(
                depth=envelope.causality.spawn_depth,
                repeats=lineage.capability_repeat_count if lineage else 0,
                budget_remaining=decremented_budget,
                total_descendants=lineage.total_descendants if lineage else None,
            )

        receipt = AdmissionReceipt(
            phase="accepted",
            decision=Decision.ALLOW,
            tenant_id=envelope.tenant_id,
            surface_id=envelope.surface_id,
            policy_profile_id=envelope.policy_profile_id,
            policy_version=policy.policy_version,
            policy_hash=policy.policy_hash,
            root_task_id=envelope.causality.root_task_id if envelope.causality else None,
            parent_task_id=envelope.causality.parent_task_id if envelope.causality else None,
            caused_by_receipt_id=envelope.causality.caused_by_receipt_id if envelope.causality else None,
            capability_id=envelope.causality.capability_id if envelope.causality else None,
            observed_counters=observed,
        )

        # Prepare forwarded payload
        forwarded_payload = envelope.payload.copy()
        if decremented_budget is not None:
            forwarded_payload["recursion_budget_remaining"] = decremented_budget
        forwarded_payload["admission_receipt_id"] = receipt.receipt_id

        return EvaluationResult(
            decision=Decision.ALLOW,
            receipt=receipt,
            forward_targets=policy.forward_targets,
            decremented_budget=decremented_budget,
            forwarded_payload=forwarded_payload,
        )

    def _deny(
        self,
        envelope: RequestEnvelope,
        policy: Optional[PolicyProfile],
        lineage: Optional[LineageStats] = None,
        reason: RejectionReason = RejectionReason.MISSING_FIELDS,
        detail: str = "",
    ) -> EvaluationResult:
        """Create DENY result."""
        observed = None
        if envelope.causality and lineage:
            observed = ObservedCounters(
                depth=envelope.causality.spawn_depth,
                repeats=lineage.capability_repeat_count,
                budget_remaining=envelope.causality.recursion_budget_remaining,
                total_descendants=lineage.total_descendants,
            )

        receipt = AdmissionReceipt(
            phase="rejected",
            decision=Decision.DENY,
            tenant_id=envelope.tenant_id,
            surface_id=envelope.surface_id,
            policy_profile_id=envelope.policy_profile_id,
            policy_version=policy.policy_version if policy else "unknown",
            policy_hash=policy.policy_hash if policy else None,
            root_task_id=envelope.causality.root_task_id if envelope.causality else None,
            parent_task_id=envelope.causality.parent_task_id if envelope.causality else None,
            caused_by_receipt_id=envelope.causality.caused_by_receipt_id if envelope.causality else None,
            capability_id=envelope.causality.capability_id if envelope.causality else None,
            observed_counters=observed,
            rejection_reason_code=reason,
            rejection_detail=detail[:500],
        )

        return EvaluationResult(
            decision=Decision.DENY,
            receipt=receipt,
            forward_targets=[],  # Never forward on DENY
        )
