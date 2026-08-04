"""Plan catalogue: the single source of truth for what each tier grants.

Entitlements live here rather than being scattered across checks, so adding a
tier or changing a limit is one edit and every surface (quota enforcement, the
pricing page, the upgrade prompt) stays consistent.

Prices are indicative until a payment provider is wired; `purchasable` is False
for anything that cannot actually be bought yet, and the UI must not pretend
otherwise.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .config import get_settings


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    tagline: str
    daily_stories: int | None  # None = use the configured free allowance
    # `purchasable` below is INTENT ("we mean to sell this"), not capability.
    # Ask is_purchasable(code) for whether it can actually be bought today.
    monthly_price_usd: float
    monthly_price_npr: int
    # The daily figure exists to stop bursts; this is the real allowance and the
    # one that bounds cost. None = use the configured free monthly allowance.
    monthly_stories: int | None = None
    features: list[str] = field(default_factory=list)
    purchasable: bool = False
    highlight: bool = False


# A subscription in any of these is live enough that a second one would
# double-bill, and live enough that deleting the account must cancel it.
# Lives here, not in the billing router, because that router is only mounted
# when billing is configured while deletion always exists.
ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing", "past_due", "incomplete", "unpaid"})

PLANS: dict[str, Plan] = {
    "free": Plan(
        code="free",
        name="Free",
        tagline="Enough for a story at bedtime",
        daily_stories=None,
        monthly_stories=None,
        monthly_price_usd=0.0,
        monthly_price_npr=0,
        features=[
            "10 illustrated stories a month",
            "English and Nepali",
            "Your child as the hero",
            "PDF download and family share links",
        ],
        purchasable=True,
    ),
    "plus": Plan(
        code="plus",
        name="Plus",
        tagline="For families who read together every night",
        daily_stories=30,
        monthly_stories=100,
        monthly_price_usd=6.0,
        monthly_price_npr=799,
        features=[
            "100 stories a month",
            "Longer stories with more illustrations",
            "Priority generation, even at busy times",
            "Print-ready PDF quality",
            "Early access to printed storybooks",
        ],
        # Intent to sell. Whether it is ACTUALLY buyable is derived by
        # is_purchasable() from billing configuration, so this stays False to
        # the API until Stripe credentials and a price id exist.
        purchasable=True,
        highlight=True,
    ),
}


def get_plan(code: str) -> Plan:
    """Unknown or stale plan codes fall back to free: never grant more by accident."""
    return PLANS.get(code, PLANS["free"])


def effective_plan_code(code: str, expires_at: datetime | None, *, now: datetime | None = None) -> str:
    """The plan a user is actually entitled to right now.

    A paid plan grants nothing unless it carries an expiry in the future. That
    is what makes a missed webhook safe: silence lets access lapse at the period
    boundary rather than granting it forever.
    """
    plan = get_plan(code)
    if plan.monthly_price_usd <= 0:
        return plan.code
    if expires_at is None:
        return "free"
    # SQLite hands back naive datetimes; comparing one to an aware `now` raises
    # TypeError on the story-creation path. The same normalisation already
    # exists in routers/auth.py for reset tokens.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return plan.code if expires_at > (now or datetime.now(UTC)) else "free"


def is_purchasable(code: str) -> bool:
    """Free is always joinable. A paid tier is only buyable when billing is
    actually wired up AND that specific plan has a price id, so a tier added
    without one can never render a buy button that leads nowhere."""
    plan = get_plan(code)
    if plan.monthly_price_usd <= 0:
        return plan.purchasable
    settings = get_settings()
    return bool(settings.billing_enabled and settings.price_id_for(plan.code))


def plan_code_for_price_id(price_id: str) -> str | None:
    """Reverse the configured price map. Fails closed: an unrecognised price
    returns None, and callers must treat that as "grant nothing"."""
    if not price_id:
        return None
    for code, configured in get_settings().stripe_price_id_map.items():
        if configured == price_id and code in PLANS:
            return code
    return None


def daily_stories_for(code: str) -> int:
    plan = get_plan(code)
    if plan.daily_stories is None:
        return get_settings().free_daily_stories
    return plan.daily_stories


def monthly_stories_for(code: str) -> int:
    plan = get_plan(code)
    if plan.monthly_stories is None:
        return get_settings().free_monthly_stories
    return plan.monthly_stories
