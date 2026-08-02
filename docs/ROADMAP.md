# Roadmap

Tied to the business model: free tier to activate, subscription for recurring revenue, print-on-demand
for revenue per user that subscriptions alone cannot reach.

## Horizon 0: launch blockers (must be true before a single real user)

- Account recovery: email verification and password reset. Today a forgotten password locks a user out
  permanently, which is unacceptable for a paid product.
- Alembic migrations, baselined before real data exists.
- Observability: structured logs with request ids, plus an error tracker.
- Legal surface for a children's product: privacy policy, terms, data deletion path.
- Real secret management and a production `SECRET_KEY` that cannot fall back to a shipped default.

## Horizon 1: monetization foundations

- Plans and pricing page; `plan` already exists on `users` but nothing sells or enforces tiers beyond quota.
- Stripe for international cards, eSewa/Khalti for Nepal.
- Subscription lifecycle: upgrade, downgrade, cancel, dunning, webhook reconciliation.
- Usage surfaces that create upgrade pressure honestly (remaining stories, what Plus unlocks).

## Horizon 2: retention and delight

- Child profiles: name, age, favorite themes, so personalization survives across stories.
- Story series and recurring characters.
- Read-aloud audio (a large differentiator for bedtime and for early readers).
- Library organization: search, favorites, collections.
- Regenerate a single illustration without regenerating the story.

## Horizon 3: print-on-demand

- Print-quality PDF generated server side (the current client-side PDF is fine for screen, not for print).
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
