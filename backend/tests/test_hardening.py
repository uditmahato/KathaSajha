import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.models import GenerationJob

from .conftest import wait_for_job

pytestmark = pytest.mark.asyncio


async def test_delete_story_removes_media_files(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "The gardener of Mustang"}, headers=auth_headers)
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)

    media_dir = os.path.join(get_settings().media_root, "stories", story_id)
    assert os.path.isdir(media_dir) and os.listdir(media_dir)

    r = await client.delete(f"/api/stories/{story_id}", headers=auth_headers)
    assert r.status_code == 204
    assert not os.path.exists(media_dir)


async def test_stale_running_job_reports_failed(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "A story that gets stuck"}, headers=auth_headers)
    job_id = r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)  # let the inline job finish cleanly first

    # Rewind the job to a long-stale 'running' state, as if the worker died.
    stale = datetime.now(UTC) - timedelta(hours=1)
    async with get_session_factory()() as session:
        job = (await session.execute(select(GenerationJob).where(GenerationJob.id == job_id))).scalar_one()
        job.status = "running"
        job.stage = "illustrating"
        job.created_at = stale
        job.updated_at = stale
        await session.commit()

    r = await client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert "timed out" in body["error"].lower()


async def test_security_headers_present(client):
    r = await client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


async def test_shared_story_hides_internal_fields(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "The singing glacier"}, headers=auth_headers)
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)
    slug = (await client.post(f"/api/stories/{story_id}/share", headers=auth_headers)).json()["share_slug"]

    shared = (await client.get(f"/api/stories/shared/{slug}")).json()
    for page in shared["pages"]:
        assert "image_error" not in page  # internal detail, owner-only


async def test_hero_name_stored_not_mangled_into_prompt(client, auth_headers):
    r = await client.post(
        "/api/stories",
        json={"prompt": "A kite race above the valley", "hero_name": "Raman"},
        headers=auth_headers,
    )
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)
    story = (await client.get(f"/api/stories/{story_id}", headers=auth_headers)).json()
    assert story["prompt"] == "A kite race above the valley"  # no name suffix baked in
    assert "main character" not in story["title"].lower()


async def test_stale_generating_story_selfheals_from_story_read(client, auth_headers):
    """After a page refresh the client loses the job id; reading the story or
    library must still fail-over a dead generation so it can be deleted."""
    r = await client.post("/api/stories", json={"prompt": "The stuck snowman"}, headers=auth_headers)
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)

    stale = datetime.now(UTC) - timedelta(hours=1)
    async with get_session_factory()() as session:
        job = (await session.execute(select(GenerationJob).where(GenerationJob.id == job_id))).scalar_one()
        job.status = "running"
        job.created_at = stale
        job.updated_at = stale
        from app.models import Story

        story = await session.get(Story, story_id)
        story.status = "generating"
        await session.commit()

    story = (await client.get(f"/api/stories/{story_id}", headers=auth_headers)).json()
    assert story["status"] == "failed"
    # ...and it no longer counts against the daily quota
    usage = (await client.get("/api/auth/usage", headers=auth_headers)).json()
    assert usage["stories_today"] < usage["daily_limit"] or usage["remaining_today"] >= 0

    r = await client.delete(f"/api/stories/{story_id}", headers=auth_headers)
    assert r.status_code == 204


async def test_password_over_72_bytes_rejected(client):
    r = await client.post(
        "/api/auth/register",
        json={"email": "long@example.com", "password": "x" * 100},
    )
    assert r.status_code == 422
