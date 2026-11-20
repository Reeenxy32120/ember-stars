"""Общие хендлеры: /help, /cancel, заглушки кнопок и обработчик ошибок."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

from bot.services import get_min_withdraw_units, get_text
from bot.utils import format_units, render

router = Router(name="common")
fallback_router = Router(name="fallback")
logger = logging.getLogger(__name__)


@router.message(Command("help"))
async def cmd_help(message: Message, db: Any, settings: Any) -> None:
    template = await get_text(db, "help")
    min_withdraw = await get_min_withdraw_units(db)
    await message.answer(
        render(template, min_withdraw=format_units(min_withdraw)),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, db: Any, settings: Any) -> None:
    await state.clear()
    template = await get_text(db, "unknown_command")
    await message.answer(
        "❌ Действие отменено.\n\n" + render(template),
    )


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "task:done")
async def cb_task_done(callback: CallbackQuery) -> None:
    await callback.answer("Ты уже выполнил это задание ✅", show_alert=True)


@router.callback_query(F.data == "cancel:state")
async def cb_cancel_state(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message is not None:
        try:
            await callback.message.edit_text("❌ Действие отменено.", reply_markup=None)
        except TelegramBadRequest:
            logger.debug("Сообщение для отмены уже устарело", exc_info=True)


# ------------------------------------------------------------------- FALLBACK
@fallback_router.message()
async def fallback_message(message: Message, db: Any, settings: Any) -> None:
    if message.from_user is None:
        return
    is_admin = message.from_user.id in getattr(settings, "admin_ids", frozenset())
    from bot.keyboards import main_menu

    if message.text and message.text.startswith("/"):
        template = await get_text(db, "unknown_command")
        await message.answer(render(template), reply_markup=main_menu(is_admin))
        return
    await message.answer(
        "🏠 Открой главное меню и выбери действие:",
        reply_markup=main_menu(is_admin),
    )


@fallback_router.callback_query()
async def fallback_callback(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела. Открой /start", show_alert=True)


async def on_error(event: ErrorEvent) -> None:
    """Глобальный обработчик ошибок: логируем и сообщаем пользователю."""
    logger.error(
        "Ошибка при обработке апдейта %s: %r",
        type(event.update).__name__,
        event.exception,
        exc_info=event.exception,
    )
    update = event.update
    if update.message is not None:
        try:
            await update.message.answer("⚠️ Произошла ошибка. Попробуй ещё раз позже.")
        except TelegramBadRequest:
            logger.debug("Не удалось сообщить об ошибке в чат", exc_info=True)
    elif update.callback_query is not None:
        try:
            await update.callback_query.answer("⚠️ Произошла ошибка", show_alert=True)
        except TelegramBadRequest:
            logger.debug("Не удалось ответить на callback с ошибкой", exc_info=True)


__all__ = ["fallback_router", "on_error", "router"]