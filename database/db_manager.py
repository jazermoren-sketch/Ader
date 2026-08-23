"""SQLite database manager for Ader Ultimate."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite


logger = logging.getLogger("Ader.database")


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
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY, applied_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ad_rooms (
            guild_id INTEGER NOT NULL, channel_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL,
            mention_type TEXT NOT NULL DEFAULT 'everyone', template TEXT NOT NULL DEFAULT '',
            image_path TEXT, panel_message_id INTEGER, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS ad_settings (
            guild_id INTEGER PRIMARY KEY, allowed_roles TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS ad_settings_v2 (
            guild_id INTEGER PRIMARY KEY, post_message TEXT NOT NULL DEFAULT '',
            giveaway_enabled INTEGER NOT NULL DEFAULT 0, giveaway_amount INTEGER NOT NULL DEFAULT 3000000,
            giveaway_duration INTEGER NOT NULL DEFAULT 3600, giveaway_sponsor_id INTEGER,
            image_path TEXT, updated_at REAL NOT NULL DEFAULT 0, required_guild_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS ad_custom_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL,
            content TEXT NOT NULL, event TEXT NOT NULL DEFAULT 'after_ad', reply_to INTEGER,
            reply_target TEXT NOT NULL DEFAULT 'none', enabled INTEGER NOT NULL DEFAULT 1,
            position INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0,
            last_message_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS ad_giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL, amount INTEGER NOT NULL, ends_at REAL NOT NULL,
            ended INTEGER NOT NULL DEFAULT 0, winner_id INTEGER, required_guild_id INTEGER,
            message_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS ad_giveaway_entries (
            giveaway_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            PRIMARY KEY(giveaway_id, user_id),
            FOREIGN KEY(giveaway_id) REFERENCES ad_giveaways(id) ON DELETE CASCADE
        );
        """)

        columns = await self.fetchall("PRAGMA table_info(users)")
        names = {row[1] for row in columns}
        if "last_daily" not in names:
            await self.connection.execute("ALTER TABLE users ADD COLUMN last_daily REAL NOT NULL DEFAULT 0")

        # SQLite's CREATE TABLE IF NOT EXISTS never upgrades existing tables.
        # Keep all compatibility upgrades in this one migration boundary.
        for table, column, definition in (
            ("ad_settings_v2", "required_guild_id", "INTEGER"),
            ("ad_custom_messages", "reply_target", "TEXT NOT NULL DEFAULT 'none'"),
            ("ad_custom_messages", "last_message_id", "INTEGER"),
            ("ad_giveaways", "required_guild_id", "INTEGER"),
            ("ad_giveaways", "message_id", "INTEGER"),
        ):
            existing = {str(row[1]) for row in await self.fetchall(f"PRAGMA table_info({table})")}
            if column not in existing:
                await self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        # This must happen before any uniqueness enforcement.  Older releases
        # permitted duplicate logical users, so a direct CREATE UNIQUE INDEX
        # prevented the bot from starting on a persistent database.
        await self._deduplicate_users_and_enforce_identity()

        await self.connection.executescript("""
        CREATE INDEX IF NOT EXISTS idx_analytics_guild_time ON analytics(guild_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_ad_rooms_guild_owner ON ad_rooms(guild_id, owner_id, active);
        CREATE INDEX IF NOT EXISTS idx_ad_giveaways_due ON ad_giveaways(ended, ends_at);
        CREATE INDEX IF NOT EXISTS idx_ad_custom_event ON ad_custom_messages(guild_id, event, position);
        """)

        await self.connection.execute(
            """INSERT OR IGNORE INTO global_balances(user_id, balance, created_at)
               SELECT user_id, 0, MIN(created_at) FROM users GROUP BY user_id"""
        )
        await self.connection.commit()
        logger.info("SQLite migration completed successfully")

    async def _deduplicate_users_and_enforce_identity(self) -> None:
        """Merge legacy duplicate user rows before creating the composite index.

        The table has no surrogate key in older databases, therefore SQLite's
        rowid is used solely to retain one canonical physical row per identity.
        This is transactional and is a no-op after the first clean migration.
        """
        assert self.connection is not None
        schema = await self.fetchall("PRAGMA table_info(users)")
        indexes = await self.fetchall("PRAGMA index_list(users)")
        columns = [(str(row[1]), str(row[2] or "")) for row in schema]
        names = [name for name, _kind in columns]
        logger.info("users schema inspected: columns=%d indexes=%d", len(columns), len(indexes))
        duplicates = await self.fetchall(
            "SELECT guild_id,user_id,COUNT(*) AS count FROM users "
            "GROUP BY guild_id,user_id HAVING COUNT(*) > 1"
        )
        logger.info("users duplicate groups found: %d", len(duplicates))
        merged = removed = 0
        try:
            await self.connection.execute("BEGIN IMMEDIATE")
            for group in duplicates:
                rows = await (await self.connection.execute(
                    "SELECT rowid AS _ader_rowid, * FROM users WHERE guild_id=? AND user_id=? "
                    "ORDER BY COALESCE(created_at, 0) DESC, rowid DESC",
                    (group["guild_id"], group["user_id"]),
                )).fetchall()
                canonical = dict(rows[0])
                for name, kind in columns:
                    values = [row[name] for row in rows]
                    lowered = name.lower()
                    if name in {"guild_id", "user_id"}:
                        continue
                    if lowered == "created_at":
                        valid = [value for value in values if value is not None]
                        if valid:
                            canonical[name] = min(valid)
                    elif lowered in {"updated_at", "last_daily", "last_seen", "last_message_at"}:
                        valid = [value for value in values if value is not None]
                        if valid:
                            canonical[name] = max(valid)
                    elif lowered in {"xp", "balance", "points", "message_count", "messages", "ticket_count", "advertisement_count", "ad_count"}:
                        canonical[name] = sum(int(value or 0) for value in values)
                    elif lowered in {"level", "rank"}:
                        canonical[name] = max(int(value or 0) for value in values)
                    elif lowered in {"inventory", "warnings"}:
                        merged_values = []
                        for value in values:
                            try:
                                parsed = json.loads(value or "[]")
                            except (TypeError, json.JSONDecodeError):
                                parsed = []
                            if isinstance(parsed, list):
                                merged_values.extend(parsed)
                        canonical[name] = json.dumps(merged_values, ensure_ascii=False)
                    elif canonical.get(name) in (None, ""):
                        canonical[name] = next((value for value in values if value not in (None, "")), canonical.get(name))

                assignments = ", ".join(f'"{name}"=?' for name in names if name not in {"guild_id", "user_id"})
                values = [canonical.get(name) for name in names if name not in {"guild_id", "user_id"}]
                values.append(canonical["_ader_rowid"])
                await self.connection.execute(f"UPDATE users SET {assignments} WHERE rowid=?", tuple(values))
                redundant = [row["_ader_rowid"] for row in rows[1:]]
                placeholders = ",".join("?" for _ in redundant)
                await self.connection.execute(f"DELETE FROM users WHERE rowid IN ({placeholders})", tuple(redundant))
                merged += 1
                removed += len(redundant)

            verify = await (await self.connection.execute(
                "SELECT 1 FROM users GROUP BY guild_id,user_id HAVING COUNT(*) > 1 LIMIT 1"
            )).fetchone()
            if verify is not None:
                raise RuntimeError("users duplicate verification failed; uniqueness was not enforced")
            await self.connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_guild_user_unique ON users(guild_id, user_id)"
            )
            await self.connection.commit()
        except Exception:
            await self.connection.rollback()
            raise
        logger.info("users duplicate rows merged: groups=%d rows_removed=%d unique_index=created_or_present", merged, removed)

    async def get_analytics(self, guild_id: int | None = None, limit: int = 100, event_type: str | None = None) -> List[Dict[str, Any]]:
        """Return analytics without relying on legacy runtime monkey patches."""
        clauses, params = [], []
        if guild_id is not None:
            clauses.append("guild_id=?")
            params.append(int(guild_id))
        if event_type:
            clauses.append("type=?")
            params.append(str(event_type))
        limit = max(1, min(int(limit), 1000))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.fetchall(f"SELECT id,guild_id,type,timestamp,data FROM analytics{where} ORDER BY timestamp DESC LIMIT ?", tuple(params + [limit]))
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["data"] = json.loads(item["data"] or "{}")
            except (TypeError, json.JSONDecodeError):
                item["data"] = {}
            result.append(item)
        return result

    async def record_analytics(self, guild_id: int | None, event_type: str, data: Dict[str, Any] | None = None) -> None:
        await self.execute("INSERT INTO analytics(guild_id,type,timestamp,data) VALUES(?,?,?,?)", (guild_id, event_type, time.time(), json.dumps(data or {}, ensure_ascii=False)))

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
