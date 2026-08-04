"""API request/response schemas."""

import re
import unicodedata
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# --- Auth ---
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=100)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, v: str) -> str:
        # bcrypt silently ignores bytes past 72; reject instead of truncating.
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long (maximum 72 bytes)")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    # Bounded even though login never hashes an over-long value: an unbounded
    # field invites a huge body purely to burn memory.
    password: str = Field(max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=256)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long (maximum 72 bytes)")
        return v


class MessageResponse(BaseModel):
    message: str


class DeleteAccountRequest(BaseModel):
    # Password re-entry, so a borrowed open session cannot erase a family's
    # library. Bounded like every other password field.
    password: str = Field(max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def new_password_fits_bcrypt(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long (maximum 72 bytes)")
        return v


class ChangePasswordResponse(BaseModel):
    """Carries a replacement token: changing the password retires every session,
    including the caller's own, so they are handed a fresh one immediately."""

    message: str
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    plan: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UsageOut(BaseModel):
    stories_today: int
    daily_limit: int
    remaining_today: int
    stories_this_month: int
    monthly_limit: int
    remaining_this_month: int
    plan: str = "free"


# --- Plans ---
class PlanOut(BaseModel):
    code: str
    name: str
    tagline: str
    daily_stories: int
    monthly_stories: int = 0
    monthly_price_usd: float
    monthly_price_npr: int
    features: list[str]
    purchasable: bool
    highlight: bool
    is_current: bool = False


class CheckoutRequest(BaseModel):
    plan_code: str = Field(pattern="^[a-z]{2,20}$")
    source: str = Field(default="", max_length=40)


class CheckoutSessionOut(BaseModel):
    checkout_url: str
    session_id: str


class PortalSessionOut(BaseModel):
    portal_url: str


class SubscriptionStateOut(BaseModel):
    plan: str
    plan_status: str = ""
    plan_renews_at: datetime | None = None
    # False means the state was already applied, e.g. the webhook won the race.
    changed: bool = False


class PlanInterestRequest(BaseModel):
    plan_code: str = Field(pattern="^[a-z]{2,20}$")
    source: str = Field(default="", max_length=40)


# --- Child profiles and companions ---
# Names now flow into the model instruction, onto a PDF cover, and into og:
# tags on a public share page. Sanitising at the schema boundary means every
# one of those consumers inherits the guarantee, rather than each re-deriving
# it: a name like "ignore previous instructions" never enters the system.
# Unicode CATEGORIES, not a character-class regex. A regex over \w rejected
# "सीता", because Devanagari vowel signs are combining marks and \w does not
# match them — which would have broken Nepali names in a Nepali-first product.
# Categories keep every script working: letters (L*), combining marks (M*, so
# Devanagari/Arabic/Thai compose correctly), and decimal digits (Nd).
_NAME_PUNCT = frozenset(" -'’.")
_NAME_TAIL_FORBIDDEN = frozenset(" -'’.")


def _clean_name(v: str) -> str:
    v = unicodedata.normalize("NFC", v).strip()
    if not v:
        raise ValueError("Name cannot be empty")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError("Name cannot contain line breaks or control characters")
    v = re.sub(r"\s+", " ", v)
    for ch in v:
        category = unicodedata.category(ch)
        if category[0] in ("L", "M") or category == "Nd" or ch in _NAME_PUNCT:
            continue
        raise ValueError("Name may only contain letters, spaces, hyphens and apostrophes")
    # A name may not begin or end with punctuation or a space, so it cannot be
    # padded into looking like a separate instruction line.
    if unicodedata.category(v[0])[0] != "L" or v[-1] in _NAME_TAIL_FORBIDDEN:
        raise ValueError("Name must start and end with a letter")
    # Re-check AFTER normalisation: Field(max_length) ran on the raw input, and
    # NFC can lengthen a string, which would overflow the VARCHAR(60) column.
    if len(v) > 60:
        raise ValueError("Name is too long (maximum 60 characters)")
    return v


def _sanitise_free_name(v: str) -> str:
    """Lenient cleaner for the typed hero field.

    That field shipped accepting anything, so REJECTING newly-invalid values
    would break personalisation for existing users mid-flow. Strip instead:
    the name now travels inside the delimited untrusted block, so removing
    control characters and capping the length is sufficient.
    """
    v = unicodedata.normalize("NFC", v)
    kept = []
    for ch in v:
        category = unicodedata.category(ch)
        if category[0] in ("L", "M") or category == "Nd" or ch in _NAME_PUNCT:
            kept.append(ch)
    v = re.sub(r"\s+", " ", "".join(kept)).strip(" -'’.")
    return v[:60]


class ChildProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    # "" = not given. Validated against the closed band vocabulary, so an
    # arbitrary string can never reach the story instruction.
    age_band: str = Field(default="", max_length=16)

    @field_validator("name")
    @classmethod
    def clean(cls, v: str) -> str:
        return _clean_name(v)

    @field_validator("age_band")
    @classmethod
    def known_band(cls, v: str) -> str:
        from .services.reading_level import is_valid_band

        if not is_valid_band(v):
            raise ValueError("Unknown age range")
        return v


class ChildProfileOut(BaseModel):
    id: str
    name: str
    age_band: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CompanionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: str = Field(default="animal", pattern="^(animal|bird|toy|other)$")
    description: str = Field(default="", max_length=120)

    @field_validator("name")
    @classmethod
    def clean(cls, v: str) -> str:
        return _clean_name(v)

    @field_validator("description")
    @classmethod
    def clean_description(cls, v: str) -> str:
        # Free text, so it is constrained to an allowlist and then travels
        # inside the delimited untrusted block. Previously it was interpolated
        # into the RULES section, where 120 characters of arbitrary text sat
        # exactly where an instruction goes.
        v = unicodedata.normalize("NFC", v).strip()
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise ValueError("Description cannot contain control characters")
        v = re.sub(r"\s+", " ", v)
        for ch in v:
            category = unicodedata.category(ch)
            if category[0] in ("L", "M") or category == "Nd" or ch in " -'’,":
                continue
            raise ValueError("Description may only contain letters, spaces, commas and hyphens")
        return v[:120]


class CompanionOut(BaseModel):
    id: str
    name: str
    kind: str
    description: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Stories ---
class CreateStoryRequest(BaseModel):
    # A static ceiling well above any sane MAX_PROMPT_CHARS. The router still
    # enforces the configured limit precisely; this only stops a caller pinning
    # memory with a multi-megabyte string that passes every other rule.
    prompt: str = Field(min_length=3, max_length=4000)
    language: str = Field(default="en", pattern="^(en|ne)$")
    hero_name: str = Field(default="", max_length=60)
    # Saved profiles to star in this story. Bounded here as well as in the
    # router so an oversized list is rejected before any lookup happens.
    child_ids: list[str] = Field(default_factory=list, max_length=3)
    companion_ids: list[str] = Field(default_factory=list, max_length=2)

    @field_validator("hero_name")
    @classmethod
    def clean_hero(cls, v: str) -> str:
        # Sanitised, not rejected: this field previously accepted anything, and
        # a parent mid-flow must not be blocked by a rule that did not exist
        # when they learned the form.
        return _sanitise_free_name(v)

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Prompt is too short")
        return v


class StoryPageOut(BaseModel):
    position: int
    text: str
    image_url: str
    image_error: str

    model_config = {"from_attributes": True}


class StorySummaryOut(BaseModel):
    id: str
    title: str
    prompt: str
    status: str
    language: str
    share_slug: str | None
    created_at: datetime
    cover_image_url: str = ""

    model_config = {"from_attributes": True}


class CastMemberOut(BaseModel):
    role: str
    name: str
    # Deliberately absent: age_band. It is never returned to any client and
    # never rendered — it exists only to steer generation.
    kind: str = ""


class StoryOut(BaseModel):
    id: str
    title: str
    prompt: str
    status: str
    error: str
    language: str
    share_slug: str | None
    provider: str
    created_at: datetime
    pages: list[StoryPageOut]
    cast: list[CastMemberOut] = []

    model_config = {"from_attributes": True}


class SharedPageOut(BaseModel):
    """Public page view: no internal error details."""

    position: int
    text: str
    image_url: str

    model_config = {"from_attributes": True}


class SharedStoryOut(BaseModel):
    """Public view of a shared story; no owner-identifying or internal data."""

    title: str
    language: str
    created_at: datetime
    pages: list[SharedPageOut]

    model_config = {"from_attributes": True}


class CreateStoryResponse(BaseModel):
    story_id: str
    job_id: str


class JobOut(BaseModel):
    id: str
    story_id: str
    status: str
    stage: str
    progress_current: int
    progress_total: int
    error: str

    model_config = {"from_attributes": True}


class ShareResponse(BaseModel):
    share_slug: str
    share_url: str
