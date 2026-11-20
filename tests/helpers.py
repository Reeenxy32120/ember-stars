"""Конструкторы Telegram-объектов для интеграционных тестов."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)

USER_ID = 777
ADMIN_ID = 900


def make_user(user_id: int = USER_ID, name: str = "Test") -> User:
    return User(id=user_id, is_bot=False, first_name=name, username=f"user{user_id}")


def make_message(
    *,
    message_id: int = 1,
    user_id: int = USER_ID,
    chat_id: int | None = None,
    chat_type: str = "private",
    text: str | None = None,
    **kwargs,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=chat_id or user_id, type=chat_type),  # type: ignore[arg-type]
        from_user=make_user(user_id),
        text=text,
        **kwargs,
    )


def message_update(
    update_id: int,
    *,
    user_id: int = USER_ID,
    text: str | None = None,
    **kwargs,
) -> Update:
    return Update(
        update_id=update_id,
        message=make_message(user_id=user_id, text=text, **kwargs),
    )


def callback_update(
    update_id: int,
    data: str,
    *,
    user_id: int = USER_ID,
    chat_type: str = "private",
) -> Update:
    message = make_message(user_id=user_id, text="base", chat_type=chat_type)
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"callback-{update_id}",
            from_user=make_user(user_id),
            chat_instance="chat-instance",
            message=message,
            data=data,
        ),
    )


def pre_checkout_update(
    update_id: int,
    *,
    payload: str = "order:1",
    total_amount: int = 5,
    user_id: int = USER_ID,
) -> Update:
    return Update(
        update_id=update_id,
        pre_checkout_query=PreCheckoutQuery(
            id=f"pcq-{update_id}",
            from_user=make_user(user_id),
            currency="XTR",
            total_amount=total_amount,
            invoice_payload=payload,
        ),
    )


def successful_payment_update(
    update_id: int,
    *,
    payload: str = "order:1",
    total_amount: int = 5,
    user_id: int = USER_ID,
) -> Update:
    payment = SuccessfulPayment(
        currency="XTR",
        total_amount=total_amount,
        invoice_payload=payload,
        telegram_payment_charge_id=f"charge-{update_id}",
        provider_payment_charge_id=f"provider-{update_id}",
    )
    return Update(
        update_id=update_id,
        message=make_message(
            user_id=user_id,
            successful_payment=payment,
        ),
    )
