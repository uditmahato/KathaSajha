"""Story CRUD, generation kickoff, sharing, PDF export."""

import asyncio
import logging
import re
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..deps import CurrentUser, DbSession
from ..errors import GENERATION_FAILED, CodedHTTPException
from ..jobs import enqueue_generation
from ..models import (
    ChildProfile,
    CompanionCharacter,
    GenerationEvent,
    GenerationJob,
    Story,
    StoryPage,
    User,
)
from ..quota import (
    enforce_auth_attempt_limit,
    enforce_burst_limit,
    enforce_daily_quota,
    enforce_global_budget,
    enforce_monthly_quota,
)
from ..routers.jobs import fail_stale_jobs_for_user, fail_story_job_if_stale
from ..schemas import (
    CastMemberOut,
    CreateStoryRequest,
    CreateStoryResponse,
    SharedStoryOut,
    ShareResponse,
    StoryOut,
    StorySummaryOut,
)
from ..services import cast as cast_service
from ..services.pdf import PdfPage, PdfUnavailableError, build_story_pdf
from ..services.reading_level import resolve_band
from ..storage import get_storage
from .auth import _client_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stories", tags=["stories"])


async def _resolve_cast(db, user, body: CreateStoryRequest) -> list[cast_service.CastMember]:
    """Turn selected profile ids into a frozen cast, in the order given.

    Unknown or someone else's ids are a 404 rather than being silently dropped:
    a parent who picked three children and got a story about two would have no
    idea why.
    """
    members: list[cast_service.CastMember] = []
    # De-duplicate while preserving order: the same id twice would otherwise
    # produce "2 heroes of equal importance" naming one child, on a generation
    # the parent has already been charged for.
    body = body.model_copy(
        update={
            "child_ids": list(dict.fromkeys(body.child_ids)),
            "companion_ids": list(dict.fromkeys(body.companion_ids)),
        }
    )
    if body.child_ids:
        rows = (
            (
                await db.execute(
                    select(ChildProfile).where(
                        ChildProfile.id.in_(body.child_ids), ChildProfile.user_id == user.id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_id = {r.id: r for r in rows}
        if len(by_id) != len(set(body.child_ids)):
            raise CodedHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                code="story.child_not_found",
                detail="One of those children was not found",
            )
        for cid in body.child_ids:
            child = by_id[cid]
            members.append(
                cast_service.CastMember(role=cast_service.CHILD, name=child.name, age_band=child.age_band)
            )
    if body.companion_ids:
        rows = (
            (
                await db.execute(
                    select(CompanionCharacter).where(
                        CompanionCharacter.id.in_(body.companion_ids),
                        CompanionCharacter.user_id == user.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        by_id = {r.id: r for r in rows}
        if len(by_id) != len(set(body.companion_ids)):
            raise CodedHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                code="story.companion_not_found",
                detail="One of those characters was not found",
            )
        for cid in body.companion_ids:
            comp = by_id[cid]
            members.append(
                cast_service.CastMember(
                    role=cast_service.COMPANION,
                    name=comp.name,
                    kind=comp.kind,
                    description=comp.description,
                )
            )
    return members


@router.post("", response_model=CreateStoryResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_story(body: CreateStoryRequest, user: CurrentUser, db: DbSession):
    settings = get_settings()
    if len(body.prompt) > settings.max_prompt_chars:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="story.prompt_too_long",
            detail=f"Prompt is too long (max {settings.max_prompt_chars} characters)",
            params={"max": settings.max_prompt_chars},
        )
    await enforce_burst_limit(user)
    await enforce_global_budget(db)
    # Lock the user row so concurrent creates from the same account serialize;
    # otherwise parallel requests all pass the quota count-check (TOCTOU).
    # SQLite compiles FOR UPDATE away, but it is single-writer anyway.
    await db.execute(select(User).where(User.id == user.id).with_for_update())
    # Monthly first: if the month is gone, telling someone their allowance
    # "resets tomorrow" would simply be false.
    await enforce_monthly_quota(db, user)
    await enforce_daily_quota(db, user)

    cast = await _resolve_cast(db, user, body)
    # The youngest selected child sets the reading level for the whole book,
    # because one book is read to all the siblings at once.
    band = resolve_band([m.age_band for m in cast_service.children(cast)])
    story = Story(
        user_id=user.id,
        prompt=body.prompt,
        # Still the single authoritative name for the PDF cover and social
        # previews; the cast snapshot supplements it, never replaces it.
        hero_name=cast_service.hero_name_for(cast, body.hero_name.strip()),
        language=body.language,
        status="pending",
        cast_json=cast_service.to_json(cast),
        reading_band=band,
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
        story.error_code = GENERATION_FAILED
        job.status = "failed"
        job.stage = "failed"
        job.error = story.error
        job.error_code = story.error_code
        # Nothing was generated, so do not charge the user for it.
        event.refunded = True
        event.refund_reason = "enqueue_failed"
        await db.commit()
        raise CodedHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="story.service_unavailable",
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
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND, code="story.not_found", detail="Story not found"
        )
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
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            code="story.shared_not_found",
            detail="Shared story not found",
        )
    return story


# Rendering is CPU-bound thread work. The Redis limiters fail open when Redis
# is down, so this semaphore is the line that holds regardless: it bounds
# concurrent renders process-wide for both the owner and the public endpoint.
_PDF_RENDER_LIMIT = asyncio.Semaphore(4)


async def _story_pdf_response(story: Story) -> Response:
    """Render a story as a storybook PDF and wrap it for download."""
    storage = get_storage()
    pdf_pages = []
    for page in story.pages:
        image = await storage.load_image(page.image_url) if page.image_url else None
        pdf_pages.append(PdfPage(text=page.text, image=image))
    try:
        async with _PDF_RENDER_LIMIT:
            data = await asyncio.to_thread(
                build_story_pdf,
                title=story.title or "A KathaSajha story",
                language=story.language,
                hero_name=story.hero_name,
                pages=pdf_pages,
                created_at=story.created_at,
            )
    except PdfUnavailableError as e:
        logger.error("PDF rendering unavailable: %s", e)
        raise CodedHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="story.pdf_unavailable",
            detail="PDF export is temporarily unavailable. Please try again later.",
        ) from e
    title = story.title or "story"
    # ASCII fallback plus RFC 5987 UTF-8 name, so Devanagari titles survive.
    ascii_name = re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_")[:60] or "story"
    # safe="" so '/' is percent-encoded too; quote() already leaves nothing
    # that could split the header, but an unescaped slash is invalid RFC 5987.
    disposition = (
        f"attachment; filename=\"{ascii_name}.pdf\"; filename*=UTF-8''{quote(title[:80], safe='')}.pdf"
    )
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )


@router.get("/shared/{slug}/pdf")
async def shared_story_pdf(slug: str, db: DbSession, request: Request):
    """The same PDF, for people a link was shared WITH — grandparents have no
    account. Unauthenticated CPU work, so it is rate limited.

    Keyed by slug AND client IP: behind a proxy with TRUSTED_PROXY_IPS unset,
    every caller shares the proxy's address, and an IP-only key would give the
    whole world one 10-per-10-minutes bucket per endpoint. Slug+IP degrades to
    per-book-per-proxy, which a family link survives.
    """
    await enforce_auth_attempt_limit("shared-pdf", f"{slug}:{_client_ip(request)}")
    story = (
        await db.execute(
            select(Story)
            .where(Story.share_slug == slug, Story.status == "complete")
            .options(selectinload(Story.pages))
        )
    ).scalar_one_or_none()
    if story is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            code="story.shared_not_found",
            detail="Shared story not found",
        )
    return await _story_pdf_response(story)


@router.get("/{story_id}/pdf")
async def story_pdf(story_id: str, user: CurrentUser, db: DbSession):
    # Authenticated but still unmetered CPU; a modest per-user window plus the
    # render semaphore keeps one account from monopolising the process.
    await enforce_auth_attempt_limit("owner-pdf", user.id)
    story = await _load_owned_story(db, user, story_id)
    if story.status != "complete":
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            code="story.pdf_not_ready",
            detail="The story is still being created; the book can be saved once it finishes.",
        )
    return await _story_pdf_response(story)


@router.get("/{story_id}", response_model=StoryOut)
async def get_story(story_id: str, user: CurrentUser, db: DbSession):
    story = await _load_owned_story(db, user, story_id)
    await fail_story_job_if_stale(db, story)
    out = StoryOut.model_validate(story)
    # Names only. The age band steers generation and is never returned.
    out.cast = [
        CastMemberOut(role=m.role, name=m.name, kind=m.kind) for m in cast_service.from_json(story.cast_json)
    ]
    return out


@router.post("/{story_id}/share", response_model=ShareResponse)
async def share_story(story_id: str, user: CurrentUser, db: DbSession, request: Request):
    story = await _load_owned_story(db, user, story_id)
    if story.status != "complete":
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            code="story.share_not_complete",
            detail="Only completed stories can be shared",
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
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            code="story.delete_while_generating",
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
