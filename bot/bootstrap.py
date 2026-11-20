"""Первичное назначение администраторов.

Если ADMIN_IDS пуст, администратор может быть определён автоматически:

1. по владельцу канала выплат (``WITHDRAW_CHANNEL``) — бот должен быть
   администратором этого канала;
2. запасной вариант — первый пользователь, нажавший /start
   (только при ``BOOTSTRAP_FIRST_ADMIN=1``).

Результат сохраняется в таблицу ``settings`` (ключ ``admin_ids``), поэтому
после перезапуска доступ сохраняется и без переменной окружения.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import Settings, parse_admin_ids
from bot.database import Database

logger = logging.getLogger(__name__)


async def _persist_admins(db: Database, admin_ids: frozenset[int]) -> None:
    raw = ",".join(str(admin_id) for admin_id in sorted(admin_ids))
    await db.set_setting("admin_ids", raw)
    logger.warning("ADMIN_IDS не задан: администраторы сохранены в БД: %s", raw)


async def ensure_admins(bot: Bot, db: Database, settings: Settings) -> frozenset[int]:
    """Гарантирует, что список админов не пуст (если это возможно)."""
    if settings.admin_ids:
        return settings.admin_ids

    stored = parse_admin_ids(await db.get_setting("admin_ids") or "")
    if stored:
        settings.admin_ids = stored
        logger.info("Администраторы загружены из БД: %s", sorted(stored))
        return stored

    if not settings.bootstrap_first_admin or not settings.withdraw_channel:
        return settings.admin_ids

    try:
        members = await bot.get_chat_administrators(settings.withdraw_channel)
    except TelegramAPIError:
        logger.warning(
            "Не удалось получить администраторов канала %s. "
            "Назначьте ADMIN_IDS вручную в .env.",
            settings.withdraw_channel,
            exc_info=True,
        )
        return settings.admin_ids

    owners = {
        int(member.user.id)
        for member in members
        if str(member.status) == "creator" and not member.user.is_bot
    }
    if owners:
        settings.admin_ids = frozenset(owners)
        await _persist_admins(db, settings.admin_ids)
        logger.warning(
            "Администратор определён по владельцу канала %s: %s",
            settings.withdraw_channel,
            sorted(settings.admin_ids),
        )
    return settings.admin_ids


async def promote_first_user(
    db: Database, settings: Settings, user_id: int
) -> bool:
    """Запасной бутстрап: первый /start становится администратором."""
    if settings.admin_ids or not settings.bootstrap_first_admin:
        return False
    settings.admin_ids = frozenset({int(user_id)})
    await _persist_admins(db, settings.admin_ids)
    logger.warning("Первый пользователь %s назначен администратором (бутстрап)", user_id)
    return True


__all__ = ["ensure_admins", "promote_first_user"]
