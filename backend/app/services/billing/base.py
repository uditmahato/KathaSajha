"""Billing provider contract.

Mirrors the generation provider pattern (services/base.py): an ABC plus a mock
that runs the whole flow offline, so the paid path is testable with no keys and
no network. Everything crossing this boundary is provider-neutral -- no Stripe
objects leak into routers or models.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class BillingError(Exception):
    """Billing failed in a way the caller should surface as a 502.

    user_message is safe to return; the original detail belongs in the log,
    because Stripe errors routinely name internal ids and account state.
    """

    def __init__(self, message: str, *, user_message: str = ""):
        super().__init__(message)
        self.user_message = user_message or "Checkout is temporarily unavailable. Please try again."


class WebhookVerificationError(Exception):
    """The payload did not carry a valid signature for our secret."""


# Stripe statuses that grant access. `past_due` is deliberately included: the
# subscription is still live and Stripe is retrying the card, so taking the
# stories away mid-retry punishes a parent for a bank's timing.
GRANTING_STATUSES = frozenset({"active", "trialing", "past_due"})


@dataclass
class SubscriptionState:
    """A subscription reduced to what entitlement actually needs."""

    customer_id: str
    subscription_id: str
    status: str
    price_id: str
    current_period_end: datetime | None = None
    # Our account id, carried in subscription metadata at checkout time. A
    # Stripe Subscription has no client_reference_id, so without this a
    # first-time buyer's subscription events match no user at all.
    user_id: str = ""

    @property
    def grants_access(self) -> bool:
        return self.status in GRANTING_STATUSES


@dataclass
class CheckoutSession:
    session_id: str
    url: str


@dataclass
class CheckoutResult:
    """The outcome of a Checkout Session, read back from the provider.

    `client_reference_id` is the user id we set at creation. It is the only
    thing that ties a session to an account, and it must be checked before any
    entitlement is granted -- a session id alone is shareable and replayable.
    """

    session_id: str
    client_reference_id: str
    paid: bool
    subscription: SubscriptionState | None = None


@dataclass
class BillingEvent:
    """A verified webhook event."""

    event_id: str
    type: str
    created: int
    subscription: SubscriptionState | None = None
    client_reference_id: str = ""
    # Set when the event names a subscription we have not resolved yet, e.g.
    # checkout.session.completed, whose `subscription` field is a bare id.
    subscription_id_hint: str = ""


class BillingProvider(ABC):
    name: str = "base"

    @abstractmethod
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
        """Start a hosted checkout. Raises BillingError on provider failure."""

    @abstractmethod
    async def create_portal(self, *, customer_id: str, return_url: str) -> str:
        """Return a hosted billing-portal URL for managing or cancelling."""

    @abstractmethod
    async def fetch_checkout(self, session_id: str) -> CheckoutResult | None:
        """Read a checkout session back. None when the provider does not know it."""

    @abstractmethod
    async def fetch_subscription(self, subscription_id: str) -> SubscriptionState | None:
        """Read a subscription back, for events that only carry its id."""

    @abstractmethod
    async def cancel_subscription(self, subscription_id: str) -> bool:
        """Cancel immediately. Returns success; must not raise — account
        deletion cannot be blocked by a provider outage."""

    @abstractmethod
    def verify_webhook(self, *, payload: bytes, signature: str) -> BillingEvent:
        """Verify a signature over the RAW body and parse it.

        Raises WebhookVerificationError. Never accepts an unsigned payload: a
        bypass here turns the endpoint into a free-upgrade API for anyone.
        """
