"""Покупка трафика (Advertiser Mode) и оплата Telegram Stars."""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot.defaults import (
    MAX_PURCHASE_QUANTITY,
    MIN_TASK_QUANTITY,
    MIN_VIEW_QUANTITY,
    ORDER_KIND_LABELS,
    ORDER_STATUS_LABELS,
    PROJECT_CATEGORIES,
)
from bot.keyboards import (
    traffic_audience,
    traffic_cancel,
    traffic_categories,
    traffic_menu,
    traffic_orders_menu,
)
from bot.services import get_prices, get_text
from bot.states import TrafficStates
from bot.utils import (
    escape,
    format_dt,
    format_units,
    render,
    safe_edit,
    telegram_stars_amount,
    truncate,
)

logger = logging.getLogger(__name__)

router = Router(name="traffic")

KIND_LABELS = {"task": "заданий", "view": "просмотров", "show": "показов"}


def _parts(data: str) -> list[str]:
    return (data or "").split(":")


def _int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _traffic_text(db: Any) -> str:
    prices = await get_prices(db)
    template = await get_text(db, "traffic")
    return render(
        template,
        task_price=format_units(prices["task"]),
        broadcast_price=format_units(prices["broadcast"]),
        view_price=format_units(prices["view"]),
        show_price=format_units(prices["show"]),
        min_tasks=MIN_TASK_QUANTITY,
    )


async def _send_invoice(
    bot: Bot,
    chat_id: int,
    *,
    title: str,
    description: str,
    payload: str,
    stars: int,
) -> None:
    await bot.send_invoice(
        chat_id=chat_id,
        title=truncate(title, 32),
        description=truncate(description, 255),
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=truncate(title, 32), amount=max(1, int(stars)))],
    )


def _quantity_prompt(kind: str, minimum: int) -> str:
    if kind == "task":
        return (
            "Сколько выполнений нужно? Целое число "
            f"от {minimum} до {MAX_PURCHASE_QUANTITY}."
        )
    if kind == "view":
        return (
            "Сколько просмотров нужно? Целое число "
            f"от {minimum} до {MAX_PURCHASE_QUANTITY}."
        )
    return (
        "Сколько показов нужно? Целое число "
        f"от {minimum} до {MAX_PURCHASE_QUANTITY}."
    )


@router.callback_query(F.data == "menu:traffic")
async def cb_traffic_menu(
    callback: CallbackQuery, db: Any, state: FSMContext
) -> None:
    await state.clear()
    await callback.answer()
    if callback.message is not None:
        await safe_edit(callback.message, await _traffic_text(db), traffic_menu())


# ------------------------------------------------------------------- задания
@router.callback_query(F.data == "traffic:buy:task")
async def cb_buy_task(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await safe_edit(
            callback.message,
            "🛠 <b>Купить задания</b>\n\nВыбери категорию проекта:",
            traffic_categories(),
        )


@router.callback_query(F.data.startswith("traffic:category:"))
async def cb_traffic_category(callback: CallbackQuery, state: FSMContext) -> None:
    parts = _parts(callback.data or "")
    code = parts[2] if len(parts) > 2 else ""
    if code not in PROJECT_CATEGORIES:
        await callback.answer("Неизвестная категория", show_alert=True)
        return
    label, multiplier = PROJECT_CATEGORIES[code]
    await state.set_state(TrafficStates.waiting_target)
    await state.update_data(order_kind="task", category=code, multiplier=multiplier)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"🛠 <b>{escape(label)}</b>\n\n"
            "Отправь ссылку на канал/бота, который нужно продвигать "
            "(например https://t.me/channel).\nДля отмены — /cancel.",
            reply_markup=traffic_cancel(),
        )


# --------------------------------------------------------------- просмотры/показы
@router.callback_query(F.data.in_({"traffic:buy:view", "traffic:buy:show"}))
async def cb_buy_views(callback: CallbackQuery, state: FSMContext) -> None:
    kind = (callback.data or "").split(":")[2]
    label = "👁 Покупка просмотров" if kind == "view" else "📢 Покупка показов"
    await state.set_state(TrafficStates.waiting_target)
    await state.update_data(order_kind=kind, category=None, multiplier=1)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"{label}\n\n"
            "Отправь ссылку на пост/канал, где нужны просмотры.\n"
            "Для отмены — /cancel.",
            reply_markup=traffic_cancel(),
        )


@router.message(TrafficStates.waiting_target)
async def traffic_target(message: Message, state: FSMContext, db: Any) -> None:
    target = (message.text or "").strip()
    if not target or len(target) > 300:
        await message.answer("Ссылка должна быть от 1 до 300 символов. Попробуй снова.")
        return
    data = await state.get_data()
    kind = str(data.get("order_kind") or "task")
    prices = await get_prices(db)
    if kind == "task":
        minimum = MIN_TASK_QUANTITY
        price_units = prices["task"] * int(data.get("multiplier") or 1)
    elif kind == "view":
        minimum = MIN_VIEW_QUANTITY
        price_units = prices["view"]
    else:
        minimum = MIN_VIEW_QUANTITY
        price_units = prices["show"]
    await state.update_data(target=target, price_units=price_units)
    await state.set_state(TrafficStates.waiting_quantity)
    await message.answer(
        _quantity_prompt(kind, minimum) + "\n\nДля отмены — /cancel.",
        reply_markup=traffic_cancel(),
    )


@router.message(TrafficStates.waiting_quantity)
async def traffic_quantity(
    message: Message, state: FSMContext, db: Any, user: dict[str, Any], bot: Bot
) -> None:
    data = await state.get_data()
    kind = str(data.get("order_kind") or "task")
    minimum = MIN_TASK_QUANTITY if kind == "task" else MIN_VIEW_QUANTITY
    quantity = _int((message.text or "").strip())
    if quantity is None or quantity < minimum or quantity > MAX_PURCHASE_QUANTITY:
        await message.answer(
            f"⚠️ Нужно целое число от {minimum} до {MAX_PURCHASE_QUANTITY}. Попробуй снова."
        )
        return

    price_units = int(data.get("price_units") or 0)
    amount = price_units * quantity
    if amount <= 0:
        await state.clear()
        await message.answer("⚠️ Не удалось рассчитать стоимость. Начни заново.")
        return
    stars = telegram_stars_amount(amount)
    target = str(data.get("target") or "")
    label = KIND_LABELS.get(kind, "заданий")
    order_id = await db.create_order(
        user_id=int(user["id"]),
        kind=kind,
        target=target,
        quantity=quantity,
        amount=amount,
        stars=stars,
    )
    await state.clear()
    description = (
        f"Количество {label}: {quantity}. "
        f"Стоимость: {format_units(amount)} ⭐. "
        "После оплаты заказ проверит администратор."
    )
    try:
        await _send_invoice(
            bot,
            message.chat.id,
            title=f"Заказ #{order_id}: {ORDER_KIND_LABELS.get(kind, kind)}",
            description=description,
            payload=f"order:{order_id}",
            stars=stars,
        )
    except TelegramBadRequest:
        logger.exception("Не удалось выставить счёт на заказ #%s", order_id)
        await message.answer("⚠️ Не удалось выставить счёт. Попробуй позже.")
        return
    await message.answer(
        f"🧾 Заказ <b>#{order_id}</b> создан.\n"
        f"⭐ К оплате: <b>{stars}</b> ⭐ (Telegram Stars).\n\n"
        "Оплати счёт выше — после оплаты администратор запустит заказ.",
        reply_markup=traffic_orders_menu(),
    )


# ------------------------------------------------------------------- рассылка
@router.callback_query(F.data == "traffic:buy:broadcast")
async def cb_buy_broadcast(callback: CallbackQuery, db: Any) -> None:
    prices = await get_prices(db)
    count = await db.get_users_count()
    if count <= 0:
        await callback.answer("В базе пока нет пользователей", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "📣 <b>Покупка рассылки</b>\n\n"
            f"В боте сейчас <b>{count}</b> пользователей. "
            f"Цена: <b>{format_units(prices['broadcast'])}</b> ⭐ за получателя.\n"
            "Выбери, какой доле аудитории отправить пост:",
            reply_markup=traffic_audience(count, prices["broadcast"]),
        )


@router.callback_query(F.data.startswith("traffic:audience:"))
async def cb_traffic_audience(callback: CallbackQuery, db: Any, user: dict[str, Any], bot: Bot) -> None:
    parts = _parts(callback.data or "")
    percent = _int(parts[2]) if len(parts) > 2 else None
    if percent not in {25, 50, 75, 100}:
        await callback.answer("Некорректная доля аудитории", show_alert=True)
        return
    prices = await get_prices(db)
    count = await db.get_users_count()
    recipients = max(1, count * percent // 100)
    amount = prices["broadcast"] * recipients
    stars = telegram_stars_amount(amount)
    order_id = await db.create_order(
        user_id=int(user["id"]),
        kind="broadcast",
        target=f"Аудитория {percent}% ({recipients} чел.)",
        quantity=recipients,
        amount=amount,
        stars=stars,
    )
    await callback.answer()
    try:
        await _send_invoice(
            bot,
            callback.from_user.id,
            title=f"Рассылка #{order_id}",
            description=(
                f"Отправка сообщения {recipients} получателям ({percent}% аудитории). "
                f"Стоимость: {format_units(amount)} ⭐."
            ),
            payload=f"order:{order_id}",
            stars=stars,
        )
    except TelegramBadRequest:
        logger.exception("Не удалось выставить счёт на рассылку #%s", order_id)
        await bot.send_message(callback.from_user.id, "⚠️ Не удалось выставить счёт.")
        return
    await bot.send_message(
        callback.from_user.id,
        f"🧾 Заказ <b>#{order_id}</b> создан.\n"
        f"⭐ К оплате: <b>{stars}</b> ⭐.",
        reply_markup=traffic_orders_menu(),
    )


# ------------------------------------------------------------------ мои заказы
@router.callback_query(F.data == "traffic:orders")
async def cb_my_orders(callback: CallbackQuery, db: Any, user: dict[str, Any]) -> None:
    orders = await db.list_user_orders(int(user["id"]), limit=10)
    if not orders:
        text = "📋 <b>Мои заказы</b>\n\nЗаказов пока нет."
    else:
        lines = ["📋 <b>Мои заказы</b>", ""]
        for order in orders:
            kind = ORDER_KIND_LABELS.get(str(order["kind"]), str(order["kind"]))
            status = ORDER_STATUS_LABELS.get(str(order["status"]), str(order["status"]))
            lines.append(
                f"#{int(order['id'])} · {kind} · {int(order['quantity'])} шт · "
                f"{int(order['stars'] or 0)} ⭐\n"
                f"   {status} · {format_dt(str(order.get('created_at') or ''))}"
            )
        text = "\n".join(lines)
    await callback.answer()
    if callback.message is not None:
        await safe_edit(callback.message, text, traffic_orders_menu())


@router.callback_query(F.data == "traffic:cancel")
async def cb_traffic_cancel(
    callback: CallbackQuery, db: Any, state: FSMContext
) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message is not None:
        await safe_edit(callback.message, await _traffic_text(db), traffic_menu())


# ----------------------------------------------------------------------- оплата
@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, db: Any) -> None:
    parts = (query.invoice_payload or "").split(":")
    order_id = _int(parts[1]) if len(parts) > 1 and parts[0] == "order" else None
    if order_id is None:
        await query.answer(ok=False, error_message="Некорректный счёт. Создай заказ заново.")
        return
    order = await db.get_order(order_id)
    if order is None:
        await query.answer(ok=False, error_message="Заказ не найден. Создай его заново.")
        return
    if str(order["status"]) != "pending":
        await query.answer(ok=False, error_message="Этот заказ уже оплачен.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(
    message: Message,
    db: Any,
    settings: Any,
    state: FSMContext,
    bot: Bot,
) -> None:
    payment = message.successful_payment
    if payment is None:
        return
    parts = (payment.invoice_payload or "").split(":")
    order_id = _int(parts[1]) if len(parts) > 1 and parts[0] == "order" else None
    if order_id is None:
        await message.answer("⚠️ Оплата получена, но заказ не найден. Напиши в поддержку.")
        return
    updated = await db.set_order_paid(order_id, int(payment.total_amount))
    if not updated:
        await message.answer("⚠️ Заказ уже был оплачен ранее. Спасибо!")
        return
    await state.clear()
    order = await db.get_order(order_id)
    await message.answer(
        "✅ <b>Оплата получена!</b>\n\n"
        f"🧾 Заказ <b>#{order_id}</b> принят. "
        "Администратор проверит его и запустит в ближайшее время.",
        reply_markup=traffic_orders_menu(),
    )
    if order is None:
        return
    admin_text = (
        "🛒 <b>Новый оплаченный заказ</b>\n\n"
        f"🆔 Заказ: <b>#{order_id}</b>\n"
        f"👤 Пользователь: ID <code>{int(order['user_id'])}</code>\n"
        f"📦 Вид: {ORDER_KIND_LABELS.get(str(order['kind']), str(order['kind']))}\n"
        f"🎯 Цель: {html.escape(str(order.get('target') or '—'))}\n"
        f"🔢 Количество: <b>{int(order['quantity'])}</b>\n"
        f"⭐ Сумма: <b>{int(payment.total_amount)}</b> ⭐\n\n"
        "Запустить/отменить: /admin → «Заказы трафика»."
    )
    for admin_id in getattr(settings, "admin_ids", frozenset()):
        try:
            await bot.send_message(admin_id, admin_text)
        except TelegramBadRequest:
            logger.debug("Не удалось уведомить админа %s", admin_id)


__all__ = ["router"]
