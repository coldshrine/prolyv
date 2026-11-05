#!/usr/bin/env python
"""Demo users and activity for local development only. It refuses to run unless BASE_URL is localhost.

Real content (teas, sections, starter topics, guides) comes from scripts/seed_prod.py. Safe to re-run: existing rows
are kept. With DEV_LOGIN=1 in .env, sign in at /login (demo user) or /dev/login/<handle>.

    uv run python scripts/seed_prod.py --skip-teas
    uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prolyv.config import get_settings  # noqa: E402
from prolyv.db import make_engine, make_session_factory  # noqa: E402
from prolyv.search import rebuild  # noqa: E402
from prolyv.models import (  # noqa: E402
    ForumSection,
    Identity,
    Post,
    Provider,
    Reply,
    Steep,
    Tea,
    TeaSession,
    Topic,
    User,
    Visibility,
    utcnow,
)

USERS = [
    ("karyna", "Karyna", "Tea TikToker. Runs Prolyv. Mostly oolong and sheng.", True),
    ("olena_tea", "Olena K.", "Lviv · Mostly shu and aged oolong. I log every session, even the bad ones.", False),
    ("tea_mike", "Mike", "Darjeeling first flush believer, gaiwan convert since 2024.", False),
    ("demo_user", "Demo User", "A local demo account.", False),
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
    if not settings.is_local:
        print("seed_demo.py only runs when BASE_URL is localhost. Use scripts/seed_prod.py on a real site.")
        return 1
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as db:
        users = seed_users(db)
        for extra in EXTRA_SEEDERS:
            extra(db, users)
        db.commit()
        rebuild(db)
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


def seed_forum(db, users: list[User]) -> None:  # noqa: ANN001
    from prolyv.search import index_reply, index_topic

    if db.execute(select(Topic).where(Topic.user_id == users[1].id).limit(1)).first() is not None:
        print("  = demo topics exist")
        return
    sections = {s.slug: s for s in db.execute(select(ForumSection)).scalars().all()}
    karyna, olena, mike = users[0], users[1], users[2]
    public = db.execute(select(TeaSession).where(TeaSession.visibility == Visibility.public)).scalars().all()
    samples = [
        (olena, "pu-erh", "Why does my shu pu-erh turn bitter after the third steep?",
         "8 g in a 100 ml gaiwan, water off the boil, 10 second steeps.\n\nSteeps one and two are lovely and sweet, then it gets harsh and drying. Same leaf, same water. What am I doing wrong?",
         [(mike, "Shu needs one quick rinse to wake up, and I'd go 7 g. From the third steep keep them at 5 seconds and it stays sweet.", True),
          (karyna, "Also check the water: very soft water makes shu flat and then bitter. A pinch of mineralisation helps.", False)]),
        (mike, "oolong", "Tie Guan Yin: spring vs autumn harvest, side by side",
         "Brewed the spring and the autumn lot from the same farm back to back. Autumn wins on aroma, spring on texture.\n\nAnyone else notice this? Attaching the session.", []),
        (karyna, "teaware", "Porcelain or clay for sheng?",
         "I keep going back and forth. Porcelain shows me the tea, clay rounds it off. What do you use, and for which years?", []),
    ]
    for author, section_slug, title, body, replies in samples:
        topic = Topic(section=sections[section_slug], user=author, title=title, body=body,
                      attached_session=next((s for s in public if s.user_id == author.id), None))
        db.add(topic)
        db.flush()
        index_topic(db, topic.id, title, body)
        for replier, text, helpful in replies:
            reply = Reply(topic=topic, user=replier, body=text, marked_helpful_by_author=helpful, helpful_count=3 if helpful else 0)
            db.add(reply)
            db.flush()
            index_reply(db, reply.id, topic.id, text)
            topic.reply_count += 1
            topic.last_reply_at = utcnow()
            topic.solved = topic.solved or helpful
    print(f"  + {len(samples)} topics")


def seed_demo_diary(db, users: list[User]) -> None:  # noqa: ANN001
    """Fill the demo user's diary so the screen shows what a used account looks like."""
    from datetime import timedelta

    demo = users[3]
    if db.execute(select(TeaSession).where(TeaSession.user_id == demo.id).limit(1)).first() is not None:
        print("  = demo diary exists")
        return
    teas = {t.slug: t for t in db.execute(select(Tea)).scalars().all()}
    now = utcnow()
    samples = [
        ("da-hong-pao", "Da Hong Pao", 7, 100, 100, "gaiwan", 4, ["roasted", "mineral"], "Charcoal first, then stone fruit from the third steep.", [10, 10, 15, 20, 30, 45], 0, False),
        ("sencha", "Sencha", 5, 150, 70, "kyusu", 5, ["umami", "grassy"], "Cooler water fixed the bitterness.", [60, 20, 45], 1, True),
        ("bai-mu-dan", "Bai Mu Dan", 6, 120, 90, "teapot", 3, ["honey", "sweet"], "Quiet but pleasant.", [45, 60, 90], 3, False),
        ("sheng-puerh-yiwu", "Sheng pu-erh, Yiwu", 7, 100, 95, "gaiwan", 4, ["floral", "honey"], "", [10, 10, 12, 15, 20, 25, 30], 6, True),
        ("darjeeling-first-flush", "Darjeeling First Flush", 3, 200, 90, "teapot", 4, ["floral", "brisk"], "Three minutes was right.", [180], 12, False),
    ]
    for slug, name, grams, water, temp, vessel, rating, tags, note, steeps, days_ago, public in samples:
        tea = teas.get(slug)
        sess = TeaSession(user=demo, tea=tea, tea_name=tea.name_en if tea else name, grams=grams, water_ml=water, temp_c=temp,
                          vessel=vessel, rating=rating, taste_tags=tags, note=note, announced=public,
                          visibility=Visibility.public if public else Visibility.private, brewed_at=now - timedelta(days=days_ago, hours=2))
        sess.steeps = [Steep(n=i + 1, seconds=x) for i, x in enumerate(steeps)]
        db.add(sess)
    print(f"  + {len(samples)} demo diary sessions")


EXTRA_SEEDERS: list = [seed_sessions, seed_demo_diary, seed_forum]


if __name__ == "__main__":
    raise SystemExit(main())
