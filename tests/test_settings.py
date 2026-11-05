import pytest
from sqlalchemy import select

from prolyv.models import AuthSession, Identity, Provider, User
from tests.conftest import csrf_token, google_login, telegram_login


def the_user(db) -> User:
    db.expire_all()
    return db.execute(select(User).where(User.deleted_at.is_(None))).scalar_one()


def test_settings_requires_login(client):
    response = client.get("/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/settings"


def test_update_profile(client, db):
    telegram_login(client, username="olena_tea")
    form = {"csrf_token": csrf_token(client), "display_name": "  Olena   K. ", "handle": "@Olena", "bio": " Shu and oolong. "}
    response = client.post("/settings", data=form, follow_redirects=False)
    assert response.status_code == 303

    user = the_user(db)
    assert user.display_name == "Olena K."
    assert user.handle == "olena"
    assert user.bio == "Shu and oolong."
    assert "Profile saved" in client.get("/settings").text
    assert client.get("/u/olena").status_code == 200
    assert client.get("/u/olena_tea").status_code == 404


def test_handle_must_be_unique(client, db):
    telegram_login(client, tg_id="1", username="first")
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="second")
    form = {"csrf_token": csrf_token(client), "display_name": "Second", "handle": "first", "bio": ""}
    response = client.post("/settings", data=form, follow_redirects=False)
    assert response.status_code == 400
    assert "already taken" in response.text
    db.expire_all()
    assert {u.handle for u in db.execute(select(User)).scalars()} == {"first", "second"}


def test_keeping_your_own_handle_is_fine(client, db):
    telegram_login(client, username="olena_tea")
    form = {"csrf_token": csrf_token(client), "display_name": "Olena", "handle": "olena_tea", "bio": ""}
    assert client.post("/settings", data=form, follow_redirects=False).status_code == 303


@pytest.mark.parametrize("bad", ["ab", "Has Space", "with-dash", "a" * 25, "admin", "ünïcode", ""])
def test_invalid_handles_are_rejected(client, bad):
    telegram_login(client)
    form = {"csrf_token": csrf_token(client), "display_name": "Olena", "handle": bad, "bio": ""}
    assert client.post("/settings", data=form, follow_redirects=False).status_code == 400


def test_bio_is_limited_to_280_characters(client, db):
    telegram_login(client)
    token = csrf_token(client)
    too_long = {"csrf_token": token, "display_name": "Olena", "handle": "olena_tea", "bio": "x" * 281}
    response = client.post("/settings", data=too_long, follow_redirects=False)
    assert response.status_code == 400 and "280" in response.text
    just_right = dict(too_long, bio="y" * 280)
    assert client.post("/settings", data=just_right, follow_redirects=False).status_code == 303
    assert the_user(db).bio == "y" * 280


def test_display_name_is_required(client):
    telegram_login(client)
    form = {"csrf_token": csrf_token(client), "display_name": "   ", "handle": "olena_tea", "bio": ""}
    assert client.post("/settings", data=form, follow_redirects=False).status_code == 400


def test_live_handle_check(client):
    telegram_login(client, tg_id="1", username="taken_one")
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="olena_tea")

    def check(value: str) -> str:
        response = client.get("/settings/handle-check", params={"handle": value}, headers={"HX-Request": "true"})
        assert response.status_code == 200
        return response.text

    assert "@newname is available" in check("newname")
    assert "your current handle" in check("olena_tea")
    assert "already taken" in check("taken_one")
    assert "reserved" in check("admin")
    assert "3–24 characters" in check("ab")
    assert "Pick a handle" in check("")


def test_handle_check_requires_login(client):
    response = client.get("/settings/handle-check", params={"handle": "x"}, headers={"HX-Request": "true"}, follow_redirects=False)
    assert response.status_code == 401


def test_csrf_token_is_required_and_bound_to_the_session(client, db):
    telegram_login(client, tg_id="1", username="first")
    first_token = csrf_token(client)
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="second")

    form = {"display_name": "Hacked", "handle": "second", "bio": ""}
    assert client.post("/settings", data=form, follow_redirects=False).status_code == 403
    assert client.post("/settings", data=dict(form, csrf_token="nope"), follow_redirects=False).status_code == 403
    assert client.post("/settings", data=dict(form, csrf_token=first_token), follow_redirects=False).status_code == 403
    db.expire_all()
    assert db.execute(select(User).where(User.handle == "second")).scalar_one().display_name != "Hacked"
    assert client.post("/settings", data=dict(form, csrf_token=csrf_token(client)), follow_redirects=False).status_code == 303


def test_unlinking_the_last_provider_is_refused(client, db):
    telegram_login(client)
    response = client.post("/settings/unlink", data={"csrf_token": csrf_token(client), "provider": "telegram"}, follow_redirects=False)
    assert response.status_code == 400
    assert "at least one way to sign in" in response.text
    assert len(the_user(db).identities) == 1


def test_unlinking_one_of_two_providers_works(client, db, google):
    telegram_login(client, username="olena_tea")
    google_login(client, google, sub="g-1", email="olena@example.com")
    response = client.post("/settings/unlink", data={"csrf_token": csrf_token(client), "provider": "google"}, follow_redirects=False)
    assert response.status_code == 303
    assert [i.provider for i in the_user(db).identities] == [Provider.telegram]
    page = client.get("/settings").text
    assert "Google unlinked" in page and "Link Google" in page


def test_unknown_provider_is_a_bad_request(client):
    telegram_login(client)
    response = client.post("/settings/unlink", data={"csrf_token": csrf_token(client), "provider": "facebook"}, follow_redirects=False)
    assert response.status_code == 400


def test_avatar_comes_from_the_provider(client, db):
    photo = "https://t.me/i/userpic/320/olena.jpg"
    telegram_login(client, photo_url=photo)
    assert the_user(db).avatar_url == photo
    token = csrf_token(client)

    assert client.post("/settings/avatar", data={"csrf_token": token, "provider": "none"}, follow_redirects=False).status_code == 303
    assert the_user(db).avatar_url is None
    assert "avatar-initials" in client.get("/settings").text

    assert client.post("/settings/avatar", data={"csrf_token": token, "provider": "telegram"}, follow_redirects=False).status_code == 303
    assert the_user(db).avatar_url == photo
    assert f'src="{photo}"' in client.get("/settings").text


def test_avatar_without_provider_photo_is_reported(client, db):
    telegram_login(client, photo_url=None)
    assert the_user(db).avatar_url is None
    client.post("/settings/avatar", data={"csrf_token": csrf_token(client), "provider": "telegram"}, follow_redirects=False)
    assert "has no photo" in client.get("/settings").text


def test_soft_delete_purges_sessions_frees_handle_and_hides_profile(client, db):
    telegram_login(client, tg_id="42", username="olena_tea")
    client.get("/settings")
    before = db.execute(select(User)).scalar_one()
    user_id = before.id

    response = client.post("/settings/delete", data={"csrf_token": csrf_token(client), "confirm": "@Olena_Tea"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert any(h.startswith('prolyv_session=""') for h in response.headers.get_list("set-cookie"))

    db.expire_all()
    assert db.execute(select(AuthSession)).first() is None
    assert db.execute(select(Identity)).first() is None
    deleted = db.get(User, user_id)
    assert deleted.deleted_at is not None
    assert deleted.handle.startswith("deleted_")
    assert deleted.display_name == "Deleted account" and deleted.bio == "" and deleted.avatar_url is None

    assert "account was deleted" in client.get("/").text
    assert client.get("/settings", follow_redirects=False).status_code == 303
    assert client.get("/u/olena_tea").status_code == 404

    telegram_login(client, tg_id="42", username="olena_tea")  # same Telegram user comes back: a fresh account
    fresh = the_user(db)
    assert fresh.id != user_id and fresh.handle == "olena_tea"


def test_delete_requires_typing_the_handle(client, db):
    telegram_login(client, username="olena_tea")
    response = client.post("/settings/delete", data={"csrf_token": csrf_token(client), "confirm": "nope"}, follow_redirects=False)
    assert response.status_code == 400
    assert "Type your handle" in response.text
    assert the_user(db).deleted_at is None


def test_public_profile(client, db):
    telegram_login(client, username="olena_tea", first_name="Olena", last_name="K.")
    form = {"csrf_token": csrf_token(client), "display_name": "Olena K.", "handle": "olena_tea", "bio": "Mostly shu and aged oolong."}
    client.post("/settings", data=form, follow_redirects=False)
    user = the_user(db)

    page = client.get("/u/olena_tea").text
    assert "Olena K." in page and "@olena_tea" in page and "Mostly shu and aged oolong." in page
    assert f"since {user.created_at.strftime('%B %Y')}" in page
    assert "Sessions" in page and "No public sessions yet" in page
    assert "Edit profile" in page

    client.cookies.clear()
    anonymous = client.get("/u/OLENA_TEA").text  # handles are case-insensitive in URLs
    assert "Edit profile" not in anonymous and "Follow" in anonymous
    assert client.get("/u/nobody_here").status_code == 404
