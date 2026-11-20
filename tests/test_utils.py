"""Тесты форматирования Stars, шаблонов и экранирования."""

from __future__ import annotations

import pytest

from bot.utils import (
    StarsParseError,
    build_ref_link,
    build_share_url,
    escape,
    format_dt,
    format_units,
    normalize_chat_ref,
    parse_stars,
    render,
    split_sponsor_refs,
    sponsor_display,
    sponsor_url,
    telegram_stars_amount,
    truncate,
)


@pytest.mark.parametrize(
    ("raw", "units"),
    [
        ("50", 500_000),
        ("0.5", 5_000),
        ("0,5", 5_000),
        ("0.032", 320),
        ("0.0345", 345),
        ("1.23456", 12_346),
        (" 100 ", 1_000_000),
        ("1⭐", 10_000),
    ],
)
def test_parse_stars(raw: str, units: int) -> None:
    assert parse_stars(raw) == units


@pytest.mark.parametrize("raw", ["", "-5", "abc", "0", "1e5"])
def test_parse_stars_rejects_invalid(raw: str) -> None:
    with pytest.raises(StarsParseError):
        parse_stars(raw)


def test_parse_stars_max_limit() -> None:
    with pytest.raises(StarsParseError):
        parse_stars("1000001")


@pytest.mark.parametrize(
    ("units", "text"),
    [
        (0, "0"),
        (5_000, "0.5"),
        (500_000, "50"),
        (345, "0.0345"),
        (32_000, "3.2"),
    ],
)
def test_format_units(units: int, text: str) -> None:
    assert format_units(units) == text


def test_telegram_stars_amount_rounds_up() -> None:
    assert telegram_stars_amount(0) == 1
    assert telegram_stars_amount(1) == 1
    assert telegram_stars_amount(10_000) == 1
    assert telegram_stars_amount(10_001) == 2
    assert telegram_stars_amount(500_000) == 50


def test_render_replaces_known_placeholders_only() -> None:
    template = "Привет, {name}! Баланс: {balance}. {unknown}"
    assert render(template, name="Иван", balance="5") == "Привет, Иван! Баланс: 5. {unknown}"


def test_escape_and_truncate() -> None:
    assert escape("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"
    assert truncate("abcdef", 4).endswith("…")
    assert len(truncate("abcdef", 4)) <= 4


def test_format_dt() -> None:
    assert format_dt("2024-05-01 10:20:30") == "01.05.2024 10:20"
    assert format_dt(None) == "—"
    assert format_dt("мусор") == "мусор"


def test_ref_links() -> None:
    link = build_ref_link("@test_bot", 777)
    assert link == "https://t.me/test_bot?start=ref_777"
    share = build_share_url(link, "текст")
    assert "t.me/share/url" in share
    assert "ref_777" in share


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-1001234567890", "-1001234567890"),
        ("@channel", "@channel"),
        ("https://t.me/channel", "@channel"),
        ("t.me/channel?start=1", "@channel"),
        ("https://example.com/page", "https://example.com/page"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_chat_ref(raw, expected) -> None:
    assert normalize_chat_ref(raw) == expected


def test_split_sponsor_refs() -> None:
    refs = split_sponsor_refs("@eezfo, https://t.me/second t.me/third; -1001234567890")
    assert refs == ["@eezfo", "@second", "@third"]
    assert split_sponsor_refs("") == []
    assert split_sponsor_refs("@eezfo, @eezfo") == ["@eezfo"]


def test_sponsor_url_and_display() -> None:
    assert sponsor_url("@eezfo") == "https://t.me/eezfo"
    assert sponsor_url("https://t.me/eezfo") == "https://t.me/eezfo"
    assert sponsor_display("@eezfo") == "@eezfo"
    assert sponsor_display("https://t.me/eezfo") == "@eezfo"
