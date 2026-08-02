import pytest

pytestmark = pytest.mark.asyncio


async def test_register_login_me(client):
    r = await client.post(
        "/api/auth/register",
        json={"email": "udit@example.com", "password": "supersecret1", "display_name": "Udit"},
    )
    assert r.status_code == 201
    token = r.json()["access_token"]

    # Duplicate registration rejected
    r = await client.post(
        "/api/auth/register",
        json={"email": "UDIT@example.com", "password": "supersecret1"},
    )
    assert r.status_code == 409

    # Login works (case-insensitive email)
    r = await client.post("/api/auth/login", json={"email": "Udit@Example.com", "password": "supersecret1"})
    assert r.status_code == 200

    # Wrong password rejected
    r = await client.post("/api/auth/login", json={"email": "udit@example.com", "password": "wrongpass99"})
    assert r.status_code == 401

    # /me returns profile
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "udit@example.com"
    assert body["plan"] == "free"


async def test_short_password_rejected(client):
    r = await client.post("/api/auth/register", json={"email": "a@b.com", "password": "short"})
    assert r.status_code == 422


async def test_protected_routes_require_auth(client):
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/stories")).status_code == 401
    assert (await client.post("/api/stories", json={"prompt": "a brave yak"})).status_code == 401


async def test_invalid_token_rejected(client):
    r = await client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
