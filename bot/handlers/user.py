"""Пользовательские хендлеры: старт, меню, задания, баланс, рефералы."""

from __future__ import annotations

import asyncio
import html
import logging
import math
import re
import time
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.bootstrap import promote_first_user
from bot.defaults import TASK_TYPE_LABELS
from bot.keyboards import (
    LANGUAGE_LABELS,
    back_to_main,
    balance_menu,
    combined_tasks_menu,
    daily_bonus_menu,
    history_menu,
    main_menu,
    profile_menu,
    promo_cancel,
    referral_menu,
    sponsor_gate,
    task_detail,
    tasks_back,
    tasks_list,
)
from bot.services import (
    botohub_client,
    claim_task_reward,
    darkboost_client,
    evaluate_sponsors,
    flyer_client,
    get_min_withdraw_units,
    get_ref_settings,
    get_sponsor_channels,
    get_sponsor_gate_text,
    get_text,
    piarflow_client,
    tgrass_client,
    verify_task,
)
from bot.states import PromoStates
from bot.utils import (
    STARS_SCALE,
    TRANSACTION_KIND_LABELS,
    build_ref_link,
    escape,
    format_dt,
    format_units,
    render,
    safe_edit,
    truncate,
)

logger = logging.getLogger(__name__)

router = Router(name="user")

TASKS_PER_PAGE = 10
API_TIMEOUT = 15.0

# --- КЭШ ЗАДАНИЙ ---
API_CACHE: dict[int, tuple[float, list[dict]]] = {}
CACHE_TTL = 60  # 1 минута — задания должны обновляться быстрее

# --- СЕССИИ DARKBOOST ---
DARKBOOST_SESSIONS: dict[int, dict[str, Any]] = {}

# --- СЕССИИ PIARFLOW (pending-ссылки, ожидающие проверки) ---
PIARFLOW_PENDING: dict[int, set[str]] = {}


# --------------------------------------------------------------------- helpers
def is_admin(settings: Any, user_id: int) -> bool:
    return int(user_id) in getattr(settings, "admin_ids", frozenset())


def _piarflow_title(link: str) -> str:
    """Достаёт @username из ссылки вида https://t.me/username."""
    match = re.search(r"t\.me/([A-Za-z0-9_]+)", link or "")
    if match:
        return f"@{match.group(1)}"
    return "Спонсор PiarFlow"


async def main_menu_text(db: Any, user: dict[str, Any]) -> str:
    template = await get_text(db, "greeting")
    name = user.get("full_name") or user.get("username") or "друг"
    return render(
        template,
        name=escape(truncate(str(name), 64)),
        balance=format_units(int(user.get("balance", 0))),
        tasks_done=int(user.get("tasks_done", 0)),
    )


async def _show_main_menu(
    message: Message, db: Any, settings: Any, user: dict[str, Any], *, edit: bool = True
) -> None:
    text = await main_menu_text(db, user)
    keyboard = main_menu(is_admin(settings, int(user["id"])))
    if edit:
        await safe_edit(message, text, keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)


def _parse_callback(data: str, min_parts: int, max_parts: int | None = None) -> list[str] | None:
    parts = (data or "").split(":")
    if len(parts) < min_parts:
        return None
    if max_parts is not None and len(parts) > max_parts:
        return None
    return parts


def _task_card_text(task: dict[str, Any], completed: bool) -> str:
    url = str(task.get("url") or "")
    link_line = f"🔗 {html.escape(url)}" if url else "🔗 Ссылка не указана"
    lines = [
        f"🚀 <b>{escape(truncate(str(task['title']), 120))}</b>",
        "",
        f"📌 Тип: <b>{TASK_TYPE_LABELS.get(str(task.get('type')), str(task.get('type')))}</b>",
        f"💰 Награда: <b>{format_units(int(task['reward']))}</b> ⭐",
        link_line,
    ]
    if task.get("is_bonus"):
        lines.append("🎁 Бонусное задание")
    if completed:
        lines.append("\n✅ Задание уже выполнено")
    else:
        lines.append("\nВыполни условия и нажми «Проверить».")
    return "\n".join(lines)


async def _notify_referrer(
    bot: Bot, referrer_id: int, payout: dict[str, Any], task_reward: int
) -> None:
    messages: list[str] = []
    if int(payout.get("percent_amount") or 0) > 0:
        messages.append(
            "🤝 Твой реферал выполнил задание!\n\n"
            f"💰 Тебе начислено <b>{format_units(int(payout['percent_amount']))}</b> ⭐ "
            f"(процент от награды {format_units(task_reward)} ⭐)."
        )
    if int(payout.get("bonus_amount") or 0) > 0:
        messages.append(
            "🎁 Бонус за активного друга!\n\n"
            f"Друг выполнил <b>{int(payout.get('tasks_done', 0))}</b> заданий — "
            f"тебе начислено <b>{format_units(int(payout['bonus_amount']))}</b> ⭐."
        )
    for text in messages:
        try:
            await bot.send_message(referrer_id, text)
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.debug("Не удалось уведомить реферера %s", referrer_id)


# ------------------------------------------------------------------ Загрузка заданий
async def _fetch_tgrass(user_id: int, from_user: Any, db: Any) -> tuple[list[dict], Any, str]:
    res = await tgrass_client.get_offers(
        user_id=user_id,
        lang=from_user.language_code or "ru",
        is_premium=from_user.is_premium or False,
        username=from_user.username,
    )
    reward_units = await db.get_int_setting("tgrass_reward_units", 20000)

    result = []
    for o in res.get("offers", []):
        if o.get("subscribed"):
            continue
        result.append(
            {
                "source": "tgrass",
                "id": str(o.get("offer_id")),
                "url": o.get("link"),
                "title": o.get("name") or ("Канал" if o.get("type") == "channel" else "Бот"),
                "type": o.get("type", "channel"),
                "reward": reward_units,
                "completed": False,
                "raw": o,
            }
        )
    return result, res, "Tgrass"


async def _fetch_botohub(user_id: int, db: Any) -> tuple[list[dict], Any, str]:
    all_tasks: list[dict] = []
    offset = 0
    limit = 50
    last_res: dict[str, Any] = {}
    while True:
        res = await botohub_client.get_tasks(user_id, limit=limit, offset=offset)
        last_res = res
        tasks = res.get("tasks", [])
        if not tasks:
            break
        reward_units = await db.get_int_setting("botohub_reward_units", 10000)
        for t in tasks:
            if t.get("completed"):
                continue
            all_tasks.append(
                {
                    "source": "botohub",
                    "id": str(t.get("resource_id") or t.get("url")),
                    "url": t["url"],
                    "title": t.get("title") or t.get("name") or "Спонсор BotoHub",
                    "type": "channel",
                    "reward": reward_units,
                    "completed": False,
                    "raw": t,
                }
            )
        if len(tasks) < limit:
            break
        offset += limit
        await asyncio.sleep(0.2)
    return all_tasks, last_res, "BotoHub"


async def _fetch_darkboost(user_id: int, db: Any) -> tuple[list[dict], Any, str]:
    try:
        res = await darkboost_client.get_sponsors(user_id, user_id, max_sponsors=10)
        sponsors = res.get("sponsors", [])
        session_id = res.get("session_id")

        reward_units = await db.get_int_setting("darkboost_reward_units", 20000)

        if sponsors and session_id and user_id not in DARKBOOST_SESSIONS:
            DARKBOOST_SESSIONS[user_id] = {
                "session_id": session_id,
                "count": len(sponsors),
            }
            logger.info(
                "DarkBoost: открыта сессия user=%s session=%s sponsors=%s",
                user_id, session_id, len(sponsors),
            )

        result = []
        for s in sponsors:
            result.append(
                {
                    "source": "darkboost",
                    "id": str(s.get("id")),
                    "url": s.get("link"),
                    "title": s.get("name") or "Спонсор DarkBoost",
                    "type": "channel",
                    "reward": reward_units,
                    "completed": False,
                    "raw": s,
                }
            )
        return result, res, "DarkBoost"
    except Exception as e:
        logger.exception("Ошибка DarkBoost: %s", e)
        raise


async def _fetch_piarflow(
    user_id: int, from_user: Any, db: Any
) -> tuple[list[dict], Any, str]:
    res = await piarflow_client.get_sponsors(
        user_id=user_id, chat_id=user_id, max_sponsors=10
    )
    reward_units = await db.get_int_setting("piarflow_reward_units", 20000)
    already_paid = await db.get_piarflow_completed_links(user_id)

    pending: set[str] = PIARFLOW_PENDING.setdefault(int(user_id), set())
    result: list[dict] = []

    for s in res.get("sponsors", []):
        link = str(s.get("link") or "")
        status = str(s.get("status") or "")
        if not link:
            continue
        if status == "not_counted":
            continue
        if link in already_paid:
            continue
        if status == "subscribed":
            # Уже подписан, но выплаты ещё не было — дадим проверить кнопкой «Проверить»
            pending.add(link)
            continue
        # status == "unsubscribed" — показываем пользователю
        pending.add(link)
        result.append(
            {
                "source": "piarflow",
                "id": link,
                "url": link,
                "title": _piarflow_title(link),
                "type": "channel",
                "reward": reward_units,
                "completed": False,
                "raw": s,
            }
        )
    return result, res, "PiarFlow"


async def _fetch_flyer(user_id: int, from_user: Any, db: Any) -> tuple[list[dict], Any, str]:
    return [], {"error": "Flyer API domain is dead"}, "Flyer"


async def _get_all_tasks(
    db: Any, user_id: int, from_user: Any, settings: Any, bot: Bot
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    errors: list[str] = []
    debug_reports: list[str] = []

    # 1. Задания из БД — всегда свежие, кэш к ним не применяется
    db_tasks, _ = await db.paginate_tasks(user_id, 1, 1000)
    for t in db_tasks:
        if t["is_completed"]:
            continue
        tasks.append(
            {
                "source": "db",
                "id": str(t["id"]),
                "url": t["url"],
                "title": t["title"],
                "type": t["type"],
                "reward": int(t["reward"]),
                "completed": False,
                "raw": t,
            }
        )

    # 2. Кэш API-заданий
    now = time.time()
    cached = API_CACHE.get(int(user_id))
    if cached is not None:
        cached_time, cached_tasks = cached
        if now - cached_time < CACHE_TTL:
            logger.debug(
                "Задания: отдаю API из кэша user=%s age=%.1fs count=%s",
                user_id, now - cached_time, len(cached_tasks),
            )
            tasks.extend(cached_tasks)
            return tasks

    # 3. API
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _fetch_tgrass(user_id, from_user, db),
                _fetch_botohub(user_id, db),
                _fetch_darkboost(user_id, db),
                _fetch_piarflow(user_id, from_user, db),
                _fetch_flyer(user_id, from_user, db),
                return_exceptions=True,
            ),
            timeout=API_TIMEOUT,
        )
        api_tasks: list[dict] = []
        for res in results:
            if isinstance(res, Exception):
                errors.append(str(res))
            else:
                fetched_tasks, raw_data, api_name = res
                api_tasks.extend(fetched_tasks)
                if not fetched_tasks:
                    debug_reports.append(f"<b>{api_name}</b> вернул пусто.")

        # Пустые ответы не кэшируем — иначе «нет заданий» залипает на CACHE_TTL
        if api_tasks:
            API_CACHE[int(user_id)] = (now, api_tasks)
            logger.info(
                "Задания: API обновлён user=%s count=%s errors=%s",
                user_id, len(api_tasks), len(errors),
            )
        else:
            API_CACHE.pop(int(user_id), None)
            logger.info(
                "Задания: API вернул пусто user=%s errors=%s",
                user_id, len(errors),
            )
        tasks.extend(api_tasks)

    except asyncio.TimeoutError:
        errors.append(f"Превышено время ожидания ({API_TIMEOUT} сек)")
        logger.warning("Задания: таймаут API user=%s", user_id)

    # 4. Диагностика админу
    if not tasks and int(user_id) in getattr(settings, "admin_ids", frozenset()):
        msg = "🛠 <b>Диагностика заданий</b>\n\n"
        if errors:
            msg += "⚠️ <b>Ошибки:</b>\n" + "\n".join(errors) + "\n\n"
        if debug_reports:
            msg += "ℹ️ <b>Ответы API:</b>\n" + "\n\n".join(debug_reports) + "\n\n"
        if not errors and not debug_reports:
            msg += "Нет заданий."
        try:
            await bot.send_message(user_id, msg)
        except (TelegramForbiddenError, TelegramBadRequest):
            pass

    return tasks


async def _show_tasks_page(
    message: Message,
    db: Any,
    settings: Any,
    user: dict[str, Any],
    from_user: Any,
    bot: Bot,
    page: int = 1,
) -> None:
    all_tasks = await _get_all_tasks(db, int(user["id"]), from_user, settings, bot)

    if not all_tasks:
        await safe_edit(message, await get_text(db, "tasks_empty"), back_to_main())
        return

    total = len(all_tasks)
    pages = max(1, math.ceil(total / TASKS_PER_PAGE))
    page = max(1, min(page, pages))

    start = (page - 1) * TASKS_PER_PAGE
    page_tasks = all_tasks[start:start + TASKS_PER_PAGE]

    text = render(
        await get_text(db, "tasks_combined"),
        available=total,
        page=page,
        pages=pages,
    )
    await safe_edit(message, text, combined_tasks_menu(page_tasks, page, pages))


# ----------------------------------------------------------------------- /start
@router.message(Command("start"))
async def cmd_start(
    message: Message,
    command: CommandObject,
    db: Any,
    settings: Any,
    user: dict[str, Any],
    state: FSMContext,
    bot: Bot,
) -> None:
    await state.clear()
    await promote_first_user(db, settings, int(user["id"]))
    payload = (command.args or "").strip()
    joined = False

    if payload.startswith("ref_"):
        try:
            referrer_id = int(payload[4:])
        except ValueError:
            referrer_id = None
        if (
            referrer_id is not None
            and referrer_id != int(user["id"])
            and user.get("ref_id") is None
        ):
            joined = await db.set_referrer(int(user["id"]), referrer_id)
            if joined:
                try:
                    await bot.send_message(
                        referrer_id,
                        "🎉 По твоей ссылке пришёл новый друг!\n"
                        f"Напомню: за {await _required_tasks_text(db)} выполненных "
                        "заданий ты получишь бонус, а пока — 10% от его наград.",
                    )
                except (TelegramForbiddenError, TelegramBadRequest):
                    pass

    fresh = await db.get_user(int(user["id"]))
    if fresh is None:
        await message.answer("Ошибка: профиль не найден. Попробуй /start ещё раз.")
        return

    if not is_admin(settings, int(fresh["id"])):
        sponsor_channels = await get_sponsor_channels(db)
        if sponsor_channels:
            missing, unverifiable = await evaluate_sponsors(bot, db, int(fresh["id"]))
            if missing:
                await message.answer(
                    await get_sponsor_gate_text(db),
                    reply_markup=sponsor_gate(sponsor_channels),
                )
                return

    text = await main_menu_text(db, fresh)
    if joined:
        join_template = await get_text(db, "referral_join")
        ref_settings = await get_ref_settings(db)
        text = (
            render(join_template, tasks_required=ref_settings["required_tasks"])
            + "\n\n"
            + text
        )

    await message.answer(text, reply_markup=main_menu(is_admin(settings, int(fresh["id"]))))


async def _required_tasks_text(db: Any) -> int:
    return int((await get_ref_settings(db))["required_tasks"])


# ------------------------------------------------------------------ меню/разделы
@router.callback_query(
    F.data.in_(
        {
            "menu:main",
            "menu:tasks",
            "menu:balance",
            "menu:referral",
            "menu:bonus",
            "menu:profile",
        }
    )
)
async def cb_main_menu(
    callback: CallbackQuery,
    db: Any,
    settings: Any,
    user: dict[str, Any],
    state: FSMContext,
    bot: Bot,
) -> None:
    await callback.answer()
    await state.clear()
    action = (callback.data or "menu:main").split(":")[1]
    fresh = await db.get_user(int(user["id"])) or user

    if callback.message is None:
        return

    if action == "tasks":
        is_cached = False
        cached = API_CACHE.get(int(fresh["id"]))
        if cached is not None and time.time() - cached[0] < CACHE_TTL:
            is_cached = True

        if not is_cached:
            await safe_edit(callback.message, "⏳ <i>Загружаем список заданий...</i>")

        await _show_tasks_page(
            callback.message, db, settings, fresh, callback.from_user, bot, page=1
        )
    elif action == "main":
        await _show_main_menu(callback.message, db, settings, fresh)
    elif action == "balance":
        await _show_balance(callback.message, db, fresh)
    elif action == "referral":
        await _show_referral(callback.message, db, settings, fresh)
    elif action == "bonus":
        await _show_daily_bonus(callback.message, db, fresh)
    elif action == "profile":
        await _show_profile(callback.message, db, settings, fresh)


# ------------------------------------------------------------------ КНОПКА ОБНОВИТЬ (СБРОС КЭША)
@router.callback_query(F.data == "tasks:refresh")
async def cb_tasks_refresh(
    callback: CallbackQuery, db: Any, settings: Any, user: dict[str, Any], bot: Bot
) -> None:
    await callback.answer("Обновляем...")
    if callback.message is None:
        return

    API_CACHE.pop(int(user["id"]), None)

    await safe_edit(callback.message, "⏳ <i>Ищем новые задания...</i>")
    fresh = await db.get_user(int(user["id"])) or user
    await _show_tasks_page(
        callback.message, db, settings, fresh, callback.from_user, bot, page=1
    )


# ------------------------------------------------------------------ ЕЖЕДНЕВНЫЙ БОНУС (КУБИК)
async def _show_daily_bonus(message: Message, db: Any, user: dict[str, Any]) -> None:
    cooldown = await db.get_bonus_cooldown(int(user["id"]))
    can_claim = cooldown <= 0

    if can_claim:
        status = "✅ Доступен прямо сейчас!"
        kb = daily_bonus_menu(True)
    else:
        hours = cooldown // 3600
        minutes = (cooldown % 3600) // 60
        time_str = f"{hours} ч {minutes} мин" if hours > 0 else f"{minutes} мин"
        status = f"⏳ Следующий бонус через <b>{time_str}</b>"
        kb = daily_bonus_menu(False)

    template = await get_text(db, "daily_bonus")
    text = render(template, status=status)
    await safe_edit(message, text, kb)


@router.callback_query(F.data == "bonus:roll")
async def cb_bonus_roll(
    callback: CallbackQuery, db: Any, user: dict[str, Any], bot: Bot
) -> None:
    cooldown = await db.get_bonus_cooldown(int(user["id"]))
    if cooldown > 0:
        await callback.answer("Бонус еще не восстановился!", show_alert=True)
        return

    await callback.answer("Бросаем кубик...")
    if callback.message is None:
        return

    msg = await bot.send_dice(callback.message.chat.id, emoji="🎲")
    dice_value = msg.dice.value

    reward_units = dice_value * STARS_SCALE
    await db.add_balance(
        int(user["id"]), reward_units, "daily_bonus", f"Кубик: выпало {dice_value}"
    )

    await asyncio.sleep(2.5)

    fresh = await db.get_user(int(user["id"])) or user
    await _show_daily_bonus(callback.message, db, fresh)

    await bot.send_message(
        callback.message.chat.id,
        f"🎉 Тебе выпало <b>{dice_value}</b>! Начислено <b>{dice_value}</b> ⭐.\n"
        f"Твой баланс: <b>{format_units(int(fresh['balance']))}</b> ⭐",
    )


# ---------------------------------------------------------------------- пагинация заданий
@router.callback_query(F.data.startswith("tasks:page:"))
async def cb_tasks_page(
    callback: CallbackQuery,
    db: Any,
    settings: Any,
    user: dict[str, Any],
    bot: Bot,
) -> None:
    """Пагинация и возврат в список заданий / бонусов.

    Поддерживаемые форматы callback_data:
        tasks:page:<N>            — обычный список заданий (старый формат)
        tasks:page:tasks:<N>      — обычный список заданий (из tasks_back/task_detail)
        tasks:page:bonus:<N>      — список бонусных заданий
    """
    await callback.answer()
    parts = _parse_callback(callback.data or "", 3, 4)
    if parts is None or callback.message is None:
        return

    origin = "tasks"
    if len(parts) == 4:
        origin = parts[2]
        raw_page = parts[3]
    else:
        raw_page = parts[2]

    try:
        page = int(raw_page)
    except ValueError:
        page = 1

    fresh = await db.get_user(int(user["id"])) or user

    if origin == "bonus":
        tasks, pages = await db.paginate_tasks(
            int(user["id"]), page, TASKS_PER_PAGE, bonus=True
        )
        if not tasks:
            await safe_edit(
                callback.message, await get_text(db, "bonus_empty"), back_to_main()
            )
        else:
            text = render(
                await get_text(db, "bonus"),
                available=await db.count_available_tasks(int(user["id"]), bonus=True),
                tasks_done=int(fresh["tasks_done"]),
                page=page,
                pages=pages,
            )
            await safe_edit(callback.message, text, tasks_list(tasks, page, pages, "bonus"))
        return

    # origin == "tasks" (и старый формат из 3 частей) — общий список
    await _show_tasks_page(
        callback.message, db, settings, fresh, callback.from_user, bot, page=page
    )


# ---------------------------------------------------------------------- общая кнопка «Проверить»
@router.callback_query(F.data == "tasks:check")
async def cb_tasks_check(
    callback: CallbackQuery, db: Any, settings: Any, user: dict[str, Any], bot: Bot
) -> None:
    await callback.answer("Проверяем...")
    if callback.message is None:
        return

    await safe_edit(callback.message, "⏳ <i>Проверяем выполнение заданий...</i>")

    API_CACHE.pop(int(user["id"]), None)

    await asyncio.gather(
        _process_tgrass_payments(db, int(user["id"]), callback.from_user),
        _process_botohub_payments(db, int(user["id"])),
        _process_darkboost_payments(db, int(user["id"])),
        _process_piarflow_payments(db, int(user["id"])),
    )

    fresh = await db.get_user(int(user["id"])) or user
    await _show_tasks_page(
        callback.message, db, settings, fresh, callback.from_user, bot, page=1
    )


# ---------------------------------------------------------------------- задания из БД
@router.callback_query(F.data.startswith("task:open:"))
async def cb_task_open(callback: CallbackQuery, db: Any, user: dict[str, Any]) -> None:
    parts = _parse_callback(callback.data or "", 4, 4)
    if parts is None or callback.message is None:
        return
    try:
        task_id = int(parts[2])
    except ValueError:
        return
    origin = parts[3] if len(parts) > 3 else "tasks"

    task = await db.get_task(task_id)
    if task is None or not task.get("is_active"):
        await callback.answer("Задание больше недоступно", show_alert=True)
        return
    completed = await db.is_task_completed(int(user["id"]), task_id)
    await callback.answer()
    await safe_edit(
        callback.message,
        _task_card_text(task, completed),
        task_detail(task_id, origin, completed),
    )


@router.callback_query(F.data.startswith("task:check:"))
async def cb_task_check(
    callback: CallbackQuery, db: Any, settings: Any, user: dict[str, Any], bot: Bot
) -> None:
    parts = _parse_callback(callback.data or "", 4, 4)
    if parts is None or callback.message is None:
        return
    try:
        task_id = int(parts[2])
    except ValueError:
        return
    origin = parts[3] if len(parts) > 3 else "tasks"

    task = await db.get_task(task_id)
    if task is None or not task.get("is_active"):
        await callback.answer("Задание больше недоступно", show_alert=True)
        return
    if await db.is_task_completed(int(user["id"]), task_id):
        await callback.answer("Ты уже выполнил это задание ✅", show_alert=True)
        return

    await callback.answer("Проверяем...")
    verified = await verify_task(bot, int(user["id"]), task)
    if not verified:
        await callback.answer(
            "Условия не выполнены. Подпишись и попробуй снова.", show_alert=True
        )
        return

    result = await claim_task_reward(db, int(user["id"]), task)
    if not result.get("completed"):
        await callback.answer("Ты уже выполнил это задание ✅", show_alert=True)
        return

    API_CACHE.pop(int(user["id"]), None)

    reward = int(result["reward"])
    payout = result.get("payout")
    if payout:
        await _notify_referrer(bot, int(payout["referrer_id"]), payout, reward)

    fresh = await db.get_user(int(user["id"])) or user
    success = (
        "✅ <b>Задание выполнено!</b>\n\n"
        f"💰 Начислено: <b>{format_units(reward)}</b> ⭐\n"
        f"⭐ Баланс: <b>{format_units(int(fresh['balance']))}</b> ⭐"
    )
    await safe_edit(callback.message, success, tasks_back(origin))


@router.callback_query(F.data == "task:done")
async def cb_task_done(callback: CallbackQuery) -> None:
    """Кнопка «✅ Уже выполнено» — раньше была без хендлера."""
    await callback.answer("Задание уже выполнено ✅", show_alert=True)


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    """Кнопки-разделители пагинации («· 1/1 ·») и пробелы."""
    await callback.answer()


# ------------------------------------------------------------------------ баланс
async def _show_balance(message: Message, db: Any, user: dict[str, Any]) -> None:
    totals = await db.get_withdrawal_totals(int(user["id"]))
    min_withdraw = await get_min_withdraw_units(db)
    template = await get_text(db, "balance")
    text = render(
        template,
        balance=format_units(int(user["balance"])),
        pending=format_units(totals["pending"]),
        paid=format_units(totals["paid"]),
        min_withdraw=format_units(min_withdraw),
    )
    await safe_edit(message, text, balance_menu())


@router.callback_query(F.data == "balance:history")
async def cb_balance_history(
    callback: CallbackQuery, db: Any, user: dict[str, Any]
) -> None:
    if callback.message is None:
        return
    transactions = await db.get_transactions(int(user["id"]), limit=15)
    if not transactions:
        lines = ["🧾 <b>История начислений</b>", "", "Начислений пока нет."]
    else:
        lines = ["🧾 <b>История начислений</b>", ""]
        for item in transactions:
            amount = int(item["amount"])
            sign = "+" if amount > 0 else "−"
            label = TRANSACTION_KIND_LABELS.get(str(item["kind"]), "Операция")
            lines.append(
                f"{label} · <b>{sign}{format_units(abs(amount))}</b> ⭐ · "
                f"{format_dt(str(item.get('created_at') or ''))}"
            )
    await callback.answer()
    await safe_edit(callback.message, "\n".join(lines), history_menu())


# ---------------------------------------------------------------------- рефералы
async def _show_referral(
    message: Message, db: Any, settings: Any, user: dict[str, Any]
) -> None:
    stats = await db.get_referral_stats(int(user["id"]))
    ref_settings = await get_ref_settings(db)
    username = getattr(settings, "bot_username", "") or "bot"
    ref_link = build_ref_link(username, int(user["id"]))
    template = await get_text(db, "referral")
    text = render(
        template,
        invited=stats["invited"],
        earned=format_units(stats["earned"]),
        bonus=format_units(ref_settings["bonus_units"]),
        tasks_required=ref_settings["required_tasks"],
        percent=ref_settings["percent"],
        link=html.escape(ref_link),
    )
    share_text = "Зарабатывай Stars за простые задания!"
    await safe_edit(message, text, referral_menu(ref_link, share_text))


# ------------------------------------------------------------------------ профиль
async def _show_profile(
    message: Message, db: Any, settings: Any, user: dict[str, Any]
) -> None:
    language = str(user.get("language") or "ru")
    notifications = bool(user.get("notifications", 1))
    template = await get_text(db, "profile")
    text = render(
        template,
        user_id=int(user["id"]),
        language=LANGUAGE_LABELS.get(language, language),
        notifications="включены" if notifications else "выключены",
    )
    partner_url = await db.get_setting("partner_channel_url")
    await safe_edit(
        message, text, profile_menu(language, notifications, partner_url or None)
    )


@router.callback_query(F.data.startswith("profile:lang:"))
async def cb_profile_language(
    callback: CallbackQuery, db: Any, settings: Any, user: dict[str, Any]
) -> None:
    try:
        language = (callback.data or "").split(":")[2]
    except IndexError:
        return
    if language not in LANGUAGE_LABELS:
        return
    await db.set_language(int(user["id"]), language)
    fresh = await db.get_user(int(user["id"])) or user
    await callback.answer("Язык сохранён")
    if callback.message is not None:
        await _show_profile(callback.message, db, settings, fresh)


@router.callback_query(F.data == "profile:notifications")
async def cb_profile_notifications(
    callback: CallbackQuery, db: Any, settings: Any, user: dict[str, Any]
) -> None:
    enabled = not bool(user.get("notifications", 1))
    await db.set_notifications(int(user["id"]), enabled)
    fresh = await db.get_user(int(user["id"])) or user
    await callback.answer("Уведомления включены" if enabled else "Уведомления выключены")
    if callback.message is not None:
        await _show_profile(callback.message, db, settings, fresh)


@router.callback_query(F.data == "profile:promo")
async def cb_profile_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PromoStates.waiting_code)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "🎟 Отправь промокод. Для отмены — /cancel.", reply_markup=promo_cancel()
        )


@router.callback_query(F.data == "profile:cancel")
async def cb_profile_cancel(
    callback: CallbackQuery,
    db: Any,
    settings: Any,
    user: dict[str, Any],
    state: FSMContext,
) -> None:
    await state.clear()
    fresh = await db.get_user(int(user["id"])) or user
    await callback.answer("Отменено")
    if callback.message is not None:
        await _show_profile(callback.message, db, settings, fresh)


@router.callback_query(F.data == "profile:partner")
async def cb_profile_partner(callback: CallbackQuery, db: Any) -> None:
    url = await db.get_setting("partner_channel_url")
    if url:
        await callback.answer("Ссылка в кнопке ниже")
    else:
        await callback.answer("Партнёрский канал пока не настроен", show_alert=True)


@router.message(PromoStates.waiting_code)
async def promo_code(
    message: Message, state: FSMContext, db: Any, settings: Any, user: dict[str, Any]
) -> None:
    code = (message.text or "").strip()
    if not code:
        await message.answer("Отправь промокод текстом.")
        return
    amount, status = await db.redeem_promo(int(user["id"]), code)
    await state.clear()
    if status == "ok":
        text = f"✅ Промокод активирован!\n\n💰 Начислено: <b>{format_units(amount)}</b> ⭐"
    elif status == "used":
        text = "⚠️ Ты уже использовал этот промокод."
    else:
        text = "❌ Неверный промокод или он уже закончился."
    fresh = await db.get_user(int(user["id"])) or user
    await message.answer(
        text, reply_markup=main_menu(is_admin(settings, int(fresh["id"])))
    )


# ------------------------------------------------------------------- Начисления
async def _process_tgrass_payments(db: Any, user_id: int, from_user: Any) -> None:
    try:
        res = await tgrass_client.get_offers(
            user_id=user_id,
            lang=from_user.language_code or "ru",
            is_premium=from_user.is_premium or False,
            username=from_user.username,
        )
        ref = await get_ref_settings(db)
        reward_units = await db.get_int_setting("tgrass_reward_units", 20000)

        for offer in res.get("offers", []):
            if not offer.get("subscribed"):
                continue
            offer_id = offer.get("offer_id")
            if offer_id is None:
                continue

            success = await db.complete_tgrass_offer(user_id, int(offer_id), reward_units)
            if success:
                logger.info("Tgrass: начислено user=%s offer=%s", user_id, offer_id)
                await db.apply_referral_rewards(
                    user_id,
                    reward_units,
                    required_tasks=ref["required_tasks"],
                    bonus=ref["bonus_units"],
                    percent=ref["percent"],
                )
    except Exception:
        logger.exception("Tgrass: ошибка при обработке платежей user=%s", user_id)


async def _process_botohub_payments(db: Any, user_id: int) -> None:
    try:
        res = await botohub_client.get_tasks(user_id, limit=50)
        reward_units = await db.get_int_setting("botohub_reward_units", 10000)
        ref = await get_ref_settings(db)

        for task in res.get("tasks", []):
            if not task.get("completed"):
                continue
            resource_id = task.get("resource_id") or task.get("url")
            if not resource_id:
                continue

            success = await db.complete_botohub_task(user_id, str(resource_id), reward_units)
            if success:
                logger.info("BotoHub: начислено user=%s resource=%s", user_id, resource_id)
                await db.apply_referral_rewards(
                    user_id,
                    reward_units,
                    required_tasks=ref["required_tasks"],
                    bonus=ref["bonus_units"],
                    percent=ref["percent"],
                )
    except Exception:
        logger.exception("BotoHub: ошибка при обработке платежей user=%s", user_id)


async def _process_darkboost_payments(db: Any, user_id: int) -> None:
    try:
        session_data = DARKBOOST_SESSIONS.get(user_id)
        if not session_data:
            logger.debug("DarkBoost: нет активной сессии user=%s", user_id)
            return

        res = await darkboost_client.check(user_id, session_data["session_id"])
        if res.get("status") == "ok":
            reward_units = await db.get_int_setting("darkboost_reward_units", 20000)
            total_reward = reward_units * session_data["count"]

            await db.add_balance(
                user_id,
                total_reward,
                "task",
                f"DarkBoost: {session_data['count']} спонсоров",
            )
            logger.info(
                "DarkBoost: начислено user=%s reward=%s sponsors=%s",
                user_id, total_reward, session_data["count"],
            )

            ref = await get_ref_settings(db)
            await db.apply_referral_rewards(
                user_id,
                total_reward,
                required_tasks=ref["required_tasks"],
                bonus=ref["bonus_units"],
                percent=ref["percent"],
            )

            DARKBOOST_SESSIONS.pop(user_id, None)
        else:
            logger.info("DarkBoost: статус не ok user=%s res=%s", user_id, res)
    except Exception as e:
        logger.exception("DarkBoost: ошибка при проверке сессии user=%s: %s", user_id, e)


async def _process_piarflow_payments(db: Any, user_id: int) -> None:
    try:
        links = list(PIARFLOW_PENDING.get(int(user_id), set()))
        if not links:
            logger.debug("PiarFlow: нет pending-ссылок user=%s", user_id)
            return

        res = await piarflow_client.check(user_id, links)
        reward_units = await db.get_int_setting("piarflow_reward_units", 20000)
        ref = await get_ref_settings(db)

        pending = PIARFLOW_PENDING.setdefault(int(user_id), set())
        for item in res.get("sponsors", []):
            link = str(item.get("link") or "")
            status = str(item.get("status") or "")
            if not link:
                continue
            if status == "subscribed":
                success = await db.complete_piarflow_offer(user_id, link, reward_units)
                if success:
                    logger.info("PiarFlow: начислено user=%s link=%s", user_id, link)
                    await db.apply_referral_rewards(
                        user_id,
                        reward_units,
                        required_tasks=ref["required_tasks"],
                        bonus=ref["bonus_units"],
                        percent=ref["percent"],
                    )
                pending.discard(link)
            elif status == "not_counted":
                logger.info("PiarFlow: not_counted user=%s link=%s", user_id, link)
                pending.discard(link)
            # status == "unsubscribed" — оставляем в pending, попробует позже
    except Exception:
        logger.exception("PiarFlow: ошибка при проверке платежей user=%s", user_id)