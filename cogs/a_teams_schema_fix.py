"""Startup migration for the verified teams system.

This cog intentionally loads before ``teams_v2`` (alphabetical filename order)
and repairs the persistent SQLite schema without deleting any existing data.
"""
from __future__ import annotations

from discord.ext import commands


class TeamsSchemaFix(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS team_settings (
                guild_id INTEGER PRIMARY KEY,
                coach_role_id INTEGER,
                max_players INTEGER NOT NULL DEFAULT 15,
                list_channel_id INTEGER,
                list_message_id INTEGER,
                updated_at REAL NOT NULL
            )"""
        )

        columns = await self.db.fetchall("PRAGMA table_info(team_settings)")
        names = {str(row[1]) for row in columns}

        # Older versions accidentally used/expected message_id. The current
        # teams cog stores persistent panel IDs as list_message_id.
        if "list_message_id" not in names:
            await self.db.execute("ALTER TABLE team_settings ADD COLUMN list_message_id INTEGER")

        if "list_channel_id" not in names:
            await self.db.execute("ALTER TABLE team_settings ADD COLUMN list_channel_id INTEGER")

        if "updated_at" not in names:
            await self.db.execute("ALTER TABLE team_settings ADD COLUMN updated_at REAL")

        if "max_players" not in names:
            await self.db.execute(
                "ALTER TABLE team_settings ADD COLUMN max_players INTEGER NOT NULL DEFAULT 15"
            )

        if "coach_role_id" not in names:
            await self.db.execute("ALTER TABLE team_settings ADD COLUMN coach_role_id INTEGER")

        # Some early builds may have stored the panel ID under message_id.
        # Copy it once into the canonical column, then keep the old column
        # untouched so no persistent data is lost.
        columns = await self.db.fetchall("PRAGMA table_info(team_settings)")
        names = {str(row[1]) for row in columns}
        if "message_id" in names and "list_message_id" in names:
            await self.db.execute(
                "UPDATE team_settings SET list_message_id=message_id "
                "WHERE list_message_id IS NULL AND message_id IS NOT NULL"
            )


async def setup(bot):
    await bot.add_cog(TeamsSchemaFix(bot))
