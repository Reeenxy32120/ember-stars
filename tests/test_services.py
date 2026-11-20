"""Тесты сервисного слоя: тексты, настройки, проверка подписок, награды."""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest

from bot.database import Database
from bot.defaults import DEFAULT_TEXTS
from bot.services import (
    check_subscription,
    claim_task_reward,
    evaluate_sponsors,
    get_prices,
    get_ref_settings,
    get_settings_view,
    get_sponsor_channels,
    get_sponsor_gate_text,
    get_text,
    normalize_chat_ref,
    reset_text,
    set_text,
    setting_display,
    setting_from_input,
    verify_task,
)


class FakeBot:
    """Минимальная замена Bot для проверки getChatMember."""

    def __init__(
        self,
        status: str = "member",
        error: Exception | None = None,
        statuses: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.statuses = statuses or {}
        self.calls: list[tuple[str, int]] = []

    async def get_chat_member(self, chat_id, user_id):
        from tests.conftest import build_chat_member

        chat_ref = str(chat_id)
        self.calls.append((chat_ref, int(user_id)))
        if self.error is not None:
            raise self.error
        status = self.statuses.get(chat_ref, self.status)
        return build_chat_member(status, int(user_id))


async def test_texts_default_override_reset(db: Database) -> None:
    assert await get_text(db, "greeting") == DEFAULT_TEXTS["greeting"]
    await set_text(db, "greeting", "Новый текст {name}")
    assert await get_text(db, "greeting") == "Новый текст {name}"
    await reset_text(db, "greeting")
    assert await get_text(db, "greeting") == DEFAULT_TEXTS["greeting"]
    assert await get_text(db, "missing") == ""


async def test_settings_view_and_display(db: Database) -> None:
    values = await get_settings_view(db)
    assert values["ref_bonus_units"] == "50000"
    assert setting_display("ref_bonus_units", values["ref_bonus_units"]) == "5 ⭐"
    assert setting_display("ref_percent", "10") == "10"


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("ref_percent", "15", "15"),
        ("ref_tasks_required", "8", "8"),
        ("ref_bonus_units", "7.5", "75000"),
        ("min_withdraw_units", "100", "1000000"),
        ("price_view_units", "0.05", "500"),
        ("sponsor_channels", "@eezfo", "@eezfo"),
        ("sponsor_channels", "https://t.me/eezfo", "@eezfo"),
        ("sponsor_channels", "@eezfo, https://t.me/second", "@eezfo,@second"),
        ("sponsor_channels", "—", ""),
    ],
)
def test_setting_from_input(key: str, raw: str, expected: str) -> None:
    assert setting_from_input(key, raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("ref_percent", "abc"),
        ("ref_percent", "101"),
        ("ref_tasks_required", "0"),
        ("ref_tasks_required", "1.5"),
        ("ref_bonus_units", "-5"),
        ("ref_bonus_units", "0"),
        ("min_withdraw_units", ""),
        ("sponsor_channels", "просто текст"),
        ("sponsor_channels", "-1001234567890"),
    ],
)
def test_setting_from_input_invalid(key: str, raw: str) -> None:
    with pytest.raises(ValueError):
        setting_from_input(key, raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-1001234567890", "-1001234567890"),
        ("@channel", "@channel"),
        ("https://t.me/channel", "@channel"),
        ("t.me/channel?start=1", "@channel"),
        ("https://example.com/page", "https://example.com/page"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_chat_ref(raw, expected) -> None:
    assert normalize_chat_ref(raw) == expected


async def test_ref_settings_and_prices_update(db: Database) -> None:
    await db.set_setting("ref_bonus_units", "12345")
    await db.set_setting("ref_tasks_required", "3")
    await db.set_setting("ref_percent", "20")
    config = await get_ref_settings(db)
    assert config == {"bonus_units": 12345, "required_tasks": 3, "percent": 20}
    await db.set_setting("price_view_units", "500")
    assert (await get_prices(db))["view"] == 500


async def test_check_subscription_statuses() -> None:
    bot = FakeBot(status="member")
    assert await check_subscription(bot, "-100123", 777) is True
    assert bot.calls == [("-100123", 777)]

    bot = FakeBot(status="administrator")
    assert await check_subscription(bot, "https://t.me/channel", 777) is True
    assert bot.calls == [("@channel", 777)]

    bot = FakeBot(status="creator")
    assert await check_subscription(bot, "@private", 777) is True

    bot = FakeBot(status="left")
    assert await check_subscription(bot, "-100123", 777) is False

    bot = FakeBot(error=TelegramBadRequest(method=None, message="bad"))  # type: ignore[arg-type]
    assert await check_subscription(bot, "-100123", 777) is False

    assert await check_subscription(bot, None, 777) is False


async def test_verify_task_types() -> None:
    channel_task = {"type": "channel", "chat_id": "-100123"}
    assert await verify_task(FakeBot(status="member"), 777, channel_task) is True
    assert await verify_task(FakeBot(status="left"), 777, channel_task) is False

    bot_task = {"type": "bot", "chat_id": None}
    assert (
        await verify_task(FakeBot(error=RuntimeError("не должен вызываться")), 777, bot_task)
        is True
    )

    link_task = {"type": "link", "chat_id": None}
    assert await verify_task(FakeBot(), 777, link_task) is True


async def test_claim_task_reward_with_referral(db: Database) -> None:
    await db.get_or_create_user(1, "referrer", "Реферер")
    await db.get_or_create_user(2, "friend", "Друг")
    await db.set_referrer(2, 1)
    task_id = await db.create_task("Канал", "channel", "https://t.me/x", 10_000, "-100")
    task = await db.get_task(task_id)
    assert task is not None

    result = await claim_task_reward(db, 2, task)
    assert result["completed"] is True
    assert result["reward"] == 10_000
    assert result["payout"] is not None
    assert result["payout"]["percent_amount"] == 1000

    repeated = await claim_task_reward(db, 2, task)
    assert repeated["completed"] is False

    referrer = await db.get_user(1)
    assert referrer is not None
    assert referrer["balance"] == 1000


async def test_sponsor_channels_from_db(db: Database) -> None:
    assert await get_sponsor_channels(db) == []
    await db.set_setting("sponsor_channels", "@eezfo, https://t.me/second")
    assert await get_sponsor_channels(db) == ["@eezfo", "@second"]


async def test_evaluate_sponsors(db: Database) -> None:
    await db.set_setting("sponsor_channels", "@eezfo,@second")

    bot = FakeBot(statuses={"@eezfo": "member", "@second": "member"})
    missing, unverifiable = await evaluate_sponsors(bot, db, 777)
    assert (missing, unverifiable) == ([], [])

    bot = FakeBot(statuses={"@eezfo": "member", "@second": "left"})
    missing, unverifiable = await evaluate_sponsors(bot, db, 777)
    assert missing == ["@second"]
    assert unverifiable == []

    bot = FakeBot(error=TelegramBadRequest(method=None, message="forbidden"))  # type: ignore[arg-type]
    missing, unverifiable = await evaluate_sponsors(bot, db, 777)
    assert missing == []
    assert unverifiable == ["@eezfo", "@second"]


async def test_sponsor_gate_text(db: Database) -> None:
    await db.set_setting("sponsor_channels", "@eezfo")
    text = await get_sponsor_gate_text(db)
    assert "https://t.me/eezfo" in text
    assert "@eezfo" in text
    assert "Проверить" in text
