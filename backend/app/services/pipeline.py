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
from .base import GenerationError, GenerationProvider, StoryRequest

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
        req = StoryRequest(
            prompt=story.prompt,
            language=story.language,
            hero_name=story.hero_name,
            max_paragraphs=settings.max_paragraphs,
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

        total = len(draft.paragraphs)
        await _update_job(job_id, stage="illustrating", progress_current=0, progress_total=total)

        storage = get_storage()
        semaphore = asyncio.Semaphore(settings.image_concurrency)
        done_count = 0
        count_lock = asyncio.Lock()

        # image_error is user-visible (owner UI); keep it generic and log the detail.
        GENERIC_IMAGE_ERROR = "The illustration for this page could not be generated."

        async def illustrate_one(position: int, image_prompt: str) -> None:
            nonlocal done_count
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
            # Progress write stays inside the lock so a slower task can't
            # overwrite a higher count with a lower one.
            async with count_lock:
                done_count += 1
                await _update_job(job_id, progress_current=done_count)

        await asyncio.gather(*(illustrate_one(i, p) for i, p in enumerate(draft.image_prompts)))

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
