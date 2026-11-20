"""Регистрация всех роутеров бота. Порядок важен: fallback — последним."""

from aiogram import Router

from bot.handlers import admin, common, subscription, traffic, user, withdraw


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(common.router)
    root.include_router(admin.router)
    root.include_router(admin.denied_router)
    root.include_router(subscription.router)
    root.include_router(user.router)
    root.include_router(withdraw.router)
    root.include_router(traffic.router)
    root.include_router(common.fallback_router)
    return root


__all__ = ["setup_routers"]
