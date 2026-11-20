"""Интеграционные тесты пользовательской части через настоящий Dispatcher.

Сеть не используется: FakeSession записывает вызовы Bot API и возвращает
валидные объекты, поэтому проверяется вся связка «апдейт → middleware →
фильтры → хендлер → БД».
"""

from __future__ import annotations

from tests.helpers import (
    ADMIN_ID,
    USER_ID,
    callback_update,
    message_update,
    pre_checkout_update,
    successful_payment_update,
)


async def test_start_creates_user_and_sends_menu(harness) -> None:
    await harness.feed(message_update(1, text="/start"))

    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["username"] == f"user{USER_ID}"
    assert "SendMessage" in harness.session.request_names()
    request = harness.session.requests[-1]
    assert "Привет" in request.text


async def test_all_main_menu_sections_open(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    for index, data in enumerate(
        [
            "menu:tasks",
            "menu:balance",
            "menu:referral",
            "menu:profile",
            "menu:traffic",
            "menu:main",
        ],
        start=2,
    ):
        harness.session.requests.clear()
        await harness.feed(callback_update(index, data))
        assert harness.errors == [], f"Ошибка на кнопке {data}: {harness.errors}"
        assert "EditMessageText" in harness.session.request_names(), data


async def test_referral_deeplink_is_saved(harness) -> None:
    await harness.feed(message_update(1, user_id=ADMIN_ID, text="/start"))
    await harness.feed(message_update(2, user_id=USER_ID, text="/start ref_900"))

    assert harness.errors == []
    assert await harness.db.get_referrer_id(USER_ID) == ADMIN_ID
    referrer = await harness.db.get_user(ADMIN_ID)
    assert referrer is not None


async def test_task_check_awards_stars_only_once(harness) -> None:
    task_id = await harness.db.create_task(
        "Подписка на канал", "channel", "https://t.me/channel", 6400, "-100123"
    )
    await harness.feed(message_update(1, text="/start"))
    await harness.feed(callback_update(2, "menu:tasks"))
    await harness.feed(callback_update(3, f"task:open:{task_id}:tasks"))
    assert harness.errors == []

    await harness.feed(callback_update(4, f"task:check:{task_id}:tasks"))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 6400
    assert user["tasks_done"] == 1

    await harness.feed(callback_update(5, f"task:check:{task_id}:tasks"))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 6400, "повторная проверка не должна начислять Stars"
    assert user["tasks_done"] == 1


async def test_task_check_rejects_without_subscription(harness) -> None:
    task_id = await harness.db.create_task(
        "Канал", "channel", "https://t.me/private", 1000, "-100777"
    )
    await harness.feed(message_update(1, text="/start"))
    harness.session.chat_member_status = "left"
    await harness.feed(callback_update(2, f"task:check:{task_id}:tasks"))

    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 0
    answered = [r for r in harness.session.requests if type(r).__name__ == "AnswerCallbackQuery"]
    assert answered and answered[-1].show_alert is True


async def test_bonus_section_uses_bonus_tasks(harness) -> None:
    task_id = await harness.db.create_task(
        "Бонусный канал", "channel", "https://t.me/bonus", 2000, "-100555", is_bonus=True
    )
    await harness.feed(message_update(1, text="/start"))
    harness.session.requests.clear()
    await harness.feed(callback_update(2, "menu:bonus"))
    assert harness.errors == []
    assert "EditMessageText" in harness.session.request_names()
    await harness.feed(callback_update(3, f"task:check:{task_id}:bonus"))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 2000


async def test_withdraw_fsm_creates_request(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    await harness.db.add_balance(USER_ID, 500_000, "admin", "тестовое начисление")
    await harness.feed(callback_update(2, "withdraw:start"))
    assert harness.errors == []

    await harness.feed(message_update(3, text="50"))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 0
    withdrawals, _ = await harness.db.paginate_withdrawals("pending", 1, 5)
    assert len(withdrawals) == 1
    assert withdrawals[0]["amount"] == 500_000
    # админ получил уведомление
    sent_texts = [
        request.text
        for request in harness.session.requests
        if type(request).__name__ == "SendMessage"
    ]
    assert any("заявка на вывод" in text.lower() for text in sent_texts)


async def test_withdraw_request_is_posted_to_payment_channel(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    await harness.db.add_balance(USER_ID, 500_000, "admin", "тестовое начисление")
    await harness.feed(callback_update(2, "withdraw:start"))
    await harness.feed(message_update(3, text="50"))
    assert harness.errors == []

    posts = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "SendMessage"
        and str(getattr(request, "chat_id", "")) == "@test_channel"
    ]
    assert len(posts) == 1, "заявка должна уходить в канал выплат"
    assert "заявка" in posts[0].text.lower()
    assert posts[0].reply_markup is not None
    callbacks = [
        button.callback_data
        for row in posts[0].reply_markup.inline_keyboard
        for button in row
    ]
    assert any(str(data).startswith("admin:wd:approve:") for data in callbacks)
    assert any(str(data).startswith("admin:wd:reject:") for data in callbacks)


async def test_withdraw_validation(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    await harness.db.add_balance(USER_ID, 500_000, "admin", "начисление")
    # меньше минимума: заявка не создаётся, состояние остаётся
    await harness.feed(callback_update(2, "withdraw:start"))
    await harness.feed(message_update(3, text="10"))
    withdrawals, _ = await harness.db.paginate_withdrawals("pending", 1, 5)
    assert withdrawals == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 500_000
    # корректная сумма
    await harness.feed(message_update(4, text="50"))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 0
    withdrawals, _ = await harness.db.paginate_withdrawals("pending", 1, 5)
    assert len(withdrawals) == 1


async def test_promo_code_flow(harness) -> None:
    await harness.db.create_promo("GIFT", 5000)
    await harness.feed(message_update(1, text="/start"))
    await harness.feed(callback_update(2, "profile:promo"))
    await harness.feed(message_update(3, text="gift"))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 5000
    # повторное использование
    await harness.feed(callback_update(4, "profile:promo"))
    await harness.feed(message_update(5, text="GIFT"))
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["balance"] == 5000


async def test_profile_toggles(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    await harness.feed(callback_update(2, "profile:notifications"))
    await harness.feed(callback_update(3, "profile:lang:en"))
    assert harness.errors == []
    user = await harness.db.get_user(USER_ID)
    assert user is not None
    assert user["notifications"] == 0
    assert user["language"] == "en"


async def test_traffic_views_purchase_and_payment(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    await harness.feed(callback_update(2, "menu:traffic"))
    await harness.feed(callback_update(3, "traffic:buy:view"))
    assert harness.errors == []
    await harness.feed(message_update(4, text="https://t.me/post/1"))
    await harness.feed(message_update(5, text="100"))
    assert harness.errors == []

    orders, _ = await harness.db.paginate_orders(None, 1, 5)
    assert len(orders) == 1
    order = orders[0]
    assert order["kind"] == "view"
    assert order["quantity"] == 100
    assert order["amount"] == 100 * 345
    assert order["status"] == "pending"
    assert "SendInvoice" in harness.session.request_names()

    await harness.feed(
        pre_checkout_update(6, payload=f"order:{order['id']}", total_amount=order["stars"])
    )
    assert harness.errors == []
    assert "AnswerPreCheckoutQuery" in harness.session.request_names()

    await harness.feed(
        successful_payment_update(7, payload=f"order:{order['id']}", total_amount=order["stars"])
    )
    assert harness.errors == []
    updated = await harness.db.get_order(order["id"])
    assert updated is not None
    assert updated["status"] == "paid"
    # админ получил уведомление об оплате
    sent_texts = [
        request.text
        for request in harness.session.requests
        if type(request).__name__ == "SendMessage"
    ]
    assert any("оплаченный заказ" in text.lower() for text in sent_texts)


async def test_traffic_tasks_purchase_validation(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    await harness.feed(callback_update(2, "traffic:buy:task"))
    await harness.feed(callback_update(3, "traffic:category:usual"))
    await harness.feed(message_update(4, text="https://t.me/mychannel"))
    # количество ниже минимума — заказ не создаётся
    await harness.feed(message_update(5, text="10"))
    orders, _ = await harness.db.paginate_orders(None, 1, 5)
    assert orders == []
    # корректное количество — инвойс
    await harness.feed(message_update(6, text="100"))
    assert harness.errors == []
    orders, _ = await harness.db.paginate_orders(None, 1, 5)
    assert len(orders) == 1
    assert orders[0]["kind"] == "task"
    assert orders[0]["amount"] == 100 * 6400


async def test_my_orders_and_cancel(harness) -> None:
    await harness.db.get_or_create_user(USER_ID, f"user{USER_ID}", "Test")
    await harness.db.create_order(USER_ID, "show", "https://t.me/post/2", 100, 860, 1)
    await harness.feed(message_update(1, text="/start"))
    await harness.feed(callback_update(2, "traffic:orders"))
    assert harness.errors == []
    # отмена текущего состояния не падает
    await harness.feed(callback_update(3, "menu:traffic"))
    await harness.feed(callback_update(4, "traffic:cancel"))
    assert harness.errors == []


async def test_unknown_callback_is_answered(harness) -> None:
    await harness.feed(message_update(1, text="/start"))
    await harness.feed(callback_update(2, "noop"))
    await harness.feed(callback_update(3, "definitely:unknown"))
    assert harness.errors == []
    answered = [
        request
        for request in harness.session.requests
        if type(request).__name__ == "AnswerCallbackQuery"
    ]
    assert len(answered) >= 2
