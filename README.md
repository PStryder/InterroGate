# InterroGate

**Admission Filter for Recursion and Invariant Safety**

InterroGate is a composable, non-orchestrating filter that can be inserted at arbitrary points in a LegiVellum topology to prevent runaway recursion and enforce selected invariants.

## Status

**Implementation:** v0.1.0 (based on SPEC-IG-0000)

See: `SPEC-IG-0000 (v0).txt` for full specification.

## Overview

InterroGate is admission control, not orchestration. It can only:
- **ALLOW** - Emit acceptance receipt, forward request
- **DENY** - Emit rejection receipt, do not forward

## Installation

```bash
pip install -e ".[dev]"
```

## Configuration

Environment variables (prefix `INTERROGATE_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | 0.0.0.0 | Server bind address |
| `PORT` | 8000 | Server port |
| `DEBUG` | false | Enable debug mode |
| `INSTANCE_ID` | interrogate-1 | Instance identifier |
| `METAGATE_URL` | - | MetaGate URL for policy retrieval |
| `MEMORYGATE_URL` | - | MemoryGate URL for lineage queries |
| `POLICY_CACHE_TTL_SECONDS` | 300 | Policy cache TTL |
| `FORWARD_TIMEOUT_SECONDS` | 30.0 | Forwarding timeout |
| `DEFAULT_MAX_SPAWN_DEPTH` | 10 | Default max spawn depth |

## MCP Interface

InterroGate exposes MCP over HTTP at `/mcp` with JSON-RPC methods:
- `tools/list`
- `tools/call`

**Core tools:**
- `interrogate.health`
- `interrogate.evaluate`
- `interrogate.admit`
- `interrogate.check`
- `interrogate.cache_clear`
- `interrogate.cache_invalidate`

## Request Envelope

```json
{
  "envelope": {
    "tenant_id": "tenant-123",
    "surface_id": "api-gateway",
    "policy_profile_id": "default-policy",
    "payload_kind": "work_order",
    "payload": { ... },
    "causality": {
      "root_task_id": "task-001",
      "parent_task_id": null,
      "caused_by_receipt_id": null,
      "spawn_depth": 0,
      "capability_id": "document-processor",
      "recursion_budget_remaining": 10
    }
  },
  "forward": true
}
```

## Non-Goals (Hard Boundaries)

InterroGate MUST NOT:
- Schedule work
- Route dynamically based on mesh state
- Select among equivalent executors
- Infer completion or progress
- Initiate new work
- Mutate intent ("fix" payloads beyond normalization)

## Policy Domain

InterroGate evaluates requests within a Policy Domain identified by:
- `tenant_id` (required)
- `surface_id` (required)
- `policy_profile_id` (required)

## Rule Set (v0)

### Hard Limits
- `max_spawn_depth` (required)
- `max_total_descendants` (optional)
- `max_repeats_per_capability` (optional)
- `max_repeats_in_ancestor_window` (optional)

### Budget ("Recursion Fuel")
If `recursion_budget_remaining` is present, InterroGate decrements on ALLOW and rejects when <= 0.

## Decision Algorithm

1. Validate envelope fields
2. Load policy profile (from MetaGate or cache)
3. Query MemoryGate for lineage stats
4. Evaluate rules in order:
   - Missing fields → DENY
   - Budget exhausted → DENY
   - Depth exceeded → DENY
   - Repeats exceeded → DENY
   - Ancestor window violated → DENY
   - Invariant violations → DENY
5. If all pass → ALLOW

## Receipts

InterroGate emits exactly one receipt per decision:
- **Acceptance Receipt**: `phase=accepted`, counters used, policy version
- **Rejection Receipt**: `phase=rejected`, reason code, rejection detail

## Data Sources

- **MetaGate** - Policy retrieval
- **MemoryGate** - History queries (descendant counts, depth, capability repeats)

## Running

```bash
# Start server
uvicorn interrogate.mcp:app --host 0.0.0.0 --port 8000

# Or use the entry point
python -m interrogate.main
```

## MetaGate Bootstrap

On startup this gate asks MetaGate for the topology it belongs to and fills in
endpoints the operator did not configure. It resolves: `receiptgate` → `receiptgate_url`, `memorygate` → `memorygate_url`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `INTERROGATE_METAGATE_ENDPOINT` | *(unset)* | MetaGate MCP endpoint. Unset disables bootstrap; the gate starts on configured values alone. |
| `INTERROGATE_METAGATE_API_KEY` | *(unset)* | Credential presented to MetaGate |
| `INTERROGATE_METAGATE_COMPONENT_KEY` | `interrogate` | Which component in the manifest this process is |
| `INTERROGATE_METAGATE_BOOTSTRAP_TIMEOUT_SECONDS` | `5.0` | Per-call timeout |

Bootstrap never prevents startup. Every failure — unreachable, timeout, auth
rejected, no binding, malformed packet — degrades to a logged warning and
"carry on with configured values", because a bootstrap authority that can take
the mesh down would be a hidden master. Explicit configuration always wins;
bootstrap fills gaps and logs when the mesh disagrees rather than overriding.

See `LegiVellum/docs/canonical/metagate.bootstrap.md` for the full contract.

## License

Proprietary - Technomancy Labs
