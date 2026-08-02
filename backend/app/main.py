"""KathaSajha API — FastAPI application entrypoint.

Run (dev):   uvicorn app.main:app --reload --port 8000    (from backend/)
Run (prod):  see Dockerfile / docker-compose.yml
"""

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import dispose_engine, init_db
from .jobs import close_job_pool
from .observability import CorrelationMiddleware, configure_logging
from .quota import close_redis
from .routers import auth, health, jobs, plans, stories

configure_logging()
logger = logging.getLogger(__name__)

FRONTEND_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()
    _KNOWN_DEV_SECRETS = {"change-me-in-production", "dev-only-secret-change-me", "test-secret"}
    if settings.environment == "production" and (
        settings.secret_key in _KNOWN_DEV_SECRETS or len(settings.secret_key) < 32
    ):
        raise RuntimeError("SECRET_KEY must be set to a long random value in production")
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


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are buffered into memory.

    Starlette imposes no limit of its own, and FastAPI reads and parses the whole
    body before the first field validator runs, so no schema constraint can stop
    a multi-megabyte POST from being materialised. Written as pure ASGI rather
    than BaseHTTPMiddleware so it can count bytes as they arrive, which also
    covers chunked requests that carry no Content-Length to check.
    """

    _BODY_METHODS = ("POST", "PUT", "PATCH")

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in self._BODY_METHODS:
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
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
                if seen > self.max_bytes:
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
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)

    media_prefix = settings.media_url_prefix.rstrip("/") + "/"

    # Tight by default; only the hosts the app genuinely uses are allowed.
    # No 'unsafe-inline' for scripts: all JS lives in /assets/app.js.
    CSP = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' https://cdnjs.cloudflare.com",  # html2pdf, loaded on demand
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

    @app.middleware("http")
    async def security_and_cache_headers(request: Request, call_next):
        response = await call_next(request)
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
        async def spa_shared(slug: str):
            return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

        @app.get("/reset-password", include_in_schema=False)
        async def spa_reset_password():
            return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

        @app.get("/story/{story_id}", include_in_schema=False)
        async def spa_story(story_id: str):
            # Deep link; the SPA fetches the story itself with the user's token.
            return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    return app


app = create_app()
