"""Middleware, наполняющий данные апдейта контекстом приложения."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from bot.config import Settings
from bot.database import Database

logger = logging.getLogger(__name__)


def _extract_user(event: TelegramObject, data: dict[str, Any]) -> User | None:
    user = data.get("event_from_user")
    if user is None:
        user = getattr(event, "from_user", None)
    return user


class ContextMiddleware(BaseMiddleware):
    """Добавляет в data: ``db``, ``settings`` и запись пользователя ``user``."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["db"] = self.db
        data["settings"] = self.settings

        user = _extract_user(event, data)
        if isinstance(user, User) and not user.is_bot:
            try:
                data["user"] = await self.db.get_or_create_user(
                    user.id, user.username, user.full_name
                )
            except Exception:  # pragma: no cover - не роняем апдейт из-за статистики
                logger.exception("Не удалось обновить пользователя %s", user.id)

        return await handler(event, data)


__all__ = ["ContextMiddleware"]
