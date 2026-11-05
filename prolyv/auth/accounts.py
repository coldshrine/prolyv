"""Account logic shared by both providers: find-or-create, linking, unlinking, soft delete."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from prolyv.handles import generate_handle
from prolyv.models import AuthSession, Identity, Provider, User, utcnow


class AccountError(Exception):
    """code: identity_taken (linked to another account) | last_provider (cannot unlink)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class ExternalProfile:
    """What a provider told us about the person who just signed in."""

    provider: Provider
    provider_user_id: str
    display_name: str
    preferred_handle: str
    email: str | None = None  # only set when the provider verified it
    avatar_url: str | None = None
    raw: dict = field(default_factory=dict)


def handle_taken(db: Session, handle: str, except_user_id: uuid.UUID | None = None) -> bool:
    stmt = select(User.id).where(User.handle == handle)
    if except_user_id is not None:
        stmt = stmt.where(User.id != except_user_id)
    return db.execute(stmt).first() is not None


def get_user_by_handle(db: Session, handle: str) -> User | None:
    return db.execute(select(User).where(User.handle == handle, User.deleted_at.is_(None))).scalar_one_or_none()


def _find_identity(db: Session, provider: Provider, provider_user_id: str) -> Identity | None:
    return db.execute(
        select(Identity).where(Identity.provider == provider, Identity.provider_user_id == provider_user_id)
    ).scalar_one_or_none()


def _find_google_by_email(db: Session, email: str) -> Identity | None:
    return db.execute(
        select(Identity)
        .join(User)
        .where(Identity.provider == Provider.google, Identity.email == email, User.deleted_at.is_(None))
    ).scalars().first()


def sign_in(db: Session, profile: ExternalProfile, current_user: User | None = None) -> tuple[User, bool]:
    """Resolve an external login to a Prolyv user. Returns (user, created).

    Order: an existing identity wins; a logged-in user links the new provider to their account; a Google
    identity whose verified email matches an existing Google identity joins that account; otherwise a new
    user is created with a generated handle.
    """
    identity = _find_identity(db, profile.provider, profile.provider_user_id)
    created = False

    if identity is not None:
        if current_user is not None and identity.user_id != current_user.id:
            raise AccountError("identity_taken")
        user = identity.user
    elif current_user is not None:
        user = current_user
        identity = Identity(user=user, provider=profile.provider, provider_user_id=profile.provider_user_id)
        db.add(identity)
    else:
        user = None
        if profile.provider == Provider.google and profile.email:
            match = _find_google_by_email(db, profile.email)
            if match is not None:
                user = match.user
        if user is None:
            user = User(
                handle=generate_handle(profile.preferred_handle, lambda h: handle_taken(db, h)),
                display_name=profile.display_name[:50] or "Tea drinker",
                avatar_url=profile.avatar_url,
            )
            db.add(user)
            created = True
        identity = Identity(user=user, provider=profile.provider, provider_user_id=profile.provider_user_id)
        db.add(identity)

    identity.email = profile.email
    identity.raw_profile = profile.raw
    if not user.avatar_url and profile.avatar_url:
        user.avatar_url = profile.avatar_url
    db.flush()
    return user, created


def unlink(db: Session, user: User, provider: Provider) -> None:
    """Remove one linked provider. Refused when it is the only way to sign in."""
    identity = user.identity_for(provider)
    if identity is None:
        return
    if len(user.identities) <= 1:
        raise AccountError("last_provider")
    user.identities.remove(identity)
    db.delete(identity)
    db.flush()


def purge_sessions(db: Session, user: User) -> int:
    count = 0
    for sess in list(user.sessions):
        db.delete(sess)
        count += 1
    db.flush()
    return count


def soft_delete(db: Session, user: User) -> None:
    """Keep the row (future posts may reference it) but free the handle, drop logins and sessions."""
    purge_sessions(db, user)
    for identity in list(user.identities):
        db.delete(identity)
    user.identities.clear()
    user.deleted_at = utcnow()
    user.handle = f"deleted_{user.id.hex[:12]}"
    user.display_name = "Deleted account"
    user.bio = ""
    user.avatar_url = None
    db.flush()


def delete_expired_sessions(db: Session) -> int:
    expired = db.execute(select(AuthSession).where(AuthSession.expires_at < utcnow())).scalars().all()
    for sess in expired:
        db.delete(sess)
    db.flush()
    return len(expired)
