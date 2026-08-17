"""Official, immutable shortcuts for Ader.

The owner economy grant is implemented as a real discord.py prefix command,
not as an on_message listener. This guarantees that Discord.py dispatches it
once through the normal command router instead of allowing multiple listeners
to execute the same grant.
"""

from __future__ import annotations

import discord
from discord.ext import commands


BOT_OWNER_ID = 1472570059367911587


class OfficialShortcuts(commands.Cog):
    """Built-in shortcuts that are not editable through the shortcuts system."""

    FIXED_BALANCE_ALIASES = {"a"}

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _reply(self, message: discord.Message, content: str | None = None, **kwargs):
        return await message.reply(content=content, mention_author=False, **kwargs)

    async def _send_balance(self, message: discord.Message) -> None:
        balance = await self.bot.db.get_balance(message.author.id)
        economy = self.bot.config.get("modules", {}).get("economy", {})
        symbol = economy.get("currency_symbol", "🪙")
        name = economy.get("currency_name", "ANOCoin")
        embed = discord.Embed(
            title=f"{symbol} رصيدك من {name}",
            description=f"عندك **{balance:,} {name}**.",
            color=discord.Color.gold(),
        )
        await self._reply(message, embed=embed)

    async def _claim_owner_give(self, message: discord.Message) -> bool:
        """Atomically claim this exact Discord message.

        The UNIQUE key is stored in SQLite, so even if an accidental second
        execution reaches this method, only the first execution is allowed to
        modify the balance.
        """
        action_key = f"owner-give:{message.id}"
        await self.bot.db.execute(
            """CREATE TABLE IF NOT EXISTS processed_commands(
                command_key TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            )"""
        )
        cur = await self.bot.db.execute(
            "INSERT OR IGNORE INTO processed_commands(command_key, created_at) VALUES(?, strftime('%s','now'))",
            (action_key,),
        )
        return cur.rowcount == 1

    @commands.command(name="اعطي")
    async def owner_give(self, ctx: commands.Context, member: discord.Member | None = None, amount: int | None = None):
        """Give ANOCoin to a member. Owner-only.

        This is deliberately a prefix command instead of an on_message
        listener. Discord.py's command dispatcher invokes one command handler
        for the message, eliminating the previous duplicate-listener path.
        """
        if ctx.guild is None:
            return
        if ctx.author.id != BOT_OWNER_ID:
            return await ctx.send("❌ هاد الأمر مخصص لصاحب البوت فقط.", delete_after=6)
        if member is None or amount is None:
            return await ctx.send("❌ الاستعمال: `!اعطي @user المبلغ`", delete_after=7)
        if amount <= 0:
            return await ctx.send("❌ المبلغ خاصو يكون أكبر من 0.", delete_after=6)
        if member.bot:
            return await ctx.send("❌ ما يمكنش تعطي العملة لبوت.", delete_after=6)

        # The claim happens before the balance mutation. A duplicate execution
        # of this exact Discord message becomes a no-op.
        if not await self._claim_owner_give(ctx.message):
            return

        try:
            await self.bot.db.add_balance(member.id, ctx.guild.id, amount)
            new_balance = await self.bot.db.get_balance(member.id)
        except Exception:
            # Allow a retry if the database operation itself failed.
            try:
                await self.bot.db.execute(
                    "DELETE FROM processed_commands WHERE command_key=?",
                    (f"owner-give:{ctx.message.id}",),
                )
            except Exception:
                pass
            raise

        economy = self.bot.config.get("modules", {}).get("economy", {})
        name = economy.get("currency_name", "ANOCoin")
        symbol = economy.get("currency_symbol", "🪙")
        embed = discord.Embed(
            title=f"{symbol} تم إعطاء العملة",
            description=(
                f"تمت إضافة **{amount:,} {name}** إلى رصيد {member.mention}.\n"
                f"الرصيد الجديد: **{new_balance:,} {name}**"
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Keep the legacy `A` balance shortcut only. `!اعطي` is intentionally
        # NOT handled here; it is handled by the real prefix command above.
        if message.author.bot or not message.guild:
            return
        content = message.content.strip().lower()
        if content in self.FIXED_BALANCE_ALIASES:
            await self._send_balance(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(OfficialShortcuts(bot))
