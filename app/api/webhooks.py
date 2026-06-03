"""Clerk webhook endpoint.

Receives user lifecycle events from Clerk via Svix-signed POST requests.
Syncs user.created and user.updated events to the local users table.

Signature verification uses the svix library (official Clerk recommendation).
If clerk_webhook_secret is empty, verification is skipped with a warning —
this matches the admin_api_key pattern used elsewhere in the codebase.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from app.core.config import settings
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/clerk")
async def clerk_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Handle Clerk user lifecycle webhook events.

    Raw body is read before any JSON parsing so the Svix signature covers
    the exact bytes Clerk sent. Event types handled:
      user.created  — upsert into users (idempotent; Clerk may replay events)
      user.updated  — update email where clerk_user_id matches
    All other event types return 200 with status: ignored.
    """
    body: bytes = await request.body()

    if not settings.clerk_webhook_secret:
        logger.warning(
            "clerk_webhook_secret is empty — skipping webhook signature verification"
        )
    else:
        headers_dict: dict[str, str] = {
            "svix-id": request.headers.get("svix-id", ""),
            "svix-timestamp": request.headers.get("svix-timestamp", ""),
            "svix-signature": request.headers.get("svix-signature", ""),
        }
        try:
            Webhook(settings.clerk_webhook_secret).verify(body, headers_dict)
        except WebhookVerificationError:
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        payload: dict = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type: str = payload.get("type", "")
    data: dict = payload.get("data", {})

    if event_type == "user.created":
        await _handle_user_created(db, data)
    elif event_type == "user.updated":
        await _handle_user_updated(db, data)
    else:
        return {"status": "ignored"}

    return {"status": "ok"}


def _extract_primary_email(data: dict) -> str:
    """Extract the primary email address from a Clerk user payload.

    Clerk sends email_addresses as a list; the primary one is identified by
    primary_email_address_id matching the id field on an email address object.
    Falls back to the first address in the list if no primary is marked.
    Returns an empty string if no email addresses are present.
    """
    email_addresses: list[dict] = data.get("email_addresses", [])
    if not email_addresses:
        return ""

    primary_id: str = data.get("primary_email_address_id", "")
    for addr in email_addresses:
        if addr.get("id") == primary_id:
            return addr.get("email_address", "")

    # Fallback: use the first address
    return email_addresses[0].get("email_address", "")


async def _handle_user_created(db: AsyncSession, data: dict) -> None:
    """Upsert a user row on user.created.

    Uses INSERT ... ON CONFLICT (clerk_user_id) DO UPDATE so that Clerk
    event replays are idempotent — replaying user.created is safe.
    """
    clerk_user_id: str = data.get("id", "")
    email: str = _extract_primary_email(data)

    if not clerk_user_id:
        logger.warning("user.created event missing id field — skipping")
        return

    await db.execute(
        text(
            """
            INSERT INTO users (id, clerk_user_id, email, created_at)
            VALUES (gen_random_uuid(), :clerk_user_id, :email, now())
            ON CONFLICT (clerk_user_id) DO UPDATE SET email = EXCLUDED.email
            """
        ),
        {"clerk_user_id": clerk_user_id, "email": email},
    )
    await db.commit()
    logger.info("user.created processed: clerk_user_id=%s", clerk_user_id)


async def _handle_user_updated(db: AsyncSession, data: dict) -> None:
    """Update email on user.updated."""
    clerk_user_id: str = data.get("id", "")
    email: str = _extract_primary_email(data)

    if not clerk_user_id:
        logger.warning("user.updated event missing id field — skipping")
        return

    await db.execute(
        text(
            "UPDATE users SET email = :email WHERE clerk_user_id = :clerk_user_id"
        ),
        {"clerk_user_id": clerk_user_id, "email": email},
    )
    await db.commit()
    logger.info("user.updated processed: clerk_user_id=%s", clerk_user_id)
