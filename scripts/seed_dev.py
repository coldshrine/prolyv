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
from prolyv.models import Identity, Provider, Steep, Tea, TeaSession, User, Visibility, utcnow  # noqa: E402

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


def seed_sessions(db, users: list[User]) -> None:  # noqa: ANN001
    """A handful of sessions so /diary, /u/{handle} and the feed have something to show."""
    from datetime import timedelta

    if db.execute(select(TeaSession).limit(1)).first() is not None:
        print("  = sessions exist")
        return
    teas = {t.slug: t for t in db.execute(select(Tea)).scalars().all()}
    olena, mike = users[1], users[2]
    now = utcnow()
    samples = [
        (olena, "shu-puerh-gong-ting", "Shu pu-erh Gong Ting", 8, 100, 100, "gaiwan", 5, ["cocoa", "earthy", "sweet"],
         "Cocoa and damp forest, date-like sweetness by the fifth steep. No mustiness at all.", [10, 8, 10, 15, 20, 30, 45, 60, 90, 120], 0, True),
        (olena, "tie-guan-yin-qing-xiang", "Tie Guan Yin (Qing Xiang)", 7, 110, 95, "gaiwan", 4, ["floral", "creamy"],
         "Keeping steep 6 short saved the cream. Orchid all the way.", [15, 10, 15, 20, 30, 45, 60], 2, True),
        (olena, "bai-mu-dan", "Bai Mu Dan 2021", 6, 120, 90, "teapot", 4, ["honey", "sweet"],
         "Dried apricot and hay. Much deeper than the young one.", [30, 30, 45, 60, 90], 5, False),
        (mike, "darjeeling-first-flush", "Darjeeling First Flush, Castleton", 3, 200, 90, "teapot", 5, ["floral", "brisk", "fruity"],
         "Muscatel grape and green almonds. Three minutes is the sweet spot.", [180, 240], 1, True),
        (mike, "sencha", "Sencha (fukamushi)", 5, 150, 75, "kyusu", 3, ["umami", "grassy"],
         "Too hot on the first pour, bitter. Second and third were lovely.", [60, 20, 45], 3, True),
    ]
    for user, slug, name, grams, water, temp, vessel, rating, tags, note, steeps, days_ago, public in samples:
        tea = teas.get(slug)
        sess = TeaSession(user=user, tea=tea, tea_name=tea.name_en if tea else name, grams=grams, water_ml=water,
                          temp_c=temp, vessel=vessel, rinse=True, rating=rating, taste_tags=tags, note=note,
                          visibility=Visibility.public if public else Visibility.private,
                          brewed_at=now - timedelta(days=days_ago, hours=3))
        sess.steeps = [Steep(n=i + 1, seconds=s) for i, s in enumerate(steeps)]
        db.add(sess)
    print(f"  + {len(samples)} sessions")


EXTRA_SEEDERS: list = [seed_sessions]  # later milestones append functions (db, users) -> None


if __name__ == "__main__":
    raise SystemExit(main())
