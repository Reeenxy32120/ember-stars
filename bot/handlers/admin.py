"""Встроенная админ-панель."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.defaults import (
    ORDER_KIND_LABELS,
    ORDER_STATUS_LABELS,
    SETTINGS_TITLES,
    TASK_TYPE_LABELS,
    TEXT_TITLES,
)
from bot.filters import IsAdmin
from bot.keyboards import (
    admin_back,
    admin_broadcast_confirm,
    admin_menu,
    admin_orders,
    admin_promo_menu,
    admin_settings_menu,
    admin_task_bonus_menu,
    admin_task_confirm_delete,
    admin_task_type_menu,
    admin_task_view,
    admin_tasks_list,
    admin_tasks_menu,
    admin_text_edit_menu,
    admin_texts_menu,
    admin_user_card,
    admin_users,
    admin_withdrawals,
)
from bot.services import (
    get_settings_view,
    get_text,
    reset_text,
    set_text,
    setting_display,
    setting_from_input,
)
from bot.states import AdminStates
from bot.utils import (
    StarsParseError,
    escape,
    format_dt,
    format_units,
    parse_stars,
    safe_edit,
    truncate,
)

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

denied_router = Router(name="admin_denied")

USERS_PER_PAGE = 5
TASKS_PER_PAGE = 5
WITHDRAWALS_PER_PAGE = 5
ORDERS_PER_PAGE = 5
BROADCAST_DELAY = 0.05


# --------------------------------------------------------------------- helpers
def _parts(data: str) -> list[str]:
    return (data or "").split(":")


def _int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cancel_kb(target: str = "admin:main") -> InlineKeyboardMarkup:
    return admin_back(target, "← Отмена")


def _skip_chat_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="admin:task:skip_chat")],
            [InlineKeyboardButton(text="← Отмена", callback_data="admin:tasks")],
        ]
    )


def _admin_main_text(stats: dict[str, Any]) -> str:
    return (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: <b>{stats['users_total']}</b> "
        f"(заблокировано: {stats['users_blocked']})\n"
        f"📋 Заданий активных: <b>{stats['tasks_active']}</b> "
        f"(всего: {stats['tasks_total']})\n"
        f"💸 Заявок на вывод: <b>{stats['withdrawals_pending']}</b> "
        f"(выплачено: {stats['withdrawals_paid']})\n"
        f"🛒 Заказов: <b>{stats['orders_total']}</b> "
        f"(оплачено: {stats['orders_paid']})\n"
        f"⭐ Суммарный баланс: <b>{format_units(stats['balance_total'])}</b> ⭐\n\n"
        "Выбери раздел:"
    )


async def _send_admin_main(message: Message, db: Any) -> None:
    stats = await db.get_stats()
    await safe_edit(message, _admin_main_text(stats), admin_menu())


def _task_card_text(task: dict[str, Any]) -> str:
    chat_id = task.get("chat_id")
    return (
        f"📋 <b>Задание #{int(task['id'])}</b>\n\n"
        f"📝 Название: {escape(truncate(str(task['title']), 120))}\n"
        f"📌 Тип: {TASK_TYPE_LABELS.get(str(task['type']), str(task['type']))}\n"
        f"🔗 Ссылка: {escape(str(task.get('url') or '—'))}\n"
        f"⭐ Награда: <b>{format_units(int(task['reward']))}</b> ⭐\n"
        f"🆔 chat_id: {escape(str(chat_id)) if chat_id else '—'}\n"
        f"🎁 Бонусное: {'да' if task.get('is_bonus') else 'нет'}\n"
        f"🟢 Активно: {'да' if task.get('is_active') else 'нет'}\n"
        f"📅 Создано: {format_dt(str(task.get('created_at') or ''))}"
    )


def _user_card_text(
    user: dict[str, Any],
    referral_stats: dict[str, int],
    withdrawal_totals: dict[str, int],
) -> str:
    username = f"@{user['username']}" if user.get("username") else "—"
    return (
        f"👤 <b>Пользователь</b>\n\n"
        f"🆔 ID: <code>{int(user['id'])}</code>\n"
        f"🔗 Username: {escape(username)}\n"
        f"📝 Имя: {escape(str(user.get('full_name') or '—'))}\n"
        f"⭐ Баланс: <b>{format_units(int(user['balance']))}</b> ⭐\n"
        f"✅ Выполнено заданий: <b>{int(user['tasks_done'])}</b>\n"
        f"👥 Приглашено: <b>{referral_stats['invited']}</b> "
        f"(заработал {format_units(referral_stats['earned'])} ⭐)\n"
        f"💸 В заявках: {format_units(withdrawal_totals['pending'])} ⭐, "
        f"выплачено: {format_units(withdrawal_totals['paid'])} ⭐\n"
        f"🔤 Язык: {escape(str(user.get('language') or 'ru'))}\n"
        f"🔔 Уведомления: {'вкл' if user.get('notifications') else 'выкл'}\n"
        f"⛔ Статус: {'заблокирован' if user.get('is_blocked') else 'активен'}\n"
        f"📅 Регистрация: {format_dt(str(user.get('created_at') or ''))}"
    )


async def _show_withdrawals(message: Message, db: Any, page: int = 1) -> None:
    items, pages = await db.paginate_withdrawals("pending", page, WITHDRAWALS_PER_PAGE)
    page = max(1, min(page, pages))
    header = (
        "💸 <b>Заявки на вывод</b>\n\n"
        f"Ожидает проверки: <b>{await db.count_withdrawals('pending')}</b>\n"
        f"Страница <b>{max(1, min(page, pages))}/{pages}</b>"
    )
    if not items:
        text = header + "\n\n✅ Заявок в ожидании нет."
    else:
        lines = [header, ""]
        for item in items:
            username = f"@{item['username']}" if item.get("username") else "—"
            lines.append(
                f"#{int(item['id'])} · {escape(username)} · "
                f"<b>{format_units(int(item['amount']))}</b> ⭐ · "
                f"{format_dt(str(item.get('created_at') or ''))} · "
                f"ID {int(item['user_id'])}"
            )
        lines.append("\n✅ — подтвердить, ❌ — отклонить и вернуть Stars.")
        text = "\n".join(lines)
    await safe_edit(message, text, admin_withdrawals(items, page, pages))


async def _show_users(message: Message, db: Any, page: int = 1) -> None:
    items, pages = await db.paginate_users(page, USERS_PER_PAGE)
    page = max(1, min(page, pages))
    header = (
        "👥 <b>Пользователи</b>\n\n"
        f"Всего: <b>{await db.get_users_count()}</b>\n"
        f"Страница <b>{max(1, min(page, pages))}/{pages}</b>"
    )
    await safe_edit(message, header, admin_users(items, page, pages))


async def _show_orders(message: Message, db: Any, page: int = 1) -> None:
    items, pages = await db.paginate_orders(None, page, ORDERS_PER_PAGE)
    page = max(1, min(page, pages))
    header = (
        "🛒 <b>Заказы трафика</b>\n\n"
        f"Всего: <b>{await db.count_orders()}</b>, "
        f"оплачено: <b>{await db.count_orders('paid')}</b>\n"
        f"Страница <b>{max(1, min(page, pages))}/{pages}</b>"
    )
    if not items:
        await safe_edit(message, header + "\n\nЗаказов нет.", admin_orders(items, page, pages))
        return
    lines = [header, ""]
    for order in items:
        kind = ORDER_KIND_LABELS.get(str(order["kind"]), str(order["kind"]))
        status = ORDER_STATUS_LABELS.get(str(order["status"]), str(order["status"]))
        stars = int(order["stars"] or 0)
        lines.append(
            f"#{int(order['id'])} · {kind} · {status}\n"
            f"   {escape(str(order.get('target') or '—'))} · "
            f"{int(order['quantity'])} шт · {stars} ⭐ · "
            f"ID {int(order['user_id'])} · {format_dt(str(order.get('created_at') or ''))}"
        )
    await safe_edit(message, "\n".join(lines), admin_orders(items, page, pages))


async def _show_settings(message: Message, db: Any) -> None:
    values = await get_settings_view(db)
    lines = ["⚙️ <b>Настройки</b>", ""]
    for key, title in SETTINGS_TITLES.items():
        lines.append(f"• {title}: <b>{setting_display(key, values.get(key, '0'))}</b>")
    lines.append("\nВыбери настройку для изменения:")
    await safe_edit(message, "\n".join(lines), admin_settings_menu(values))


# --------------------------------------------------------------- /admin и меню
@denied_router.message(Command("admin"))
async def denied_admin(message: Message, db: Any) -> None:
    await message.answer(await get_text(db, "admin_denied"))


@denied_router.callback_query(F.data.startswith("admin:"))
async def denied_admin_callback(
    callback: CallbackQuery, db: Any, settings: Any
) -> None:
    if callback.from_user.id in getattr(settings, "admin_ids", frozenset()):
        await callback.answer("Кнопка устарела, открой /admin", show_alert=True)
        return
    await callback.answer(await get_text(db, "admin_denied"), show_alert=True)


@router.message(Command("admin"))
async def cmd_admin(message: Message, db: Any, state: FSMContext) -> None:
    await state.clear()
    stats = await db.get_stats()
    await message.answer(_admin_main_text(stats), reply_markup=admin_menu())


@router.callback_query(F.data == "admin:main")
async def cb_admin_main(callback: CallbackQuery, db: Any, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if callback.message is not None:
        await _send_admin_main(callback.message, db)

# ----------------------------------------------------------------- Промокоды
@router.callback_query(F.data == "admin:promo")
async def cb_admin_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if callback.message is not None:
        await safe_edit(
            callback.message,
            "🎟 <b>Управление промокодами</b>\n\n"
            "Здесь ты можешь создавать промокоды на получение Stars.",
            admin_promo_menu(),
        )

@router.callback_query(F.data == "admin:promo:add")
async def cb_admin_promo_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.promo_code_input)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "🎟 <b>Создание промокода (Шаг 1/3)</b>\n\n"
            "Отправь текст промокода (например, <code>WELCOME</code>).\n"
            "Для отмены — /cancel.",
            reply_markup=_cancel_kb("admin:promo"),
        )

@router.message(AdminStates.promo_code_input)
async def admin_promo_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if not code or " " in code:
        await message.answer("Промокод должен быть одним словом без пробелов. Попробуй снова.")
        return
    await state.update_data(promo_code=code)
    await state.set_state(AdminStates.promo_amount_input)
    await message.answer(
        "🎟 <b>Создание промокода (Шаг 2/3)</b>\n\n"
        "Сколько Stars будет начислено за этот промокод? (например, 5)\n"
        "Для отмены — /cancel.",
        reply_markup=_cancel_kb("admin:promo"),
    )

@router.message(AdminStates.promo_amount_input)
async def admin_promo_amount(message: Message, state: FSMContext) -> None:
    try:
        amount_units = parse_stars(message.text or "")
    except StarsParseError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}")
        return
    await state.update_data(promo_amount=amount_units)
    await state.set_state(AdminStates.promo_uses_input)
    await message.answer(
        "🎟 <b>Создание промокода (Шаг 3/3)</b>\n\n"
        "Сколько раз можно активировать этот промокод? (например, 10)\n"
        "Для отмены — /cancel.",
        reply_markup=_cancel_kb("admin:promo"),
    )

@router.message(AdminStates.promo_uses_input)
async def admin_promo_uses(message: Message, db: Any, state: FSMContext) -> None:
    try:
        uses = int((message.text or "").strip())
        if uses <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Нужно целое положительное число. Попробуй снова.")
        return

    data = await state.get_data()
    code = data.get("promo_code")
    amount = data.get("promo_amount")

    if not code or not amount:
        await state.clear()
        await message.answer("Ошибка данных. Начни заново: /admin -> Промокоды.")
        return

    await db.create_promo(code, amount, uses)
    await state.clear()
    await message.answer(
        f"✅ Промокод <code>{code}</code> успешно создан!\n\n"
        f"💰 Сумма: <b>{format_units(amount)}</b> ⭐\n"
        f"🔢 Активаций: <b>{uses}</b>",
        reply_markup=admin_back("admin:promo", "← К промокодам"),
    )

# ----------------------------------------------------------------------- тексты
# ... (остальной код admin.py без изменений, начиная с @router.callback_query(F.data == "admin:texts"))