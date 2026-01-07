"""InterroGate configuration."""

from functools import lru_cache
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """InterroGate settings from environment."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Instance identity
    instance_id: str = "interrogate-1"

    # External services
    metagate_url: Optional[str] = None
    memorygate_url: Optional[str] = None

    # Policy cache
    policy_cache_ttl_seconds: int = 300
    policy_cache_max_size: int = 100

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 1000

    # Forwarding
    forward_timeout_seconds: float = 30.0
    forward_retries: int = 2

    # Default policy (fallback if MetaGate unavailable)
    default_max_spawn_depth: int = 10
    default_max_repeats_per_capability: int = 5

    model_config = {"env_prefix": "INTERROGATE_"}


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
