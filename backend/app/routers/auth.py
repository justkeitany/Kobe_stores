from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, field_validator
import os, re
from app.auth import (
    verify_password, hash_password, create_access_token, create_refresh_token,
    decode_token, get_current_admin, check_brute_force,
    record_failed_login, clear_login_attempts,
)
from app.config import settings
from app.redis_client import get_redis

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Default credentials — user MUST change username AND password on first login
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
FORCE_CHANGE_KEY = "credentials_changed"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangeCredentialsRequest(BaseModel):
    current_password: str
    new_username: str
    new_password: str

    @field_validator("new_username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if v == DEFAULT_USERNAME:
            raise ValueError("New username cannot be the default username")
        if not v.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError("Username can only contain letters, numbers, - _ .")
        return v

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if v == DEFAULT_PASSWORD:
            raise ValueError("New password cannot be the default password")
        return v


class ChangePasswordRequest(BaseModel):
    """Used from Settings page after first-login setup."""
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


async def _is_default_password_unchanged() -> bool:
    """Returns True if the admin has NOT yet changed their password."""
    redis = await get_redis()
    changed = await redis.get(FORCE_CHANGE_KEY)
    return changed is None


async def _mark_password_changed():
    redis = await get_redis()
    # Persist forever (no expiry)
    await redis.set(FORCE_CHANGE_KEY, "1")


def _check_credentials(username: str, password: str) -> bool:
    """
    Check credentials in priority order:
    1. Current ADMIN_PASSWORD from settings (supports plain or bcrypt)
    2. Default credentials (only while password hasn't been changed)
    """
    if username == settings.ADMIN_USERNAME:
        # Plain text match (covers initial .env setup and dev)
        if password == settings.ADMIN_PASSWORD:
            return True
        # bcrypt match (after hashed password is stored)
        try:
            if verify_password(password, settings.ADMIN_PASSWORD):
                return True
        except Exception:
            pass
    # Allow default creds as fallback until changed
    if username == DEFAULT_USERNAME and password == DEFAULT_PASSWORD:
        return True
    return False


def _persist_credentials(new_username: str, new_password: str):
    """Write new username and password to the .env file on disk."""
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "../../.env"),
    ]
    for env_path in candidates:
        env_path = os.path.abspath(env_path)
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                content = f.read()
            content = re.sub(r"^ADMIN_USERNAME=.*$", f"ADMIN_USERNAME={new_username}", content, flags=re.MULTILINE)
            content = re.sub(r"^ADMIN_PASSWORD=.*$", f"ADMIN_PASSWORD={new_password}", content, flags=re.MULTILINE)
            with open(env_path, "w") as f:
                f.write(content)
            return


def _persist_password(new_password: str):
    """Write only password to the .env file."""
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "../../.env"),
    ]
    for env_path in candidates:
        env_path = os.path.abspath(env_path)
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                content = f.read()
            content = re.sub(r"^ADMIN_PASSWORD=.*$", f"ADMIN_PASSWORD={new_password}", content, flags=re.MULTILINE)
            with open(env_path, "w") as f:
                f.write(content)
            return


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    client_ip = request.client.host
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    await check_brute_force(client_ip)

    if not _check_credentials(form_data.username, form_data.password):
        await record_failed_login(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    await clear_login_attempts(client_ip)

    # Flag must_change if still on default password
    using_default = (
        form_data.username == DEFAULT_USERNAME
        and form_data.password == DEFAULT_PASSWORD
    )
    must_change = using_default and await _is_default_password_unchanged()

    data = {"sub": form_data.username}
    return TokenResponse(
        access_token=create_access_token(data),
        refresh_token=create_refresh_token(data),
        must_change_password=must_change,
    )


@router.post("/change-credentials")
async def change_credentials(
    body: ChangeCredentialsRequest,
    admin=Depends(get_current_admin),
):
    """First-login endpoint: changes both username and password at once."""
    if not _check_credentials(admin["username"], body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    _persist_credentials(body.new_username, body.new_password)
    settings.ADMIN_USERNAME = body.new_username
    settings.ADMIN_PASSWORD = body.new_password
    await _mark_password_changed()

    return {"ok": True, "message": "Credentials updated. Please sign in with your new username and password."}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    admin=Depends(get_current_admin),
):
    """Settings page: change password only (username stays the same)."""
    if not _check_credentials(admin["username"], body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    _persist_password(body.new_password)
    settings.ADMIN_PASSWORD = body.new_password
    await _mark_password_changed()

    return {"ok": True, "message": "Password updated successfully."}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    data = {"sub": payload["sub"]}
    return TokenResponse(
        access_token=create_access_token(data),
        refresh_token=create_refresh_token(data),
    )


@router.get("/me")
async def me(admin=Depends(get_current_admin)):
    must_change = await _is_default_password_unchanged()
    return {"username": admin["username"], "must_change_password": must_change}
