from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import settings
from app.redis_client import get_redis

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

BRUTE_FORCE_MAX_ATTEMPTS = 10
BRUTE_FORCE_WINDOW = 900  # 15 minutes


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_admin(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> dict:
    # IP whitelist check
    if settings.allowed_admin_ips:
        client_ip = request.client.host
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        if client_ip not in settings.allowed_admin_ips:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied from this IP",
            )

    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    username = payload.get("sub")
    if username != settings.ADMIN_USERNAME:
        raise HTTPException(status_code=401, detail="Not authorized")

    return {"username": username}


async def check_brute_force(ip: str) -> None:
    redis = await get_redis()
    key = f"login_attempts:{ip}"
    attempts = await redis.get(key)
    if attempts and int(attempts) >= BRUTE_FORCE_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again in 15 minutes.",
        )


async def record_failed_login(ip: str) -> None:
    redis = await get_redis()
    key = f"login_attempts:{ip}"
    await redis.incr(key)
    await redis.expire(key, BRUTE_FORCE_WINDOW)


async def clear_login_attempts(ip: str) -> None:
    redis = await get_redis()
    await redis.delete(f"login_attempts:{ip}")
