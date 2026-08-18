from __future__ import annotations

import asyncio

import discord
from discord.ext import commands


class AdRoomPermissions(commands.Cog):
    """Enforces that advertising-room owners cannot manage or write in their rooms."""

    def __init__(self, bot):
        self.bot = bot
        self.task = asyncio.create_task(self._enforce_after_ready())

    def cog_unload(self):
        if not self.task.done():
            self.task.cancel()

    async def _enforce_after_ready(self):
        await self.bot.wait_until_ready()
        rows = await self.bot.db.fetchall("SELECT channel_id, owner_id FROM ad_rooms WHERE active=1")
        for row in rows:
            channel = self.bot.get_channel(int(row["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                continue
            owner = channel.guild.get_member(int(row["owner_id"]))
            if not owner:
                continue
            try:
                overwrite = channel.overwrites_for(owner)
                overwrite.view_channel = True
                overwrite.send_messages = False
                overwrite.manage_channels = False
                overwrite.manage_messages = False
                overwrite.attach_files = False
                overwrite.embed_links = False
                overwrite.mention_everyone = False
                await channel.set_permissions(owner, overwrite=overwrite, reason="Ader advertising room: owner has no room controls")
            except (discord.Forbidden, discord.HTTPException):
                continue


async def setup(bot):
    await bot.add_cog(AdRoomPermissions(bot))
