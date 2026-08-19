from __future__ import annotations

import time
from pathlib import Path

from discord.ext import commands


class AdSettingsV2(commands.Cog):
    """Shared database bootstrap for the interactive advertising settings system."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        db_path = Path(bot.config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))
        self.image_dir = db_path.parent / "ad_images"

    async def cog_load(self):
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ad_settings_v2 (
                guild_id INTEGER PRIMARY KEY,
                post_message TEXT NOT NULL DEFAULT '',
                giveaway_enabled INTEGER NOT NULL DEFAULT 0,
                giveaway_amount INTEGER NOT NULL DEFAULT 3000000,
                giveaway_duration INTEGER NOT NULL DEFAULT 3600,
                giveaway_sponsor_id INTEGER,
                image_path TEXT,
                updated_at REAL NOT NULL DEFAULT 0
            )"""
        )


async def setup(bot):
    await bot.add_cog(AdSettingsV2(bot))
