"""Job dispatch: ARQ (Redis) in production, inline asyncio task for keyless dev/tests."""

import asyncio
import logging

from .config import get_settings
from .services.pipeline import run_generation

logger = logging.getLogger(__name__)

_arq_pool = None
_inline_tasks: set[asyncio.Task] = set()  # keep refs so tasks aren't garbage-collected


async def _get_arq_pool():
    global _arq_pool
    if _arq_pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _arq_pool


async def enqueue_generation(story_id: str) -> None:
    settings = get_settings()
    if settings.job_backend == "arq":
        pool = await _get_arq_pool()
        await pool.enqueue_job("generate_story", story_id)
        logger.info("Enqueued story %s on ARQ", story_id)
    else:
        task = asyncio.create_task(run_generation(story_id))
        _inline_tasks.add(task)
        task.add_done_callback(_inline_tasks.discard)
        logger.info("Started inline generation for story %s", story_id)


async def close_job_pool() -> None:
    global _arq_pool
    if _arq_pool is not None:
        await _arq_pool.aclose()
        _arq_pool = None
