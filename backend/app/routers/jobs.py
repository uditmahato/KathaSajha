"""Job progress polling + stale-job failover shared with the stories router."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentUser, DbSession
from ..errors import GENERATION_STALLED, CodedHTTPException
from ..models import GenerationEvent, GenerationJob, Story
from ..schemas import JobOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

STALE_AFTER = timedelta(minutes=15)


def _is_stale(job: GenerationJob) -> bool:
    if job.status not in ("queued", "running"):
        return False
    last_touch = job.updated_at or job.created_at
    if last_touch.tzinfo is None:  # SQLite returns naive datetimes
        last_touch = last_touch.replace(tzinfo=UTC)
    return datetime.now(UTC) - last_touch > STALE_AFTER


async def fail_job_if_stale(db: AsyncSession, job: GenerationJob) -> bool:
    """A queued/running job with no heartbeat for STALE_AFTER means the worker
    died mid-flight; fail it so clients stop polling and the user can retry.
    Returns True if the job was failed."""
    if not _is_stale(job):
        return False
    job.status = "failed"
    job.stage = "failed"
    job.error = "Generation timed out. Please try again."
    job.error_code = GENERATION_STALLED
    story = await db.get(Story, job.story_id)
    if story is not None and story.status in ("pending", "generating"):
        story.status = "failed"
        story.error = job.error
        story.error_code = job.error_code
    # Our failure, so the user keeps the allowance.
    await db.execute(
        update(GenerationEvent)
        .where(GenerationEvent.story_id == job.story_id, GenerationEvent.refunded.is_(False))
        .values(refunded=True, refund_reason="stale_timeout")
    )
    await db.commit()
    return True


async def fail_stale_jobs_for_user(db: AsyncSession, user_id: str) -> None:
    """Bulk version of the stale-job failover for the library listing.

    Two statements and one commit regardless of library size, instead of a
    SELECT plus a COMMIT per story.
    """
    cutoff = datetime.now(UTC) - STALE_AFTER
    stale_story_ids = (
        (
            await db.execute(
                select(GenerationJob.story_id)
                .join(Story, Story.id == GenerationJob.story_id)
                .where(
                    Story.user_id == user_id,
                    GenerationJob.status.in_(["queued", "running"]),
                    GenerationJob.updated_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    if not stale_story_ids:
        return
    message = "Generation timed out. Please try again."
    await db.execute(
        update(GenerationJob)
        .where(GenerationJob.story_id.in_(stale_story_ids))
        .values(status="failed", stage="failed", error=message, error_code=GENERATION_STALLED)
    )
    await db.execute(
        update(Story)
        .where(Story.id.in_(stale_story_ids), Story.status.in_(["pending", "generating"]))
        .values(status="failed", error=message, error_code=GENERATION_STALLED)
    )
    # Our failure, so refund the ledger entries too.
    await db.execute(
        update(GenerationEvent)
        .where(GenerationEvent.story_id.in_(stale_story_ids), GenerationEvent.refunded.is_(False))
        .values(refunded=True, refund_reason="stale_timeout")
    )
    await db.commit()
    logger.warning("Failed %d stale generation(s)", len(stale_story_ids), extra={"user_id": user_id})


async def fail_story_job_if_stale(db: AsyncSession, story: Story) -> None:
    """Same failover, but entered from a story read (e.g. after a page refresh
    the client no longer knows the job id)."""
    if story.status not in ("pending", "generating"):
        return
    job = (
        await db.execute(select(GenerationJob).where(GenerationJob.story_id == story.id))
    ).scalar_one_or_none()
    if job is not None:
        await fail_job_if_stale(db, job)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, user: CurrentUser, db: DbSession):
    job = (
        await db.execute(
            select(GenerationJob)
            .join(Story, Story.id == GenerationJob.story_id)
            .where(GenerationJob.id == job_id, Story.user_id == user.id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND, code="job.not_found", detail="Job not found"
        )
    await fail_job_if_stale(db, job)
    return job
