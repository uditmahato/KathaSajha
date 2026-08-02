"""Plan catalogue and demand capture.

There is no payment provider yet, so this deliberately does not pretend to sell
anything. It publishes what each tier grants and records interest at the moment
a parent hits the wall, which is the evidence needed to justify building billing.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from ..deps import CurrentUser, DbSession
from ..models import PlanInterest, User
from ..plans import PLANS, daily_stories_for, get_plan, is_purchasable, monthly_stories_for
from ..quota import effective_plan_for
from ..schemas import MessageResponse, PlanInterestRequest, PlanOut
from ..security import decode_access_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/plans", tags=["plans"])

_optional_bearer = HTTPBearer(auto_error=False)


async def _optional_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)],
) -> User | None:
    """Pricing is public, but a signed-in visitor should see their current plan."""
    if credentials is None:
        return None
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub") if payload else None
    if not user_id:
        return None
    user = await db.get(User, user_id)
    # Same retirement rule as the main auth dependency: a token retired by a
    # password change must not keep showing someone "Your plan".
    if user is None or payload.get("ver") != user.token_version:
        return None
    return user


@router.get("", response_model=list[PlanOut])
async def list_plans(user: Annotated[User | None, Depends(_optional_user)]):
    # Effective, not stored: a lapsed subscriber must not still see "Your plan".
    current = effective_plan_for(user) if user else None
    return [
        PlanOut(
            code=p.code,
            name=p.name,
            tagline=p.tagline,
            daily_stories=daily_stories_for(p.code),
            monthly_stories=monthly_stories_for(p.code),
            monthly_price_usd=p.monthly_price_usd,
            monthly_price_npr=p.monthly_price_npr,
            features=p.features,
            # Derived, never the stored flag: a paid tier is only buyable once
            # billing is configured AND that plan has a price id.
            purchasable=is_purchasable(p.code),
            highlight=p.highlight,
            is_current=(p.code == current),
        )
        for p in PLANS.values()
    ]


@router.post("/interest", response_model=MessageResponse)
async def register_interest(body: PlanInterestRequest, user: CurrentUser, db: DbSession):
    plan = get_plan(body.plan_code)
    if plan.code == "free":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already on the free plan.",
        )

    existing = (
        await db.execute(
            select(PlanInterest).where(PlanInterest.user_id == user.id, PlanInterest.plan_code == plan.code)
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(PlanInterest(user_id=user.id, plan_code=plan.code, source=body.source))
        await db.commit()
        logger.info(
            "Plan interest registered",
            extra={"user_id": user.id, "plan": plan.code, "source": body.source},
        )

    return MessageResponse(
        message=(
            f"Thank you. We'll email you the moment {plan.name} opens up, "
            "and you'll get early-bird pricing for being first."
        )
    )
