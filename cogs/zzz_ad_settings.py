from __future__ import annotations

from discord.ext import commands


class LegacyAdSettingsDisabled(commands.Cog):
    """Legacy per-room ad settings are intentionally disabled.

    The canonical implementation is cogs.ad_settings_v2.py and exposes
    /ad-settings for Administrators only. No panel is created in ad rooms.
    """

    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(LegacyAdSettingsDisabled(bot))
