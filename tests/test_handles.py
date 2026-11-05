import pytest

from prolyv.handles import RESERVED, generate_handle, is_valid_handle, slugify


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Olena K.", "olena_k"),
        ("  Olena   Kovalenko ", "olena_kovalenko"),
        ("olena_tea", "olena_tea"),
        ("José Ñandú", "jose_nandu"),
        ("Олена Коваленко", "olena_kovalenko"),
        ("Карина Зуєва", "karyna_zuieva"),
        ("Tea!!! Lover???", "tea_lover"),
        ("日本茶", ""),
        ("", ""),
    ],
)
def test_slugify(text, expected):
    assert slugify(text) == expected


@pytest.mark.parametrize("handle", ["abc", "olena_tea", "a1_", "x" * 24, "tea2026"])
def test_valid_handles(handle):
    assert is_valid_handle(handle)


@pytest.mark.parametrize("handle", ["ab", "x" * 25, "Olena", "with-dash", "with space", "ünïcode", "admin", "settings", "u"])
def test_invalid_handles(handle):
    assert not is_valid_handle(handle)


def test_generate_handle_uses_the_name_when_free():
    assert generate_handle("Olena K.", lambda h: False) == "olena_k"


def test_generate_handle_adds_numeric_suffix_on_collision():
    taken = {"olena_k", "olena_k2", "olena_k3"}
    assert generate_handle("Olena K.", taken.__contains__) == "olena_k4"


def test_generate_handle_pads_short_names():
    assert generate_handle("Al", lambda h: False) == "al_tea"
    assert generate_handle("", lambda h: False) == "tea_friend"
    assert generate_handle("日本茶", lambda h: False) == "tea_friend"
    assert generate_handle("", {"tea_friend"}.__contains__) == "tea_friend2"


def test_generate_handle_truncates_long_names_and_keeps_suffix_in_bounds():
    long_name = "a" * 40
    assert generate_handle(long_name, lambda h: False) == "a" * 24
    taken = {"a" * 24}
    result = generate_handle(long_name, taken.__contains__)
    assert result == "a" * 23 + "2"
    assert len(result) == 24


def test_generate_handle_avoids_reserved_words():
    assert "admin" in RESERVED
    assert generate_handle("admin", lambda h: False) == "admin2"
    assert generate_handle("Settings", lambda h: False) == "settings2"
