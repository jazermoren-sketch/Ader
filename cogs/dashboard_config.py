"""Database-backed dashboard configuration and permission rules."""
from __future__ import annotations

import time
from discord.ext import commands


class DashboardConfig(commands.Cog):
    """Creates the dashboard configuration tables used by the live dashboard."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_command_settings (
                guild_id INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_roles TEXT NOT NULL DEFAULT '[]',
                denied_roles TEXT NOT NULL DEFAULT '[]',
                allowed_channels TEXT NOT NULL DEFAULT '[]',
                denied_channels TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL,
                PRIMARY KEY (guild_id, command_name)
            )
        """)
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_shortcut_settings (
                guild_id INTEGER NOT NULL,
                shortcut_name TEXT NOT NULL,
                alias TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_roles TEXT NOT NULL DEFAULT '[]',
                denied_roles TEXT NOT NULL DEFAULT '[]',
                allowed_channels TEXT NOT NULL DEFAULT '[]',
                denied_channels TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL,
                PRIMARY KEY (guild_id, shortcut_name)
            )
        """)
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_ticket_settings (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_roles TEXT NOT NULL DEFAULT '[]',
                denied_roles TEXT NOT NULL DEFAULT '[]',
                allowed_channels TEXT NOT NULL DEFAULT '[]',
                denied_channels TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL
            )
        """)
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_settings_meta (
                guild_id INTEGER PRIMARY KEY,
                updated_at REAL NOT NULL
            )
        """)

    async def allowed(self, guild_id: int, command_name: str, user, channel_id: int | None) -> tuple[bool, str]:
        import json
        row = await self.bot.db.fetchone(
            "SELECT * FROM dashboard_command_settings WHERE guild_id=? AND command_name=?",
            (guild_id, command_name),
        )
        if not row:
            return True, ""
        if not bool(row["enabled"]):
            return False, "هذا الأمر معطل من لوحة التحكم."

        def ids(key):
            try:
                return {int(x) for x in json.loads(row[key] or "[]")}
            except Exception:
                return set()

        role_ids = {r.id for r in getattr(user, "roles", [])}
        denied_roles = ids("denied_roles")
        allowed_roles = ids("allowed_roles")
        denied_channels = ids("denied_channels")
        allowed_channels = ids("allowed_channels")
        if denied_roles & role_ids:
            return False, "لا تملك رتبة مسموحاً لها باستخدام هذا الأمر."
        if allowed_roles and not (allowed_roles & role_ids):
            return False, "لا تملك رتبة مسموحاً لها باستخدام هذا الأمر."
        if channel_id is not None and channel_id in denied_channels:
            return False, "لا يمكن استخدام هذا الأمر في هذه القناة."
        if channel_id is not None and allowed_channels and channel_id not in allowed_channels:
            return False, "لا يمكن استخدام هذا الأمر في هذه القناة."
        return True, ""


async def setup(bot):
    await bot.add_cog(DashboardConfig(bot))
