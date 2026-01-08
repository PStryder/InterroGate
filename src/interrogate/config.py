"""InterroGate configuration."""

from functools import lru_cache
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """InterroGate settings from environment."""

    model_config = SettingsConfigDict(
        env_prefix="INTERROGATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server
    host: str = Field(default="0.0.0.0", description="Server bind address")
    port: int = Field(default=8000, description="Server port")
    debug: bool = Field(default=False, description="Enable debug mode")

    # Instance identity
    instance_id: str = Field(default="interrogate-1", description="Instance identifier")

    # External services
    metagate_url: Optional[str] = Field(default=None, description="MetaGate URL")
    memorygate_url: Optional[str] = Field(default=None, description="MemoryGate URL")

    # Policy cache
    policy_cache_ttl_seconds: int = Field(default=300, description="Policy cache TTL in seconds")
    policy_cache_max_size: int = Field(default=100, description="Maximum policy cache size")

    # Rate limiting
    rate_limit_enabled: bool = Field(default=True, description="Enable rate limiting")
    rate_limit_requests_per_minute: int = Field(default=1000, description="Rate limit per minute")

    # Forwarding
    forward_timeout_seconds: float = Field(default=30.0, description="Forward request timeout")
    forward_retries: int = Field(default=2, description="Forward retry attempts")

    # Default policy (fallback if MetaGate unavailable)
    default_max_spawn_depth: int = Field(default=10, description="Default max spawn depth")
    default_max_repeats_per_capability: int = Field(default=5, description="Default max repeats per capability")

    # Authentication
    api_key: str = Field(default="", description="API key for authentication")
    allow_insecure_dev: bool = Field(default=False, description="Allow unauthenticated access (dev only)")

    # CORS configuration (explicit allowlist for security)
    cors_allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        description="Allowed CORS origins"
    )
    cors_allow_credentials: bool = Field(default=True, description="Allow credentials in CORS requests")
    cors_allowed_methods: list[str] = Field(
        default=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        description="Allowed HTTP methods"
    )
    cors_allowed_headers: list[str] = Field(
        default=["Authorization", "Content-Type", "X-Tenant-ID"],
        description="Allowed request headers"
    )

    # Validators
    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port number range."""
        if not 1 <= v <= 65535:
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("metagate_url", "memorygate_url")
    @classmethod
    def validate_integration_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate integration URLs are HTTP(S)."""
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"URL must start with http:// or https://, got {v}")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str, info) -> str:
        """Validate API key is set when auth is required."""
        allow_insecure = info.data.get("allow_insecure_dev", False)
        if not v and not allow_insecure:
            raise ValueError("api_key is required when allow_insecure_dev=False")
        return v


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
