<!-- Generated 2026-08-15. Stack-level context: ../LV_STACK_REVIEW.md -->

> **Review 2 — InterroGate**
> Part of a full-stack review of LV_Stack (11 repos, ~97k LOC) conducted 2026-08-15.
> Stack-wide findings that affect this repo but are not fixable inside it are in
> `../LV_STACK_REVIEW.md` and `../_CROSS_REPO_ANALYSIS.md`. Read the stack report first —
> several findings below have a shared root cause.

---

# InterroGate — Code Review

Reviewed: `/home/claude/lv/InterroGate/` @ v0.1.0 (~2.9k LOC), against `SPEC-IG-0000 (v0).txt`,
`LegiVellum/docs/canonical/InterroGate/`, and `Gate v1 Exit Criteria Template.txt`.

## Verdict

The receipt modelling is the best work in either repo — the accepted→complete pair, DENY-as-success,
no-escalation and no-ownership-of-admitted-work semantics are correct, reasoned in prose, and
covered by real tests including canonical schema conformance. Everything around it fails open.
Every dependency failure in the decision path degrades toward permission: an unreachable, erroring,
or unauthorised MetaGate yields a **permissive default policy** (`can_spawn=True`, depth 10, no
allowlist), and an unreachable ReceiptGate yields **all-zero lineage stats** that silently disable
every history-based recursion limit. The `POLICY_NOT_FOUND` DENY branch is unreachable dead code.
And no component in the stack calls InterroGate — admission control is, today, decorative.

## Exit Criteria Scorecard

| § | Section | Score | Justification |
|---|---------|-------|---------------|
| 1 | Build & Run | **PARTIAL** | Cold start validates config and `.env.example` exists, but no Makefile/`run_local.sh`, no Dockerfile, no HTTP health endpoint (MCP tool only), and `.env.example` omits `INTERROGATE_RECEIPTGATE_URL` — so the shipped example produces a gate that emits no receipts at all. |
| 2 | API & Contract Stability | **PARTIAL** | Tools frozen by snapshot, but **every** error — auth failure, unknown tool, MetaGate 500 — returns JSON-RPC code `"ERROR"` (`mcp.py:273`), asserted as correct by `test_mcp_contract.py:68`. Callers cannot distinguish 401 from an evaluation crash. |
| 3 | Canonical Principals | **PARTIAL** | `SERVICE_PRINCIPAL_ID = "svc:interrogate"` defined (`legivellum_receipts.py:54`); `SYSTEM_PRINCIPAL_ID` absent; all receipts are addressed to `svc:interrogate` regardless of origin (`legivellum_receipts.py:93-97`), so the internal/external ownership rule is not expressed. |
| 4 | Receipt Model Invariants | **PARTIAL** | Phases, terminal semantics and lineage are right and tested; but no explicit `TERMINAL_RECEIPT_TYPES` set exists, `dedupe_key` is non-deterministic (M-4), and a half-emitted pair leaves an obligation open forever (H-3). |
| 5 | Persistence & Migration | **PASS (N/A)** | No DB. Policy cache is in-process; note its eviction is bypassed (M-2). |
| 6 | Core Behavioral Guarantees | **FAIL** | No golden-path demo for admission exists anywhere in the stack; nothing calls the gate; in default config `forward_targets` is `[]` so `interrogate.admit` admits and forwards nowhere. |
| 7 | Test Requirements | **FAIL** | 7 test files and **zero tests for `evaluator.py`** — the decision algorithm, the entire point of the component, has no coverage. No fail-closed regression of any kind. |
| 8 | Observability | **PASS** | `telemetry.py` counts deny reasons, forward retries and error codes; decisions are logged with policy identity (`legivellum_receipts.py:294-299`). |
| 9 | v1 Lock Rules | **PARTIAL** | Tool surface locked by snapshot; the *semantics* that most need locking — that a missing policy is permissive rather than a DENY — are neither documented nor frozen. |
| 10 | Open Issues / Deferred | **FAIL** | `V1_EXIT_CRITERIA.md` is a CI checklist. `max_total_descendants`, `allowed_targets` and `max_artifact_size_bytes` are accepted in policy and silently ignored, with no note saying so. |

## Fail-Open Audit

Decision path = `mcp.py:191 evaluate()` → `policy.get_policy()` → `lineage.get_lineage_stats()` →
six rule checks. Every row below is a failure of a dependency *inside* that path.

| Failure mode | file:line | Resulting decision |
|---|---|---|
| `INTERROGATE_METAGATE_URL` unset | `policy.py:102`, `:112-114` | **ALLOW-tending** — permissive default policy |
| MetaGate unreachable / DNS fail / connect timeout (`httpx.RequestError`) | `policy.py:175-177` → `:112-114` | **ALLOW-tending** — logs, returns `None`, falls to permissive default |
| MetaGate returns JSON-RPC error (auth denied, not admin, internal error) | `policy.py:155-156` → `:112-114` | **ALLOW-tending** — permissive default |
| MetaGate says the profile does not exist (`code == "not_found"`) | `policy.py:152-154` → `:112-114` | **ALLOW-tending** — permissive default. The `POLICY_NOT_FOUND` DENY at `evaluator.py:79-85` is unreachable |
| MetaGate returns 4xx/5xx (`httpx.HTTPStatusError` — *not* a `RequestError`) | `policy.py:149` uncaught by `:175` | Exception escapes to `mcp.py:272` → JSON-RPC error, **no decision, no receipt** |
| MetaGate returns malformed policy (Pydantic `ValidationError`) | `policy.py:173` uncaught | Exception escapes → **no decision, no receipt** |
| Cached policy is stale after the real policy was tightened | `policy.py:96-99`, TTL from `policy.cache_ttl_seconds` | **ALLOW-tending** — serves the old permissive policy, and `cache_invalidate` cannot clear it (H-2) |
| `INTERROGATE_RECEIPTGATE_URL` unset | `lineage.py:44-47` | **ALLOW-tending** — zero-valued `LineageStats`; depth/repeat/ancestor limits inert |
| ReceiptGate unreachable (`httpx.RequestError`) | `lineage.py:81-83` | **ALLOW-tending** — zero-valued `LineageStats` |
| ReceiptGate returns JSON-RPC error | `lineage.py:72-74` | **ALLOW-tending** — zero-valued `LineageStats` |
| ReceiptGate returns 4xx/5xx (`HTTPStatusError`) | `lineage.py:70` uncaught | Exception escapes → **no decision, no receipt** |
| Receipt ledger unreachable at emission time | `legivellum_receipts.py:285-292` | **ALLOW/DENY stands, receipt silently dropped** (H-3) |
| Forward target down after ALLOW | `forwarder.py:244-276` | ALLOW stands, `forwarded: false`; receipt already says complete/success |

Two distinct failure classes, both bad: dependency failures that produce a *permissive decision*
(the majority), and dependency failures that produce *no decision at all* while the receipt-based
audit trail records nothing. Spec §6 states "InterroGate MUST be deterministic: identical inputs +
identical observed history must produce identical outcomes." With this table, identical inputs
produce different outcomes depending on the health of two other services.

## DENY Receipt Semantics Conformance

**Conformant.** Verified against `legivellum_receipts.py:204-220` and
`LegiVellum/docs/canonical/InterroGate/alignment.md`:

| Requirement | Evidence | Verdict |
|---|---|---|
| DENY is `phase: complete` | `legivellum_receipts.py:208` (`phase="complete"`), test `test_evaluation_is_an_accepted_complete_pair` | PASS |
| DENY is `status: success` | `legivellum_receipts.py:214` (`status="success"`), test `test_deny_is_a_successful_completion` | PASS |
| Decision lives in the body | `body={"result": result}` with `result["decision"] == "DENY"` (`:185-216`) | PASS |
| DENY is not a failure | No `status="failure"` path exists | PASS |
| DENY is not an escalation | `escalation_class/reason/to` all `"NA"` (`:125-127`), test `test_admission_never_escalates` | PASS |
| No new receipt phase invented | Only `accepted` and `complete` are emitted | PASS |
| ALLOW does not make InterroGate owner of admitted work | Exactly 2 receipts, both `task_type="admission.evaluate"` on a private `admission-<uuid>` task; `parent_task_id` is the caller's parent, making admission a sibling of the gated work (`:90`), test `test_allow_does_not_mint_downstream_work` | PASS |
| `caused_by_receipt_id` links correctly | `accepted.caused_by = envelope.causality.caused_by_receipt_id` or `"NA"` (`:164-168,179`); `complete.caused_by = accepted.receipt_id` (`:215`) | PASS |
| Canonical schema conformance | `test_admission_receipts.py` validates both receipts against `receipt.schema.v1.json` and checks every required field is present | PASS |

Two caveats that do not break the semantics but weaken them in operation:
- **`interrogate.check` emits nothing** (`mcp.py:204`). Defensible as a dry-run, but it makes the tool a decision oracle with no ledger trace — an attacker can map the entire policy boundary (and read `would_forward_to`, M-5) invisibly.
- **Emission is best-effort and unsignalled** (H-3): the semantics are right in `build_admission_receipts` and can be silently discarded in `emit`.

## Is InterroGate actually on the request path?

**No.** Stack-wide grep evidence over `/home/claude/lv/`:

- `grep -rn "interrogate\.\(evaluate\|admit\|check\)"` outside `InterroGate/` returns **only documentation**: `LegiVellum/docs/canonical/InterroGate/README.md:51-53`. No caller in any repo.
- `grep -rn "admission_receipt_id\|X-InterroGate"` across `AsyncGate/src`, `DeleGate/src`, `CogniGate/src`, `DepotGate/src`, `ReceiptGate/src` returns **nothing**. No downstream gate requires, checks, or records an admission receipt. Any caller holding an AsyncGate or DeleGate key submits work directly and InterroGate never sees it.
- The demo stack runs it and never uses it: `LegiVellum/problemata_demo/docker-compose.yml:186-206` starts the container; `wait_for_stack.py:34` health-checks it; `demo_client.py` has clients for InterView, ReceiptGate, AsyncGate, DeleGate and **none for InterroGate**; there is no `admission_path.py` beside `golden_path.py` / `plan_path.py` / `escalation_path.py` / `observe_path.py`.
- The declared topology asserts an edge that no code implements: `LegiVellum/shared/legivellum/problemata_control.py:729-730` adds `interrogate → asyncgate` with purpose `"lease"`, but nothing forwards leases through it and `forward_targets` is `[]` in every default policy (`policy.py:202`).
- Even in the demo's own configuration the gate cannot work as intended: compose sets `INTERROGATE_METAGATE_URL` but **no `INTERROGATE_METAGATE_API_KEY`**, while MetaGate requires an *admin* principal for `metagate.admin_profiles` (`MetaGate/src/metagate/mcp/routes.py:605-607`). Policy fetch is therefore rejected on every request and the permissive default is used every time (see C-1).

**Finding (HIGH, `bypass`).** Admission control that nothing routes through enforces nothing. This
is not a latent risk — it is the current state. Either a downstream gate must reject work lacking a
valid `admission_receipt_id`, or InterroGate must be the only reachable ingress for the surfaces it
governs. Neither is true, and nothing in the repo records that gap.

## Critical & High Findings

### C-1 (CRITICAL) — Every policy-resolution failure yields a permissive default policy; the DENY branch is dead code
`policy.py:112-114`, `policy.py:186-204`, `evaluator.py:79-85`

```python
# policy.py:112
        # Return default fallback policy
        logger.warning(f"Using default policy for {cache_key}")
        return self._get_default_policy(policy_profile_id)
```

```python
# policy.py:188  _get_default_policy
        return PolicyProfile(
            policy_profile_id=policy_profile_id,
            policy_version="default",
            ...
            capability_allowlist=None,
            capability_denylist=None,
            can_spawn=True,
            forward_targets=[],
```

`get_policy` has no path that returns `None`: MetaGate unset, unreachable, erroring, or reporting
`not_found` all fall through to the permissive default. Consequently `evaluator.py:79-85` —

```python
        if not policy:
            return self._deny(... reason=RejectionReason.POLICY_NOT_FOUND ...)
```

— is **unreachable**. The one place the code refuses admission for lack of a policy can never run.

**Failure scenario.** Attacker capability: none beyond the ability to reach InterroGate; or simply
a MetaGate outage. A tenant is deployed with `policy_profile_id="locked-down"` whose real profile
sets `can_spawn=False` and `capability_denylist=["cap.exfiltrate"]`. MetaGate is restarted, or its
credential rotates, or — as in the shipped demo — InterroGate lacks the admin credential
`metagate.admin_profiles` demands. `_fetch_from_metagate` logs `MetaGate error: ...` and returns
`None`; `get_policy` returns the default; the evaluator runs against `can_spawn=True`, no denylist,
depth 10. Requests that policy forbids are admitted, and the emitted receipt records
`policy_version: "default"` — so the ledger faithfully documents that the gate was open. The most
likely time for MetaGate to be down is during an incident, which is exactly when admission control
matters.

Fix direction: `get_policy` must return `None` (or raise) on *fetch failure*, and only use a default
when the operator has explicitly opted into one (`INTERROGATE_ALLOW_DEFAULT_POLICY=true`), with the
distinction between "policy says allow" and "we could not read the policy" preserved in the receipt.

### C-2 (CRITICAL) — Lineage failure silently disables every history-based limit
`lineage.py:44-47`, `lineage.py:72-74`, `lineage.py:81-83`, `evaluator.py:156-158`

```python
# lineage.py:81
        except httpx.RequestError as e:
            logger.error(f"ReceiptGate request failed: {e}")
            return LineageStats(tenant_id=tenant_id, root_task_id=root_task_id)
```

All three degradation paths return `LineageStats` with `current_depth=0`, `total_descendants=0`,
`capability_repeat_count=0`, `ancestor_capability_ids=[]` — indistinguishable from "this is a fresh
root task". The evaluator then trusts the caller: `current_depth = max(envelope.causality.spawn_depth,
lineage.current_depth)` (`evaluator.py:155-158`), and `_check_repeats` / `_check_ancestor_window`
compare against zeros and pass.

**Failure scenario.** Attacker capability: an authenticated client (a worker whose credential was
stolen, or a buggy agent). ReceiptGate is unreachable, or `receiptgate_url` was never set. The
client submits `spawn_depth: 0, capability_id: "cap.plan"` on every recursive call. Depth check
compares `max(0, 0) >= 10` → passes. Repeat check has no lineage → returns early. Ancestor window →
returns early. Every request is ALLOWed, forever. The specific defence the component exists to
provide — "prevent runaway recursion" — is off, and the only signal is a `logger.error` line. Spec
§11 anticipates this ("incomplete history query → mitigate with component-local enforcement as
backstop"); no backstop exists, and no configuration distinguishes "history says fine" from "history
unavailable".

### H-1 (HIGH) — Nothing routes through InterroGate; the gate is bypassable by construction
Evidence and reasoning in *Is InterroGate actually on the request path?* above.

**Failure scenario.** Attacker capability: any credential for AsyncGate or DeleGate — i.e. exactly
what a legitimate worker holds. `POST asyncgate/mcp {"name":"asyncgate.submit_task", ...}` with no
admission receipt. AsyncGate accepts it (`grep admission_receipt_id AsyncGate/src` → no matches).
Recursion limits, capability denylists and spawn budgets are never consulted. This also makes C-1
and C-2 lower-impact today and higher-impact the moment someone wires the gate in and assumes it
works.

### H-2 (HIGH) — `cache_invalidate` is a no-op: a cached permissive policy cannot be flushed
`policy.py:95-109`, `policy.py:48-50`, `policy.py:206-208`, `mcp.py:239-245`

```python
# policy.py:95   write path — composite key
        cache_key = f"{tenant_id}:{surface_id}:{policy_profile_id}"
        cached = self._cache.get(cache_key)
        ...
                self._cache._cache[cache_key] = (policy_with_key, datetime.utcnow())

# policy.py:48   invalidate path — bare key
    def invalidate(self, policy_profile_id: str) -> None:
        self._cache.pop(policy_profile_id, None)
```

Entries are stored under `"<tenant>:<surface>:<profile>"` and invalidated under `"<profile>"`. The
keys never match, so `interrogate.cache_invalidate` always returns
`{"cleared": False, "invalidated": "<id>"}` (`mcp.py:245`) while removing nothing.

**Failure scenario.** An operator tightens a policy in MetaGate after discovering abuse, then calls
`interrogate.cache_invalidate{policy_profile_id: "public-api"}` and receives a success response.
The stale permissive policy continues to be served until its TTL expires — where the TTL comes from
the *policy document itself* (`policy.py:31`, `PolicyProfile.cache_ttl_seconds`, unbounded), so a
policy that shipped with `cache_ttl_seconds: 86400` pins the old rules for a day. The operator has
been told the invalidation worked. `cache_clear` (`policy.py:210-212`) does work, so the mitigation
is "clear everything", which nobody will discover under pressure.

### H-3 (HIGH) — Admission receipts are dropped silently, and a half-emitted pair leaves an obligation open forever
`legivellum_receipts.py:279-292`, `mcp.py:204-205`

```python
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                await self._submit(client, accepted)
                await self._submit(client, complete)
        except Exception as exc:  # noqa: BLE001 - never block an admission decision
            logger.warning("admission_receipt_emit_failed ...")
            return False
```

The `False` return is discarded at the call site (`mcp.py:205`) and never reaches the caller: the
JSON-RPC response (`mcp.py:224-229`) contains `decision`, `receipt`, `forwarded`,
`forward_results` — nothing about whether the decision was recorded.

**Failure scenario A (lost audit).** ReceiptGate is down for ten minutes. Every DENY in that window
— precisely the interesting ones, since a DENY produces no downstream obligation and so has no
other trace — vanishes. Callers receive `decision: "DENY"` with an inline `receipt` object bearing a
ULID that exists nowhere in the ledger. Post-incident, the record of what was refused and why is
gone, and nothing indicates data is missing rather than nothing having happened.

**Failure scenario B (dangling obligation).** `accepted` POSTs successfully; ReceiptGate then dies,
or returns 500, before `complete` lands. The ledger now holds an open `accepted` obligation for
`admission-<uuid>` owned by `svc:interrogate` that will never be closed — there is no retry, no
queue, no compensation, and no ledger-side timeout. This directly violates core invariant 2
("anything accepting responsibility emits an `accepted` receipt", with the corresponding terminal).
The repo's own test suite asserts the happy-path pair closes cleanly
(`test_denied_chain_leaves_no_open_obligation`) and never tests the split.

### H-4 (HIGH) — Depth is computed from receipt *count*, so ordinary long lineages are denied
`lineage.py:93-94`, `evaluator.py:155-164`

```python
        current_depth = len(receipts)
        total_descendants = max(0, current_depth - 1)
```

`receipts` is the result of `receiptgate.list_task_receipts{task_id: root_task_id}` — every receipt
ever written against the root task, not the spawn chain. Each work step normally writes at least an
`accepted` and a `complete`.

**Failure scenario.** A legitimate task performs six steps → ~12 receipts on the root task →
`current_depth = 12` → `max(spawn_depth, 12) >= 10` → **DENY, depth_exceeded** on a task whose
actual spawn depth is 1. The rejection detail says "Spawn depth 12 exceeds max 10", which is
actively misleading to whoever debugs it. Combined with C-2, the metric is both wrong when the
ledger is up and absent when it is down; there is no configuration in which it measures spawn depth.
Note this is a *fail-closed* bug, so it is the one failure mode a user will actually notice.

## Medium Findings

### M-1 (MEDIUM) — `HTTPStatusError` escapes both dependency clients, producing neither ALLOW nor DENY
`policy.py:148-149` + `:175`, `lineage.py:69-70` + `:81`

Both clients call `response.raise_for_status()` and then catch only `httpx.RequestError`.
`httpx.HTTPStatusError` is a sibling of `RequestError` under `HTTPError`, not a subclass (verified),
so a 401/403/500 from MetaGate or ReceiptGate propagates out of `evaluate()` to the generic handler
at `mcp.py:272-275` and is returned as JSON-RPC code `"ERROR"`. No decision, no receipt, no deny
reason counted.

**Failure scenario.** MetaGate's credential expires and it returns 401. Every admission request now
returns an error whose code is `"ERROR"` — the same code as "unknown tool" and "auth failed". A
caller written to `if response["result"]["decision"] == "DENY": abort` raises `KeyError`; whether
that ends up fail-open or fail-closed is decided by client-side code the gate has no contract with.
An admission gate must never delegate the safe default to its callers.

### M-2 (MEDIUM) — Policy cache bypasses its own eviction and grows without bound on caller-controlled keys
`policy.py:109` vs `policy.py:39-46`

```python
                policy_with_key = policy.model_copy()
                self._cache._cache[cache_key] = (policy_with_key, datetime.utcnow())
```

`PolicyCache.set()` — which enforces `_max_size` — is never called; the manager writes to the
private dict directly, so `INTERROGATE_POLICY_CACHE_MAX_SIZE` is documented in the README and
enforced nowhere. The key is `f"{tenant_id}:{surface_id}:{policy_profile_id}"`, all three
caller-supplied and unvalidated (`models.py:60-62`).

**Failure scenario.** Attacker capability: an InterroGate API key. Send N requests with random
`surface_id` values against a reachable MetaGate that resolves them; each successful fetch inserts a
permanent cache entry. Memory grows until OOM. (Unresolvable profiles fall to the default, which is
not cached — which is its own problem: with MetaGate configured but failing, every single request
pays a fresh 10 s-timeout HTTP round trip on the decision path, `policy.py:69`.)

### M-3 (MEDIUM) — Policy fields accepted, documented, and silently ignored
`evaluator.py:97-103`, `models.py:84`, `:96`, `:98`

`_check_depth`, `_check_repeats`, `_check_ancestor_window`, `_check_invariants` are called;
`max_total_descendants`, `allowed_targets` and `max_artifact_size_bytes` are never read anywhere in
`evaluator.py` or `forwarder.py`. Forward targets are validated against the *process* allowlist
(`forwarder.py:64-83`, `INTERROGATE_FORWARD_ALLOWED_HOSTS`), not against the policy's
`allowed_targets`, so the per-domain constraint spec §4.3 describes does not exist.

**Failure scenario.** An operator writes a profile with `max_total_descendants: 50` believing fan-out
is capped. A task spawns 5000 descendants; every one is ALLOWed; nothing logs that the field was
ignored. Silent non-enforcement of a configured control is worse than not offering the field.
Recurrence of CODE_REVIEW_1 H-2, unfixed.

### M-4 (MEDIUM) — `dedupe_key` is non-deterministic, so retries multiply ledger writes
`legivellum_receipts.py:92`, `:154`

```python
    task_id = f"admission-{uuid4()}"
    ...
        "dedupe_key": f"admission:{task_id}:{phase}",
```

The dedupe key is derived from a fresh UUID, so it is unique per evaluation by construction and can
never deduplicate anything. Exit Criteria §7 requires a "dedupe behavior verified" regression; there
is none.

**Failure scenario.** A client with retry logic sends the same admission request 5 times (network
flakiness, or a forward that timed out). The identical decision is written 10 times as 5 unrelated
obligations. At scale the ledger fills with duplicate admission pairs that no query can collapse,
and "how many times was this refused?" becomes unanswerable. A content hash over
`(tenant, surface, profile, causality, decision)` would make retries idempotent.

### M-5 (MEDIUM) — `interrogate.check` discloses internal forward targets to any authenticated caller
`mcp.py:230-231`

```python
        if name == "interrogate.check":
            response["would_forward_to"] = result.forward_targets if ... else []
```

`forward_targets` are internal MCP endpoints from the policy (e.g. `http://asyncgate:8000/mcp::asyncgate.submit_task`).
`check` requires only the shared API key, emits no receipt (`mcp.py:204`), and is explicitly
described as a dry-run.

**Failure scenario.** Attacker capability: the InterroGate key. Iterate `policy_profile_id` /
`surface_id` values against `interrogate.check` to enumerate which internal services each policy
domain forwards to, plus — via `rejection_reason_code` — the shape of every policy's limits, with
zero ledger trace. It is a free, silent reconnaissance oracle over the topology.

### M-6 (MEDIUM) — No privilege separation on the cache-management tools
`mcp.py:170-172`, `:234-245`

`validate_api_key_value` is the only check, and it is the same single key used for `evaluate`. Any
principal permitted to ask for an admission decision may also flush the entire policy cache.

**Failure scenario.** Attacker capability: a compromised worker credential. Call
`interrogate.cache_clear`, then flood `interrogate.evaluate` while MetaGate is slow or rate-limiting.
Every miss falls to the permissive default policy (C-1) — the attacker turns a cache into an
attack primitive to downgrade policy. CODE_REVIEW_1 S-HIGH-2 is only half-fixed: auth exists,
authorization does not.

### M-7 (MEDIUM) — Envelope `tenant_id` is unchecked, and the previous check was deleted
`mcp.py:186-191`, `models.py:60`

CODE_REVIEW_1 M-2 recorded that `api.py:165-167` logged a warning on `X-Tenant-ID` vs
`envelope.tenant_id` mismatch without rejecting. `api.py` no longer exists; the MCP handler does not
compare them at all. The check regressed from "weak" to "absent".

**Failure scenario.** A caller sets `envelope.tenant_id` to any value. That value selects the policy
cache key (M-2), the policy domain (spec §2 says domains are explicit and MUST NOT be inferred), the
lineage query scope, and the `tenant_id` stamped on both emitted receipts
(`legivellum_receipts.py:85`). One tenant can therefore have its decisions evaluated under — and
attributed to — another tenant's policy domain. Same root cause as InterView H-1: no principal is
ever derived from the credential.

### M-8 (MEDIUM) — Forward retries have no backoff and hold the decision path for up to ~90 s
`forwarder.py:220-276`, `config.py:49-50`

`for attempt in range(self._settings.forward_retries + 1)` retries immediately on 5xx and network
errors — no sleep, no jitter, no circuit breaker — with `forward_timeout_seconds=30.0` default and
`forward_retries=2`. Targets are iterated sequentially (`forwarder.py:150`).

**Failure scenario.** A downstream target is overloaded and returning 503. Every admitted request
issues 3 immediate POSTs to it — InterroGate amplifies load onto a service that is already failing —
while the caller blocks up to 90 s per target. `_test_env` in `test_forwarding_failure_modes.py:41`
sets `INTERROGATE_FORWARD_RETRIES=1`, so the shipped default's timing behaviour is never exercised.

## Low / Nits

- **L-1 (LOW)** `config.py:70-83` defines `cors_allowed_origins`, `cors_allow_credentials`, `cors_allowed_methods`, `cors_allowed_headers`; `grep -n "CORS" src/interrogate/*.py` returns nothing. Four settings documented in `README.md:72-79` are wired to no application. The sibling (`InterView/src/interview/main.py:53-59`) does apply them.
- **L-2 (LOW)** `mcp.py:272-275` returns `str(exc)` to the caller for any unexpected exception, including MetaGate/ReceiptGate errors carrying internal URLs. Same defect as InterView M-4.
- **L-3 (LOW)** `mcp.py:210-221` collects `Authorization` and `X-API-Key` from the inbound request into `original_headers`; whether they are relayed downstream depends entirely on `forward_pass_headers`. The default excludes them (good), but adding `"Authorization"` to a config list silently turns InterroGate into a credential relay. Nothing in the code or README warns of this.
- **L-4 (LOW)** `config.py:18` omits `extra="ignore"`, so `pydantic-settings` defaults to `extra="forbid"`: a stray `INTERROGATE_*` variable in `.env` crashes startup. InterView sets `extra="ignore"` (`config.py:20`). Unexplained divergence.
- **L-5 (LOW)** `.env.example` omits `INTERROGATE_RECEIPTGATE_URL`, `INTERROGATE_RECEIPTGATE_API_KEY`, `INTERROGATE_METAGATE_ENDPOINT`, `INTERROGATE_METAGATE_API_KEY`, and all forwarding/CORS keys. Copied verbatim, it produces a gate with no lineage source and no receipt emission — the two dependencies whose absence causes C-2 and H-3.
- **L-6 (LOW)** `mcp.py:183-184` raises `RuntimeError("Service not ready")` when `state.evaluator` is unset, but `interrogate.health` returns `"healthy"` unconditionally (`mcp.py:174-181`) with no dependency check. Recurrence of CODE_REVIEW_1 L-4.
- **NIT-1** `test_mcp_contract.py:68` asserts the auth-failure code is `"ERROR"`, freezing the collapsed error model into the contract.
- **NIT-2** CI runs `pytest tests/test_mcp_snapshot.py` without `--no-cov` (`.github/workflows/ci.yml:45`) while `pyproject.toml:57` applies `--cov-fail-under=40` to every invocation. InterView hit exactly this and added `--no-cov` with an explanatory comment. **SUSPECTED** CI failure on InterroGate — a single-file run over a larger package is very unlikely to reach 40%. To confirm: run `pytest tests/test_mcp_snapshot.py` and read the coverage total (not run here per instructions).
- **NIT-3** `datetime.utcnow()` at `models.py:166`, `policy.py:33,46,109` — naive UTC, deprecated in 3.12.
- **NIT-4** `README.md:177-178` still documents "Rejection Receipt: `phase=rejected`", contradicting the implemented and canonical `phase=complete` semantics that `legivellum_receipts.py` and the canonical alignment note now define. The internal `AdmissionReceipt.phase` genuinely is `"rejected"` (`evaluator.py:315`), which is fine as an internal DTO but makes the README wrong about what enters the ledger.
- **NIT-5** `README.md:216` says Proprietary; `LICENSE` is Apache 2.0. Recurrence of CODE_REVIEW_1 M-7.

## Test Coverage Gaps

7 test files, `--cov-fail-under=40`. The receipt-semantics suite is genuinely good. The decision
engine has **no tests at all** — `evaluator.py` is not imported by any test file. Missing
regressions, named:

1. **Every DENY rule.** No test proves that `spawn_depth >= max_spawn_depth` denies, that budget `<= 0` denies, that a denylisted capability denies, that `can_spawn=False` denies a `work_order`, or that the ancestor window works. The component's entire purpose is untested.
2. **Fail-closed on policy failure.** No test asserts what happens when MetaGate errors. `test_policy_fallback.py` asserts the *opposite* — that an unconfigured MetaGate yields the permissive default — thereby freezing C-1 in as intended behaviour.
3. **Fail-closed on lineage failure.** No test for `lineage` returning zeros on error, nor for the resulting ALLOW (C-2).
4. **`HTTPStatusError` handling.** No test sends a 500 from MetaGate or ReceiptGate; the uncaught-exception path (M-1) would be caught by one `httpx_mock.add_response(status_code=500)`.
5. **Cache invalidation.** No test calls `invalidate()` and then asserts the next `get_policy` refetches — this would have caught H-2 immediately.
6. **Partial receipt emission.** `TestEmissionIsBestEffort` covers "no endpoint" and "unreachable", never "accepted succeeded, complete failed" (H-3B).
7. **Determinism (spec §6 MUST).** No test asserts identical inputs + identical history ⇒ identical outcome.
8. **`forward` never happens on DENY.** `forwarder.forward` raises on a DENY result (`forwarder.py:138-139`) but no test asserts the MCP layer never reaches it.
9. **Rate limiting.** Disabled in every fixture; never exercised.

## Delta vs CODE_REVIEW_1.md

| CODE_REVIEW_1 finding | Status now |
|---|---|
| C-1 missing `pydantic-settings` dependency | **Fixed** — `pyproject.toml:16` |
| C-2 no test coverage | **Partially fixed** — 7 files, but `evaluator.py` still has zero |
| H-1 no authentication | **Fixed** — `auth.py` + `mcp.py:171-172`, constant-time compare |
| H-2 `max_total_descendants` not enforced | **Still open** (M-3), along with `allowed_targets` and `max_artifact_size_bytes` |
| H-3 silent lineage failures "may allow requests that should be denied" | **Still open, and confirmed** (C-2). The prior review flagged it as a possibility; it is the actual behaviour on three separate paths |
| H-4 rate limiting not implemented | **Fixed** — `middleware/rate_limit.py`, applied `mcp.py:253` |
| M-1 direct cache access `self._cache._cache[...]` | **Still open** (`policy.py:109`) — quoted verbatim in the prior review, unchanged, and it disables `policy_cache_max_size` (M-2) |
| M-2 tenant mismatch only logged | **Regressed** — the check no longer exists at all (M-7) |
| M-3 no payload size limits | Still open — `payload: dict[str, Any]` unbounded (`models.py:64`) |
| M-4 hard-coded lineage timeout | Still open (`lineage.py:28`, 10 s) |
| M-5 no circuit breaker | Still open (M-8) |
| M-6 SSRF risk in forwarder | **Fixed** — `forwarder.py:56-83` scheme + host allowlist, default `localhost` only |
| M-7 license mismatch | Still open (NIT-5) |
| L-4 health reports healthy without dependency checks | Still open (L-6) |
| L-5 `datetime.utcnow()` | Still open (NIT-3) |
| **Not in CODE_REVIEW_1 at all** | Its §3.1 says "Critical Issues: NONE FOUND" and its §4.1 lists "Graceful Degradation — returns default policy when MetaGate unavailable" as a **strength**. The single most serious defect in the component was reviewed and approved. Receipt emission (`legivellum_receipts.py`) did not exist then and is new, good work. |

## Cross-repo observations

- **Same skeleton.** `auth.py`, `telemetry.py`, `middleware/rate_limit.py`, `config.py` validators, `metagate_client.py` and CI are near-identical to InterView's. InterroGate is the stricter sibling: ruff `+B`, `check_untyped_defs = true`, `disallow_incomplete_defs = true`, plus `jsonschema` and a canonical-schema conformance test. InterView has none of that. No recorded reason for the split.
- **Opposite wiring mistakes.** InterroGate defines CORS settings and applies them to nothing (L-1); InterView applies CORS to `interview.main:app` but tests `interview.mcp:app`, so its contract tests exercise an app nobody deploys. Both siblings mis-wire the same subsystem in different directions.
- **Shared root cause: no principal.** Both reduce authorization to one shared bearer token, both accept `tenant_id` as an unvalidated caller string (`mcp.py:188` / `InterView/src/interview/api.py:181`). In InterView this becomes data egress; in InterroGate it becomes policy-domain selection. This is a stack-level design gap, and neither repo records that it depends on someone else fixing it.
- **Opposite failure philosophies, correctly applied in only one place.** Both inherit "bootstrap must never block startup" from `metagate.bootstrap.md`, which is right for both. InterroGate then extends that reasoning to the *decision path* (`policy.py:112`, `lineage.py:83`, `legivellum_receipts.py:285`) — where an observer degrading gracefully is correct, but a gate degrading gracefully means opening. The comment at `legivellum_receipts.py:227-229` explicitly cites the bootstrap precedent as justification. That inference is the bug.
- **Error-model divergence.** InterView wraps auth in its own try/except and returns `"AUTH_FAILED"` (`InterView/src/interview/mcp.py:232-236`); InterroGate lets the `HTTPException` fall into the generic handler and returns `"ERROR"` (`mcp.py:272-275`), then asserts that in its contract test. Their respective `V1_EXIT_CRITERIA.md` files differ in exactly this line ("rejected with auth failure code" vs "rejected"), so the divergence has been ratified rather than noticed.
- **`.gitignore` divergence.** InterroGate ignores `.coverage`; InterView does not and has the binary committed.
- **Neither repo has a Makefile, `run_local.sh`, or Dockerfile**, so Exit Criteria §1's one-command run and container build fail identically for both. The demo compose works around it with `pip install` inside `python:3.11-slim`.

## What's solid

- **The receipt model.** `legivellum_receipts.py` is the strongest artefact in either repo: the module docstring argues the modelling decision, the code implements it, and 20+ tests pin the semantics — including canonical schema validation and a guard against a newly-required schema field going unemitted. DENY-as-successful-completion, no synthetic escalation, no ownership of admitted work, correct `caused_by_receipt_id` chaining: all correct and all defended by tests.
- **Forwarding safety.** Scheme + host allowlist defaulting to localhost only, wildcard support that is explicit, DENY results structurally unable to be forwarded (`forwarder.py:138`, plus `forward_targets=[]` on DENY at `evaluator.py:334`), and sensitive headers not relayed by default.
- **Observability of decisions.** Deny reasons and forward retries are counted with reason codes (`telemetry.py:37-68`), which is more than most components here manage.
- **Evaluation order matches spec §6 exactly**, and the checks are pure functions of `(envelope, policy, lineage)` — the engine is deterministic given its inputs. The non-determinism lives entirely in how those inputs are obtained.
- **Rule structure is honest about optionality**: each `_check_*` returns early when the policy field is absent, so an under-specified policy does not crash — it just does not constrain, which is the correct local behaviour even though the global default that feeds it is wrong.
