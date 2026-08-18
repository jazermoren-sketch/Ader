from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


class AdPanelBootstrap(commands.Cog):
    """Ensures every active advertising room has exactly one persistent control panel."""

    def __init__(self, bot):
        self.bot = bot
        self._started = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        await asyncio.sleep(3)
        cog = self.bot.get_cog("AdvertisingShop")
        if cog is None:
            log.error("AdvertisingShop cog is not loaded; cannot bootstrap ad panels")
            return

        rows = await self.bot.db.fetchall(
            "SELECT * FROM ad_rooms WHERE active=1"
        )
        for row in rows:
            channel = self.bot.get_channel(int(row["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                await self._ensure_panel(cog, row, channel)
            except (discord.Forbidden, discord.HTTPException, Exception) as exc:
                log.warning("Could not ensure ad panel in %s: %s", channel.id, exc)

    async def _ensure_panel(self, cog, row, channel):
        owner_id = int(row["owner_id"])
        mention = str(row["mention_type"] or "everyone")
        view = cog.AdPanel(cog, owner_id, channel.id, mention) if hasattr(cog, "AdPanel") else None
        if view is None:
            # AdvertisingShop keeps AdPanel at module scope; importing it here avoids
            # creating a second implementation of the panel.
            from .advertising_shop import AdPanel
            view = AdPanel(cog, owner_id, channel.id, mention)

        message_id = row["panel_message_id"]
        existing = None
        if message_id:
            try:
                existing = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                existing = None

        if existing:
            await existing.edit(view=view)
            return

        # Search only recent messages from the bot for an existing panel. This avoids
        # creating duplicates after a database migration or a deleted panel message.
        async for message in channel.history(limit=30):
            if message.author.id != self.bot.user.id:
                continue
            if message.components:
                existing = message
                break

        if existing:
            await self.bot.db.execute(
                "UPDATE ad_rooms SET panel_message_id=? WHERE channel_id=?",
                (existing.id, channel.id),
            )
            await existing.edit(view=view)
            return

        await cog.render_panel(channel)


async def setup(bot):
    await bot.add_cog(AdPanelBootstrap(bot))
