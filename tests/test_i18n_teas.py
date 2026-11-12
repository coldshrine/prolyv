"""The tea base in Russian: lists, types, origins, A–Z, the tea page and the admin form."""

from __future__ import annotations

import pytest

from tests.test_teas import admin_client, admin_login, make_tea


@pytest.fixture
def russian(client):
    client.cookies.set("lang", "ru")
    return client


def test_lists_in_russian(russian, db):
    make_tea(db, "tie-guan-yin", "Tie Guan Yin", name_ru="Те Гуань Инь", popularity=5)
    make_tea(db, "sencha", "Sencha", category="green", country="Japan", region="Shizuoka", popularity=4)
    page = russian.get("/teas").text
    assert "База чаёв · 2 чая" in page and ">Чаи</h1>" in page and "Искать по любому названию" in page
    assert ">Япония (1)</option>" in page and ">Листовой (2)</option>" in page and "Сначала известные" in page
    assert ">Зелёный <span" in page and ">Улун <span" in page and ">Все</a>" in page
    assert "Any origin" not in page and "Most known first" not in page and "Tea base ·" not in page
    assert "Чаи по запросу «sencha»" in russian.get("/teas", params={"q": "sencha"}).text
    green = russian.get("/teas/c/green").text
    assert "<h1 class=\"h1\">Зелёные чаи</h1>" in green and "База чаёв · 1 чай<" in green
    for i in range(3):
        make_tea(db, f"anxi-{i}", f"Anxi Oolong {i}")
    origin = russian.get("/teas/o/china").text
    assert "Чаи: Китай" in origin and "с происхождением: Китай." in origin and ">Фуцзянь <span" in origin
    assert "Ничего не найдено" in russian.get("/teas", params={"q": "nothing-like-this"}).text


def test_types_origins_and_a_to_z_in_russian(russian, db):
    for i in range(11):
        make_tea(db, f"anxi-{i}", f"Anxi Oolong {i}")
    make_tea(db, "sencha", "Sencha", category="green", country="Japan", region="Shizuoka")
    types = russian.get("/teas/types").text
    assert ">Виды чая</h1>" in types and "Без окисления" in types and ">Шу пуэр <span" in types and "Unoxidised" not in types
    origins = russian.get("/teas/origins").text
    assert ">По странам и регионам</h1>" in origins and ">Китай</a>" in origins and ">Фуцзянь <span" in origins
    assert ">Другие страны</h2>" in origins and ">Япония <span" in origins and ">Страны и регионы</a>" in origins
    a_z = russian.get("/teas/a-z").text
    assert ">Все чаи от A до Z</h1>" in a_z and "12 чаёв" in a_z and "Чаи на букву A" in a_z


def test_tea_page_defaults_to_the_russian_card_text(russian, db):
    make_tea(db, "dhp", "Da Hong Pao", name_ru="Да Хун Пао", region="Fujian · Wuyi Mountains",
             i18n={"ru": {"description": "Описание чая.\n\nВторой абзац."}})
    page = russian.get("/teas/dhp").text
    assert "Описание чая." in page and "First paragraph" not in page and ">О чае Да Хун Пао</h2>" in page
    assert ">Заваривание</h2>" in page and ">Улун</a>" in page and ">Фуцзянь</a>" in page and "Китай · Фуцзянь · Горы Уишань" in page
    assert '<link rel="canonical" href="http://testserver/teas/dhp?lang=ru">' in page and '"inLanguage": "ru"' in page
    assert 'href="/teas/dhp?lang=en"' in page  # the bare address would show Russian again
    english = russian.get("/teas/dhp", params={"lang": "en"}).text
    assert "First paragraph" in english and "Описание чая." not in english and '"inLanguage": "en"' in english
    assert ">Заваривание</h2>" in english  # the interface stays Russian
    assert '<link rel="canonical" href="http://testserver/teas/dhp">' in english


def test_tea_page_without_a_russian_text(russian, db):
    make_tea(db, "plain", "Plain Tea")
    page = russian.get("/teas/plain").text
    assert "First paragraph" in page and ">Заваривание</h2>" in page and "Фото пока нет" in page and ">Как продаётся</h2>" in page
    assert "15 с" in page and "Brewing" not in page and "How it is sold" not in page


def test_english_visitors_keep_the_english_card(client, db):
    make_tea(db, "dhp", "Da Hong Pao", name_ru="Да Хун Пао", i18n={"ru": {"description": "Описание чая."}})
    page = client.get("/teas/dhp").text
    assert "First paragraph" in page and ">Brewing</h2>" in page and 'href="/teas/dhp"' in page and "15s" in page


def test_admin_tea_pages_in_russian(tmp_path, google, db):
    client = admin_client(tmp_path, google)
    admin_login(client)
    client.cookies.set("lang", "ru")
    listing = client.get("/admin/teas").text
    assert " Новый чай</a>" in listing and "опубликовано" in listing
    form = client.get("/admin/teas/new").text
    assert "Название (по-английски)" in form and "Выберите…" in form and ">Высокий</option>" in form and "Сохранить карточку" in form
