import pytest

from .conftest import wait_for_job

pytestmark = pytest.mark.asyncio


async def test_full_story_generation_flow(client, auth_headers):
    # Kick off generation
    r = await client.post(
        "/api/stories",
        json={"prompt": "Leo the Lion and Lily the Lost Girl", "language": "en"},
        headers=auth_headers,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    story_id, job_id = body["story_id"], body["job_id"]

    # Progress reaches completion
    job = await wait_for_job(client, auth_headers, job_id)
    assert job["status"] == "complete", job
    assert job["stage"] == "done"
    assert job["progress_total"] > 0
    assert job["progress_current"] == job["progress_total"]

    # Story is complete with pages and images
    r = await client.get(f"/api/stories/{story_id}", headers=auth_headers)
    assert r.status_code == 200
    story = r.json()
    assert story["status"] == "complete"
    assert story["title"]
    assert story["provider"] == "mock"
    assert 1 <= len(story["pages"]) <= 3
    for page in story["pages"]:
        assert page["text"]
        assert page["image_url"].startswith("/media/")

    # Image files actually exist and are served
    img = await client.get(story["pages"][0]["image_url"])
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")

    # Library lists it with a cover image
    r = await client.get("/api/stories", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["cover_image_url"].startswith("/media/")


async def test_story_isolation_between_users(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "The kind yeti of Khumbu"}, headers=auth_headers)
    story_id = r.json()["story_id"]
    job_id = r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)

    # Second user can't see the first user's story or job
    r2 = await client.post(
        "/api/auth/register", json={"email": "other@example.com", "password": "password123"}
    )
    other = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    assert (await client.get(f"/api/stories/{story_id}", headers=other)).status_code == 404
    assert (await client.get(f"/api/jobs/{job_id}", headers=other)).status_code == 404
    assert (await client.get("/api/stories", headers=other)).json() == []


async def test_share_flow(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "A dancing rhododendron"}, headers=auth_headers)
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)

    # Share
    r = await client.post(f"/api/stories/{story_id}/share", headers=auth_headers)
    assert r.status_code == 200
    slug = r.json()["share_slug"]
    assert slug

    # Sharing again returns the same slug (idempotent)
    r = await client.post(f"/api/stories/{story_id}/share", headers=auth_headers)
    assert r.json()["share_slug"] == slug

    # Public access without auth
    r = await client.get(f"/api/stories/shared/{slug}")
    assert r.status_code == 200
    shared = r.json()
    assert shared["title"]
    assert "prompt" not in shared  # no private fields leaked
    assert len(shared["pages"]) >= 1

    # Unshare kills the link
    r = await client.delete(f"/api/stories/{story_id}/share", headers=auth_headers)
    assert r.status_code == 204
    assert (await client.get(f"/api/stories/shared/{slug}")).status_code == 404


async def test_prompt_validation(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "ab"}, headers=auth_headers)
    assert r.status_code == 422
    r = await client.post("/api/stories", json={"prompt": "x" * 600}, headers=auth_headers)
    assert r.status_code == 422


async def test_daily_quota_enforced(client, auth_headers):
    job_ids = []
    for i in range(3):  # FREE_DAILY_STORIES=3 in test env
        r = await client.post(
            "/api/stories", json={"prompt": f"Adventure number {i} in the hills"}, headers=auth_headers
        )
        assert r.status_code == 202, r.text
        job_ids.append(r.json()["job_id"])

    r = await client.post("/api/stories", json={"prompt": "One story too many"}, headers=auth_headers)
    assert r.status_code == 429
    detail = r.json()["detail"].lower()
    assert "used all 3 stories" in detail and "resets tomorrow" in detail

    # Usage endpoint reflects it
    r = await client.get("/api/auth/usage", headers=auth_headers)
    usage = r.json()
    assert usage["stories_today"] == 3
    assert usage["remaining_today"] == 0

    # Let inline jobs finish so the test teardown is clean
    for jid in job_ids:
        await wait_for_job(client, auth_headers, jid)


async def test_delete_story_removes_it(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "A momo that could fly"}, headers=auth_headers)
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    await wait_for_job(client, auth_headers, job_id)

    r = await client.delete(f"/api/stories/{story_id}", headers=auth_headers)
    assert r.status_code == 204
    assert (await client.get(f"/api/stories/{story_id}", headers=auth_headers)).status_code == 404


async def test_nepali_language_story(client, auth_headers):
    r = await client.post(
        "/api/stories", json={"prompt": "साहसी हिमाली केटी", "language": "ne"}, headers=auth_headers
    )
    assert r.status_code == 202
    job = await wait_for_job(client, auth_headers, r.json()["job_id"])
    assert job["status"] == "complete"
    story = (await client.get(f"/api/stories/{r.json()['story_id']}", headers=auth_headers)).json()
    assert story["language"] == "ne"


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["provider"] == "mock"
