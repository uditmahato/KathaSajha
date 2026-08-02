# KathaSajha — AI Story Generator (कथा साझा)

KathaSajha generates illustrated children's stories from a prompt, in English or Nepali. It is built as a production-grade system: FastAPI API, background job workers, Postgres, Redis, per-user accounts with daily quotas, and pluggable AI providers, all wired together with Docker Compose.

## Architecture

```
                        ┌────────────────────────────────────────────┐
 Browser (SPA)          │                Docker Compose              │
 frontend/  ────────►   │  api (FastAPI/uvicorn)   worker (ARQ)      │
  - auth, create,       │      │        │              │             │
    progress, library,  │      │        └── enqueue ───┤             │
    share, PDF          │      ▼                       ▼             │
                        │  Postgres 16            Gemini / Mock      │
                        │  (users, stories,       provider           │
                        │   pages, jobs)              │              │
                        │      ▲                      ▼              │
                        │  Redis 7 (queue,        media volume       │
                        │  rate limits)           (/media images)    │
                        └────────────────────────────────────────────┘
```

- **API** (`backend/app`) — FastAPI. Auth (JWT + bcrypt), story CRUD, share links, job progress polling, health checks. Serves the SPA and generated images.
- **Worker** — ARQ (Redis queue). Runs the generation pipeline: one structured-JSON Gemini call produces title + paragraphs + per-paragraph illustration prompts, then illustrations generate **in parallel** with per-image progress updates.
- **Providers** — `GENERATION_PROVIDER=auto` uses Gemini when `GOOGLE_API_KEY` is set, otherwise a **mock provider** that produces deterministic stories and locally-rendered illustrations, so the entire system is testable with no API key and zero cost.
- **Quotas** — daily story limit per user (DB-counted, plan-aware) + Redis burst rate limiting.
- **Storage** — images on a local volume by default; S3-compatible (R2/S3/GCS) via env vars.

## Quickstart (Docker — recommended)

```bash
cp .env.example .env       # defaults are fine for a local trial (mock mode)
docker compose up --build
```

Open http://localhost:8000 — sign up, create a story, watch the progress bar, download the PDF. Without a `GOOGLE_API_KEY` the app runs in **mock mode** end to end.

To use real AI generation, put your key in `.env`:

```
GOOGLE_API_KEY=your-key-here
```

and restart (`docker compose up -d`). The health endpoint reports the active provider: http://localhost:8000/api/health

## Morning test checklist

The stack may already be running (start it otherwise: `docker compose up -d`). Then:

1. Open http://localhost:8000 → sign up with any email/password.
2. Create a story (try the Show Example button). Watch the progress bar: writing → illustrating N of M.
3. The story opens automatically: illustrated pages, Save PDF, Share.
4. Click Share → open the copied `/shared/...` link in a private/incognito window (no login needed).
5. Create stories until the daily limit (3) blocks you with a friendly message; the header badge tracks remaining stories.
6. Try a Nepali story: switch Language to नेपाली.
7. `docker compose logs worker` shows each generation being processed.

Everything above runs in **mock mode** (no API key, zero cost). When you add `GOOGLE_API_KEY` to `.env` and run `docker compose up -d`, the same flow uses real Gemini stories and illustrations — check http://localhost:8000/api/health shows `"provider": "gemini"`.

## Local development (no Docker)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows; on mac/linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

With no configuration this uses SQLite, inline background jobs (no Redis needed), and the mock provider. Open http://localhost:8000.

### Tests

```bash
cd backend
.venv/Scripts/python -m pytest
```

Covers: auth, the full generation flow (job progress → pages → served images), user isolation, quotas, share links, validation, Nepali stories.

## API overview

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Create account → JWT |
| POST | `/api/auth/login` | Log in → JWT |
| GET | `/api/auth/me` | Current user |
| GET | `/api/auth/usage` | Stories used/remaining today |
| POST | `/api/stories` | Start story generation (202 → `story_id`, `job_id`) |
| GET | `/api/jobs/{id}` | Poll progress (stage, current/total) |
| GET | `/api/stories` | My story library |
| GET | `/api/stories/{id}` | Full story with pages |
| POST | `/api/stories/{id}/share` | Create public share link |
| GET | `/api/stories/shared/{slug}` | Public shared story (no auth) |
| DELETE | `/api/stories/{id}` | Delete story |
| GET | `/api/health` | Liveness/readiness |

Interactive docs: http://localhost:8000/api/docs

## Configuration

All via environment variables (see `.env.example`). Key ones:

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | dev value | **Must** be set in production (JWT signing) |
| `GOOGLE_API_KEY` | empty | Empty → mock provider |
| `GENERATION_PROVIDER` | `auto` | `auto` / `gemini` / `mock` |
| `DATABASE_URL` | SQLite | Compose sets Postgres automatically |
| `JOB_BACKEND` | `inline` | Compose sets `arq` (Redis worker) |
| `FREE_DAILY_STORIES` | 3 | Free-plan daily quota |
| `STORAGE_BACKEND` | `local` | `s3` + `S3_*` vars for R2/S3 |

## Scaling notes

- **More generation throughput**: `docker compose up --scale worker=3`. Workers are stateless; each processes up to 8 stories concurrently.
- **More API throughput**: raise uvicorn `--workers` in the Dockerfile CMD, or scale `api` behind a load balancer. The API is stateless (JWT), so horizontal scaling is safe.
- **Media at scale**: switch `STORAGE_BACKEND=s3` (Cloudflare R2 recommended) so images ship to object storage + CDN instead of the local volume.
- **Schema changes**: tables are auto-created on startup; introduce Alembic once the schema starts evolving in production.

## Project structure

```
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, static/SPA mounts
│   │   ├── config.py          # env-driven settings
│   │   ├── models.py          # User, Story, StoryPage, GenerationJob
│   │   ├── routers/           # auth, stories, jobs, health
│   │   ├── services/          # gemini + mock providers, pipeline
│   │   ├── worker.py          # ARQ worker entrypoint
│   │   ├── jobs.py            # queue dispatch (arq | inline)
│   │   ├── quota.py           # daily quotas + burst rate limits
│   │   └── storage.py         # local / S3 image storage
│   └── tests/                 # pytest suite
├── frontend/                  # vanilla-JS SPA (auth, progress, library, share, PDF)
├── Dockerfile                 # shared api/worker image
└── docker-compose.yml         # api + worker + postgres + redis
```
