#!/usr/bin/env python
"""Local development data: a few users (one admin) and, as later milestones land, sessions, topics and posts.

Safe to re-run: existing rows are kept. Sign in with DEV_LOGIN=1 in .env and open /dev/login/<handle>.

    uv run python scripts/seed_dev.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prolyv.config import get_settings  # noqa: E402
from prolyv.db import make_engine, make_session_factory  # noqa: E402
from prolyv.models import Identity, Provider, User  # noqa: E402

USERS = [
    ("karyna", "Karyna", "Tea TikToker. Runs Prolyv. Mostly oolong and sheng.", True),
    ("olena_tea", "Olena K.", "Lviv · Mostly shu and aged oolong. I log every session, even the bad ones.", False),
    ("tea_mike", "Mike", "Darjeeling first flush believer, gaiwan convert since 2024.", False),
]


def seed_users(db) -> list[User]:  # noqa: ANN001
    users = []
    for handle, name, bio, _is_admin in USERS:
        user = db.execute(select(User).where(User.handle == handle)).scalar_one_or_none()
        if user is None:
            user = User(handle=handle, display_name=name, bio=bio)
            db.add(user)
            db.flush()
            db.add(Identity(user=user, provider=Provider.telegram, provider_user_id=f"dev-{handle}",
                            raw_profile={"id": f"dev-{handle}", "first_name": name, "username": handle}))
            print(f"  + user @{handle}")
        else:
            print(f"  = user @{handle} exists")
        users.append(user)
    return users


def main() -> int:
    settings = get_settings()
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as db:
        users = seed_users(db)
        for extra in EXTRA_SEEDERS:
            extra(db, users)
        db.commit()
    admins = [h for h, _, _, is_admin in USERS if is_admin]
    print(f"\ndone. Admin handle(s) for ADMIN_HANDLES: {', '.join(admins)}")
    if settings.dev_login_enabled:
        for handle, *_ in USERS:
            print(f"  {settings.base_url}/dev/login/{handle}")
    else:
        print("  set DEV_LOGIN=1 in .env to sign in as these users at /dev/login/<handle>")
    return 0


EXTRA_SEEDERS: list = []  # later milestones append functions (db, users) -> None


if __name__ == "__main__":
    raise SystemExit(main())
