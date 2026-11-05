#!/usr/bin/env python
"""Real launch content: the tea base, forum sections, starter topics and guides, written under the Prolyv name.

No invented people, views or statistics. Safe to re-run: existing guides and topics are left untouched, so edits made
in the admin stay.

    uv run python scripts/seed_prod.py               # also runs scripts/import_teas.py
    uv run python scripts/seed_prod.py --skip-teas
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from prolyv.config import get_settings  # noqa: E402
from prolyv.db import make_engine, make_session_factory  # noqa: E402
from prolyv.markdown import render_markdown  # noqa: E402
from prolyv.models import ForumSection, Post, Tea, Topic, User, utcnow  # noqa: E402
from prolyv.search import ensure_forum_sections, ensure_fts, index_post, index_topic  # noqa: E402

GUIDES = [
    {
        "slug": "first-gongfu-session",
        "title": "Your first gongfu session, step by step",
        "summary": "A gaiwan, a jug, a cup and ten minutes. Here is the whole routine with numbers you can copy.",
        "tea": "tie-guan-yin-qing-xiang",
        "body": """Gongfu brewing means a lot of leaf, a little water and short steeps, repeated many times. It looks like a ceremony, but at home it is just a way to taste how a tea changes from one pour to the next. All you need is a small brewing vessel, something to pour into, a cup and a kettle.

## What you need

- A gaiwan or small teapot, about 100 ml
- A small jug, called a fair cup
- One small cup
- A kitchen scale, if you have one

## A recipe to start with

For most oolongs, use **7 g of leaf per 100 ml** and water just off the boil.

1. Warm everything with hot water and pour it away.
2. Add the leaf.
3. Pour water over it and tip it straight out. This is the rinse. It opens tightly rolled leaf, and you can skip it for delicate teas.
4. Fill the vessel again, wait **15 seconds**, and pour everything into the jug.
5. Drink, then repeat. Keep the second steep about as short as the first, then add 5 to 10 seconds each time.

## How to adjust

If the tea tastes harsh, shorten the steep or use a little less leaf. If it tastes thin, steep longer or use more leaf. Change one thing at a time and write it down, otherwise you won't know what helped.

Pour out all the water each time, because leaf sitting in water keeps brewing. Stop when the steeps stop being interesting, which for many teas means somewhere between five and ten.

## Keep the numbers

Log the session in your diary as you go. The timer on the session page records each steep, and next time you can press "Brew it the same way" and start from what worked.
""",
    },
    {
        "slug": "leaf-water-temperature-starting-points",
        "title": "Leaf, water and temperature: starting points for every kind of tea",
        "summary": "A table to start from when a package gives no instructions, and how to correct it by taste.",
        "tea": None,
        "body": """No table can tell you how a particular tea should be brewed. It can only tell you where to start. These are ordinary starting points for gongfu brewing in a 100 ml vessel. Treat them as a first attempt, then correct by taste.

| Tea | Leaf | Water | First steep |
|---|---|---|---|
| Green | 3–4 g | 75–85 °C | 30 s |
| White, buds | 5 g | 85–90 °C | 30 s |
| Yellow | 4 g | 80–85 °C | 30 s |
| Light oolong | 7 g | 95 °C | 15 s |
| Roasted oolong | 7 g | 100 °C | 10 s |
| Black | 5 g | 90–95 °C | 15 s |
| Young sheng pu-erh | 7 g | 90–95 °C | 10 s |
| Shu pu-erh | 7 g | 100 °C | 10 s |

## Brewing in a mug or teapot

For Western brewing, the usual starting point is about 1 g of leaf per 100 ml of water, steeped for 2 to 4 minutes at the same temperature as above.

## Correcting by taste

- **Bitter or drying:** cooler water or a shorter steep.
- **Thin or watery:** more leaf or a longer steep.
- **Sour or flat:** often water that was too cool for that tea.

Water matters more than most people expect. Very hard water can dull a tea, so if everything tastes flat, try filtered water before blaming the leaf.

Every card in the tea base lists brewing parameters for that tea, so check there first.
""",
    },
    {
        "slug": "reading-puerh-wrapper",
        "title": "How to read a pu-erh wrapper",
        "summary": "Sheng or shu, what the year means, and what recipe numbers like 7542 tell you.",
        "tea": "dayi-7542",
        "body": """A pu-erh wrapper can look like a puzzle, but only a few pieces of information matter when you are choosing what to buy.

## Sheng or shu

**Sheng** (raw) is sun-dried and pressed without a fast fermentation step. Young sheng can be bright and bitter, and it changes slowly over years of storage. **Shu** (ripe) goes through a wet-pile fermentation before pressing, so it is dark, smooth and ready to drink. Wrappers often show 生茶 for raw and 熟茶 for ripe. The leaf and the liquor are the real test: young sheng brews pale gold, shu brews a deep red-brown.

## The year

Pu-erh is sold by year, but the printed year is a claim and not a guarantee, particularly for expensive old teas. Ask whether it means the year the leaf was picked or the year it was pressed.

For sheng, a useful rough guide is: young up to about 5 years, mid-aged around 5 to 15, aged beyond that. Storage matters as much as age. The same cake kept in dry Kunming and in humid Guangzhou will taste very different.

## Recipe numbers

Factory teas often carry a four-digit number. The usual reading is:

- the first two digits give the year the recipe was first made
- the third gives the main leaf grade
- the fourth gives the factory: 1 for Kunming, 2 for Menghai, 3 for Xiaguan, 4 for Fengqing

So 7542 is a recipe from 1975, leaf grade 4, from Menghai. The number names the recipe, not the year this cake was made. A cake marked 7542 might have been pressed decades later.

## Weight and shape

A standard cake weighs about 357 g, and seven of them make a bamboo-wrapped tong. Tuos, bricks and small cakes are also common.

## Mountain names

Names such as Lao Ban Zhang or Bingdao carry high prices, and they are often used on tea from elsewhere. A famous name at a low price deserves suspicion. Buy a small sample first when a shop offers one.
""",
    },
    {
        "slug": "storing-tea-at-home",
        "title": "How to store tea at home",
        "summary": "What light, air, moisture and smells do to tea, and which teas belong in the fridge.",
        "tea": None,
        "body": """Tea has four enemies at home: light, air, moisture and strong smells. Good storage mostly means keeping those away, and it doesn't need special equipment. Keep tea out of the kitchen cupboard above the stove and away from spices and coffee, because leaf picks up smells easily.

## Fresh green and light oolong

These lose aroma fastest. Keep them sealed in an opaque, airtight container, buy small amounts and drink them within a few months to a year.

The fridge can help with vacuum-sealed packs, particularly green-style Tie Guan Yin and high-mountain oolong. Let a cold pack reach room temperature before you open it, otherwise moisture condenses on the leaf.

## Black tea and roasted oolong

These are more forgiving. An airtight tin at room temperature is enough, and they usually hold up well for a year or two. Roasted oolongs are sometimes rested for months after roasting, because a fresh roast can taste sharp.

## White tea

Loose fresh white tea can be stored like green tea. Pressed white tea is often kept for years, in a dry place with no smells.

## Pu-erh and other dark teas

These are the teas people deliberately age. Keep them away from direct light and strong smells, with some air around them, and with moderate humidity. Damp conditions can cause mould, and it is safest to throw away tea that smells musty or shows fuzzy patches. Don't confuse mould with the yellow "golden flowers" that are a normal part of Fu brick, which are an intended part of how that tea is made.

## A simple rule

If you bought it to drink soon, keep it sealed. If you bought it to age, keep it clean, dark and dry, and check on it now and then.
""",
    },
    {
        "slug": "keeping-a-tea-diary",
        "title": "What to write in a tea diary",
        "summary": "Five things worth recording, and why a bad session is the most useful one to log.",
        "tea": None,
        "body": """A tea diary doesn't have to be poetry. Its job is simple: next time, you should be able to repeat what worked and avoid what didn't. Thirty seconds per session is enough.

## Five things worth recording

1. **The tea.** Its name, the harvest or year if you know it, and where you got it.
2. **Leaf and water.** Grams, millilitres and temperature. Without numbers, you can't repeat a good session or fix a bad one.
3. **The steeps.** How long each one was. You will often find the tea peaked at a particular steep.
4. **What changed.** You don't need fancy tasting words. "Sweeter by steep three" or "drying at the end" is more useful than a long list of flowers.
5. **One thing to try next time.** This is the note you will thank yourself for.

## Log the bad sessions too

A session that went wrong tells you more than one that went well. If you wrote down that 100 °C made a green tea bitter, you won't make that mistake again.

## Private by default

Sessions in your Prolyv diary are private unless you make them public, so write what you really think. If a session could help someone, switch it to public or attach it to a question in Discussions, and people can see exactly what you did and suggest what to change.
""",
    },
]

TOPICS = [
    ("beginners", True, "Welcome: what are you drinking this week?",
     "This is the place to say hello. Tell us what is in your cup this week and how you brew it, whether that's a gaiwan or a mug.\n\nIf you logged a session, attach it, so other people can see the numbers."),
    ("beginners", False, "New to loose-leaf tea? Ask your first question here",
     "No question is too basic. What do you want to know first: what to buy, what to brew it in, or why it tasted bitter?"),
    ("teaware", False, "Gaiwan or small teapot for a first gongfu set?",
     "What did you start with, and would you choose the same again? Please say what you brew most, because the answer depends on the tea."),
    ("where-to-buy", False, "Where do you buy tea in your country?",
     "Share shops you have actually ordered from: the country, what you bought and how it arrived. If you work for or own a shop, please say so."),
]


def get_prolyv_user(db) -> User:  # noqa: ANN001
    """The site's own byline. It has no sign-in method, so nobody can log in as it, and the handle is reserved."""
    user = db.execute(select(User).where(User.handle == "prolyv")).scalar_one_or_none()
    if user is None:
        user = User(handle="prolyv", display_name="Prolyv", bio="Tea diary, tea base, forum and blog.")
        db.add(user)
        db.flush()
    return user


def seed_guides(db, author: User) -> None:  # noqa: ANN001
    for guide in GUIDES:
        if db.execute(select(Post.id).where(Post.slug == guide["slug"])).first() is not None:
            print(f"  = guide {guide['slug']} exists")
            continue
        tea = db.execute(select(Tea).where(Tea.slug == guide["tea"])).scalar_one_or_none() if guide["tea"] else None
        post = Post(slug=guide["slug"], title=guide["title"], summary=guide["summary"], body_md=guide["body"],
                    body_html=render_markdown(guide["body"]), author=author, tea=tea, published_at=utcnow())
        db.add(post)
        db.flush()
        index_post(db, post)
        print(f"  + guide {guide['slug']}")


def seed_topics(db, author: User) -> None:  # noqa: ANN001
    sections = {s.slug: s for s in db.execute(select(ForumSection)).scalars().all()}
    for section, pinned, title, body in TOPICS:
        if db.execute(select(Topic.id).where(Topic.title == title)).first() is not None:
            print(f"  = topic {title!r} exists")
            continue
        topic = Topic(section=sections[section], user=author, title=title, body=body, pinned=pinned)
        db.add(topic)
        db.flush()
        index_topic(db, topic.id, title, body)
        print(f"  + topic {title!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-teas", action="store_true", help="don't run the tea importer")
    args = parser.parse_args()
    if not args.skip_teas:
        import import_teas

        code = import_teas.main([])
        if code:
            print("The tea importer reported problems; see above. Continuing with the rest.")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    ensure_fts(engine)
    ensure_forum_sections(engine)
    with make_session_factory(engine)() as db:
        author = get_prolyv_user(db)
        seed_guides(db, author)
        seed_topics(db, author)
        db.commit()
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
