"""Story generation pipeline — runs inside the ARQ worker or inline in the API process.

Stages: writing_story -> illustrating (parallel, progress per image) -> done.
Story text failure fails the job; individual image failures degrade gracefully.
"""

import asyncio
import logging

from sqlalchemy import delete, select, update

from ..config import get_settings
from ..db import get_session_factory
from ..models import GenerationEvent, GenerationJob, Story, StoryPage
from ..storage import get_storage
from . import cast as cast_service
from .base import GenerationError, GenerationProvider, StoryRequest, Usage

logger = logging.getLogger(__name__)

_provider: GenerationProvider | None = None


def get_provider() -> GenerationProvider:
    global _provider
    if _provider is None:
        name = get_settings().resolved_provider
        if name == "gemini":
            from .gemini import GeminiProvider

            _provider = GeminiProvider()
        else:
            from .mock import MockProvider

            _provider = MockProvider()
        logger.info("Generation provider: %s", _provider.name)
    return _provider


def reset_provider() -> None:
    """Test helper."""
    global _provider
    _provider = None


async def _record_usage(story_id: str, provider_name: str, usage: Usage) -> None:
    """Attach consumption to this story's ledger entry.

    Best-effort telemetry: a failure here must never fail a story the customer
    already has. Units are stored rather than money, so cost can be recomputed
    for historical rows when rates are set or change.
    """
    try:
        settings = get_settings()
        async with get_session_factory()() as session:
            event = (
                await session.execute(select(GenerationEvent).where(GenerationEvent.story_id == story_id))
            ).scalar_one_or_none()
            if event is None:
                return
            event.provider = provider_name
            event.input_tokens = usage.input_tokens
            event.output_tokens = usage.output_tokens
            event.images = usage.images
            await session.commit()
        cost = settings.estimate_cost_usd(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            images=usage.images,
        )
        logger.info(
            "Generation cost recorded",
            extra={
                "story_id": story_id,
                "provider": provider_name,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "images": usage.images,
                "estimated_cost_usd": cost,
                "rates_configured": settings.cost_rates_configured,
            },
        )
    except Exception as e:
        logger.error("Could not record usage for story %s: %s", story_id, e, exc_info=True)


async def _update_job(job_id: str, **fields) -> None:
    """Write job progress in a fresh short-lived session (safe from parallel tasks)."""
    async with get_session_factory()() as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        await session.commit()


async def run_generation(story_id: str) -> None:
    """Entry point invoked by the job backend. Owns the story/job lifecycle."""
    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        story = await session.get(Story, story_id)
        if story is None:
            logger.error("run_generation: story %s not found", story_id)
            return
        job = (
            await session.execute(select(GenerationJob).where(GenerationJob.story_id == story_id))
        ).scalar_one_or_none()
        if job is None:
            logger.error("run_generation: job for story %s not found", story_id)
            return
        job_id = job.id
        # Read inside the session; the row is detached once it closes.
        story_cast_json = story.cast_json
        req = StoryRequest(
            prompt=story.prompt,
            language=story.language,
            hero_name=story.hero_name,
            max_paragraphs=settings.max_paragraphs,
            cast_json=story.cast_json,
            reading_band=story.reading_band,
        )

    try:
        provider = get_provider()
        await _update_job(job_id, status="running", stage="writing_story")

        draft = await provider.write_story(req)

        # Persist title + page skeletons. Delete any existing pages first so a
        # queue-level retry of this job can't duplicate them.
        async with factory() as session:
            story = await session.get(Story, story_id)
            if story is None:
                return
            story.title = draft.title
            story.status = "generating"
            story.provider = provider.name
            await session.execute(delete(StoryPage).where(StoryPage.story_id == story_id))
            for i, (text, img_prompt) in enumerate(zip(draft.paragraphs, draft.image_prompts, strict=False)):
                session.add(StoryPage(story_id=story_id, position=i, text=text, image_prompt=img_prompt))
            await session.commit()

        # Measure whether every named child actually got to act. Logged, not
        # repaired: a repair call doubles the cost of the most expensive story
        # shape, and there is no live-API data yet to justify that. This is the
        # evidence that would.
        story_cast = cast_service.from_json(story_cast_json)
        gaps = cast_service.coverage_gaps(draft.paragraphs, story_cast)
        if gaps:
            # COUNTS, never names or the band. Log extras are copied verbatim
            # by the JSON formatter and ride along as Sentry breadcrumbs, which
            # send_default_pii=False does not filter — that would put children's
            # first names and reading level into a third-party processor the
            # privacy page does not even list. The counts carry all the evidence
            # needed to decide whether repair is worth building.
            logger.warning(
                "Story sidelined a named child",
                extra={
                    "story_id": story_id,
                    "sidelined_count": len(gaps),
                    "cast_size": len(cast_service.children(story_cast)),
                },
            )

        total = len(draft.paragraphs)
        await _update_job(job_id, stage="illustrating", progress_current=0, progress_total=total)

        storage = get_storage()
        semaphore = asyncio.Semaphore(settings.image_concurrency)
        done_count = 0
        total_usage = draft.usage
        count_lock = asyncio.Lock()

        # image_error is user-visible (owner UI); keep it generic and log the detail.
        GENERIC_IMAGE_ERROR = "The illustration for this page could not be generated."

        async def illustrate_one(position: int, image_prompt: str) -> None:
            nonlocal done_count, total_usage
            async with semaphore:
                image = await provider.illustrate(image_prompt, title=draft.title, position=position)
            url, err = "", ""
            if image.ok:
                try:
                    url = await storage.save_image(
                        image.data, story_id=story_id, position=position, mime=image.mime
                    )
                except Exception as e:
                    logger.error(
                        "Storing image %d for story %s failed: %s", position, story_id, e, exc_info=True
                    )
                    err = GENERIC_IMAGE_ERROR
            else:
                logger.warning("Illustration %d for story %s failed: %s", position, story_id, image.error)
                err = GENERIC_IMAGE_ERROR
            async with factory() as session:
                page = (
                    await session.execute(
                        select(StoryPage).where(
                            StoryPage.story_id == story_id, StoryPage.position == position
                        )
                    )
                ).scalar_one_or_none()
                if page is not None:
                    page.image_url = url
                    page.image_error = err
                    await session.commit()
                elif url:
                    # The page vanished while this illustration was being made:
                    # the account or story was deleted mid-generation. The file
                    # was already written, and save_image recreates the very
                    # directory deletion just removed, so nothing else will ever
                    # reclaim it — the row that would have pointed at it is gone.
                    # On a children's product that is a picture derived from a
                    # child's name sitting on a public media mount forever.
                    logger.info("Story %s disappeared mid-generation; removing orphaned media", story_id)
                    try:
                        await storage.delete_story_media(story_id)
                    except Exception as e:
                        logger.error("Could not remove orphaned media for %s: %s", story_id, e)
            # Progress write stays inside the lock so a slower task can't
            # overwrite a higher count with a lower one. Usage accumulates in
            # the same critical section for the same reason.
            async with count_lock:
                done_count += 1
                total_usage = total_usage + image.usage
                await _update_job(job_id, progress_current=done_count)

        await asyncio.gather(*(illustrate_one(i, p) for i, p in enumerate(draft.image_prompts)))

        # Final sweep: if the story was deleted while the last images were in
        # flight, every write after the endpoint's rmtree is orphaned. Cheap,
        # and it closes the window the per-image check cannot (a save landing
        # after that check but before deletion committed).
        async with factory() as session:
            if await session.get(Story, story_id) is None:
                logger.info("Story %s deleted during generation; sweeping media", story_id)
                try:
                    await storage.delete_story_media(story_id)
                except Exception as e:
                    logger.error("Media sweep for deleted story %s failed: %s", story_id, e)
                return

        await _record_usage(story_id, provider.name, total_usage)

        async with factory() as session:
            story = await session.get(Story, story_id)
            if story is not None:
                story.status = "complete"
                await session.commit()
        await _update_job(
            job_id, status="complete", stage="done", progress_current=total, progress_total=total
        )
        logger.info("Story %s generated (%d pages)", story_id, total)

    except Exception as e:
        user_message = (
            e.user_message
            if isinstance(e, GenerationError)
            else "Something went wrong while creating your story. Please try again."
        )
        logger.error("Generation failed for story %s: %s", story_id, e, exc_info=True)
        async with factory() as session:
            story = await session.get(Story, story_id)
            if story is not None:
                story.status = "failed"
                story.error = user_message
            # The story never reached the reader, so refund the ledger entry:
            # the user must not lose an allowance to our failure.
            await session.execute(
                update(GenerationEvent)
                .where(GenerationEvent.story_id == story_id, GenerationEvent.refunded.is_(False))
                .values(refunded=True, refund_reason="generation_failed")
            )
            await session.commit()
        await _update_job(job_id, status="failed", stage="failed", error=user_message)
