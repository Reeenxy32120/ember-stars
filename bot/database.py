"""Слой доступа к данным: SQLite + aiosqlite.

Все суммы (баланс, награды, цены, суммы заявок) хранятся целыми числами
во внутренних единицах: 1 ⭐ = STARS_SCALE (10 000) единиц.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

from bot.defaults import DEFAULT_SETTINGS
from bot.utils import format_units

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT NOT NULL DEFAULT '',
    balance INTEGER NOT NULL DEFAULT 0,
    ref_id INTEGER,
    tasks_done INTEGER NOT NULL DEFAULT 0,
    language TEXT NOT NULL DEFAULT 'ru',
    notifications INTEGER NOT NULL DEFAULT 1,
    is_blocked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'channel',
    url TEXT NOT NULL DEFAULT '',
    reward INTEGER NOT NULL DEFAULT 0,
    chat_id TEXT,
    is_bonus INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    reward INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, task_id)
);

CREATE TABLE IF NOT EXISTS tgrass_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    offer_id INTEGER NOT NULL,
    reward INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, offer_id)
);

CREATE TABLE IF NOT EXISTS botohub_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    resource_id TEXT NOT NULL,
    reward INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, resource_id)
);

CREATE TABLE IF NOT EXISTS flyer_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    signature TEXT NOT NULL,
    reward INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, signature)
);

CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    gift TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT,
    admin_id INTEGER
);

CREATE TABLE IF NOT EXISTS referrals (
    user_id INTEGER PRIMARY KEY,
    referrer_id INTEGER NOT NULL,
    bonus_paid INTEGER NOT NULL DEFAULT 0,
    earned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target TEXT,
    quantity INTEGER NOT NULL DEFAULT 0,
    amount INTEGER NOT NULL DEFAULT 0,
    stars INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    paid_at TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    kind TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    amount INTEGER NOT NULL,
    uses_left INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS promo_redemptions (
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, code)
);

CREATE INDEX IF NOT EXISTS idx_completions_user ON task_completions (user_id);
CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals (status);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id);
"""

ALLOWED_TASK_UPDATE_FIELDS = frozenset(
    {"title", "type", "url", "reward", "chat_id", "is_bonus", "is_active", "sort_order"}
)


class Database:
    """Единая точка доступа к SQLite. Одно соединение, запись под локом."""

    def __init__(self, path: str | Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup
    async def connect(self) -> None:
        path = str(self.path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        if path != ":memory:":
            await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA synchronous = NORMAL")

    async def init(self) -> None:
        """Подключиться, создать схему и значения настроек по умолчанию."""
        await self.connect()
        await self._conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            await self._conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не подключена: вызовите Database.init()")
        return self._conn

    async def _fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        cursor = await self.conn.execute(sql, tuple(params))
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def _fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def _scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        cursor = await self.conn.execute(sql, tuple(params))
        row = await cursor.fetchone()
        await cursor.close()
        return row[0] if row is not None else None

    # ------------------------------------------------------------------ users
    async def get_or_create_user(
        self, user_id: int, username: str | None, full_name: str | None
    ) -> dict[str, Any]:
        async with self._write_lock:
            await self.conn.execute(
                "INSERT OR IGNORE INTO users (id, username, full_name) VALUES (?, ?, ?)",
                (int(user_id), username, full_name or ""),
            )
            await self.conn.execute(
                "UPDATE users SET username = ?, full_name = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (username, full_name or "", int(user_id)),
            )
            await self.conn.commit()
        user = await self.get_user(user_id)
        assert user is not None
        return user

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM users WHERE id = ?", (int(user_id),))

    async def get_users_count(self) -> int:
        return int(await self._scalar("SELECT COUNT(*) FROM users") or 0)

    async def get_active_user_ids(self) -> list[int]:
        rows = await self._fetchall("SELECT id FROM users WHERE is_blocked = 0")
        return [int(row["id"]) for row in rows]

    async def list_users(self, offset: int = 0, limit: int = 5) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM users ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (int(limit), int(offset)),
        )

    async def find_user(self, query: str) -> dict[str, Any] | None:
        value = (query or "").strip()
        if not value:
            return None
        if value.isdigit():
            user = await self.get_user(int(value))
            if user is not None:
                return user
        username = value.lstrip("@")
        return await self._fetchone(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE ORDER BY id LIMIT 1",
            (username,),
        )

    async def set_user_blocked(self, user_id: int, blocked: bool) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE users SET is_blocked = ?, updated_at = datetime('now') WHERE id = ?",
                (1 if blocked else 0, int(user_id)),
            )
            await self.conn.commit()

    async def set_notifications(self, user_id: int, enabled: bool) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE users SET notifications = ?, updated_at = datetime('now') WHERE id = ?",
                (1 if enabled else 0, int(user_id)),
            )
            await self.conn.commit()

    async def set_language(self, user_id: int, language: str) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE users SET language = ?, updated_at = datetime('now') WHERE id = ?",
                (language, int(user_id)),
            )
            await self.conn.commit()

    async def add_balance(
        self, user_id: int, amount: int, kind: str, comment: str | None = None
    ) -> int:
        """Начисляет/списываетStars и пишет запись в историю операций."""
        amount = int(amount)
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE users SET balance = balance + ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (amount, int(user_id)),
            )
            await self.conn.execute(
                "INSERT INTO transactions (user_id, amount, kind, comment) VALUES (?, ?, ?, ?)",
                (int(user_id), amount, kind, comment),
            )
            await self.conn.commit()
        balance = await self._scalar("SELECT balance FROM users WHERE id = ?", (int(user_id),))
        return int(balance or 0)

    async def get_transactions(self, user_id: int, limit: int = 15) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (int(user_id), int(limit)),
        )

    # ------------------------------------------------------------------ tasks
    async def create_task(
        self,
        title: str,
        task_type: str,
        url: str,
        reward: int,
        chat_id: str | None = None,
        is_bonus: bool = False,
    ) -> int:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "INSERT INTO tasks (title, type, url, reward, chat_id, is_bonus) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, task_type, url, int(reward), chat_id, 1 if is_bonus else 0),
            )
            await self.conn.commit()
            return int(cursor.lastrowid or 0)

    async def update_task(self, task_id: int, **fields: Any) -> bool:
        allowed = {key: value for key, value in fields.items() if key in ALLOWED_TASK_UPDATE_FIELDS}
        if not allowed:
            return False
        for key in ("is_bonus", "is_active"):
            if key in allowed:
                allowed[key] = 1 if allowed[key] else 0
        if "reward" in allowed:
            allowed["reward"] = int(allowed["reward"])
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        async with self._write_lock:
            cursor = await self.conn.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?",
                (*allowed.values(), int(task_id)),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def delete_task(self, task_id: int) -> bool:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM task_completions WHERE task_id = ?", (int(task_id),)
            )
            cursor = await self.conn.execute("DELETE FROM tasks WHERE id = ?", (int(task_id),))
            await self.conn.commit()
            return cursor.rowcount > 0

    async def get_task(self, task_id: int) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM tasks WHERE id = ?", (int(task_id),))

    async def count_tasks(self, active_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM tasks"
        if active_only:
            sql += " WHERE is_active = 1"
        return int(await self._scalar(sql) or 0)

    async def count_available_tasks(self, user_id: int, bonus: bool = False) -> int:
        return int(
            await self._scalar(
                "SELECT COUNT(*) FROM tasks t WHERE t.is_active = 1 AND t.is_bonus = ? "
                "AND t.id NOT IN (SELECT task_id FROM task_completions WHERE user_id = ?)",
                (1 if bonus else 0, int(user_id)),
            )
            or 0
        )

    async def paginate_tasks(
        self, user_id: int, page: int = 1, per_page: int = 8, bonus: bool = False
    ) -> tuple[list[dict[str, Any]], int]:
        total = int(
            await self._scalar(
                "SELECT COUNT(*) FROM tasks WHERE is_active = 1 AND is_bonus = ?",
                (1 if bonus else 0,),
            )
            or 0
        )
        pages = max(1, math.ceil(total / per_page))
        page = max(1, min(int(page), pages))
        rows = await self._fetchall(
            "SELECT t.*, (c.task_id IS NOT NULL) AS is_completed "
            "FROM tasks t "
            "LEFT JOIN task_completions c ON c.task_id = t.id AND c.user_id = ? "
            "WHERE t.is_active = 1 AND t.is_bonus = ? "
            "ORDER BY t.sort_order, t.id LIMIT ? OFFSET ?",
            (int(user_id), 1 if bonus else 0, per_page, (page - 1) * per_page),
        )
        return rows, pages

    async def paginate_admin_tasks(
        self, page: int = 1, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        total = await self.count_tasks(active_only=False)
        pages = max(1, math.ceil(total / per_page))
        page = max(1, min(int(page), pages))
        rows = await self._fetchall(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page),
        )
        return rows, pages

    async def is_task_completed(self, user_id: int, task_id: int) -> bool:
        value = await self._scalar(
            "SELECT 1 FROM task_completions WHERE user_id = ? AND task_id = ?",
            (int(user_id), int(task_id)),
        )
        return value is not None

    async def complete_task(self, user_id: int, task_id: int, reward: int) -> bool:
        """Начисляет награду ровно один раз. False — задание уже выполнено."""
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "INSERT OR IGNORE INTO task_completions (user_id, task_id, reward) "
                    "VALUES (?, ?, ?)",
                    (int(user_id), int(task_id), int(reward)),
                )
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    return False
                await self.conn.execute(
                    "UPDATE users SET balance = balance + ?, tasks_done = tasks_done + 1, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (int(reward), int(user_id)),
                )
                await self.conn.execute(
                    "INSERT INTO transactions (user_id, amount, kind, comment) "
                    "VALUES (?, ?, 'task', ?)",
                    (int(user_id), int(reward), f"Задание #{task_id}"),
                )
                await self.conn.commit()
                return True
            except Exception:
                await self.conn.rollback()
                raise

    async def get_user_completions(
        self, user_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT c.*, t.title FROM task_completions c "
            "LEFT JOIN tasks t ON t.id = c.task_id "
            "WHERE c.user_id = ? ORDER BY c.id DESC LIMIT ?",
            (int(user_id), int(limit)),
        )

    # -------------------------------------------------------------- withdrawals
    async def create_withdrawal(self, user_id: int, amount: int, gift: str = "Подарок") -> int | None:
        amount = int(amount)
        if amount <= 0:
            return None
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "UPDATE users SET balance = balance - ?, updated_at = datetime('now') "
                    "WHERE id = ? AND balance >= ?",
                    (amount, int(user_id), amount),
                )
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    return None
                insert = await self.conn.execute(
                    "INSERT INTO withdrawals (user_id, amount, gift, status) VALUES (?, ?, ?, 'pending')",
                    (int(user_id), amount, gift),
                )
                await self.conn.execute(
                    "INSERT INTO transactions (user_id, amount, kind, comment) "
                    "VALUES (?, ?, 'withdraw', ?)",
                    (int(user_id), -amount, f"Вывод: {gift}"),
                )
                await self.conn.commit()
                return int(insert.lastrowid or 0)
            except Exception:
                await self.conn.rollback()
                raise

    async def get_withdrawal(self, withdrawal_id: int) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT w.*, u.username, u.full_name FROM withdrawals w "
            "LEFT JOIN users u ON u.id = w.user_id WHERE w.id = ?",
            (int(withdrawal_id),),
        )

    async def get_user_withdrawals(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM withdrawals WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (int(user_id), int(limit)),
        )

    async def paginate_withdrawals(
        self, status: str = "pending", page: int = 1, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        total = await self.count_withdrawals(status)
        pages = max(1, math.ceil(total / per_page))
        page = max(1, min(int(page), pages))
        rows = await self._fetchall(
            "SELECT w.*, u.username, u.full_name FROM withdrawals w "
            "LEFT JOIN users u ON u.id = w.user_id "
            "WHERE w.status = ? ORDER BY w.id LIMIT ? OFFSET ?",
            (status, per_page, (page - 1) * per_page),
        )
        return rows, pages

    async def count_withdrawals(self, status: str | None = None) -> int:
        if status is None:
            return int(await self._scalar("SELECT COUNT(*) FROM withdrawals") or 0)
        return int(
            await self._scalar("SELECT COUNT(*) FROM withdrawals WHERE status = ?", (status,))
            or 0
        )

    async def approve_withdrawal(
        self, withdrawal_id: int, admin_id: int
    ) -> dict[str, Any] | None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "UPDATE withdrawals SET status = 'paid', processed_at = datetime('now'), "
                    "admin_id = ? WHERE id = ? AND status = 'pending'",
                    (int(admin_id), int(withdrawal_id)),
                )
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    return None
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise
        return await self.get_withdrawal(withdrawal_id)

    async def reject_withdrawal(
        self, withdrawal_id: int, admin_id: int
    ) -> dict[str, Any] | None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetchone(
                    "SELECT * FROM withdrawals WHERE id = ? AND status = 'pending'",
                    (int(withdrawal_id),),
                )
                if row is None:
                    await self.conn.rollback()
                    return None
                await self.conn.execute(
                    "UPDATE withdrawals SET status = 'rejected', processed_at = datetime('now'), "
                    "admin_id = ? WHERE id = ?",
                    (int(admin_id), int(withdrawal_id)),
                )
                await self.conn.execute(
                    "UPDATE users SET balance = balance + ?, updated_at = datetime('now') "
                    "WHERE id = ?",
                    (int(row["amount"]), int(row["user_id"])),
                )
                await self.conn.execute(
                    "INSERT INTO transactions (user_id, amount, kind, comment) "
                    "VALUES (?, ?, 'withdraw_refund', 'Возврат заявки')",
                    (int(row["user_id"]), int(row["amount"])),
                )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise
        return await self.get_withdrawal(withdrawal_id)

    async def get_withdrawal_totals(self, user_id: int) -> dict[str, int]:
        pending = await self._scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM withdrawals "
            "WHERE user_id = ? AND status = 'pending'",
            (int(user_id),),
        )
        paid = await self._scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM withdrawals "
            "WHERE user_id = ? AND status = 'paid'",
            (int(user_id),),
        )
        return {"pending": int(pending or 0), "paid": int(paid or 0)}

    # ---------------------------------------------------------------- referrals
    async def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        if int(user_id) == int(referrer_id):
            return False
        async with self._write_lock:
            user = await self._fetchone(
                "SELECT id, ref_id FROM users WHERE id = ?", (int(user_id),)
            )
            referrer = await self._fetchone(
                "SELECT id FROM users WHERE id = ?", (int(referrer_id),)
            )
            if user is None or referrer is None or user["ref_id"] is not None:
                return False
            await self.conn.execute(
                "UPDATE users SET ref_id = ?, updated_at = datetime('now') WHERE id = ?",
                (int(referrer_id), int(user_id)),
            )
            await self.conn.execute(
                "INSERT OR IGNORE INTO referrals (user_id, referrer_id) VALUES (?, ?)",
                (int(user_id), int(referrer_id)),
            )
            await self.conn.commit()
            return True

    async def get_referrer_id(self, user_id: int) -> int | None:
        user = await self._fetchone("SELECT ref_id FROM users WHERE id = ?", (int(user_id),))
        if user is None or user["ref_id"] is None:
            return None
        return int(user["ref_id"])

    async def apply_referral_rewards(
        self,
        user_id: int,
        reward: int,
        *,
        required_tasks: int,
        bonus: int,
        percent: int,
    ) -> dict[str, Any] | None:
        """Начисляет рефереру процент и, при достижении порога, бонус."""
        async with self._write_lock:
            referral = await self._fetchone(
                "SELECT * FROM referrals WHERE user_id = ?", (int(user_id),)
            )
            if referral is None:
                return None
            referrer_id = int(referral["referrer_id"])
            percent_amount = int(reward) * max(0, int(percent)) // 100
            bonus_amount = 0
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                if percent_amount > 0:
                    await self.conn.execute(
                        "UPDATE users SET balance = balance + ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (percent_amount, referrer_id),
                    )
                    await self.conn.execute(
                        "INSERT INTO transactions (user_id, amount, kind, comment) "
                        "VALUES (?, ?, 'referral_percent', ?)",
                        (referrer_id, percent_amount, f"Реферал {user_id}"),
                    )
                    await self.conn.execute(
                        "UPDATE referrals SET earned = earned + ? WHERE user_id = ?",
                        (percent_amount, int(user_id)),
                    )
                task_row = await self._fetchone(
                    "SELECT tasks_done FROM users WHERE id = ?", (int(user_id),)
                )
                tasks_done = int(task_row["tasks_done"]) if task_row else 0
                if (
                    int(referral["bonus_paid"]) == 0
                    and int(required_tasks) > 0
                    and tasks_done >= int(required_tasks)
                    and int(bonus) > 0
                ):
                    await self.conn.execute(
                        "UPDATE users SET balance = balance + ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (int(bonus), referrer_id),
                    )
                    await self.conn.execute(
                        "INSERT INTO transactions (user_id, amount, kind, comment) "
                        "VALUES (?, ?, 'referral_bonus', ?)",
                        (referrer_id, int(bonus), f"Друг {user_id}: {tasks_done} заданий"),
                    )
                    await self.conn.execute(
                        "UPDATE referrals SET bonus_paid = 1 WHERE user_id = ?", (int(user_id),)
                    )
                    bonus_amount = int(bonus)
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise
            referrer = await self._fetchone(
                "SELECT balance FROM users WHERE id = ?", (referrer_id,)
            )
            return {
                "referrer_id": referrer_id,
                "percent_amount": percent_amount,
                "bonus_amount": bonus_amount,
                "tasks_done": tasks_done,
                "referrer_balance": int(referrer["balance"]) if referrer else 0,
            }

    async def get_referral_stats(self, user_id: int) -> dict[str, int]:
        invited = await self._scalar(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (int(user_id),)
        )
        earned = await self._scalar(
            "SELECT COALESCE(SUM(earned), 0) FROM referrals WHERE referrer_id = ?",
            (int(user_id),),
        )
        return {"invited": int(invited or 0), "earned": int(earned or 0)}

    async def list_referrals(self, referrer_id: int, limit: int = 10) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT r.*, u.username, u.full_name FROM referrals r "
            "LEFT JOIN users u ON u.id = r.user_id "
            "WHERE r.referrer_id = ? ORDER BY r.created_at DESC LIMIT ?",
            (int(referrer_id), int(limit)),
        )

    # ----------------------------------------------------------------- settings
    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        value = await self._scalar("SELECT value FROM settings WHERE key = ?", (key,))
        return value if value is not None else default

    async def get_int_setting(self, key: str, default: int = 0) -> int:
        raw = await self.get_setting(key)
        try:
            return int(raw) if raw is not None else int(default)
        except (TypeError, ValueError):
            return int(default)

    async def set_setting(self, key: str, value: str | int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            await self.conn.commit()

    async def get_all_settings(self) -> dict[str, str]:
        rows = await self._fetchall("SELECT key, value FROM settings")
        return {row["key"]: row["value"] for row in rows}

    # ------------------------------------------------------------------- orders
    async def create_order(
        self,
        user_id: int,
        kind: str,
        target: str,
        quantity: int,
        amount: int,
        stars: int,
    ) -> int:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "INSERT INTO orders (user_id, kind, target, quantity, amount, stars, "
                "status, payload) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL)",
                (int(user_id), kind, target, int(quantity), int(amount), int(stars)),
            )
            await self.conn.commit()
            return int(cursor.lastrowid or 0)

    async def get_order(self, order_id: int) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT o.*, u.username, u.full_name FROM orders o "
            "LEFT JOIN users u ON u.id = o.user_id WHERE o.id = ?",
            (int(order_id),),
        )

    async def set_order_paid(self, order_id: int, stars: int) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "UPDATE orders SET status = 'paid', stars = ?, paid_at = datetime('now'), "
                "payload = ? WHERE id = ? AND status = 'pending'",
                (int(stars), f"order:{int(order_id)}", int(order_id)),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def set_order_status(self, order_id: int, status: str) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "UPDATE orders SET status = ? WHERE id = ?", (status, int(order_id))
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def count_orders(self, status: str | None = None) -> int:
        if status is None:
            return int(await self._scalar("SELECT COUNT(*) FROM orders") or 0)
        return int(
            await self._scalar("SELECT COUNT(*) FROM orders WHERE status = ?", (status,)) or 0
        )

    async def paginate_orders(
        self, status: str | None = None, page: int = 1, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        total = await self.count_orders(status)
        pages = max(1, math.ceil(total / per_page))
        page = max(1, min(int(page), pages))
        sql = (
            "SELECT o.*, u.username, u.full_name FROM orders o "
            "LEFT JOIN users u ON u.id = o.user_id"
        )
        params: tuple[Any, ...]
        if status is None:
            sql += " ORDER BY o.id DESC LIMIT ? OFFSET ?"
            params = (per_page, (page - 1) * per_page)
        else:
            sql += " WHERE o.status = ? ORDER BY o.id DESC LIMIT ? OFFSET ?"
            params = (status, per_page, (page - 1) * per_page)
        return await self._fetchall(sql, params), pages

    async def list_user_orders(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (int(user_id), int(limit)),
        )

    # -------------------------------------------------------------- promo codes
    async def redeem_promo(self, user_id: int, code: str) -> tuple[int, str]:
        """Возвращает (amount, status), status: ok | invalid | used."""
        value = (code or "").strip().upper()
        if not value:
            return 0, "invalid"
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                promo = await self._fetchone(
                    "SELECT * FROM promo_codes WHERE code = ?", (value,)
                )
                if promo is None or int(promo["uses_left"]) <= 0:
                    await self.conn.rollback()
                    return 0, "invalid"
                used = await self._scalar(
                    "SELECT 1 FROM promo_redemptions WHERE user_id = ? AND code = ?",
                    (int(user_id), value),
                )
                if used is not None:
                    await self.conn.rollback()
                    return 0, "used"
                await self.conn.execute(
                    "UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code = ?", (value,)
                )
                await self.conn.execute(
                    "INSERT INTO promo_redemptions (user_id, code) VALUES (?, ?)",
                    (int(user_id), value),
                )
                await self.conn.execute(
                    "UPDATE users SET balance = balance + ?, updated_at = datetime('now') "
                    "WHERE id = ?",
                    (int(promo["amount"]), int(user_id)),
                )
                await self.conn.execute(
                    "INSERT INTO transactions (user_id, amount, kind, comment) "
                    "VALUES (?, ?, 'promo', ?)",
                    (int(user_id), int(promo["amount"]), f"Промокод {value}"),
                )
                await self.conn.commit()
                return int(promo["amount"]), "ok"
            except Exception:
                await self.conn.rollback()
                raise

    async def create_promo(self, code: str, amount: int, uses: int = 1) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "INSERT OR REPLACE INTO promo_codes (code, amount, uses_left) VALUES (?, ?, ?)",
                ((code or "").strip().upper(), int(amount), int(uses)),
            )
            await self.conn.commit()

    # -------------------------------------------------------------------- stats
    async def paginate_users(
        self, page: int = 1, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        total = await self.get_users_count()
        pages = max(1, math.ceil(total / per_page))
        page = max(1, min(int(page), pages))
        return await self.list_users((page - 1) * per_page, per_page), pages

    async def get_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        stats["users_total"] = int(await self._scalar("SELECT COUNT(*) FROM users") or 0)
        stats["users_blocked"] = int(
            await self._scalar("SELECT COUNT(*) FROM users WHERE is_blocked = 1") or 0
        )
        stats["tasks_active"] = await self.count_tasks(active_only=True)
        stats["tasks_total"] = await self.count_tasks(active_only=False)
        stats["completions"] = int(
            await self._scalar("SELECT COUNT(*) FROM task_completions") or 0
        )
        stats["rewards_paid"] = int(
            await self._scalar(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE kind = 'task'"
            )
            or 0
        )
        stats["withdrawals_pending"] = await self.count_withdrawals("pending")
        stats["withdrawals_paid"] = await self.count_withdrawals("paid")
        stats["withdrawals_paid_sum"] = int(
            await self._scalar(
                "SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE status = 'paid'"
            )
            or 0
        )
        stats["orders_total"] = await self.count_orders()
        stats["orders_paid"] = await self.count_orders("paid")
        stats["balance_total"] = int(
            await self._scalar("SELECT COALESCE(SUM(balance), 0) FROM users") or 0
        )
        return stats

    def format_stars(self, units: int) -> str:
        return format_units(units)

    # -------------------------------------------------------------- tgrass
    async def complete_tgrass_offer(self, user_id: int, offer_id: int, reward: int) -> bool:
        """Начисляет награду за оффер Tgrass ровно один раз."""
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "INSERT OR IGNORE INTO tgrass_completions (user_id, offer_id, reward) "
                    "VALUES (?, ?, ?)",
                    (int(user_id), int(offer_id), int(reward)),
                )
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    return False
                await self.conn.execute(
                    "UPDATE users SET balance = balance + ?, tasks_done = tasks_done + 1, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (int(reward), int(user_id)),
                )
                await self.conn.execute(
                    "INSERT INTO transactions (user_id, amount, kind, comment) "
                    "VALUES (?, ?, 'task', ?)",
                    (int(user_id), int(reward), f"Tgrass оффер #{offer_id}"),
                )
                await self.conn.commit()
                return True
            except Exception:
                await self.conn.rollback()
                raise

    # -------------------------------------------------------------- botohub
    async def complete_botohub_task(self, user_id: int, resource_id: str, reward: int) -> bool:
        """Начисляет награду за задание BotoHub ровно один раз."""
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "INSERT OR IGNORE INTO botohub_completions (user_id, resource_id, reward) "
                    "VALUES (?, ?, ?)",
                    (int(user_id), str(resource_id), int(reward)),
                )
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    return False

                if reward > 0:
                    await self.conn.execute(
                        "UPDATE users SET balance = balance + ?, tasks_done = tasks_done + 1, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (int(reward), int(user_id)),
                    )
                    await self.conn.execute(
                        "INSERT INTO transactions (user_id, amount, kind, comment) "
                        "VALUES (?, ?, 'task', ?)",
                        (int(user_id), int(reward), f"BotoHub #{resource_id}"),
                    )
                else:
                    await self.conn.execute(
                        "UPDATE users SET tasks_done = tasks_done + 1, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (int(user_id),),
                    )
                await self.conn.commit()
                return True
            except Exception:
                await self.conn.rollback()
                raise

    # -------------------------------------------------------------- flyer
    async def complete_flyer_task(self, user_id: int, signature: str, reward: int) -> bool:
        """Начисляет награду за задание Flyer ровно один раз."""
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "INSERT OR IGNORE INTO flyer_completions (user_id, signature, reward) "
                    "VALUES (?, ?, ?)",
                    (int(user_id), str(signature), int(reward)),
                )
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    return False

                if reward > 0:
                    await self.conn.execute(
                        "UPDATE users SET balance = balance + ?, tasks_done = tasks_done + 1, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (int(reward), int(user_id)),
                    )
                    await self.conn.execute(
                        "INSERT INTO transactions (user_id, amount, kind, comment) "
                        "VALUES (?, ?, 'task', ?)",
                        (int(user_id), int(reward), f"Flyer #{signature}"),
                    )
                else:
                    await self.conn.execute(
                        "UPDATE users SET tasks_done = tasks_done + 1, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (int(user_id),),
                    )
                await self.conn.commit()
                return True
            except Exception:
                await self.conn.rollback()
                raise