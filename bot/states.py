"""Группы состояний FSM."""

from aiogram.fsm.state import State, StatesGroup


class WithdrawStates(StatesGroup):
    waiting_amount = State()


class PromoStates(StatesGroup):
    waiting_code = State()


class TrafficStates(StatesGroup):
    waiting_target = State()
    waiting_quantity = State()


class AdminStates(StatesGroup):
    waiting_text = State()
    task_title = State()
    task_url = State()
    task_chat_id = State()
    task_reward = State()
    task_bonus = State()
    edit_value = State()
    broadcast_message = State()
    user_search = State()
    user_grant = State()
    setting_value = State()

    # --- Состояния для промокодов ---
    promo_code_input = State()
    promo_amount_input = State()
    promo_uses_input = State()