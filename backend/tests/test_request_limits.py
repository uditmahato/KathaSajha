"""Guards on what a caller can force the server to allocate or trust.

These cover three ways an unauthenticated or low-privilege caller could push the
server further than intended: an oversized body, a spoofed client address, and a
token that outlived the password it was issued against.
"""

import pytest
from fastapi import Request

from app.config import get_settings
from app.routers.auth import _client_ip

pytestmark = pytest.mark.asyncio


# --- Body size ---------------------------------------------------------------


async def test_oversized_body_is_refused_before_parsing(client):
    """The body is buffered and parsed before any field validator runs, so the
    ceiling has to sit in front of the application, not in the schema."""
    limit = get_settings().max_request_body_bytes
    payload = '{"email":"a@b.com","password":"' + ("x" * (limit + 2048)) + '"}'

    r = await client.post(
        "/api/auth/login",
        content=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413, r.text
    assert "too large" in r.json()["detail"].lower()


async def test_oversized_body_refused_without_content_length(client):
    """A chunked request declares no length, so the ceiling must also count
    bytes as they arrive rather than trusting the header alone."""

    async def chunks():
        for _ in range(8):
            yield b"x" * 16384

    r = await client.post(
        "/api/auth/login",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413, r.text


async def test_normal_sized_body_still_works(client):
    r = await client.post(
        "/api/auth/register",
        json={"email": "normal@example.com", "password": "password123", "display_name": "Normal"},
    )
    assert r.status_code == 201, r.text


async def test_prompt_field_has_a_hard_ceiling(client, auth_headers):
    """Above the schema cap the request is rejected as invalid input, never
    carried far enough to become a generation."""
    r = await client.post("/api/stories", json={"prompt": "a" * 5000}, headers=auth_headers)
    assert r.status_code == 422, r.text


# --- Client address trust ----------------------------------------------------


def _request_with(peer: str, forwarded: str | None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": headers, "client": (peer, 1234)}
    )


async def test_forwarded_for_is_ignored_from_an_untrusted_peer(monkeypatch):
    """Believing this header unconditionally let anyone rotate it per request
    and walk straight through the IP-keyed brute-force limits."""
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxy_ips", "", raising=False)

    assert _client_ip(_request_with("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
    # Every spoofed value must collapse to the same real peer, or the limiter
    # is counting attackers separately instead of together.
    assert _client_ip(_request_with("203.0.113.9", "5.6.7.8")) == "203.0.113.9"


async def test_forwarded_for_is_honoured_from_a_trusted_proxy(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.1, 10.0.0.2", raising=False)

    assert _client_ip(_request_with("10.0.0.1", "1.2.3.4, 10.0.0.1")) == "1.2.3.4"
    assert _client_ip(_request_with("10.0.0.9", "1.2.3.4")) == "10.0.0.9"


async def test_missing_forwarded_header_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "10.0.0.1", raising=False)
    assert _client_ip(_request_with("10.0.0.1", None)) == "10.0.0.1"


# --- Token retirement --------------------------------------------------------


async def test_changing_the_password_retires_existing_tokens(client):
    r = await client.post(
        "/api/auth/register",
        json={"email": "retire@example.com", "password": "original123", "display_name": ""},
    )
    old = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=old)).status_code == 200

    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": "original123", "new_password": "replacement123"},
        headers=old,
    )
    assert changed.status_code == 200, changed.text

    # The stolen-token case: the old bearer must stop working immediately.
    assert (await client.get("/api/auth/me", headers=old)).status_code == 401
    # ...and the caller who just changed it keeps working, with the new token.
    fresh = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=fresh)).status_code == 200


async def test_resetting_the_password_retires_existing_tokens(client, monkeypatch):
    import app.routers.auth as auth_router

    sent = {}

    class CapturingSender:
        async def send(self, *, to, subject, text):
            sent["text"] = text

    monkeypatch.setattr(auth_router, "get_email_sender", lambda: CapturingSender())

    r = await client.post(
        "/api/auth/register",
        json={"email": "reset-retire@example.com", "password": "original123", "display_name": ""},
    )
    old = {"Authorization": f"Bearer {r.json()['access_token']}"}

    await client.post("/api/auth/forgot-password", json={"email": "reset-retire@example.com"})
    token = sent["text"].split("token=")[1].split()[0]

    done = await client.post("/api/auth/reset-password", json={"token": token, "password": "brandnew12345"})
    assert done.status_code == 200, done.text

    # Whoever held the pre-reset token is exactly who the reset exists to lock out.
    assert (await client.get("/api/auth/me", headers=old)).status_code == 401
    fresh = {"Authorization": f"Bearer {done.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=fresh)).status_code == 200
