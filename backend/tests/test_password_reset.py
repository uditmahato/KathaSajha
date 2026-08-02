"""Account recovery: the flow a locked-out paying customer depends on."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import get_session_factory
from app.models import PasswordResetToken
from app.security import hash_reset_token

pytestmark = pytest.mark.asyncio


async def _register(client, email="reset@example.com", password="original123"):
    r = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


@pytest.fixture()
def captured_emails(monkeypatch):
    """Capture outbound email instead of logging it, so tests can read the link."""
    from app.services import email as email_module

    sent: list[dict] = []

    class CapturingSender(email_module.EmailSender):
        async def send(self, *, to: str, subject: str, text: str) -> None:
            sent.append({"to": to, "subject": subject, "text": text})

    email_module.reset_email_sender()
    monkeypatch.setattr(email_module, "get_email_sender", lambda: CapturingSender())
    # The auth router imported the symbol directly.
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "get_email_sender", lambda: CapturingSender())
    yield sent
    email_module.reset_email_sender()


def _token_from(email_text: str) -> str:
    marker = "token="
    start = email_text.index(marker) + len(marker)
    end = email_text.find("\n", start)
    return email_text[start : end if end != -1 else None].strip()


async def test_full_reset_flow_logs_user_in(client, captured_emails):
    await _register(client)

    r = await client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
    assert r.status_code == 200
    assert "if an account exists" in r.json()["message"].lower()
    assert len(captured_emails) == 1
    token = _token_from(captured_emails[0]["text"])

    r = await client.post("/api/auth/reset-password", json={"token": token, "password": "brandnew456"})
    assert r.status_code == 200
    new_jwt = r.json()["access_token"]

    # The returned token works immediately.
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_jwt}"})
    assert me.status_code == 200

    # New password works, old one does not.
    assert (
        await client.post("/api/auth/login", json={"email": "reset@example.com", "password": "brandnew456"})
    ).status_code == 200
    assert (
        await client.post("/api/auth/login", json={"email": "reset@example.com", "password": "original123"})
    ).status_code == 401


async def test_reset_token_is_single_use(client, captured_emails):
    await _register(client, email="single@example.com")
    await client.post("/api/auth/forgot-password", json={"email": "single@example.com"})
    token = _token_from(captured_emails[0]["text"])

    assert (
        await client.post("/api/auth/reset-password", json={"token": token, "password": "firstuse123"})
    ).status_code == 200
    second = await client.post("/api/auth/reset-password", json={"token": token, "password": "seconduse123"})
    assert second.status_code == 400
    assert "invalid or has expired" in second.json()["detail"]


async def test_requesting_again_invalidates_the_previous_link(client, captured_emails):
    await _register(client, email="two@example.com")
    await client.post("/api/auth/forgot-password", json={"email": "two@example.com"})
    await client.post("/api/auth/forgot-password", json={"email": "two@example.com"})
    assert len(captured_emails) == 2
    old_token = _token_from(captured_emails[0]["text"])
    new_token = _token_from(captured_emails[1]["text"])

    assert (
        await client.post("/api/auth/reset-password", json={"token": old_token, "password": "shouldfail123"})
    ).status_code == 400
    assert (
        await client.post("/api/auth/reset-password", json={"token": new_token, "password": "shouldwork123"})
    ).status_code == 200


async def test_expired_token_rejected(client, captured_emails):
    await _register(client, email="expired@example.com")
    await client.post("/api/auth/forgot-password", json={"email": "expired@example.com"})
    token = _token_from(captured_emails[0]["text"])

    async with get_session_factory()() as session:
        record = (
            await session.execute(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_reset_token(token))
            )
        ).scalar_one()
        record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    r = await client.post("/api/auth/reset-password", json={"token": token, "password": "toolate12345"})
    assert r.status_code == 400


async def test_unknown_email_gives_identical_response_and_sends_nothing(client, captured_emails):
    r = await client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert "if an account exists" in r.json()["message"].lower()
    assert captured_emails == []  # no enumeration signal, no wasted send


async def test_garbage_token_rejected(client):
    r = await client.post("/api/auth/reset-password", json={"token": "x" * 40, "password": "whatever12345"})
    assert r.status_code == 400


async def test_only_the_token_hash_is_stored(client, captured_emails):
    await _register(client, email="hashonly@example.com")
    await client.post("/api/auth/forgot-password", json={"email": "hashonly@example.com"})
    token = _token_from(captured_emails[0]["text"])

    async with get_session_factory()() as session:
        rows = (await session.execute(select(PasswordResetToken))).scalars().all()
        assert rows, "a token row should exist"
        for row in rows:
            assert row.token_hash != token  # never the plaintext
            assert len(row.token_hash) == 64  # sha256 hex


async def test_change_password_requires_current_password(client):
    token = await _register(client, email="change@example.com", password="original123")
    headers = {"Authorization": f"Bearer {token}"}

    wrong = await client.post(
        "/api/auth/change-password",
        json={"current_password": "notitatall", "new_password": "newpass12345"},
        headers=headers,
    )
    assert wrong.status_code == 401

    ok = await client.post(
        "/api/auth/change-password",
        json={"current_password": "original123", "new_password": "newpass12345"},
        headers=headers,
    )
    assert ok.status_code == 200
    assert (
        await client.post("/api/auth/login", json={"email": "change@example.com", "password": "newpass12345"})
    ).status_code == 200
