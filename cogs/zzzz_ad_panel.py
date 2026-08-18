from __future__ import annotations

from discord.ext import commands


class LegacyAdPanelDisabled(commands.Cog):
    """Legacy advertising-room panels are disabled by design."""

    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(LegacyAdPanelDisabled(bot))
