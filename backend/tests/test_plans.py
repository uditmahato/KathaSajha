"""The monetization surface: what a tier grants, and demand capture at the wall."""

import pytest
from sqlalchemy import select

from app.db import get_session_factory
from app.models import PlanInterest

from .conftest import wait_for_job

pytestmark = pytest.mark.asyncio


async def test_plans_are_public_and_honest(client):
    r = await client.get("/api/plans")
    assert r.status_code == 200
    plans = {p["code"]: p for p in r.json()}
    assert "free" in plans and "plus" in plans

    free, plus = plans["free"], plans["plus"]
    assert free["daily_stories"] == 3  # matches FREE_DAILY_STORIES in the test env
    assert free["purchasable"] is True
    # Plus cannot be bought yet; the API must not claim otherwise.
    assert plus["purchasable"] is False
    assert plus["daily_stories"] > free["daily_stories"]
    assert plus["monthly_price_usd"] > 0
    assert plus["features"]


async def test_plans_marks_the_signed_in_users_current_plan(client, auth_headers):
    r = await client.get("/api/plans", headers=auth_headers)
    plans = {p["code"]: p for p in r.json()}
    assert plans["free"]["is_current"] is True
    assert plans["plus"]["is_current"] is False


async def test_hitting_the_wall_flags_quota_exhausted_for_the_upgrade_prompt(client, auth_headers):
    jobs = []
    for i in range(3):
        r = await client.post(
            "/api/stories", json={"prompt": f"Wall story {i} about a brave duckling"}, headers=auth_headers
        )
        assert r.status_code == 202
        jobs.append(r.json()["job_id"])
    for j in jobs:
        await wait_for_job(client, auth_headers, j)

    blocked = await client.post(
        "/api/stories", json={"prompt": "The one that hits the wall"}, headers=auth_headers
    )
    assert blocked.status_code == 429
    # The client distinguishes the wall from a validation error by this header.
    assert blocked.headers.get("X-Quota-Exhausted") == "daily"


async def test_interest_is_recorded_once_per_user_and_plan(client, auth_headers):
    first = await client.post(
        "/api/plans/interest", json={"plan_code": "plus", "source": "quota_wall"}, headers=auth_headers
    )
    assert first.status_code == 200
    assert "plus" in first.json()["message"].lower()

    second = await client.post(
        "/api/plans/interest", json={"plan_code": "plus", "source": "pricing_page"}, headers=auth_headers
    )
    assert second.status_code == 200  # idempotent, still friendly

    async with get_session_factory()() as session:
        rows = (await session.execute(select(PlanInterest))).scalars().all()
        assert len(rows) == 1, "interest must not duplicate per user and plan"
        assert rows[0].plan_code == "plus"
        assert rows[0].source == "quota_wall", "the first (highest-intent) source is kept"


async def test_interest_requires_auth_and_rejects_free(client, auth_headers):
    assert (await client.post("/api/plans/interest", json={"plan_code": "plus"})).status_code == 401
    r = await client.post("/api/plans/interest", json={"plan_code": "free"}, headers=auth_headers)
    assert r.status_code == 400


async def test_usage_reports_the_plan(client, auth_headers):
    r = await client.get("/api/auth/usage", headers=auth_headers)
    assert r.json()["plan"] == "free"
