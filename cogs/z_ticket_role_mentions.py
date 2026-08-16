from __future__ import annotations

import asyncio

import discord
from discord.ext import commands


class TicketRoleMentions(commands.Cog):
    """Reliably mentions the selected support role when a ticket is created."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._original = None

    def _lock_for(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def cog_load(self):
        ticket_manager = self.bot.get_cog("TicketManager")
        if ticket_manager is None:
            raise RuntimeError("TicketManager must be loaded before TicketRoleMentions")

        self._original = ticket_manager.create_ticket_from_panel

        async def wrapped(interaction: discord.Interaction, panel_id: int, option_index: int):
            guild = interaction.guild
            if guild is None:
                return await self._original(interaction, panel_id, option_index)

            # Serialize clicks by the same user so two rapid clicks cannot
            # generate duplicate support-role notifications.
            async with self._lock_for(guild.id, interaction.user.id):
                db = ticket_manager.db
                existing = await db.fetchone(
                    "SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open' LIMIT 1",
                    (guild.id, interaction.user.id),
                )

                await self._original(interaction, panel_id, option_index)

                # If a ticket already existed before this click, the original
                # manager did not create anything; never send another mention.
                if existing:
                    return

                ticket = await db.fetchone(
                    "SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open' LIMIT 1",
                    (guild.id, interaction.user.id),
                )
                if not ticket:
                    return

                panel = await db.get_ticket_panel(panel_id)
                if not panel or int(panel.get("guild_id") or 0) != guild.id:
                    return

                role_id = int(panel.get("support_role_id") or 0)
                if role_id <= 0:
                    return

                role = guild.get_role(role_id)
                channel = guild.get_channel(int(ticket["channel_id"]))
                if role is None or not isinstance(channel, discord.TextChannel):
                    return

                try:
                    await channel.send(
                        content=role.mention,
                        allowed_mentions=discord.AllowedMentions(
                            roles=True,
                            users=False,
                            everyone=False,
                            replied_user=False,
                        ),
                    )
                except discord.HTTPException as exc:
                    print(f"[TicketRoleMentions] role mention failed: {exc!r}")

        ticket_manager.create_ticket_from_panel = wrapped

    async def cog_unload(self):
        ticket_manager = self.bot.get_cog("TicketManager")
        if ticket_manager is not None and self._original is not None:
            ticket_manager.create_ticket_from_panel = self._original


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketRoleMentions(bot))
