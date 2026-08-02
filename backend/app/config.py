"""Application configuration via environment variables (.env supported)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core ---
    app_name: str = "KathaSajha"
    environment: Literal["development", "production", "test"] = "development"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24  # 24h

    # --- Database ---
    # Prod (docker): postgresql+asyncpg://katha:katha@db:5432/kathasajha
    # Dev fallback:  sqlite+aiosqlite:///./data/kathasajha.db
    database_url: str = "sqlite+aiosqlite:///./data/kathasajha.db"

    # --- Redis / jobs ---
    redis_url: str = "redis://localhost:6379/0"
    # "arq" uses the Redis-backed worker (prod). "inline" runs generation as an
    # asyncio task inside the API process (keyless dev, tests).
    job_backend: Literal["arq", "inline"] = "inline"

    # --- Generation ---
    google_api_key: str = ""
    # "auto" picks gemini when a key is present, otherwise mock.
    generation_provider: Literal["auto", "gemini", "mock"] = "auto"
    story_model: str = "gemini-2.5-flash"
    image_model: str = "gemini-2.5-flash-image"
    max_prompt_chars: int = 500
    max_paragraphs: int = 5
    image_concurrency: int = 4
    generation_call_timeout_seconds: int = 120

    # --- Database pool (ignored for SQLite) ---
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Storage ---
    storage_backend: Literal["local", "s3"] = "local"
    media_root: str = "./data/media"
    media_url_prefix: str = "/media"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_public_base_url: str = ""

    # --- Limits / quotas ---
    rate_limit_enabled: bool = True
    rate_limit_generate_per_hour: int = 10  # per user
    rate_limit_auth_per_10min: int = 10  # per IP and per target email
    # Hard ceiling on any request body. Starlette has no default limit and the
    # body is fully buffered and parsed before a single field validator runs, so
    # without this an unauthenticated POST is a memory-exhaustion vector. The
    # largest legitimate request is a prompt plus a few short fields.
    max_request_body_bytes: int = 64 * 1024
    # Comma-separated peer IPs whose X-Forwarded-For header may be believed.
    # Empty means trust none: the header is client-controlled, and honouring it
    # unconditionally lets anyone rotate it to bypass the IP-keyed auth limits.
    trusted_proxy_ips: str = ""
    free_daily_stories: int = 3
    # Platform-wide ceiling on paid generations per UTC day. Per-user limits bound
    # one account; this bounds the bill when someone scripts thousands of signups.
    # Set to 0 to disable (not advisable once a real API key is configured).
    global_daily_generation_limit: int = 500

    # --- CORS (empty = same-origin only, no CORS needed) ---
    cors_origins: str = ""

    # --- Observability ---
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "text"  # compose sets json in production

    # --- Email (console prints the message to the log; no credentials needed) ---
    email_backend: Literal["console", "smtp"] = "console"
    email_from: str = "KathaSajha <no-reply@kathasajha.com>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    # Used to build links in emails. Empty means derive from the request.
    public_base_url: str = ""
    password_reset_ttl_minutes: int = 60

    @property
    def resolved_provider(self) -> str:
        if self.generation_provider == "auto":
            return "gemini" if self.google_api_key else "mock"
        return self.generation_provider

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_proxy_list(self) -> list[str]:
        return [p.strip() for p in self.trusted_proxy_ips.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
