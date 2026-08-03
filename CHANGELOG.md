# Changelog

## Iteration 4: the PDF becomes a book

### Added
- **Server-side PDF rendering** (`GET /api/stories/{id}/pdf`, and
  `GET /api/stories/shared/{slug}/pdf` so grandparents without accounts get the
  book too). Real storybook layout: cream cover with framed art and the child's
  name, one scene per page with vector text and page numbers, a back cover.
  Page count is exactly pages + 2, a property the tests now pin by parsing the
  output with pypdf. Devanagari renders through real text shaping (uharfbuzz);
  fonts resolve per-platform (Noto in the image and CI, system fonts on a dev
  box), verified visually in both languages.

### Removed
- **html2pdf and the last CDN script.** The old exporter screenshotted the DOM
  into JPEGs, and a CSS sizing bug shipped two blank sheets after every content
  page: a 5-page story exported as 15 pages, 10 of them blank, with a blank
  cover. Found by opening a real exported file, not by any test. The CSP now
  reads `script-src 'self'`.

### Fixed
- The mock provider interpolated a lowercase fallback hero mid-sentence
  ("...celebrated. our young hero had learned"), visible in exported books.

## Iteration 3: request hardening and session revocation

### Added
- **Request body ceiling** (`MAX_REQUEST_BODY_BYTES`, default 64 KB) enforced by ASGI middleware that
  checks `Content-Length` and counts streamed bytes, so chunked requests carrying no declared length
  are covered too. Field constraints could not do this: the body is fully buffered and parsed before
  the first validator runs, which made an unauthenticated POST a memory-exhaustion vector.
- **Session revocation**: `users.token_version` is carried in every access token and checked on each
  request. Changing or resetting a password bumps it, retiring every token issued beforehand.
  `change-password` returns a replacement token so securing an account does not log you out of it.
- **`TRUSTED_PROXY_IPS`**: `X-Forwarded-For` is now believed only when the direct peer is a configured
  proxy. Empty by default.
- **Subresource integrity** on the html2pdf CDN script, so a hijacked CDN response cannot execute and
  read the token out of `localStorage`.
- Tests for all of the above, including that a spoofed `X-Forwarded-For` collapses to the real peer.

### Fixed
- A test patched `asyncio.sleep` with a lambda whose body called `asyncio.sleep`, which resolved to the
  lambda itself and recursed until the stack limit.
- The Gemini test fakes replaced `sys.modules["google"]` with a plain namespace, making it a non-package
  so `from google.genai import types` failed in eight tests. Only the client is faked now, so the tests
  exercise the real SDK types and will notice signature drift.
- `test_hung_call_times_out_and_is_retried` set the call timeout to 0, which makes `asyncio.wait_for`
  cancel the coroutine before it starts, so the retry path it exists to cover never ran.
- Unsorted imports and formatting in `migrations/env.py`, invisible because CI scopes ruff to `app tests`.
- `.gitignore` did not cover `.ruff_cache/` or `.coverage`.

## Iteration 2: launch-readiness (in progress)

### Added
- **Project memory** in `docs/`: architecture, ADRs, schema, API contract, UI/UX patterns, roadmap,
  tech debt, and lessons learned. Read `docs/PROJECT_MEMORY.md` first.
- **Landing page** for logged-out visitors: hero, how-it-works, six feature cards, calls to action.
  Shared-story visitors now also see a signup path, closing the growth loop.
- **Account recovery**: forgot password, single-use expiring reset links, and change password.
  Email goes through a pluggable sender; the console backend prints the link to the log, so the
  whole flow works with no SMTP credentials.
- **Alembic migrations** with a baseline revision, plus a one-shot compose `migrate` service that
  api and worker wait on. `alembic check` runs in CI, so editing a model without a migration fails
  the build.
- **Structured JSON logging** with correlation ids: every API request gets an id (returned as
  `X-Request-ID`), and every worker job is tagged with its story id, so one user's report is
  traceable end to end.
- **Brute-force protection** on register, login, forgot-password, and reset-password, limited by
  both client IP and target email.
- **Ruff** linting and formatting with a security ruleset, enforced in CI.
- **Progressive story reveal**: the story text now appears as soon as it is written, with
  illustrations streaming into shimmering placeholders. Previously the reader waited for every
  image before seeing a single word.
- **Toasts and an accessible confirmation dialog** replacing `window.alert` and `window.confirm`
  (focus trap, Escape to cancel, focus restored on close).
- **Accessibility**: skip link, focus moved into each view on navigation, visible focus rings,
  live-region progress with `aria-valuenow`, keyboard-operable library cards, descriptive alt text
  built from the page text, `lang="ne"` on Nepali content, and `prefers-reduced-motion` support.

### Fixed
- `db` and `redis` had no restart policy, so a stopped database never came back and the API
  crash-looped. Observed live during this iteration.
- `LocalStorage.save_image` did blocking disk I/O inside an async function, stalling the event loop
  during every illustration write. Now offloaded to a thread.
- Story deletion swallowed media-cleanup failures silently; orphaned files were undiscoverable.
- Enqueue failures were raised without the original exception context and were never logged.

## Iteration 1: rebuild as a production system

### Added
- FastAPI backend with JWT accounts, story CRUD, share links, job progress, health checks.
- ARQ worker on Redis with an inline asyncio fallback for keyless development and tests.
- Generation provider abstraction: `GeminiProvider` (google-genai, one structured-JSON story call plus
  parallel illustrations) and `MockProvider` (offline stories and Pillow-rendered images, no API key).
- Postgres and Redis via docker compose, plus a shared api/worker image.
- Storage abstraction: local disk now, S3-compatible by configuration.
- Daily quotas per plan, Redis burst limiting, prompt length caps, child-safety and prompt-injection
  rules in the story instruction.
- Vanilla JS SPA: auth, story creation with live progress, library with covers, reader, public share
  pages, client-side PDF, Nepali language option, hero-name personalization.
- 19 pytest tests and a GitHub Actions workflow that runs them plus a Docker smoke test.
- Landing page for logged-out visitors with hero, how-it-works, features, and calls to action.

### Fixed (from the original Flask prototype)
- Illustrations could attach to the wrong paragraphs when any image failed.
- Deprecated `google-generativeai` SDK and deprecated model ids replaced with `google-genai`.
- Base64 images inlined in JSON responses replaced with stored images and URLs.
- Story text inserted with `innerHTML` (XSS via model output) replaced with `textContent`.
- Unused imports, unpinned dependencies, and a stylesheet that was never linked.

### Fixed (found by the audit passes during the rebuild)
- Production `SECRET_KEY` guard could be bypassed by the compose default.
- Daily quota bypass under concurrent requests (now a row lock before counting).
- Internal exception text leaked to public share pages.
- Image prompts padded with the wrong paragraph when the model returned fewer prompts than pages.
- Duplicate pages on queue retry (now idempotent, with a uniqueness constraint).
- Postgres healthcheck false positive during initdb, and a three-process `create_all` race.
- Stories stuck "generating" forever after a refresh or worker crash; they now fail over and are deletable.
- Progress bar could move backwards under concurrent illustration writes.
- bcrypt silently truncating passwords over 72 bytes.
- Frontend: blank page on a rejected promise, polling surviving logout, misleading clipboard message.
