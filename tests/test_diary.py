import re
import uuid

from sqlalchemy import select

from prolyv.models import SessionComment, TeaSession, Visibility
from tests.conftest import csrf_token, telegram_login
from tests.test_teas import make_tea


def new_session(client, token, **overrides):
    data = {"csrf_token": token, "tea_name": "Tie Guan Yin", "grams": "7", "water_ml": "110", "temp_c": "95",
            "vessel": "gaiwan", "rinse": "on", "rating": "4", "taste_tags": ["floral", "creamy"],
            "note": "Orchid and butter.\n\nKeep steep six short.", "visibility": "private", "allow_comments": "on",
            "brewed_at": "2026-11-05", "steep_seconds": ["15", "10", "15"], "steep_note": ["rinse first", "", "longer"]}
    data.update(overrides)
    return client.post("/sessions/new", data=data, follow_redirects=False)


def session_id_from(response) -> str:
    match = re.search(r"/sessions/([0-9a-f-]{36})", response.headers["location"])
    assert match, response.headers.get("location")
    return match.group(1)


def test_diary_and_new_session_require_login(client):
    assert client.get("/diary", follow_redirects=False).headers["location"] == "/login?next=/diary"
    assert client.get("/sessions/new", follow_redirects=False).status_code == 303


def test_create_session_with_steeps_and_view_it(client, db):
    telegram_login(client, username="olena_tea")
    response = new_session(client, csrf_token(client))
    assert response.status_code == 303
    sid = session_id_from(response)

    sess = db.get(TeaSession, uuid.UUID(sid))
    assert sess.tea_name == "Tie Guan Yin" and sess.grams == 7 and sess.water_ml == 110 and sess.temp_c == 95
    assert sess.vessel == "gaiwan" and sess.rinse and sess.rating == 4 and sess.taste_tags == ["floral", "creamy"]
    assert [(s.n, s.seconds, s.note) for s in sess.steeps] == [(1, 15, "rinse first"), (2, 10, None), (3, 15, "longer")]
    assert sess.visibility == Visibility.private and sess.brewed_at.date().isoformat() == "2026-11-05"

    page = client.get(f"/sessions/{sid}").text
    assert "Tie Guan Yin" in page and "private · only you can see this" in page
    assert "7 g" in page and "110 ml" in page and "95 °C" in page and "Gaiwan" in page
    assert "15 s" in page and "rinse first" in page and "★★★★☆" in page
    assert "Session saved" in page and "Make public" in page and "Brew it the same way" in page
    assert "Keep steep six short." in page

    diary = client.get("/diary").text
    assert "Tie Guan Yin" in diary and "Last 30 days" in diary and "Cups calendar" in diary and "Shelf" in diary
    assert "1 session logged" in diary


def test_private_sessions_are_hidden_from_others(client, db):
    telegram_login(client, tg_id="1", username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client)))
    client.cookies.clear()
    assert client.get(f"/sessions/{sid}").status_code == 404
    telegram_login(client, tg_id="2", username="mike")
    assert client.get(f"/sessions/{sid}").status_code == 404
    assert client.get(f"/sessions/{sid}/edit").status_code == 404
    assert "Tie Guan Yin" not in client.get("/u/olena_tea").text


def test_public_session_shows_on_profile_and_tea_card(client, db):
    tea = make_tea(db, "tie-guan-yin", "Tie Guan Yin")
    telegram_login(client, tg_id="1", username="olena_tea")
    token = csrf_token(client)
    sid = session_id_from(new_session(client, token, tea_id=str(tea.id), visibility="public"))
    client.cookies.clear()

    page = client.get(f"/sessions/{sid}").text
    assert "public session" in page and "Share" in page and "tea card" in page
    assert 'property="og:title" content="Tie Guan Yin · Olena K.' in page
    assert "Tie Guan Yin" in client.get("/u/olena_tea").text
    assert "1</p><p class=\"stat-label\">public sessions" in client.get("/u/olena_tea").text.replace("\n", "")
    tea_page = client.get("/teas/tie-guan-yin").text
    assert "@olena_tea" in tea_page and "No public sessions yet" not in tea_page


def test_visibility_toggle_edit_and_delete(client, db):
    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    sid = session_id_from(new_session(client, token))
    assert client.post(f"/sessions/{sid}/visibility", data={"csrf_token": token, "visibility": "public"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(TeaSession, uuid.UUID(sid)).is_public

    edit_page = client.get(f"/sessions/{sid}/edit").text
    assert 'value="Tie Guan Yin"' in edit_page and 'value="15"' in edit_page
    response = client.post(f"/sessions/{sid}/edit", data={"csrf_token": token, "tea_name": "Tie Guan Yin (autumn)", "grams": "6.5",
                                                           "rating": "5", "visibility": "public", "brewed_at": "2026-11-04",
                                                           "steep_seconds": ["20", "20"], "steep_note": ["", ""]}, follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    sess = db.get(TeaSession, uuid.UUID(sid))
    assert sess.tea_name == "Tie Guan Yin (autumn)" and sess.grams == 6.5 and sess.rating == 5 and len(sess.steeps) == 2

    assert client.post(f"/sessions/{sid}/delete", data={"csrf_token": token}, follow_redirects=False).headers["location"] == "/diary"
    assert client.get(f"/sessions/{sid}").status_code == 404


def test_only_the_owner_can_edit_or_delete(client):
    telegram_login(client, tg_id="1", username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client), visibility="public"))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    assert client.get(f"/sessions/{sid}/edit").status_code == 403
    assert client.post(f"/sessions/{sid}/delete", data={"csrf_token": token}, follow_redirects=False).status_code == 403
    assert client.get(f"/sessions/{sid}").status_code == 200


def test_validation_errors(client):
    telegram_login(client)
    token = csrf_token(client)
    response = new_session(client, token, tea_name="", grams="900", temp_c="abc", brewed_at="2099-01-01", steep_seconds=["0"])
    assert response.status_code == 400
    text = response.text
    assert "Which tea was it?" in text and "Leaf must be between" in text and "Temperature must be a number" in text
    assert "That date is in the future" in text and "between 1 second and an hour" in text


def test_comments_on_public_sessions(client, db):
    telegram_login(client, tg_id="1", username="olena_tea")
    owner_token = csrf_token(client)
    sid = session_id_from(new_session(client, owner_token, visibility="public"))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    assert client.post(f"/sessions/{sid}/comments", data={"csrf_token": token, "body": "Brewed this too, lovely."}, follow_redirects=False).status_code == 303
    page = client.get(f"/sessions/{sid}").text
    assert "Brewed this too, lovely." in page and "Comments · 1" in page and "@mike" in page
    comment = db.execute(select(SessionComment)).scalar_one()

    client.cookies.clear()
    telegram_login(client, tg_id="3", username="stranger")
    stranger_token = csrf_token(client)
    assert client.post(f"/sessions/{sid}/comments/{comment.id}/delete", data={"csrf_token": stranger_token}, follow_redirects=False).status_code == 403

    client.cookies.clear()
    telegram_login(client, tg_id="1", username="olena_tea")  # the session owner can delete any comment
    assert client.post(f"/sessions/{sid}/comments/{comment.id}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False).status_code == 303
    assert "Brewed this too, lovely." not in client.get(f"/sessions/{sid}").text


def test_comments_respect_allow_comments(client):
    telegram_login(client, tg_id="1", username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client), visibility="public", allow_comments=""))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    client.post(f"/sessions/{sid}/comments", data={"csrf_token": csrf_token(client), "body": "nope"}, follow_redirects=False)
    page = client.get(f"/sessions/{sid}").text
    assert "Comments are off" in page and "nope" not in page


def test_new_session_prefills_from_tea_and_from_session(client, db):
    tea = make_tea(db, "sencha", "Sencha", category="green", country="Japan", region="Shizuoka",
                   brewing={"grams": 5, "water_ml": 150, "temp_c": 75, "steeps": 3, "seconds": [60, 30, 60]})
    telegram_login(client, tg_id="1", username="olena_tea")
    page = client.get("/sessions/new", params={"tea": "sencha"}).text
    assert 'value="Sencha"' in page and 'value="5"' in page and 'value="150"' in page and 'value="75"' in page
    assert 'data-suggested="60,30,60"' in page and f'value="{tea.id}"' in page

    sid = session_id_from(new_session(client, csrf_token(client), visibility="public", steep_seconds=["20", "25"]))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    copy = client.get("/sessions/new", params={"from": sid}).text
    assert 'value="Tie Guan Yin"' in copy and 'data-suggested="20,25"' in copy and 'value="110"' in copy
    assert "rinse first" not in copy  # a copy takes the parameters, not the notes


def test_tea_suggestions(client, db):
    make_tea(db, "tie-guan-yin", "Tie Guan Yin", aliases=["TGY"])
    telegram_login(client)
    assert "Tie Guan Yin" in client.get("/teas/suggest", params={"q": "tgy"}).text
    assert "Not in the base yet" in client.get("/teas/suggest", params={"q": "zzz"}).text
    assert client.get("/teas/suggest", params={"q": "t"}).text.strip() == ""
