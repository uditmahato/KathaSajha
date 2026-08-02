"""Database models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100), default="")
    plan: Mapped[str] = mapped_column(String(20), default="free")  # free | plus
    # Bumped on every password change or reset. Tokens carry the value they were
    # issued with, so bumping it invalidates every session at once. Without this
    # a stolen token outlives the reset that was meant to revoke it.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stories: Mapped[list["Story"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class PlanInterest(Base):
    """Demand signal for a tier that cannot be bought yet.

    Recorded at the exact moment a parent hits the daily wall, which is the
    highest-intent moment the product has. This is what tells us whether to
    build billing, and what to charge.
    """

    __tablename__ = "plan_interest"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_code: Mapped[str] = mapped_column(String(20))
    # Where the interest was expressed: "quota_wall" converts very differently
    # from "pricing_page" and the difference should drive the roadmap.
    source: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (UniqueConstraint("user_id", "plan_code", name="uq_plan_interest_user_plan"),)


class GenerationEvent(Base):
    """Append-only record of every generation the platform paid for.

    Quota is counted from this ledger, never from `stories`, because a story row
    is deletable: counting stories let a user loop create-then-delete for
    unlimited paid generations. The story reference is nullable and set to NULL
    on delete, so the charge survives the story it created.
    """

    __tablename__ = "generation_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[str | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Refunded when the failure was ours (safety block, provider outage, crash).
    # A refunded event does not count against the user's daily allowance.
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    refund_reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (
        # Serves both the per-user daily count and the global daily ceiling.
        Index("ix_generation_events_user_created", "user_id", "created_at"),
    )


class PasswordResetToken(Base):
    """Single-use, expiring reset tokens. Only the SHA-256 hash is stored, so a
    database leak does not hand out account access."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt: Mapped[str] = mapped_column(Text)
    hero_name: Mapped[str] = mapped_column(String(60), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    language: Mapped[str] = mapped_column(String(10), default="en")  # en | ne
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending -> generating -> complete | failed
    error: Mapped[str] = mapped_column(Text, default="")
    share_slug: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    owner: Mapped["User"] = relationship(back_populates="stories")
    pages: Mapped[list["StoryPage"]] = relationship(
        back_populates="story", cascade="all, delete-orphan", order_by="StoryPage.position"
    )
    job: Mapped["GenerationJob | None"] = relationship(
        back_populates="story", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_story_owner"),
        # The library listing filters by user then sorts by recency; separate
        # single-column indexes force a sort of everything the user owns.
        Index("ix_stories_user_created", "user_id", "created_at"),
    )


class StoryPage(Base):
    __tablename__ = "story_pages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(String(500), default="")  # empty = no image
    image_error: Mapped[str] = mapped_column(Text, default="")

    story: Mapped[Story] = relationship(back_populates="pages")

    __table_args__ = (UniqueConstraint("story_id", "position", name="uq_page_story_position"),)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    story_id: Mapped[str] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued -> running -> complete | failed
    stage: Mapped[str] = mapped_column(String(50), default="queued")
    # queued | writing_story | illustrating | finalizing | done | failed
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    story: Mapped[Story] = relationship(back_populates="job")
