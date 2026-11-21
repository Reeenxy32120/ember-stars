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
            gift = item.get("gift") or "Подарок"
            lines.append(
                f"#{int(item['id'])} · {escape(username)} · <b>{gift}</b> · "
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
@router.callback_query(F.data == "admin:texts")
async def cb_admin_texts(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await safe_edit(
            callback.message,
            "📝 <b>Тексты</b>\n\nВыбери текст, который нужно изменить:",
            admin_texts_menu(),
        )

@router.callback_query(F.data.startswith("admin:text:"))
async def cb_admin_text(
    callback: CallbackQuery, db: Any, state: FSMContext
) -> None:
    parts = _parts(callback.data or "")
    if len(parts) < 3:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    key = parts[2]
    if key == "reset" and len(parts) >= 4:
        target = parts[3]
        if target in TEXT_TITLES:
            await reset_text(db, target)
            await callback.answer("Текст сброшен")
            await safe_edit(
                callback.message,
                f"♻️ Текст «{TEXT_TITLES[target]}» сброшен к стандартному.",
                admin_back("admin:texts", "← К текстам"),
            )
        else:
            await callback.answer("Неизвестный текст", show_alert=True)
        return
    if key not in TEXT_TITLES:
        await callback.answer("Неизвестный текст", show_alert=True)
        return

    current = await get_text(db, key)
    await state.set_state(AdminStates.waiting_text)
    await state.update_data(text_key=key)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"📝 <b>{TEXT_TITLES[key]}</b>\n\n"
            f"Текущий текст:\n<code>{escape(current)}</code>\n\n"
            "Отправь новый текст одним сообщением. Можно использовать HTML-теги "
            "и плейсхолдеры в фигурных скобках.\nДля отмены — /cancel.",
            reply_markup=admin_text_edit_menu(key),
        )

@router.message(AdminStates.waiting_text)
async def admin_text_save(message: Message, db: Any, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("text_key")
    if key not in TEXT_TITLES:
        await state.clear()
        await message.answer("Не удалось определить текст. Открой админку заново.")
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer("Текст не может быть пустым. Отправь текст или /cancel.")
        return
    if len(value) > 4000:
        await message.answer("Слишком длинный текст (максимум 4000 символов).")
        return
    await set_text(db, key, value)
    await state.clear()
    await message.answer(
        f"✅ Текст «{TEXT_TITLES[key]}» сохранён.",
        reply_markup=admin_back("admin:texts", "← К текстам"),
    )

# ---------------------------------------------------------------------- задания
@router.callback_query(F.data == "admin:tasks")
async def cb_admin_tasks(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await safe_edit(
            callback.message,
            "📋 <b>Управление заданиями</b>\n\n"
            "Добавляй задания, редактируй награду/ссылку, включай и выключай их.",
            admin_tasks_menu(),
        )

@router.callback_query(F.data.startswith("admin:tasks:page:"))
async def cb_admin_tasks_page(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    page = _int(parts[3]) if len(parts) > 3 else None
    if page is None:
        await callback.answer("Некорректная страница", show_alert=True)
        return
    items, pages = await db.paginate_admin_tasks(page, TASKS_PER_PAGE)
    page = max(1, min(page, pages))
    header = (
        "🗂 <b>Все задания</b>\n\n"
        f"Всего: <b>{await db.count_tasks(active_only=False)}</b> · "
        f"страница <b>{page}/{pages}</b>"
    )
    if not items:
        header += "\n\nЗаданий пока нет. Нажми «Добавить задание»."
    await callback.answer()
    if callback.message is not None:
        await safe_edit(callback.message, header, admin_tasks_list(items, page, pages))

@router.callback_query(F.data == "admin:task:add")
async def cb_admin_task_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.task_title)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "➕ <b>Новое задание</b>\n\nШаг 1/5. Отправь название задания.",
            reply_markup=_cancel_kb("admin:tasks"),
        )

@router.message(AdminStates.task_title)
async def admin_task_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title or len(title) > 120:
        await message.answer("Название должно быть от 1 до 120 символов. Попробуй снова.")
        return
    await state.update_data(task_title=title)
    await state.set_state(AdminStates.task_url)
    await message.answer(
        "Шаг 2/5. Отправь ссылку задания (например https://t.me/channel или @channel).",
        reply_markup=_cancel_kb("admin:tasks"),
    )

@router.message(AdminStates.task_url)
async def admin_task_url(message: Message, state: FSMContext) -> None:
    url = (message.text or "").strip()
    if not url or any(char.isspace() for char in url):
        await message.answer("Ссылка должна быть одним словом без пробелов. Попробуй снова.")
        return
    await state.update_data(task_url=url)
    await state.set_state(AdminStates.task_url)  # ждём выбор типа кнопкой
    await message.answer(
        "Шаг 3/5. Выбери тип задания:",
        reply_markup=admin_task_type_menu(),
    )

@router.callback_query(F.data.startswith("admin:task:type:"))
async def cb_admin_task_type(callback: CallbackQuery, state: FSMContext) -> None:
    parts = _parts(callback.data or "")
    task_type = parts[3] if len(parts) > 3 else ""
    if task_type not in TASK_TYPE_LABELS:
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("task_title") or not data.get("task_url"):
        await callback.answer("Черновик задания потерян, начни заново", show_alert=True)
        await state.clear()
        return
    await state.update_data(task_type=task_type)
    await callback.answer()
    if callback.message is None:
        return
    if task_type == "link":
        await state.update_data(task_chat_id=None)
        await state.set_state(AdminStates.task_reward)
        await callback.message.answer(
            "Шаг 4/5. Отправь награду в Stars (например 0.5).",
            reply_markup=_cancel_kb("admin:tasks"),
        )
        return
    await state.set_state(AdminStates.task_chat_id)
    hint = (
        "Отправь ID/юзернейм канала (@channel или -100...), чтобы бот мог "
        "проверять подписку."
    )
    if task_type == "bot":
        hint = "Отправь ID/юзернейм бота-канала для проверки или пропусти этот шаг."
    await callback.message.answer(
        f"Шаг 4/5. {hint}",
        reply_markup=_skip_chat_kb(),
    )

@router.callback_query(F.data == "admin:task:skip_chat")
async def cb_admin_task_skip_chat(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(task_chat_id=None)
    await state.set_state(AdminStates.task_reward)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "Шаг 4/5. Отправь награду в Stars (например 0.5).",
            reply_markup=_cancel_kb("admin:tasks"),
        )

@router.message(AdminStates.task_chat_id)
async def admin_task_chat_id(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value in {"-", "—", "skip", "пропустить"}:
        value = ""
    await state.update_data(task_chat_id=value or None)
    await state.set_state(AdminStates.task_reward)
    await message.answer(
        "Шаг 4/5. Отправь награду в Stars (например 0.5).",
        reply_markup=_cancel_kb("admin:tasks"),
    )

@router.message(AdminStates.task_reward)
async def admin_task_reward(message: Message, state: FSMContext) -> None:
    try:
        reward = parse_stars(message.text or "")
    except StarsParseError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}")
        return
    await state.update_data(task_reward=reward)
    await state.set_state(AdminStates.task_bonus)
    await message.answer(
        "Шаг 5/5. Это бонусное задание?",
        reply_markup=admin_task_bonus_menu(),
    )

@router.callback_query(F.data.startswith("admin:task:bonus:"))
async def cb_admin_task_bonus(callback: CallbackQuery, db: Any, state: FSMContext) -> None:
    parts = _parts(callback.data or "")
    choice = parts[3] if len(parts) > 3 else ""
    if choice not in {"yes", "no"}:
        await callback.answer("Некорректный выбор", show_alert=True)
        return
    data = await state.get_data()
    title = data.get("task_title")
    url = data.get("task_url")
    task_type = data.get("task_type")
    reward = data.get("task_reward")
    if not title or not url or task_type not in TASK_TYPE_LABELS or not reward:
        await callback.answer("Черновик задания потерян, начни заново", show_alert=True)
        await state.clear()
        return
    task_id = await db.create_task(
        title=title,
        task_type=task_type,
        url=url,
        reward=int(reward),
        chat_id=data.get("task_chat_id") or None,
        is_bonus=choice == "yes",
    )
    await state.clear()
    task = await db.get_task(task_id)
    await callback.answer("Задание создано")
    if callback.message is not None and task is not None:
        await callback.message.answer(
            "✅ Задание создано!\n\n" + _task_card_text(task),
            reply_markup=admin_task_view(task),
        )

@router.callback_query(F.data.startswith("admin:task:edit:"))
async def cb_admin_task_edit(callback: CallbackQuery, db: Any, state: FSMContext) -> None:
    parts = _parts(callback.data or "")
    if len(parts) < 5:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    task_id = _int(parts[3])
    field = parts[4]
    if task_id is None or field not in {"title", "url", "reward", "chat_id"}:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    task = await db.get_task(task_id)
    if task is None:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    prompts = {
        "title": "Отправь новое название задания.",
        "url": "Отправь новую ссылку задания.",
        "reward": "Отправь новую награду в Stars (например 0.5).",
        "chat_id": "Отправь новый chat_id (или «—», чтобы очистить).",
    }
    await state.set_state(AdminStates.edit_value)
    await state.update_data(task_id=task_id, field=field)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"✏️ <b>Задание #{task_id}</b>\n\n{prompts[field]}",
            reply_markup=_cancel_kb(f"admin:task:{task_id}"),
        )

@router.message(AdminStates.edit_value)
async def admin_task_edit_value(message: Message, db: Any, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = data.get("task_id")
    field = data.get("field")
    task = await db.get_task(int(task_id)) if task_id else None
    if task is None or field not in {"title", "url", "reward", "chat_id"}:
        await state.clear()
        await message.answer("Задание не найдено, открой раздел заново.")
        return
    raw = (message.text or "").strip()
    updates: dict[str, Any] = {}
    if field == "title":
        if not raw or len(raw) > 120:
            await message.answer("Название должно быть от 1 до 120 символов.")
            return
        updates["title"] = raw
    elif field == "url":
        if not raw or any(char.isspace() for char in raw):
            await message.answer("Ссылка должна быть одним словом без пробелов.")
            return
        updates["url"] = raw
    elif field == "reward":
        try:
            updates["reward"] = parse_stars(raw)
        except StarsParseError as exc:
            await message.answer(f"⚠️ {escape(str(exc))}")
            return
    else:
        updates["chat_id"] = None if raw in {"-", "—", "skip"} else raw
    await db.update_task(int(task_id), **updates)
    await state.clear()
    fresh = await db.get_task(int(task_id))
    if fresh is None:
        await message.answer("Задание удалено.")
        return
    await message.answer(
        "✅ Задание обновлено.\n\n" + _task_card_text(fresh),
        reply_markup=admin_task_view(fresh),
    )

@router.callback_query(F.data.startswith("admin:task:delete_confirm:"))
async def cb_admin_task_delete_confirm(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    task_id = _int(parts[3]) if len(parts) > 3 else None
    if task_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    deleted = await db.delete_task(task_id)
    await callback.answer("Удалено" if deleted else "Задание не найдено")
    if callback.message is not None:
        items, pages = await db.paginate_admin_tasks(1, TASKS_PER_PAGE)
        page = max(1, min(1, pages))
        await safe_edit(
            callback.message,
            "🗂 Задание удалено.\n\n"
            f"Всего заданий: <b>{await db.count_tasks(active_only=False)}</b> · "
            f"страница <b>{page}/{pages}</b>",
            admin_tasks_list(items, page, pages),
        )

@router.callback_query(F.data.startswith("admin:task:delete:"))
async def cb_admin_task_delete(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    task_id = _int(parts[3]) if len(parts) > 3 else None
    if task_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    task = await db.get_task(task_id)
    if task is None:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await safe_edit(
            callback.message,
            f"🗑 Удалить задание #{task_id} «{escape(truncate(str(task['title']), 60))}»?\n\n"
            "Действие необратимо.",
            admin_task_confirm_delete(task_id),
        )

@router.callback_query(F.data.startswith("admin:task:toggle:"))
async def cb_admin_task_toggle(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    task_id = _int(parts[3]) if len(parts) > 3 else None
    if task_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    task = await db.get_task(task_id)
    if task is None:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    new_state = not bool(task.get("is_active"))
    await db.update_task(task_id, is_active=new_state)
    fresh = await db.get_task(task_id)
    await callback.answer("Включено" if new_state else "Выключено")
    if callback.message is not None and fresh is not None:
        await safe_edit(callback.message, _task_card_text(fresh), admin_task_view(fresh))

@router.callback_query(F.data.regexp(r"^admin:task:\d+$"))
async def cb_admin_task_view(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    task_id = _int(parts[2]) if len(parts) > 2 else None
    if task_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    task = await db.get_task(task_id)
    if task is None:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await safe_edit(callback.message, _task_card_text(task), admin_task_view(task))

# ------------------------------------------------------------- заявки на вывод
@router.callback_query(F.data == "admin:withdrawals")
async def cb_admin_withdrawals(callback: CallbackQuery, db: Any) -> None:
    await callback.answer()
    if callback.message is not None:
        await _show_withdrawals(callback.message, db, 1)

@router.callback_query(F.data.startswith("admin:withdrawals:page:"))
async def cb_admin_withdrawals_page(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    page = _int(parts[3]) if len(parts) > 3 else None
    if page is None:
        await callback.answer("Некорректная страница", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _show_withdrawals(callback.message, db, page)

@router.callback_query(F.data.startswith("admin:wd:approve:"))
async def cb_admin_wd_approve(callback: CallbackQuery, db: Any, bot: Bot) -> None:
    parts = _parts(callback.data or "")
    withdrawal_id = _int(parts[3]) if len(parts) > 3 else None
    if withdrawal_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    result = await db.approve_withdrawal(withdrawal_id, callback.from_user.id)
    if result is None:
        await callback.answer("Заявка уже обработана", show_alert=True)
    else:
        await callback.answer("Выплачено")
        try:
            await bot.send_message(
                int(result["user_id"]),
                "✅ Твоя заявка на вывод "
                f"<b>{format_units(int(result['amount']))}</b> ⭐ ({result.get('gift', 'подарок')}) выплачена!\n\n"
                "Спасибо за работу 💫",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.debug("Не удалось уведомить пользователя %s", result["user_id"])
        await _finish_withdrawal_decision(
            callback, db, withdrawal_id, approved=True, withdrawal=result
        )

@router.callback_query(F.data.startswith("admin:wd:reject:"))
async def cb_admin_wd_reject(callback: CallbackQuery, db: Any, bot: Bot) -> None:
    parts = _parts(callback.data or "")
    withdrawal_id = _int(parts[3]) if len(parts) > 3 else None
    if withdrawal_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    result = await db.reject_withdrawal(withdrawal_id, callback.from_user.id)
    if result is None:
        await callback.answer("Заявка уже обработана", show_alert=True)
    else:
        await callback.answer("Отклонено, Stars возвращены")
        try:
            await bot.send_message(
                int(result["user_id"]),
                "❌ Заявка на вывод отклонена администратором.\n"
                f"Stars <b>{format_units(int(result['amount']))}</b> ⭐ возвращены на баланс.",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.debug("Не удалось уведомить пользователя %s", result["user_id"])
        await _finish_withdrawal_decision(
            callback, db, withdrawal_id, approved=False, withdrawal=result
        )

async def _finish_withdrawal_decision(
    callback: CallbackQuery,
    db: Any,
    withdrawal_id: int,
    *,
    approved: bool,
    withdrawal: dict[str, Any],
) -> None:
    if callback.message is None:
        return
    if str(callback.message.chat.type) == "channel":
        amount = format_units(int(withdrawal["amount"]))
        user_id = int(withdrawal["user_id"])
        gift = withdrawal.get("gift", "Подарок")
        admin_name = escape(callback.from_user.full_name or str(callback.from_user.id))
        if approved:
            text = (
                f"✅ <b>Заявка #{withdrawal_id} выплачена</b>\n\n"
                f"👤 Пользователь: <code>{user_id}</code>\n"
                f"🎁 Подарок: <b>{gift}</b>\n"
                f"⭐ Сумма: <b>{amount}</b> ⭐\n"
                f"🛠 Подтвердил: {admin_name}"
            )
        else:
            text = (
                f"❌ <b>Заявка #{withdrawal_id} отклонена</b>\n\n"
                f"👤 Пользователь: <code>{user_id}</code>\n"
                f"⭐ Stars <b>{amount}</b> возвращены на баланс\n"
                f"🛠 Отклонил: {admin_name}"
            )
        await safe_edit(callback.message, text, None)
    else:
        await _show_withdrawals(callback.message, db, 1)

# ------------------------------------------------------------------ пользователи
@router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery, db: Any) -> None:
    await callback.answer()
    if callback.message is not None:
        await _show_users(callback.message, db, 1)

@router.callback_query(F.data.startswith("admin:users:page:"))
async def cb_admin_users_page(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    page = _int(parts[3]) if len(parts) > 3 else None
    if page is None:
        await callback.answer("Некорректная страница", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _show_users(callback.message, db, page)

@router.callback_query(F.data == "admin:user:search")
async def cb_admin_user_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.user_search)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "🔎 Отправь ID пользователя или @username.",
            reply_markup=_cancel_kb("admin:users:page:1"),
        )

@router.message(AdminStates.user_search)
async def admin_user_search(message: Message, db: Any, state: FSMContext) -> None:
    query = (message.text or "").strip()
    user = await db.find_user(query)
    if user is None:
        await message.answer("Пользователь не найден. Попробуй другой ID/@username или /cancel.")
        return
    await state.clear()
    await _send_user_card(message, db, user)

async def _send_user_card(message: Message, db: Any, user: dict[str, Any]) -> None:
    refs = await db.get_referral_stats(int(user["id"]))
    totals = await db.get_withdrawal_totals(int(user["id"]))
    await message.answer(
        _user_card_text(user, refs, totals),
        reply_markup=admin_user_card(user),
    )

@router.callback_query(F.data.regexp(r"^admin:user:\d+$"))
async def cb_admin_user_view(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    user_id = _int(parts[2]) if len(parts) > 2 else None
    if user_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    user = await db.get_user(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    refs = await db.get_referral_stats(user_id)
    totals = await db.get_withdrawal_totals(user_id)
    await callback.answer()
    if callback.message is not None:
        await safe_edit(
            callback.message,
            _user_card_text(user, refs, totals),
            admin_user_card(user),
        )

@router.callback_query(F.data.startswith("admin:user:ban:"))
async def cb_admin_user_ban(callback: CallbackQuery, db: Any) -> None:
    await _admin_user_set_blocked(callback, db, blocked=True)

@router.callback_query(F.data.startswith("admin:user:unban:"))
async def cb_admin_user_unban(callback: CallbackQuery, db: Any) -> None:
    await _admin_user_set_blocked(callback, db, blocked=False)

async def _admin_user_set_blocked(callback: CallbackQuery, db: Any, blocked: bool) -> None:
    parts = _parts(callback.data or "")
    user_id = _int(parts[3]) if len(parts) > 3 else None
    if user_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    user = await db.get_user(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await db.set_user_blocked(user_id, blocked)
    fresh = await db.get_user(user_id) or user
    await callback.answer("Заблокирован" if blocked else "Разблокирован")
    if callback.message is not None:
        refs = await db.get_referral_stats(user_id)
        totals = await db.get_withdrawal_totals(user_id)
        await safe_edit(
            callback.message,
            _user_card_text(fresh, refs, totals),
            admin_user_card(fresh),
        )

@router.callback_query(F.data.startswith("admin:user:grant:"))
async def cb_admin_user_grant(callback: CallbackQuery, db: Any, state: FSMContext) -> None:
    parts = _parts(callback.data or "")
    user_id = _int(parts[3]) if len(parts) > 3 else None
    if user_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    user = await db.get_user(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await state.set_state(AdminStates.user_grant)
    await state.update_data(user_id=user_id)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"🎁 Начисление Stars пользователю <code>{user_id}</code>.\n"
            "Отправь сумму в Stars (например 5).",
            reply_markup=_cancel_kb(f"admin:user:{user_id}"),
        )

@router.message(AdminStates.user_grant)
async def admin_user_grant(message: Message, db: Any, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        await state.clear()
        await message.answer("Пользователь не выбран. Открой раздел заново.")
        return
    try:
        amount = parse_stars(message.text or "")
    except StarsParseError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}")
        return
    balance = await db.add_balance(int(user_id), amount, "admin", "Начисление администратором")
    await state.clear()
    await message.answer(
        f"✅ Начислено <b>{format_units(amount)}</b> ⭐ пользователю "
        f"<code>{user_id}</code>.\nНовый баланс: <b>{format_units(balance)}</b> ⭐.",
        reply_markup=admin_back("admin:users:page:1", "← К пользователям"),
    )

# ---------------------------------------------------------------------- рассылка
@router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.broadcast_message)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "📢 <b>Рассылка</b>\n\n"
            "Отправь сообщение (текст, фото или видео). Оно будет скопировано "
            "всем пользователям. Для отмены — /cancel.",
            reply_markup=_cancel_kb("admin:main"),
        )

@router.message(AdminStates.broadcast_message)
async def admin_broadcast_capture(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.text is None and not message.photo and not message.video:
        await message.answer("Поддерживается текст, фото или видео. Отправь ещё раз или /cancel.")
        return
    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
    )
    await message.answer("📢 Подтверди рассылку:", reply_markup=admin_broadcast_confirm())

@router.callback_query(F.data == "admin:broadcast:send")
async def cb_admin_broadcast_send(
    callback: CallbackQuery, db: Any, settings: Any, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    from_chat_id = data.get("broadcast_chat_id")
    message_id = data.get("broadcast_message_id")
    if not from_chat_id or not message_id:
        await callback.answer("Сообщение для рассылки не найдено", show_alert=True)
        return
    await state.clear()
    await callback.answer("Рассылка запущена")
    if callback.message is not None:
        await safe_edit(callback.message, "📢 Рассылка выполняется…")

    user_ids = await db.get_active_user_ids()
    sent = failed = blocked = 0
    for user_id in user_ids:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=int(from_chat_id),
                message_id=int(message_id),
            )
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after))
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=int(from_chat_id),
                    message_id=int(message_id),
                )
                sent += 1
            except (TelegramForbiddenError, TelegramBadRequest):
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
            await db.set_user_blocked(user_id, True)
        except TelegramBadRequest:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)

    report = (
        "📢 <b>Рассылка завершена</b>\n\n"
        f"✅ Отправлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{failed}</b>\n"
        f"👥 Всего в базе: <b>{len(user_ids)}</b>"
    )
    try:
        await bot.send_message(callback.from_user.id, report)
    except (TelegramForbiddenError, TelegramBadRequest):
        logger.debug("Не удалось отправить отчёт о рассылке")

@router.callback_query(F.data == "admin:broadcast:cancel")
async def cb_admin_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Рассылка отменена")
    if callback.message is not None:
        await safe_edit(callback.message, "📢 Рассылка отменена.", admin_menu())

# --------------------------------------------------------------------- настройки
@router.callback_query(F.data == "admin:settings")
async def cb_admin_settings(callback: CallbackQuery, db: Any) -> None:
    await callback.answer()
    if callback.message is not None:
        await _show_settings(callback.message, db)

@router.callback_query(F.data.startswith("admin:setting:"))
async def cb_admin_setting(callback: CallbackQuery, db: Any, state: FSMContext) -> None:
    parts = _parts(callback.data or "")
    key = parts[2] if len(parts) > 2 else ""
    if key not in SETTINGS_TITLES:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return
    values = await get_settings_view(db)
    current = setting_display(key, values.get(key, "0"))
    await state.set_state(AdminStates.setting_value)
    await state.update_data(setting_key=key)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"⚙️ <b>{SETTINGS_TITLES[key]}</b>\n\n"
            f"Текущее значение: <b>{escape(current)}</b>\n\n"
            "Отправь новое значение. Для отмены — /cancel.",
            reply_markup=_cancel_kb("admin:settings"),
        )

@router.message(AdminStates.setting_value)
async def admin_setting_save(message: Message, db: Any, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("setting_key")
    if key not in SETTINGS_TITLES:
        await state.clear()
        await message.answer("Настройка не найдена, открой раздел заново.")
        return
    raw = (message.text or "").strip()
    try:
        value = setting_from_input(key, raw)
    except ValueError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}")
        return
    await db.set_setting(key, value)
    await state.clear()
    values = await get_settings_view(db)
    lines = ["⚙️ <b>Настройки</b>", ""]
    for item_key, title in SETTINGS_TITLES.items():
        lines.append(f"• {title}: <b>{setting_display(item_key, values.get(item_key, '0'))}</b>")
    lines.append(f"\n✅ «{SETTINGS_TITLES[key]}» сохранено.")
    await message.answer("\n".join(lines), reply_markup=admin_settings_menu(values))

# --------------------------------------------------------------- заказы трафика
@router.callback_query(F.data == "admin:orders")
async def cb_admin_orders(callback: CallbackQuery, db: Any) -> None:
    await callback.answer()
    if callback.message is not None:
        await _show_orders(callback.message, db, 1)

@router.callback_query(F.data.startswith("admin:orders:page:"))
async def cb_admin_orders_page(callback: CallbackQuery, db: Any) -> None:
    parts = _parts(callback.data or "")
    page = _int(parts[3]) if len(parts) > 3 else None
    if page is None:
        await callback.answer("Некорректная страница", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _show_orders(callback.message, db, page)

@router.callback_query(F.data.startswith("admin:order:done:"))
async def cb_admin_order_done(callback: CallbackQuery, db: Any, bot: Bot) -> None:
    await _admin_order_status(callback, db, bot, "completed")

@router.callback_query(F.data.startswith("admin:order:cancel:"))
async def cb_admin_order_cancel(callback: CallbackQuery, db: Any, bot: Bot) -> None:
    await _admin_order_status(callback, db, bot, "cancelled")

async def _admin_order_status(
    callback: CallbackQuery, db: Any, bot: Bot, status: str
) -> None:
    parts = _parts(callback.data or "")
    order_id = _int(parts[3]) if len(parts) > 3 else None
    if order_id is None:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    order = await db.get_order(order_id)
    if order is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await db.set_order_status(order_id, status)
    label = "запущен" if status == "completed" else "отменён"
    await callback.answer(f"Заказ #{order_id} {label}")
    try:
        await bot.send_message(
            int(order["user_id"]),
            f"🛒 Заказ <b>#{order_id}</b> {label} администратором.",
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        logger.debug("Не удалось уведомить пользователя %s", order["user_id"])
    if callback.message is not None:
        await _show_orders(callback.message, db, 1)

__all__ = ["denied_router", "router"]