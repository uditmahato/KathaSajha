"""Plan catalogue: the single source of truth for what each tier grants.

Entitlements live here rather than being scattered across checks, so adding a
tier or changing a limit is one edit and every surface (quota enforcement, the
pricing page, the upgrade prompt) stays consistent.

Prices are indicative until a payment provider is wired; `purchasable` is False
for anything that cannot actually be bought yet, and the UI must not pretend
otherwise.
"""

from dataclasses import dataclass, field

from .config import get_settings


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    tagline: str
    daily_stories: int | None  # None = use the configured free allowance
    monthly_price_usd: float
    monthly_price_npr: int
    # The daily figure exists to stop bursts; this is the real allowance and the
    # one that bounds cost. None = use the configured free monthly allowance.
    monthly_stories: int | None = None
    features: list[str] = field(default_factory=list)
    purchasable: bool = False
    highlight: bool = False


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
        purchasable=False,  # no payment provider connected yet
        highlight=True,
    ),
}


def get_plan(code: str) -> Plan:
    """Unknown or stale plan codes fall back to free: never grant more by accident."""
    return PLANS.get(code, PLANS["free"])


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
