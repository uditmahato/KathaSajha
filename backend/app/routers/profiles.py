"""Saved children and companion characters.

The retention feature: once the app knows a family, every story starts from
something rather than from an empty box, and cancelling costs the parent
something. Deliberately small — a first name and an optional age range for a
child; a name, kind, and short description for a pet or toy.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import func, select

from ..deps import CurrentUser, DbSession
from ..errors import CodedHTTPException
from ..models import ChildProfile, CompanionCharacter
from ..schemas import ChildProfileOut, ChildProfileRequest, CompanionOut, CompanionRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/profiles", tags=["profiles"])

# Generous for any real family, and a bound on an authenticated write endpoint
# that would otherwise grow without limit. Flat across plans: charging a parent
# to record their fourth child would be a strange thing to sell.
MAX_CHILDREN = 8
MAX_COMPANIONS = 8


async def _count(db, model, user_id: str) -> int:
    return int((await db.execute(select(func.count(model.id)).where(model.user_id == user_id))).scalar_one())


@router.get("/children", response_model=list[ChildProfileOut])
async def list_children(user: CurrentUser, db: DbSession):
    rows = (
        (
            await db.execute(
                select(ChildProfile).where(ChildProfile.user_id == user.id).order_by(ChildProfile.created_at)
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/children", response_model=ChildProfileOut, status_code=status.HTTP_201_CREATED)
async def create_child(body: ChildProfileRequest, user: CurrentUser, db: DbSession):
    if await _count(db, ChildProfile, user.id) >= MAX_CHILDREN:
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            code="profile.child_limit",
            detail=f"You can save up to {MAX_CHILDREN} children.",
            params={"max": MAX_CHILDREN},
        )
    child = ChildProfile(
        user_id=user.id,
        name=body.name,
        age_band=body.age_band,
        age_band_set_at=datetime.now(UTC) if body.age_band else None,
    )
    db.add(child)
    await db.commit()
    return child


@router.put("/children/{child_id}", response_model=ChildProfileOut)
async def update_child(child_id: str, body: ChildProfileRequest, user: CurrentUser, db: DbSession):
    child = (
        await db.execute(
            select(ChildProfile).where(ChildProfile.id == child_id, ChildProfile.user_id == user.id)
        )
    ).scalar_one_or_none()
    if child is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND, code="profile.child_not_found", detail="Child not found"
        )
    child.name = body.name
    if body.age_band != child.age_band:
        # Only a band change restamps the date; renaming should not make the
        # app think the age was just re-confirmed.
        child.age_band = body.age_band
        child.age_band_set_at = datetime.now(UTC) if body.age_band else None
    await db.commit()
    return child


@router.delete("/children/{child_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_child(child_id: str, user: CurrentUser, db: DbSession):
    """Removes the profile only. Stories already written keep the name printed
    on their pages — they were snapshotted, not linked — which the privacy page
    states plainly."""
    child = (
        await db.execute(
            select(ChildProfile).where(ChildProfile.id == child_id, ChildProfile.user_id == user.id)
        )
    ).scalar_one_or_none()
    if child is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND, code="profile.child_not_found", detail="Child not found"
        )
    await db.delete(child)
    await db.commit()


@router.get("/companions", response_model=list[CompanionOut])
async def list_companions(user: CurrentUser, db: DbSession):
    rows = (
        (
            await db.execute(
                select(CompanionCharacter)
                .where(CompanionCharacter.user_id == user.id)
                .order_by(CompanionCharacter.created_at)
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/companions", response_model=CompanionOut, status_code=status.HTTP_201_CREATED)
async def create_companion(body: CompanionRequest, user: CurrentUser, db: DbSession):
    if await _count(db, CompanionCharacter, user.id) >= MAX_COMPANIONS:
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            code="profile.companion_limit",
            detail=f"You can save up to {MAX_COMPANIONS} characters.",
            params={"max": MAX_COMPANIONS},
        )
    companion = CompanionCharacter(
        user_id=user.id, name=body.name, kind=body.kind, description=body.description
    )
    db.add(companion)
    await db.commit()
    return companion


@router.delete("/companions/{companion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_companion(companion_id: str, user: CurrentUser, db: DbSession):
    companion = (
        await db.execute(
            select(CompanionCharacter).where(
                CompanionCharacter.id == companion_id, CompanionCharacter.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if companion is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            code="profile.companion_not_found",
            detail="Character not found",
        )
    await db.delete(companion)
    await db.commit()
