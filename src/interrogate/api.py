"""InterroGate REST API (LEGACY, not mounted).

Implements the HTTP interface for admission control. MCP-only deployments should use /mcp tools.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import get_settings
from .evaluator import AdmissionEvaluator
from .forwarder import RequestForwarder
from .lineage import LineageClient
from .middleware import get_rate_limiter
from .models import (
    AdmissionReceipt,
    Decision,
    EvaluationResult,
    RequestEnvelope,
)
from .policy import PolicyManager
from .auth import verify_api_key

logger = logging.getLogger(__name__)


# Global state
class AppState:
    policy_manager: Optional[PolicyManager] = None
    lineage_client: Optional[LineageClient] = None
    evaluator: Optional[AdmissionEvaluator] = None
    forwarder: Optional[RequestForwarder] = None


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()

    logger.info(f"Starting InterroGate {settings.instance_id}...")

    # Initialize components
    state.policy_manager = PolicyManager()
    state.lineage_client = LineageClient()
    state.evaluator = AdmissionEvaluator(
        policy_manager=state.policy_manager,
        lineage_client=state.lineage_client,
    )
    state.forwarder = RequestForwarder()

    logger.info("InterroGate started - admission control active")

    yield

    # Cleanup
    logger.info("Shutting down InterroGate...")
    if state.policy_manager:
        await state.policy_manager.close()
    if state.lineage_client:
        await state.lineage_client.close()
    if state.forwarder:
        await state.forwarder.close()

    logger.info("InterroGate shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="InterroGate",
    description="""
InterroGate - Admission Filter for Recursion and Invariant Safety.

A composable, non-orchestrating filter that prevents runaway recursion
and enforces selected invariants in LegiVellum topologies.

InterroGate may only:
- ALLOW (emit acceptance receipt, forward)
- DENY (emit rejection receipt, do not forward)

This is admission control, not orchestration.
    """,
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allowed_methods,
    allow_headers=settings.cors_allowed_headers,
)


# Rate limiting dependency
async def rate_limit_dependency(request: Request) -> None:
    """Rate limiting dependency."""
    settings = get_settings()
    limiter = get_rate_limiter(
        calls_per_minute=settings.rate_limit_requests_per_minute,
        enabled=settings.rate_limit_enabled
    )
    await limiter.check_request(request)


# API Models
class HealthResponse(BaseModel):
    status: str
    instance_id: str
    version: str = "0.1.0"


class EvaluateRequest(BaseModel):
    """Request to evaluate admission."""

    envelope: RequestEnvelope = Field(..., description="Request envelope to evaluate")
    forward: bool = Field(default=True, description="Whether to forward on ALLOW")


class EvaluateResponse(BaseModel):
    """Response from admission evaluation."""

    decision: Decision
    receipt: AdmissionReceipt
    forwarded: bool = False
    forward_results: list[dict[str, Any]] = Field(default_factory=list)


class PolicyCacheResponse(BaseModel):
    """Response from cache operations."""

    cleared: bool
    invalidated: Optional[str] = None


# Endpoints


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint."""
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        service="InterroGate",
        version="0.1.0",
        instance_id=settings.instance_id,
    )


@app.get("/", tags=["root"])
async def root():
    """Root endpoint."""
    settings = get_settings()
    return {
        "service": "InterroGate",
        "version": "0.1.0",
        "instance_id": settings.instance_id,
        "doctrine": "Admission control, not orchestration.",
    }


@app.post("/v1/evaluate", response_model=EvaluateResponse, tags=["admission"], dependencies=[Depends(verify_api_key), Depends(rate_limit_dependency)])
async def evaluate_admission(
    request: EvaluateRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    authorization: Optional[str] = Header(None),
):
    """Evaluate an admission request.

    This is the main entry point for admission control.
    Evaluates the request against policy rules and either:
    - ALLOW: Optionally forwards to configured targets
    - DENY: Returns rejection receipt, never forwards
    """
    if not state.evaluator:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Override tenant_id from header if present
    envelope = request.envelope
    if x_tenant_id and envelope.tenant_id != x_tenant_id:
        logger.warning(
            f"Tenant mismatch: envelope={envelope.tenant_id}, header={x_tenant_id}"
        )

    # Evaluate
    result = await state.evaluator.evaluate(envelope)

    # Forward if allowed and requested
    forwarded = False
    forward_results = []

    if result.decision == Decision.ALLOW and request.forward and state.forwarder:
        original_headers = {}
        if x_tenant_id:
            original_headers["X-Tenant-ID"] = x_tenant_id
        if x_request_id:
            original_headers["X-Request-ID"] = x_request_id
        if authorization:
            original_headers["Authorization"] = authorization

        forward_results = await state.forwarder.forward(result, original_headers)
        forwarded = any(r.get("success") for r in forward_results)

    return EvaluateResponse(
        decision=result.decision,
        receipt=result.receipt,
        forwarded=forwarded,
        forward_results=forward_results,
    )


@app.post("/v1/admit", response_model=EvaluateResponse, tags=["admission"], dependencies=[Depends(verify_api_key)])
async def admit(
    envelope: RequestEnvelope,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
):
    """Shorthand admission endpoint.

    Evaluates and forwards in one call. Equivalent to
    POST /v1/evaluate with forward=true.
    """
    request = EvaluateRequest(envelope=envelope, forward=True)
    return await evaluate_admission(
        request=request,
        x_tenant_id=x_tenant_id,
    )


@app.post("/v1/check", tags=["admission"], dependencies=[Depends(verify_api_key)])
async def check_only(
    envelope: RequestEnvelope,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
):
    """Check admission without forwarding.

    Returns decision and receipt but never forwards.
    Useful for dry-run testing.
    """
    if not state.evaluator:
        raise HTTPException(status_code=503, detail="Service not ready")

    result = await state.evaluator.evaluate(envelope)

    return {
        "decision": result.decision,
        "receipt": result.receipt,
        "would_forward_to": result.forward_targets if result.decision == Decision.ALLOW else [],
    }


@app.post("/v1/cache/clear", response_model=PolicyCacheResponse, tags=["admin"], dependencies=[Depends(verify_api_key)])
async def clear_cache():
    """Clear the policy cache."""
    if state.policy_manager:
        state.policy_manager.clear_cache()
    return PolicyCacheResponse(cleared=True)


@app.post("/v1/cache/invalidate/{policy_profile_id}", response_model=PolicyCacheResponse, tags=["admin"], dependencies=[Depends(verify_api_key)])
async def invalidate_policy(policy_profile_id: str):
    """Invalidate a specific policy in the cache."""
    if state.policy_manager:
        state.policy_manager.invalidate(policy_profile_id)
    return PolicyCacheResponse(cleared=False, invalidated=policy_profile_id)
