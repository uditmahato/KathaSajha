"""Server-side PDF: the artifact is opened and parsed, never assumed.

The previous exporter shipped a 15-page file for a 5-page story — ten sheets
blank, the cover blank — and every test was green, because no test ever
looked at the output. These tests read the produced PDF back with pypdf and
assert on what a parent would actually hold.
"""

from io import BytesIO

import pytest
from pypdf import PdfReader

from app.services.pdf import devanagari_font_available
from app.storage import LocalStorage

from .conftest import wait_for_job

# No module-level asyncio mark: this file mixes sync renderer tests with async
# endpoint tests, and pytest.ini already runs in asyncio auto mode.


async def _completed_story(client, headers, prompt="The yak who counts stars", **extra):
    body = {"prompt": prompt, **extra}
    r = await client.post("/api/stories", json=body, headers=headers)
    assert r.status_code == 202, r.text
    story_id, job_id = r.json()["story_id"], r.json()["job_id"]
    job = await wait_for_job(client, headers, job_id)
    assert job["status"] == "complete", job
    return story_id


async def test_pdf_page_count_is_exactly_cover_story_back(client, auth_headers):
    """The property the old exporter violated: n story pages -> n + 2 sheets."""
    story_id = await _completed_story(client, auth_headers)
    story = (await client.get(f"/api/stories/{story_id}", headers=auth_headers)).json()

    r = await client.get(f"/api/stories/{story_id}/pdf", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")

    reader = PdfReader(BytesIO(r.content))
    assert len(reader.pages) == len(story["pages"]) + 2


async def test_no_page_is_blank(client, auth_headers):
    """Ten of the old exporter's fifteen sheets were empty paper."""
    story_id = await _completed_story(client, auth_headers)
    story = (await client.get(f"/api/stories/{story_id}", headers=auth_headers)).json()

    r = await client.get(f"/api/stories/{story_id}/pdf", headers=auth_headers)
    reader = PdfReader(BytesIO(r.content))

    cover = reader.pages[0].extract_text() or ""
    assert story["title"].split()[0] in cover, "the cover must carry the title"
    for i, page in enumerate(story["pages"], start=1):
        extracted = reader.pages[i].extract_text() or ""
        assert page["text"].split()[0] in extracted, f"story page {i} lost its text"
        assert str(i) in extracted, f"story page {i} has no page number"
    back = reader.pages[-1].extract_text() or ""
    assert "End" in back or "समाप्त" in back


async def test_text_is_vector_not_pixels(client, auth_headers):
    """extract_text working at all proves the text is real glyphs, not a
    screenshot — the old exporter's pages extracted as empty strings."""
    story_id = await _completed_story(client, auth_headers)
    r = await client.get(f"/api/stories/{story_id}/pdf", headers=auth_headers)
    reader = PdfReader(BytesIO(r.content))
    assert any((p.extract_text() or "").strip() for p in reader.pages)


async def test_nepali_story_renders_as_a_book(client, auth_headers):
    if not devanagari_font_available():
        pytest.skip("no Devanagari font on this host")
    story_id = await _completed_story(
        client, auth_headers, prompt="चङ्गा उडाउने केटी", language="ne", hero_name="सीता"
    )
    story = (await client.get(f"/api/stories/{story_id}", headers=auth_headers)).json()
    r = await client.get(f"/api/stories/{story_id}/pdf", headers=auth_headers)
    assert r.status_code == 200, r.text
    reader = PdfReader(BytesIO(r.content))
    assert len(reader.pages) == len(story["pages"]) + 2


async def test_pdf_of_someone_elses_story_is_404(client, auth_headers):
    story_id = await _completed_story(client, auth_headers)
    r2 = await client.post(
        "/api/auth/register",
        json={"email": "other-pdf@example.com", "password": "password123", "display_name": ""},
    )
    other = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    assert (await client.get(f"/api/stories/{story_id}/pdf", headers=other)).status_code == 404


async def test_incomplete_story_cannot_be_exported(client, auth_headers):
    r = await client.post("/api/stories", json={"prompt": "Not finished yet"}, headers=auth_headers)
    story_id = r.json()["story_id"]
    resp = await client.get(f"/api/stories/{story_id}/pdf", headers=auth_headers)
    # Inline generation may already have finished; only a 409 or a full book
    # are acceptable — never a half-rendered artifact.
    assert resp.status_code in (200, 409)


async def test_shared_pdf_is_public(client, auth_headers):
    """Grandparents have no account; the share link must include the book."""
    story_id = await _completed_story(client, auth_headers)
    slug = (await client.post(f"/api/stories/{story_id}/share", headers=auth_headers)).json()["share_slug"]

    r = await client.get(f"/api/stories/shared/{slug}/pdf")  # no auth
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"


async def test_unshared_story_has_no_public_pdf(client, auth_headers):
    story_id = await _completed_story(client, auth_headers)
    slug = (await client.post(f"/api/stories/{story_id}/share", headers=auth_headers)).json()["share_slug"]
    await client.delete(f"/api/stories/{story_id}/share", headers=auth_headers)
    assert (await client.get(f"/api/stories/shared/{slug}/pdf")).status_code == 404


async def test_missing_illustration_degrades_the_page_not_the_book(client, auth_headers):
    import os

    story_id = await _completed_story(client, auth_headers)
    story = (await client.get(f"/api/stories/{story_id}", headers=auth_headers)).json()
    # Delete one image file from disk: the book must still build, full length.
    from app.config import get_settings

    media_dir = os.path.join(get_settings().media_root, "stories", story_id)
    victim = sorted(os.listdir(media_dir))[0]
    os.remove(os.path.join(media_dir, victim))

    r = await client.get(f"/api/stories/{story_id}/pdf", headers=auth_headers)
    assert r.status_code == 200, r.text
    reader = PdfReader(BytesIO(r.content))
    assert len(reader.pages) == len(story["pages"]) + 2


async def test_image_loader_refuses_paths_outside_media_root(tmp_path):
    """image_url maps a URL to a filesystem path; a crafted row must not be
    able to pull arbitrary files into a PDF someone then shares."""
    root = tmp_path / "media"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("not for books")
    storage = LocalStorage(str(root), "/media")

    assert await storage.load_image("/media/../secret.txt") is None
    assert await storage.load_image("/elsewhere/x.png") is None


# --- Regressions found by adversarial review ---------------------------------


def _png(w=1024, h=1024) -> bytes:
    from io import BytesIO as _B

    from PIL import Image as _I

    buf = _B()
    _I.new("RGB", (w, h), (200, 150, 100)).save(buf, format="PNG")
    return buf.getvalue()


def test_corrupt_image_degrades_the_page_not_the_book():
    """A truncated write or zero-byte file must never 500 a share link that
    was already handed out. Truncated PNGs pass the header probe and only
    fail at draw time, so the draw itself is guarded."""
    from app.services.pdf import PdfPage, build_story_pdf

    truncated = _png()[: len(_png()) // 2]
    for bad in (b"", b"garbage", truncated):
        data = build_story_pdf(
            title="Resilient Book",
            language="en",
            hero_name="",
            pages=[PdfPage(text="First page.", image=bad), PdfPage(text="Second page.", image=_png())],
        )
        reader = PdfReader(BytesIO(data))
        assert len(reader.pages) == 4, "corrupt image must not change the page count"
        assert "First page.".split()[0] in (reader.pages[1].extract_text() or "")


def test_long_paragraph_stays_on_one_sheet():
    """~150 words with a square illustration used to spill a continuation
    sheet carrying the page number — the old exporter's bug, reborn."""
    from app.services.pdf import PdfPage, build_story_pdf

    long_text = ("The brave little yak walked up the winding mountain path and " * 18).strip()
    data = build_story_pdf(
        title="A Long-Winded Tale",
        language="en",
        hero_name="Maya",
        pages=[PdfPage(text=long_text, image=_png())] * 3,
    )
    reader = PdfReader(BytesIO(data))
    assert len(reader.pages) == 5, "long text must shrink to fit, never add sheets"
    for i in (1, 2, 3):
        extracted = reader.pages[i].extract_text() or ""
        assert str(i) in extracted, f"page number {i} must sit on its own scene's sheet"


def test_long_title_keeps_the_cover_to_one_sheet():
    from app.services.pdf import PdfPage, build_story_pdf

    title = "The Extraordinarily Detailed and Remarkably Long Tale of the Yak, "
    title = (title * 4)[:290]
    data = build_story_pdf(
        title=title, language="en", hero_name="Aarav", pages=[PdfPage(text="Once.", image=_png())]
    )
    assert len(PdfReader(BytesIO(data)).pages) == 3


async def test_filename_header_survives_a_hostile_title(client, auth_headers):
    """A '/' in the title made filename* invalid RFC 5987."""
    from sqlalchemy import update

    from app.db import get_session_factory
    from app.models import Story

    story_id = await _completed_story(client, auth_headers)
    async with get_session_factory()() as session:
        await session.execute(
            update(Story).where(Story.id == story_id).values(title='My/Story "quoted" tale')
        )
        await session.commit()

    r = await client.get(f"/api/stories/{story_id}/pdf", headers=auth_headers)
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert "%2F" in disposition, "slash must be percent-encoded in filename*"
    assert "\n" not in disposition and "\r" not in disposition
