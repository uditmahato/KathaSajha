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
    # Which entitlement; plan_expires_at says whether it is still live. A paid
    # plan with no expiry grants nothing, so a webhook that never arrives
    # expires access instead of granting it forever.
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NULLable, not default="": NULLs are distinct under a unique index but
    # empty strings are not, so a default of "" would collide on the second
    # user who has no Stripe customer yet.
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    stripe_subscription_status: Mapped[str] = mapped_column(String(30), default="", server_default="")
    # Provider timestamp of the newest billing event applied to this account.
    # Webhooks can arrive out of order; an older snapshot must not roll a newer
    # subscription state backwards.
    last_billing_event_at: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Bumped on every password change or reset. Tokens carry the value they were
    # issued with, so bumping it invalidates every session at once. Without this
    # a stolen token outlives the reset that was meant to revoke it.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stories: Mapped[list["Story"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class ChildProfile(Base):
    """A child a parent saves so personalisation survives across stories.

    Deliberately minimal for a children's product: a first name and an OPTIONAL
    coarse age band. No birthday, no exact age, no free text, no interests. The
    band is the only age information the system ever consumes — storing an
    integer would collect more than is used, which is exactly what a
    data-minimisation review asks about.
    """

    __tablename__ = "child_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    # "" = the parent did not say. Never inferred, never auto-advanced.
    age_band: Mapped[str] = mapped_column(String(16), default="", server_default="")
    # Bumped only when the BAND changes, so the UI can ask "still preschool?"
    # after a long while instead of silently ageing a child on a timer.
    age_band_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionCharacter(Base):
    """A non-child character a family reuses: a pet, a bird, a favourite toy.

    Separate from ChildProfile rather than one table with a discriminator,
    because the columns genuinely differ and — more importantly — a companion
    must never be able to carry an age band.
    """

    __tablename__ = "companion_characters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20), default="animal")  # animal|bird|toy|other
    description: Mapped[str] = mapped_column(String(120), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    # SET NULL, not CASCADE: this ledger is the platform's financial record and
    # the global daily cost ceiling counts it. Cascading on account deletion
    # would let create-generate-delete loops drain the budget invisibly, and
    # GDPR deletion requires removing the PERSON, not the accounting.
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    story_id: Mapped[str | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Refunded when the failure was ours (safety block, provider outage, crash).
    # A refunded event does not count against the user's daily allowance.
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    refund_reason: Mapped[str] = mapped_column(String(200), default="")
    # What this generation consumed. Units rather than money, because prices
    # change and a stored dollar figure silently rots; cost is recomputed from
    # configured rates. Zero for the mock provider, which is genuinely free.
    provider: Mapped[str] = mapped_column(String(20), default="", server_default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    images: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (
        # Serves both the per-user daily count and the global daily ceiling.
        Index("ix_generation_events_user_created", "user_id", "created_at"),
    )


class BillingEventRecord(Base):
    """Every webhook we have already applied, keyed by the provider's own id.

    Stripe delivers at-least-once and can deliver out of order. The primary key
    is the provider's `evt_` id rather than our usual uuid, because that is
    exactly the value that makes a redelivery detectable. `provider_created` is
    the ordering watermark: an older event arriving after a newer one must not
    roll a subscription backwards.
    """

    __tablename__ = "billing_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # provider event id
    type: Mapped[str] = mapped_column(String(80))
    provider_created: Mapped[int] = mapped_column(Integer, default=0)
    # No foreign key: an event about a deleted account must still be recorded
    # rather than failing the webhook and triggering three days of retries.
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="")  # applied | ignored | unmatched
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


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
    # Frozen at create time: who starred, as JSON. A snapshot rather than a
    # foreign key, so renaming or deleting a profile never rewrites a book
    # already on the shelf. "" means a story made before profiles existed and
    # it renders through exactly the paths it always did.
    cast_json: Mapped[str] = mapped_column(Text, default="", server_default="")
    # The reading band this story was written for. Band code only — never an
    # age. "" reproduces pre-feature behaviour.
    reading_band: Mapped[str] = mapped_column(String(16), default="", server_default="")
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
