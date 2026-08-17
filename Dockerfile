# InterroGate — admission control.
#
# Build context is the STACK ROOT, not this repository:
#
#     docker build -f InterroGate/Dockerfile .
#
# The image installs the canonical protocol package from the sibling LegiVellum
# checkout. `legivellum` is a hard dependency and is not published to an index,
# so a repo-scoped context cannot satisfy it.
#
# InterroGate had no Dockerfile at all: the demo stack pip-installed it into a
# bare python:3.11-slim with the repo bind-mounted, so the workflow written to
# catch container bugs could not catch InterroGate's, and every `compose up`
# required PyPI to be reachable.

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# The canonical protocol package first: receipt models, validation, and the
# schema, which ships as package data so validation needs no source checkout.
COPY LegiVellum/pyproject.toml LegiVellum/README.md /src/LegiVellum/
COPY LegiVellum/shared/ /src/LegiVellum/shared/
RUN pip install --no-cache-dir /src/LegiVellum

COPY InterroGate/pyproject.toml InterroGate/README.md /src/InterroGate/
COPY InterroGate/src/ /src/InterroGate/src/
RUN pip install --no-cache-dir /src/InterroGate

# Fail the build if the validator is not importable. An admission gate that
# cannot validate the receipts it emits must not be publishable.
RUN python -c "import legivellum.validation as v; p = v.schema_path(); assert p.exists(), p; print('receipt schema resolved at', p)"

RUN addgroup --system --gid 1001 interrogate \
    && adduser --system --uid 1001 --gid 1001 interrogate \
    && chown -R interrogate:interrogate /app

USER interrogate

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json, os, urllib.request; \
url=os.environ.get('INTERROGATE_MCP_URL','http://localhost:8000/mcp'); \
payload={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'interrogate.health','arguments':{}}}; \
req=urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}); \
data=json.load(urllib.request.urlopen(req, timeout=5)); \
assert 'result' in data"

CMD ["python", "-m", "uvicorn", "interrogate.mcp:app", "--host", "0.0.0.0", "--port", "8000"]
