from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://bargainista:bargainista@localhost:5432/bargainista"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    # MCP server — http://mcp:8001 inside Docker, http://localhost:8001 outside
    mcp_server_url: str = "http://localhost:8001"

    # AI API key — empty by default; required from Phase 3 onward
    anthropic_api_key: str = ""

    # Admin API key — protects depreciation config CRUD endpoints
    # Set a strong random value in .env; empty string disables admin endpoints
    admin_api_key: str = ""

    # Clerk auth — JWT verification and webhook signature
    # clerk_jwks_url: Clerk JWKS endpoint; default is the standard Clerk URL
    clerk_jwks_url: str = "https://api.clerk.com/v1/jwks"
    # clerk_webhook_secret: set in .env; empty string skips verification with a warning
    clerk_webhook_secret: str = ""

    debug: bool = False


settings = Settings()
