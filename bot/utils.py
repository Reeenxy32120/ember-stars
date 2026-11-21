"""Сервисный слой: тексты из БД, настройки, проверка заданий, награды."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.config import load_settings
from bot.database import Database
from bot.defaults import DEFAULT_SETTINGS, DEFAULT_TEXTS, STAR_UNIT_SETTINGS, TASK_TYPE_LABELS
from bot.utils import (
    STARS_SCALE,
    escape,
    format_units,
    normalize_chat_ref,
    render,
    split_sponsor_refs,
    sponsor_display,
    sponsor_url,
)

logger = logging.getLogger(__name__)

SUBSCRIBED_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}


# ------------------------------------------------------------------- TGRASS
class TgrassClient:
    """Клиент для работы с API Tgrass."""

    def __init__(self, api_key: str, base_url: str = "https://tgrass.space") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Auth": self.api_key,
        }

    async def get_offers(
        self,
        user_id: int,
        lang: str,
        is_premium: bool,
        username: str | None = None,
        limit: int = 50,  # <-- ДОБАВЛЕН ПАРАМЕТР LIMIT
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tg_user_id": int(user_id),
            "lang": (lang or "ru")[:8],
            "is_premium": bool(is_premium),
            "limit": int(limit),  # <-- ПЕРЕДАЕМ LIMIT
        }
        if username:
            payload["tg_login"] = username.lstrip("@")

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:  # <-- УВЕЛИЧЕН ТАЙМАУТ
                response = await client.post(
                    f"{self.base_url}/offers",
                    json=payload,
                    headers=self.headers,
                )
                response.raise_for_status()
                data = response.json()
                logger.info("Tgrass API /offers ответ: %s", data)
                return data
        except httpx.HTTPError as exc:
            logger.error("Tgrass API /offers ошибка: %s", exc)
            raise

    async def check_offer(self, user_id: int, offer_id: int) -> dict[str, Any]:
        payload = {
            "tg_user_id": int(user_id),
            "offer_id": int(offer_id),
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.base_url}/check",
                    json=payload,
                    headers=self.headers,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            logger.error("Tgrass API /check ошибка: %s", exc)
            raise

# ------------------------------------------------------------------- BOTOHUB
class BotohubClient:
    """Клиент для работы с API BotoHub."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.base_url = "https://botohub.me"
        self.headers = {
            "Auth": self.token,
            "Content-Type": "application/json",
        }

    async def get_tasks(self, chat_id: int, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        payload = {
            "chat_id": int(chat_id),
            "limit": int(limit),   # <-- ДОБАВЛЕН LIMIT
            "offset": int(offset), # <-- ДОБАВЛЕН OFFSET
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:  # <-- УВЕЛИЧЕН ТАЙМАУТ
                response = await client.post(
                    f"{self.base_url}/get-tasks-extended",
                    json=payload,
                    headers=self.headers,
                )
                response.raise_for_status()
                data = response.json()
                logger.info("BotoHub API /get-tasks-extended ответ: %s", data)
                return data
        except httpx.HTTPError as exc:
            logger.error("BotoHub API ошибка: %s", exc)
            raise

# ------------------------------------------------------------------- FLYER
class FlyerClient:
    """Клиент для работы с API Flyer."""

    def __init__(self, api_key: str, base_url: str = "https://api.flyerservice.io") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def get_tasks(self, user_id: int, language_code: str = "ru", limit: int = 50) -> dict[str, Any]:
        payload = {
            "key": self.api_key,
            "user_id": int(user_id),
            "language_code": language_code,
            "limit": limit,  # <-- УВЕЛИЧЕН ЛИМИТ (было 10)
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:  # <-- УВЕЛИЧЕН ТАЙМАУТ
                response = await client.post(
                    f"{self.base_url}/get_tasks",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                logger.info("Flyer API /get_tasks ответ: %s", data)
                return data
        except httpx.HTTPError as exc:
            logger.error("Flyer API /get_tasks ошибка: %s", exc)
            raise

    async def check_task(self, signature: str) -> dict[str, Any]:
        payload = {
            "key": self.api_key,
            "signature": signature,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.base_url}/check_task",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            logger.error("Flyer API /check_task ошибка: %s", exc)
            raise


_settings = load_settings()
tgrass_client = TgrassClient(
    api_key=_settings.tgrass_api_key,
    base_url=_settings.tgrass_api_url,
)
botohub_client = BotohubClient(_settings.botohub_api_token)
flyer_client = FlyerClient(_settings.flyer_api_key)


# --------------------------------------------------------------------- тексты
async def get_text(db: Database, key: str) -> str:
    """Текст раздела: значение из настроек админки или дефолт."""
    value = await db.get_setting(f"text:{key}")
    if value is None:
        return DEFAULT_TEXTS.get(key, "")
    return value


async def get_texts(db: Database) -> dict[str, str]:
    """Все редактируемые тексты (дефолты + переопределения)."""
    stored = await db.get_all_settings()
    return {
        key: stored.get(f"text:{key}", default)
        for key, default in DEFAULT_TEXTS.items()
    }


async def set_text(db: Database, key: str, value: str) -> None:
    await db.set_setting(f"text:{key}", value)


async def reset_text(db: Database, key: str) -> None:
    await db.set_setting(f"text:{key}", DEFAULT_TEXTS.get(key, ""))


# ------------------------------------------------------------------- настройки
def default_units(key: str) -> int:
    return int(DEFAULT_SETTINGS.get(key, "0"))


async def get_units_setting(db: Database, key: str) -> int:
    """Настройка, хранящаяся в единицах Stars (1 ⭐ = 10 000)."""
    return await db.get_int_setting(key, default_units(key))


async def get_min_withdraw_units(db: Database) -> int:
    return await get_units_setting(db, "min_withdraw_units")


async def get_ref_settings(db: Database) -> dict[str, int]:
    return {
        "bonus_units": await get_units_setting(db, "ref_bonus_units"),
        "required_tasks": await db.get_int_setting(
            "ref_tasks_required", int(DEFAULT_SETTINGS["ref_tasks_required"])
        ),
        "percent": await db.get_int_setting(
            "ref_percent", int(DEFAULT_SETTINGS["ref_percent"])
        ),
    }


async def get_prices(db: Database) -> dict[str, int]:
    return {
        "task": await get_units_setting(db, "price_task_units"),
        "broadcast": await get_units_setting(db, "price_broadcast_units"),
        "view": await get_units_setting(db, "price_view_units"),
        "show": await get_units_setting(db, "price_show_units"),
    }


async def get_settings_view(db: Database) -> dict[str, str]:
    """Все настройки с подставленными дефолтами."""
    stored = await db.get_all_settings()
    return {key: stored.get(key, default) for key, default in DEFAULT_SETTINGS.items()}


def setting_display(key: str, raw: str | int) -> str:
    """Человекочитаемое значение настройки для админки."""
    if key == "sponsor_channels":
        refs = split_sponsor_refs(str(raw or ""))
        return ", ".join(sponsor_display(ref) for ref in refs) or "—"
    if key in STAR_UNIT_SETTINGS:
        try:
            return f"{format_units(int(raw))} ⭐"
        except (TypeError, ValueError):
            return str(raw)
    return str(raw)


def setting_from_input(key: str, raw: str) -> str:
    """Проверяет ввод админа и возвращает значение для записи в БД."""
    from bot.utils import StarsParseError, parse_stars

    value = (raw or "").strip()
    if not value:
        raise ValueError("Пустое значение")
    if key == "sponsor_channels":
        if value.lower() in {"-", "—", "none", "off", "0", "нет"}:
            return ""
        refs = split_sponsor_refs(value)
        if not refs:
            raise ValueError(
                "Укажи публичный канал: @username или https://t.me/username"
            )
        return ",".join(refs)
    if key in STAR_UNIT_SETTINGS:
        try:
            units = parse_stars(value)
        except StarsParseError as exc:
            raise ValueError(str(exc)) from exc
        return str(units)
    if not re.fullmatch(r"\d+", value):
        raise ValueError("Нужно целое положительное число")
    number = int(value)
    if number <= 0:
        raise ValueError("Значение должно быть больше нуля")
    if key == "ref_percent" and number > 100:
        raise ValueError("Процент не может быть больше 100")
    return str(number)


# ------------------------------------------------------------------- спонсоры
async def get_sponsor_channels(db: Database) -> list[str]:
    """Список каналов обязательной подписки (@username)."""
    raw = await db.get_setting("sponsor_channels", "")
    return split_sponsor_refs(raw or "")


async def evaluate_sponsors(
    bot: Bot, db: Database, user_id: int
) -> tuple[list[str], list[str]]:
    """Возвращает (каналы без подписки, каналы без возможности проверки)."""
    sponsors = await get_sponsor_channels(db)
    missing: list[str] = []
    unverifiable: list[str] = []
    for ref in sponsors:
        try:
            member = await bot.get_chat_member(chat_id=ref, user_id=user_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            unverifiable.append(ref)
            continue
        try:
            status = ChatMemberStatus(str(member.status))
        except ValueError:
            unverifiable.append(ref)
            continue
        if status in SUBSCRIBED_STATUSES:
            continue
        if status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False):
            continue
        missing.append(ref)
    return missing, unverifiable


async def get_sponsor_gate_text(db: Database) -> str:
    """Готовый текст экрана обязательной подписки."""
    template = await get_text(db, "sponsor_gate")
    sponsors = await get_sponsor_channels(db)
    links = "\n".join(
        f'• <a href="{escape(sponsor_url(ref))}">{escape(sponsor_display(ref))}</a>'
        for ref in sponsors
    )
    return render(template, sponsors=links)


# ------------------------------------------------------------------- задания
async def check_subscription(
    bot: Bot, chat_id: str | int | None, user_id: int
) -> bool:
    """Проверяет подписку через getChatMember. Ошибки API → False."""
    chat_ref = normalize_chat_ref(chat_id)
    if not chat_ref:
        return False
    try:
        member = await bot.get_chat_member(chat_id=chat_ref, user_id=user_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    try:
        status = ChatMemberStatus(str(member.status))
    except ValueError:
        return False
    if status in SUBSCRIBED_STATUSES:
        return True
    if status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(member, "is_member", False))
    return False


async def verify_task(bot: Bot, user_id: int, task: dict[str, Any]) -> bool:
    """Проверяет условия задания."""
    task_type = str(task.get("type") or "channel")
    chat_id = task.get("chat_id")
    if task_type == "channel":
        return await check_subscription(bot, chat_id or task.get("url"), user_id)
    if task_type == "bot" and chat_id:
        return await check_subscription(bot, chat_id, user_id)
    return True


def task_type_label(task: dict[str, Any]) -> str:
    return TASK_TYPE_LABELS.get(str(task.get("type")), str(task.get("type")))


async def claim_task_reward(db: Database, user_id: int, task: dict[str, Any]) -> dict[str, Any]:
    """Начисляет награду и реферальные бонусы. ``completed=False`` — уже было."""
    reward = int(task["reward"])
    completed = await db.complete_task(user_id, int(task["id"]), reward)
    if not completed:
        return {"completed": False, "reward": reward, "payout": None}
    ref = await get_ref_settings(db)
    payout = await db.apply_referral_rewards(
        user_id,
        reward,
        required_tasks=ref["required_tasks"],
        bonus=ref["bonus_units"],
        percent=ref["percent"],
    )
    return {"completed": True, "reward": reward, "payout": payout}


def units_to_stars(value: int) -> float:
    return int(value) / STARS_SCALE