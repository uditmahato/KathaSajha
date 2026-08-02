"""Startup validation for billing configuration.

Half-configured billing fails in a money-shaped way that is invisible from the
server: with a secret key but no webhook secret, Stripe takes the parent's card,
the webhook arrives unverifiable and is rejected, and the account is never
upgraded. Stripe's dashboard shows a clean payment, our log shows a rejected
request, and nobody connects the two until a customer complains.

So this refuses to boot instead. A failed deploy is a sixty-second fix; a
charged customer with no stories is not.

A pure function on purpose: httpx's ASGITransport (what the tests use) does not
run lifespan events, so no test would ever execute this if it only lived inside
lifespan. It is unit-tested directly against constructed Settings.
"""

from .config import Settings
from .plans import PLANS


class BillingConfigError(RuntimeError):
    """Billing is partially configured. Naming the gap is the whole point."""


def validate_billing_settings(settings: Settings) -> None:
    stripe_values = {
        "STRIPE_SECRET_KEY": settings.stripe_secret_key,
        "STRIPE_WEBHOOK_SECRET": settings.stripe_webhook_secret,
        "STRIPE_PRICE_IDS": settings.stripe_price_ids,
    }
    provided = {k for k, v in stripe_values.items() if v}
    if not provided:
        return  # fully dormant, which is a valid state

    missing = sorted(k for k, v in stripe_values.items() if not v)
    if missing:
        raise BillingConfigError(
            "Billing is half-configured: "
            + ", ".join(sorted(provided))
            + " set but "
            + ", ".join(missing)
            + " missing. Set all of them or none. A secret key without a webhook "
            "secret takes payments and never upgrades the customer."
        )

    # A price id that is really a product id is the single most common paste
    # error here, and it fails only at the moment a customer tries to pay.
    for code, price_id in settings.stripe_price_id_map.items():
        if code not in PLANS:
            raise BillingConfigError(
                f"STRIPE_PRICE_IDS names plan '{code}', which is not in the plan catalogue."
            )
        if not price_id.startswith("price_"):
            raise BillingConfigError(
                f"STRIPE_PRICE_IDS['{code}'] is '{price_id}', which is not a price id. "
                "Stripe price ids start with 'price_' ('prod_' is a product id)."
            )

    # Every paid plan must be purchasable, or the pricing page offers a tier
    # that cannot be bought.
    for code, plan in PLANS.items():
        if plan.monthly_price_usd > 0 and not settings.price_id_for(code):
            raise BillingConfigError(
                f"Plan '{code}' costs money but has no Stripe price id in STRIPE_PRICE_IDS."
            )

    # A live key outside production charges real cards from a laptop or CI.
    # That is unrecoverable in a way a refused boot is not.
    if settings.stripe_secret_key.startswith("sk_live_") and settings.environment != "production":
        raise BillingConfigError(
            f"A live Stripe key is configured but ENVIRONMENT is '{settings.environment}'. "
            "Use a test key outside production."
        )
