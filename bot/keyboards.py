"""Все inline-клавиатуры бота."""

from __future__ import annotations
from collections.abc import Sequence
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.defaults import (
    BROADCAST_PERCENTAGES,
    ORDER_STATUS_LABELS,
    PROJECT_CATEGORIES,
    SETTINGS_TITLES,
    STAR_UNIT_SETTINGS,
    TASK_TYPE_LABELS,
    TEXT_TITLES,
    WITHDRAW_GIFTS,
)
from bot.utils import STARS_SCALE, build_share_url, format_units, sponsor_url, truncate

NOOP_CALLBACK = "noop"

def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)

def _url_button(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)

def pagination_row(prefix: str, page: int, pages: int, suffix: str = "") -> list[InlineKeyboardButton]:
    left = _button("‹", f"{prefix}:{page - 1}{suffix}") if page > 1 else _button(" ", NOOP_CALLBACK)
    right = (
        _button("›", f"{prefix}:{page + 1}{suffix}")
        if page < pages
        else _button(" ", NOOP_CALLBACK)
    )
    return [left, _button(f"· {page}/{pages} ·", NOOP_CALLBACK), right]

# --------------------------------------------------------------- главное меню
def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [_button("🚀 Задания", "menu:tasks")],
        [
            _button("💰 Баланс", "menu:balance"),
            _button("👥 Пригласить друзей", "menu:referral"),
        ],
        [
            _button("🎁 Бонус", "menu:bonus"),
            _button("👤 Профиль", "menu:profile"),
        ],
        [_button("🛒 Покупка трафика", "menu:traffic")],
    ]
    if is_admin:
        rows.append([_button("⚙️ Админ-панель", "admin:main")])
    return _kb(rows)

def back_to_main(text: str = "← Главная") -> InlineKeyboardMarkup:
    return _kb([[_button(text, "menu:main")]])

# ---------------------------------------------------------------- ЕЖЕДНЕВНЫЙ БОНУС (КУБИК)
def daily_bonus_menu(can_claim: bool) -> InlineKeyboardMarkup:
    if can_claim:
        return _kb([
            [_button("🎲 Бросить кубик", "bonus:roll")],
            [_button("← Главная", "menu:main")]
        ])
    else:
        return _kb([
            [_button("⏳ Уже получен", NOOP_CALLBACK)],
            [_button("← Главная", "menu:main")]
        ])

# ---------------------------------------------------------------- Общий список заданий
def combined_tasks_menu(tasks: Sequence[dict], page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, task in enumerate(tasks, start=1):
        prefix = "📢" if task["type"] == "channel" else "🤖"
        title = truncate(task.get("title") or "Задание", 18)
        reward_str = f"{format_units(task['reward'])} ⭐"

        label = f"{prefix} {title} | {reward_str}"

        if task["source"] == "db":
            rows.append([_button(label, f"task:open:{task['id']}:tasks")])
        else:
            rows.append([_url_button(label, task["url"])])

    if pages > 1:
        rows.append(pagination_row("tasks:page", page, pages))

    rows.append([_button("✅ Проверить", "tasks:check")])
    rows.append([_button("🔄 Обновить", "tasks:refresh")]) # <-- ИСПРАВЛЕНО
    rows.append([_button("← Главная", "menu:main")])
    return _kb(rows)

# ---------------------------------------------------------------- Задания/Бонус (из БД)
def tasks_list(
    tasks: Sequence[dict], page: int, pages: int, origin: str = "tasks"
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, task in enumerate(tasks, start=1):
        done_mark = "✅ " if task.get("is_completed") else ""
        label = (
            f"{done_mark}{index}. {truncate(str(task['title']), 32)} · "
            f"{format_units(int(task['reward']))} ⭐"
        )
        rows.append([_button(label, f"task:open:{int(task['id'])}:{origin}")])
    rows.append(pagination_row("tasks:page", page, pages, suffix=f":{origin}"))
    rows.append([_button("🔄 Обновить", f"tasks:page:{origin}:{page}")])
    rows.append([_button("← Главная", "menu:main")])
    return _kb(rows)

def task_detail(task_id: int, origin: str, completed: bool = False) -> InlineKeyboardMarkup:
    if completed:
        check_button = _button("✅ Уже выполнено", "task:done")
    else:
        check_button = _button("✅ Проверить", f"task:check:{int(task_id)}:{origin}")
    return _kb(
        [
            [check_button],
            [_button("← К заданиям", f"tasks:page:{origin}:1")],
            [_button("🏠 Главная", "menu:main")],
        ]
    )

def tasks_back(origin: str = "tasks") -> InlineKeyboardMarkup:
    label = "← К бонусам" if origin == "bonus" else "← К заданиям"
    return _kb(
        [
            [_button(label, f"tasks:page:{origin}:1")],
            [_button("🏠 Главная", "menu:main")],
        ]
    )

# --------------------------------------------------------------------- баланс
def balance_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_button("🎁 Вывести звёзды", "withdraw:start")],
            [_button("🧾 История начислений", "balance:history")],
            [_button("← Главная", "menu:main")],
        ]
    )

def history_menu() -> InlineKeyboardMarkup:
    return _kb([[_button("← К балансу", "menu:balance")]])

# ------------------------------------------------------------------ ВЫВОД (ПОДАРКИ)
def withdraw_gifts_menu(user_balance: int, friends_count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []

    for gift in WITHDRAW_GIFTS:
        price_units = gift["price"] * STARS_SCALE
        is_locked = user_balance < price_units
        lock_mark = " 🔒" if is_locked else ""

        if friends_count < 3:
            lock_mark = " 🔒"

        label = f"{gift['emoji']} {gift['price']}⭐{lock_mark}"
        callback = f"withdraw:gift:{gift['id']}"

        current_row.append(_button(label, callback))

        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    rows.append([_button("📜 История выводов", "withdraw:history")])
    rows.append([_button("← Назад", "menu:balance")])
    return _kb(rows)

def withdraw_history_menu() -> InlineKeyboardMarkup:
    return _kb([[_button("← К подаркам", "withdraw:start")]])

def withdraw_cancel() -> InlineKeyboardMarkup:
    return _kb([[_button("← Отмена", "menu:balance")]])

def withdrawal_channel_decision(withdrawal_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                _button("✅ Подтвердить", f"admin:wd:approve:{int(withdrawal_id)}"),
                _button("❌ Отклонить", f"admin:wd:reject:{int(withdrawal_id)}"),
            ]
        ]
    )

def sponsor_gate(sponsors: Sequence[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, ref in enumerate(sponsors, start=1):
        label = "📢 Подписаться" if len(sponsors) == 1 else f"📢 Канал {index}"
        rows.append([_url_button(label, sponsor_url(ref))])
    rows.append([_button("✅ Проверить", "sub:check")])
    return _kb(rows)

# ------------------------------------------------------------------ рефералы
def referral_menu(ref_link: str, share_text: str) -> InlineKeyboardMarkup:
    return _kb(
        [
            [_url_button("👥 Поделиться ссылкой", build_share_url(ref_link, share_text))],
            [_button("← Главная", "menu:main")],
        ]
    )

# -------------------------------------------------------------------- профиль
LANGUAGE_LABELS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "ua": "🇺🇦 Українська"}

def profile_menu(language: str, notifications: bool, partner_url: str | None = None) -> InlineKeyboardMarkup:
    languages = [
        _button(
            ("✅ " if code == language else "") + label,
            f"profile:lang:{code}",
        )
        for code, label in LANGUAGE_LABELS.items()
    ]
    notifications_button = _button(
        "🔕 Выключить уведомления" if notifications else "🔔 Включить уведомления",
        "profile:notifications",
    )
    rows = [
        languages,
        [notifications_button],
        [
            _button("🎟 Ввести промокод", "profile:promo"),
            _url_button("🤝 Партнёрский канал", partner_url)
            if partner_url
            else _button("🤝 Партнёрский канал", "profile:partner"),
        ],
        [_button("← Главная", "menu:main")],
    ]
    return _kb(rows)

def promo_cancel() -> InlineKeyboardMarkup:
    return _kb([[_button("← Отмена", "profile:cancel")]])

# ------------------------------------------------------------ покупка трафика
def traffic_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_button("🛠 Купить задания", "traffic:buy:task")],
            [
                _button("📣 Купить рассылку", "traffic:buy:broadcast"),
                _button("👁 Купить просмотры", "traffic:buy:view"),
            ],
            [_button("📢 Купить показы", "traffic:buy:show")],
            [_button("📋 Мои заказы", "traffic:orders")],
            [_button("← Главная", "menu:main")],
        ]
    )

def traffic_categories() -> InlineKeyboardMarkup:
    rows = [
        [_button(label, f"traffic:category:{code}")]
        for code, (label, _multiplier) in PROJECT_CATEGORIES.items()
    ]
    rows.append([_button("← Назад", "menu:traffic")])
    return _kb(rows)

def traffic_audience(count_users: int, price_units: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for percent in BROADCAST_PERCENTAGES:
        recipients = max(1, count_users * percent // 100)
        total = price_units * recipients
        rows.append(
            [
                _button(
                    f"{percent}% · {recipients} чел. · {format_units(total)} ⭐",
                    f"traffic:audience:{percent}",
                )
            ]
        )
    rows.append([_button("← Назад", "menu:traffic")])
    return _kb(rows)

def traffic_cancel() -> InlineKeyboardMarkup:
    return _kb([[_button("← Отмена", "traffic:cancel")]])

def traffic_orders_menu() -> InlineKeyboardMarkup:
    return _kb([[_button("← В покупку трафика", "menu:traffic")]])

# --------------------------------------------------------------- админ-панель
def admin_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                _button("📝 Тексты", "admin:texts"),
                _button("📋 Задания", "admin:tasks"),
            ],
            [
                _button("📢 Рассылка", "admin:broadcast"),
                _button("👥 Пользователи", "admin:users"),
            ],
            [
                _button("💸 Заявки на вывод", "admin:withdrawals"),
                _button("⚙️ Настройки", "admin:settings"),
            ],
            [
                _button("🎟 Промокоды", "admin:promo"),
                _button("🛒 Заказы трафика", "admin:orders"),
            ],
            [_button("← Главное меню", "menu:main")],
        ]
    )

def admin_promo_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_button("➕ Создать промокод", "admin:promo:add")],
            [_button("← Назад", "admin:main")],
        ]
    )

def admin_back(target: str = "admin:main", text: str = "← Назад") -> InlineKeyboardMarkup:
    return _kb([[_button(text, target)]])

def admin_texts_menu() -> InlineKeyboardMarkup:
    keys = list(TEXT_TITLES)
    rows = [
        [_button(f"📝 {TEXT_TITLES[key]}", f"admin:text:{key}")]
        for key in keys
    ]
    rows.append([_button("← Назад", "admin:main")])
    return _kb(rows)

def admin_text_edit_menu(key: str) -> InlineKeyboardMarkup:
    return _kb(
        [
            [_button("♻️ Сбросить на стандартный", f"admin:text:reset:{key}")],
            [_button("← К текстам", "admin:texts")],
        ]
    )

def admin_tasks_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_button("➕ Добавить задание", "admin:task:add")],
            [_button("🗂 Все задания", "admin:tasks:page:1")],
            [_button("← Назад", "admin:main")],
        ]
    )

def admin_tasks_list(tasks: Sequence[dict], page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        status = "🟢" if task.get("is_active") else "🔴"
        bonus = "🎁" if task.get("is_bonus") else ""
        rows.append(
            [
                _button(
                    f"{status} #{task['id']} {bonus} {truncate(str(task['title']), 28)}",
                    f"admin:task:{int(task['id'])}",
                )
            ]
        )
    rows.append(pagination_row("admin:tasks:page", page, pages))
    rows.append([_button("← К заданиям", "admin:tasks")])
    return _kb(rows)

def admin_task_view(task: dict) -> InlineKeyboardMarkup:
    task_id = int(task["id"])
    rows = [
        [
            _button("✏️ Название", f"admin:task:edit:{task_id}:title"),
            _button("🔗 Ссылка", f"admin:task:edit:{task_id}:url"),
        ],
        [
            _button("⭐ Награда", f"admin:task:edit:{task_id}:reward"),
            _button("🆔 chat_id", f"admin:task:edit:{task_id}:chat_id"),
        ],
        [
            _button(
                "🔴 Выключить" if task.get("is_active") else "🟢 Включить",
                f"admin:task:toggle:{task_id}",
            )
        ],
        [_button("🗑 Удалить", f"admin:task:delete:{task_id}")],
        [_button("← К списку", "admin:tasks:page:1")],
    ]
    return _kb(rows)

def admin_task_confirm_delete(task_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [_button("✅ Да, удалить", f"admin:task:delete_confirm:{task_id}")],
            [_button("← Отмена", f"admin:task:{task_id}")],
        ]
    )

def admin_task_type_menu() -> InlineKeyboardMarkup:
    rows = [
        [_button(label, f"admin:task:type:{code}")]
        for code, label in TASK_TYPE_LABELS.items()
    ]
    rows.append([_button("← Отмена", "admin:tasks")])
    return _kb(rows)

def admin_task_bonus_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                _button("🎁 Бонусное", "admin:task:bonus:yes"),
                _button("📋 Обычное", "admin:task:bonus:no"),
            ],
            [_button("← Отмена", "admin:tasks")],
        ]
    )

def admin_withdrawals(items: Sequence[dict], page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        gift_name = item.get("gift", "Подарок")
        rows.append(
            [
                _button(f"✅ #{int(item['id'])} {gift_name}", f"admin:wd:approve:{int(item['id'])}"),
                _button(f"❌ #{int(item['id'])}", f"admin:wd:reject:{int(item['id'])}"),
            ]
        )
    rows.append(pagination_row("admin:withdrawals:page", page, pages))
    rows.append([_button("← Назад", "admin:main")])
    return _kb(rows)

def admin_users(items: Sequence[dict], page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user in items:
        name = user.get("username") or user.get("full_name") or str(user["id"])
        rows.append(
            [_button(f"👤 {truncate(name, 24)} · {format_units(int(user['balance']))} ⭐",
                     f"admin:user:{int(user['id'])}")]
        )
    rows.append(pagination_row("admin:users:page", page, pages))
    rows.append(
        [
            _button("🔎 Найти по ID/@username", "admin:user:search"),
            _button("← Назад", "admin:main"),
        ]
    )
    return _kb(rows)

def admin_user_card(user: dict) -> InlineKeyboardMarkup:
    user_id = int(user["id"])
    ban_button = (
        _button("✅ Разблокировать", f"admin:user:unban:{user_id}")
        if user.get("is_blocked")
        else _button("⛔ Заблокировать", f"admin:user:ban:{user_id}")
    )
    return _kb(
        [
            [ban_button],
            [_button("🎁 Начислить Stars", f"admin:user:grant:{user_id}")],
            [_button("← К пользователям", "admin:users:page:1")],
        ]
    )

def admin_broadcast_confirm() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_button("📢 Отправить всем", "admin:broadcast:send")],
            [_button("← Отмена", "admin:broadcast:cancel")],
        ]
    )

def admin_settings_menu(values: dict[str, str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, title in SETTINGS_TITLES.items():
        raw = values.get(key, "0")
        if key in STAR_UNIT_SETTINGS:
            try:
                shown = f"{format_units(int(raw))} ⭐"
            except (TypeError, ValueError):
                shown = str(raw)
        else:
            shown = str(raw)
        rows.append([_button(f"⚙️ {title}: {shown}", f"admin:setting:{key}")])
    rows.append([_button("← Назад", "admin:main")])
    return _kb(rows)

def admin_orders(items: Sequence[dict], page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for order in items:
        order_id = int(order["id"])
        status = ORDER_STATUS_LABELS.get(str(order["status"]), str(order["status"]))
        rows.append(
            [
                _button(
                    f"#{order_id} · {truncate(str(order.get('kind')), 7)} · {status}",
                    NOOP_CALLBACK,
                )
            ]
        )
        rows.append(
            [
                _button(f"✅ Запущен #{order_id}", f"admin:order:done:{order_id}"),
                _button(f"❌ Отменить #{order_id}", f"admin:order:cancel:{order_id}"),
            ]
        )
    rows.append(pagination_row("admin:orders:page", page, pages))
    rows.append([_button("← Назад", "admin:main")])
    return _kb(rows)

def cancel_state(text: str = "← Отмена", callback: str = "cancel:state") -> InlineKeyboardMarkup:
    return _kb([[_button(text, callback)]])

__all__ = [
    "admin_back", "admin_broadcast_confirm", "admin_menu", "admin_orders",
    "admin_promo_menu", "admin_settings_menu", "admin_task_bonus_menu",
    "admin_task_confirm_delete", "admin_task_type_menu", "admin_task_view",
    "admin_tasks_list", "admin_tasks_menu", "admin_text_edit_menu", "admin_texts_menu",
    "admin_user_card", "admin_users", "admin_withdrawals", "back_to_main",
    "balance_menu", "cancel_state", "combined_tasks_menu", "history_menu",
    "main_menu", "profile_menu", "promo_cancel", "referral_menu", "sponsor_gate",
    "task_detail", "tasks_back", "tasks_list", "traffic_audience", "traffic_cancel",
    "traffic_categories", "traffic_menu", "traffic_orders_menu", "withdraw_cancel",
    "withdrawal_channel_decision", "withdraw_gifts_menu", "withdraw_history_menu",
    "daily_bonus_menu",
]