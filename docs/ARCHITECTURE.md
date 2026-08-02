# Architecture

## System shape

```
Browser (vanilla JS SPA, frontend/)
  |  JWT in Authorization header
  v
API (FastAPI, uvicorn)  --- enqueue ---> Redis ---> Worker (ARQ)
  |                                                    |
  |-- Postgres (users, stories, story_pages,           |-- Generation provider
  |             generation_jobs)  <--- progress -------|   (Gemini | Mock)
  |                                                    |
  |-- Media (local volume now, S3/R2 later)  <---------|   image bytes
```

Everything runs from one Docker image; `docker-compose.yml` starts `api`, `worker`, `db`, `redis`.
The API also serves the SPA and, in local mode, the generated images at `/media`.

## Components

| Component | File(s) | Responsibility |
|---|---|---|
| Config | `backend/app/config.py` | All environment-driven settings, one source of truth. `resolved_provider` picks gemini vs mock |
| DB access | `backend/app/db.py` | Async engine, session factory, `init_db` with a Postgres advisory lock |
| Models | `backend/app/models.py` | `User`, `Story`, `StoryPage`, `GenerationJob` |
| Auth | `backend/app/security.py`, `deps.py` | bcrypt hashing, HS256 JWT, `CurrentUser` dependency |
| Quotas | `backend/app/quota.py` | DB-counted daily quota (authoritative) + Redis burst limiter (fails open) |
| Routers | `backend/app/routers/*.py` | HTTP surface only. No business logic beyond orchestration |
| Providers | `backend/app/services/{gemini,mock}.py` | Swappable generation backends behind `services/base.py` |
| Pipeline | `backend/app/services/pipeline.py` | Owns the story lifecycle and job progress. Runs in worker or inline |
| Jobs | `backend/app/jobs.py`, `worker.py` | Dispatch (ARQ or inline asyncio) and the ARQ worker entrypoint |
| Storage | `backend/app/storage.py` | `LocalStorage` / `S3Storage` behind one interface |

## Generation flow

1. `POST /api/stories` validates, locks the user row, enforces quota, inserts `Story` + `GenerationJob`
   (`status=queued`), enqueues, returns `202` with `story_id` and `job_id`.
2. The worker runs `run_generation(story_id)`:
   - stage `writing_story`: ONE structured-JSON provider call returns title, paragraphs, and one
     illustration prompt per paragraph. This replaced the original design's N extra summarization calls.
   - persists title and page skeletons (deleting any existing pages first, so a queue retry is idempotent).
   - stage `illustrating`: all illustrations generate concurrently under a semaphore
     (`image_concurrency`). Each completion writes its image and bumps `progress_current` under a lock.
   - marks story `complete`.
3. The browser polls `GET /api/jobs/{id}` for stage and progress, then loads the story.

Story-text failure fails the job with a friendly message. Individual image failures degrade that page only.

## Why it is built this way

- **Queue over long HTTP request**: a story takes tens of seconds with a real model. Long requests die
  behind proxies and give no progress. The queue also lets generation scale separately from the API.
- **Provider abstraction with a mock**: the entire product is developable, testable, and demoable with
  no API key and zero cost. CI runs the real pipeline in mock mode.
- **One structured call for text**: fewer round trips, lower cost, and no fragile string parsing of
  markdown headings.
- **Progress written to the DB, not held in memory**: any API replica can answer a poll, and progress
  survives a worker restart.
- **Storage behind an interface**: local disk is fine at zero users; moving to R2/S3 is a config change,
  not a rewrite.

## Known architectural constraints

- Schema is created with `create_all` plus an advisory lock. There are no migrations yet; the first
  production schema change with real data needs Alembic (see TECH_DEBT).
- `job_backend=inline` exists for keyless dev and tests only. It loses in-flight work on restart, which
  startup reconciliation now fails cleanly rather than leaving stuck stories.
