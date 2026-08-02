"""ARQ worker entrypoint.

Run with:  arq app.worker.WorkerSettings   (from the backend/ directory, or in the
worker container via docker compose).
"""

import logging

from arq.connections import RedisSettings

from .config import get_settings
from .observability import configure_logging, set_correlation_id
from .services.pipeline import run_generation

configure_logging()
logger = logging.getLogger(__name__)


async def generate_story(ctx: dict, story_id: str) -> None:
    # Correlate every line this job emits with the story it belongs to.
    set_correlation_id(f"story:{story_id[:12]}")
    logger.info("Worker picked up story", extra={"story_id": story_id})
    await run_generation(story_id)


async def startup(ctx: dict) -> None:
    # Ensure tables exist even if the worker starts before the API.
    from .db import init_db

    await init_db()
    logger.info("Worker started", extra={"provider": get_settings().resolved_provider})


async def shutdown(ctx: dict) -> None:
    from .db import dispose_engine

    await dispose_engine()


class WorkerSettings:
    functions = [generate_story]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 8  # stories processed concurrently per worker instance
    job_timeout = 600  # hard cap: a story must finish within 10 minutes
    keep_result = 3600
