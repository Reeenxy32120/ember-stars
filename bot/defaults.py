"""Тексты по умолчанию и настройки."""

from __future__ import annotations

DEFAULT_TEXTS: dict[str, str] = {
    "greeting": (
        "👋 <b>Привет, {name}!</b>\n\n"
        "Выполняй задания, получай Stars и приглашай друзей.\n"
        "Награда появится на балансе после проверки.\n\n"
        "💰 Доступно: <b>{balance}</b> ⭐\n"
        "✅ Выполнено заданий: <b>{tasks_done}</b>"
    ),
    "tasks_combined": (
        "🚀 <b>Задания</b>\n\n"
        "Выполни задания и нажми «✅ Проверить» внизу списка.\n\n"
        "Доступно: <b>{available}</b>\n"
        "Страница: <b>{page}/{pages}</b>"
    ),
    "tasks_empty": (
        "🙈 Пока нет доступных заданий.\n"
        "Загляни позже — новые задания появляются регулярно."
    ),
    "bonus": (
        "🎁 <b>Бонус</b>\n\n"
        "Выполни условия ниже. Награда за бонус доступна после проверки."
    ),
    "bonus_empty": "🎁 Сейчас нет доступных бонусных заданий. Загляни позже.",
    "balance": (
        "💰 <b>Баланс</b>\n\n"
        "⭐ Доступно: <b>{balance}</b>\n"
        "⏳ В заявках: <b>{pending}</b>\n"
        "✅ Выплачено: <b>{paid}</b>\n\n"
        "💡 Вывод от <b>{min_withdraw}</b> ⭐."
    ),
    "referral": (
        "👥 <b>Пригласить друзей</b>\n\n"
        "Приглашено: <b>{invited}</b>\n"
        "Заработано: <b>{earned}</b> ⭐\n\n"
        "🎁 Бонус за активного друга — <b>{bonus}</b> ⭐\n"
        "Друг должен выполнить <b>{tasks_required}</b> заданий.\n\n"
        "🤝 <b>{percent}%</b> от его наград за задания\n\n"
        "Твоя ссылка:\n{link}"
    ),
    "referral_join": (
        "🎉 Ты пришёл по приглашению друга!\n\n"
        "Выполни <b>{tasks_required}</b> заданий — и друг получит бонус, "
        "а ты начнёшь зарабатывать Stars."
    ),
    "profile": (
        "👤 <b>Профиль</b>\n\n"
        "🆔 ID: <code>{user_id}</code>\n"
        "🔤 Язык: <b>{language}</b>\n"
        "🔔 Уведомления о заданиях: <b>{notifications}</b>\n\n"
        "🎟 Есть промокод? Введи его кнопкой ниже."
    ),
    "traffic": (
        "🛒 <b>Покупка трафика</b>\n\n"
        "📝 Задания: <b>{task_price}</b> ⭐ за выполнение, минимум {min_tasks}.\n"
        "📣 Рассылка: <b>{broadcast_price}</b> ⭐ за получателя.\n"
        "👁 Просмотры: <b>{view_price}</b> ⭐ за просмотр.\n"
        "📢 Показы: <b>{show_price}</b> ⭐ за показ.\n\n"
        "Оплата звёздами Telegram. После оплаты заказ проверяет администратор."
    ),
    "withdraw_ask": (
        "💸 <b>Создать заявку на вывод</b>\n\n"
        "Сколько Stars вывести? Минимум — <b>{min_withdraw}</b> ⭐.\n"
        "Доступно — <b>{balance}</b> ⭐.\n\n"
        "Для отмены — /cancel."
    ),
    "withdraw_created": (
        "✅ Заявка на вывод <b>{amount}</b> ⭐ создана.\n\n"
        "Статус: ожидает проверки администратором."
    ),
    "withdraw_cancelled": "❌ Создание заявки отменено.",
    "sponsor_gate": (
        "🔒 <b>Доступ к боту</b>\n\n"
        "Чтобы пользоваться ботом, подпишись на спонсоров:\n"
        "{sponsors}\n\n"
        "После подписки нажми «✅ Проверить»."
    ),
    "help": (
        "ℹ️ <b>Помощь</b>\n\n"
        "/start — главное меню\n"
        "/cancel — отменить текущее действие\n\n"
        "Выполняй задания, приглашай друзей и выводи Stars от {min_withdraw} ⭐."
    ),
    "admin_denied": "⛔ У тебя нет доступа к админ-панели.",
    "unknown_command": "🤔 Не понимаю команду. Открой главное меню.",
}

TEXT_TITLES: dict[str, str] = {
    "greeting": "Приветствие главного меню",
    "tasks_combined": "Раздел «Задания»",
    "balance": "Раздел «Баланс»",
    "referral": "Реферальный раздел",
    "referral_join": "Сообщение при реф-переходе",
    "bonus": "Раздел «Бонус»",
    "profile": "Раздел «Профиль»",
    "traffic": "Раздел «Покупка трафика»",
    "withdraw_ask": "Запрос суммы вывода",
    "sponsor_gate": "Экран подписки на спонсоров",
}

EDITABLE_TEXTS: tuple[str, ...] = tuple(TEXT_TITLES)

DEFAULT_SETTINGS: dict[str, str] = {
    "ref_bonus_units": "50000",
    "ref_tasks_required": "6",
    "ref_percent": "10",
    "min_withdraw_units": "500000",
    "price_task_units": "6400",
    "price_broadcast_units": "250",
    "price_view_units": "345",
    "price_show_units": "860",
    "sponsor_channels": "",
    "botohub_reward_units": "10000",  # 1 ⭐
}

SETTINGS_TITLES: dict[str, str] = {
    "ref_bonus_units": "Бонус реферера",
    "ref_tasks_required": "Заданий для бонуса",
    "ref_percent": "Процент реферера",
    "min_withdraw_units": "Минимальный вывод",
    "price_task_units": "Цена задания",
    "price_broadcast_units": "Цена рассылки за получателя",
    "price_view_units": "Цена просмотра",
    "price_show_units": "Цена показа",
    "sponsor_channels": "Спонсоры (обязательная подписка)",
    "botohub_reward_units": "Награда за задание BotoHub",
}

STAR_UNIT_SETTINGS: frozenset[str] = frozenset(
    {
        "ref_bonus_units",
        "min_withdraw_units",
        "price_task_units",
        "price_broadcast_units",
        "price_view_units",
        "price_show_units",
        "botohub_reward_units",
    }
)

MIN_TASK_QUANTITY = 100
MIN_VIEW_QUANTITY = 100
MAX_PURCHASE_QUANTITY = 1_000_000
BROADCAST_PERCENTAGES: tuple[int, ...] = (25, 50, 75, 100)

PROJECT_CATEGORIES: dict[str, tuple[str, int]] = {
    "usual": ("✅ Обычный проект", 1),
    "risky": ("🎰 Казино, ставки, рискованный (x5)", 5),
}

TASK_TYPE_LABELS: dict[str, str] = {
    "channel": "📢 Подписка на канал",
    "bot": "🤖 Переход в бота",
    "link": "🔗 Переход по ссылке",
}

ORDER_KIND_LABELS: dict[str, str] = {
    "task": "📝 Задания",
    "broadcast": "📣 Рассылка",
    "view": "👁 Просмотры",
    "show": "📢 Показы",
}

ORDER_STATUS_LABELS: dict[str, str] = {
    "pending": "⏳ Ожидает оплаты",
    "paid": "✅ Оплачен, ожидает запуска",
    "running": "🚀 Выполняется",
    "completed": "🏁 Выполнен",
    "cancelled": "❌ Отменён",
}

BROADCAST_STATUS_LABELS: dict[str, str] = {
    "pending": "⏳ Ожидает",
    "paid": "✅ Выплачено",
    "rejected": "❌ Отклонена",
}