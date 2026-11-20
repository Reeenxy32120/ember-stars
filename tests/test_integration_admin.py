"""Интеграционные тесты админ-панели через настоящий Dispatcher."""

from __future__ import annotations

from tests.helpers import ADMIN_ID, USER_ID, callback_update, message_update


async def test_admin_denied_for_regular_user(harness) -> None:
    await harness.feed(message_update(1, user_id=USER_ID, text="/admin"))
    assert harness.errors == []
    sent = [r.text for r in harness.session.requests if type(r).__name__ == "SendMessage"]
    assert any("нет доступа" in text for text in sent)


async def test_admin_callback_denied_for_regular_user(harness) -> None:
    await harness.feed(callback_update(1, "admin:main", user_id=USER_ID))
    assert harness.errors == []
    answered = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "AnswerCallbackQuery"
    ]
    assert answered
    assert "нет доступа" in (answered[-1].text or "")


async def test_admin_main_menu_and_text_edit(harness) -> None:
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    assert harness.errors == []
    await harness.feed(callback_update(2, "admin:main", user_id=ADMIN_ID))
    await harness.feed(callback_update(3, "admin:texts", user_id=ADMIN_ID))
    await harness.feed(callback_update(4, "admin:text:greeting", user_id=ADMIN_ID))
    await harness.feed(message_update(5, user_id=ADMIN_ID, text="Привет, {name}! Ты в боте."))
    assert harness.errors == []
    assert await harness.db.get_setting("text:greeting") == "Привет, {name}! Ты в боте."
    # сброс
    await harness.feed(callback_update(6, "admin:text:reset:greeting", user_id=ADMIN_ID))
    assert harness.errors == []
    assert await harness.db.get_setting("text:greeting") is not None
    assert "Привет" in (await harness.db.get_setting("text:greeting") or "")


async def test_admin_task_crud(harness) -> None:
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:task:add", user_id=ADMIN_ID))
    await harness.feed(message_update(3, user_id=ADMIN_ID, text="Моё задание"))
    await harness.feed(message_update(4, user_id=ADMIN_ID, text="https://t.me/mychannel"))
    await harness.feed(callback_update(5, "admin:task:type:channel", user_id=ADMIN_ID))
    await harness.feed(message_update(6, user_id=ADMIN_ID, text="-1001234567890"))
    await harness.feed(message_update(7, user_id=ADMIN_ID, text="0.5"))
    await harness.feed(callback_update(8, "admin:task:bonus:no", user_id=ADMIN_ID))
    assert harness.errors == []

    tasks, _ = await harness.db.paginate_admin_tasks(1, 5)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["title"] == "Моё задание"
    assert task["reward"] == 5000
    assert task["chat_id"] == "-1001234567890"

    # редактирование награды
    await harness.feed(
        callback_update(9, f"admin:task:edit:{task['id']}:reward", user_id=ADMIN_ID)
    )
    await harness.feed(message_update(10, user_id=ADMIN_ID, text="1"))
    updated = await harness.db.get_task(task["id"])
    assert updated is not None
    assert updated["reward"] == 10000

    # выключение
    await harness.feed(callback_update(11, f"admin:task:toggle:{task['id']}", user_id=ADMIN_ID))
    updated = await harness.db.get_task(task["id"])
    assert updated is not None
    assert updated["is_active"] == 0

    # удаление
    await harness.feed(callback_update(12, f"admin:task:delete:{task['id']}", user_id=ADMIN_ID))
    await harness.feed(
        callback_update(13, f"admin:task:delete_confirm:{task['id']}", user_id=ADMIN_ID)
    )
    assert harness.errors == []
    assert await harness.db.get_task(task["id"]) is None


async def test_channel_decision_edits_channel_message(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, f"user{USER_ID}", "Test")
    await harness.db.add_balance(USER_ID, 500_000, "admin", "начисление")
    withdrawal_id = await harness.db.create_withdrawal(USER_ID, 500_000)
    assert withdrawal_id is not None

    await harness.feed(
        callback_update(
            1,
            f"admin:wd:approve:{withdrawal_id}",
            user_id=ADMIN_ID,
            chat_type="channel",
        )
    )
    assert harness.errors == []
    edits = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "EditMessageText"
    ]
    assert edits
    assert "выплачена" in edits[-1].text.lower()
    withdrawal = await harness.db.get_withdrawal(withdrawal_id)
    assert withdrawal is not None
    assert withdrawal["status"] == "paid"


async def test_channel_reject_refunds_and_edits(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, f"user{USER_ID}", "Test")
    await harness.db.add_balance(USER_ID, 500_000, "admin", "начисление")
    withdrawal_id = await harness.db.create_withdrawal(USER_ID, 500_000)
    assert withdrawal_id is not None

    await harness.feed(
        callback_update(
            1,
            f"admin:wd:reject:{withdrawal_id}",
            user_id=ADMIN_ID,
            chat_type="channel",
        )
    )
    assert harness.errors == []
    edits = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "EditMessageText"
    ]
    assert edits
    assert "отклонена" in edits[-1].text.lower()
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 500_000


async def test_admin_withdraw_approve(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, f"user{USER_ID}", "Test")
    await harness.db.add_balance(USER_ID, 500_000, "admin", "начисление")
    withdrawal_id = await harness.db.create_withdrawal(USER_ID, 500_000)
    assert withdrawal_id is not None
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:withdrawals", user_id=ADMIN_ID))
    assert harness.errors == []
    await harness.feed(callback_update(3, f"admin:wd:approve:{withdrawal_id}", user_id=ADMIN_ID))
    assert harness.errors == []

    withdrawal = await harness.db.get_withdrawal(withdrawal_id)
    assert withdrawal is not None
    assert withdrawal["status"] == "paid"
    # повторное подтверждение отклоняется
    await harness.feed(callback_update(4, f"admin:wd:approve:{withdrawal_id}", user_id=ADMIN_ID))
    assert harness.errors == []


async def test_admin_withdraw_reject_refunds(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, f"user{USER_ID}", "Test")
    await harness.db.add_balance(USER_ID, 500_000, "admin", "начисление")
    withdrawal_id = await harness.db.create_withdrawal(USER_ID, 500_000)
    assert withdrawal_id is not None
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:withdrawals", user_id=ADMIN_ID))
    await harness.feed(callback_update(3, f"admin:wd:reject:{withdrawal_id}", user_id=ADMIN_ID))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 500_000


async def test_admin_sponsors_setting(harness) -> None:
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:setting:sponsor_channels", user_id=ADMIN_ID))
    await harness.feed(message_update(3, user_id=ADMIN_ID, text="https://t.me/eezfo"))
    assert harness.errors == []
    assert await harness.db.get_setting("sponsor_channels") == "@eezfo"

    # очистка настройки отключает обязательную подписку
    await harness.feed(callback_update(4, "admin:setting:sponsor_channels", user_id=ADMIN_ID))
    await harness.feed(message_update(5, user_id=ADMIN_ID, text="—"))
    assert harness.errors == []
    assert await harness.db.get_setting("sponsor_channels") == ""


async def test_admin_settings_editing(harness) -> None:
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:settings", user_id=ADMIN_ID))
    await harness.feed(callback_update(3, "admin:setting:ref_percent", user_id=ADMIN_ID))
    await harness.feed(message_update(4, user_id=ADMIN_ID, text="20"))
    assert harness.errors == []
    assert await harness.db.get_int_setting("ref_percent") == 20
    # некорректное значение не сохраняется
    await harness.feed(callback_update(5, "admin:setting:ref_percent", user_id=ADMIN_ID))
    await harness.feed(message_update(6, user_id=ADMIN_ID, text="abc"))
    assert harness.errors == []
    assert await harness.db.get_int_setting("ref_percent") == 20


async def test_admin_users_browse_and_grant(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, f"user{USER_ID}", "Test")
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:users", user_id=ADMIN_ID))
    await harness.feed(callback_update(3, f"admin:user:{USER_ID}", user_id=ADMIN_ID))
    await harness.feed(callback_update(4, f"admin:user:grant:{USER_ID}", user_id=ADMIN_ID))
    await harness.feed(message_update(5, user_id=ADMIN_ID, text="5"))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 50_000

    await harness.feed(callback_update(6, f"admin:user:ban:{USER_ID}", user_id=ADMIN_ID))
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["is_blocked"] == 1
    await harness.feed(callback_update(7, f"admin:user:unban:{USER_ID}", user_id=ADMIN_ID))
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["is_blocked"] == 0


async def test_admin_user_search(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, "searchable", "Test")
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:user:search", user_id=ADMIN_ID))
    await harness.feed(message_update(3, user_id=ADMIN_ID, text="@searchable"))
    assert harness.errors == []
    assert any(
        "searchable" in request.text
        for request in harness.session.requests
        if type(request).__name__ == "SendMessage"
    )


async def test_admin_broadcast(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, f"user{USER_ID}", "Test")
    await harness.db.get_or_create_user(1111, "user1111", "Second")
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:broadcast", user_id=ADMIN_ID))
    await harness.feed(message_update(3, user_id=ADMIN_ID, text="Всем привет!"))
    await harness.feed(callback_update(4, "admin:broadcast:send", user_id=ADMIN_ID))
    assert harness.errors == []
    copied = [
        request for request in harness.session.requests if type(request).__name__ == "CopyMessage"
    ]
    # админ тоже зарегистрирован в БД (/admin создал запись) — рассылка идёт всем
    assert len(copied) == 3
    reports = [
        request.text
        for request in harness.session.requests
        if type(request).__name__ == "SendMessage" and "Рассылка завершена" in request.text
    ]
    assert reports


async def test_admin_orders_management(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, f"user{USER_ID}", "Test")
    order_id = await harness.db.create_order(USER_ID, "view", "https://t.me/post/1", 100, 34_500, 4)
    await harness.db.set_order_paid(order_id, 4)
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    await harness.feed(callback_update(2, "admin:orders", user_id=ADMIN_ID))
    await harness.feed(callback_update(3, f"admin:order:done:{order_id}", user_id=ADMIN_ID))
    assert harness.errors == []
    order = await harness.db.get_order(order_id)
    assert order is not None
    assert order["status"] == "completed"
