"""Тесты слоя БД: начисления, защита от накрутки, рефералы, заявки, заказы."""

from __future__ import annotations

from bot.database import Database
from bot.services import get_min_withdraw_units, get_prices, get_ref_settings


async def test_init_seeds_defaults(db: Database) -> None:
    assert await db.get_setting("ref_bonus_units") == "50000"
    assert await db.get_int_setting("ref_tasks_required") == 6
    assert (await get_ref_settings(db))["percent"] == 10
    assert await get_min_withdraw_units(db) == 500_000
    prices = await get_prices(db)
    assert prices["task"] == 6400
    assert prices["broadcast"] == 250
    assert prices["view"] == 345
    assert prices["show"] == 860


async def test_user_creation_updates_profile(db: Database) -> None:
    user = await db.get_or_create_user(1, "old_name", "Иван")
    assert user["balance"] == 0
    assert user["tasks_done"] == 0
    user = await db.get_or_create_user(1, "new_name", "Иван Петров")
    assert user["username"] == "new_name"
    assert user["full_name"] == "Иван Петров"
    assert await db.get_users_count() == 1


async def test_add_balance_writes_transaction(db: Database) -> None:
    await db.get_or_create_user(1, "u", "U")
    balance = await db.add_balance(1, 5000, "admin", "тест")
    assert balance == 5000
    transactions = await db.get_transactions(1)
    assert transactions[0]["amount"] == 5000
    assert transactions[0]["kind"] == "admin"
    # отрицательная операция тоже поддерживается
    balance = await db.add_balance(1, -2000, "admin", "списание")
    assert balance == 3000


async def test_complete_task_only_once(db: Database) -> None:
    await db.get_or_create_user(1, "u", "U")
    task_id = await db.create_task("Канал", "channel", "https://t.me/x", 6400, "-100123")
    assert await db.complete_task(1, task_id, 6400) is True
    assert await db.complete_task(1, task_id, 6400) is False
    user = await db.get_user(1)
    assert user is not None
    assert user["balance"] == 6400
    assert user["tasks_done"] == 1
    assert await db.count_available_tasks(1) == 0
    transactions = await db.get_transactions(1)
    assert [tx["kind"] for tx in transactions].count("task") == 1


async def test_tasks_pagination_and_filters(db: Database) -> None:
    await db.get_or_create_user(1, "u", "U")
    for index in range(10):
        await db.create_task(f"Задание {index}", "channel", f"https://t.me/{index}", 1000)
    await db.create_task("Бонус", "channel", "https://t.me/bonus", 2000, is_bonus=True)
    tasks, pages = await db.paginate_tasks(1, 1, per_page=4)
    assert len(tasks) == 4
    assert pages == 3
    tasks, pages = await db.paginate_tasks(1, 99, per_page=4)
    assert pages == 3
    assert len(tasks) == 2
    bonus, bonus_pages = await db.paginate_tasks(1, 1, per_page=4, bonus=True)
    assert len(bonus) == 1
    assert bonus_pages == 1
    assert await db.count_available_tasks(1, bonus=True) == 1


async def test_referral_percent_and_bonus(db: Database) -> None:
    await db.get_or_create_user(1, "referrer", "Реферер")
    await db.get_or_create_user(2, "friend", "Друг")
    assert await db.set_referrer(2, 1) is True
    # повторно/на себя нельзя
    assert await db.set_referrer(2, 1) is False
    assert await db.set_referrer(1, 1) is False
    assert await db.set_referrer(2, 555) is False

    task_ids = []
    for index in range(6):
        task_ids.append(
            await db.create_task(f"Задание {index}", "channel", "https://t.me/x", 1000)
        )

    bonus_events = 0
    for task_id in task_ids:
        assert await db.complete_task(2, task_id, 1000) is True
        payout = await db.apply_referral_rewards(
            2, 1000, required_tasks=6, bonus=50000, percent=10
        )
        assert payout is not None
        assert payout["percent_amount"] == 100
        if payout["bonus_amount"]:
            bonus_events += 1
    assert bonus_events == 1

    referrer = await db.get_user(1)
    assert referrer is not None
    assert referrer["balance"] == 600 + 50000
    stats = await db.get_referral_stats(1)
    assert stats == {"invited": 1, "earned": 600}
    # повторный вызов бонуса не начисляет
    payout = await db.apply_referral_rewards(2, 1000, required_tasks=6, bonus=50000, percent=10)
    assert payout is not None
    assert payout["bonus_amount"] == 0


async def test_withdrawal_lifecycle(db: Database) -> None:
    await db.get_or_create_user(1, "u", "U")
    await db.add_balance(1, 600_000, "admin", "начисление")
    withdrawal_id = await db.create_withdrawal(1, 500_000)
    assert withdrawal_id is not None
    user = await db.get_user(1)
    assert user is not None
    assert user["balance"] == 100_000
    totals = await db.get_withdrawal_totals(1)
    assert totals["pending"] == 500_000
    assert await db.count_withdrawals("pending") == 1

    approved = await db.approve_withdrawal(withdrawal_id, 900)
    assert approved is not None
    assert approved["status"] == "paid"
    assert await db.approve_withdrawal(withdrawal_id, 900) is None
    # повторная обработка невозможна
    assert await db.reject_withdrawal(withdrawal_id, 900) is None
    user = await db.get_user(1)
    assert user is not None
    assert user["balance"] == 100_000
    totals = await db.get_withdrawal_totals(1)
    assert totals == {"pending": 0, "paid": 500_000}


async def test_withdrawal_reject_refunds(db: Database) -> None:
    await db.get_or_create_user(1, "u", "U")
    await db.add_balance(1, 500_000, "admin", "начисление")
    withdrawal_id = await db.create_withdrawal(1, 500_000)
    assert withdrawal_id is not None
    rejected = await db.reject_withdrawal(withdrawal_id, 900)
    assert rejected is not None
    assert rejected["status"] == "rejected"
    user = await db.get_user(1)
    assert user is not None
    assert user["balance"] == 500_000
    transactions = await db.get_transactions(1)
    assert any(tx["kind"] == "withdraw_refund" for tx in transactions)


async def test_withdrawal_insufficient_balance(db: Database) -> None:
    await db.get_or_create_user(1, "u", "U")
    await db.add_balance(1, 10_000, "admin", "начисление")
    assert await db.create_withdrawal(1, 500_000) is None
    user = await db.get_user(1)
    assert user is not None
    assert user["balance"] == 10_000


async def test_promo_codes(db: Database) -> None:
    await db.get_or_create_user(1, "u1", "U1")
    await db.get_or_create_user(2, "u2", "U2")
    await db.create_promo("welcome", 5000, uses=2)

    amount, status = await db.redeem_promo(1, "welcome")
    assert (amount, status) == (5000, "ok")
    assert (await db.redeem_promo(1, "welcome"))[1] == "used"
    assert (await db.redeem_promo(2, "WELCOME"))[0] == 5000
    assert (await db.redeem_promo(2, "unknown"))[1] == "invalid"
    user = await db.get_user(1)
    assert user is not None
    assert user["balance"] == 5000


async def test_orders_flow(db: Database) -> None:
    await db.get_or_create_user(1, "u", "U")
    order_id = await db.create_order(1, "view", "https://t.me/post/1", 100, 34_500, 4)
    order = await db.get_order(order_id)
    assert order is not None
    assert order["status"] == "pending"
    assert await db.set_order_paid(order_id, 4) is True
    assert await db.set_order_paid(order_id, 4) is False
    order = await db.get_order(order_id)
    assert order is not None
    assert order["status"] == "paid"
    assert order["stars"] == 4
    assert await db.set_order_status(order_id, "completed") is True
    orders, pages = await db.paginate_orders(None, 1, per_page=5)
    assert len(orders) == 1
    assert pages == 1
    assert await db.count_orders("completed") == 1


async def test_settings_and_stats(db: Database) -> None:
    await db.get_or_create_user(1, "u", "U")
    await db.set_setting("ref_percent", "15")
    assert await db.get_int_setting("ref_percent") == 15
    await db.add_balance(1, 100_000, "admin", "тест")
    stats = await db.get_stats()
    assert stats["users_total"] == 1
    assert stats["balance_total"] == 100_000
    assert stats["tasks_active"] == 0
    values = await db.get_all_settings()
    assert values["ref_percent"] == "15"
