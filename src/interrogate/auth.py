"""
Authentication for InterroGate MCP HTTP requests.

Simple API key authentication for protecting MCP endpoints.
"""

import logging
import secrets
from typing import Optional

from fastapi import Header, HTTPException, status

from .config import get_settings

logger = logging.getLogger(__name__)

# API key prefix for InterroGate
API_KEY_PREFIX = "ig_"


def validate_api_key_value(api_key: Optional[str]) -> bool:
    """Validate an API key string against configured settings."""
    settings = get_settings()

    if settings.allow_insecure_dev:
        return True

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization. Use Authorization: Bearer <key> or X-API-Key header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not settings.api_key:
        logger.error(
            "SECURITY VIOLATION: api_key not configured. "
            "Set INTERROGATE_API_KEY or enable INTERROGATE_ALLOW_INSECURE_DEV=true (dev only)."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server misconfigured: authentication not properly initialized",
        )

    if not secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True


def verify_api_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> bool:
    """
    Verify API key for protected endpoints.

    Checks Authorization: Bearer or X-API-Key header against
    the configured INTERROGATE_API_KEY environment variable.
    """
    api_key = None
    if authorization and authorization.startswith("Bearer "):
        api_key = authorization[7:]
    elif x_api_key:
        api_key = x_api_key

    return validate_api_key_value(api_key)


def generate_api_key() -> str:
    """Generate a new API key with ig_ prefix.

    Utility function for generating keys - the key should be stored
    in INTERROGATE_API_KEY environment variable.
    """
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
