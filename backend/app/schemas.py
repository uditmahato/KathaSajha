"""API request/response schemas."""

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
    monthly_price_usd: float
    monthly_price_npr: int
    features: list[str]
    purchasable: bool
    highlight: bool
    is_current: bool = False


class PlanInterestRequest(BaseModel):
    plan_code: str = Field(pattern="^[a-z]{2,20}$")
    source: str = Field(default="", max_length=40)


# --- Stories ---
class CreateStoryRequest(BaseModel):
    # A static ceiling well above any sane MAX_PROMPT_CHARS. The router still
    # enforces the configured limit precisely; this only stops a caller pinning
    # memory with a multi-megabyte string that passes every other rule.
    prompt: str = Field(min_length=3, max_length=4000)
    language: str = Field(default="en", pattern="^(en|ne)$")
    hero_name: str = Field(default="", max_length=60)

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
