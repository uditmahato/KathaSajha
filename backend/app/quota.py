"""Per-user quotas (DB-counted, source of truth) and burst rate limiting (Redis)."""

import logging
from datetime import UTC, datetime, time

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import GenerationEvent, User
from .plans import daily_stories_for, effective_plan_code, monthly_stories_for

logger = logging.getLogger(__name__)

# Entitlements live in plans.py so the pricing page and the enforcement code can
# never drift apart. An unknown plan string resolves to free, never to more.

_redis = None


async def _get_redis():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def effective_plan_for(user: User) -> str:
    """The single place entitlement is decided. Every limit and every piece of
    plan-facing copy must go through here, or the UI will say "Plus" while the
    quota enforces free."""
    return effective_plan_code(user.plan, user.plan_expires_at)


def daily_limit_for(user: User) -> int:
    return daily_stories_for(effective_plan_for(user))


def monthly_limit_for(user: User) -> int:
    return monthly_stories_for(effective_plan_for(user))


def _utc_day_start() -> datetime:
    return datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)


def _utc_month_start() -> datetime:
    today = datetime.now(UTC).date()
    return datetime.combine(today.replace(day=1), time.min, tzinfo=UTC)


async def stories_created_today(db: AsyncSession, user: User) -> int:
    """Count from the append-only ledger, not from `stories`.

    Counting story rows was exploitable: a user could create three stories,
    delete them, and generate forever. Refunded events (our failures) are
    excluded, so users are never charged for our errors.
    """
    result = await db.execute(
        select(func.count(GenerationEvent.id)).where(
            GenerationEvent.user_id == user.id,
            GenerationEvent.created_at >= _utc_day_start(),
            GenerationEvent.refunded.is_(False),
        )
    )
    return int(result.scalar_one())


async def stories_created_this_month(db: AsyncSession, user: User) -> int:
    """Same ledger, calendar-month window. This is the bound that actually caps
    what an account can cost; the daily figure only smooths bursts."""
    result = await db.execute(
        select(func.count(GenerationEvent.id)).where(
            GenerationEvent.user_id == user.id,
            GenerationEvent.created_at >= _utc_month_start(),
            GenerationEvent.refunded.is_(False),
        )
    )
    return int(result.scalar_one())


async def enforce_daily_quota(db: AsyncSession, user: User) -> None:
    limit = daily_limit_for(user)
    used = await stories_created_today(db, user)
    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You've used all {limit} stories on your {effective_plan_for(user)} plan today. "
                "Your allowance resets tomorrow."
            ),
            headers={"X-Quota-Exhausted": "daily"},
        )


async def enforce_monthly_quota(db: AsyncSession, user: User) -> None:
    limit = monthly_limit_for(user)
    used = await stories_created_this_month(db, user)
    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You've used all {limit} stories on your {effective_plan_for(user)} plan this month. "
                "Your allowance resets at the start of next month."
            ),
            # Same signal as the daily wall: the frontend answers it with an
            # upgrade offer rather than a red error line.
            headers={"X-Quota-Exhausted": "monthly"},
        )


async def enforce_global_budget(db: AsyncSession) -> None:
    """Platform-wide daily ceiling on paid generations.

    Per-user limits bound one account; nothing bounds the number of accounts.
    Without this, scripted signups convert directly into an unbounded bill.
    This fails CLOSED: if the ceiling cannot be evaluated, generation stops,
    because the cost of a false refusal is one annoyed user and the cost of a
    false allowance is unbounded money.
    """
    settings = get_settings()
    if settings.global_daily_generation_limit <= 0:
        return  # explicitly disabled
    try:
        total = int(
            (
                await db.execute(
                    select(func.count(GenerationEvent.id)).where(
                        GenerationEvent.created_at >= _utc_day_start(),
                        GenerationEvent.refunded.is_(False),
                    )
                )
            ).scalar_one()
        )
    except Exception as e:
        logger.error("Global budget check failed; refusing generation: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Story creation is briefly paused. Please try again in a few minutes.",
        ) from e

    limit = settings.global_daily_generation_limit
    if total >= limit:
        # Loud: this is either real growth worth celebrating or an attack worth stopping.
        logger.error(
            "GLOBAL DAILY GENERATION CEILING REACHED",
            extra={"generations_today": total, "limit": limit},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="KathaSajha is at capacity for today. Please try again tomorrow.",
        )
    if total >= int(limit * 0.8):
        logger.warning(
            "Global daily generation budget above 80 percent",
            extra={"generations_today": total, "limit": limit},
        )


async def enforce_burst_limit(user: User) -> None:
    """Redis fixed-window limiter for request bursts. Fails open if Redis is down
    (the DB-backed daily quota still holds the real line)."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    try:
        r = await _get_redis()
        hour = datetime.now(UTC).strftime("%Y%m%d%H")
        key = f"rl:gen:{user.id}:{hour}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 3900)
        if count > settings.rate_limit_generate_per_hour:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You're creating stories too quickly. Please wait a little while.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Rate limiter unavailable, failing open: %s", e)


async def enforce_auth_attempt_limit(scope: str, identifier: str) -> None:
    """Throttle credential-guessing on the auth endpoints, keyed by client IP and
    by target email. Unlike generation limits this protects accounts, not cost,
    so it applies before any password work is done."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    try:
        r = await _get_redis()
        window = datetime.now(UTC).strftime("%Y%m%d%H%M")[:-1]  # 10-minute bucket
        key = f"rl:auth:{scope}:{identifier}:{window}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 900)
        if count > settings.rate_limit_auth_per_10min:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait a few minutes and try again.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Auth rate limiter unavailable, failing open: %s", e)


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
