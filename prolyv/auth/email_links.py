"""One-time e-mail sign-in links: the token goes in the link, only its SHA-256 is stored, valid for 15 minutes."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prolyv.auth.accounts import ExternalProfile
from prolyv.handles import slugify
from prolyv.models import LoginToken, Provider, utcnow
from prolyv.security import hash_token, new_session_token

TOKEN_MINUTES = 15
MAX_PER_HOUR = 5
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def normalize_email(value: str) -> str | None:
    email = (value or "").strip().lower()
    return email if len(email) <= 320 and EMAIL_RE.fullmatch(email) else None


def create_login_token(db: Session, email: str, user_id: uuid.UUID | None = None) -> str | None:
    """Returns the raw token, or None when this address asked for too many links in the last hour."""
    since = utcnow() - timedelta(hours=1)
    recent = db.scalar(select(func.count()).select_from(LoginToken).where(LoginToken.email == email, LoginToken.created_at >= since))
    if (recent or 0) >= MAX_PER_HOUR:
        return None
    token = new_session_token()
    db.add(LoginToken(email=email, token_hash=hash_token(token), user_id=user_id,
                      expires_at=utcnow() + timedelta(minutes=TOKEN_MINUTES)))
    db.flush()
    return token


def find_valid_token(db: Session, token: str | None) -> LoginToken | None:
    if not token:
        return None
    row = db.execute(select(LoginToken).where(LoginToken.token_hash == hash_token(token))).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        return None
    return row


def email_profile(email: str) -> ExternalProfile:
    local = email.split("@", 1)[0]
    name = " ".join(part.capitalize() for part in slugify(local).split("_") if part) or "Tea drinker"
    return ExternalProfile(provider=Provider.email, provider_user_id=email, display_name=name, preferred_handle=local,
                           email=email, raw={"email": email})
