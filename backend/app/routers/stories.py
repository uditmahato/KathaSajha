"""Story CRUD, generation kickoff, sharing."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..deps import CurrentUser, DbSession
from ..jobs import enqueue_generation
from ..models import GenerationEvent, GenerationJob, Story, StoryPage, User
from ..quota import enforce_burst_limit, enforce_daily_quota, enforce_global_budget
from ..routers.jobs import fail_stale_jobs_for_user, fail_story_job_if_stale
from ..schemas import (
    CreateStoryRequest,
    CreateStoryResponse,
    SharedStoryOut,
    ShareResponse,
    StoryOut,
    StorySummaryOut,
)
from ..storage import get_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stories", tags=["stories"])


@router.post("", response_model=CreateStoryResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_story(body: CreateStoryRequest, user: CurrentUser, db: DbSession):
    settings = get_settings()
    if len(body.prompt) > settings.max_prompt_chars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Prompt is too long (max {settings.max_prompt_chars} characters)",
        )
    await enforce_burst_limit(user)
    await enforce_global_budget(db)
    # Lock the user row so concurrent creates from the same account serialize;
    # otherwise parallel requests all pass the quota count-check (TOCTOU).
    # SQLite compiles FOR UPDATE away, but it is single-writer anyway.
    await db.execute(select(User).where(User.id == user.id).with_for_update())
    await enforce_daily_quota(db, user)

    story = Story(
        user_id=user.id,
        prompt=body.prompt,
        hero_name=body.hero_name.strip(),
        language=body.language,
        status="pending",
    )
    db.add(story)
    await db.flush()
    job = GenerationJob(story_id=story.id)
    db.add(job)
    # The ledger entry is what quota counts. It is written in the same
    # transaction as the story so a generation can never happen unbilled.
    event = GenerationEvent(user_id=user.id, story_id=story.id)
    db.add(event)
    await db.commit()

    try:
        await enqueue_generation(story.id)
    except Exception as e:
        logger.error("Failed to enqueue story %s: %s", story.id, e, exc_info=True)
        story.status = "failed"
        story.error = "Could not start generation. Please try again in a moment."
        job.status = "failed"
        job.stage = "failed"
        job.error = story.error
        # Nothing was generated, so do not charge the user for it.
        event.refunded = True
        event.refund_reason = "enqueue_failed"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Story service is briefly unavailable. Please try again.",
        ) from e
    return CreateStoryResponse(story_id=story.id, job_id=job.id)


@router.get("", response_model=list[StorySummaryOut])
async def list_stories(user: CurrentUser, db: DbSession, limit: int = 50, offset: int = 0):
    """The home screen, polled every few seconds while anything is generating.

    Deliberately three statements regardless of library size: fail stale jobs in
    bulk, fetch the story rows, fetch only the cover image URLs. It previously
    hydrated every page's full paragraph text just to pick one cover, then ran a
    SELECT and a COMMIT per story.
    """
    limit = min(max(limit, 1), 100)

    await fail_stale_jobs_for_user(db, user.id)

    rows = (
        (
            await db.execute(
                select(Story)
                .where(Story.user_id == user.id)
                .order_by(Story.created_at.desc())
                .limit(limit)
                .offset(max(offset, 0))
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []

    # One extra query for covers: just the URL column, only illustrated pages,
    # lowest position first so the first row per story is the cover.
    story_ids = [s.id for s in rows]
    cover_rows = (
        await db.execute(
            select(StoryPage.story_id, StoryPage.image_url)
            .where(StoryPage.story_id.in_(story_ids), StoryPage.image_url != "")
            .order_by(StoryPage.story_id, StoryPage.position)
        )
    ).all()
    covers: dict[str, str] = {}
    for story_id, image_url in cover_rows:
        covers.setdefault(story_id, image_url)

    out = []
    for s in rows:
        item = StorySummaryOut.model_validate(s)
        item.cover_image_url = covers.get(s.id, "")
        out.append(item)
    return out


async def _load_owned_story(db, user, story_id: str) -> Story:
    story = (
        await db.execute(
            select(Story)
            .where(Story.id == story_id, Story.user_id == user.id)
            .options(selectinload(Story.pages))
        )
    ).scalar_one_or_none()
    if story is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Story not found")
    return story


@router.get("/shared/{slug}", response_model=SharedStoryOut)
async def get_shared_story(slug: str, db: DbSession):
    """Public, unauthenticated view for share links."""
    story = (
        await db.execute(
            select(Story)
            .where(Story.share_slug == slug, Story.status == "complete")
            .options(selectinload(Story.pages))
        )
    ).scalar_one_or_none()
    if story is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared story not found")
    return story


@router.get("/{story_id}", response_model=StoryOut)
async def get_story(story_id: str, user: CurrentUser, db: DbSession):
    story = await _load_owned_story(db, user, story_id)
    await fail_story_job_if_stale(db, story)
    return story


@router.post("/{story_id}/share", response_model=ShareResponse)
async def share_story(story_id: str, user: CurrentUser, db: DbSession, request: Request):
    story = await _load_owned_story(db, user, story_id)
    if story.status != "complete":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only completed stories can be shared"
        )
    if not story.share_slug:
        story.share_slug = uuid.uuid4().hex[:12]
        await db.commit()
    base = str(request.base_url).rstrip("/")
    return ShareResponse(share_slug=story.share_slug, share_url=f"{base}/shared/{story.share_slug}")


@router.delete("/{story_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def unshare_story(story_id: str, user: CurrentUser, db: DbSession):
    story = await _load_owned_story(db, user, story_id)
    story.share_slug = None
    await db.commit()


@router.delete("/{story_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_story(story_id: str, user: CurrentUser, db: DbSession):
    story = await _load_owned_story(db, user, story_id)
    if story.status in ("pending", "generating"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Story is still generating; wait for it to finish before deleting",
        )
    await db.delete(story)
    await db.commit()
    try:
        await get_storage().delete_story_media(story_id)
    except Exception as e:
        # Media cleanup is best-effort; the DB row is already gone. Log it so
        # orphaned files are discoverable rather than silently accumulating.
        logger.warning("Could not delete media for story %s: %s", story_id, e)
