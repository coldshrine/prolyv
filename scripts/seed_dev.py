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
from prolyv.markdown import render_markdown  # noqa: E402
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


def seed_forum(db, users: list[User]) -> None:  # noqa: ANN001
    from prolyv.forum_search import index_reply, index_topic

    if db.execute(select(Topic).limit(1)).first() is not None:
        print("  = topics exist")
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
        (karyna, "beginners", "What to buy first: gaiwan or a small teapot?",
         "If you are starting gongfu brewing with oolong and pu-erh, a 100 ml porcelain gaiwan does everything. Teapots come later, when you know what you like.", []),
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


POST_BODY = """## The short version

Shu pu-erh turns bitter when the leaf is pushed too hard at the start. One quick rinse, then keep the first three
steeps at five to ten seconds, and it stays sweet for a dozen infusions.

## Parameters I used

- 8 g of leaf, a 100 ml gaiwan, water just off the boil
- rinse 5 s, then 5 · 5 · 8 · 10 · 15 · 20 · 30 · 45 · 60 · 90 seconds
- porcelain, not clay: you want to taste the leaf, not the pot

## What to listen for

By steep four the cocoa shows up; by steep seven it is dates and damp forest. If it is drying at steep three, the
water was too hot for too long. Brew along below and tell me where yours turned.
"""


def seed_posts(db, users: list[User]) -> None:  # noqa: ANN001
    if db.execute(select(Post).limit(1)).first() is not None:
        print("  = posts exist")
        return
    karyna = users[0]
    tea = db.execute(select(Tea).where(Tea.slug == "shu-puerh-gong-ting")).scalar_one_or_none()
    post = Post(author=karyna, slug="why-shu-puerh-turns-bitter", title="Why shu pu-erh turns bitter after the third steep",
                summary="One rinse, short early steeps, and the cocoa shows up by steep four.", body_md=POST_BODY,
                body_html=render_markdown(POST_BODY), tea=tea, published_at=utcnow())
    db.add(post)
    print("  + 1 post")


EXTRA_SEEDERS: list = [seed_sessions, seed_forum, seed_posts]  # later milestones append functions (db, users) -> None


if __name__ == "__main__":
    raise SystemExit(main())
