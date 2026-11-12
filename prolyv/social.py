"""Notifications, follows and the public numbers on a profile: small helpers the routes call.

Everything a visitor can see on a profile is computed here from public, non-hidden rows only.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.orm import Session, selectinload

from prolyv.i18n import N_, _, _n, get_lang
from prolyv.models import CATEGORY_LABELS, Follow, Notification, Post, Reply, Tea, TeaSession, Topic, User, Visibility

MAX_FANOUT = 1000
FOLLOW_TEXT = "started following you"


def in_context(context: str, text: str) -> str:
    """_() for a word whose Russian depends on where it stands: "Following" is «Вы подписаны» on a button but «Подписки»
    as the name of a list. The Russian is under "context|text"; without one it is the plain _(text)."""
    key = f"{context}|{text}"
    translated = _(key)
    return _(text) if translated == key else translated


def notify(db: Session, user_id: uuid.UUID, actor: User | None, text: str, url: str) -> None:
    """`text` is English, whatever language the actor uses: the reader may use another, and NOTICES translates it
    when it is shown. Never pass it through _()."""
    if actor is not None and actor.id == user_id:
        return
    db.add(Notification(user_id=user_id, actor_id=actor.id if actor else None, text=text[:300], url=url[:300]))


def notify_followers(db: Session, actor: User, text: str, url: str) -> None:
    follower_ids = db.execute(select(Follow.follower_id).where(Follow.followee_id == actor.id).limit(MAX_FANOUT)).scalars().all()
    for follower_id in follower_ids:
        notify(db, follower_id, actor, text, url)


def notify_first_follow(db: Session, target: User, actor: User) -> None:
    """Only the first follow of a pair tells anyone, so toggling Follow can't flood someone's bell."""
    told = db.execute(
        select(Notification.id).where(Notification.user_id == target.id, Notification.actor_id == actor.id, Notification.text.like(f"%{FOLLOW_TEXT}")).limit(1)
    ).first()
    if told is None:
        notify(db, target.id, actor, f"@{actor.handle} {FOLLOW_TEXT}", f"/u/{actor.handle}")


def is_following(db: Session, follower: User | None, followee_id: uuid.UUID) -> bool:
    if follower is None:
        return False
    return db.execute(select(Follow.id).where(Follow.follower_id == follower.id, Follow.followee_id == followee_id)).first() is not None


def follow_counts(db: Session, user_id: uuid.UUID) -> tuple[int, int]:
    followers = db.scalar(select(func.count()).select_from(Follow).join(User, User.id == Follow.follower_id).where(Follow.followee_id == user_id, User.deleted_at.is_(None))) or 0
    following = db.scalar(select(func.count()).select_from(Follow).join(User, User.id == Follow.followee_id).where(Follow.follower_id == user_id, User.deleted_at.is_(None))) or 0
    return followers, following


def followee_ids(db: Session, user: User) -> list[uuid.UUID]:
    return list(db.execute(select(Follow.followee_id).where(Follow.follower_id == user.id)).scalars().all())


# ---------------------------------------------------------------------------------------------- notifications


_ACTOR_LEAD = re.compile(r"^@[a-z0-9_]+", re.IGNORECASE)
_TARGET = re.compile(r"^/(discussions|sessions|blog|u)/([^/?#]+)")

# Every notification the site writes, as stored (in English) and as shown in the reader's language. A new kind of
# notification adds its shape here; text that matches none is shown as stored. More specific shapes go first.
_WHO = r"(?P<who>@[A-Za-z0-9_]+|Deleted account) "
NOTICES = [(re.compile(f"^{pattern}$", re.DOTALL), template) for pattern, template in (
    (_WHO + FOLLOW_TEXT, N_("{who} started following you")),
    (_WHO + r"replied to “(?P<title>.*)”", N_("{who} replied to “{title}”")),
    (_WHO + r"mentioned you in a comment on the (?P<tea>.*) session", N_("{who} mentioned you in a comment on the {tea} session")),
    (_WHO + r"mentioned you in a comment on “(?P<title>.*)”", N_("{who} mentioned you in a comment on “{title}”")),
    (_WHO + r"mentioned you in “(?P<title>.*)”", N_("{who} mentioned you in “{title}”")),
    (_WHO + r"started a topic: (?P<title>.*)", N_("{who} started a topic: {title}")),
    (_WHO + r"commented on your (?P<tea>.*) session", N_("{who} commented on your {tea} session")),
    (_WHO + r"commented on “(?P<title>.*)”", N_("{who} commented on “{title}”")),
    (_WHO + r"logged (?P<title>.*)", N_("{who} logged {title}")),
    (r"Your reply in “(?P<title>.*)” was marked helpful", N_("Your reply in “{title}” was marked helpful")),
)]


def notice_text(text: str) -> str:
    """A stored notification in the reader's language."""
    if get_lang() == "en":
        return text
    for pattern, template in NOTICES:
        match = pattern.match(text)
        if match:
            values = match.groupdict()
            if values.get("who") == "Deleted account":
                values["who"] = _("Deleted account")
            return _(template, **values)
    return text


def _uuids(values: set[str]) -> set[uuid.UUID]:
    out = set()
    for value in values:
        try:
            out.add(uuid.UUID(value))
        except ValueError:
            pass
    return out


def notification_views(db: Session, viewer: User, rows: list[Notification], admin: bool = False) -> list[dict]:
    """Text and link built from the actor as they are now, so a rename or a deleted account doesn't leave an old handle
    or a 404 behind. A link whose page is gone (or no longer visible to this viewer) comes back as url=None."""
    wanted: dict[str, set[str]] = {"discussions": set(), "sessions": set(), "blog": set(), "u": set()}
    for n in rows:
        match = _TARGET.match(n.url)
        if match and not (match[1] == "u" and n.actor is not None):
            wanted[match[1]].add(match[2])
    alive: dict[str, set[str]] = {key: set() for key in wanted}
    if wanted["discussions"]:
        stmt = select(Topic.id).where(Topic.id.in_(_uuids(wanted["discussions"])))
        alive["discussions"] = {str(i) for i in db.execute(stmt if admin else stmt.where(Topic.hidden.is_(False))).scalars()}
    if wanted["sessions"]:
        stmt = select(TeaSession.id).where(TeaSession.id.in_(_uuids(wanted["sessions"])))
        if not admin:
            stmt = stmt.where(((TeaSession.visibility == Visibility.public) & TeaSession.hidden.is_(False)) | (TeaSession.user_id == viewer.id))
        alive["sessions"] = {str(i) for i in db.execute(stmt).scalars()}
    if wanted["blog"]:
        alive["blog"] = set(db.execute(select(Post.slug).where(Post.slug.in_(wanted["blog"]), Post.published_at.is_not(None))).scalars())
    if wanted["u"]:
        alive["u"] = set(db.execute(select(User.handle).where(User.handle.in_(wanted["u"]), User.deleted_at.is_(None))).scalars())

    views = []
    for n in rows:
        text, url, actor = n.text, n.url, n.actor
        if actor is not None:
            text = _ACTOR_LEAD.sub("Deleted account" if actor.deleted_at else f"@{actor.handle}", text, count=1)
        match = _TARGET.match(url)
        if match and match[1] == "u" and actor is not None:
            url = None if actor.deleted_at else f"/u/{actor.handle}"
        elif match and match[2] not in alive[match[1]]:
            url = None
        views.append({"id": n.id, "text": notice_text(text), "url": url, "actor": actor, "created_at": n.created_at})
    return views


# ---------------------------------------------------------------------------------------------- profile numbers


KIND_GRADIENTS = {
    "shu": "linear-gradient(90deg, #A86BFF, #3A0E8C)",
    "oolong": "linear-gradient(90deg, #FFB347, #FF4FD8)",
    "sheng": "linear-gradient(90deg, #D6F25C, #8FAE12)",
    "white": "linear-gradient(90deg, #F4EEFF, #B9A6F0)",
    "black": "linear-gradient(90deg, #FF7A6B, #C4161C)",
    "green": "linear-gradient(90deg, #5CF2B8, #0E9E73)",
    "yellow": "linear-gradient(90deg, #FFF1A8, #E8A100)",
    "heicha": "linear-gradient(90deg, #C28E5C, #5A3A22)",
    "tisane": "linear-gradient(90deg, #FF9BEA, #C77DFF)",
}
OTHER_GRADIENT = "linear-gradient(90deg, #9EA3CF, #5A5F8F)"

# The only place badge rules live. A badge shows its rule as text, and one that isn't earned isn't drawn.
# Titles and rules are English here; earned_badges() hands them out in the visitor's language.
BADGES = (
    {"title": N_("First steep"), "rule": N_("At least 1 public session"), "number": "sessions", "at_least": 1, "grad": "linear-gradient(135deg, #FF4FD8, #FF8A1F)"},
    {"title": N_("Regular"), "rule": N_("30 or more public sessions"), "number": "sessions", "at_least": 30, "grad": "linear-gradient(135deg, #FFE066, #FF8A1F)"},
    {"title": N_("Helpful"), "rule": N_("5 or more helpful replies"), "number": "helpful", "at_least": 5, "grad": "linear-gradient(135deg, #22E3A6, #22B8FF)"},
    {"title": N_("Explorer"), "rule": N_("10 or more teas on the shelf"), "number": "teas", "at_least": 10, "grad": "linear-gradient(135deg, #D6F25C, #22E3A6)"},
    {"title": N_("Brewed along"), "rule": N_("3 or more public sessions brewed along with a blog post"), "number": "brew_alongs", "at_least": 3, "grad": "linear-gradient(135deg, #C77DFF, #8A3FFC)"},
)


def earned_badges(numbers: dict[str, int]) -> list[dict]:
    return [{**badge, "title": in_context("badge", badge["title"]), "rule": _(badge["rule"])}
            for badge in BADGES if numbers.get(badge["number"], 0) >= badge["at_least"]]


def compact(n: int) -> str:
    """1234 -> 1.2k, the way the canvas writes big stats."""
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}".removesuffix(".0") + "k"


def _whole_percents(counts: list[int]) -> list[int]:
    """Rounded shares that still add up to 100."""
    total = sum(counts)
    exact = [c * 100 / total for c in counts]
    whole = [int(e) for e in exact]
    by_remainder = sorted(range(len(counts)), key=lambda i: exact[i] - whole[i], reverse=True)
    for i in by_remainder[: 100 - sum(whole)]:
        whole[i] += 1
    return whole


def public_diary(db: Session, user_id: uuid.UUID) -> dict:
    """One query over this person's public, non-hidden sessions: the shelf, the tea profile and the numbers. Private
    sessions never enter this function, so nothing here can leak them."""
    rows = db.execute(
        select(TeaSession.id, TeaSession.tea_id, TeaSession.tea_name, TeaSession.rating, TeaSession.taste_tags, TeaSession.post_id, Tea.category, Tea.liquor_hex)
        .outerjoin(Tea, Tea.id == TeaSession.tea_id)
        .where(TeaSession.user_id == user_id, TeaSession.visibility == Visibility.public, TeaSession.hidden.is_(False))
        .order_by(TeaSession.brewed_at.desc(), TeaSession.id)
    ).all()

    shelf: dict[object, dict] = {}
    kinds: Counter = Counter()
    other = 0
    notes: Counter = Counter()
    for row in rows:
        entry = shelf.setdefault(row.tea_id or row.tea_name.strip().lower(), {
            "name": row.tea_name,  # newest first, so this is how they wrote it last
            "kind": row.category.value if row.category else "oolong",
            "hex": row.liquor_hex,
            "url": f"/sessions/{row.id}",
            "count": 0,
            "ratings": [],
        })
        entry["count"] += 1
        if row.rating:
            entry["ratings"].append(row.rating)
        if row.category:
            kinds[row.category] += 1
        else:
            other += 1
        notes.update({tag.strip().lower() for tag in row.taste_tags or [] if isinstance(tag, str) and tag.strip()})
    teas = sorted(shelf.values(), key=lambda t: -t["count"])  # stable: equal counts stay newest first
    for tea in teas:
        tea["rating"] = round(sum(tea["ratings"]) / len(tea["ratings"]), 1) if tea["ratings"] else None

    share = []
    if kinds:  # a tea typed in by hand has no type, so "Other" alone would say nothing
        ranked = kinds.most_common()
        counts = [count for _kind, count in ranked] + ([other] if other else [])
        percents = _whole_percents(counts)
        share = [{"label": _(CATEGORY_LABELS[kind]), "count": count, "percent": percents[i], "grad": KIND_GRADIENTS.get(kind.value, OTHER_GRADIENT)}
                 for i, (kind, count) in enumerate(ranked)]
        if other:
            share.append({"label": _("Other"), "count": other, "percent": percents[-1], "grad": OTHER_GRADIENT})

    return {
        "sessions": len(rows),
        "teas": teas,
        "share": share,
        "notes": [{"label": tag.capitalize(), "count": count} for tag, count in notes.most_common(8)],
        "favourite_kind": kinds.most_common(1)[0][0].value if kinds else "oolong",
        "brew_alongs": sum(1 for row in rows if row.post_id),
    }


def _shown_replies(user_id: uuid.UUID):
    return (select(Reply).join(Topic, Topic.id == Reply.topic_id)
            .where(Reply.user_id == user_id, Reply.hidden.is_(False), Topic.hidden.is_(False)))


_HELPFUL = (Reply.helpful_count > 0) | Reply.marked_helpful_by_author.is_(True)


def helpful_reply_total(db: Session, user_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(_shown_replies(user_id).where(_HELPFUL).subquery())) or 0


def most_helpful_reply(db: Session, user_id: uuid.UUID) -> Reply | None:
    stmt = (_shown_replies(user_id).where(_HELPFUL).options(selectinload(Reply.topic))
            .order_by(Reply.helpful_count.desc(), Reply.marked_helpful_by_author.desc(), Reply.created_at.desc()).limit(1))
    return db.execute(stmt).scalars().first()


def reply_excerpt(body: str, limit: int = 220) -> str:
    """What the person said themselves, without the paragraphs they quoted."""
    text = next((p.strip() for p in body.split("\n\n") if p.strip() and not p.lstrip().startswith(">")), "")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def activity_page(db: Session, user_id: uuid.UUID, limit: int, offset: int) -> tuple[list[dict], int]:
    """Topics and replies by one person, newest first, as one list. Hidden ones, and replies in hidden topics, stay out."""
    topics = select(Topic.id.label("id"), literal("topic").label("kind"), Topic.created_at.label("at")).where(Topic.user_id == user_id, Topic.hidden.is_(False))
    replies = (select(Reply.id.label("id"), literal("reply").label("kind"), Reply.created_at.label("at")).join(Topic, Topic.id == Reply.topic_id)
               .where(Reply.user_id == user_id, Reply.hidden.is_(False), Topic.hidden.is_(False)))
    merged = union_all(topics, replies).subquery()
    total = db.scalar(select(func.count()).select_from(merged)) or 0
    keys = db.execute(select(merged.c.id, merged.c.kind).order_by(merged.c.at.desc(), merged.c.id).offset(offset).limit(limit)).all()

    topic_ids = [k.id for k in keys if k.kind == "topic"]
    reply_ids = [k.id for k in keys if k.kind == "reply"]
    loaded_topics = {t.id: t for t in db.execute(select(Topic).options(selectinload(Topic.section)).where(Topic.id.in_(topic_ids))).scalars()} if topic_ids else {}
    loaded_replies = {r.id: r for r in db.execute(
        select(Reply).options(selectinload(Reply.topic).selectinload(Topic.section)).where(Reply.id.in_(reply_ids))
    ).scalars()} if reply_ids else {}

    items = []
    for key in keys:
        if key.kind == "topic":
            topic = loaded_topics[key.id]
            meta = _n("{n} reply", "{n} replies", topic.reply_count) + (" · " + _("solved") if topic.solved else "")
            items.append({"kind": "topic", "section": _(topic.section.name), "at": topic.created_at, "title": topic.title,
                          "url": f"/discussions/{topic.id}", "excerpt": "", "meta": meta})
        else:
            reply = loaded_replies[key.id]
            bits = [_("Marked helpful by the author")] if reply.marked_helpful_by_author else []
            if reply.helpful_count:
                bits.append(_("{n} helpful", n=reply.helpful_count))
            items.append({"kind": "reply", "section": _(reply.topic.section.name), "at": reply.created_at, "title": reply.topic.title,
                          "url": f"/discussions/{reply.topic_id}#reply-{reply.id}", "excerpt": reply_excerpt(reply.body, 160), "meta": " · ".join(bits)})
    return items, total
