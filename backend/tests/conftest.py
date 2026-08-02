"""Test fixtures: in-memory-ish SQLite, mock provider, inline jobs, temp media dir."""

import asyncio
import os
import tempfile

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Configure environment BEFORE importing the app.
_tmp = tempfile.mkdtemp(prefix="kathasajha-test-")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{os.path.join(_tmp, 'test.db')}",
        "JOB_BACKEND": "inline",
        "GENERATION_PROVIDER": "mock",
        "RATE_LIMIT_ENABLED": "false",
        "MEDIA_ROOT": os.path.join(_tmp, "media"),
        "SECRET_KEY": "test-secret",
        "FREE_DAILY_STORIES": "3",
        "MAX_PARAGRAPHS": "3",
    }
)

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db import Base, dispose_engine, get_engine, init_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.pipeline import reset_provider  # noqa: E402
from app.storage import reset_storage  # noqa: E402


@pytest_asyncio.fixture()
async def client():
    reset_provider()
    reset_storage()
    # Each test gets an empty schema. Sharing rows between tests hid a real bug:
    # per-user assertions passed while platform-wide counts silently accumulated.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    # Inline generation runs as detached asyncio tasks. Let them finish before
    # tearing the schema down, or a late write races the next test's drop_all.
    from app.jobs import _inline_tasks

    if _inline_tasks:
        await asyncio.wait(set(_inline_tasks), timeout=30)
    await dispose_engine()


@pytest_asyncio.fixture()
async def auth_headers(client: AsyncClient):
    """Registered user's bearer headers."""
    import uuid

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "display_name": "Test User"},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def wait_for_job(client: AsyncClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    """Poll a job until it completes or fails."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        resp = await client.get(f"/api/jobs/{job_id}", headers=headers)
        assert resp.status_code == 200, resp.text
        job = resp.json()
        if job["status"] in ("complete", "failed"):
            return job
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Job {job_id} did not finish: {job}")
        await asyncio.sleep(0.2)
