from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)
TARGET_GUILD_ID = 1490355290116194388


class CommandSyncFix(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._task = None

    def cog_unload(self):
        if self._task and not self._task.done():
            self._task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._sync())

    async def _sync(self):
        await asyncio.sleep(2)
        guild = self.bot.get_guild(TARGET_GUILD_ID)
        if guild is None:
            return
        try:
            target = discord.Object(id=TARGET_GUILD_ID)
            self.bot.tree.copy_global_to(guild=target)
            synced = await self.bot.tree.sync(guild=target)
            logger.info("Synced %d commands to guild %s", len(synced), TARGET_GUILD_ID)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.error("Could not sync guild %s: %s", TARGET_GUILD_ID, exc)


async def setup(bot):
    await bot.add_cog(CommandSyncFix(bot))
