"""Middleware обязательной подписки на спонсоров.

Пропускает дальше только подписанных пользователей (и администраторов).
Исключения:

- команда ``/start`` — её обрабатывает user.py, чтобы не потерять реферальный
  payload: он сохраняется в БД до показа экрана подписки;
- callback ``sub:check`` — кнопка «Проверить» на экране подписки.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.keyboards import sponsor_gate
from bot.services import evaluate_sponsors, get_sponsor_channels, get_sponsor_gate_text

logger = logging.getLogger(__name__)

GATE_THROTTLE_SECONDS = 1.5
CHECK_CALLBACK = "sub:check"


def _is_start_command(event: Message) -> bool:
    text = (event.text or "").strip()
    if not text.startswith("/"):
        return False
    return text.split()[0].split("@")[0].lower() == "/start"


class SubscriptionMiddleware(BaseMiddleware):
    """Блокирует использование бота без подписки на спонсоров."""

    def __init__(self) -> None:
        self._last_gate_at: dict[int, float] = {}

    def reset_throttle(self) -> None:
        self._last_gate_at.clear()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        db = data.get("db")
        settings = data.get("settings")
        bot = data.get("bot")
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        if db is None or settings is None or bot is None or user is None or user.is_bot:
            return await handler(event, data)
        if int(user.id) in getattr(settings, "admin_ids", frozenset()):
            return await handler(event, data)
        if isinstance(event, Message) and _is_start_command(event):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == CHECK_CALLBACK:
            return await handler(event, data)

        sponsors = await get_sponsor_channels(db)
        if not sponsors:
            return await handler(event, data)

        missing, unverifiable = await evaluate_sponsors(bot, db, int(user.id))
        if not missing:
            if unverifiable:
                logger.warning(
                    "Не удалось проверить подписку на %s — пропускаем пользователя %s",
                    ", ".join(unverifiable),
                    user.id,
                )
            return await handler(event, data)

        await self._show_gate(event, data, db, sponsors, user.id)
        return None

    async def _show_gate(
        self,
        event: TelegramObject,
        data: dict[str, Any],
        db: Any,
        sponsors: list[str],
        user_id: int,
    ) -> None:
        bot = data["bot"]
        now = time.monotonic()
        if isinstance(event, CallbackQuery):
            try:
                await event.answer("Сначала подпишись на спонсоров", show_alert=True)
            except TelegramBadRequest:
                logger.debug("Не удалось ответить на callback подписки", exc_info=True)

        if now - self._last_gate_at.get(int(user_id), 0.0) < GATE_THROTTLE_SECONDS:
            return
        self._last_gate_at[int(user_id)] = now

        text = await get_sponsor_gate_text(db)
        keyboard = sponsor_gate(sponsors)
        try:
            if isinstance(event, CallbackQuery):
                if event.message is not None:
                    await event.message.edit_text(text, reply_markup=keyboard)
                    return
                await bot.send_message(event.from_user.id, text, reply_markup=keyboard)
            elif isinstance(event, Message):
                await bot.send_message(event.chat.id, text, reply_markup=keyboard)
        except TelegramBadRequest:
            logger.debug("Не удалось показать экран подписки", exc_info=True)


__all__ = ["SubscriptionMiddleware"]
