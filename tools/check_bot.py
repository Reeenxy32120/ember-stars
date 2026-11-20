"""Диагностика подключения бота.

Проверяет токен, доступ к Telegram API, канал выплат и выводит ID
администраторов канала (владельца), чтобы их можно было указать в ADMIN_IDS.

Запуск::

    .\\.venv\\Scripts\\python.exe tools\\check_bot.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:  # консоль Windows часто в cp1251 — принудительно UTF-8 для вывода
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass


async def main() -> int:
    from bot.config import load_settings

    settings = load_settings()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        me = await bot.get_me()
        print(f"OK  Бот: @{me.username} (id={me.id})")

        if settings.withdraw_channel:
            try:
                chat = await bot.get_chat(settings.withdraw_channel)
                print(
                    f"OK  Канал выплат: {chat.title!r} (id={chat.id}, "
                    f"type={chat.type}, username=@{chat.username or '—'})"
                )
            except TelegramAPIError as exc:
                print(f"ERR Канал {settings.withdraw_channel}: {exc}")
                return 2

            try:
                members = await bot.get_chat_administrators(chat.id)
            except TelegramAPIError as exc:
                print(f"ERR Не удалось получить администраторов канала: {exc}")
                return 3
            print("Администраторы канала:")
            owners: list[int] = []
            for member in members:
                username = f"@{member.user.username}" if member.user.username else "—"
                marker = " ← владелец" if str(member.status) == "creator" else ""
                print(
                    f"  {member.status!s:<14} id={member.user.id:<14} "
                    f"{username:<20} {member.user.full_name}{marker}"
                )
                if str(member.status) == "creator" and not member.user.is_bot:
                    owners.append(int(member.user.id))
            if owners:
                print(f"\nВладелец канала (для ADMIN_IDS): {','.join(str(owner) for owner in owners)}")

            try:
                bot_member = await bot.get_chat_member(chat.id, me.id)
                permissions = []
                for name in ("can_post_messages", "can_edit_messages", "can_delete_messages"):
                    if getattr(bot_member, name, None):
                        permissions.append(name)
                print(
                    f"OK  Права бота в канале: status={bot_member.status}, "
                    f"permissions={', '.join(permissions) or '—'}"
                )
            except TelegramAPIError as exc:
                print(f"WARN Не удалось проверить права бота в канале: {exc}")
        else:
            print("INFO WITHDRAW_CHANNEL не задан — канал выплат не проверялся.")

        if settings.sponsor_channels:
            from bot.utils import normalize_chat_ref, sponsor_display

            print("\nКаналы обязательной подписки (SPONSOR_CHANNELS):")
            for raw in settings.sponsor_channels:
                ref = normalize_chat_ref(raw)
                if not ref:
                    print(f"  ERR некорректная ссылка: {raw!r}")
                    continue
                try:
                    chat = await bot.get_chat(ref)
                    member = await bot.get_chat_member(ref, me.id)
                    print(
                        f"  OK  {sponsor_display(ref):<16} id={chat.id:<16} "
                        f"бот: {member.status}"
                    )
                except TelegramAPIError as exc:
                    print(f"  ERR {ref}: {exc}")
        else:
            print("INFO SPONSOR_CHANNELS не задан — обязательная подписка выключена.")

        print(f"INFO Текущие ADMIN_IDS: {sorted(settings.admin_ids) or 'пусто'}")
        return 0
    except TelegramAPIError as exc:
        print(f"ERR Telegram API: {exc}")
        return 1
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
