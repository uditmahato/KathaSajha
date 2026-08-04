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

## ADR-009: English is the source, not a catalogue; only other locales get a file
**Status**: accepted
**Context**: The UI carried ~130 English strings in `index.html` and ~55 built at runtime in `app.js`.
The obvious design extracts all of them into `en.json` and `ne.json` and renders from the catalogue.
**Decision**: English stays literally where it is — in the markup, annotated with `data-i18n="key"`,
and in an `EN` object inside `frontend/i18n.js` for strings the app constructs. `frontend/i18n/ne.js`
is the only catalogue, loaded only when the locale is not English.
**Why**: "Nothing regresses for an English user" becomes structural instead of a promise — an English
visitor makes the same requests, receives the same bytes, and would still see correct English if every
line of the i18n code were deleted. The failure mode of every bug in the applier is "the app is in
English", never "the app is broken" and never "the app shows i18n.key.name".

## ADR-010: The catalogue is applied client-side, not rendered server-side
**Status**: accepted
**Context**: CSP is `script-src 'self'` with no `'unsafe-inline'`, so there is no inline `<head>` script
available to pre-empt the paint. The alternative was rendering the shell per locale in FastAPI, extending
the seam that already rewrites `index.html` for social previews.
**Decision**: Client-side. `i18n.js` loads before `app.js`, fetches the catalogue, translates the DOM,
and resolves `KS_I18N.ready`; `app.js` gates its entire boot router on that promise.
**Why**: Every `<section class="view">` already ships `class="hidden"` and is revealed only by `show()`,
which now runs inside the gate — so the landing hero, the screen that decides signups, cannot flash from
English to Nepali. Only the header and footer paint for one frame in English, which is accepted.
Server rendering would have converted six routes from `FileResponse` to `HTMLResponse` (losing 304
revalidation), required `Vary: Cookie` on every one of them, and made the `/shared/{slug}` social-meta
injection order load-bearing — three failure modes, on the growth loop, to remove one frame of chrome.

## ADR-011: Server errors carry a code beside their English prose, not an Accept-Language translation
**Status**: accepted
**Context**: ~44 `HTTPException(detail="English sentence")` sites, plus English sentences frozen into
`Story.error` and `GenerationJob.error`.
**Decision**: `CodedHTTPException` adds `code` and `params` as **siblings** to `detail`; `detail` stays an
English string forever. `Story.error_code` / `GenerationJob.error_code` are written alongside the existing
prose on every failure. The client prefers a `srv.<code>` translation and falls back to the server's prose.
**Why**: The string most worth translating is written by the ARQ worker — a separate process started from
a job id, with no request and no headers in scope. A code travels through a database column; a header does
not. Keeping `detail` a string means an older client is unaffected (`app.js` tests
`typeof detail === 'string'`, so an object would degrade every error to "Request failed (429)"), and
keeping the prose column means rows written before the codes existed, or by a worker deployed ahead of the
API, still render.

## ADR-012: Numerals stay Latin in both locales
**Status**: accepted
**Context**: CLDR's default numbering system for `ne` is `deva`, so `toLocaleDateString('ne-NP')` emits
`३ अगस्ट` while `toFixed(2)` prices and quota counts stay Latin — on the same library card.
**Decision**: Latin digits everywhere, including inside Nepali prose in the catalogue. Dates use an
explicit `ne-NP-u-nu-latn`. Pinned by a test that rejects Devanagari digits in `ne.js`.
**Why**: Decided once and written down, rather than letting ICU decide it implicitly per call site.
Prices and quota limits cannot be Devanagari without more work, and mixed numerals are worse than either
choice made consistently.
