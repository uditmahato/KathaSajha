# KathaSajha — AI Story Generator (कथा साझा)

KathaSajha turns one idea into an illustrated children's story, in English or Nepali, starring the
child as the hero. Built for **Non-Resident Nepali parents abroad** whose children are losing the
language (see [ADR-013](docs/DECISIONS.md)) — not for families in Nepal, which is a different
product and decides several things in this repo.

FastAPI plus a background worker, Postgres and Redis, a vanilla-JS SPA with no build step, and
provider abstractions for generation, storage, email and billing so the whole system runs offline
with no API key and no cost.

> **Status: pre-launch.** The software is close to complete; the product has not been validated.
> It has never been deployed, and until recently every story ever generated came from the mock
> provider. Read [Honest status](#honest-status) before drawing conclusions.

## Architecture

```
                        ┌────────────────────────────────────────────┐
 Browser (SPA)          │                Docker Compose              │
 frontend/  ────────►   │  api (FastAPI/uvicorn)   worker (ARQ)      │
  - landing, auth,      │      │        │              │             │
    create, progress,   │      │        └── enqueue ───┤             │
    library, reader,    │      ▼                       ▼             │
    share, PDF, i18n    │  Postgres 16            Gemini / Mock      │
                        │  (users, children,      provider           │
                        │   stories, pages,           │              │
                        │   jobs, ledger)             ▼              │
                        │      ▲                  media volume       │
                        │  Redis 7 (queue,        (/media images)    │
                        │  rate limits)                              │
                        └────────────────────────────────────────────┘
```

- **API** (`backend/app`) — auth (JWT + bcrypt, with session revocation via `token_version`),
  stories, child profiles, plans, billing, job polling, health. Serves the SPA and media.
- **Worker** — ARQ over Redis. One structured-JSON call produces title, paragraphs and
  per-paragraph illustration prompts; illustrations then generate **in parallel** with per-image
  progress. Story-text failure fails the job; individual image failures degrade gracefully.
- **Providers** — generation, storage, email and billing are all interfaces with mock
  implementations, so the full system is testable with no credentials and zero spend.
- **Quotas** — an append-only `GenerationEvent` ledger is the source of truth (daily and monthly
  per user, plus a platform-wide daily ceiling), with Redis burst limiting in front. Failed
  generations are refunded automatically.
- **PDF** — rendered server-side with fpdf2 and uharfbuzz: vector text, a real cover, page
  numbers, and correct Devanagari shaping.

## What is built

| Area | Detail |
|---|---|
| Accounts | Register, log in, password reset by emailed single-use token, change password, full in-app account deletion. Changing a password retires every existing session. |
| Generation | English or Nepali, 3-5 pages, one illustration per page, live progress. Prompt-injection defence: caller-supplied text never enters the instruction section, only a nonce-delimited data block. |
| Child profiles | Saved children with an optional **age band** (never a birthday) that sets the reading level, plus companion characters. Names are snapshotted into each story, so deleting a profile never rewrites a book already on the shelf. |
| Reading levels | Five bands with per-band sentence and word limits, and a safety floor applied to every band regardless. |
| Sharing | Public `/shared/{slug}` pages with per-story social previews, so a forwarded link previews as that child's story rather than a bare URL. |
| Plans and billing | Plans, entitlements and demand capture. Stripe is **fully integrated and deliberately dormant** — hosted checkout, billing portal, signature-verified webhooks with idempotency and an out-of-order watermark. It turns on with three values in `.env` and a restart, no code change. |
| Interface languages | English and Nepali, with no build step and no i18n library. English stays literal in the markup; only a non-English locale loads a catalogue. Sixteen CI gates fail the build on a new untranslated string, a missing key, or a dropped placeholder. |
| Error messages | Errors carry a stable `code` and `params` beside the English `detail`, including failures frozen into database rows by the worker — so a Nepali parent gets Nepali when a story fails or a quota wall appears. |
| Cost telemetry | Token and image counts recorded per generation, so cost can be recomputed for historical rows once rates are set. |
| Operations | Alembic migrations with a CI drift check, structured logs with request ids, Sentry wiring (dormant), and production guards that refuse to boot on half-configuration. |

## Quickstart (Docker)

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

Open http://localhost:8000 — sign up, create a story, watch the progress, download the PDF.
Everything works end to end with **no API key and zero cost**.

For real generation, put a Google AI Studio key in `.env` and restart. `GET /api/health` reports
the active provider.

> **Illustrations need Google Cloud billing.** Every Gemini image model returns `429 … limit: 0`
> on the free tier — unavailable, not merely exhausted. Text generation works on a free key. To
> read *real* stories with placeholder art while billing is off, set `IMAGE_PROVIDER=mock`.
> Production refuses to boot in that combination.

## Local development (no Docker)

```bash
cd backend && python -m venv .venv && pip install -r requirements-dev.txt
```

```bash
uvicorn app.main:app --reload --port 8000
```

Defaults to SQLite, inline jobs (no Redis needed), and the mock provider.

`.claude/launch.json` also defines three ready-made configurations: `kathasajha-local` (mock,
port 8000), `kathasajha-real-stories` (real Gemini text with placeholder art, port 8020), and
`kathasajha-billing-demo` (the full checkout flow against the mock billing provider, port 8010).

### Tests

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

218 tests across 16 files: auth and session revocation, the full generation pipeline, quota and
ledger accounting, user isolation, share links and social previews, request-size limits, PDF
output parsed as a real artifact, the Stripe SDK boundary against the pinned SDK, child profiles
and reading levels, unit economics, release gates, and the i18n drift gates.

> On Windows, run the suite through a memory-capped wrapper. A bare `pytest` once reached 58 GB
> and took the development machine down; the cause was never reproduced. See
> [docs/LESSONS.md](docs/LESSONS.md).

## API overview

Errors return `{"detail": "<sentence>"}`, and reachable errors also carry `code` and `params`.
`detail` is always a string. Full reference in [docs/API.md](docs/API.md); interactive docs at
`/api/docs`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` · `/login` | Create account / log in, returns a JWT |
| GET | `/api/auth/me` · `/usage` | Current user; stories used and remaining |
| POST | `/api/auth/forgot-password` · `/reset-password` | Emailed single-use reset token |
| POST | `/api/auth/change-password` | Retires every session, returns a fresh token |
| DELETE | `/api/auth/me` | Erase the account (password re-entry required) |
| POST | `/api/stories` | Start generation (202, returns `story_id` and `job_id`) |
| GET | `/api/stories` · `/{id}` | Library; full story with pages and cast |
| GET | `/api/stories/{id}/pdf` | Server-rendered PDF storybook |
| POST | `/api/stories/{id}/share` | Create a public share link |
| GET | `/api/stories/shared/{slug}` · `/pdf` | Public story and PDF, no auth |
| DELETE | `/api/stories/{id}` · `/{id}/share` | Delete story; revoke share link |
| GET | `/api/jobs/{id}` | Poll progress (stage, current/total) |
| GET | `/api/plans` | Plan catalogue with live purchasability |
| POST | `/api/plans/interest` | Demand capture while a plan is not yet buyable |
| GET/POST/PUT/DELETE | `/api/profiles/children` · `/companions` | Saved children and characters |
| POST | `/api/billing/checkout` · `/portal` · `/webhook` | Mounted only when billing is configured |
| GET | `/api/health` | Liveness and active providers |

## Configuration

All via environment variables — see [`.env.example`](.env.example). The application **refuses to
boot** rather than run half-configured in production: console email, partial Stripe credentials, a
weak `SECRET_KEY`, or placeholder illustrations alongside real stories.

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | dev value | Must be set in production; validated for entropy |
| `GOOGLE_API_KEY` | empty | Empty means the mock provider |
| `GENERATION_PROVIDER` | `auto` | `auto` / `gemini` / `mock` |
| `IMAGE_PROVIDER` | `auto` | `mock` gives real stories with placeholder art (dev only) |
| `STORY_MODEL` | `gemini-3.6-flash` | Chosen on measured name fidelity |
| `DATABASE_URL` | SQLite | Compose sets Postgres |
| `JOB_BACKEND` | `inline` | Compose sets `arq` |
| `FREE_DAILY_STORIES` / `FREE_MONTHLY_STORIES` | 3 / 10 | The monthly figure is the real allowance |
| `GLOBAL_DAILY_GENERATION_LIMIT` | 500 | Platform-wide ceiling on spend |
| `STORAGE_BACKEND` | `local` | `s3` plus `S3_*` for R2 or S3 |
| `EMAIL_BACKEND` | `console` | `smtp` plus `SMTP_*` required in production |
| `STRIPE_SECRET_KEY` · `STRIPE_WEBHOOK_SECRET` · `STRIPE_PRICE_IDS` | empty | All three, or billing stays off |
| `PRICE_PER_*` | 0 | Provider rates; until set, every generation logs $0 |
| `SENTRY_DSN` | empty | Error tracking, dormant until set |

## Honest status

Kept in the README rather than a private doc, because the gap between "the code works" and "the
product works" is the most important thing to know about this repository.

- **Never deployed.** Every item under Deploy in [docs/GO_LIVE.md](docs/GO_LIVE.md) is open.
- **Illustrations are unverified.** Image generation requires billing, so no real illustration has
  ever been produced or reviewed. The art *is* the product, and its quality is unknown.
- **The Nepali is AI-written and not native-speaker reviewed.** The mechanism is gated by tests;
  the words are the risk. `frontend/i18n/ne.js` is marked `pending_native_speaker_review`.
- **Legal review outstanding**, across several jurisdictions at once — the diaspora concentrates in
  COPPA, the UK Children's Code and GDPR-K simultaneously.
- **Unit economics unverified.** Text is measured at roughly 490 in and 490 out tokens per story, a
  fraction of a cent. Images are the cost driver and cannot be measured until billing is on.
- **A leaked `GOOGLE_API_KEY` in git history** (commit `b3f882e`) must be rotated. The repo is
  public, and deleting the file does not help.
- **The landing page shows no example story**, which for a visual product is the main conversion
  gap. The public share-link infrastructure needed to fix it already exists.

Ranked detail in [docs/TECH_DEBT.md](docs/TECH_DEBT.md).

## Documentation

| File | What it answers |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit together |
| [DECISIONS.md](docs/DECISIONS.md) | Fourteen ADRs — why it is built this way |
| [SCHEMA.md](docs/SCHEMA.md) · [API.md](docs/API.md) | Tables and endpoints |
| [ROADMAP.md](docs/ROADMAP.md) | What is done and what is next |
| [TECH_DEBT.md](docs/TECH_DEBT.md) | Known gaps, ranked by what hurts at launch |
| [GO_LIVE.md](docs/GO_LIVE.md) | Everything the codebase cannot contain |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Running it in production |
| [LESSONS.md](docs/LESSONS.md) | Bugs that cost real time, each with a rule |
| [UI_UX_PATTERNS.md](docs/UI_UX_PATTERNS.md) | Interface rules and the review checklist |

## Project structure

```
├── backend/
│   ├── app/
│   │   ├── main.py            # app factory, SPA + social-preview serving, security headers
│   │   ├── config.py          # env-driven settings and provider resolution
│   │   ├── models.py          # User, ChildProfile, CompanionCharacter, Story, StoryPage,
│   │   │                      #   GenerationJob, GenerationEvent, PasswordResetToken, ...
│   │   ├── errors.py          # coded errors: a stable `code` beside the English prose
│   │   ├── quota.py           # ledger-backed quotas, global ceiling, burst limits
│   │   ├── plans.py           # plan catalogue and entitlements
│   │   ├── routers/           # auth, stories, jobs, plans, profiles, billing, health
│   │   ├── services/
│   │   │   ├── gemini.py        # real provider; instruction building and injection defence
│   │   │   ├── mock.py          # deterministic stories + locally drawn illustrations
│   │   │   ├── pipeline.py      # generation lifecycle, parallel illustration, refunds
│   │   │   ├── cast.py          # story cast snapshot and coverage measurement
│   │   │   ├── reading_level.py # age bands to length, sentence and safety rules
│   │   │   ├── pdf.py           # server-side storybook with Devanagari shaping
│   │   │   ├── email.py         # console and SMTP senders
│   │   │   └── billing/         # Stripe and mock billing providers
│   │   └── worker.py          # ARQ entrypoint
│   ├── migrations/            # Alembic, ten revisions, drift-checked in CI
│   └── tests/                 # 218 tests
├── frontend/                  # vanilla JS SPA, no build step, CSP script-src 'self'
│   ├── i18n.js                # translation runtime
│   └── i18n/ne.js             # Nepali catalogue
├── docs/                      # architecture, decisions, debt, go-live
├── Dockerfile                 # shared api/worker image
└── docker-compose.yml         # api + worker + postgres + redis
```
