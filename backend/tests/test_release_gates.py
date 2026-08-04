"""Release gates: deletion, production guards, legal surface.

Account deletion is the legal path for a children's product, so these tests
assert on what actually disappears — and on the one thing that must NOT:
the generation ledger, which the platform-wide cost ceiling counts.
"""

import os

import pytest
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.main import validate_production_settings
from app.models import GenerationEvent, Story, User

from .conftest import wait_for_job

# No module-level asyncio mark: sync guard tests mixed with async flows,
# and pytest.ini already runs in asyncio auto mode.


async def _register(client, email):
    r = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "display_name": "Del"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _story(client, headers, prompt="A tale to be deleted"):
    r = await client.post("/api/stories", json={"prompt": prompt}, headers=headers)
    ids = r.json()
    await wait_for_job(client, headers, ids["job_id"])
    return ids["story_id"]


# --- Deletion ----------------------------------------------------------------


async def test_deletion_requires_the_password(client):
    headers = await _register(client, "del-auth@example.com")
    r = await client.request("DELETE", "/api/auth/me", json={"password": "not-the-password"}, headers=headers)
    assert r.status_code == 401
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


async def test_deletion_removes_account_stories_and_media(client):
    headers = await _register(client, "del-full@example.com")
    story_id = await _story(client, headers)
    media_dir = os.path.join(get_settings().media_root, "stories", story_id)
    assert os.path.isdir(media_dir) and os.listdir(media_dir)

    r = await client.request("DELETE", "/api/auth/me", json={"password": "password123"}, headers=headers)
    assert r.status_code == 200, r.text

    # The session is dead, the story rows are gone, the images are gone.
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    assert not os.path.exists(media_dir)
    async with get_session_factory()() as session:
        assert (await session.execute(select(Story).where(Story.id == story_id))).scalar_one_or_none() is None
        assert (
            await session.execute(select(User).where(User.email == "del-full@example.com"))
        ).scalar_one_or_none() is None


async def test_deletion_anonymises_the_ledger_but_keeps_the_count(client):
    """The cost-ceiling bypass: create, generate, delete, repeat. The global
    daily budget counts ledger rows, so deletion must orphan them, not erase
    them — or deleted accounts would refund the platform's spend."""
    headers = await _register(client, "del-ledger@example.com")
    await _story(client, headers)

    async with get_session_factory()() as session:
        before = (await session.execute(select(func.count(GenerationEvent.id)))).scalar_one()

    r = await client.request("DELETE", "/api/auth/me", json={"password": "password123"}, headers=headers)
    assert r.status_code == 200

    async with get_session_factory()() as session:
        after = (await session.execute(select(func.count(GenerationEvent.id)))).scalar_one()
        orphans = (
            await session.execute(
                select(func.count(GenerationEvent.id)).where(GenerationEvent.user_id.is_(None))
            )
        ).scalar_one()
    assert after == before, "deletion must not shrink the financial ledger"
    assert orphans >= 1, "the deleted user's events must be anonymised, not kept attributed"


async def test_deleted_email_can_register_again(client):
    headers = await _register(client, "del-reuse@example.com")
    await client.request("DELETE", "/api/auth/me", json={"password": "password123"}, headers=headers)
    r = await client.post(
        "/api/auth/register",
        json={"email": "del-reuse@example.com", "password": "password456", "display_name": ""},
    )
    assert r.status_code == 201, "a deleted address must be free to start over"


async def test_shared_links_die_with_the_account(client):
    headers = await _register(client, "del-shared@example.com")
    story_id = await _story(client, headers)
    slug = (await client.post(f"/api/stories/{story_id}/share", headers=headers)).json()["share_slug"]
    assert (await client.get(f"/api/stories/shared/{slug}")).status_code == 200

    await client.request("DELETE", "/api/auth/me", json={"password": "password123"}, headers=headers)
    assert (await client.get(f"/api/stories/shared/{slug}")).status_code == 404


# --- Production guards -------------------------------------------------------


def test_console_email_backend_refuses_to_boot_in_production():
    """A production deploy that forgets SMTP would write password-reset links
    into the logs and deliver nothing, silently."""
    s = Settings(_env_file=None, environment="production", email_backend="console")
    with pytest.raises(RuntimeError) as e:
        validate_production_settings(s)
    assert "EMAIL_BACKEND" in str(e.value)


def test_smtp_backend_boots_in_production():
    # A host is required too — see test_smtp_backend_without_a_host_refuses_to_boot.
    validate_production_settings(
        Settings(
            _env_file=None,
            environment="production",
            email_backend="smtp",
            smtp_host="smtp.example.com",
        )
    )


def test_console_email_is_fine_outside_production():
    validate_production_settings(Settings(_env_file=None, environment="development"))


async def test_hsts_present_only_in_production(client, monkeypatch):
    """HSTS on localhost would poison the dev browser for a year."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    r = await client.get("/api/health")
    assert "strict-transport-security" not in r.headers

    monkeypatch.setattr(get_settings(), "environment", "production", raising=False)
    prod_app = create_app()
    async with AsyncClient(transport=ASGITransport(app=prod_app), base_url="http://test") as c:
        r = await c.get("/api/health")
    assert r.headers.get("strict-transport-security") == "max-age=31536000; includeSubDomains"


# --- Legal surface -----------------------------------------------------------


async def test_legal_pages_are_served(client):
    for path, marker in (("/privacy", "Privacy Policy"), ("/terms", "Terms of Service")):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert marker in r.text
        assert "children" in r.text.lower(), f"{path} must address children's data"


async def test_signup_carries_consent_links(client):
    body = (await client.get("/")).text
    assert 'href="/terms"' in body and 'href="/privacy"' in body


# --- Regressions found by adversarial review ---------------------------------


def test_smtp_backend_without_a_host_refuses_to_boot():
    """The guard's own failure mode: EMAIL_BACKEND=smtp with SMTP_HOST empty
    boots fine, then smtplib.SMTP("") never connects and the sender swallows
    the error — every reset email silently dropped, exactly what the guard
    exists to prevent. SMTP_HOST ships empty, so it is one omission away."""
    s = Settings(_env_file=None, environment="production", email_backend="smtp", smtp_host="")
    with pytest.raises(RuntimeError) as e:
        validate_production_settings(s)
    assert "SMTP_HOST" in str(e.value)


def test_smtp_with_a_host_boots():
    validate_production_settings(
        Settings(_env_file=None, environment="production", email_backend="smtp", smtp_host="smtp.example.com")
    )


async def test_deleting_mid_generation_leaves_no_orphaned_media(client, monkeypatch):
    """A parent deletes their account while illustrations are still being
    written. save_image recreates the directory deletion just removed, and the
    row that would point at those files is gone — so nothing would ever reclaim
    them. On a children's product those are pictures made from a child's name.
    """
    import asyncio
    import os

    headers = await _register(client, "del-race@example.com")
    r = await client.post("/api/stories", json={"prompt": "A race against deletion"}, headers=headers)
    story_id = r.json()["story_id"]

    # Delete while the inline generation task is still running.
    await asyncio.sleep(0.2)
    resp = await client.request("DELETE", "/api/auth/me", json={"password": "password123"}, headers=headers)
    assert resp.status_code == 200, resp.text

    # Let the detached generation task finish writing whatever it was mid-way through.
    from app.jobs import _inline_tasks

    if _inline_tasks:
        await asyncio.wait(set(_inline_tasks), timeout=30)

    media_dir = os.path.join(get_settings().media_root, "stories", story_id)
    leftovers = os.listdir(media_dir) if os.path.isdir(media_dir) else []
    assert not leftovers, f"orphaned illustrations survived deletion: {leftovers}"


async def test_deletion_does_not_alert_for_a_long_cancelled_subscription(client, caplog):
    """A formerly-subscribed account raised a false 'cancel manually' incident
    on every deletion, training the operator to ignore a real one."""
    headers = await _register(client, "del-exsub@example.com")
    me = (await client.get("/api/auth/me", headers=headers)).json()
    async with get_session_factory()() as session:
        user = (await session.execute(select(User).where(User.id == me["id"]))).scalar_one()
        user.stripe_subscription_id = "sub_long_gone"
        user.stripe_subscription_status = "canceled"
        await session.commit()

    with caplog.at_level("ERROR"):
        r = await client.request("DELETE", "/api/auth/me", json={"password": "password123"}, headers=headers)
    assert r.status_code == 200
    assert "LIVE SUBSCRIPTION" not in caplog.text
