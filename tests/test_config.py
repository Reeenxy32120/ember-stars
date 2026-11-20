"""Тесты конфигурации: разбор ADMIN_IDS и загрузка .env-настроек."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import Settings, load_settings, parse_admin_ids


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123", {123}),
        ("123,456", {123, 456}),
        ("123 456;789", {123, 456, 789}),
        (" 1 , 2 , abc, -5 ", {1, 2}),
        ("", set()),
    ],
)
def test_parse_admin_ids(raw: str, expected: set[int]) -> None:
    assert parse_admin_ids(raw) == expected


def test_load_settings_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOT_TOKEN", "42:TEST")
    monkeypatch.setenv("ADMIN_IDS", "1,2")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "custom.sqlite3"))
    monkeypatch.setenv("BOT_USERNAME", "@my_bot")
    monkeypatch.setenv("SUPPORT_CONTACT", "@help")
    monkeypatch.setenv("WITHDRAW_CHANNEL", "@payments")
    monkeypatch.setenv("SPONSOR_CHANNELS", "@eezfo, https://t.me/second")
    monkeypatch.setenv("BOOTSTRAP_FIRST_ADMIN", "1")

    settings = load_settings()
    assert isinstance(settings, Settings)
    assert settings.bot_token == "42:TEST"
    assert settings.admin_ids == frozenset({1, 2})
    assert settings.db_path == tmp_path / "custom.sqlite3"
    assert settings.bot_username == "my_bot"
    assert settings.support_contact == "@help"
    assert settings.withdraw_channel == "@payments"
    assert settings.sponsor_channels == ("@eezfo", "https://t.me/second")
    assert settings.bootstrap_first_admin is True


def test_load_settings_defaults(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "42:TEST")
    for name in (
        "ADMIN_IDS",
        "DB_PATH",
        "BOT_USERNAME",
        "SUPPORT_CONTACT",
        "WITHDRAW_CHANNEL",
        "SPONSOR_CHANNELS",
        "BOOTSTRAP_FIRST_ADMIN",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()
    assert settings.admin_ids == frozenset()
    assert settings.db_path.name == "stars_bot.sqlite3"
    assert settings.bot_username == ""
    assert settings.support_contact == "@support"
    assert settings.withdraw_channel == ""
    assert settings.sponsor_channels == ()
    assert settings.bootstrap_first_admin is False


def test_load_settings_requires_token(monkeypatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        load_settings()
