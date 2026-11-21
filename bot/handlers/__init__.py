"""Пакет хендлеров. Здесь собираются все роутеры."""

from aiogram import Router

from bot.handlers import admin, common, subscription, traffic, user, withdraw
from bot.handlers.common import fallback_router


def setup_routers() -> Router:
    """Собирает все роутеры в правильном порядке."""
    root_router = Router()

    # Сначала общие и пользовательские
    root_router.include_router(common.router)
    root_router.include_router(subscription.router)
    root_router.include_router(user.router)
    root_router.include_router(traffic.router)
    root_router.include_router(withdraw.router)  # Новый роутер вывода

    # Админку подключаем СТРОГО ДО denied_router, иначе кнопки будут выдавать "устарела"
    root_router.include_router(admin.router)
    root_router.include_router(admin.denied_router)

    # Фоллбэки в самом конце
    root_router.include_router(fallback_router)

    return root_router