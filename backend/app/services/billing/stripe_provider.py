"""Stripe-backed billing. The SDK is imported lazily, exactly like the Gemini
provider, so the dependency is never touched while billing is dormant."""

import asyncio
import logging
from datetime import UTC, datetime

from ...config import get_settings
from .base import (
    BillingError,
    BillingEvent,
    BillingProvider,
    CheckoutResult,
    CheckoutSession,
    SubscriptionState,
    WebhookVerificationError,
)

logger = logging.getLogger(__name__)


class StripeBillingProvider(BillingProvider):
    name = "stripe"

    def __init__(self):
        import stripe  # lazy: only needed once billing is actually configured

        settings = get_settings()
        if not settings.stripe_secret_key:
            raise BillingError("Stripe secret key is not configured")
        self._stripe = stripe
        self.client = stripe.StripeClient(
            settings.stripe_secret_key, stripe_version=settings.stripe_api_version
        )
        self.webhook_secret = settings.stripe_webhook_secret

    @staticmethod
    def _items_of(sub) -> list:
        # Subscript, not getattr: `sub.items` is ambiguous with the mapping
        # method of the same name on dict-like SDK objects.
        try:
            return list(sub["items"]["data"] or [])
        except (KeyError, TypeError):
            return []

    @classmethod
    def _period_end(cls, sub) -> datetime | None:
        """Period end, from wherever this API version puts it.

        Stripe moved current_period_end off the Subscription and onto its items
        in API 2025-03-31.basil. Our client pins an outbound version, but a
        webhook payload is rendered with the version configured on the ENDPOINT,
        which we do not control. Reading only the old location returns None on a
        modern account, and a None expiry silently revokes a paying customer.
        """
        ts = sub.get("current_period_end") if hasattr(sub, "get") else None
        if ts is None:
            ts = getattr(sub, "current_period_end", None)
        if ts is None:
            ends = [
                i.get("current_period_end") if hasattr(i, "get") else getattr(i, "current_period_end", None)
                for i in cls._items_of(sub)
            ]
            ends = [e for e in ends if e]
            ts = max(ends) if ends else None
        return datetime.fromtimestamp(int(ts), UTC) if ts else None

    @classmethod
    def _state_from(cls, sub) -> SubscriptionState:
        items = cls._items_of(sub)
        price_id = ""
        if items:
            price = items[0]["price"] if "price" in items[0] else None
            price_id = (price["id"] if price else "") or ""
        customer = sub["customer"] if "customer" in sub else ""
        metadata = (sub["metadata"] if "metadata" in sub else None) or {}
        return SubscriptionState(
            customer_id=customer if isinstance(customer, str) else (customer["id"] if customer else ""),
            subscription_id=sub["id"] if "id" in sub else "",
            status=sub["status"] if "status" in sub else "",
            price_id=price_id,
            current_period_end=cls._period_end(sub),
            user_id=(metadata["user_id"] if "user_id" in metadata else "") or "",
        )

    async def create_checkout(
        self,
        *,
        user_id: str,
        email: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        customer_id: str = "",
    ) -> CheckoutSession:
        def _create():
            params = {
                "mode": "subscription",
                "line_items": [{"price": price_id, "quantity": 1}],
                "success_url": success_url,
                "cancel_url": cancel_url,
                # Ties the SESSION to an account. Checked before any entitlement.
                "client_reference_id": user_id,
                # Ties the SUBSCRIPTION to an account. Without this, no
                # customer.subscription.* event can be matched to a first-time
                # buyer, because those objects carry no client_reference_id and
                # the account's Stripe ids are still NULL.
                "subscription_data": {"metadata": {"user_id": user_id}},
                "allow_promotion_codes": True,
            }
            if customer_id:
                # Reuse the customer, or Stripe mints a new one per checkout and
                # a repeat purchase becomes two live subscriptions on two
                # customers, both billed.
                params["customer"] = customer_id
            elif email:
                params["customer_email"] = email
            return self.client.checkout.sessions.create(params=params)

        try:
            session = await asyncio.to_thread(_create)
        except Exception as e:
            logger.error("Stripe checkout creation failed: %s", e, exc_info=True)
            raise BillingError(f"Stripe checkout creation failed: {e}") from e
        return CheckoutSession(session_id=session.id, url=session.url)

    async def create_portal(self, *, customer_id: str, return_url: str) -> str:
        def _create():
            return self.client.billing_portal.sessions.create(
                params={"customer": customer_id, "return_url": return_url}
            )

        try:
            session = await asyncio.to_thread(_create)
        except Exception as e:
            logger.error("Stripe portal creation failed: %s", e, exc_info=True)
            raise BillingError(
                f"Stripe portal creation failed: {e}",
                user_message="Billing management is temporarily unavailable. Please try again.",
            ) from e
        return session.url

    async def fetch_checkout(self, session_id: str) -> CheckoutResult | None:
        def _fetch():
            return self.client.checkout.sessions.retrieve(session_id, params={"expand": ["subscription"]})

        try:
            session = await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.warning("Stripe session %s could not be read: %s", session_id, e)
            return None
        sub = getattr(session, "subscription", None)
        return CheckoutResult(
            session_id=getattr(session, "id", session_id),
            client_reference_id=getattr(session, "client_reference_id", "") or "",
            paid=getattr(session, "payment_status", "") == "paid",
            subscription=self._state_from(sub) if sub and not isinstance(sub, str) else None,
        )

    async def cancel_subscription(self, subscription_id: str) -> bool:
        def _cancel():
            return self.client.subscriptions.cancel(subscription_id)

        try:
            await asyncio.to_thread(_cancel)
            return True
        except Exception as e:
            # Deletion proceeds regardless; a live subscription on a deleted
            # account is an operator problem, and this log is its ticket.
            logger.error("Could not cancel subscription %s: %s", subscription_id, e, exc_info=True)
            return False

    def verify_webhook(self, *, payload: bytes, signature: str) -> BillingEvent:
        if not self.webhook_secret:
            raise WebhookVerificationError("No webhook secret configured")
        try:
            event = self._stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
        except Exception as e:
            raise WebhookVerificationError(str(e)) from e

        # construct_event already returns typed objects. There is no
        # stripe.util in v8+, and StripeObject stopped subclassing dict, so
        # .get() is not available either -- both would raise AttributeError
        # outside the router's WebhookVerificationError handler and turn every
        # delivery into a 500.
        event_type = event["type"]
        obj = event["data"]["object"]

        subscription = None
        subscription_id_hint = ""
        client_reference_id = ""
        if event_type.startswith("customer.subscription."):
            subscription = self._state_from(obj)
        elif event_type.startswith("checkout.session."):
            # A Checkout Session is the ONLY event carrying client_reference_id,
            # which for a first-time buyer is the only link to an account. Its
            # `subscription` field is a bare id, resolved by the caller.
            client_reference_id = (obj["client_reference_id"] if "client_reference_id" in obj else "") or ""
            raw_sub = obj["subscription"] if "subscription" in obj else None
            if isinstance(raw_sub, str):
                subscription_id_hint = raw_sub
            elif raw_sub is not None:
                subscription = self._state_from(raw_sub)

        return BillingEvent(
            event_id=event["id"],
            type=event_type,
            created=int(event["created"] or 0),
            subscription=subscription,
            client_reference_id=client_reference_id,
            subscription_id_hint=subscription_id_hint,
        )

    async def fetch_subscription(self, subscription_id: str) -> SubscriptionState | None:
        def _fetch():
            return self.client.subscriptions.retrieve(subscription_id)

        try:
            return self._state_from(await asyncio.to_thread(_fetch))
        except Exception as e:
            logger.warning("Stripe subscription %s could not be read: %s", subscription_id, e)
            return None
