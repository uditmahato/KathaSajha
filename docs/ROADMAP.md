# Roadmap

Tied to the business model: free tier to activate, subscription for recurring revenue, print-on-demand
for revenue per user that subscriptions alone cannot reach.

Status is written down so this file stays usable as an answer to "what is left".
DONE means shipped and tested; anything unmarked is still ahead.

## Horizon 0: launch blockers (must be true before a single real user)

- **DONE** Account recovery: password reset by emailed single-use token, which also
  retires every session issued before it.
- **DONE** Alembic migrations, baselined before real data exists, with a CI drift check.
- **DONE** Observability: structured logs with request ids. Sentry is wired but dormant
  until a `SENTRY_DSN` exists.
- **DONE** Legal surface: privacy policy, terms, and a full in-app deletion path. Both
  documents are honest drafts and still need a lawyer — see GO_LIVE.md.
- **DONE** Real secret management; production refuses to boot on a default or low-entropy
  `SECRET_KEY`.
- Remaining in this horizon is entirely owner action, not code: rotate the leaked API key,
  legal review, credentials, and a deploy. GO_LIVE.md is the list.

## Horizon 1: monetization foundations

- **DONE** Plans, pricing page, entitlements enforced by quota, and `plan_interest` demand capture.
- **DONE** Stripe for international cards: hosted checkout, billing portal, signature-verified
  webhooks with idempotency and an out-of-order watermark. Dormant until credentials exist.
- **eSewa/Khalti for Nepal.** The real remaining gap: Stripe cannot take most Nepali cards, so
  today's only rail serves the diaspora, not Kathmandu. `BillingProvider` already abstracts this.
- Dunning and downgrade flows. Upgrade, cancel, and webhook reconciliation are done.
- **DONE** Usage surfaces that create upgrade pressure honestly: the remaining-stories badge and
  the quota wall that answers with an offer rather than a red error.

## Speaking Nepali (positioning, not polish)

Stories have always been generated in Nepali. The interface around them was English,
which undercut the one thing this product is differentiated on.

- **DONE** Interface localisation with no build step: English stays literal in the markup,
  a single catalogue overrides it, and 16 CI gates fail the build when a new English string,
  a missing translation, or a dropped placeholder appears.
- **DONE** Server messages carry a stable code beside their English prose, including failures
  frozen into database rows by the worker — so a Nepali parent gets Nepali at the moment a
  story fails, hits the quota wall, or is asked for money.
- **Native-speaker review of `frontend/i18n/ne.js`.** The catalogue is AI-written and marked
  `pending_native_speaker_review`. The mechanism is gated; the words are the risk.
- Nepali legal pages, if counsel requires them. Today both are English-only and the app says
  so plainly in Nepali rather than machine-translating a privacy policy for a children's product.
- PDF colophon and `PlanOut.features` are the last English surfaces — see TECH_DEBT for why
  the PDF one is more expensive than it looks.

## Horizon 2: retention and delight

- **DONE** Child profiles: saved children with an optional age band that drives reading level,
  plus companion characters. Names are snapshotted into each story so deleting a profile never
  rewrites a book already on the shelf.
- Story series and recurring characters.
- Read-aloud audio (a large differentiator for bedtime and for early readers).
- Library organization: search, favorites, collections.
- Regenerate a single illustration without regenerating the story.

## Horizon 3: print-on-demand

- **DONE (screen)** Print-quality PDF generated server side: vector text, real cover, page
  numbers, Devanagari shaping. Still ~100 DPI illustrations, so it is a keepsake on screen and
  not yet a printable book — see TECH_DEBT.
- Higher-resolution illustrations (~2550px), which raises generation cost per story.
- Cover designer, page count padding, bleed and trim.
- Print partner integration and order lifecycle.
- Gifting flow, which is the seasonal revenue engine (Dashain, Tihar, birthdays).

## Horizon 4: distribution

- Public story gallery with opt-in sharing, which doubles as SEO surface.
- Schools and diaspora language programs (B2B, higher value, stickier).
- Referral mechanics built on the existing share link.

## Explicit non-goals for now

- Native mobile apps. The web app is responsive and installable later as a PWA.
- User-generated illustration uploads.
- Real-time collaboration.
