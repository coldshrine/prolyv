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
    assert "Tea 00" in first and "Tea 25" not in first
    assert "Show more" in first and "24 of 30" in first and "page=2" in first
    assert 'hx-select=".more-list &gt; *"' in first or 'hx-select=".more-list > *"' in first
    second = client.get("/teas", params={"sort": "name", "page": 2}).text
    assert "Tea 25" in second and "Tea 00" not in second
    assert "Show more" not in second and "Previous page" in second
    assert "Tea 25" in client.get("/teas", params={"sort": "name", "page": 99}).text


def test_lists_do_not_query_per_card(app, client, db):
    from sqlalchemy import event

    from prolyv.models import TeaImage

    for n in range(40):
        tea = make_tea(db, f"tea-{n:02}", f"Tea {n:02}", popularity=1)
        db.add(TeaImage(tea_id=tea.id, url=f"https://upload.wikimedia.org/{n}.jpg", thumb_url=f"https://upload.wikimedia.org/{n}-t.jpg",
                        author="x", license="CC0", is_primary=True))
    db.commit()
    seen = []
    event.listen(app.state.engine, "before_cursor_execute", lambda _c, _cu, statement, *rest: seen.append(statement))
    for url in ("/teas", "/teas/c/oolong", "/teas?q=tea", "/teas?page=2"):
        seen.clear()
        page = client.get(url)
        assert page.status_code == 200 and "-t.jpg" in page.text
        assert len(seen) < 12, (url, len(seen))


def test_live_filter_also_refreshes_the_heading_and_count(client, db):
    make_tea(db, "sencha", "Sencha", category="green", country="Japan")
    make_tea(db, "rou-gui", "Rou Gui")
    page = client.get("/teas", params={"q": "sencha"}).text
    assert 'hx-select-oob="#tea-head"' in page and 'id="tea-head"' in page
    head = page.split('id="tea-head"')[1].split("</div>")[0]
    assert "1 tea" in head and "Teas matching" in head and "sencha" in head


def test_origin_levels_cope_with_free_form_regions():
    from prolyv.models import origin_levels

    assert origin_levels("Nantou · Lugu; Taipei · Muzha") == ["Nantou", "Lugu", "Taipei", "Muzha"]
    assert origin_levels("Central Province · Dimbula (Hatton, Talawakelle; 1,100 m)") == ["Central Province", "Dimbula"]
    assert origin_levels("Nantou · Mingjian, Lugu and Zhushan") == ["Nantou", "Mingjian", "Lugu", "Zhushan"]
    assert origin_levels("") == [] and origin_levels(None) == []
    assert origin_levels("Darjeeling · Sandakphu") == ["Darjeeling", "Sandakphu"]


def test_origin_pages_stay_small_and_link_to_every_region(client, db):
    for n in range(20):
        make_tea(db, f"tea-{n:02}", f"Tea {n:02}", country="China", region=f"Province{n:02}")
    overview = client.get("/teas/origins").text
    assert "All 20 regions" in overview and overview.count("/teas/o/province") == 12
    country = client.get("/teas/o/china").text
    assert country.count('href="/teas/o/province') == 20
    assert client.get("/teas/o/province03").status_code == 200


def test_a_to_z_shows_one_letter_at_a_time(client, db):
    make_tea(db, "sencha", "Sencha", category="green", country="Japan")
    make_tea(db, "bai-hao", "Bái Háo")
    make_tea(db, "1000-peaks", "1000 Peaks")
    first = client.get("/teas/a-z").text
    assert "/teas/bai-hao" in first and "/teas/sencha" not in first and "letter=S" in first and "letter=%23" in first
    s_page = client.get("/teas/a-z", params={"letter": "s"}).text
    assert "/teas/sencha" in s_page and "/teas/bai-hao" not in s_page
    assert "/teas/1000-peaks" in client.get("/teas/a-z", params={"letter": "#"}).text
    assert "/teas/bai-hao" in client.get("/teas/a-z", params={"letter": "nope"}).text
    sitemap = client.get("/sitemap.xml").text
    assert "/teas/a-z?letter=S<" in sitemap and "/teas/a-z?letter=%23<" in sitemap


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
    assert '"@type": "Article"' in page and "Photo: Someone · CC BY-SA 4.0" in page
    assert "Rou Gui" in page  # related oolong
    assert "Sessions with this tea" in page
    assert client.get("/teas/TIE-GUAN-YIN").status_code == 200
    assert client.get("/teas/nope").status_code == 404


def test_draft_tea_is_visible_to_admins_only(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    make_tea(db, "mystery", "Mystery tea", status=TeaStatus.pending)
    assert client.get("/teas/mystery").status_code == 404
    telegram_login(client, username="visitor")
    assert client.get("/teas/mystery").status_code == 404
    client.cookies.clear()
    admin_login(client)
    assert "Draft. Only admins" in client.get("/teas/mystery").text


def test_users_cannot_add_teas(client):
    telegram_login(client)
    assert client.get("/teas/new").status_code == 404
    assert client.post("/teas/new", data={"csrf_token": csrf_token(client), "name_en": "X"}).status_code in (404, 405)
    assert client.get("/admin/teas").status_code == 403
    assert client.get("/admin/teas/new").status_code == 403
    page = client.get("/teas").text
    assert "Add a tea" not in page and "/teas/new" not in page


def test_admin_tea_crud(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    token = csrf_token(client)
    form = {"csrf_token": token, "name_en": "Mystery Mountain Oolong", "category": "oolong", "form": "loose",
            "origin_country": "Taiwan", "origin_region": "Nantou", "description": "A rolled oolong from a small garden we found on a trip. " * 2,
            "taste_tags": "floral, creamy", "aliases": "MMO", "brew_grams": "6", "brew_water_ml": "120",
            "brew_temp_c": "95", "brew_steeps": "3", "brew_seconds": "20, 25, 30", "published": "on"}
    response = client.post("/admin/teas/new", data=form, follow_redirects=False)
    assert response.headers["location"] == "/teas/mystery-mountain-oolong"
    tea = db.execute(select(Tea).where(Tea.slug == "mystery-mountain-oolong")).scalar_one()
    assert tea.status == TeaStatus.published and tea.brewing["seconds"] == [20, 25, 30] and tea.taste_tags == ["floral", "creamy"]
    assert [a.alias for a in tea.aliases] == ["MMO"] and "Mystery Mountain" in client.get("/admin/teas").text

    edited = dict(form, name_en="Mystery Mountain Oolong (Nantou)", popularity="4")
    edited.pop("published")
    assert client.post("/admin/teas/mystery-mountain-oolong/edit", data=edited, follow_redirects=False).status_code == 303
    db.expire_all()
    tea = db.execute(select(Tea).where(Tea.slug == "mystery-mountain-oolong")).scalar_one()
    assert tea.status == TeaStatus.pending and tea.locked and tea.popularity == 4
    assert "Mystery Mountain" not in client.get("/teas").text

    assert client.post("/admin/teas/mystery-mountain-oolong/delete", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert client.get("/teas/mystery-mountain-oolong").status_code == 404


def test_admin_tea_form_validation(tmp_path, google):
    client = admin_client(tmp_path, google)
    admin_login(client)
    form = {"csrf_token": csrf_token(client), "name_en": "X", "category": "nope", "form": "loose", "origin_country": "", "description": "short"}
    response = client.post("/admin/teas/new", data=form, follow_redirects=False)
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
    dev_app = build_app(make_settings(tmp_path, dev_login=True, base_url="http://localhost:8000"), google)
    with TestClient(dev_app) as client:
        telegram_login(client, username="olena_tea")
        client.cookies.clear()
        response = client.get("/dev/login/olena_tea", follow_redirects=False)
        assert response.status_code == 303
        assert client.get("/settings").status_code == 200
        assert client.get("/dev/login/nobody", follow_redirects=False).status_code == 404


def test_encyclopedia_fields_on_the_card(client, db):
    make_tea(db, "tie-guan-yin", "Tie Guan Yin", name_zh="铁观音", name_ru="Те Гуань Инь", liquor_hex="#D8C86A", leaf_hex="#3F6B3F",
             history="Documented since the Qing dynasty.\n\nA legend ties it to a farmer.", sources=["https://en.wikipedia.org/wiki/Tieguanyin"],
             confidence="low", i18n={"ru": {"description": "Первый абзац.\n\nВторой абзац.", "history": "История чая.", "taste_profile": "Орхидея."}})
    page = client.get("/teas/tie-guan-yin").text
    assert "铁观音" in page and "Те Гуань Инь" in page and "History" in page and "Documented since" in page
    assert "background:#D8C86A" in page and "background:#3F6B3F" in page and "Sources" in page
    assert "thinly documented" in page and 'hreflang="ru"' in page and "?lang=ru" in page and "中文" not in page
    ru = client.get("/teas/tie-guan-yin", params={"lang": "ru"}).text
    assert "Первый абзац." in ru and "История чая." in ru and "Орхидея." in ru and "First paragraph" not in ru
    assert "First paragraph" in client.get("/teas/tie-guan-yin", params={"lang": "zh"}).text  # no translation: English

    make_tea(db, "plain", "Plain Tea")
    plain = client.get("/teas/plain").text
    assert "lang-switch" not in plain and "No photo yet" in plain and "hreflang" not in plain


def test_search_finds_chinese_russian_and_folded_names(client, db):
    make_tea(db, "tie-guan-yin", "Tie Guan Yin", name_zh="铁观音", name_ru="Те Гуань Инь", name_romanized="Tiě Guānyīn", aliases=["Iron Goddess"])
    make_tea(db, "sencha", "Sencha", category="green", name_zh="煎茶", name_ru="Сенча")
    for query in ("铁观音", "观音", "те гуань", "Гуань", "tieguanyin", "tiě", "iron goddess"):
        names = re.findall(r'class="tea-card-name">([^<]+)<', client.get("/teas", params={"q": query}).text)
        assert names == ["Tie Guan Yin"], query
    assert re.findall(r'class="tea-card-name">([^<]+)<', client.get("/teas", params={"q": "сенча"}).text) == ["Sencha"]
    assert "Nothing found" in client.get("/teas", params={"q": "100%_"}).text


def test_index_pages_and_sitemap(client, db, settings):
    make_tea(db, "da-hong-pao", "Da Hong Pao", region="Fujian · Wuyi Mountains", name_zh="大红袍")
    make_tea(db, "sencha", "Sencha", category="green", country="Japan", region="Shizuoka")
    types = client.get("/teas/types").text
    assert "Oolong" in types and "/teas/c/oolong" in types
    origins = client.get("/teas/origins").text
    assert "/teas/o/china" in origins and "/teas/o/wuyi-mountains" in origins and "/teas/o/japan" in origins
    az = client.get("/teas/a-z").text
    assert "letter=D" in az and "letter=S" in az
    az = client.get("/teas/a-z", params={"letter": "D"}).text
    assert "大红袍" in az and "/teas/sencha" not in az
    sitemap = client.get("/sitemap.xml").text
    for path in ("/teas/types", "/teas/origins", "/teas/a-z", "/teas/o/wuyi-mountains", "/teas/c/oolong"):
        assert f"{settings.base_url}{path}<" in sitemap
