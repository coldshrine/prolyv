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
    # Show more fetches one page and pushes ?page=2; opening that URL, as Back does, shows everything so far
    assert 'hx-push-url="/teas?sort=name&amp;page=2"' in first or 'hx-push-url="/teas?sort=name&page=2"' in first
    more = client.get("/teas", params={"sort": "name", "page": 2}, headers={"hx-request": "true"}).text
    assert "Tea 25" in more and "Tea 00" not in more
    second = client.get("/teas", params={"sort": "name", "page": 2}).text
    assert "Tea 25" in second and "Tea 00" in second and second.count('class="tea-card"') == 30
    assert "Show more" not in second
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


def test_only_the_leading_place_becomes_a_region():
    from prolyv.models import origin_levels

    assert origin_levels("Nantou · Lugu; Taipei · Muzha") == ["Nantou", "Lugu"]
    assert origin_levels("Central Province · Dimbula (Hatton, Talawakelle; 1,100 m)") == ["Central Province", "Dimbula"]
    assert origin_levels("Nantou · Mingjian, Lugu and Zhushan") == ["Nantou", "Mingjian"]
    assert origin_levels("Fuding and Zhenghe") == ["Fuding"]
    assert origin_levels("") == [] and origin_levels(None) == []


def test_regions_are_listed_from_three_teas_and_thin_pages_are_noindex(client, db):
    for n in range(20):
        make_tea(db, f"tea-{n:02}", f"Tea {n:02}", country="China", region=f"Province{n % 15:02}")
    for n in range(3):
        make_tea(db, f"big-{n}", f"Big {n}", country="China", region="Bigregion")
    overview = client.get("/teas/origins").text
    assert "/teas/o/bigregion" in overview and "/teas/o/province00" not in overview
    assert "/teas/o/bigregion" in client.get("/teas/o/china").text
    thin = client.get("/teas/o/province14")
    assert thin.status_code == 200 and 'name="robots" content="noindex"' in thin.text
    assert 'name="robots"' not in client.get("/teas/o/bigregion").text
    sitemap = client.get("/sitemap.xml").text
    assert "/teas/o/bigregion<" in sitemap and "/teas/o/province14<" not in sitemap


def test_explainers_are_articles_not_teas(client, db):
    make_tea(db, "style-aged-teas", "Aged Teas", category="sheng")
    make_tea(db, "real-tea", "Real Tea", category="sheng")
    grid = client.get("/teas").text
    assert "/teas/real-tea" in grid and "/teas/style-aged-teas" not in grid and "1 tea" in grid
    assert "/teas/style-aged-teas" not in client.get("/teas/a-z").text
    types = client.get("/teas/types").text
    assert "/teas/style-aged-teas" in types and "How tea gets its character" in types
    article = client.get("/teas/style-aged-teas").text
    assert "Log this tea" not in article and "Sessions with this tea" not in article
    assert "/teas/style-aged-teas<" in client.get("/sitemap.xml").text


def test_thin_cards_are_noindex_and_out_of_the_sitemap(client, db):
    make_tea(db, "rare-one", "Rare One", confidence="low")
    make_tea(db, "known-one", "Known One", confidence="high")
    make_tea(db, "unrated-one", "Unrated One")
    assert 'content="noindex"' in client.get("/teas/rare-one").text
    assert "thinly documented" in client.get("/teas/rare-one").text
    assert 'content="noindex"' not in client.get("/teas/known-one").text
    sitemap = client.get("/sitemap.xml").text
    assert "/teas/known-one<" in sitemap and "/teas/unrated-one<" in sitemap and "/teas/rare-one<" not in sitemap


def test_sitemap_has_the_blog_the_forum_and_the_static_pages(client, settings):
    sitemap = client.get("/sitemap.xml").text
    for path in ("/blog<", "/discussions<", "/about<", "/rules<", "/privacy<", "/terms<"):
        assert path in sitemap
    assert "/login<" not in sitemap


def test_search_finds_names_written_together_and_traditional_characters(client, db):
    from prolyv.search import rebuild

    make_tea(db, "yiwu", "Yiwu Sheng", category="sheng")
    make_tea(db, "wuyi-dhp", "Wuyi Da Hong Pao", name_zh="大红袍")
    make_tea(db, "tgy", "Tie Guan Yin", name_zh="铁观音")
    rebuild(db)
    db.commit()
    for query, slug in (("puerh", "yiwu"), ("puer", "yiwu"), ("pu-erh", "yiwu"), ("dahongpao", "wuyi-dhp"), ("鐵觀音", "tgy"), ("铁观音", "tgy")):
        assert f"/teas/{slug}" in client.get("/search", params={"q": query}).text, query
        assert f"/teas/{slug}" in client.get("/teas", params={"q": query}).text, query


def test_search_all_tab_shows_a_few_of_each_kind(client, db):
    from sqlalchemy import select as sql_select

    from prolyv.models import ForumSection, Topic, User
    from prolyv.search import rebuild

    for n in range(9):
        make_tea(db, f"tea-{n}", f"Black Tea {n}", category="black")
    user = User(handle="someone", display_name="Someone")
    db.add(user)
    section = db.execute(sql_select(ForumSection)).scalars().first()
    topic = Topic(section=section, user=user, title="About black tea", body="Black tea talk here.")
    db.add(topic)
    db.flush()
    rebuild(db)
    db.commit()
    page = client.get("/search", params={"q": "black"}).text
    assert page.count('class="tea-row"') == 5 and "About black tea" in page and "All teas →" in page
    assert client.get("/search", params={"q": "black", "type": "teas"}).text.count('class="tea-row"') == 9


def test_category_chips_keep_the_filters_and_count_within_them(client, db):
    make_tea(db, "jp-green", "Jp Green", category="green", country="Japan")
    make_tea(db, "cn-green", "Cn Green", category="green")
    make_tea(db, "cn-oolong", "Cn Oolong")
    page = client.get("/teas", params={"origin": "japan"}).text
    assert 'href="/teas/c/green?origin=japan"' in page
    origin = client.get("/teas/o/china").text
    assert 'href="/teas/o/china?category=green"' in origin and 'name="origin"' not in origin
    assert "/teas/cn-green" in client.get("/teas/o/china", params={"category": "green"}).text
    assert "/teas/cn-oolong" not in client.get("/teas/o/china", params={"category": "green"}).text


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
    assert "铁观音" in page and "How it is sold" in page and "Sold by season" in page
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
    with TestClient(dev_app, base_url="http://localhost:8000") as client:
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
    assert "Первый абзац." in ru and "История чая." in ru and "First paragraph" not in ru
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
    assert "/teas/o/china" in origins and "/teas/o/japan" in origins
    az = client.get("/teas/a-z").text
    assert "letter=D" in az and "letter=S" in az
    az = client.get("/teas/a-z", params={"letter": "D"}).text
    assert "大红袍" in az and "/teas/sencha" not in az
    sitemap = client.get("/sitemap.xml").text
    for path in ("/teas/types", "/teas/origins", "/teas/a-z", "/teas/c/oolong"):
        assert f"{settings.base_url}{path}<" in sitemap


def test_various_and_blended_are_not_places_to_browse(client, db):
    for n in range(4):
        make_tea(db, f"blend-{n}", f"Blend {n}", country="Various", region="Blended")
        make_tea(db, f"cn-{n}", f"Cn {n}", country="China", region="Yunnan")
    origins = client.get("/teas/origins").text
    assert "/teas/o/china" in origins and "/teas/o/various" not in origins and "/teas/o/blended" not in origins
    assert client.get("/teas/o/various").status_code == 404 and client.get("/teas/o/blended").status_code == 404
    assert 'value="various"' not in client.get("/teas").text
    sitemap = client.get("/sitemap.xml").text
    assert "/teas/o/various" not in sitemap and "/teas/o/blended" not in sitemap
    assert "/teas/o/various" not in client.get("/teas/blend-0").text


def test_sources_read_as_names_not_raw_addresses():
    from prolyv.web.text import source_label

    assert source_label("https://zh.wikipedia.org/wiki/%E5%A4%A7%E7%BA%A2%E8%A2%8D") == "Wikipedia (zh): 大红袍"
    assert source_label("https://en.wikipedia.org/wiki/Da_Hong_Pao") == "Wikipedia (en): Da Hong Pao"
    assert source_label("https://www.example.com/a/very/long/path") == "example.com"


def test_a_refused_tea_form_keeps_published(tmp_path, google):
    client = admin_client(tmp_path, google)
    admin_login(client)
    form = {"csrf_token": csrf_token(client), "name_en": "Kept Tea", "category": "green", "form": "loose", "origin_country": "Japan",
            "description": "A description long enough to pass the check for this card.", "published": "on", "brew_temp_c": "300"}
    refused = client.post("/admin/teas/new", data=form, follow_redirects=False)
    assert refused.status_code == 400 and 'name="published" checked' in refused.text


def test_a_region_repeating_the_country_or_saying_nothing_is_not_shown(client, db):
    make_tea(db, "same", "Same", country="China", region="China")
    make_tea(db, "none", "None", country="Unknown", region="Unknown")
    make_tea(db, "fine", "Fine", country="China", region="Fujian")
    grid = client.get("/teas").text
    assert "China · China" not in grid and "Unknown" not in grid.split("</select>")[-1] and "China · Fujian" in grid
    assert "China · China" not in client.get("/teas/same").text
    assert client.get("/teas/o/unknown").status_code == 404


def test_short_cards_have_no_history_section_and_one_thin_note(client, db):
    make_tea(db, "short", "Short", confidence="low", history="")
    page = client.get("/teas/short").text
    assert '<h2 class="h2">History</h2>' not in page and page.count("thinly documented") == 1


def test_tea_page_labels_follow_the_cards_language(client, db):
    make_tea(db, "dhp", "Da Hong Pao", name_ru="Да Хун Пао", name_zh="大红袍", i18n={"ru": {"description": "Описание чая."}, "zh": {"description": "茶的介绍。"}})
    russian = client.get("/teas/dhp", params={"lang": "ru"}).text
    assert ">Заваривание</h2>" in russian and ">Как продаётся</h2>" in russian and ">О чае Да Хун Пао</h2>" in russian and ">Происхождение</span>" in russian
    assert ">Brewing</h2>" not in russian and "How it is sold" not in russian
    chinese = client.get("/teas/dhp", params={"lang": "zh"}).text
    assert ">冲泡</h2>" in chinese and ">关于大红袍</h2>" in chinese
    english = client.get("/teas/dhp").text
    assert ">Brewing</h2>" in english and ">About Da Hong Pao</h2>" in english and "Заваривание" not in english
    assert client.get("/teas").text.count("Заваривание") == 0


def test_every_translated_label_exists_in_both_languages():
    from prolyv.web.tea_labels import LABELS

    assert set(LABELS["ru"]) == set(LABELS["zh"])


def test_a_short_card_leads_with_what_the_tea_is(client, db):
    make_tea(db, "apricot", "Apricot Tea", confidence="low", taste_profile="Expect what black teas usually give.",
             description="Black tea scented with apricot.\n\nSold in bags and loose.")
    page = client.get("/teas/apricot").text
    assert '<p class="lead" lang="en">Black tea scented with apricot.</p>' in page and "Expect what black teas" not in page.split("</main>")[0].split('class="lead"')[1].split("</p>")[0]
    assert page.count("Black tea scented with apricot.") >= 1 and "<p>Black tea scented with apricot.</p>" not in page and "Sold in bags and loose." in page
    make_tea(db, "known", "Known Tea", confidence="high", taste_profile="Plum and smoke.")
    assert '<p class="lead" lang="en">Plum and smoke.</p>' in client.get("/teas/known").text
