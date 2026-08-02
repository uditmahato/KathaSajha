"""Share links must preview as the story, and must not become an injection point.

The share link is the product's only growth loop, so the preview is a revenue
surface. It is also the one place where model-generated text is rendered into
HTML rather than set as textContent, which makes escaping load-bearing.
"""

import pytest
from sqlalchemy import select

from app.db import get_session_factory
from app.models import Story

from .conftest import wait_for_job

pytestmark = pytest.mark.asyncio


async def _shared_story(client, headers, prompt="The yak who learned to fly"):
    """Create, generate, and share a story. Returns (story_id, slug)."""
    r = await client.post("/api/stories", json={"prompt": prompt}, headers=headers)
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    job = await wait_for_job(client, headers, job_id)
    assert job["status"] == "complete", job
    r = await client.post(f"/api/stories/{story_id}/share", headers=headers)
    assert r.status_code == 200, r.text
    return story_id, r.json()["share_slug"]


async def test_shared_page_carries_the_story_preview(client, auth_headers):
    _, slug = await _shared_story(client, auth_headers)

    page = await client.get(f"/shared/{slug}")
    assert page.status_code == 200
    body = page.text

    assert 'property="og:title"' in body
    assert 'property="og:image"' in body
    assert f"/shared/{slug}" in body
    # The default site-level tags must have been replaced, not appended, or the
    # crawler sees two og:title values and picks the wrong one.
    assert body.count('property="og:title"') == 1
    assert "og:type" in body and 'content="article"' in body


async def test_preview_uses_the_stories_own_title_and_opening(client, auth_headers):
    story_id, slug = await _shared_story(client, auth_headers)

    async with get_session_factory()() as session:
        story = (await session.execute(select(Story).where(Story.id == story_id))).scalar_one()
        title = story.title

    body = (await client.get(f"/shared/{slug}")).text
    assert title in body, "the preview should name this story, not the site"


async def test_a_hostile_title_cannot_inject_markup(client, auth_headers):
    """Titles are model output derived from a user prompt. A prompt-injection
    attempt that lands in the title must not escape the attribute."""
    story_id, slug = await _shared_story(client, auth_headers)

    payload = '"><script>alert(1)</script><meta x="'
    async with get_session_factory()() as session:
        story = (await session.execute(select(Story).where(Story.id == story_id))).scalar_one()
        story.title = payload
        await session.commit()

    body = (await client.get(f"/shared/{slug}")).text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body, "the payload should survive only in escaped form"
    # The attribute must not have been terminated early.
    assert '"><script' not in body


async def test_unknown_slug_still_serves_the_app(client):
    page = await client.get("/shared/does-not-exist")
    assert page.status_code == 200
    # Falls back to the site-level tags rather than erroring or leaking.
    assert 'content="website"' in page.text


async def test_landing_page_keeps_its_default_preview(client):
    body = (await client.get("/")).text
    assert 'property="og:site_name"' in body
    assert 'name="description"' in body
