"""Тестовые фикстуры: временная БД, фейковая сессия Telegram и Dispatcher.

Dispatcher и роутеры создаются один раз на процесс (aiogram запрещает
подключать один Router к двум родителям), а БД, настройки и бот подменяются
на каждый тест.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import TelegramMethod
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberRestricted,
    Message,
    Update,
    User,
)

from bot.config import Settings
from bot.database import Database
from bot.handlers import setup_routers
from bot.middlewares.context import ContextMiddleware
from bot.middlewares.subscription import SubscriptionMiddleware

ADMIN_ID = 900
USER_ID = 777

_DISPATCHER: Dispatcher | None = None
_MIDDLEWARE: ContextMiddleware | None = None
_SUBSCRIPTION: SubscriptionMiddleware | None = None
_ERRORS: list[BaseException] = []


def build_chat_member(status: str, user_id: int = USER_ID):
    """Собирает объект участника чата для любого статуса (без строгой валидации)."""
    user = User(id=user_id, is_bot=False, first_name="User")
    if status == "creator":
        return ChatMemberOwner(status="creator", user=user, is_anonymous=False)
    if status == "administrator":
        return ChatMemberAdministrator.model_construct(
            status="administrator",
            user=user,
            is_anonymous=False,
            can_be_edited=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=True,
            can_invite_users=True,
        )
    if status == "restricted":
        return ChatMemberRestricted.model_construct(
            status="restricted", user=user, is_member=True
        )
    if status == "member":
        return ChatMemberMember(status="member", user=user)
    return ChatMemberLeft(status="left", user=user)


class FakeSession(BaseSession):
    """Сессия без сети: записывает методы и отдаёт заготовленные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[Any] = []
        self.errors: dict[str, Exception] = {}
        self.chat_member_status = "member"
        self.chat_member_statuses: dict[str, str] = {}

    def request_names(self) -> list[str]:
        return [type(request).__name__ for request in self.requests]

    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ):
        yield b""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        self.requests.append(method)
        name = type(method).__name__
        if name in self.errors:
            raise self.errors[name]
        if name == "GetChatMember":
            chat_ref = str(getattr(method, "chat_id", ""))
            status = self.chat_member_statuses.get(chat_ref, self.chat_member_status)
            return build_chat_member(status)
        if name in {
            "SendMessage",
            "EditMessageText",
            "CopyMessage",
            "SendInvoice",
            "EditMessageCaption",
        }:
            return Message(
                message_id=1,
                date=datetime.now(tz=timezone.utc),
                chat=Chat(id=USER_ID, type="private"),
            )
        return True


def _ensure_dispatcher() -> Dispatcher:
    global _DISPATCHER, _MIDDLEWARE, _SUBSCRIPTION
    if _DISPATCHER is not None:
        return _DISPATCHER

    dispatcher = Dispatcher(storage=MemoryStorage())
    middleware = ContextMiddleware(db=None, settings=None)  # type: ignore[arg-type]
    subscription = SubscriptionMiddleware()
    _MIDDLEWARE = middleware
    _SUBSCRIPTION = subscription

    async def collect_error(event) -> None:
        _ERRORS.append(event.exception)

    dispatcher.errors.register(collect_error)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.message.outer_middleware(subscription)
    dispatcher.callback_query.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(subscription)
    dispatcher.pre_checkout_query.outer_middleware(middleware)
    dispatcher.include_router(setup_routers())
    _DISPATCHER = dispatcher
    return dispatcher


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        bot_token="42:TEST",
        admin_ids=frozenset({ADMIN_ID}),
        db_path=tmp_path / "test.sqlite3",
        bot_username="test_bot",
        support_contact="@support",
        withdraw_channel="@test_channel",
        bootstrap_first_admin=False,
    )


@pytest_asyncio.fixture
async def db(settings: Settings):
    database = Database(settings.db_path)
    await database.init()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def harness(settings: Settings, db: Database):
    dispatcher = _ensure_dispatcher()
    assert _MIDDLEWARE is not None
    assert _SUBSCRIPTION is not None
    _MIDDLEWARE.db = db
    _MIDDLEWARE.settings = settings
    _SUBSCRIPTION.reset_throttle()
    dispatcher.storage.storage.clear()
    _ERRORS.clear()

    session = FakeSession()
    bot = Bot(
        token="42:TEST",
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )

    async def feed(update: Update) -> None:
        await dispatcher.feed_update(bot, update)

    state = SimpleNamespace(
        bot=bot,
        session=session,
        dp=dispatcher,
        db=db,
        settings=settings,
        errors=_ERRORS,
        feed=feed,
    )
    try:
        yield state
    finally:
        await bot.session.close()


__all__ = [
    "ADMIN_ID",
    "USER_ID",
    "FakeSession",
    "build_chat_member",
]
