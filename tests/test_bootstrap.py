"""Тесты автоматического назначения администраторов."""

from __future__ import annotations

from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, User

from bot.bootstrap import ensure_admins, promote_first_user
from bot.config import Settings


def _owner(user_id: int, is_bot: bool = False):
    return ChatMemberOwner(
        status="creator",
        user=User(id=user_id, is_bot=is_bot, first_name="Owner"),
        is_anonymous=False,
    )


def _admin(user_id: int, is_bot: bool = False):
    return ChatMemberAdministrator.model_construct(
        status="administrator",
        user=User(id=user_id, is_bot=is_bot, first_name="Admin"),
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


class FakeBot:
    def __init__(self, members) -> None:
        self.members = members
        self.calls: list[str] = []

    async def get_chat_administrators(self, chat_id):
        self.calls.append(str(chat_id))
        return self.members


def _settings(**overrides) -> Settings:
    data = {
        "bot_token": "42:TEST",
        "admin_ids": frozenset(),
        "withdraw_channel": "@payments",
        "bootstrap_first_admin": True,
    }
    data.update(overrides)
    return Settings(**data)


async def test_admins_loaded_from_database(db) -> None:
    await db.set_setting("admin_ids", "10,20")
    settings = _settings()
    bot = FakeBot([])
    result = await ensure_admins(bot, db, settings)
    assert result == frozenset({10, 20})
    assert settings.admin_ids == frozenset({10, 20})
    assert bot.calls == []


async def test_admin_resolved_from_channel_owner(db) -> None:
    settings = _settings()
    bot = FakeBot([_owner(900), _admin(42, is_bot=True)])
    result = await ensure_admins(bot, db, settings)
    assert result == frozenset({900})
    assert bot.calls == ["@payments"]
    assert await db.get_setting("admin_ids") == "900"


async def test_existing_admin_ids_not_overwritten(db) -> None:
    settings = _settings(admin_ids=frozenset({5}))
    bot = FakeBot([_owner(900)])
    result = await ensure_admins(bot, db, settings)
    assert result == frozenset({5})
    assert bot.calls == []
    assert await db.get_setting("admin_ids") is None


async def test_bootstrap_disabled_does_nothing(db) -> None:
    settings = _settings(bootstrap_first_admin=False)
    bot = FakeBot([_owner(900)])
    result = await ensure_admins(bot, db, settings)
    assert result == frozenset()
    assert bot.calls == []


async def test_first_user_promoted(db) -> None:
    settings = _settings()
    assert await promote_first_user(db, settings, 777) is True
    assert settings.admin_ids == frozenset({777})
    assert await db.get_setting("admin_ids") == "777"
    # второй пользователь уже не становится админом
    assert await promote_first_user(db, settings, 888) is False


async def test_ensure_admins_handles_api_error(db) -> None:
    class BrokenBot:
        async def get_chat_administrators(self, chat_id):
            from aiogram.exceptions import TelegramBadRequest

            raise TelegramBadRequest(method=None, message="forbidden")  # type: ignore[arg-type]

    settings = _settings()
    result = await ensure_admins(BrokenBot(), db, settings)  # type: ignore[arg-type]
    assert result == frozenset()
    assert await db.get_setting("admin_ids") is None
