"""Ticket state consistency guard.

Keeps the persistent ticket state synchronized with Discord channels.  A ticket
row must not remain ``open`` after its Discord channel has been deleted; such a
stale row makes the next ticket attempt point to ``#unknown``.
"""
from __future__ import annotations

import asyncio
import time

from discord.ext import commands


class TicketStateGuard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cleanup_lock = asyncio.Lock()
        self._ready_cleanup_done = False

    async def _cleanup_stale_open_tickets(self) -> int:
        """Mark open ticket rows as deleted when their Discord channel is gone."""
        db = getattr(self.bot, "db", None)
        if db is None:
            return 0

        async with self._cleanup_lock:
            rows = await db.fetchall(
                "SELECT id, guild_id, channel_id FROM tickets "
                "WHERE status='open' AND channel_id IS NOT NULL"
            )
            stale_ids: list[int] = []
            for row in rows:
                guild = self.bot.get_guild(int(row["guild_id"]))
                channel = guild.get_channel(int(row["channel_id"])) if guild else None
                if channel is None:
                    stale_ids.append(int(row["id"]))

            if not stale_ids:
                return 0

            placeholders = ",".join("?" for _ in stale_ids)
            now = time.time()
            await db.execute(
                f"UPDATE tickets SET status='deleted', closed_at=COALESCE(closed_at, ?) "
                f"WHERE id IN ({placeholders}) AND status='open'",
                tuple([now, *stale_ids]),
            )
            await db.connection.commit()
            return len(stale_ids)

    async def cog_load(self) -> None:
        # This repairs stale rows already present in persistent SQLite data.
        try:
            await self._cleanup_stale_open_tickets()
        except Exception as exc:
            print(f"[TicketStateGuard] startup cleanup error: {exc!r}")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Run again after Discord's guild/channel cache is fully populated.
        if self._ready_cleanup_done:
            return
        self._ready_cleanup_done = True
        try:
            cleaned = await self._cleanup_stale_open_tickets()
            if cleaned:
                print(f"[TicketStateGuard] cleaned {cleaned} stale open ticket(s)")
        except Exception as exc:
            print(f"[TicketStateGuard] ready cleanup error: {exc!r}")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel) -> None:
        """Immediately invalidate an open ticket when its channel disappears."""
        db = getattr(self.bot, "db", None)
        if db is None:
            return
        try:
            cur = await db.execute(
                "UPDATE tickets SET status='deleted', closed_at=COALESCE(closed_at, ?) "
                "WHERE channel_id=? AND status='open'",
                (time.time(), int(channel.id)),
            )
            if cur.rowcount:
                await db.connection.commit()
        except Exception as exc:
            print(f"[TicketStateGuard] channel-delete sync error: {exc!r}")


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketStateGuard(bot))
