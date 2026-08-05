# Technical debt, risks, and known gaps

Ranked by what would hurt most at launch, **for the user named in ADR-013: a Non-Resident Nepali
parent abroad raising a child who is losing the language.** That framing decides several rankings
here — most visibly, a Nepal-domestic payment rail is not a launch gap, and a child's inability to
read Devanagari is.

Updated every iteration. Items move to CHANGELOG when fixed.

## Critical (blocks launch)

| Item | Impact | Status |
|---|---|---|
| Leaked `GOOGLE_API_KEY` in git history (commit `b3f882e`) | Anyone who clones the public repo can bill the owner's account | OPEN. Owner must rotate the key in Google AI Studio. Deleting the file does not help; the object is still in history |
| No error tracker | Sentry wiring shipped, dormant until a DSN exists | PARTIAL. Needs a Sentry project + SENTRY_DSN |
| Legal surface for a children's product, in several jurisdictions at once | Drafts of privacy/terms shipped and a full in-app deletion path exists. Targeting NRN raises this bar rather than lowering it: the diaspora concentrates in exactly the strictest regimes — COPPA in the US, the Children's Code in the UK, GDPR-K in the EU, plus Australia. A single-jurisdiction review does not cover the audience | PARTIAL. See GO_LIVE.md. Tell your counsel which countries you will actually accept signups from |

## High

| Item | Impact | Status |
|---|---|---|
| The child may not be able to read the story | An NRN child often understands spoken Nepali but was never schooled in Devanagari, so a Nepali story can be unreadable to exactly the reader it was written for — and the PDF keepsake becomes decorative rather than legible. The product currently renders Nepali in Devanagari only, with no audio | OPEN. The central product risk for this market. Answer is per-child (ADR-014): read-aloud audio, romanized Nepali, or both. Needs real NRN parents asked before building, not a guess |
| Nothing can be bought yet | Stripe is fully integrated — hosted checkout, billing portal, signature-verified webhooks with idempotency and an out-of-order watermark, subscription lifecycle, SDK boundary tests — but DORMANT until credentials exist, which is deliberate. `is_purchasable()` derives buyability from configuration, so the pricing page honestly says "Opening soon" rather than showing a button that leads nowhere | PARTIAL. Owner action only: three values in `.env` and a restart, no code change. See GO_LIVE.md. Verify with `.claude/launch.json` -> `kathasajha-billing-demo`, which runs the whole flow against the mock provider |
| Frontend has no RUNTIME tests | `app.js` is 1,219 lines and nothing ever executes it. `backend/tests/test_i18n.py` analyses the frontend files statically (16 gates over markup, catalogues and structure), which is real coverage but cannot catch a runtime fault. Proven concretely in the i18n cycle: `const t = token()` shadowed the translator, so every expired session showed the parent `t is not a function` — and that shipped through 215 green tests AND manual browser verification, because the check exercised a path `api()` excludes. Found only by adversarial review | OPEN. Highest-value engineering gap. A DOM runner (jsdom/vitest, or Playwright against the dev server) would have caught it in seconds |
| No type checking or dependency scanning in CI | Ruff lint and format run, plus a migration drift check. Nothing catches type errors or vulnerable dependencies | PARTIAL |
| Quota day boundary is UTC | No single timezone is correct for a diaspora: UTC midnight is 7pm in New York, 4pm in Los Angeles and 11am in Sydney, so a parent can lose a day's allowance mid-evening with no explanation. Previously logged as "resets mid-morning in Kathmandu", which mis-stated both the cause and the fix | OPEN. The fix is a per-user timezone or a rolling 24-hour window, NOT "use Nepal time" |

## Medium

| Item | Impact | Status |
|---|---|---|
| Mock illustrations are ~100 DPI in the PDF | Text is now vector and crisp at any size, but a 768x512 image on a 6.3in page prints soft. True print-on-demand needs ~2550px images, which raises generation cost | OPEN. Blocks Horizon 3 print, not screen use |
| English visitors pay for i18n they never use | `/assets/i18n.js` is an unconditional blocking script (~5.9 KB gzipped) and the `data-i18n` attributes added ~1.1 KB gzipped to `index.html`. Both revalidate to 304 after first load, so the cost is one round trip and ~7 KB on a cold visit. The legal pages, which previously loaded no JS at all, now block on it too | OPEN. Splitting a ~1 KB switcher-only file for the legal pages would recover most of it |
| Nepali catalogue is not native-speaker reviewed | `frontend/i18n/ne.js` was written by an AI. The mechanism is sound and gated; the *words* are the risk, and clunky Nepali undercuts the one thing the product is differentiated on. Nine misspellings of "free" were found only by pointing an adversarial reviewer at the file | OPEN. Owner item in GO_LIVE.md. Note the UI locale matters less for NRN than assumed — the buying parent usually reads English (ADR-013) — so this is about the signal it sends and about grandparents opening share links, not about comprehension |
| Pricing is anchored to Nepal, not to the buyer | Plus shows "$6.00" with "about NPR 799 a month" beneath it. For an NRN buyer in the US or UK the NPR figure is not their currency, $6 is trivially affordable, and the low anchor may actively depress perceived value | OPEN. Product decision, not a defect. Worth testing a higher price before launch rather than after |
| PDF furniture stays English inside a Nepali book | `services/pdf.py` already branches on `story.language` for the page text, "समाप्त", and the missing-illustration note, but the colophon (`a KathaSajha storybook`, `Made for X with KathaSajha`, `%B %Y`) is drawn with `set_font("latin")`. Putting Devanagari in those strings without also fixing the font family yields tofu or a raised exception on the paid-feature path | OPEN. Deliberately deferred — the font-family trap makes this more expensive than it looks |
| Pydantic 422 messages reach users in English | `schemas.py` raises ~14 English `ValueError`s and FastAPI adds its own ("String should have at least 8 characters"). The client now renders a translated generic instead of `detail[0].msg`, which loses field specificity | PARTIAL. Field-level codes would need a `RequestValidationError` handler |
| `PlanOut.features` is server-sent English marketing copy | Rendered verbatim into the upgrade modal at the moment of highest purchase intent, and served to signed-out visitors. Not covered by the client catalogue | OPEN. Needs `feature_keys` beside the existing strings |

| Item | Impact | Status |
|---|---|---|
| User enumeration on `/register` | Returns 409 for an existing address, while `/forgot-password` deliberately avoids exactly this leak. The customer list is enumerable | OPEN |
| Postgres connection ceiling | `pool_size` 10 + `max_overflow` 20 per process, times 2 uvicorn workers plus the worker, is up to 90 against a default `max_connections` of 100. `--scale worker=3` exceeds it | OPEN |
| CI ruff scope excludes `migrations/` | Exactly why the `env.py` lint and format problems went unnoticed. One word in `ci.yml` prevents the regression | OPEN |
| No thumbnails | The library grid loads full-size illustrations as covers | OPEN |
| Media has cache headers but no CDN | Immutable caching shipped, so re-reads are free for the browser. Origin bandwidth still scales with cold reads | PARTIAL |
| Polling instead of push | Every client polls every 1.2s during generation and every 5s in the library | ACCEPTED for now. Revisit with SSE at scale |

## Low

| Item | Impact | Status |
|---|---|---|
| `/api/health` is unauthenticated and verbose | Exposes environment, provider, and job backend to anyone who asks | OPEN |
| Google Fonts loaded from Google | A third-party request per visitor on a children's site, which is a privacy question in the EU regardless of CSP | OPEN |
| No worker healthcheck in compose | A wedged ARQ worker is neither detected nor restarted, unlike `api` | OPEN |
| `restart: unless-stopped` on every compose service | The whole stack returns on every boot whether or not anyone is working on it, and no service declares a memory or CPU limit | OPEN |

## Watch list

- **Unexplained 58 GB pytest run (2026-08-02).** A bare `pytest` reached 58 GB of private commit against
  a 60.7 GB limit and took the development machine down. Three genuine test bugs were found and fixed,
  but none of them plausibly allocates at that scale, and a clean run now peaks at 0.10 GB. Not
  reproduced, not explained. Runs go through the memory-capped watchdog; if it returns, the watchdog
  output plus the in-flight test will identify it. See LESSONS.md.
- **Local Python is 3.13, CI and the image are 3.12.** Asyncio internals differ between them, so a
  local-only failure that CI never sees is plausible. Worth aligning.

## Deferred by targeting (not gaps, given ADR-013)

- **eSewa/Khalti.** Previously ranked High on the assumption that Kathmandu was the market. For
  NRN, Stripe is the correct and sufficient rail — cards across the US, UK, EU, Australia and the
  Gulf, plus Apple Pay and Google Pay free through hosted checkout. `BillingProvider` already
  abstracts payments, so adding a Nepal rail later is an implementation, not a redesign. Revisit
  only when the product deliberately expands into Nepal itself.

## Accepted risks (deliberate, revisit later)

- **Vanilla JS with no build step**: chosen for speed and zero tooling. Revisit past roughly a dozen views.
- **Redis burst limiter fails open**: the durable daily quota is the real limit; availability beats
  strictness for a non-billing guard.
- **Single Postgres instance, no replica**: correct at zero users. Managed Postgres with backups before launch.
