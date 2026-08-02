"""Offline billing provider: the whole paid flow, no keys and no network.

Signature verification here is NOT a stub. It implements the same
timestamp-and-HMAC scheme Stripe uses, so the tests exercise our real rejection
behaviour -- bad signature, stale timestamp, replayed event. A provider that
returned "valid" unconditionally would make every one of those tests vacuous,
and the bypass would be one careless edit away from reaching production.
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

from .base import (
    BillingEvent,
    BillingProvider,
    CheckoutResult,
    CheckoutSession,
    SubscriptionState,
    WebhookVerificationError,
)

# Reject signatures older than this, so a captured payload cannot be replayed
# indefinitely. Stripe's own default tolerance is the same five minutes.
SIGNATURE_TOLERANCE_SECONDS = 300


def sign_payload(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """Build a Stripe-format signature header. Used by the mock and by tests."""
    ts = int(time.time()) if timestamp is None else timestamp
    signed = f"{ts}.".encode() + payload
    mac = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _parse_header(signature: str) -> tuple[int, list[str]]:
    ts, macs = 0, []
    for part in signature.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                ts = int(value)
            except ValueError as e:
                raise WebhookVerificationError("Malformed timestamp in signature header") from e
        elif key == "v1":
            macs.append(value)
    if not ts or not macs:
        raise WebhookVerificationError("Signature header missing timestamp or signature")
    return ts, macs


def verify_signature(payload: bytes, signature: str, secret: str) -> None:
    """Raise WebhookVerificationError unless the payload is authentic and fresh."""
    if not secret:
        # Never treat "no secret configured" as "everything is valid".
        raise WebhookVerificationError("No webhook secret configured")
    ts, macs = _parse_header(signature)
    if abs(int(time.time()) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        raise WebhookVerificationError("Signature timestamp outside tolerance")
    expected = hmac.new(secret.encode("utf-8"), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    # compare_digest, not ==, so a timing side channel cannot leak the secret.
    if not any(hmac.compare_digest(expected, m) for m in macs):
        raise WebhookVerificationError("Signature does not match payload")


def _subscription_from(obj: dict) -> SubscriptionState | None:
    if not obj.get("subscription_id"):
        return None
    end = obj.get("current_period_end")
    return SubscriptionState(
        customer_id=obj.get("customer_id", ""),
        subscription_id=obj["subscription_id"],
        status=obj.get("status", "active"),
        price_id=obj.get("price_id", ""),
        current_period_end=datetime.fromtimestamp(end, UTC) if end else None,
        user_id=(obj.get("metadata") or {}).get("user_id", ""),
    )


class MockBillingProvider(BillingProvider):
    name = "mock"

    def __init__(self, webhook_secret: str):
        # Required, with no default: a default would be a shared constant that
        # every deployment using this provider would silently agree on.
        self.webhook_secret = webhook_secret
        self._sessions: dict[str, CheckoutResult] = {}

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
        session_id = f"cs_test_{uuid.uuid4().hex[:24]}"
        # Unpaid until the test explicitly completes it, mirroring the real
        # world where the redirect happens long before any money moves.
        self._sessions[session_id] = CheckoutResult(
            session_id=session_id, client_reference_id=user_id, paid=False
        )
        return CheckoutSession(session_id=session_id, url=f"{success_url}&mock_session={session_id}")

    async def create_portal(self, *, customer_id: str, return_url: str) -> str:
        return f"{return_url}?mock_portal={customer_id}"

    async def fetch_checkout(self, session_id: str) -> CheckoutResult | None:
        return self._sessions.get(session_id)

    async def fetch_subscription(self, subscription_id: str) -> SubscriptionState | None:
        for result in self._sessions.values():
            if result.subscription and result.subscription.subscription_id == subscription_id:
                return result.subscription
        return None

    def verify_webhook(self, *, payload: bytes, signature: str) -> BillingEvent:
        verify_signature(payload, signature, self.webhook_secret)
        try:
            body = json.loads(payload)
        except json.JSONDecodeError as e:
            raise WebhookVerificationError("Payload is not valid JSON") from e
        obj = body.get("data", {}).get("object", {})
        return BillingEvent(
            event_id=body.get("id", ""),
            type=body.get("type", ""),
            created=int(body.get("created", 0)),
            subscription=_subscription_from(obj),
            client_reference_id=obj.get("client_reference_id", ""),
        )

    # --- test helpers -------------------------------------------------------

    def complete_session(
        self, session_id: str, *, customer_id: str, subscription_id: str, price_id: str
    ) -> None:
        """Mark a session paid, as Stripe would once the card clears."""
        existing = self._sessions[session_id]
        self._sessions[session_id] = CheckoutResult(
            session_id=session_id,
            client_reference_id=existing.client_reference_id,
            paid=True,
            subscription=SubscriptionState(
                customer_id=customer_id,
                subscription_id=subscription_id,
                status="active",
                price_id=price_id,
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            ),
        )
