# InterroGate Code Review

**Review Date:** 2026-01-08
**Spec Reference:** SPEC-IG-0000 (v0)
**Codebase Version:** 0.1.0
**Reviewer:** Claude Code (Automated Review)

---

## Executive Summary

InterroGate is an admission control filter designed to prevent runaway recursion and enforce invariants in LegiVellum topologies. The implementation demonstrates **solid alignment with the v0 specification** and follows modern Python practices using FastAPI, Pydantic, and async patterns.

### Overall Assessment: **GOOD with Minor Issues**

**Strengths:**
- Core spec requirements (MUST) are implemented correctly
- Clean separation of concerns across modules
- Proper use of Pydantic for validation and typing
- Deterministic decision algorithm as required
- Good error handling patterns

**Concerns:**
- No test coverage (Critical gap)
- Missing dependency in pyproject.toml
- Some security considerations need attention
- Rate limiting is configured but not implemented

---

## 1. Spec Compliance Analysis

### 1.1 MUST Requirements (v0 Minimal Requirements - Section 10)

| Requirement | Status | Notes |
|-------------|--------|-------|
| Support Policy Domains (tenant_id + surface_id + policy_profile_id) | IMPLEMENTED | `RequestEnvelope` model includes all three fields |
| Enforce max_spawn_depth | IMPLEMENTED | `_check_depth()` in evaluator.py enforces this |
| Emit accept/reject receipts | IMPLEMENTED | `AdmissionReceipt` model with phase field |
| Allow configured forwarding target(s) | IMPLEMENTED | `RequestForwarder` handles this |

### 1.2 Non-Goals Compliance (Section 0)

| Non-Goal | Status | Notes |
|----------|--------|-------|
| MUST NOT schedule work | COMPLIANT | Code only evaluates and forwards |
| MUST NOT route dynamically | COMPLIANT | Forward targets are static configuration |
| MUST NOT select among executors | COMPLIANT | No executor selection logic |
| MUST NOT infer completion/progress | COMPLIANT | No progress inference |
| MUST NOT initiate new work | COMPLIANT | Only admits existing requests |
| MUST NOT mutate intent | COMPLIANT | Only adds receipt_id and decrements budget |

### 1.3 SHOULD Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Support recursion budget ("fuel") | IMPLEMENTED | `_check_budget()` + budget decrement on ALLOW |
| Support repeat caps | IMPLEMENTED | `_check_repeats()` in evaluator.py |
| Query MemoryGate for lineage stats | IMPLEMENTED | `LineageClient` class |

### 1.4 Decision Algorithm Compliance (Section 6)

The evaluation order in `evaluator.py` matches the spec:

```
1. Validate envelope fields          -> Pydantic validation
2. Validate causality fields         -> _check_causality_required()
3. Load policy profile               -> PolicyManager.get_policy()
4. Query MemoryGate for lineage      -> LineageClient.get_lineage_stats()
5. Evaluate rules in order:
   - missing fields -> DENY          -> Covered by Pydantic + causality check
   - budget exhausted -> DENY        -> _check_budget()
   - depth exceeded -> DENY          -> _check_depth()
   - repeats exceeded -> DENY        -> _check_repeats()
   - ancestor window violated -> DENY -> _check_ancestor_window()
   - invariant violations -> DENY    -> _check_invariants()
6. If all pass -> ALLOW              -> _allow()
```

**VERDICT: Fully compliant with decision algorithm.**

### 1.5 Receipt Fields Compliance (Section 7)

**Acceptance Receipt (Section 7.1):**

| Field | Status |
|-------|--------|
| phase = accepted | IMPLEMENTED |
| tenant_id | IMPLEMENTED |
| surface_id | IMPLEMENTED |
| policy_profile_id | IMPLEMENTED |
| root_task_id, parent_task_id, caused_by_receipt_id | IMPLEMENTED |
| capability_id | IMPLEMENTED |
| observed counters | IMPLEMENTED |
| policy_version / policy_hash | IMPLEMENTED |

**Rejection Receipt (Section 7.2):**

| Field | Status |
|-------|--------|
| phase = rejected | IMPLEMENTED |
| all acceptance fields | IMPLEMENTED |
| rejection_reason_code | IMPLEMENTED (enum) |
| rejection_detail | IMPLEMENTED (max 500 chars) |

### 1.6 Missing/Incomplete Features

1. **max_total_descendants check** - The field exists in `PolicyProfile` and `LineageStats` but is **NOT EVALUATED** in the decision algorithm. The spec says it's optional, but if configured, it should be enforced.

2. **Lease authority constraints** (Section 4.3) - Mentioned in spec but not implemented. `allowed_targets` field exists but no validation logic.

3. **Maximum artifact size declarations** (Section 4.3) - Field `max_artifact_size_bytes` exists but no validation logic.

---

## 2. Code Quality Assessment

### 2.1 Project Structure

```
InterroGate/
├── src/interrogate/
│   ├── __init__.py      # Version info
│   ├── models.py        # Pydantic models (187 lines)
│   ├── config.py        # Settings (46 lines)
│   ├── policy.py        # Policy management (168 lines)
│   ├── lineage.py       # MemoryGate client (136 lines)
│   ├── evaluator.py     # Core decision logic (334 lines)
│   ├── forwarder.py     # Request forwarding (179 lines)
│   ├── api.py           # FastAPI endpoints (250 lines)
│   └── main.py          # Entry point (31 lines)
├── pyproject.toml
├── README.md
└── SPEC-IG-0000 (v0).txt
```

**Assessment:** Clean modular structure with clear separation of concerns.

### 2.2 Code Patterns

**Positive Patterns:**

1. **Dependency Injection Ready** - Components accept optional HTTP clients
2. **Async Throughout** - Proper async/await usage
3. **Context Managers** - Lifespan management for cleanup
4. **Type Hints** - Comprehensive typing with Pydantic
5. **Enum Usage** - `Decision`, `RejectionReason`, `PayloadKind` prevent magic strings
6. **Docstrings** - Most functions documented with spec references

**Negative Patterns:**

1. **Global State** - `AppState` class with global `state` instance in `api.py`
2. **Direct Cache Access** - `policy.py` line 111 bypasses cache abstraction:
   ```python
   self._cache._cache[cache_key] = (policy_with_key, datetime.utcnow())
   ```
3. **Missing Abstract Base Classes** - No interfaces for Policy/Lineage clients

### 2.3 Readability

- **Score: 8/10**
- Clear variable names
- Logical function organization
- Good use of early returns
- Comments reference spec sections

---

## 3. Security Review

### 3.1 Critical Issues

**NONE FOUND**

### 3.2 High Priority Issues

| Issue | Location | Description | Recommendation |
|-------|----------|-------------|----------------|
| **S-HIGH-1: No Authentication** | `api.py` | No authentication mechanism. Any caller can evaluate/admit requests. | Implement API key or JWT authentication |
| **S-HIGH-2: No Authorization on Admin Endpoints** | `api.py:236-249` | Cache clear/invalidate endpoints have no access control | Add admin authentication |
| **S-HIGH-3: Tenant Isolation Not Enforced** | `api.py:165-167` | Warning logged on tenant mismatch but request proceeds | Reject requests where header tenant != envelope tenant |

### 3.3 Medium Priority Issues

| Issue | Location | Description | Recommendation |
|-------|----------|-------------|----------------|
| **S-MED-1: No Input Size Limits** | `models.py:63` | `payload: dict[str, Any]` has no size limit | Add payload size validation |
| **S-MED-2: SSRF Risk** | `forwarder.py` | Forward targets come from policy, but no URL validation | Validate forward URLs against allowlist |
| **S-MED-3: Rate Limiting Configured but Not Implemented** | `config.py:28-29` | `rate_limit_enabled` and `rate_limit_requests_per_minute` exist but are never used | Implement rate limiting middleware |

### 3.4 Low Priority Issues

| Issue | Location | Description | Recommendation |
|-------|----------|-------------|----------------|
| **S-LOW-1: Debug Mode in Production** | `config.py:14` | `debug: bool = False` but could be enabled via env | Ensure DEBUG cannot be enabled in production |
| **S-LOW-2: Binding to 0.0.0.0** | `config.py:12` | Default binds to all interfaces | Consider 127.0.0.1 default |
| **S-LOW-3: No Request Logging** | `api.py` | No structured logging of decisions for audit trail | Add audit logging |

---

## 4. Error Handling Review

### 4.1 Strengths

1. **Graceful Degradation** - Returns default policy when MetaGate unavailable
2. **Custom Exceptions** - `EvaluationError` with reason codes
3. **Retry Logic** - Forwarder retries on 5xx errors
4. **HTTP Error Handling** - Catches `httpx.RequestError`

### 4.2 Issues

| Issue | Location | Description |
|-------|----------|-------------|
| **E-1: Silent Failure on Lineage Errors** | `lineage.py:85-96` | Returns empty stats on any error, potentially allowing requests that should be denied |
| **E-2: No Circuit Breaker** | `policy.py`, `lineage.py` | Continuous failed requests to MetaGate/MemoryGate with no backoff |
| **E-3: No Timeout Configuration for Lineage** | `lineage.py:29` | Hardcoded 10s timeout |

---

## 5. Testing Review

### 5.1 Current State

**NO TESTS EXIST**

The `pyproject.toml` includes test dependencies:
```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.23.0",
    "pytest-httpx>=0.28.0",
]
```

But no test files were found in the repository.

### 5.2 Required Test Coverage

**Critical Tests Needed:**

1. **Unit Tests - Evaluator**
   - Test each check function in isolation
   - Test decision algorithm order
   - Test determinism (same input = same output)

2. **Unit Tests - Models**
   - Pydantic validation
   - Field constraints
   - Serialization/deserialization

3. **Integration Tests - API**
   - `/v1/evaluate` with various envelopes
   - `/v1/check` dry-run behavior
   - Error responses

4. **Integration Tests - External Services**
   - Mock MetaGate responses
   - Mock MemoryGate responses
   - Fallback behavior

5. **Security Tests**
   - Tenant isolation
   - Input validation
   - Injection attempts

---

## 6. Issues Found (Categorized by Severity)

### 6.1 Critical

| ID | Issue | Location | Impact |
|----|-------|----------|--------|
| **C-1** | **Missing Dependency** | `pyproject.toml` | `pydantic-settings` is imported in `config.py` but not listed in dependencies. Application will fail to start. |
| **C-2** | **No Test Coverage** | Project-wide | No way to verify correctness. High regression risk. |

### 6.2 High

| ID | Issue | Location | Impact |
|----|-------|----------|--------|
| **H-1** | No Authentication | `api.py` | Any caller can use the service |
| **H-2** | max_total_descendants Not Enforced | `evaluator.py` | Policy field is ignored |
| **H-3** | Silent Lineage Failures | `lineage.py` | May allow requests that should be denied |
| **H-4** | Rate Limiting Not Implemented | `config.py`, `api.py` | DoS vulnerability |

### 6.3 Medium

| ID | Issue | Location | Impact |
|----|-------|----------|--------|
| **M-1** | Direct Cache Access | `policy.py:111` | Breaks encapsulation |
| **M-2** | Tenant Mismatch Allowed | `api.py:165-167` | Potential authorization bypass |
| **M-3** | No Payload Size Limits | `models.py` | Memory exhaustion risk |
| **M-4** | Hardcoded Timeouts | `lineage.py:29` | Inflexible |
| **M-5** | No Circuit Breaker | External clients | Cascading failures |
| **M-6** | SSRF Risk in Forwarder | `forwarder.py` | Arbitrary URL requests |
| **M-7** | README License Mismatch | `README.md:144` | Says "Proprietary" but LICENSE file is Apache 2.0 |

### 6.4 Low

| ID | Issue | Location | Impact |
|----|-------|----------|--------|
| **L-1** | Global State Pattern | `api.py:29-36` | Testing difficulty |
| **L-2** | No Structured Logging | Various | Audit trail gaps |
| **L-3** | Missing `__all__` exports | `__init__.py` | Unclear public API |
| **L-4** | No healthcheck dependency verification | `api.py:124-131` | Reports healthy even if deps unavailable |
| **L-5** | datetime.utcnow() deprecated | `models.py:165`, `policy.py` | Use datetime.now(timezone.utc) |

---

## 7. Recommendations

### 7.1 Immediate Actions (Before Production)

1. **Fix Missing Dependency**
   ```toml
   dependencies = [
       ...
       "pydantic-settings>=2.0.0",
   ]
   ```

2. **Add Authentication Middleware**
   - Implement API key validation for all endpoints
   - Add admin-only protection for cache endpoints

3. **Implement Rate Limiting**
   - Use the configured values in `Settings`
   - Consider `slowapi` or custom middleware

4. **Write Core Tests**
   - Start with evaluator decision tests
   - Add API integration tests

### 7.2 Short-Term Improvements

1. **Add max_total_descendants Check**
   ```python
   def _check_total_descendants(self, ...):
       if policy.max_total_descendants and lineage:
           if lineage.total_descendants >= policy.max_total_descendants:
               raise EvaluationError(...)
   ```

2. **Enforce Tenant Isolation**
   ```python
   if x_tenant_id and envelope.tenant_id != x_tenant_id:
       raise HTTPException(403, "Tenant mismatch")
   ```

3. **Add Circuit Breaker**
   - Use `circuitbreaker` library for external service calls
   - Implement fallback behavior

4. **Fix Cache Encapsulation**
   - Add proper `set_with_key()` method to `PolicyCache`

### 7.3 Long-Term Improvements

1. **Implement OpenTelemetry**
   - Tracing for request flow
   - Metrics for decision counts, latencies

2. **Add Audit Logging**
   - Log all decisions with structured format
   - Include policy version, counters used

3. **Consider Event-Driven Mode**
   - Support async message queues as alternative to HTTP forwarding

4. **Policy Validation on Load**
   - Validate policy profiles have sensible values
   - Warn on overly permissive policies

---

## 8. Code Samples - Issues Found

### Issue C-1: Missing Dependency

**File:** `F:/HexyLab/LV_Stack/InterroGate/pyproject.toml`

```toml
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn>=0.27.0",
    "pydantic>=2.5.0",
    "httpx>=0.26.0",
    "ulid-py>=1.1.0",
    # MISSING: "pydantic-settings>=2.0.0",  <- Required by config.py
]
```

### Issue H-2: max_total_descendants Not Enforced

**File:** `F:/HexyLab/LV_Stack/InterroGate/src/interrogate/evaluator.py`

The field is received in `LineageStats.total_descendants` but never checked:

```python
# Current implementation is missing this check:
def _check_total_descendants(
    self,
    envelope: RequestEnvelope,
    policy: PolicyProfile,
    lineage: Optional[LineageStats],
) -> None:
    """Check total descendants against policy limit."""
    if not policy.max_total_descendants:
        return
    if not lineage:
        return
    if lineage.total_descendants >= policy.max_total_descendants:
        raise EvaluationError(
            f"Total descendants {lineage.total_descendants} exceeds max {policy.max_total_descendants}",
            RejectionReason.INVARIANT_VIOLATION,  # Or add new reason code
        )
```

### Issue M-1: Direct Cache Access

**File:** `F:/HexyLab/LV_Stack/InterroGate/src/interrogate/policy.py`

```python
# Line 111 - Bypasses cache abstraction
self._cache._cache[cache_key] = (policy_with_key, datetime.utcnow())

# Should be:
# Add method to PolicyCache:
# def set_with_key(self, key: str, policy: PolicyProfile) -> None:
#     ...
```

### Issue M-2: Tenant Mismatch Allowed

**File:** `F:/HexyLab/LV_Stack/InterroGate/src/interrogate/api.py`

```python
# Lines 165-167 - Only logs warning, doesn't reject
if x_tenant_id and envelope.tenant_id != x_tenant_id:
    logger.warning(
        f"Tenant mismatch: envelope={envelope.tenant_id}, header={x_tenant_id}"
    )
    # Should raise HTTPException(403, "Tenant mismatch")
```

---

## 9. Conclusion

InterroGate demonstrates a solid foundation for admission control with good spec compliance. The core decision algorithm is correctly implemented and follows the deterministic evaluation order required by the specification.

**Key Priorities:**

1. **Critical:** Fix the missing `pydantic-settings` dependency
2. **Critical:** Add test coverage before production use
3. **High:** Implement authentication and authorization
4. **High:** Implement the configured rate limiting
5. **Medium:** Add the missing `max_total_descendants` check

The codebase is well-structured and maintainable. With the identified issues addressed, InterroGate will be production-ready for its intended role as an admission filter in LegiVellum topologies.

---

*End of Code Review*
