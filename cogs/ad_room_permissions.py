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

    async def _apply(self, channel: discord.TextChannel, owner_id: int):
        owner = channel.guild.get_member(int(owner_id))
        if not owner:
            return
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
            pass

    async def _enforce_after_ready(self):
        await self.bot.wait_until_ready()
        rows = await self.bot.db.fetchall("SELECT channel_id, owner_id FROM ad_rooms WHERE active=1")
        for row in rows:
            channel = self.bot.get_channel(int(row["channel_id"]))
            if isinstance(channel, discord.TextChannel):
                await self._apply(channel, int(row["owner_id"]))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.TextChannel):
            return
        await asyncio.sleep(1)
        row = await self.bot.db.fetchone("SELECT owner_id FROM ad_rooms WHERE channel_id=? AND active=1", (channel.id,))
        if row:
            await self._apply(channel, int(row["owner_id"]))


async def setup(bot):
    await bot.add_cog(AdRoomPermissions(bot))
