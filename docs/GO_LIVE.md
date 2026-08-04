# Go-live checklist

Everything the codebase cannot contain. Each unchecked box blocks the step
next to it; the app itself enforces several of these by refusing to boot.

## Security (before anything else)

- [ ] **Rotate the `GOOGLE_API_KEY` leaked in commit `b3f882e`** in Google AI
      Studio. The repo is public; deleting the file does not help. Nothing
      else on this list matters while this key works.

## Accounts and credentials

- [ ] Google AI Studio: new API key -> `GOOGLE_API_KEY`
- [ ] Fill real prices from Google's pricing page: `PRICE_PER_1M_INPUT_TOKENS_USD`,
      `PRICE_PER_1M_OUTPUT_TOKENS_USD`, `PRICE_PER_IMAGE_USD` — until then every
      generation logs cost $0 and the free-tier economics are unverified
- [ ] SMTP provider (Postmark/SES/Resend): `EMAIL_BACKEND=smtp` + `SMTP_*`.
      Production **refuses to boot** on the console backend
- [ ] Sentry project -> `SENTRY_DSN`
- [ ] Domain + DNS -> `PUBLIC_BASE_URL=https://...`

## Legal (children's product — not optional)

- [ ] Have `/privacy` and `/terms` reviewed by a qualified lawyer
      (COPPA + GDPR-K exposure). They are honest drafts of what the code does,
      not legal advice
- [ ] Replace the `[contact email]` and `[governing law]` placeholders; remove
      the "draft pending review" notices
- [ ] Confirm the deletion flow satisfies your counsel (in-app: footer ->
      Delete my account)

## Billing (when ready to charge)

- [ ] Stripe: create the product and a monthly price -> `STRIPE_PRICE_IDS=plus=price_...`
- [ ] Register the webhook endpoint `https://<domain>/api/billing/webhook`
      **in the same mode (test/live) as the key** -> `STRIPE_WEBHOOK_SECRET`
- [ ] `STRIPE_SECRET_KEY` (test first). The app refuses half-configuration
- [ ] One test-mode purchase end to end
- [ ] One live-mode purchase, then refund it. This is the only check that
      catches a webhook registered in the wrong dashboard mode

## First real generation

- [ ] With the new key on staging: generate one English and one Nepali story,
      read both, export both PDFs
- [ ] Check the logged `estimated_cost_usd` against the Google console bill

## Deploy (see DEPLOYMENT.md)

- [ ] Managed Postgres or a disciplined backup habit; `POSTGRES_PASSWORD`
      must not be the compose default `katha`
- [ ] `ENVIRONMENT=production`, long random `SECRET_KEY`
- [ ] TLS terminator in front; set `TRUSTED_PROXY_IPS` to the proxy's address
      or the rate limiter throttles all users as one
- [ ] Verify `/api/health` from the public URL; watch Sentry for a day
