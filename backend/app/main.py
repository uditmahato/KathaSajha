"""KathaSajha API — FastAPI application entrypoint.

Run (dev):   uvicorn app.main:app --reload --port 8000    (from backend/)
Run (prod):  see Dockerfile / docker-compose.yml
"""

import asyncio
import html
import json
import logging
import mimetypes
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .billing_guard import validate_billing_settings
from .config import get_settings
from .db import dispose_engine, init_db
from .deps import DbSession
from .jobs import close_job_pool
from .models import Story
from .observability import CorrelationMiddleware, configure_logging
from .quota import close_redis
from .routers import auth, health, jobs, plans, stories

configure_logging()
logger = logging.getLogger(__name__)

FRONTEND_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))

# StaticFiles asks the OS for extension -> MIME mappings. On Windows that means
# the registry, where .js is commonly registered as text/plain; combined with the
# X-Content-Type-Options: nosniff header set below, the browser then refuses to
# execute the SPA and every page renders blank with no console error. Pin the
# handful of types we serve so behaviour cannot depend on the host machine.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


def validate_production_settings(settings) -> None:
    """Refuse to boot production with silently-broken account recovery.

    The console email backend prints password-reset links into the log and
    sends nothing. In development that is the feature; in production it means
    every locked-out parent stays locked out while secrets leak into logs, and
    nothing anywhere looks wrong. Same fail-loud philosophy as the billing
    guard. Pure function because the test client never runs lifespan.
    """
    if settings.environment != "production":
        return
    if settings.email_backend == "console":
        raise RuntimeError(
            "EMAIL_BACKEND is 'console' in production: password-reset links would be "
            "written to the log and never delivered. Configure EMAIL_BACKEND=smtp "
            "and the SMTP_* settings."
        )
    if settings.email_backend == "smtp" and not settings.smtp_host:
        # Same silent failure by another route: smtplib.SMTP("") constructs
        # without connecting, the first command raises, and SmtpEmailSender
        # swallows it by design — so every reset email is dropped while
        # /forgot-password still reports success. SMTP_HOST ships empty, so
        # this is one forgotten variable away in the exact flow GO_LIVE walks
        # an operator through.
        raise RuntimeError(
            "EMAIL_BACKEND is 'smtp' in production but SMTP_HOST is empty: every "
            "password-reset email would be silently dropped. Set SMTP_HOST."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.sentry_dsn:
        # Dormant until a DSN exists, like billing. Errors-only: tracing costs
        # money and the structured logs already carry request correlation.
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0,
            send_default_pii=False,
        )
        logger.info("Sentry error tracking enabled")
    await init_db()
    _KNOWN_DEV_SECRETS = {"change-me-in-production", "dev-only-secret-change-me", "test-secret"}
    if settings.environment == "production" and (
        settings.secret_key in _KNOWN_DEV_SECRETS or len(settings.secret_key) < 32
    ):
        raise RuntimeError("SECRET_KEY must be set to a long random value in production")
    validate_production_settings(settings)
    # Refuses to boot on half-configured billing, in every environment: a
    # partially-wired dev box creates real test-mode sessions and has the same
    # shape of bug, and enforcing uniformly means the dev config proves the
    # production one.
    validate_billing_settings(settings)
    if settings.job_backend == "inline":
        # A previous process may have died with generations in flight; fail those
        # orphans now so users aren't left with permanently-stuck stories.
        from sqlalchemy import update

        from .db import get_session_factory
        from .models import GenerationJob, Story

        async with get_session_factory()() as session:
            await session.execute(
                update(GenerationJob)
                .where(GenerationJob.status.in_(["queued", "running"]))
                .values(
                    status="failed",
                    stage="failed",
                    error="Interrupted by a server restart. Please try again.",
                )
            )
            await session.execute(
                update(Story)
                .where(Story.status.in_(["pending", "generating"]))
                .values(status="failed", error="Interrupted by a server restart. Please try again.")
            )
            await session.commit()
    logger.info(
        "KathaSajha up (env=%s, provider=%s, jobs=%s, storage=%s)",
        settings.environment,
        settings.resolved_provider,
        settings.job_backend,
        settings.storage_backend,
    )
    yield
    await close_job_pool()
    await close_redis()
    await dispose_engine()


_SOCIAL_START = "<!--SOCIAL_META_START-->"
_SOCIAL_END = "<!--SOCIAL_META_END-->"
_WHITESPACE = re.compile(r"\s+")


async def _read_shell() -> str:
    """The SPA shell, read off the event loop. Small file, but it is disk I/O on
    a public path that crawlers and chat apps hit."""
    path = os.path.join(FRONTEND_DIR, "index.html")

    def _read() -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    return await asyncio.to_thread(_read)


def _meta(name: str, value: str, *, attr: str = "property") -> str:
    # Every value here is model output derived from a user prompt. Unescaped in
    # an HTML attribute, a title carrying a quote would break out of the tag and
    # inject markup into a page served to everyone the link is forwarded to.
    return f'<meta {attr}="{html.escape(name, quote=True)}" content="{html.escape(value, quote=True)}">'


def _summarise(text: str, limit: int = 160) -> str:
    collapsed = _WHITESPACE.sub(" ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _social_tags(*, title: str, description: str, url: str, image: str) -> str:
    tags = [
        _meta("og:type", "article"),
        _meta("og:site_name", "KathaSajha"),
        _meta("og:title", title),
        _meta("og:description", description),
        _meta("og:url", url),
        _meta("description", description, attr="name"),
        _meta("twitter:card", "summary_large_image", attr="name"),
        _meta("twitter:title", title, attr="name"),
        _meta("twitter:description", description, attr="name"),
    ]
    if image:
        tags.append(_meta("og:image", image))
        tags.append(_meta("twitter:image", image, attr="name"))
    return "\n    ".join(tags)


def _inject_social(shell: str, tags: str) -> str:
    start, end = shell.find(_SOCIAL_START), shell.find(_SOCIAL_END)
    if start == -1 or end == -1 or end < start:
        return shell  # markers missing: serve the shell rather than fail the page
    return shell[: start + len(_SOCIAL_START)] + "\n    " + tags + "\n    " + shell[end:]


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are buffered into memory.

    Starlette imposes no limit of its own, and FastAPI reads and parses the whole
    body before the first field validator runs, so no schema constraint can stop
    a multi-megabyte POST from being materialised. Written as pure ASGI rather
    than BaseHTTPMiddleware so it can count bytes as they arrive, which also
    covers chunked requests that carry no Content-Length to check.
    """

    _BODY_METHODS = ("POST", "PUT", "PATCH")

    def __init__(self, app, max_bytes: int, exempt_paths: dict[str, int] | None = None):
        self.app = app
        self.max_bytes = max_bytes
        # Paths with their own ceiling. The Stripe webhook needs one: events are
        # usually a few KB but can exceed the global cap, and a 413 makes Stripe
        # retry for three days and then disable the endpoint. Truncating its
        # body would be worse still -- signature verification is over the exact
        # bytes, so a trimmed payload silently fails to verify.
        self.exempt_paths = exempt_paths or {}

    def _limit_for(self, path: str) -> int:
        return self.exempt_paths.get(path, self.max_bytes)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in self._BODY_METHODS:
            await self.app(scope, receive, send)
            return

        max_bytes = self._limit_for(scope.get("path", ""))
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > max_bytes
            except ValueError:
                too_big = True
            if too_big:
                await self._reject(send)
                return

        seen = 0
        too_large = False
        started = False
        end_of_stream = {"type": "http.request", "body": b"", "more_body": False}

        async def counting_receive():
            # Raising from here does not work: FastAPI wraps body parsing in a
            # broad `except Exception` and rewrites anything it catches into a
            # 400. So instead the stream is cut short and the app's resulting
            # response is discarded in favour of a 413 below.
            nonlocal seen, too_large
            if too_large:
                return end_of_stream  # stop draining the client entirely
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > max_bytes:
                    too_large = True
                    return end_of_stream
            return message

        async def watching_send(message):
            nonlocal started
            if too_large:
                return  # swallow whatever the app made of the truncated body
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, counting_receive, watching_send)
        if too_large and not started:  # cannot replace a response already sent
            await self._reject(send)

    async def _reject(self, send) -> None:
        body = json.dumps({"detail": "Request body is too large."}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="KathaSajha API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # PNG/JPEG illustrations are already compressed; gzipping them burns CPU for
    # roughly nothing. Only JSON and the SPA assets are worth compressing.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(CorrelationMiddleware)
    # Added last, so it wraps everything else: an oversized body is refused
    # before any other layer gets the chance to buffer it.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
        exempt_paths={"/api/billing/webhook": settings.webhook_max_body_bytes},
    )

    media_prefix = settings.media_url_prefix.rstrip("/") + "/"

    # Tight by default; only the hosts the app genuinely uses are allowed.
    # No 'unsafe-inline' for scripts: all JS lives in /assets/app.js. No CDN
    # hosts either — PDFs render server-side, so the last third-party script
    # (html2pdf) is gone.
    CSP = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "img-src 'self' data: blob:",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )

    # HSTS only where TLS exists; on a dev box it would poison localhost.
    hsts = settings.environment == "production"

    @app.middleware("http")
    async def security_and_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if hsts:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        path = request.url.path
        if path.startswith(media_prefix):
            # Illustration filenames carry a random suffix and are never rewritten,
            # so they can be cached hard: re-reading a story costs no bandwidth.
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif path == "/" or path.startswith("/assets/"):
            # The SPA shell must revalidate or users get stale JS after a deploy.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(stories.router)
    app.include_router(jobs.router)
    app.include_router(plans.router)
    if settings.billing_enabled:
        # Mounted only when billing is fully configured. While dormant these
        # paths do not exist at all -- a stub webhook that answers 200 without a
        # verified signature is an unauthenticated free-upgrade endpoint.
        from .routers import billing

        app.include_router(billing.router)
        logger.info("Billing enabled (provider=%s)", settings.resolved_billing_provider)

    # Locally-stored generated images.
    if settings.storage_backend == "local":
        os.makedirs(settings.media_root, exist_ok=True)
        app.mount(settings.media_url_prefix, StaticFiles(directory=settings.media_root), name="media")

    # Frontend SPA.
    if os.path.isdir(FRONTEND_DIR):
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")

        @app.get("/", include_in_schema=False)
        async def spa_index():
            return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

        @app.get("/shared/{slug}", include_in_schema=False)
        async def spa_shared(slug: str, request: Request, db: DbSession):
            """Share pages carry the story's own social preview.

            This is the product's only growth loop: a grandparent forwards a
            link. Served as a bare shell it previewed as a naked URL with no
            title, cover, or child's name, which is the whole reason someone
            taps it. The story is public by definition here, so no owner data
            is exposed that the page does not already show.
            """
            shell = await _read_shell()
            story = (
                await db.execute(
                    select(Story)
                    .where(Story.share_slug == slug, Story.status == "complete")
                    .options(selectinload(Story.pages))
                )
            ).scalar_one_or_none()
            if story is None:
                return HTMLResponse(shell)  # unknown slug: the SPA renders its own 404

            base = (get_settings().public_base_url or str(request.base_url)).rstrip("/")
            cover = next((p.image_url for p in story.pages if p.image_url), "")
            opening = story.pages[0].text if story.pages else ""
            tags = _social_tags(
                title=story.title or "A story from KathaSajha",
                description=_summarise(opening) or "An illustrated children's story made on KathaSajha.",
                url=f"{base}/shared/{slug}",
                image=f"{base}{cover}" if cover.startswith("/") else cover,
            )
            return HTMLResponse(_inject_social(shell, tags))

        @app.get("/reset-password", include_in_schema=False)
        async def spa_reset_password():
            return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

        @app.get("/privacy", include_in_schema=False)
        async def privacy_page():
            return FileResponse(os.path.join(FRONTEND_DIR, "privacy.html"))

        @app.get("/terms", include_in_schema=False)
        async def terms_page():
            return FileResponse(os.path.join(FRONTEND_DIR, "terms.html"))

        @app.get("/billing/return", include_in_schema=False)
        async def spa_billing_return():
            # Registered even while billing is dormant: Stripe return URLs are
            # configured once, and a 404 on the way back from a real payment is
            # the worst possible moment to discover a missing route.
            return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

        @app.get("/story/{story_id}", include_in_schema=False)
        async def spa_story(story_id: str):
            # Deep link; the SPA fetches the story itself with the user's token.
            return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    return app


app = create_app()
