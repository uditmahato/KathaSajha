# Architecture Decision Records

Append-only. Newest last. Each record states the decision, the alternatives, and the reason.

## ADR-001: Replace Flask prototype with FastAPI + async SQLAlchemy
**Status**: accepted
**Context**: The prototype was a single Flask file that generated a story and N images inside one HTTP
request, returned base64 images inline, and stored nothing.
**Decision**: Rebuild as FastAPI with async SQLAlchemy 2.0.
**Alternatives**: keep Flask with a thread pool; move to Django.
**Why**: The workload is IO-bound fan-out to a model API, which async handles natively. FastAPI gives
typed request/response schemas and OpenAPI docs for free. Django was too heavy for a service with four tables.

## ADR-002: Background queue (ARQ on Redis) with an inline fallback
**Status**: accepted
**Context**: Generation takes tens of seconds; hosting proxies commonly time out at 30-60s.
**Decision**: Enqueue generation and poll a job row. `JOB_BACKEND=inline` runs it as an asyncio task
for keyless local dev and tests.
**Alternatives**: Celery (heavier, sync-first), FastAPI BackgroundTasks (dies with the process, no visibility).
**Why**: ARQ is asyncio-native and small. The inline mode keeps `pip install && uvicorn` working with
no Redis, which matters for contributor onboarding and CI.

## ADR-003: Provider abstraction with an offline mock
**Status**: accepted
**Context**: Development against a paid API is slow, costly, and impossible without a key.
**Decision**: `GenerationProvider` interface with `GeminiProvider` and `MockProvider`.
`GENERATION_PROVIDER=auto` selects gemini when a key exists, else mock. The mock renders real PNG
illustrations locally with Pillow.
**Why**: The whole product, including images, PDFs, and progress UI, is testable offline and free.
CI exercises the real pipeline without secrets.

## ADR-004: One structured-JSON call for story text
**Status**: accepted
**Context**: The prototype made 1 story call + N summarization calls + N image calls per story.
**Decision**: A single call returns `{title, moral, paragraphs[], image_prompts[]}` via a response schema.
**Why**: Removes N calls (roughly halving cost and latency), removes fragile `# heading` parsing, and keeps
illustration prompts aligned to paragraphs by construction.

## ADR-005: Images to object storage, never inline in JSON
**Status**: accepted
**Context**: The prototype returned base64 images in the JSON response (33% size overhead, MBs per story,
uncacheable).
**Decision**: `Storage` interface. Local disk today, S3-compatible (R2) by config.
**Why**: Responses drop to kilobytes, images become CDN-cacheable, and server memory stops holding image bytes.

## ADR-006: Daily quota counted in the database, burst limited in Redis
**Status**: accepted
**Context**: Each story costs real money; abuse is a direct financial risk.
**Decision**: Authoritative daily count from the `stories` table (excluding failed stories), plus a Redis
fixed-window burst limiter that fails open. Story creation takes a `SELECT ... FOR UPDATE` on the user row
so concurrent requests cannot both pass the check.
**Why**: Redis alone is not durable enough to be the source of truth for billing-adjacent limits; the DB
alone cannot cheaply stop bursts. Failed stories are not charged to the user because they are our fault.

## ADR-007: Vanilla JS SPA instead of a framework
**Status**: accepted, revisit at the subscription milestone
**Context**: The UI is five views with no complex shared state.
**Decision**: Plain ES5-compatible JS with explicit DOM construction, no build step.
**Why**: Zero build tooling, instant load, no dependency treadmill, and it kept the whole frontend under
500 lines. Revisit if the surface grows past roughly a dozen views or needs real routing and state sharing.
All text goes into the DOM with `textContent`, never `innerHTML`, so model output cannot inject markup.

## ADR-008: Landing page for logged-out visitors
**Status**: accepted
**Context**: The app opened directly on a login form, which explains nothing to a first-time parent.
**Decision**: A marketing landing view is the default for logged-out visitors, with hero, how-it-works,
features, and calls to action; the auth form is one click away. Shared-story visitors also see the
signup call to action.
**Why**: Consumer products must sell before they ask for an email. The shared-story page is the growth
loop and needed a conversion path.
