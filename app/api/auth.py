"""Clerk JWT verification as FastAPI dependencies.

Two dependencies are provided:
  get_optional_user — returns User | None; never raises on anonymous requests.
  get_required_user — raises HTTP 401 if no authenticated user is present.

JWKS key source is initialised once at FastAPI lifespan via init_jwks_client().
PyJWKClient handles key caching and rotation transparently.
"""

from __future__ import annotations

import logging

import jwt
from fastapi import Depends, HTTPException, Request
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.db_models import User

logger = logging.getLogger(__name__)

# Module-level JWKS client — populated once at startup via init_jwks_client().
# PyJWKClient handles key caching and transparent refresh on unknown key IDs,
# so a single instance is safe across concurrent requests.
_JWKS_CLIENT: PyJWKClient | None = None


def init_jwks_client() -> None:
    """Initialise the module-level JWKS client.

    Called during FastAPI lifespan (after init_db, before yield).
    Uses PyJWKClient with default caching (300 s JWK set TTL).
    No network I/O at construction time — the first key fetch is deferred
    until the first JWT verification attempt.
    """
    global _JWKS_CLIENT
    _JWKS_CLIENT = PyJWKClient(settings.clerk_jwks_url)
    logger.info("Clerk JWKS client initialised (url=%s)", settings.clerk_jwks_url)


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Extract and verify a Clerk JWT from the Authorization header.

    Returns the matching User row if the token is valid and the user exists
    in the local database. Returns None in all other cases — missing header,
    malformed token, expired token, invalid signature, or user not yet synced
    via webhook. Never raises on anonymous requests.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        return None

    if _JWKS_CLIENT is None:
        logger.warning("JWKS client not initialised — cannot verify JWT")
        return None

    try:
        signing_key = _JWKS_CLIENT.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        # Covers: ExpiredSignatureError, InvalidSignatureError, DecodeError, etc.
        return None
    except Exception:
        # PyJWKClient network errors or unexpected failures — treat as unauthenticated.
        logger.exception("Unexpected error during JWT verification")
        return None

    clerk_user_id: str | None = payload.get("sub")
    if not clerk_user_id:
        return None

    user: User | None = await db.scalar(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return user


async def get_required_user(
    user: User | None = Depends(get_optional_user),
) -> User:
    """Require an authenticated user.

    Raises HTTP 401 if get_optional_user returns None.
    Not used by any route in Phase 1a — implemented for Phase 2.
    """
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
