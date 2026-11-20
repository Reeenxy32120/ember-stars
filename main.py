"""Точка входа бота.

Запуск::

    python main.py

Перед запуском скопируйте .env.example в .env и укажите BOT_TOKEN и ADMIN_IDS.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.bootstrap import ensure_admins
from bot.config import load_settings
from bot.database import Database
from bot.handlers import setup_routers
from bot.handlers.common import on_error
from bot.middlewares.context import ContextMiddleware
from bot.middlewares.subscription import SubscriptionMiddleware

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="cancel", description="Отменить действие"),
]


def setup_logging(level: int = logging.INFO) -> None:
    # В фоновом/перенаправленном режиме Windows консоль может быть в cp1251,
    # а логи содержат кириллицу — принудительно пишем UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):  # pragma: no cover
            pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


async def main() -> None:
    setup_logging()
    settings = load_settings()
    if not settings.admin_ids:
        logger.warning(
            "ADMIN_IDS пуст: админ-панель будет недоступна. "
            "Укажите Telegram ID администраторов в .env."
        )

    db = Database(settings.db_path)
    await db.init()
    logger.info("База данных готова: %s", settings.db_path)

    # Спонсоры из .env применяются один раз (дальше значение живёт в админке).
    if settings.sponsor_channels and not await db.get_setting("sponsor_channels_seeded"):
        await db.set_setting("sponsor_channels", ",".join(settings.sponsor_channels))
        await db.set_setting("sponsor_channels_seeded", "1")
        logger.info("Спонсоры обязательной подписки: %s", ", ".join(settings.sponsor_channels))

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.errors.register(on_error)

    context_middleware = ContextMiddleware(db, settings)
    subscription_middleware = SubscriptionMiddleware()
    dispatcher.message.outer_middleware(context_middleware)
    dispatcher.message.outer_middleware(subscription_middleware)
    dispatcher.callback_query.outer_middleware(context_middleware)
    dispatcher.callback_query.outer_middleware(subscription_middleware)
    dispatcher.pre_checkout_query.outer_middleware(context_middleware)

    dispatcher.include_router(setup_routers())

    try:
        bot_info = await bot.get_me()
        if not settings.bot_username:
            settings.bot_username = bot_info.username or ""
        logger.info("Бот @%s (id=%s) запускается", settings.bot_username, bot_info.id)

        await ensure_admins(bot, db, settings)
        if settings.admin_ids:
            logger.info("Администраторы: %s", sorted(settings.admin_ids))
        else:
            logger.warning(
                "ADMIN_IDS пуст и бутстрап не сработал: админ-панель недоступна. "
                "Укажите ADMIN_IDS в .env или проверьте права бота в канале выплат %s.",
                settings.withdraw_channel or "(канал не задан)",
            )
        if settings.withdraw_channel:
            logger.info("Канал заявок на вывод: %s", settings.withdraw_channel)

        try:
            await bot.set_my_commands(BOT_COMMANDS)
        except Exception:  # pragma: no cover - не критично для работы
            logger.exception("Не удалось установить команды бота")

        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )
    finally:
        await bot.session.close()
        await db.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover
        print("Остановлено пользователем")
    except RuntimeError as exc:  # pragma: no cover - конфигурационные ошибки
        print(f"Ошибка запуска: {exc}")
        sys.exit(1)
    except TelegramAPIError as exc:  # pragma: no cover - неверный токен/сеть
        print(f"Ошибка Telegram API: {exc}. Проверьте BOT_TOKEN и доступ к api.telegram.org.")
        sys.exit(1)
