"""SQLite database manager for Ader Ultimate."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite


class DatabaseManager:
    def __init__(self, path: str = "data/ader.sqlite3", *args, **kwargs):
        self.path = Path(path)
        self.connection: Optional[aiosqlite.Connection] = None
        self._connected = False

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self._migrate()
        self._connected = True

    async def disconnect(self) -> None:
        if self.connection:
            await self.connection.close()
        self.connection = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _migrate(self) -> None:
        await self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 0,
            balance INTEGER NOT NULL DEFAULT 0, inventory TEXT NOT NULL DEFAULT '[]',
            warnings TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS global_balances (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS guilds (
            guild_id INTEGER PRIMARY KEY, config TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            channel_id INTEGER, user_id INTEGER, status TEXT NOT NULL DEFAULT 'open',
            claimed_by INTEGER, created_at REAL NOT NULL, closed_at REAL, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS ticket_panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER,
            message_id INTEGER,
            title TEXT NOT NULL DEFAULT '🎫 الدعم الفني',
            description TEXT NOT NULL DEFAULT 'اختار القسم المناسب لفتح تذكرة.',
            image_url TEXT,
            mode TEXT NOT NULL DEFAULT 'buttons',
            button_label TEXT NOT NULL DEFAULT 'فتح تذكرة',
            button_emoji TEXT NOT NULL DEFAULT '🎫',
            category_id INTEGER,
            support_role_id INTEGER,
            ticket_description TEXT NOT NULL DEFAULT 'شرح لينا المشكل ديالك بالتفصيل.',
            options TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, type TEXT NOT NULL,
            timestamp REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, guild_id INTEGER,
            channel_id INTEGER, remind_at REAL NOT NULL, completed INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS shop (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            name TEXT NOT NULL, price INTEGER NOT NULL, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY, key TEXT NOT NULL, value TEXT
        );
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL, prize TEXT NOT NULL, ends_at REAL NOT NULL, winners INTEGER NOT NULL DEFAULT 1, ended INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER,
            message_id INTEGER, user_id INTEGER NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL, remind_at REAL NOT NULL, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS custom_commands (
            guild_id INTEGER NOT NULL, name TEXT NOT NULL, response TEXT NOT NULL,
            PRIMARY KEY(guild_id, name)
        );
        CREATE TABLE IF NOT EXISTS anti_nuke (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, action TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0, window_start REAL NOT NULL,
            PRIMARY KEY(guild_id, user_id, action)
        );
        """)

        columns = await self.fetchall("PRAGMA table_info(users)")
        names = {row[1] for row in columns}
        if "last_daily" not in names:
            await self.connection.execute("ALTER TABLE users ADD COLUMN last_daily REAL NOT NULL DEFAULT 0")

        await self.connection.execute(
            """INSERT OR IGNORE INTO global_balances(user_id, balance, created_at)
               SELECT user_id, 0, MIN(created_at) FROM users GROUP BY user_id"""
        )
        await self.connection.commit()

    async def execute(self, sql: str, params: tuple = ()):
        cur = await self.connection.execute(sql, params)
        await self.connection.commit()
        return cur

    async def fetchone(self, sql: str, params: tuple = ()):
        cur = await self.connection.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()):
        cur = await self.connection.execute(sql, params)
        return await cur.fetchall()

    async def get_user(self, user_id: int, guild_id: int) -> Optional[Dict[str, Any]]:
        row = await self.fetchone("SELECT * FROM users WHERE user_id=? AND guild_id=?", (user_id, guild_id))
        if not row:
            return None
        d = dict(row)
        d['inventory'] = json.loads(d['inventory'] or '[]')
        d['warnings'] = json.loads(d['warnings'] or '[]')
        d['balance'] = await self.get_balance(user_id)
        return d

    async def create_user(self, user_id: int, guild_id: int, data: Dict[str, Any] = None) -> Dict[str, Any]:
        data = data or {}
        await self.execute(
            "INSERT OR IGNORE INTO users(user_id,guild_id,xp,level,balance,inventory,warnings,created_at,last_daily) VALUES(?,?,?,?,?,?,?,?,?)",
            (user_id, guild_id, data.get('xp', 0), data.get('level', 0), 0, json.dumps(data.get('inventory', [])), json.dumps(data.get('warnings', [])), time.time(), data.get('last_daily', 0)),
        )
        await self.execute("INSERT OR IGNORE INTO global_balances(user_id,balance,created_at) VALUES(?,?,?)", (user_id, 0, time.time()))
        return await self.get_user(user_id, guild_id)

    async def get_balance(self, user_id: int) -> int:
        row = await self.fetchone("SELECT balance FROM global_balances WHERE user_id=?", (user_id,))
        return int(row[0]) if row else 0

    async def update_global_balance(self, user_id: int, amount: int) -> bool:
        await self.execute("INSERT OR IGNORE INTO global_balances(user_id,balance,created_at) VALUES(?,?,?)", (user_id, 0, time.time()))
        cur = await self.execute("UPDATE global_balances SET balance=balance+? WHERE user_id=?", (amount, user_id))
        return cur.rowcount > 0

    async def set_global_balance(self, user_id: int, amount: int) -> bool:
        if amount < 0:
            return False
        await self.execute("INSERT OR IGNORE INTO global_balances(user_id,balance,created_at) VALUES(?,?,?)", (user_id, amount, time.time()))
        cur = await self.execute("UPDATE global_balances SET balance=? WHERE user_id=?", (amount, user_id))
        return cur.rowcount > 0

    async def update_user(self, user_id: int, guild_id: int, data: Dict[str, Any]) -> bool:
        if not data:
            return False
        if 'balance' in data:
            await self.set_global_balance(user_id, int(data['balance']))
            data = {k: v for k, v in data.items() if k != 'balance'}
        if not data:
            return True
        sets, vals = [], []
        for key, value in data.items():
            if key in ('inventory', 'warnings'):
                value = json.dumps(value)
            if key not in {'xp', 'level', 'inventory', 'warnings', 'last_daily'}:
                continue
            sets.append(f"{key}=?")
            vals.append(value)
        if not sets:
            return False
        vals += [user_id, guild_id]
        cur = await self.execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id=? AND guild_id=?", tuple(vals))
        return cur.rowcount > 0

    async def increment_user_field(self, user_id: int, guild_id: int, field: str, amount: int = 1) -> bool:
        if field not in {'xp', 'level'}:
            return False
        await self.create_user(user_id, guild_id)
        cur = await self.execute(f"UPDATE users SET {field}={field}+? WHERE user_id=? AND guild_id=?", (amount, user_id, guild_id))
        return cur.rowcount > 0

    async def get_guild(self, guild_id: int) -> Optional[Dict[str, Any]]:
        row = await self.fetchone("SELECT * FROM guilds WHERE guild_id=?", (guild_id,))
        if not row:
            return None
        d = dict(row)
        d['modules'] = json.loads(d.pop('config', '{}') or '{}')
        return d

    async def create_guild(self, guild_id: int, data: Dict[str, Any] = None) -> Dict[str, Any]:
        await self.execute("INSERT OR IGNORE INTO guilds(guild_id,config,created_at) VALUES(?,?,?)", (guild_id, json.dumps(data or {}), time.time()))
        return await self.get_guild(guild_id)

    async def update_guild(self, guild_id: int, data: Dict[str, Any]) -> bool:
        await self.create_guild(guild_id)
        current = await self.get_guild(guild_id)
        cfg = current.get('modules', {}) if current else {}
        cfg.update(data)
        cur = await self.execute("UPDATE guilds SET config=? WHERE guild_id=?", (json.dumps(cfg), guild_id))
        return cur.rowcount > 0

    async def get_leaderboard(self, guild_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        rows = await self.fetchall("SELECT * FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT ?", (guild_id, limit))
        return [dict(r) for r in rows]

    async def add_balance(self, user_id: int, guild_id: int, amount: int) -> bool:
        await self.create_user(user_id, guild_id)
        return await self.update_global_balance(user_id, amount)

    async def remove_balance(self, user_id: int, guild_id: int, amount: int) -> bool:
        balance = await self.get_balance(user_id)
        if balance < amount:
            return False
        return await self.update_global_balance(user_id, -amount)

    async def add_item(self, user_id: int, guild_id: int, item: Dict[str, Any]) -> bool:
        u = await self.create_user(user_id, guild_id)
        inv = u['inventory']
        inv.append(item)
        return await self.update_user(user_id, guild_id, {'inventory': inv})

    async def add_warning(self, user_id: int, guild_id: int, warning: Dict[str, Any]) -> bool:
        u = await self.create_user(user_id, guild_id)
        warnings = u['warnings']
        warnings.append(warning)
        await self.update_user(user_id, guild_id, {'warnings': warnings})
        await self.execute("INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)", (guild_id, user_id, warning.get('moderator_id', 0), warning.get('reason', ''), time.time()))
        return True

    async def get_warnings(self, user_id: int, guild_id: int) -> List[Dict[str, Any]]:
        rows = await self.fetchall("SELECT * FROM warnings WHERE user_id=? AND guild_id=? AND active=1 ORDER BY id DESC", (user_id, guild_id))
        return [dict(r) for r in rows]

    async def create_ticket(self, ticket_data: Dict[str, Any]) -> str:
        cur = await self.execute("INSERT INTO tickets(guild_id,channel_id,user_id,status,claimed_by,created_at,data) VALUES(?,?,?,?,?,?,?)", (ticket_data.get('guild_id'), ticket_data.get('channel_id'), ticket_data.get('user_id'), ticket_data.get('status', 'open'), ticket_data.get('claimed_by'), time.time(), json.dumps(ticket_data)))
        return str(cur.lastrowid)

    async def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        r = await self.fetchone("SELECT * FROM tickets WHERE id=?", (int(ticket_id),))
        return dict(r) if r else None

    async def update_ticket(self, ticket_id: str, data: Dict[str, Any]) -> bool:
        if not data:
            return False
        sets, vals = [], []
        for k, v in data.items():
            if k in {'status', 'claimed_by', 'channel_id', 'user_id', 'closed_at'}:
                sets.append(f'{k}=?')
                vals.append(v)
        if not sets:
            return False
        vals.append(int(ticket_id))
        cur = await self.execute(f"UPDATE tickets SET {','.join(sets)} WHERE id=?", tuple(vals))
        return cur.rowcount > 0

    async def create_ticket_panel(self, data: Dict[str, Any]) -> int:
        options = json.dumps(data.get('options', []), ensure_ascii=False)
        cur = await self.execute(
            """INSERT INTO ticket_panels(guild_id,channel_id,message_id,title,description,image_url,mode,button_label,button_emoji,category_id,support_role_id,ticket_description,options,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data['guild_id'], data.get('channel_id'), data.get('message_id'), data.get('title', '🎫 الدعم الفني'), data.get('description', 'اختار القسم المناسب لفتح تذكرة.'), data.get('image_url'), data.get('mode', 'buttons'), data.get('button_label', 'فتح تذكرة'), data.get('button_emoji', '🎫'), data.get('category_id'), data.get('support_role_id'), data.get('ticket_description', 'شرح لينا المشكل ديالك بالتفصيل.'), options, time.time())
        )
        return int(cur.lastrowid)

    async def get_ticket_panel(self, panel_id: int) -> Optional[Dict[str, Any]]:
        row = await self.fetchone("SELECT * FROM ticket_panels WHERE id=?", (panel_id,))
        if not row:
            return None
        data = dict(row)
        data['options'] = json.loads(data.get('options') or '[]')
        return data

    async def list_ticket_panels(self, guild_id: int) -> List[Dict[str, Any]]:
        rows = await self.fetchall("SELECT * FROM ticket_panels WHERE guild_id=? ORDER BY id DESC", (guild_id,))
        result = []
        for row in rows:
            data = dict(row)
            data['options'] = json.loads(data.get('options') or '[]')
            result.append(data)
        return result

    async def get_all_ticket_panels(self) -> List[Dict[str, Any]]:
        """Return every saved panel so persistent ticket views can be restored on startup."""
        rows = await self.fetchall("SELECT * FROM ticket_panels ORDER BY id DESC")
        result = []
        for row in rows:
            data = dict(row)
            data['options'] = json.loads(data.get('options') or '[]')
            result.append(data)
        return result

    async def update_ticket_panel(self, panel_id: int, data: Dict[str, Any]) -> bool:
        allowed = {'guild_id', 'channel_id', 'message_id', 'title', 'description', 'image_url', 'mode', 'button_label', 'button_emoji', 'category_id', 'support_role_id', 'ticket_description', 'options'}
        sets, vals = [], []
        for key, value in data.items():
            if key not in allowed:
                continue
            if key == 'options':
                value = json.dumps(value, ensure_ascii=False)
            sets.append(f"{key}=?")
            vals.append(value)
        if not sets:
            return False
        vals.append(panel_id)
        cur = await self.execute(f"UPDATE ticket_panels SET {', '.join(sets)} WHERE id=?", tuple(vals))
        return cur.rowcount > 0

    async def delete_ticket_panel(self, panel_id: int) -> bool:
        cur = await self.execute("DELETE FROM ticket_panels WHERE id=?", (panel_id,))
        return cur.rowcount > 0

    async def create_reminder(self, data: Dict[str, Any]) -> str:
        """Create a reminder using the legacy reminders table expected by Utility."""
        payload = dict(data)
        payload['message'] = str(data.get('message', ''))
        cur = await self.execute(
            "INSERT INTO reminders(user_id,guild_id,channel_id,remind_at,completed,data) VALUES(?,?,?,?,0,?)",
            (data.get('user_id'), data.get('guild_id'), data.get('channel_id'), float(data['remind_at']), json.dumps(payload, ensure_ascii=False)),
        )
        return str(cur.lastrowid)

    async def get_due_reminders(self, current_time: float) -> List[Dict[str, Any]]:
        """Return pending reminders whose due time has passed."""
        rows = await self.fetchall(
            "SELECT id AS _id, user_id, guild_id, channel_id, remind_at, completed, data FROM reminders WHERE completed=0 AND remind_at<=? ORDER BY remind_at ASC",
            (current_time,),
        )
        result = []
        for row in rows:
            item = dict(row)
            try:
                payload = json.loads(item.pop('data') or '{}')
            except (TypeError, json.JSONDecodeError):
                payload = {}
            item.update(payload)
            item['_id'] = int(row['_id'])
            item['message'] = str(item.get('message', payload.get('text', 'Reminder')))
            result.append(item)
        return result

    async def complete_reminder(self, reminder_id: str) -> bool:
        cur = await self.execute("UPDATE reminders SET completed=1 WHERE id=?", (int(reminder_id),))
        return cur.rowcount > 0

    async def get_shop_items(self, guild_id: int) -> List[Dict[str, Any]]:
        rows = await self.fetchall("SELECT * FROM shop WHERE guild_id=? ORDER BY id ASC", (guild_id,))
        return [dict(r) for r in rows]
