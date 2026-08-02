# HTTP API

Base: same origin as the SPA. Auth: `Authorization: Bearer <jwt>`.
Interactive docs: `/api/docs`. Machine-readable: `/api/openapi.json`.

## Conventions

- Errors return `{"detail": "<human sentence>"}`. `detail` is always safe to show a user.
- Validation errors (422) return FastAPI's list form; the client reads `detail[0].msg`.
- Ids are 32-char hex strings.
- Times are ISO-8601 UTC.

## Auth

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/auth/register` | `{email, password (8..72 bytes), display_name?}` | 201 `{access_token, token_type}` |
| POST | `/api/auth/login` | `{email, password}` | 200 `{access_token, token_type}` |
| GET | `/api/auth/me` | | 200 `{id, email, display_name, plan, created_at}` |
| GET | `/api/auth/usage` | | 200 `{stories_today, daily_limit, remaining_today}` |

409 on duplicate email. 401 on bad credentials, missing, invalid, or expired token.

## Stories

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/stories` | `{prompt (3..500), language: en\|ne, hero_name?}` | 202 `{story_id, job_id}` |
| GET | `/api/stories?limit&offset` | | 200 `[{id, title, prompt, status, language, share_slug, created_at, cover_image_url}]` |
| GET | `/api/stories/{id}` | | 200 full story with `pages[]` |
| POST | `/api/stories/{id}/share` | | 200 `{share_slug, share_url}`, idempotent |
| DELETE | `/api/stories/{id}/share` | | 204 |
| DELETE | `/api/stories/{id}` | | 204, also deletes stored images |
| GET | `/api/stories/shared/{slug}` | no auth | 200 `{title, language, created_at, pages[]}` |

- 429 when the daily quota or burst limit is hit; `detail` explains which.
- 422 when the prompt is too short or too long.
- 409 when sharing an incomplete story or deleting one that is still generating.
- 503 when the queue cannot accept the job; the story is marked failed so it does not linger.
- 404 for another user's story, a missing story, or an unshared slug. Ownership is enforced on every read.

The public shared payload deliberately omits `prompt`, `error`, `provider`, `image_error`, and any
owner-identifying field.

## Jobs

| Method | Path | Returns |
|---|---|---|
| GET | `/api/jobs/{id}` | `{id, story_id, status, stage, progress_current, progress_total, error}` |

`stage` drives the progress copy: `queued`, `writing_story`, `illustrating`, `done`, `failed`.
A job with no heartbeat for 15 minutes is failed over on read, so clients always terminate.

## Health

`GET /api/health` returns `{status, environment, provider, job_backend}` after a database round trip.
Used by the compose healthcheck and any load balancer.
