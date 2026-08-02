"""Billing: dormant by default, and correct when it is not.

Two things are being protected here. First, that shipping this changed nothing
while no credentials exist. Second, that once credentials exist there is no path
to a paid plan that does not involve paying -- and no path where a paying
customer silently loses access.
"""

import json
import time

import pytest
from sqlalchemy import select

from app.billing_guard import BillingConfigError, validate_billing_settings
from app.config import Settings, get_settings
from app.db import get_session_factory
from app.models import User
from app.plans import effective_plan_code, is_purchasable, plan_code_for_price_id
from app.services.billing.base import WebhookVerificationError
from app.services.billing.mock import sign_payload, verify_signature

PRICE = "price_test_plus"


# --- Dormancy ----------------------------------------------------------------


def test_no_credentials_means_billing_off():
    s = Settings(_env_file=None)
    assert s.resolved_billing_provider == "none"
    assert s.billing_enabled is False


def test_partial_credentials_never_enable_billing():
    """The dangerous direction. Any missing piece must resolve to off."""
    for kwargs in (
        {"stripe_secret_key": "sk_test_x"},
        {"stripe_secret_key": "sk_test_x", "stripe_webhook_secret": "whsec_x"},
        {"stripe_webhook_secret": "whsec_x", "stripe_price_ids": f"plus={PRICE}"},
    ):
        s = Settings(_env_file=None, **kwargs)
        assert s.resolved_billing_provider == "none", kwargs


def test_auto_never_resolves_to_mock():
    """A fake billing provider reached by misconfiguration would hand out paid
    plans for free. Only an explicit opt-in may select it."""
    s = Settings(_env_file=None, stripe_secret_key="sk_test_x")
    assert s.resolved_billing_provider != "mock"


async def test_billing_routes_do_not_exist_while_dormant(client, auth_headers):
    """Not 503 -- absent. A stub that answers without verifying a signature is
    how an unauthenticated free-upgrade endpoint gets shipped."""
    for method, path in (
        ("post", "/api/billing/checkout"),
        ("post", "/api/billing/portal"),
        ("post", "/api/billing/webhook"),
    ):
        r = await getattr(client, method)(path, json={}, headers=auth_headers)
        assert r.status_code == 404, f"{path} -> {r.status_code}"


async def test_plus_is_not_purchasable_while_dormant(client):
    plans = (await client.get("/api/plans")).json()
    by_code = {p["code"]: p for p in plans}
    assert by_code["free"]["purchasable"] is True
    assert by_code["plus"]["purchasable"] is False, "cannot sell what cannot be paid for"


async def test_billing_return_route_exists_even_while_dormant(client):
    """Stripe return URLs are configured once. A 404 on the way back from a real
    payment is the worst possible moment to find a missing route."""
    assert (await client.get("/billing/return?status=success")).status_code == 200


# --- Startup guard -----------------------------------------------------------


def test_guard_allows_fully_dormant():
    validate_billing_settings(Settings(_env_file=None))


def test_guard_rejects_half_configured():
    """Secret key without a webhook secret charges the card and never upgrades."""
    s = Settings(_env_file=None, stripe_secret_key="sk_test_x")
    with pytest.raises(BillingConfigError) as e:
        validate_billing_settings(s)
    assert "STRIPE_WEBHOOK_SECRET" in str(e.value)


def test_guard_rejects_product_id_pasted_as_price_id():
    s = Settings(
        _env_file=None,
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret="whsec_x",
        stripe_price_ids="plus=prod_abc123",
    )
    with pytest.raises(BillingConfigError) as e:
        validate_billing_settings(s)
    assert "price_" in str(e.value)


def test_guard_rejects_live_key_outside_production():
    s = Settings(
        _env_file=None,
        environment="development",
        stripe_secret_key="sk_live_x",
        stripe_webhook_secret="whsec_x",
        stripe_price_ids=f"plus={PRICE}",
    )
    with pytest.raises(BillingConfigError) as e:
        validate_billing_settings(s)
    assert "live" in str(e.value).lower()


def test_guard_accepts_a_complete_configuration():
    validate_billing_settings(
        Settings(
            _env_file=None,
            stripe_secret_key="sk_test_x",
            stripe_webhook_secret="whsec_x",
            stripe_price_ids=f"plus={PRICE}",
        )
    )


# --- Entitlement derivation --------------------------------------------------


def test_paid_plan_without_expiry_grants_nothing():
    """The backstop against a webhook that never arrives."""
    assert effective_plan_code("plus", None) == "free"


def test_expired_paid_plan_lapses_to_free():
    from datetime import UTC, datetime, timedelta

    past = datetime.now(UTC) - timedelta(seconds=1)
    assert effective_plan_code("plus", past) == "free"


def test_live_paid_plan_grants():
    from datetime import UTC, datetime, timedelta

    future = datetime.now(UTC) + timedelta(days=5)
    assert effective_plan_code("plus", future) == "plus"


def test_naive_expiry_from_sqlite_does_not_raise():
    """SQLite hands back naive datetimes; comparing one to an aware now raises
    TypeError on the story-creation hot path. This repo has hit that before."""
    from datetime import datetime, timedelta

    naive_future = (datetime.utcnow() + timedelta(days=5)).replace(tzinfo=None)
    assert effective_plan_code("plus", naive_future) == "plus"


def test_free_plan_ignores_expiry():
    assert effective_plan_code("free", None) == "free"


def test_unknown_price_id_grants_nothing():
    assert plan_code_for_price_id("price_never_configured") is None
    assert plan_code_for_price_id("") is None


async def test_expired_subscriber_is_enforced_as_free(client, auth_headers):
    """The end-to-end version: quota must follow the effective plan, not the
    stored column."""
    from datetime import UTC, datetime, timedelta

    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    async with get_session_factory()() as session:
        user = (await session.execute(select(User).where(User.id == me["id"]))).scalar_one()
        user.plan = "plus"
        user.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    usage = (await client.get("/api/auth/usage", headers=auth_headers)).json()
    assert usage["plan"] == "free", "a lapsed subscriber must not still read as Plus"
    assert usage["daily_limit"] == get_settings().free_daily_stories


async def test_live_subscriber_gets_the_paid_allowance(client, auth_headers):
    from datetime import UTC, datetime, timedelta

    from app.plans import monthly_stories_for

    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    async with get_session_factory()() as session:
        user = (await session.execute(select(User).where(User.id == me["id"]))).scalar_one()
        user.plan = "plus"
        user.plan_expires_at = datetime.now(UTC) + timedelta(days=20)
        await session.commit()

    usage = (await client.get("/api/auth/usage", headers=auth_headers)).json()
    assert usage["plan"] == "plus"
    assert usage["monthly_limit"] == monthly_stories_for("plus")


# --- Webhook signature -------------------------------------------------------


def _payload() -> bytes:
    return json.dumps({"id": "evt_1", "type": "customer.subscription.updated"}).encode()


def test_valid_signature_passes():
    body = _payload()
    verify_signature(body, sign_payload(body, "whsec_test"), "whsec_test")


def test_wrong_secret_is_rejected():
    body = _payload()
    with pytest.raises(WebhookVerificationError):
        verify_signature(body, sign_payload(body, "whsec_attacker"), "whsec_test")


def test_tampered_payload_is_rejected():
    body = _payload()
    header = sign_payload(body, "whsec_test")
    with pytest.raises(WebhookVerificationError):
        verify_signature(body + b" ", header, "whsec_test")


def test_replayed_old_signature_is_rejected():
    body = _payload()
    stale = sign_payload(body, "whsec_test", timestamp=int(time.time()) - 4000)
    with pytest.raises(WebhookVerificationError):
        verify_signature(body, stale, "whsec_test")


def test_missing_secret_never_means_valid():
    """The bypass an implementer reaches for to test locally. With a mistyped
    secret in production it turns the endpoint into a free-upgrade API."""
    body = _payload()
    with pytest.raises(WebhookVerificationError):
        verify_signature(body, sign_payload(body, "whsec_test"), "")


def test_unsigned_request_is_rejected():
    with pytest.raises(WebhookVerificationError):
        verify_signature(_payload(), "", "whsec_test")


# --- Purchasability ----------------------------------------------------------


def test_free_is_always_purchasable():
    assert is_purchasable("free") is True


def test_paid_plan_needs_billing_and_a_price_id(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "billing_provider", "stripe", raising=False)
    monkeypatch.setattr(s, "stripe_price_ids", "", raising=False)
    assert is_purchasable("plus") is False, "no price id means no buy button"

    monkeypatch.setattr(s, "stripe_price_ids", f"plus={PRICE}", raising=False)
    assert is_purchasable("plus") is True


# --- Regressions found by adversarial review ---------------------------------


class _NoopDb:
    async def commit(self):
        return None


def _user(**over):
    from types import SimpleNamespace

    base = {
        "id": "u1",
        "plan": "plus",
        "plan_expires_at": None,
        "stripe_subscription_status": "active",
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "last_billing_event_at": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_unknown_price_on_an_active_subscription_does_not_revoke():
    """Rotating a Stripe price would otherwise mass-downgrade every legacy
    subscriber on their next renewal event."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.routers.billing import apply_subscription_state
    from app.services.billing.base import SubscriptionState

    expiry = datetime.now(UTC) + timedelta(days=20)
    user = _user(plan_expires_at=expiry)
    asyncio.run(
        apply_subscription_state(
            _NoopDb(),
            user,
            SubscriptionState("cus_1", "sub_1", "active", "price_rotated_away", expiry),
        )
    )
    assert user.plan == "plus", "unrecognised price means stale config, not a lapsed customer"
    assert user.plan_expires_at == expiry


def test_granting_subscription_without_period_end_keeps_existing_expiry():
    """A None period end is a parsing bug; writing it silently revokes."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.routers.billing import apply_subscription_state
    from app.services.billing.base import SubscriptionState

    expiry = datetime.now(UTC) + timedelta(days=20)
    user = _user(plan_expires_at=expiry)
    asyncio.run(
        apply_subscription_state(_NoopDb(), user, SubscriptionState("cus_1", "sub_1", "active", PRICE, None))
    )
    assert user.plan_expires_at == expiry, "must not null a paying customer's expiry"


def test_stale_event_cannot_roll_entitlement_backwards():
    """The watermark lives in the single writer now, so it protects whichever
    path provisioned first -- including confirm."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.routers.billing import apply_subscription_state
    from app.services.billing.base import SubscriptionState

    expiry = datetime.now(UTC) + timedelta(days=20)
    user = _user(plan_expires_at=expiry, last_billing_event_at=2000)
    applied = asyncio.run(
        apply_subscription_state(
            _NoopDb(),
            user,
            SubscriptionState("cus_1", "sub_1", "incomplete", PRICE, None),
            event_at=1000,
        )
    )
    assert applied is False
    assert user.plan == "plus", "an older snapshot must not undo a newer upgrade"


def test_cancellation_still_revokes():
    """The guards above must not have made revocation impossible."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.routers.billing import apply_subscription_state
    from app.services.billing.base import SubscriptionState

    user = _user(plan_expires_at=datetime.now(UTC) + timedelta(days=20))
    asyncio.run(
        apply_subscription_state(
            _NoopDb(), user, SubscriptionState("cus_1", "sub_1", "canceled", PRICE, None)
        )
    )
    assert user.plan == "free" and user.plan_expires_at is None


def test_checkout_session_event_carries_the_account_link():
    """checkout.session.completed is the only event with client_reference_id, so
    it must be able to establish entitlement for a first-time buyer."""
    from app.services.billing.mock import MockBillingProvider

    p = MockBillingProvider("whsec_t")
    body = json.dumps(
        {
            "id": "evt_cs_1",
            "type": "checkout.session.completed",
            "created": 1700000000,
            "data": {"object": {"client_reference_id": "user-123", "subscription_id": "sub_9"}},
        }
    ).encode()
    event = p.verify_webhook(payload=body, signature=sign_payload(body, "whsec_t"))
    assert event.client_reference_id == "user-123"


def test_subscription_metadata_carries_the_account_id():
    """Subscription objects have no client_reference_id; without metadata a
    first-time buyer's subscription events match no user at all."""
    from app.services.billing.mock import MockBillingProvider

    p = MockBillingProvider("whsec_t")
    body = json.dumps(
        {
            "id": "evt_sub_1",
            "type": "customer.subscription.updated",
            "created": 1700000000,
            "data": {"object": {"subscription_id": "sub_9", "metadata": {"user_id": "user-123"}}},
        }
    ).encode()
    event = p.verify_webhook(payload=body, signature=sign_payload(body, "whsec_t"))
    assert event.subscription.user_id == "user-123"


def test_compose_passes_billing_settings_to_containers():
    """Otherwise filling .env changes nothing inside the container, and the
    config-only go-live promise is false."""
    import pathlib

    compose = pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    for var in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_IDS", "PUBLIC_BASE_URL"):
        assert var in text, f"{var} is not passed through to the containers"
