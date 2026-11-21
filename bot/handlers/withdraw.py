"""Логика вывода звёзд в виде подарков."""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from bot.defaults import WITHDRAW_GIFTS
from bot.keyboards import (
    back_to_main,
    withdraw_gifts_menu,
    withdraw_history_menu,
    withdrawal_channel_decision,
)
from bot.services import get_min_withdraw_units
from bot.utils import STARS_SCALE, escape, format_dt, format_units, safe_edit

logger = logging.getLogger(__name__)

router = Router(name="withdraw")

FRIENDS_REQUIRED = 3

async def _build_withdraw_text(db: Any, user: dict[str, Any]) -> str:
    balance_units = int(user["balance"])
    balance_stars = format_units(balance_units)

    # Считаем друзей
    ref_stats = await db.get_referral_stats(int(user["id"]))
    friends_count = ref_stats["invited"]

    min_units = await get_min_withdraw_units(db)
    min_stars = format_units(min_units)

    # Прогресс-бар друзей
    progress = min(10, int((friends_count / FRIENDS_REQUIRED) * 10))
    bar = "🟩" * progress + "⬜" * (10 - progress)
    percent = min(100, int((friends_count / FRIENDS_REQUIRED) * 100))

    mode_text = ""
    if friends_count < FRIENDS_REQUIRED:
        mode_text = (
            f"🚨 Режим: <b>Первый вывод</b> — проверка администратором 🔒\n"
            f"Для вывода нужно <b>{FRIENDS_REQUIRED}</b> друзей, у тебя {friends_count}\n"
            f"[{bar}] {percent}%"
        )
    else:
        mode_text = (
            f"✅ Режим: <b>Обычный вывод</b> — проверка администратором\n"
            f"Условие по друзьям выполнено ({friends_count}/{FRIENDS_REQUIRED})"
        )

    return (
        "🎁 <b>Вывод звёзд подарком</b>\n\n"
        f"🐊 Баланс: <b>{balance_stars}</b> ⭐ | Минимум: <b>{min_stars}</b> ⭐\n"
        f"{mode_text}\n\n"
        "🎁 Выбери подарок — он придёт прямо в твой профиль.\n"
        "<i>Подарок можно оставить или обменять на звёзды. 🔒 — пока не хватает</i>"
    )


@router.callback_query(F.data == "withdraw:start")
async def cb_withdraw_start(callback: CallbackQuery, db: Any, user: dict[str, Any]) -> None:
    await callback.answer()
    if callback.message is None:
        return

    fresh = await db.get_user(int(user["id"])) or user
    text = await _build_withdraw_text(db, fresh)
    ref_stats = await db.get_referral_stats(int(user["id"]))

    await safe_edit(
        callback.message,
        text,
        withdraw_gifts_menu(int(fresh["balance"]), ref_stats["invited"])
    )


@router.callback_query(F.data.startswith("withdraw:gift:"))
async def cb_withdraw_gift(callback: CallbackQuery, db: Any, user: dict[str, Any], bot: Bot, settings: Any) -> None:
    gift_id = (callback.data or "").split(":")[2]
    gift = next((g for g in WITHDRAW_GIFTS if g["id"] == gift_id), None)
    if not gift:
        await callback.answer("Подарок не найден", show_alert=True)
        return

    fresh = await db.get_user(int(user["id"])) or user
    balance_units = int(fresh["balance"])
    price_units = gift["price"] * STARS_SCALE
    ref_stats = await db.get_referral_stats(int(user["id"]))

    if ref_stats["invited"] < FRIENDS_REQUIRED:
        await callback.answer(
            f"Для первого вывода нужно пригласить {FRIENDS_REQUIRED} друзей. У тебя {ref_stats['invited']}.",
            show_alert=True,
        )
        return

    if balance_units < price_units:
        await callback.answer("Недостаточно звёзд на балансе", show_alert=True)
        return

    # Создаем заявку
    withdrawal_id = await db.create_withdrawal(int(user["id"]), price_units, gift["name"])
    if not withdrawal_id:
        await callback.answer("Ошибка при создании заявки. Попробуй позже.", show_alert=True)
        return

    await callback.answer("Заявка создана!", show_alert=False)

    # Текст для юзера
    success_text = (
        "✅ <b>Заявка на вывод создана!</b>\n\n"
        f"🎁 Подарок: <b>{gift['name']}</b>\n"
        f"💰 Сумма: <b>{gift['price']}</b> ⭐\n\n"
        "Ожидай проверки администратором."
    )
    if callback.message:
        await safe_edit(callback.message, success_text, back_to_main("← В меню"))

    # Отправка в канал или админам
    admin_text = (
        "💸 <b>Новая заявка на вывод!</b>\n\n"
        f"🆔 Заявка: <b>#{withdrawal_id}</b>\n"
        f"👤 Пользователь: {escape(fresh.get('full_name') or '')} (ID: <code>{user['id']}</code>)\n"
        f"🎁 Подарок: <b>{gift['name']}</b>\n"
        f"💰 Сумма: <b>{gift['price']}</b> ⭐\n\n"
        "Обработать можно в /admin -> Заявки на вывод."
    )

    sent_to_channel = False
    if getattr(settings, "withdraw_channel", None):
        try:
            await bot.send_message(
                chat_id=settings.withdraw_channel,
                text=admin_text,
                reply_markup=withdrawal_channel_decision(withdrawal_id)
            )
            sent_to_channel = True
        except Exception as e:
            logger.error(f"Не удалось отправить заявку в канал: {e}")

    if not sent_to_channel:
        for admin_id in getattr(settings, "admin_ids", frozenset()):
            try:
                await bot.send_message(admin_id, admin_text)
            except (TelegramForbiddenError, TelegramBadRequest):
                pass


@router.callback_query(F.data == "withdraw:history")
async def cb_withdraw_history(callback: CallbackQuery, db: Any, user: dict[str, Any]) -> None:
    await callback.answer()
    if callback.message is None:
        return

    withdrawals = await db.get_user_withdrawals(int(user["id"]), limit=10)
    if not withdrawals:
        text = "📜 <b>История выводов</b>\n\nУ тебя пока нет заявок на вывод."
    else:
        lines = ["📜 <b>История выводов</b>\n"]
        for w in withdrawals:
            gift = w.get("gift") or "Подарок"
            status = w.get("status")
            amount = format_units(int(w["amount"]))
            dt = format_dt(str(w.get("created_at") or ""))

            if status == "pending":
                icon = "⏳"
            elif status == "paid":
                icon = "✅"
            else:
                icon = "❌"

            lines.append(f"{icon} {gift} — <b>{amount}</b> ⭐ ({dt})")
        text = "\n".join(lines)

    await safe_edit(callback.message, text, withdraw_history_menu())