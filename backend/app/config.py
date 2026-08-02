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
    # The daily cap stops bursts; this one bounds what a free account can
    # actually cost. At 3/day with no monthly bound, a free user could take 90
    # stories a month -- roughly 450 illustrations -- which at any plausible
    # per-image price exceeds what the paid tier charges.
    free_monthly_stories: int = 10

    # --- Unit cost telemetry ---
    # Rates from the provider's pricing page, in USD. Left at 0 deliberately:
    # a wrong hardcoded price is worse than an obviously-unset one, and these
    # change. Token and image COUNTS are always recorded regardless, so cost can
    # be recomputed for past generations once these are set.
    price_per_1m_input_tokens_usd: float = 0.0
    price_per_1m_output_tokens_usd: float = 0.0
    price_per_image_usd: float = 0.0

    # Platform-wide ceiling on paid generations per UTC day. Per-user limits bound
    # one account; this bounds the bill when someone scripts thousands of signups.
    # Set to 0 to disable (not advisable once a real API key is configured).
    global_daily_generation_limit: int = 500

    # --- Billing (dormant until credentials are supplied) ---
    # "auto" turns billing on only when every required Stripe value is present.
    # It never resolves to "mock": accidentally shipping a fake billing provider
    # that hands out paid plans is a worse failure than billing being off.
    billing_provider: Literal["auto", "stripe", "mock", "none"] = "auto"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # plan code -> Stripe price id, e.g. "plus=price_123". Same parsing style as
    # cors_origins and trusted_proxy_ips.
    stripe_price_ids: str = ""
    stripe_api_version: str = "2024-06-20"
    # Stripe events are usually a few KB but can exceed the global body ceiling.
    # A 413 makes Stripe retry for three days and then disable the endpoint.
    webhook_max_body_bytes: int = 256 * 1024
    # Days of access kept after a payment fails, so a card that expires at
    # bedtime does not take the story away that night.
    subscription_grace_days: int = 3

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

    @property
    def stripe_price_id_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for pair in self.stripe_price_ids.split(","):
            code, _, price_id = pair.partition("=")
            if code.strip() and price_id.strip():
                out[code.strip()] = price_id.strip()
        return out

    @property
    def resolved_billing_provider(self) -> str:
        """Billing is on only when EVERY required value is present.

        Partial configuration resolving to "on" is the dangerous direction: a
        secret key without a webhook secret takes a parent's money and never
        upgrades them, and nothing in our logs looks wrong.
        """
        if self.billing_provider == "auto":
            ready = self.stripe_secret_key and self.stripe_webhook_secret and self.stripe_price_id_map
            return "stripe" if ready else "none"
        return self.billing_provider

    @property
    def billing_enabled(self) -> bool:
        return self.resolved_billing_provider != "none"

    def price_id_for(self, plan_code: str) -> str:
        return self.stripe_price_id_map.get(plan_code, "")

    @property
    def cost_rates_configured(self) -> bool:
        """False means the numbers below are unset, not that generation is free."""
        return any(
            (
                self.price_per_1m_input_tokens_usd,
                self.price_per_1m_output_tokens_usd,
                self.price_per_image_usd,
            )
        )

    def estimate_cost_usd(self, *, input_tokens: int, output_tokens: int, images: int) -> float:
        return round(
            (input_tokens / 1_000_000) * self.price_per_1m_input_tokens_usd
            + (output_tokens / 1_000_000) * self.price_per_1m_output_tokens_usd
            + images * self.price_per_image_usd,
            6,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
