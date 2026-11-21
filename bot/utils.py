"""Вспомогательные функции: форматирование Stars, HTML, шаблоны текстов."""

from __future__ import annotations

import html
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import quote, urlsplit

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

STARS_SCALE = 10_000
MAX_STARS_INPUT = Decimal("1000000")
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

TRANSACTION_KIND_LABELS = {
    "task": "✅ Задание",
    "referral_bonus": "🎁 Бонус за друга",
    "referral_percent": "👥 % от реферала",
    "withdraw": "💸 Заявка на вывод",
    "withdraw_refund": "↩️ Возврат заявки",
    "promo": "🎟 Промокод",
    "admin": "🛠 Начисление админа",
    "order_refund": "↩️ Возврат за заказ",
    "daily_bonus": "🎲 Ежедневный бонус",
}


class StarsParseError(ValueError):
    """Пользователь ввёл некорректную сумму Stars."""


def parse_stars(raw: str, *, max_stars: Decimal = MAX_STARS_INPUT) -> int:
    """Превращает пользовательский ввод («50», «50,5») во внутренние единицы.

    1 ⭐ = STARS_SCALE внутренних единиц. Точность — до 4 знаков после запятой.
    """
    text = (raw or "").strip().replace(",", ".").replace("⭐", "").replace(" ", "")
    if not text:
        raise StarsParseError("Введи число, например 50")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise StarsParseError("Нужно положительное число, например 50")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - защита от экзотики
        raise StarsParseError("Не удалось распознать число") from exc
    if value <= 0:
        raise StarsParseError("Сумма должна быть больше нуля")
    if value > max_stars:
        raise StarsParseError(f"Слишком большая сумма (максимум {max_stars} ⭐)")
    units = int((value * STARS_SCALE).to_integral_value(rounding=ROUND_HALF_UP))
    if units <= 0:
        raise StarsParseError("Слишком маленькая сумма")
    return units


def format_units(units: int) -> str:
    """Форматирует внутренние единицы как сумму Stars без лишних нулей."""
    value = Decimal(int(units)) / STARS_SCALE
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def telegram_stars_amount(units: int) -> int:
    """Сколько целых Stars списать при оплате (минимум 1)."""
    return max(1, -(-int(units) // STARS_SCALE))


def escape(value: object) -> str:
    """Экранирует пользовательский текст для parse_mode=HTML."""
    return html.escape(str(value), quote=False)


def render(template: str, **values: object) -> str:
    """Подставляет {ключи} в шаблон, не трогая незнакомые плейсхолдеры."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return str(values[key])
        return match.group(0)

    return PLACEHOLDER_RE.sub(replace, template or "")


def format_dt(raw: str | None) -> str:
    """«2024-05-01 10:20:30» (UTC из SQLite) → «01.05.2024 10:20»."""
    if not raw:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            continue
    return raw


def truncate(text: str, limit: int = 60) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_ref_link(bot_username: str, user_id: int) -> str:
    username = (bot_username or "").lstrip("@")
    return f"https://t.me/{username}?start=ref_{user_id}"


def build_share_url(ref_link: str, text: str) -> str:
    return f"https://t.me/share/url?url={quote(ref_link, safe='')}&text={quote(text, safe='')}"


def normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    value = username.strip().lstrip("@")
    return value or None


SPONSOR_SPLIT_RE = re.compile(r"[,;\s]+")


def normalize_chat_ref(chat_id: str | int | None) -> str | None:
    """Приводит ссылку вида https://t.me/channel к виду @channel."""
    if chat_id is None:
        return None
    value = str(chat_id).strip()
    if value.startswith("@"):
        return value
    if value.startswith("t.me/"):
        value = "https://" + value
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if parsed.netloc.lower() not in {"t.me", "telegram.me", "telegram.dog"}:
            return value
        path = parsed.path.strip("/")
        if not path:
            return None
        username = path.split("/")[0]
        return f"@{username}" if username else None
    return value or None


def split_sponsor_refs(raw: str) -> list[str]:
    """Разбирает список каналов-спонсоров в канонические @username."""
    refs: list[str] = []
    seen: set[str] = set()
    for chunk in SPONSOR_SPLIT_RE.split(raw or ""):
        ref = normalize_chat_ref(chunk)
        if not ref or not (ref.startswith("@") or ref.startswith(("http://", "https://"))):
            continue
        key = ref.lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs


def sponsor_url(ref: str) -> str:
    """Ссылка для кнопки «Подписаться»."""
    if ref.startswith("@"):
        return f"https://t.me/{ref[1:]}"
    if ref.startswith("t.me/"):
        return "https://" + ref
    return ref


def sponsor_display(ref: str) -> str:
    """Подпись канала в тексте экрана подписки."""
    if ref.startswith("@"):
        return ref
    normalized = normalize_chat_ref(ref)
    if normalized and normalized.startswith("@"):
        return normalized
    return ref


async def safe_edit(
    message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Редактирует сообщение; при неудаче отправляет новое, игнорируя «not modified»."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        try:
            await message.answer(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            pass


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many