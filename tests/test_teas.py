import re

from sqlalchemy import select
from starlette.testclient import TestClient

from prolyv.models import Tea, TeaAlias, TeaCategory, TeaForm, TeaImage, TeaStatus
from tests.conftest import build_app, csrf_token, make_settings, telegram_login

ADMIN = "karyna"


def make_tea(db, slug, name, category="oolong", country="China", region="Fujian · Anxi", popularity=3, tags=None,
             aliases=(), status=TeaStatus.published, image=None, **fields):
    tea = Tea(slug=slug, name_en=name, category=TeaCategory(category), origin_country=country, origin_region=region,
              form=TeaForm.loose, popularity=popularity, taste_tags=tags or ["floral"], status=status,
              description=fields.pop("description", "First paragraph about the tea.\n\nSecond paragraph with more."),
              vintage_note=fields.pop("vintage_note", "Sold by season, spring and autumn of the current year."),
              brewing=fields.pop("brewing", {"grams": 7, "water_ml": 110, "temp_c": 95, "steeps": 5, "seconds": [15, 10, 15, 20, 30]}),
              **fields)
    tea.aliases = [TeaAlias(alias=a) for a in aliases]
    if image:
        tea.images = [TeaImage(url=image, thumb_url=image, author="Someone", license="CC BY-SA 4.0", is_primary=True)]
    tea.refresh_search_text()
    db.add(tea)
    db.commit()
    return tea


def admin_client(tmp_path, google) -> TestClient:
    app = build_app(make_settings(tmp_path, admin_handles=ADMIN), google)
    return TestClient(app)


def admin_login(client):
    """A distinct Telegram id: the same id would log back into the first account regardless of username."""
    return telegram_login(client, tg_id="900", username=ADMIN, first_name="Karyna")


def test_list_search_and_facets(client, db):
    make_tea(db, "tie-guan-yin", "Tie Guan Yin", popularity=5, aliases=["Iron Goddess of Mercy", "TGY"])
    make_tea(db, "sencha", "Sencha", category="green", country="Japan", region="Shizuoka", popularity=4)
    make_tea(db, "secret", "Secret tea", status=TeaStatus.pending)

    page = client.get("/teas").text
    assert "Tie Guan Yin" in page and "Sencha" in page and "Secret tea" not in page
    assert "2 teas" in page
    assert "/teas/c/oolong" in page and "/teas/c/green" in page

    assert "Tie Guan Yin" in client.get("/teas", params={"q": "goddess"}).text  # alias search
    assert "Tie Guan Yin" in client.get("/teas", params={"q": "anxi"}).text  # region search
    results = client.get("/teas", params={"q": "zzz-nothing"}).text
    assert "Nothing found" in results

    assert "Sencha" in client.get("/teas", params={"origin": "japan"}).text
    assert "Tie Guan Yin" not in client.get("/teas", params={"origin": "japan"}).text
    names = re.findall(r'class="tea-card-name">([^<]+)<', client.get("/teas", params={"sort": "name"}).text)
    assert names == ["Sencha", "Tie Guan Yin"]


def test_category_and_origin_pages(client, db):
    make_tea(db, "da-hong-pao", "Da Hong Pao", region="Fujian · Wuyi Mountains")
    make_tea(db, "longjing", "Longjing", category="green", region="Zhejiang · Hangzhou")

    oolong = client.get("/teas/c/oolong").text
    assert "Da Hong Pao" in oolong and "Longjing" not in oolong and "Oolong teas" in oolong
    wuyi = client.get("/teas/o/wuyi-mountains").text
    assert "Teas from Wuyi Mountains" in wuyi and "Da Hong Pao" in wuyi and "Longjing" not in wuyi
    china = client.get("/teas/o/china").text
    assert "Da Hong Pao" in china and "Longjing" in china
    assert client.get("/teas/c/purple").status_code == 404
    assert client.get("/teas/o/atlantis").status_code == 404


def test_pagination(client, db):
    for n in range(30):
        make_tea(db, f"tea-{n:02}", f"Tea {n:02}", popularity=1)
    first = client.get("/teas", params={"sort": "name"}).text
    assert "Tea 00" in first and "Tea 25" not in first and "Page 1 of 2" in first
    second = client.get("/teas", params={"sort": "name", "page": 2}).text
    assert "Tea 25" in second and "Tea 00" not in second


def test_detail_page(client, db):
    make_tea(db, "tie-guan-yin", "Tie Guan Yin", name_native="铁观音", aliases=["TGY"], wikipedia_url="https://en.wikipedia.org/wiki/Tieguanyin",
             taste_profile="Orchid florals over a creamy body.", image="https://upload.wikimedia.org/x.jpg")
    make_tea(db, "rou-gui", "Rou Gui", region="Fujian · Wuyi Mountains")
    page = client.get("/teas/tie-guan-yin").text
    assert "铁观音" in page and "Sold as · vintages" in page and "Sold by season" in page
    assert "Brewing" in page and "7 g" in page and "95 °C" in page
    assert "Read more on Wikipedia" in page and "Also known as: TGY" in page
    assert "Log this tea" in page and "/login?next=/sessions/new%3Ftea%3Dtie-guan-yin" in page
    assert 'property="og:image" content="https://upload.wikimedia.org/x.jpg"' in page
    assert '"@type": "Product"' in page and "Photo: Someone · CC BY-SA 4.0" in page
    assert "Rou Gui" in page  # related oolong
    assert "Sessions with this tea" in page
    assert client.get("/teas/TIE-GUAN-YIN").status_code == 200
    assert client.get("/teas/nope").status_code == 404


def test_pending_tea_is_private_until_approved(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    make_tea(db, "mystery", "Mystery tea", status=TeaStatus.pending)
    assert client.get("/teas/mystery").status_code == 404
    telegram_login(client, username="visitor")
    assert client.get("/teas/mystery").status_code == 404
    client.cookies.clear()
    admin_login(client)
    assert client.get("/teas/mystery").status_code == 200
    assert "waiting for review" in client.get("/teas/mystery").text


def test_request_review_edit_and_delete_flow(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()

    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    form = {"csrf_token": token, "name_en": "Mystery Mountain Oolong", "category": "oolong", "form": "loose",
            "origin_country": "Taiwan", "origin_region": "Nantou", "description": "A rolled oolong from a small garden we found on a trip. " * 2,
            "taste_tags": "floral, creamy", "aliases": "MMO", "brew_grams": "6", "brew_water_ml": "120",
            "brew_temp_c": "95", "brew_steeps": "3", "brew_seconds": "20, 25, 30"}
    response = client.post("/teas/new", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/teas/mystery-mountain-oolong"
    page = client.get("/teas/mystery-mountain-oolong").text  # the author can see their pending card
    assert "waiting for review" in page and "Mystery Mountain Oolong" in page
    assert "Mystery Mountain" not in client.get("/teas").text
    tea = db.execute(select(Tea).where(Tea.slug == "mystery-mountain-oolong")).scalar_one()
    assert tea.status == TeaStatus.pending and tea.brewing["seconds"] == [20, 25, 30] and tea.taste_tags == ["floral", "creamy"]
    assert [a.alias for a in tea.aliases] == ["MMO"]

    assert client.get("/admin/teas").status_code == 403  # not an admin
    client.cookies.clear()
    admin_login(client)
    admin_token = csrf_token(client)
    queue = client.get("/admin/teas").text
    assert "Mystery Mountain Oolong" in queue and "waiting for review" in queue

    edited = dict(form, csrf_token=admin_token, name_en="Mystery Mountain Oolong (Nantou)", popularity="4")
    assert client.post("/admin/teas/mystery-mountain-oolong/edit", data=edited, follow_redirects=False).status_code == 303
    assert client.post("/admin/teas/mystery-mountain-oolong/approve", data={"csrf_token": admin_token}, follow_redirects=False).status_code == 303
    db.expire_all()
    tea = db.execute(select(Tea).where(Tea.slug == "mystery-mountain-oolong")).scalar_one()
    assert tea.status == TeaStatus.published and tea.locked and tea.popularity == 4
    assert "Mystery Mountain Oolong (Nantou)" in client.get("/teas").text

    assert client.post("/admin/teas/mystery-mountain-oolong/delete", data={"csrf_token": admin_token}, follow_redirects=False).status_code == 303
    assert client.get("/teas/mystery-mountain-oolong").status_code == 404


def test_admin_adds_teas_directly_as_published(tmp_path, google):
    client = admin_client(tmp_path, google)
    admin_login(client)
    form = {"csrf_token": csrf_token(client), "name_en": "Quick Oolong", "category": "oolong", "form": "loose",
            "origin_country": "China", "description": "Short but long enough description of a quick test tea card."}
    client.post("/teas/new", data=form, follow_redirects=False)
    assert "Quick Oolong" in client.get("/teas").text


def test_request_form_validation(client):
    telegram_login(client)
    form = {"csrf_token": csrf_token(client), "name_en": "X", "category": "nope", "form": "loose", "origin_country": "", "description": "short"}
    response = client.post("/teas/new", data=form, follow_redirects=False)
    assert response.status_code == 400
    assert "Pick a category" in response.text and "Where is it from?" in response.text


def test_sitemap_lists_published_teas(client, db, settings):
    make_tea(db, "sencha", "Sencha", category="green", country="Japan")
    make_tea(db, "hidden", "Hidden", status=TeaStatus.pending)
    response = client.get("/sitemap.xml")
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/xml")
    assert f"{settings.base_url}/teas/sencha" in response.text
    assert "/teas/hidden" not in response.text
    assert "/teas/c/green" in response.text


def test_dev_login_only_works_locally(tmp_path, google):
    dev_app = build_app(make_settings(tmp_path, dev_login=True), google)
    with TestClient(dev_app) as client:
        telegram_login(client, username="olena_tea")
        client.cookies.clear()
        response = client.get("/dev/login/olena_tea", follow_redirects=False)
        assert response.status_code == 303
        assert client.get("/settings").status_code == 200
        assert client.get("/dev/login/nobody", follow_redirects=False).status_code == 404

    (tmp_path / "prod").mkdir(exist_ok=True)
    prod_app = build_app(make_settings(tmp_path / "prod", dev_login=True, base_url="https://prolyv.example"), google)
    with TestClient(prod_app, base_url="https://testserver") as client:
        assert client.get("/dev/login/olena_tea", follow_redirects=False).status_code == 404
