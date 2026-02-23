# InterroGate v1 Exit Criteria

InterroGate is eligible for a v1 release only when all items below are satisfied.

## Quality Gate (Required on CI)

Run from repository root:

```bash
python -m pip install -e ".[dev]"
python -m pip check
python -m compileall -q src/interrogate
ruff check src tests
mypy src/interrogate
pytest
```

Passing means:
- No Ruff violations.
- No MyPy errors.
- All tests pass.
- Coverage floor is met (`--cov-fail-under=40` from `pyproject.toml`).

## Required MCP Contract Assertions

The test suite must continuously verify:
- `tools/list` works without auth and advertises core tools.
- Unknown JSON-RPC methods return `-32601`.
- `tools/call` without auth is rejected.
- Authenticated `interrogate.health` returns service identity and healthy status.

## Release Blocking Rule

Any failing quality-gate check or MCP contract assertion blocks v1 promotion.
