"""Cost telemetry and the allowance that bounds it.

The free tier previously permitted 3 stories a day with no monthly bound, so a
fully-active free account could take ~90 stories and ~450 illustrations a month
against a $6 paid tier. These tests hold the bound in place and keep the ledger
recording what each generation actually consumed.
"""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.models import GenerationEvent
from app.services.base import Usage

from .conftest import wait_for_job

# No module-level asyncio mark: this file mixes sync and async tests, and
# pytest.ini already runs in asyncio auto mode.


# --- Cost arithmetic ---------------------------------------------------------


def test_usage_adds_componentwise():
    total = Usage(input_tokens=100, output_tokens=20, images=1) + Usage(
        input_tokens=5, output_tokens=3, images=2
    )
    assert (total.input_tokens, total.output_tokens, total.images) == (105, 23, 3)


def test_cost_is_zero_until_rates_are_configured(monkeypatch):
    """Unset rates must read as an obvious zero, never as an invented price."""
    s = get_settings()
    monkeypatch.setattr(s, "price_per_1m_input_tokens_usd", 0.0, raising=False)
    monkeypatch.setattr(s, "price_per_1m_output_tokens_usd", 0.0, raising=False)
    monkeypatch.setattr(s, "price_per_image_usd", 0.0, raising=False)

    assert s.cost_rates_configured is False
    assert s.estimate_cost_usd(input_tokens=10_000, output_tokens=5_000, images=5) == 0.0


def test_cost_uses_configured_rates(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "price_per_1m_input_tokens_usd", 0.30, raising=False)
    monkeypatch.setattr(s, "price_per_1m_output_tokens_usd", 2.50, raising=False)
    monkeypatch.setattr(s, "price_per_image_usd", 0.04, raising=False)

    assert s.cost_rates_configured is True
    # 1M in @ 0.30 + 1M out @ 2.50 + 5 images @ 0.04 = 0.30 + 2.50 + 0.20
    cost = s.estimate_cost_usd(input_tokens=1_000_000, output_tokens=1_000_000, images=5)
    assert cost == pytest.approx(3.00)


def test_five_illustrations_a_story_is_the_cost_driver(monkeypatch):
    """The arithmetic that motivated the repricing, pinned so it cannot regress
    quietly: images dominate, and the old free tier permitted 450 a month."""
    s = get_settings()
    monkeypatch.setattr(s, "price_per_1m_input_tokens_usd", 0.30, raising=False)
    monkeypatch.setattr(s, "price_per_1m_output_tokens_usd", 2.50, raising=False)
    monkeypatch.setattr(s, "price_per_image_usd", 0.04, raising=False)

    one_story = s.estimate_cost_usd(input_tokens=800, output_tokens=1_200, images=5)
    old_free_tier_month = one_story * 90  # 3/day
    new_free_tier_month = one_story * s.free_monthly_stories

    assert old_free_tier_month > 6.00, "the old free tier could exceed the paid price"
    assert new_free_tier_month < 6.00, "the new one must sit under it"


# --- Ledger telemetry --------------------------------------------------------


async def test_generation_records_provider_and_units(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "A yak counts stars"}, headers=auth_headers)
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)

    async with get_session_factory()() as session:
        event = (
            await session.execute(select(GenerationEvent).where(GenerationEvent.story_id == story_id))
        ).scalar_one()

    assert event.provider == "mock"
    # The mock is genuinely free, so zeros here are correct rather than missing.
    assert event.input_tokens == 0
    assert event.output_tokens == 0
    assert event.images == 0


# --- Monthly allowance -------------------------------------------------------


async def test_monthly_allowance_is_reported(client, auth_headers):
    u = (await client.get("/api/auth/usage", headers=auth_headers)).json()
    assert u["monthly_limit"] == get_settings().free_monthly_stories
    assert u["remaining_this_month"] == u["monthly_limit"] - u["stories_this_month"]


async def test_monthly_wall_blocks_and_is_labelled(client, auth_headers, monkeypatch):
    """With the monthly allowance spent, creation stops even when today's daily
    allowance is untouched, and the response is tagged so the UI can answer it
    with an upgrade offer instead of a red error."""
    monkeypatch.setattr(get_settings(), "free_monthly_stories", 2, raising=False)

    for i in range(2):
        r = await client.post("/api/stories", json={"prompt": f"Story {i}"}, headers=auth_headers)
        assert r.status_code == 202, r.text
        await wait_for_job(client, auth_headers, r.json()["job_id"])

    blocked = await client.post("/api/stories", json={"prompt": "One too many"}, headers=auth_headers)
    assert blocked.status_code == 429
    assert blocked.headers.get("X-Quota-Exhausted") == "monthly"
    assert "month" in blocked.json()["detail"].lower()


async def test_monthly_message_does_not_promise_a_tomorrow_reset(client, auth_headers, monkeypatch):
    """Checking daily first would tell someone out of monthly allowance that it
    resets tomorrow, which is false."""
    monkeypatch.setattr(get_settings(), "free_monthly_stories", 1, raising=False)

    r = await client.post("/api/stories", json={"prompt": "Only one"}, headers=auth_headers)
    await wait_for_job(client, auth_headers, r.json()["job_id"])

    blocked = await client.post("/api/stories", json={"prompt": "Blocked"}, headers=auth_headers)
    assert blocked.status_code == 429
    assert "tomorrow" not in blocked.json()["detail"].lower()


async def test_refunded_generations_do_not_consume_the_month(client, auth_headers, monkeypatch):
    """Our failures must not eat someone's monthly allowance."""
    monkeypatch.setattr(get_settings(), "free_monthly_stories", 2, raising=False)

    r = await client.post("/api/stories", json={"prompt": "Will be refunded"}, headers=auth_headers)
    story_id = r.json()["story_id"]
    await wait_for_job(client, auth_headers, r.json()["job_id"])

    async with get_session_factory()() as session:
        event = (
            await session.execute(select(GenerationEvent).where(GenerationEvent.story_id == story_id))
        ).scalar_one()
        event.refunded = True
        event.refund_reason = "generation_failed"
        await session.commit()

    u = (await client.get("/api/auth/usage", headers=auth_headers)).json()
    assert u["stories_this_month"] == 0, "a refunded generation must not count"
