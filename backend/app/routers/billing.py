"""Checkout, billing portal, return-confirmation, and the Stripe webhook.

This router is mounted only when billing is fully configured, so while dormant
these paths do not exist at all rather than returning a stub. A stub is how an
unauthenticated free-upgrade endpoint gets shipped by accident.

Entitlement is written in exactly one place -- apply_subscription_state -- which
both the webhook and the return-confirmation call. Whichever arrives first wins
and the second is a no-op, so a webhook that never lands still upgrades the
customer, and a duplicate delivery cannot double-apply.
"""

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..deps import CurrentUser, DbSession
from ..models import BillingEventRecord, User
from ..plans import get_plan, plan_code_for_price_id
from ..quota import effective_plan_for, enforce_auth_attempt_limit
from ..schemas import CheckoutRequest, CheckoutSessionOut, PortalSessionOut, SubscriptionStateOut
from ..services.billing import get_billing
from ..services.billing.base import BillingError, SubscriptionState, WebhookVerificationError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])

# A subscription in any of these is live enough that starting a second one
# would double-bill. `incomplete` counts: the customer is mid-payment.
ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing", "past_due", "incomplete", "unpaid"})

# Only these change entitlement. Everything else is acknowledged and ignored:
# returning an error for an unrecognised type makes Stripe retry for three days
# and then disable the endpoint.
HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)


def _base_url(request: Request) -> str:
    return (get_settings().public_base_url or str(request.base_url)).rstrip("/")


async def apply_subscription_state(
    db: AsyncSession, user: User, state: SubscriptionState, *, event_at: int = 0
) -> bool:
    """Reconcile a user against a subscription. The single entitlement writer.

    Sets rather than deltas, so applying the same state twice is harmless and
    order between the webhook and the return-confirmation does not matter.
    Owns the ordering watermark too: a guard maintained only by the webhook
    would be inert for every account provisioned by the confirm path.
    Returns whether anything changed.
    """
    if event_at and event_at < (user.last_billing_event_at or 0):
        # An older snapshot must not roll a newer subscription backwards.
        logger.info(
            "Ignoring stale subscription state",
            extra={"user_id": user.id, "event_at": event_at, "watermark": user.last_billing_event_at},
        )
        return False

    plan_code = plan_code_for_price_id(state.price_id)
    before = (user.plan, user.plan_expires_at, user.stripe_subscription_status)

    if state.grants_access and plan_code is None:
        # An unrecognised price means OUR config is stale, not that the customer
        # stopped paying. Revoking here would mass-downgrade every legacy
        # subscriber the moment a price is rotated. Leave entitlement alone and
        # make the misconfiguration loud instead.
        logger.error(
            "Active subscription references an unconfigured price; leaving entitlement unchanged",
            extra={"user_id": user.id, "price_id": state.price_id},
        )
    elif state.grants_access and state.current_period_end is None:
        # A granting subscription with no period end is a parsing bug, not an
        # entitlement. Writing None here would silently revoke a paying customer.
        logger.error(
            "Granting subscription has no period end; keeping existing expiry",
            extra={"user_id": user.id, "subscription_id": state.subscription_id},
        )
    elif state.grants_access:
        user.plan = plan_code
        user.plan_expires_at = state.current_period_end
    else:
        user.plan = "free"
        user.plan_expires_at = None

    user.stripe_subscription_status = state.status
    if event_at:
        user.last_billing_event_at = max(event_at, user.last_billing_event_at or 0)
    if state.customer_id:
        user.stripe_customer_id = state.customer_id
    if state.subscription_id:
        user.stripe_subscription_id = state.subscription_id
    await db.commit()

    changed = before != (user.plan, user.plan_expires_at, user.stripe_subscription_status)
    if changed:
        logger.info(
            "Subscription state applied",
            extra={
                "user_id": user.id,
                "plan": user.plan,
                "status": state.status,
                "expires_at": str(user.plan_expires_at),
            },
        )
    return changed


@router.post("/checkout", response_model=CheckoutSessionOut)
async def start_checkout(body: CheckoutRequest, user: CurrentUser, db: DbSession, request: Request):
    settings = get_settings()
    plan = get_plan(body.plan_code)
    if plan.monthly_price_usd <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You are already on the free plan."
        )
    price_id = settings.price_id_for(plan.code)
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Paid plans are not open yet."
        )
    if effective_plan_for(user) == plan.code:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"You're already on {plan.name}.")
    # A live subscription that has not yet granted entitlement (webhook in
    # flight, or a confirm that never happened) would otherwise let the same
    # parent buy a second subscription and be billed twice.
    if user.stripe_subscription_id and user.stripe_subscription_status in ACTIVE_SUBSCRIPTION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a subscription. Manage it from your billing settings.",
        )
    # Checkout creation is cheap for us but not free for Stripe, and an
    # unbounded loop of sessions is a nuisance worth throttling.
    await enforce_auth_attempt_limit("billing-checkout", user.id)

    base = _base_url(request)
    try:
        session = await get_billing().create_checkout(
            user_id=user.id,
            email=user.email,
            price_id=price_id,
            success_url=f"{base}/billing/return?status=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}/billing/return?status=cancelled",
            customer_id=user.stripe_customer_id or "",
        )
    except BillingError as e:
        logger.error("Checkout failed for user %s: %s", user.id, e, exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=e.user_message) from e
    return CheckoutSessionOut(checkout_url=session.url, session_id=session.session_id)


@router.post("/portal", response_model=PortalSessionOut)
async def open_portal(user: CurrentUser, request: Request):
    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You don't have a paid subscription to manage.",
        )
    try:
        url = await get_billing().create_portal(
            customer_id=user.stripe_customer_id,
            return_url=f"{_base_url(request)}/billing/return?status=managed",
        )
    except BillingError as e:
        logger.error("Portal failed for user %s: %s", user.id, e, exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=e.user_message) from e
    return PortalSessionOut(portal_url=url)


@router.post("/checkout/{session_id}/confirm", response_model=SubscriptionStateOut)
async def confirm_checkout(
    user: CurrentUser,
    db: DbSession,
    session_id: str = Path(pattern=r"^cs_[A-Za-z0-9_]{6,120}$"),
):
    """Provision on return from checkout, without waiting for the webhook.

    Webhooks are asynchronous and can be misconfigured entirely (a live-mode
    endpoint that was only ever registered in test mode delivers nothing, with
    no error anywhere). This path means a paid customer is upgraded even then.
    """
    result = await get_billing().fetch_checkout(session_id)
    # The session id alone proves nothing -- it is shareable and replayable, so
    # the account that opened it must match the caller. A mismatch and an
    # unknown session return the same 404 so session existence does not leak.
    if result is None or result.client_reference_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checkout session not found")
    if not result.paid or result.subscription is None:
        return SubscriptionStateOut(
            plan=effective_plan_for(user), plan_status="pending", plan_renews_at=None, changed=False
        )
    # Stamp the watermark from this path too. Maintained only by the webhook it
    # would be inert for every account the confirm path provisioned, and a
    # delayed "incomplete" event from a 3DS flow could then undo a paid upgrade.
    changed = await apply_subscription_state(db, user, result.subscription, event_at=int(time.time()))
    return SubscriptionStateOut(
        plan=effective_plan_for(user),
        plan_status=user.stripe_subscription_status,
        plan_renews_at=user.plan_expires_at,
        changed=changed,
    )


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: DbSession):
    # The RAW body: signature is over exactly these bytes, so re-serialising
    # parsed JSON would never verify.
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = get_billing().verify_webhook(payload=payload, signature=signature)
    except WebhookVerificationError as e:
        logger.warning("Rejected webhook with bad signature: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature") from e

    # Idempotency: the provider's own event id is the key. A redelivery finds
    # the row and stops before touching entitlement.
    if event.event_id:
        existing = await db.get(BillingEventRecord, event.event_id)
        if existing is not None:
            logger.info("Ignoring duplicate webhook %s", event.event_id)
            return {"received": True, "duplicate": True}

    record = BillingEventRecord(
        # A verified event always has an id. The uuid fallback keeps an
        # id-less payload from colliding with anything, rather than reusing a
        # memory address that CPython recycles.
        id=event.event_id or f"unsigned-{uuid.uuid4().hex}",
        type=event.type,
        provider_created=event.created,
        status="ignored",
    )

    if event.type in HANDLED_EVENTS:
        state = event.subscription
        if state is None and event.subscription_id_hint:
            # checkout.session.completed names its subscription by id only.
            # Resolving it here is what lets the webhook provision a first-time
            # buyer whose browser never made it back to the return URL.
            state = await get_billing().fetch_subscription(event.subscription_id_hint)
        if state is None:
            record.status = "ignored"
        else:
            user = await _user_for(db, event, state)
            if user is None:
                record.status = "unmatched"
                logger.warning(
                    "Webhook %s matched no user",
                    event.type,
                    extra={"event_id": event.event_id, "subscription_id": state.subscription_id},
                )
            else:
                record.user_id = user.id
                applied = await apply_subscription_state(db, user, state, event_at=event.created)
                record.status = "applied" if applied else "ignored_stale"

    db.add(record)
    await db.commit()
    # 200 even for ignored types: anything else makes Stripe retry for days.
    return {"received": True}


async def _user_for(db: AsyncSession, event, sub: SubscriptionState) -> User | None:
    """Find the account an event belongs to, cheapest identifier first.

    Order matters for a FIRST purchase: the account's Stripe ids are still NULL,
    so the only links are the session's client_reference_id and the metadata we
    stamped onto the subscription at checkout time.
    """
    for candidate in (event.client_reference_id, sub.user_id):
        if candidate:
            user = await db.get(User, candidate)
            if user is not None:
                return user
    for column, value in (
        (User.stripe_subscription_id, sub.subscription_id),
        (User.stripe_customer_id, sub.customer_id),
    ):
        if value:
            found = (await db.execute(select(User).where(column == value))).scalar_one_or_none()
            if found is not None:
                return found
    return None
