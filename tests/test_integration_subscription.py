"""Интеграционные тесты обязательной подписки на спонсоров."""

from __future__ import annotations

from tests.helpers import ADMIN_ID, USER_ID, callback_update, message_update

GREETING_MARKER = "Выполняй задания"
GATE_MARKER = "подпишись на спонсоров"


async def enable_gate(harness, status: str = "left") -> None:
    await harness.db.set_setting("sponsor_channels", "@eezfo")
    harness.session.chat_member_statuses["@eezfo"] = status


def sent_messages(harness) -> list:
    return [
        request
        for request in harness.session.requests
        if type(request).__name__ == "SendMessage"
    ]


async def test_start_shows_gate_before_menu(harness) -> None:
    await enable_gate(harness)
    await harness.feed(message_update(1, text="/start"))
    assert harness.errors == []

    messages = sent_messages(harness)
    gate = [m for m in messages if GATE_MARKER in (m.text or "").lower()]
    assert gate, "должен прийти экран подписки"
    assert not any(GREETING_MARKER in (m.text or "") for m in messages)

    buttons = [
        button for row in gate[0].reply_markup.inline_keyboard for button in row
    ]
    urls = [button.url for button in buttons if button.url]
    assert "https://t.me/eezfo" in urls
    assert any(button.callback_data == "sub:check" for button in buttons)

    user = await harness.db.get_user(USER_ID)
    assert user is not None, "регистрация должна произойти до проверки подписки"


async def test_check_after_subscribe_opens_menu(harness) -> None:
    await enable_gate(harness, status="left")
    await harness.feed(message_update(1, text="/start"))
    assert harness.errors == []

    harness.session.chat_member_statuses["@eezfo"] = "member"
    harness.session.requests.clear()
    await harness.feed(callback_update(2, "sub:check"))
    assert harness.errors == []

    edits = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "EditMessageText"
    ]
    assert edits, "экран подписки должен замениться на главное меню"
    assert GREETING_MARKER in edits[-1].text

    answers = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "AnswerCallbackQuery"
    ]
    assert answers and "Доступ открыт" in (answers[-1].text or "")


async def test_check_without_subscription_keeps_gate(harness) -> None:
    await enable_gate(harness, status="left")
    await harness.feed(message_update(1, text="/start"))
    harness.session.requests.clear()
    await harness.feed(callback_update(2, "sub:check"))
    assert harness.errors == []

    answers = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "AnswerCallbackQuery"
    ]
    assert answers
    assert answers[-1].show_alert is True
    assert "не найдена" in (answers[-1].text or "").lower()

    edits = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "EditMessageText"
    ]
    assert not any(GREETING_MARKER in request.text for request in edits)


async def test_regular_message_is_blocked(harness) -> None:
    await enable_gate(harness)
    await harness.feed(message_update(1, text="привет"))
    assert harness.errors == []

    messages = sent_messages(harness)
    assert any(GATE_MARKER in (m.text or "").lower() for m in messages)
    assert not any("Открой главное меню" in (m.text or "") for m in messages)


async def test_menu_callback_is_blocked(harness) -> None:
    await enable_gate(harness)
    await harness.feed(message_update(1, text="/start"))
    harness.session.requests.clear()
    await harness.feed(callback_update(2, "menu:balance"))
    assert harness.errors == []

    answers = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "AnswerCallbackQuery"
    ]
    assert answers and answers[-1].show_alert is True
    edits = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "EditMessageText"
    ]
    assert not any("В заявках" in request.text for request in edits)


async def test_admin_bypasses_gate(harness) -> None:
    await enable_gate(harness)
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/admin"))
    assert harness.errors == []
    messages = sent_messages(harness)
    assert any("Админ-панель" in (m.text or "") for m in messages)


async def test_no_sponsors_means_no_gate(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    assert harness.errors == []
    messages = sent_messages(harness)
    assert any(GREETING_MARKER in (m.text or "") for m in messages)
    assert not any(GATE_MARKER in (m.text or "").lower() for m in messages)


async def test_multiple_sponsors(harness) -> None:
    await harness.db.set_setting("sponsor_channels", "@eezfo,@second")
    harness.session.chat_member_statuses["@eezfo"] = "left"
    harness.session.chat_member_statuses["@second"] = "member"

    await harness.feed(message_update(1, text="/start"))
    assert harness.errors == []
    messages = sent_messages(harness)
    gate = [m for m in messages if GATE_MARKER in (m.text or "").lower()]
    assert gate
    buttons = [
        button for row in gate[0].reply_markup.inline_keyboard for button in row
    ]
    urls = {button.url for button in buttons if button.url}
    assert "https://t.me/eezfo" in urls
    assert "https://t.me/second" in urls
    assert not any(GREETING_MARKER in (m.text or "") for m in messages)

    # подписался на оба — доступ открывается
    harness.session.chat_member_statuses["@eezfo"] = "member"
    harness.session.requests.clear()
    await harness.feed(callback_update(2, "sub:check"))
    assert harness.errors == []
    edits = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "EditMessageText"
    ]
    assert edits and GREETING_MARKER in edits[-1].text
