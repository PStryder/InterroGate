"""InterroGate MCP server (HTTP JSON-RPC)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, Field

from .auth import validate_api_key_value
from .config import get_settings
from .evaluator import AdmissionEvaluator
from .forwarder import RequestForwarder
from .lineage import LineageClient
from .middleware import get_rate_limiter
from .models import Decision, RequestEnvelope
from .policy import PolicyManager

logger = logging.getLogger(__name__)


class AppState:
    policy_manager: Optional[PolicyManager] = None
    lineage_client: Optional[LineageClient] = None
    evaluator: Optional[AdmissionEvaluator] = None
    forwarder: Optional[RequestForwarder] = None


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    settings = get_settings()
    logger.info("interrogate_starting instance_id=%s", settings.instance_id)

    state.policy_manager = PolicyManager()
    state.lineage_client = LineageClient()
    state.evaluator = AdmissionEvaluator(
        policy_manager=state.policy_manager,
        lineage_client=state.lineage_client,
    )
    state.forwarder = RequestForwarder()

    yield

    if state.policy_manager:
        await state.policy_manager.close()
    if state.lineage_client:
        await state.lineage_client.close()
    if state.forwarder:
        await state.forwarder.close()
    logger.info("interrogate_shutdown instance_id=%s", settings.instance_id)


class MCPRequest(BaseModel):
    """JSON-RPC request envelope for MCP."""

    jsonrpc: str = Field(default="2.0")
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    id: Any = None


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: Any, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "interrogate.health",
        "description": "Health check / service info",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "interrogate.evaluate",
        "description": "Evaluate admission with optional forwarding",
        "inputSchema": {
            "type": "object",
            "properties": {
                "envelope": {"type": "object"},
                "forward": {"type": "boolean"},
                "auth_token": {"type": "string"},
            },
            "required": ["envelope"],
        },
    },
    {
        "name": "interrogate.admit",
        "description": "Evaluate and forward (shorthand)",
        "inputSchema": {
            "type": "object",
            "properties": {"envelope": {"type": "object"}, "auth_token": {"type": "string"}},
            "required": ["envelope"],
        },
    },
    {
        "name": "interrogate.check",
        "description": "Evaluate without forwarding (dry-run)",
        "inputSchema": {
            "type": "object",
            "properties": {"envelope": {"type": "object"}, "auth_token": {"type": "string"}},
            "required": ["envelope"],
        },
    },
    {
        "name": "interrogate.cache_clear",
        "description": "Clear policy cache",
        "inputSchema": {"type": "object", "properties": {"auth_token": {"type": "string"}}},
    },
    {
        "name": "interrogate.cache_invalidate",
        "description": "Invalidate a cached policy profile",
        "inputSchema": {
            "type": "object",
            "properties": {
                "policy_profile_id": {"type": "string"},
                "auth_token": {"type": "string"},
            },
            "required": ["policy_profile_id"],
        },
    },
]


router = APIRouter(prefix="/mcp", tags=["mcp"])


def _extract_auth_token(arguments: dict[str, Any], request: Request) -> Optional[str]:
    token = arguments.pop("auth_token", None)
    if token:
        return token
    auth_header = request.headers.get("authorization")
    api_key_header = request.headers.get("x-api-key")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1]
    if api_key_header:
        return api_key_header
    return None


async def _rate_limit(request: Request) -> None:
    settings = get_settings()
    limiter = get_rate_limiter(
        calls_per_minute=settings.rate_limit_requests_per_minute,
        enabled=settings.rate_limit_enabled,
    )
    await limiter.check_request(request)


async def _handle_tool(name: str, arguments: dict[str, Any], request: Request) -> dict[str, Any]:
    auth_token = _extract_auth_token(arguments, request)
    validate_api_key_value(auth_token)

    if name == "interrogate.health":
        settings = get_settings()
        return {
            "status": "healthy",
            "service": "InterroGate",
            "version": "0.1.0",
            "instance_id": settings.instance_id,
        }

    if not state.evaluator:
        raise RuntimeError("Service not ready")

    if name in {"interrogate.evaluate", "interrogate.admit", "interrogate.check"}:
        envelope_data = arguments.get("envelope") or {}
        envelope = RequestEnvelope(**envelope_data)
        forward = bool(arguments.get("forward", name != "interrogate.check"))

        result = await state.evaluator.evaluate(envelope)

        forwarded = False
        forward_results: list[dict[str, Any]] = []
        if result.decision == Decision.ALLOW and forward and state.forwarder:
            original_headers: dict[str, str] = {}
            pass_through_headers = {
                "Authorization": "authorization",
                "X-Tenant-ID": "x-tenant-id",
                "X-Request-ID": "x-request-id",
                "X-API-Key": "x-api-key",
            }
            for outbound_name, inbound_name in pass_through_headers.items():
                value = request.headers.get(inbound_name)
                if value:
                    original_headers[outbound_name] = value
            forward_results = await state.forwarder.forward(result, original_headers)
            forwarded = any(r.get("success") for r in forward_results)

        response = {
            "decision": result.decision,
            "receipt": result.receipt.model_dump(),
            "forwarded": forwarded,
            "forward_results": forward_results,
        }
        if name == "interrogate.check":
            response["would_forward_to"] = result.forward_targets if result.decision == Decision.ALLOW else []
        return response

    if name == "interrogate.cache_clear":
        if state.policy_manager:
            state.policy_manager.clear_cache()
        return {"cleared": True}

    if name == "interrogate.cache_invalidate":
        policy_profile_id = arguments.get("policy_profile_id")
        if not policy_profile_id:
            raise ValueError("policy_profile_id is required")
        if state.policy_manager:
            state.policy_manager.invalidate(policy_profile_id)
        return {"cleared": False, "invalidated": policy_profile_id}

    raise ValueError(f"Unknown tool: {name}")


@router.post("")
async def mcp_entry(request_body: MCPRequest, request: Request) -> dict[str, Any]:
    await _rate_limit(request)

    if request_body.method == "tools/list":
        return _jsonrpc_result(request_body.id, {"tools": MCP_TOOLS})

    if request_body.method != "tools/call":
        return _jsonrpc_error(request_body.id, -32601, f"Method not found: {request_body.method}")

    params = request_body.params or {}
    tool_name = params.get("name")
    arguments = params.get("arguments") or {}
    if not tool_name:
        return _jsonrpc_error(request_body.id, -32602, "Missing tool name")

    try:
        result = await _handle_tool(tool_name, arguments, request)
        return _jsonrpc_result(request_body.id, result)
    except Exception as exc:
        return _jsonrpc_error(request_body.id, getattr(exc, "code", "ERROR"), str(exc))


app = FastAPI(title="InterroGate", version="0.1.0", lifespan=lifespan)
app.include_router(router)
