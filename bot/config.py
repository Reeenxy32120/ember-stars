"""Конфигурация приложения: читается из файла .env в корне проекта."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
DEFAULT_DB_PATH = BASE_DIR / "data" / "stars_bot.sqlite3"

load_dotenv(ENV_FILE)


def parse_admin_ids(raw: str) -> frozenset[int]:
    """Разбирает ADMIN_IDS вида «123, 456;789» в множество id."""
    result: set[int] = set()
    for chunk in str(raw or "").replace(",", " ").replace(";", " ").split():
        try:
            value = int(chunk)
        except ValueError:
            continue
        if value > 0:
            result.add(value)
    return frozenset(result)


@dataclass
class Settings:
    """Настройки, которые нужны хендлерам и сервисам."""

    bot_token: str
    admin_ids: frozenset[int] = field(default_factory=frozenset)
    db_path: Path = DEFAULT_DB_PATH
    bot_username: str = ""
    support_contact: str = "@support"
    withdraw_channel: str = ""
    bootstrap_first_admin: bool = False
    sponsor_channels: tuple[str, ...] = ()

    # --- Поля для API сервисов ---
    flyer_api_key: str = ""
    tgrass_api_key: str = ""
    tgrass_api_url: str = "https://tgrass.space"
    botohub_api_token: str = ""
    darkboost_api_key: str = ""  # <--- ДОБАВЛЕНО

    @property
    def is_admin_configured(self) -> bool:
        return bool(self.admin_ids)


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env_file: Path | None = None) -> Settings:
    """Загружает настройки из окружения (и .env, если он есть)."""
    if env_file is not None:
        load_dotenv(env_file, override=False)

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен бота."
        )

    raw_db_path = os.getenv("DB_PATH", "").strip()
    db_path = Path(raw_db_path) if raw_db_path else DEFAULT_DB_PATH
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    bot_username = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    support_contact = os.getenv("SUPPORT_CONTACT", "@support").strip() or "@support"
    withdraw_channel = os.getenv("WITHDRAW_CHANNEL", "").strip()
    sponsor_channels = tuple(
        dict.fromkeys(
            chunk
            for chunk in re.split(r"[,;\s]+", os.getenv("SPONSOR_CHANNELS", ""))
            if chunk
        )
    )

    return Settings(
        bot_token=token,
        admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        db_path=db_path,
        bot_username=bot_username,
        support_contact=support_contact,
        withdraw_channel=withdraw_channel,
        bootstrap_first_admin=_parse_bool(os.getenv("BOOTSTRAP_FIRST_ADMIN")),
        sponsor_channels=sponsor_channels,

        flyer_api_key=os.getenv("FLYER_API_KEY", "").strip(),
        tgrass_api_key=os.getenv("TGRASS_API_KEY", "").strip(),
        tgrass_api_url=os.getenv("TGRASS_API_URL", "https://tgrass.space").strip(),
        botohub_api_token=os.getenv("BOTOHUB_TOKEN", "").strip(),
        darkboost_api_key=os.getenv("DARKBOOST_API_KEY", "").strip(), # <--- ДОБАВЛЕНО
    )