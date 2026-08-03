# Technical debt, risks, and known gaps

Ranked by what would hurt most at launch. Updated every iteration. Items move to CHANGELOG when fixed.

## Critical (blocks launch)

| Item | Impact | Status |
|---|---|---|
| Leaked `GOOGLE_API_KEY` in git history (commit `b3f882e`) | Anyone who clones the public repo can bill the owner's account | OPEN. Owner must rotate the key in Google AI Studio. Deleting the file does not help; the object is still in history |
| No error tracker | Structured JSON logs with correlation ids now exist, but nothing aggregates or alerts. A crash at 2am is discovered by a user, not by us | PARTIAL. Logging shipped in iteration 2; Sentry or equivalent still missing |
| Legal surface for a children's product | Privacy policy, terms, and a data deletion path are still absent, and this is a product about children | OPEN |

## High

| Item | Impact | Status |
|---|---|---|
| No payment provider | A pricing page and demand capture exist, but nothing can actually be bought. Zero revenue path today | PARTIAL. Plans, entitlements, and `plan_interest` shipped in iteration 2; Stripe/eSewa/Khalti still missing |
| Frontend has no automated tests | 851 lines of `app.js` and every regression is caught only by manual browsing | OPEN |
| No type checking or dependency scanning in CI | Ruff lint and format run, plus a migration drift check. Nothing catches type errors or vulnerable dependencies | PARTIAL |
| Quota day boundary is UTC | A parent in Kathmandu (UTC+5:45) sees the daily limit reset mid-morning, which feels arbitrary | OPEN |
| Console email backend can run in production | `EMAIL_BACKEND` defaults to `console`. A deploy that forgets to set `smtp` writes reset links to the log and sends nothing, with no startup failure. The production `SECRET_KEY` guard should cover this too | OPEN |

## Medium

| Item | Impact | Status |
|---|---|---|
| Mock illustrations are ~100 DPI in the PDF | Text is now vector and crisp at any size, but a 768x512 image on a 6.3in page prints soft. True print-on-demand needs ~2550px images, which raises generation cost | OPEN. Blocks Horizon 3 print, not screen use |

| Item | Impact | Status |
|---|---|---|
| User enumeration on `/register` | Returns 409 for an existing address, while `/forgot-password` deliberately avoids exactly this leak. The customer list is enumerable | OPEN |
| Postgres connection ceiling | `pool_size` 10 + `max_overflow` 20 per process, times 2 uvicorn workers plus the worker, is up to 90 against a default `max_connections` of 100. `--scale worker=3` exceeds it | OPEN |
| CI ruff scope excludes `migrations/` | Exactly why the `env.py` lint and format problems went unnoticed. One word in `ci.yml` prevents the regression | OPEN |
| No HSTS header | The other security headers are set; `Strict-Transport-Security` is not | OPEN |
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

## Accepted risks (deliberate, revisit later)

- **Vanilla JS with no build step**: chosen for speed and zero tooling. Revisit past roughly a dozen views.
- **Redis burst limiter fails open**: the durable daily quota is the real limit; availability beats
  strictness for a non-billing guard.
- **Single Postgres instance, no replica**: correct at zero users. Managed Postgres with backups before launch.
