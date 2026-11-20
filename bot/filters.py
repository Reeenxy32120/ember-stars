"""Фильтры aiogram."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from bot.config import Settings


class IsAdmin(BaseFilter):
    """Пропускает только пользователей из ADMIN_IDS.

    ``settings`` приходит из ContextMiddleware.
    """

    async def __call__(
        self, event: TelegramObject, settings: Settings | None = None, **kwargs: object
    ) -> bool:
        if settings is None:
            return False
        user = getattr(event, "from_user", None)
        return bool(user is not None and user.id in settings.admin_ids)
