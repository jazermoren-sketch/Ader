"""Owner-only ANOCoin controls: currency blacklist and forced withdrawals."""

from __future__ import annotations

import sqlite3

import discord
from discord.ext import commands

OWNER_ID = 1472570059367911587


class OwnerCurrency(commands.Cog):
    """Prefix-only currency administration restricted to the bot owner."""

    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config

    async def cog_load(self) -> None:
        # Keep this feature independent and compatible with existing databases.
        await self.db.connection.execute(
            """CREATE TABLE IF NOT EXISTS currency_blacklist (
                user_id INTEGER PRIMARY KEY,
                created_at REAL NOT NULL
            )"""
        )
        await self.db.connection.commit()

    async def _is_blacklisted(self, user_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM currency_blacklist WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        return row is not None

    @commands.command(name="بلاك ليست")
    async def currency_blacklist(self, ctx: commands.Context, member: discord.Member | None = None):
        """Add a member to the currency blacklist."""
        if ctx.author.id != OWNER_ID:
            return await ctx.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
        if member is None:
            return await ctx.send("❌ الاستعمال: `بلاك ليست @العضو` أو `بلاك ليست ID`", delete_after=8)
        if member.bot:
            return await ctx.send("❌ لا يمكن وضع بوت في بلاك ليست العملة.", delete_after=8)
        if await self._is_blacklisted(member.id):
            return await ctx.send(f"⚠️ {member.mention} موجود بالفعل في بلاك ليست العملة.", delete_after=8)

        await self.db.connection.execute(
            "INSERT INTO currency_blacklist(user_id, created_at) VALUES (?, strftime('%s','now'))",
            (member.id,),
        )
        await self.db.connection.commit()
        await ctx.send(
            f"✅ **تم تأكيد بلاك ليست العملة**\n{member.mention} أصبح الآن في **Currency Blacklist**.\n"
            "لن يتمكن من استخدام وظائف العملة المحمية."
        )

    @commands.command(name="الغاء بلاك ليست")
    async def currency_unblacklist(self, ctx: commands.Context, member: discord.Member | None = None):
        """Remove a member from the currency blacklist."""
        if ctx.author.id != OWNER_ID:
            return await ctx.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
        if member is None:
            return await ctx.send("❌ الاستعمال: `الغاء بلاك ليست @العضو` أو `الغاء بلاك ليست ID`", delete_after=8)
        if not await self._is_blacklisted(member.id):
            return await ctx.send(f"⚠️ {member.mention} ماشي موجود في بلاك ليست العملة.", delete_after=8)

        await self.db.connection.execute("DELETE FROM currency_blacklist WHERE user_id = ?", (member.id,))
        await self.db.connection.commit()
        await ctx.send(f"✅ تم **إلغاء بلاك ليست العملة** عن {member.mention}.")

    @commands.command(name="سحب")
    async def currency_withdraw(self, ctx: commands.Context, member: discord.Member | None = None, amount: int | None = None):
        """Withdraw ANOCoin from another member's global balance."""
        if ctx.author.id != OWNER_ID:
            return await ctx.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
        if member is None or amount is None:
            return await ctx.send("❌ الاستعمال: `سحب @العضو المبلغ` أو `سحب ID المبلغ`", delete_after=8)
        if member.bot:
            return await ctx.send("❌ لا يمكن سحب العملة من بوت.", delete_after=8)
        if amount <= 0:
            return await ctx.send("❌ المبلغ يجب أن يكون أكبر من 0.", delete_after=8)

        balance = await self.db.get_balance(member.id)
        if balance < amount:
            return await ctx.send(
                f"❌ رصيد {member.mention} غير كافٍ.\n"
                f"الرصيد الحالي: **{balance:,} ANORIS**\nالمبلغ المطلوب: **{amount:,} ANORIS**",
                delete_after=10,
            )

        removed = await self.db.remove_balance(member.id, ctx.guild.id if ctx.guild else 0, amount)
        if not removed:
            return await ctx.send("❌ تعذر سحب المبلغ. لم يتم تغيير الرصيد.", delete_after=8)

        new_balance = await self.db.get_balance(member.id)
        await ctx.send(
            f"✅ **تم سحب العملة بنجاح**\n"
            f"العضو: {member.mention}\n"
            f"المبلغ المسحوب: **{amount:,} ANORIS**\n"
            f"الرصيد الجديد: **{new_balance:,} ANORIS**"
        )

    async def cog_check(self, ctx: commands.Context) -> bool:
        # Prefix commands in this cog are owner-only. Keep a defensive check
        # even if a command is invoked through an alias or future addition.
        if ctx.author.id != OWNER_ID:
            raise commands.CheckFailure("Owner only")
        return True

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ الاستعمال غير صحيح. استعمل Mention أو ID، والمبلغ في أمر السحب.", delete_after=8)
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("❌ العضو غير صالح. استعمل Mention أو ID صحيح.", delete_after=8)
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerCurrency(bot, bot.db, bot.config))
