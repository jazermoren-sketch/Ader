from __future__ import annotations

import discord
from discord.ext import commands


class LegacyAdPanelDisabled(commands.Cog):
    """Legacy advertising panels are disabled and cleaned up on startup."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        rows = await self.db.fetchall("SELECT channel_id, panel_message_id FROM ad_rooms WHERE active=1 AND panel_message_id IS NOT NULL")
        for row in rows:
            channel = self.bot.get_channel(int(row["channel_id"]))
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(int(row["panel_message_id"]))
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            await self.db.execute("UPDATE ad_rooms SET panel_message_id=NULL WHERE channel_id=?", (int(row["channel_id"]),))


async def setup(bot):
    await bot.add_cog(LegacyAdPanelDisabled(bot))
