"""Notifications and follows: small helpers the routes call."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prolyv.models import Follow, Notification, User

MAX_FANOUT = 1000


def notify(db: Session, user_id: uuid.UUID, actor: User | None, text: str, url: str) -> None:
    if actor is not None and actor.id == user_id:
        return
    db.add(Notification(user_id=user_id, actor_id=actor.id if actor else None, text=text[:300], url=url[:300]))


def notify_followers(db: Session, actor: User, text: str, url: str) -> None:
    follower_ids = db.execute(select(Follow.follower_id).where(Follow.followee_id == actor.id).limit(MAX_FANOUT)).scalars().all()
    for follower_id in follower_ids:
        notify(db, follower_id, actor, text, url)


def is_following(db: Session, follower: User | None, followee_id: uuid.UUID) -> bool:
    if follower is None:
        return False
    return db.execute(select(Follow.id).where(Follow.follower_id == follower.id, Follow.followee_id == followee_id)).first() is not None


def follow_counts(db: Session, user_id: uuid.UUID) -> tuple[int, int]:
    followers = db.scalar(select(func.count()).select_from(Follow).where(Follow.followee_id == user_id)) or 0
    following = db.scalar(select(func.count()).select_from(Follow).where(Follow.follower_id == user_id)) or 0
    return followers, following


def followee_ids(db: Session, user: User) -> list[uuid.UUID]:
    return list(db.execute(select(Follow.followee_id).where(Follow.follower_id == user.id)).scalars().all())
