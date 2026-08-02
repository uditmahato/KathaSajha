"""Quota accounting must survive deletion, and the platform must have a spend ceiling.

These guard the two ways a real API key turns into an unbounded bill.
"""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.models import GenerationEvent

from .conftest import wait_for_job

pytestmark = pytest.mark.asyncio


async def _create(client, headers, prompt="A yak who paints the sky"):
    return await client.post("/api/stories", json={"prompt": prompt}, headers=headers)


async def test_deleting_a_story_does_not_refund_quota(client, auth_headers):
    """The exploit: create 3, delete 3, generate forever."""
    ids = []
    for i in range(3):  # FREE_DAILY_STORIES=3 in the test env
        r = await _create(client, auth_headers, f"Story number {i} about a kind snow leopard")
        assert r.status_code == 202, r.text
        ids.append((r.json()["story_id"], r.json()["job_id"]))
    for _sid, jid in ids:
        await wait_for_job(client, auth_headers, jid)

    # Delete every one of them.
    for sid, _jid in ids:
        assert (await client.delete(f"/api/stories/{sid}", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/stories", headers=auth_headers)).json() == []

    # Quota must still be exhausted: the ledger outlives the stories.
    blocked = await _create(client, auth_headers, "One more after deleting everything")
    assert blocked.status_code == 429, "deleting stories refunded quota (unbounded spend)"

    usage = (await client.get("/api/auth/usage", headers=auth_headers)).json()
    assert usage["stories_today"] == 3
    assert usage["remaining_today"] == 0


async def test_ledger_survives_story_deletion_with_null_story_id(client, auth_headers):
    r = await _create(client, auth_headers, "A story that will be deleted")
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)
    await client.delete(f"/api/stories/{story_id}", headers=auth_headers)

    async with get_session_factory()() as session:
        events = (await session.execute(select(GenerationEvent))).scalars().all()
        assert len(events) == 1, "the charge record must survive"
        assert events[0].story_id is None, "story reference should be nulled, not cascade-deleted"
        assert events[0].refunded is False


async def test_failed_generation_is_refunded(client, auth_headers, monkeypatch):
    """Users must not lose an allowance to our failures."""
    from app.services import pipeline
    from app.services.base import GenerationError

    async def boom(self, req):
        raise GenerationError("provider exploded", user_message="We could not write that story.")

    pipeline.reset_provider()
    monkeypatch.setattr("app.services.mock.MockProvider.write_story", boom)

    r = await _create(client, auth_headers, "This one will fail on purpose")
    job = await wait_for_job(client, auth_headers, r.json()["job_id"])
    assert job["status"] == "failed"

    usage = (await client.get("/api/auth/usage", headers=auth_headers)).json()
    assert usage["stories_today"] == 0, "a failed generation must not consume quota"

    async with get_session_factory()() as session:
        event = (await session.execute(select(GenerationEvent))).scalars().one()
        assert event.refunded is True
        assert event.refund_reason == "generation_failed"


async def test_global_budget_blocks_when_ceiling_reached(client, auth_headers, monkeypatch):
    """Per-user limits bound one account; this bounds the bill."""
    settings = get_settings()
    monkeypatch.setattr(settings, "global_daily_generation_limit", 2, raising=False)

    first = await _create(client, auth_headers, "Global budget story one")
    second = await _create(client, auth_headers, "Global budget story two")
    assert first.status_code == 202
    assert second.status_code == 202
    for r in (first, second):
        await wait_for_job(client, auth_headers, r.json()["job_id"])

    third = await _create(client, auth_headers, "Global budget story three")
    assert third.status_code == 503
    assert "capacity" in third.json()["detail"].lower()


async def test_global_budget_fails_closed_when_it_cannot_be_evaluated(client, auth_headers, monkeypatch):
    """A broken ceiling check must stop spending, not wave it through."""
    import app.quota as quota_module

    settings = get_settings()
    monkeypatch.setattr(settings, "global_daily_generation_limit", 100, raising=False)

    original = quota_module.select

    def exploding_select(*args, **kwargs):
        raise RuntimeError("database is unhappy")

    monkeypatch.setattr(quota_module, "select", exploding_select)
    try:
        r = await _create(client, auth_headers, "Should be refused, not allowed")
        assert r.status_code == 503, "budget check failure must fail closed"
    finally:
        monkeypatch.setattr(quota_module, "select", original)


async def test_unknown_plan_gets_free_allowance_not_unlimited(client, auth_headers):
    """A typo or a stale plan string must never grant more than free."""
    from app.models import User
    from app.quota import daily_limit_for

    async with get_session_factory()() as session:
        user = (await session.execute(select(User))).scalars().first()
        user.plan = "enterprise-typo"
        limit = daily_limit_for(user)
    assert limit == get_settings().free_daily_stories
