"""Registration, login, password recovery, current-user, usage."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, update

from ..config import get_settings
from ..deps import CurrentUser, DbSession
from ..models import PasswordResetToken, User
from ..quota import (
    daily_limit_for,
    effective_plan_for,
    enforce_auth_attempt_limit,
    monthly_limit_for,
    stories_created_this_month,
    stories_created_today,
)
from ..schemas import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UsageOut,
    UserOut,
)
from ..security import (
    create_access_token,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from ..services.email import get_email_sender

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """The client address used to key the brute-force limits.

    X-Forwarded-For is set by the caller, so it is only believed when the direct
    peer is a configured proxy. Trusting it unconditionally meant an attacker
    could rotate the header per request and bypass the IP-keyed limits entirely.
    """
    peer = request.client.host if request.client else ""
    if peer and peer in get_settings().trusted_proxy_list:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer or "unknown"


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: DbSession, request: Request):
    await enforce_auth_attempt_limit("register", _client_ip(request))
    email = body.email.lower().strip()
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists"
        )
    user = User(
        email=email, password_hash=hash_password(body.password), display_name=body.display_name.strip()
    )
    db.add(user)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user.id, user.token_version))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DbSession, request: Request):
    email = body.email.lower().strip()
    # Limit by IP (one attacker, many accounts) and by email (many IPs, one account).
    await enforce_auth_attempt_limit("login-ip", _client_ip(request))
    await enforce_auth_attempt_limit("login-email", email)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id, user.token_version))


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(body: ForgotPasswordRequest, db: DbSession, request: Request):
    """Always returns the same message: revealing whether an address is registered
    would leak the customer list."""
    settings = get_settings()
    await enforce_auth_attempt_limit("forgot", _client_ip(request))
    generic = MessageResponse(
        message="If an account exists for that email, we've sent a link to reset the password."
    )
    email = body.email.lower().strip()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        return generic

    # Invalidate any outstanding tokens so only the newest link works.
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=datetime.now(UTC))
    )
    raw, token_hash = generate_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.password_reset_ttl_minutes),
        )
    )
    await db.commit()

    base = (settings.public_base_url or str(request.base_url)).rstrip("/")
    link = f"{base}/reset-password?token={raw}"
    await get_email_sender().send(
        to=user.email,
        subject="Reset your KathaSajha password",
        text=(
            f"Hello{' ' + user.display_name if user.display_name else ''},\n\n"
            "We received a request to reset your KathaSajha password.\n\n"
            f"Open this link to choose a new one:\n{link}\n\n"
            f"The link works once and expires in {settings.password_reset_ttl_minutes} minutes.\n"
            "If you did not ask for this, you can ignore this email; nothing will change.\n\n"
            "Happy storytelling,\nKathaSajha"
        ),
    )
    return generic


@router.post("/reset-password", response_model=TokenResponse)
async def reset_password(body: ResetPasswordRequest, db: DbSession, request: Request):
    await enforce_auth_attempt_limit("reset", _client_ip(request))
    now = datetime.now(UTC)
    record = (
        await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_reset_token(body.token))
        )
    ).scalar_one_or_none()

    expires = record.expires_at if record else None
    if expires is not None and expires.tzinfo is None:  # SQLite returns naive datetimes
        expires = expires.replace(tzinfo=UTC)
    if record is None or record.used_at is not None or expires is None or expires < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Please request a new one.",
        )

    user = await db.get(User, record.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This reset link is no longer valid."
        )

    user.password_hash = hash_password(body.password)
    # Retire every session issued before this reset. The point of a reset is
    # that whoever held the old credentials loses access; leaving live tokens
    # alone would hand an attacker up to a full token lifetime of extra access.
    user.token_version += 1
    record.used_at = now
    await db.commit()
    logger.info("Password reset completed", extra={"user_id": user.id})
    # Log them straight in: a parent who just reset should not face another form.
    return TokenResponse(access_token=create_access_token(user.id, user.token_version))


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(body: ChangePasswordRequest, user: CurrentUser, db: DbSession):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    await db.commit()
    # Every session is now retired, including this caller's. Hand back a fresh
    # token so securing the account does not log them out of it.
    return ChangePasswordResponse(
        message="Your password has been updated.",
        access_token=create_access_token(user.id, user.token_version),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user


@router.get("/usage", response_model=UsageOut)
async def usage(user: CurrentUser, db: DbSession):
    used = await stories_created_today(db, user)
    limit = daily_limit_for(user)
    used_month = await stories_created_this_month(db, user)
    limit_month = monthly_limit_for(user)
    return UsageOut(
        stories_today=used,
        daily_limit=limit,
        remaining_today=max(0, limit - used),
        stories_this_month=used_month,
        monthly_limit=limit_month,
        remaining_this_month=max(0, limit_month - used_month),
        plan=effective_plan_for(user),
    )
