"""The Stripe SDK boundary, exercised against the real pinned SDK.

Everything else in the billing suite runs through MockBillingProvider, which
proves our logic but cannot catch the SDK changing under us. That gap was not
hypothetical: an earlier version of stripe_provider.py called
`stripe.util.convert_to_stripe_object` (removed in v8+) and `.get()` on a
StripeObject (no longer a dict), so every webhook delivery would have raised
AttributeError, returned 500, and had the endpoint disabled by Stripe after
three days of retries. Nothing in the mock-driven suite could see it.

These tests use no credentials and make no network calls: payloads are signed
locally and fed through the real `stripe.Webhook.construct_event`.
"""

import json
import time

import pytest

from app.config import get_settings
from app.services.billing.base import WebhookVerificationError
from app.services.billing.mock import sign_payload

SECRET = "whsec_sdk_boundary_test"
PRICE = "price_plus"


@pytest.fixture
def provider(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_not_a_real_key", raising=False)
    monkeypatch.setattr(s, "stripe_webhook_secret", SECRET, raising=False)
    monkeypatch.setattr(s, "stripe_price_ids", f"plus={PRICE}", raising=False)

    from app.services.billing.stripe_provider import StripeBillingProvider

    return StripeBillingProvider()


def _subscription_event(*, legacy_period: bool = False, event_id: str = "evt_sub") -> dict:
    """A Stripe subscription event in the shape the API actually emits."""
    item = {
        "id": "si_1",
        "object": "subscription_item",
        "price": {"id": PRICE, "object": "price"},
        "current_period_start": 1699999999,
        "current_period_end": 1700086400,
    }
    obj = {
        "id": "sub_123",
        "object": "subscription",
        "customer": "cus_123",
        "status": "active",
        "metadata": {"user_id": "user-abc"},
        "items": {"object": "list", "data": [item]},
    }
    if legacy_period:
        # Pre-2025-03-31.basil: the period lived on the subscription itself.
        obj["current_period_end"] = 1700086400
        del item["current_period_end"]
    return {
        "id": event_id,
        "object": "event",
        "created": 1700000000,
        "type": "customer.subscription.updated",
        "data": {"object": obj},
    }


def _signed(payload: dict, secret: str = SECRET, *, timestamp: int | None = None):
    body = json.dumps(payload).encode()
    return body, sign_payload(body, secret, timestamp=timestamp)


# --- Parsing -----------------------------------------------------------------


def test_modern_subscription_payload_parses(provider):
    """API 2025-03-31.basil and later put the period end on the items, and the
    webhook payload shape follows the ENDPOINT's version, not our client pin."""
    body, sig = _signed(_subscription_event())
    event = provider.verify_webhook(payload=body, signature=sig)

    assert event.type == "customer.subscription.updated"
    assert event.event_id == "evt_sub"
    assert event.created == 1700000000
    s = event.subscription
    assert s.subscription_id == "sub_123"
    assert s.customer_id == "cus_123"
    assert s.status == "active"
    assert s.price_id == PRICE
    assert s.user_id == "user-abc", "metadata is the only account link on a subscription object"
    assert s.current_period_end is not None, "a None expiry silently revokes a paying customer"


def test_legacy_subscription_payload_still_parses(provider):
    """An older endpoint version must not produce a NULL expiry."""
    body, sig = _signed(_subscription_event(legacy_period=True))
    s = provider.verify_webhook(payload=body, signature=sig).subscription
    assert s.current_period_end is not None
    assert s.price_id == PRICE


def test_checkout_session_payload_yields_the_account_link(provider):
    """checkout.session.completed is the only event carrying
    client_reference_id, and its `subscription` is a bare id."""
    body, sig = _signed(
        {
            "id": "evt_cs",
            "object": "event",
            "created": 1700000001,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_1",
                    "object": "checkout.session",
                    "client_reference_id": "user-abc",
                    "subscription": "sub_123",
                    "customer": "cus_123",
                    "payment_status": "paid",
                }
            },
        }
    )
    event = provider.verify_webhook(payload=body, signature=sig)
    assert event.client_reference_id == "user-abc"
    assert event.subscription_id_hint == "sub_123"


def test_unhandled_event_type_does_not_raise(provider):
    """Anything we do not handle must parse quietly. Raising would make Stripe
    retry for three days and then disable the endpoint."""
    body, sig = _signed(
        {
            "id": "evt_inv",
            "object": "event",
            "created": 1700000002,
            "type": "invoice.payment_succeeded",
            "data": {"object": {"id": "in_1", "object": "invoice"}},
        }
    )
    event = provider.verify_webhook(payload=body, signature=sig)
    assert event.type == "invoice.payment_succeeded"
    assert event.subscription is None


# --- Signature verification, by the real SDK ---------------------------------


def test_real_sdk_accepts_our_signature_scheme(provider):
    """Proves MockBillingProvider's signer is faithful rather than convenient:
    the header it produces is accepted by Stripe's own verifier."""
    body, sig = _signed(_subscription_event())
    assert provider.verify_webhook(payload=body, signature=sig) is not None


def test_wrong_secret_rejected_by_real_sdk(provider):
    body, sig = _signed(_subscription_event(), secret="whsec_attacker")
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(payload=body, signature=sig)


def test_tampered_payload_rejected_by_real_sdk(provider):
    body, sig = _signed(_subscription_event())
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(payload=body + b" ", signature=sig)


def test_stale_timestamp_rejected_by_real_sdk(provider):
    """A captured payload must not be replayable indefinitely."""
    body, sig = _signed(_subscription_event(), timestamp=int(time.time()) - 4000)
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(payload=body, signature=sig)


def test_missing_signature_header_rejected(provider):
    body, _ = _signed(_subscription_event())
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(payload=body, signature="")


# --- Outbound call construction ----------------------------------------------


async def test_checkout_carries_account_id_and_reuses_the_customer(provider, monkeypatch):
    """The two fixes that stop a first-time buyer being unprovisionable and a
    repeat buyer being billed twice. Captured without any network call."""
    captured = {}

    class _Sessions:
        def create(self, params):
            captured.update(params)
            return type("S", (), {"id": "cs_test_x", "url": "https://checkout.stripe.com/x"})()

    monkeypatch.setattr(provider.client.checkout, "sessions", _Sessions(), raising=False)

    await provider.create_checkout(
        user_id="user-abc",
        email="parent@example.com",
        price_id=PRICE,
        success_url="https://app.example/return?status=success",
        cancel_url="https://app.example/return?status=cancelled",
        customer_id="cus_existing",
    )

    assert captured["client_reference_id"] == "user-abc", "ties the SESSION to the account"
    assert captured["subscription_data"]["metadata"]["user_id"] == "user-abc", (
        "ties the SUBSCRIPTION to the account; without it no subscription webhook "
        "can be matched to a first-time buyer"
    )
    assert captured["customer"] == "cus_existing", "reuse, or a repeat purchase bills twice"
    assert "customer_email" not in captured, "customer and customer_email are mutually exclusive"
    assert captured["mode"] == "subscription"


async def test_checkout_falls_back_to_email_for_a_new_customer(provider, monkeypatch):
    captured = {}

    class _Sessions:
        def create(self, params):
            captured.update(params)
            return type("S", (), {"id": "cs_test_y", "url": "https://checkout.stripe.com/y"})()

    monkeypatch.setattr(provider.client.checkout, "sessions", _Sessions(), raising=False)

    await provider.create_checkout(
        user_id="user-new",
        email="new@example.com",
        price_id=PRICE,
        success_url="https://app.example/ok",
        cancel_url="https://app.example/no",
    )
    assert captured["customer_email"] == "new@example.com"
    assert "customer" not in captured
