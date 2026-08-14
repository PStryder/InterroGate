"""InterroGate configuration."""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator, model_validator
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
    metagate_url: Optional[str] = Field(default=None, description="MetaGate MCP endpoint")
    metagate_api_key: Optional[str] = Field(default=None, description="MetaGate API key")
    receiptgate_url: Optional[str] = Field(default=None, description="ReceiptGate MCP endpoint")
    receiptgate_api_key: Optional[str] = Field(default=None, description="ReceiptGate API key")
    memorygate_url: Optional[str] = Field(
        default=None,
        description="Deprecated: legacy MemoryGate URL (use receiptgate_url)",
    )

    # Policy cache
    policy_cache_ttl_seconds: int = Field(default=300, description="Policy cache TTL in seconds")
    policy_cache_max_size: int = Field(default=100, description="Maximum policy cache size")

    # Rate limiting
    rate_limit_enabled: bool = Field(default=True, description="Enable rate limiting")
    rate_limit_requests_per_minute: int = Field(default=1000, description="Rate limit per minute")

    # Forwarding
    forward_timeout_seconds: float = Field(default=30.0, description="Forward request timeout")
    forward_retries: int = Field(default=2, description="Forward retry attempts")
    forward_allowed_hosts: list[str] = Field(
        default=["localhost", "127.0.0.1"],
        description="Allowed forward target hosts (use '*' to allow any)"
    )
    forward_allowed_schemes: list[str] = Field(
        default=["http", "https"],
        description="Allowed schemes for forward targets"
    )
    forward_pass_headers: list[str] = Field(
        default=["X-Tenant-ID", "X-Request-ID"],
        description="Headers allowed to pass through to forward targets"
    )

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

    # MetaGate bootstrap. Optional by design: MetaGate is a describe-only,
    # non-blocking authority, so an unset endpoint means "use the values
    # configured here" rather than a misconfiguration.
    metagate_endpoint: Optional[str] = Field(default=None, description="MetaGate MCP endpoint for bootstrap")
    metagate_component_key: str = Field(default="interrogate", description="Component key presented at bootstrap")
    metagate_bootstrap_timeout_seconds: float = Field(default=5.0, description="Bootstrap request timeout")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port number range."""
        if not 1 <= v <= 65535:
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("metagate_url", "receiptgate_url", "memorygate_url")
    @classmethod
    def validate_integration_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate integration URLs are HTTP(S)."""
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"URL must start with http:// or https://, got {v}")
        return v

    @model_validator(mode="after")
    def validate_api_key(self) -> "Settings":
        """Validate API key is set when auth is required.

        Checked after every field is populated: a field_validator on api_key
        cannot see allow_insecure_dev, which is declared later and so is never
        present in ValidationInfo.data -- the flag read as False always, and
        INTERROGATE_ALLOW_INSECURE_DEV=true could not start the service.
        """
        if not self.api_key and not self.allow_insecure_dev:
            raise ValueError("api_key is required when allow_insecure_dev=False")
        return self


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
