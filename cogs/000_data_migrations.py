"""Small idempotent data migrations that must run before feature cogs."""
from __future__ import annotations

import time
from discord.ext import commands


class DataMigrations(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        db = self.bot.db
        await db.execute(
            """CREATE TABLE IF NOT EXISTS processed_commands (
                command_key TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_processed_commands_created
               ON processed_commands(created_at)"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS tournament_reward_claims (
                tournament_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reward_key TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                claimed_at REAL NOT NULL,
                PRIMARY KEY(tournament_id, user_id, reward_key)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS tournament_badges (
                user_id INTEGER NOT NULL,
                badge_key TEXT NOT NULL,
                earned_at REAL NOT NULL,
                PRIMARY KEY(user_id, badge_key)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS tournament_titles (
                user_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_tournament_reward_claims_user
               ON tournament_reward_claims(user_id, reward_key)"""
        )
        # Keep the guard table tidy without touching recent entries.
        await db.execute(
            "DELETE FROM processed_commands WHERE created_at < ?",
            (time.time() - 7 * 86400,),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DataMigrations(bot))
