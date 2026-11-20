"""Хендлер кнопки «✅ Проверить» на экране обязательной подписки."""

from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from bot.keyboards import main_menu
from bot.services import evaluate_sponsors, get_sponsor_channels
from bot.utils import safe_edit

router = Router(name="subscription")


def is_admin(settings: Any, user_id: int) -> bool:
    return int(user_id) in getattr(settings, "admin_ids", frozenset())


@router.callback_query(F.data == "sub:check")
async def cb_check_subscription(
    callback: CallbackQuery,
    db: Any,
    settings: Any,
    user: dict[str, Any],
    bot: Bot,
) -> None:
    sponsors = await get_sponsor_channels(db)
    if not sponsors:
        await callback.answer("Подписка больше не требуется ✅")
        return

    missing, unverifiable = await evaluate_sponsors(bot, db, int(user["id"]))
    if missing:
        names = ", ".join(missing)
        await callback.answer(
            f"Подписка не найдена: {names}. Подпишись и нажми «Проверить» ещё раз.",
            show_alert=True,
        )
        return
    if unverifiable:
        await callback.answer(
            "Не удалось проверить подписку. Попробуй позже или напиши администратору.",
            show_alert=True,
        )
        return

    await callback.answer("Доступ открыт ✅")
    fresh = await db.get_user(int(user["id"])) or user
    # Импорт внутри функции, чтобы не было цикла между модулями хендлеров.
    from bot.handlers.user import main_menu_text

    text = await main_menu_text(db, fresh)
    keyboard = main_menu(is_admin(settings, int(fresh["id"])))
    if callback.message is not None:
        await safe_edit(callback.message, text, keyboard)
    else:
        await bot.send_message(int(fresh["id"]), text, reply_markup=keyboard)


__all__ = ["router"]
