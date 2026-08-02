"""Liveness/readiness for load balancers and compose healthchecks."""

from fastapi import APIRouter
from sqlalchemy import text

from ..config import get_settings
from ..deps import DbSession

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health(db: DbSession):
    settings = get_settings()
    await db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "environment": settings.environment,
        "provider": settings.resolved_provider,
        "job_backend": settings.job_backend,
    }
