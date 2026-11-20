"""Вывод Stars: FSM-заявка на вывод и уведомление администраторов."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import (
    back_to_main,
    main_menu,
    withdraw_cancel,
    withdrawal_channel_decision,
)
from bot.services import get_min_withdraw_units, get_text
from bot.states import WithdrawStates
from bot.utils import StarsParseError, escape, format_units, parse_stars, render

logger = logging.getLogger(__name__)

router = Router(name="withdraw")


def is_admin(settings: Any, user_id: int) -> bool:
    return int(user_id) in getattr(settings, "admin_ids", frozenset())


@router.callback_query(F.data == "withdraw:start")
async def cb_withdraw_start(
    callback: CallbackQuery,
    db: Any,
    user: dict[str, Any],
    state: FSMContext,
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    fresh = await db.get_user(int(user["id"]))
    if fresh is None:
        await callback.answer("Профиль не найден", show_alert=True)
        return
    balance = int(fresh["balance"])
    min_withdraw = await get_min_withdraw_units(db)
    if balance < min_withdraw:
        await callback.answer(
            f"Недостаточно Stars. Минимум для вывода — "
            f"{format_units(min_withdraw)} ⭐, у тебя {format_units(balance)} ⭐.",
            show_alert=True,
        )
        return

    await state.set_state(WithdrawStates.waiting_amount)
    template = await get_text(db, "withdraw_ask")
    text = render(
        template,
        min_withdraw=format_units(min_withdraw),
        balance=format_units(balance),
    )
    await callback.answer()
    await callback.message.answer(text, reply_markup=withdraw_cancel())


@router.message(WithdrawStates.waiting_amount)
async def withdraw_amount(
    message: Message,
    state: FSMContext,
    db: Any,
    settings: Any,
    user: dict[str, Any],
    bot: Bot,
) -> None:
    fresh = await db.get_user(int(user["id"]))
    if fresh is None:
        await state.clear()
        await message.answer("Профиль не найден. Открой /start.")
        return
    balance = int(fresh["balance"])
    min_withdraw = await get_min_withdraw_units(db)

    try:
        amount = parse_stars(message.text or "")
    except StarsParseError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}\n\nВведи сумму в Stars, например {format_units(min_withdraw)}.")
        return

    if amount < min_withdraw:
        await message.answer(
            f"⚠️ Минимальная сумма вывода — <b>{format_units(min_withdraw)}</b> ⭐.",
            reply_markup=withdraw_cancel(),
        )
        return
    if amount > balance:
        await message.answer(
            f"⚠️ На балансе только <b>{format_units(balance)}</b> ⭐. Введи сумму меньше.",
            reply_markup=withdraw_cancel(),
        )
        return

    withdrawal_id = await db.create_withdrawal(int(user["id"]), amount)
    if withdrawal_id is None:
        await message.answer(
            "⚠️ Не удалось создать заявку: недостаточно Stars. Попробуй меньшую сумму.",
            reply_markup=withdraw_cancel(),
        )
        return

    await state.clear()
    template = await get_text(db, "withdraw_created")
    await message.answer(
        render(template, amount=format_units(amount)),
        reply_markup=main_menu(is_admin(settings, int(user["id"]))),
    )

    username = f"@{fresh['username']}" if fresh.get("username") else "—"
    admin_text = (
        "💸 <b>Новая заявка на вывод</b>\n\n"
        f"🆔 Заявка: <b>#{withdrawal_id}</b>\n"
        f"👤 Пользователь: {escape(str(fresh['full_name']))} ({escape(username)})\n"
        f"🆔 ID: <code>{int(fresh['id'])}</code>\n"
        f"⭐ Сумма: <b>{format_units(amount)}</b> ⭐\n\n"
        "Обработать: раздел «Заявки на вывод» в /admin."
    )
    channel_text = (
        "💸 <b>Новая заявка на вывод</b>\n\n"
        f"🆔 Заявка: <b>#{withdrawal_id}</b>\n"
        f"👤 Пользователь: {escape(str(fresh['full_name']))} ({escape(username)})\n"
        f"🆔 ID: <code>{int(fresh['id'])}</code>\n"
        f"⭐ Сумма: <b>{format_units(amount)}</b> ⭐\n\n"
        "Нажми кнопку ниже после выплаты или отклонения заявки."
    )
    for admin_id in getattr(settings, "admin_ids", frozenset()):
        try:
            await bot.send_message(admin_id, admin_text)
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.debug("Не удалось уведомить админа %s", admin_id)

    channel = getattr(settings, "withdraw_channel", "")
    if channel:
        try:
            await bot.send_message(
                channel,
                channel_text,
                reply_markup=withdrawal_channel_decision(withdrawal_id),
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.exception(
                "Не удалось отправить заявку #%s в канал выплат %s",
                withdrawal_id,
                channel,
            )


@router.callback_query(F.data == "withdraw:cancel")
async def cb_withdraw_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    db: Any,
    settings: Any,
    user: dict[str, Any],
) -> None:
    await state.clear()
    await callback.answer("Заявка отменена")
    if callback.message is None:
        return
    fresh = await db.get_user(int(user["id"])) or user
    try:
        await callback.message.edit_text(
            "❌ Создание заявки отменено.",
            reply_markup=back_to_main(),
        )
    except TelegramBadRequest:
        await safe_answer_main(callback, db, settings, fresh)


async def safe_answer_main(
    callback: CallbackQuery, db: Any, settings: Any, user: dict[str, Any]
) -> None:
    """Запасной путь, если сообщение нельзя отредактировать."""
    if callback.message is None:
        return
    try:
        from bot.handlers.user import main_menu_text

        await callback.message.answer(
            await main_menu_text(db, user),
            reply_markup=main_menu(is_admin(settings, int(user["id"]))),
        )
    except TelegramBadRequest:
        logger.debug("Не удалось отправить главное меню", exc_info=True)


__all__ = ["router", "withdraw_amount"]
