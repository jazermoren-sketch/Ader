"""Owner-only ANORIS controls and delegated bot-owner commands."""

from __future__ import annotations

import time

import discord
from discord.ext import commands

OWNER_ID = 1472570059367911587
BLACKLIST_FINE = 25_000
OWNER_MENTION = "<@1472570059367911587>"


class OwnerCurrency(commands.Cog):
    """Prefix-only currency administration restricted to the bot owner or delegates."""

    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config

    async def cog_load(self) -> None:
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS currency_blacklist (
                user_id INTEGER PRIMARY KEY,
                created_at REAL NOT NULL
            )"""
        )
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS owner_command_delegates (
                user_id INTEGER PRIMARY KEY,
                created_at REAL NOT NULL
            )"""
        )

    def _prefixes(self) -> list[str]:
        configured = self.config.get("bot", {}).get("prefix", "!")
        if isinstance(configured, str):
            prefixes = [configured]
        elif isinstance(configured, (list, tuple)):
            prefixes = [str(p) for p in configured]
        else:
            prefixes = ["!"]
        for prefix in ("!", "$", "-"):
            if prefix not in prefixes:
                prefixes.append(prefix)
        return sorted({p for p in prefixes if p}, key=len, reverse=True)

    def _parse(self, content: str) -> tuple[str, str] | None:
        for prefix in self._prefixes():
            if not content.startswith(prefix):
                continue
            body = content[len(prefix):].strip()
            lowered = body.casefold()
            for command_name in (
                "الغاء بلاك ليست",
                "بلاك ليست",
                "الغاء بوت",
                "سحب",
                "بوت",
                "اعطي",
            ):
                if lowered == command_name.casefold() or lowered.startswith(command_name.casefold() + " "):
                    return command_name, body[len(command_name):].strip()
        return None

    async def _is_delegate(self, user_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM owner_command_delegates WHERE user_id=? LIMIT 1", (user_id,)
        )
        return row is not None

    async def _is_authorized(self, user_id: int) -> bool:
        return user_id == OWNER_ID or await self._is_delegate(user_id)

    async def _is_blacklisted(self, user_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM currency_blacklist WHERE user_id=? LIMIT 1", (user_id,)
        )
        return row is not None

    async def _resolve_member(self, ctx: commands.Context, value: str) -> discord.Member | None:
        if not value:
            return None
        try:
            return await commands.MemberConverter().convert(ctx, value)
        except commands.BadArgument:
            return None

    async def _blacklist(self, ctx: commands.Context, member: discord.Member) -> None:
        if member.bot:
            await ctx.send("❌ لا يمكن وضع بوت في بلاك ليست العملة.", delete_after=8)
            return
        if member.id == OWNER_ID:
            await ctx.send("❌ لا يمكن وضع صاحب البوت في بلاك ليست العملة.", delete_after=8)
            return
        if await self._is_blacklisted(member.id):
            await ctx.send(f"⚠️ {member.mention} موجود بالفعل في بلاك ليست العملة.", delete_after=8)
            return

        await self.db.execute(
            "INSERT INTO currency_blacklist(user_id, created_at) VALUES (?, ?)",
            (member.id, time.time()),
        )
        await ctx.send(
            f"✅ **تم تأكيد بلاك ليست العملة**\n"
            f"العضو: {member.mention}\n"
            f"💰 الغرامة: **{BLACKLIST_FINE:,} ANORIS**\n"
            f"👤 خاص العضو يخلص الغرامة لصاحب البوت {OWNER_MENTION}.\n"
            f"⚠️ **البوت ما غاديش يخصم حتى ANORIS تلقائياً من رصيد العضو.**\n"
            f"💳 الأداء كيديرو العضو يدوياً لصاحب البوت."
        )

    async def _unblacklist(self, ctx: commands.Context, member: discord.Member) -> None:
        if not await self._is_blacklisted(member.id):
            await ctx.send(f"⚠️ {member.mention} ماشي موجود في بلاك ليست العملة.", delete_after=8)
            return
        await self.db.execute("DELETE FROM currency_blacklist WHERE user_id=?", (member.id,))
        await ctx.send(f"✅ تم **إلغاء بلاك ليست العملة** عن {member.mention}.")

    async def _withdraw(self, ctx: commands.Context, member: discord.Member, amount_text: str) -> None:
        if member.bot:
            await ctx.send("❌ لا يمكن سحب العملة من بوت.", delete_after=8)
            return
        try:
            amount = int(amount_text.replace(",", "").replace(" ", ""))
        except ValueError:
            await ctx.send("❌ المبلغ يجب أن يكون رقماً صحيحاً.", delete_after=8)
            return
        if amount <= 0:
            await ctx.send("❌ المبلغ يجب أن يكون أكبر من 0.", delete_after=8)
            return
        balance = await self.db.get_balance(member.id)
        if balance < amount:
            await ctx.send(
                f"❌ رصيد {member.mention} غير كافٍ.\n"
                f"الرصيد الحالي: **{balance:,} ANORIS**\n"
                f"المبلغ المطلوب: **{amount:,} ANORIS**",
                delete_after=10,
            )
            return
        if not await self.db.remove_balance(member.id, ctx.guild.id, amount):
            await ctx.send("❌ تعذر سحب المبلغ. لم يتم تغيير الرصيد.", delete_after=8)
            return
        new_balance = await self.db.get_balance(member.id)
        await ctx.send(
            f"✅ **تم سحب العملة بنجاح**\n"
            f"العضو: {member.mention}\n"
            f"المبلغ المسحوب: **{amount:,} ANORIS**\n"
            f"الرصيد الجديد: **{new_balance:,} ANORIS**"
        )

    async def _give(self, ctx: commands.Context, member: discord.Member, amount_text: str) -> None:
        if member.bot:
            await ctx.send("❌ لا يمكن إعطاء ANORIS لبوت.", delete_after=8)
            return
        try:
            amount = int(amount_text.replace(",", "").replace(" ", ""))
        except ValueError:
            await ctx.send("❌ المبلغ يجب أن يكون رقماً صحيحاً.", delete_after=8)
            return
        if amount <= 0:
            await ctx.send("❌ المبلغ يجب أن يكون أكبر من 0.", delete_after=8)
            return
        await self.db.add_balance(member.id, ctx.guild.id, amount)
        new_balance = await self.db.get_balance(member.id)
        await ctx.send(
            f"✅ **تم إعطاء ANORIS بنجاح**\n"
            f"العضو: {member.mention}\n"
            f"المبلغ: **{amount:,} ANORIS**\n"
            f"الرصيد الجديد: **{new_balance:,} ANORIS**"
        )

    async def _delegate(self, ctx: commands.Context, member: discord.Member) -> None:
        if member.bot:
            await ctx.send("❌ لا يمكن إعطاء صلاحيات أوامر البوت لبوت آخر.", delete_after=8)
            return
        if member.id == OWNER_ID:
            await ctx.send("ℹ️ هذا العضو هو صاحب البوت أصلاً.", delete_after=8)
            return
        if await self._is_delegate(member.id):
            await ctx.send(f"⚠️ {member.mention} عنده بالفعل صلاحيات أوامر صاحب البوت.", delete_after=8)
            return
        await self.db.execute(
            "INSERT INTO owner_command_delegates(user_id, created_at) VALUES (?, ?)",
            (member.id, time.time()),
        )
        await ctx.send(
            f"✅ **تم منح صلاحيات أوامر البوت** لـ {member.mention}.\n"
            f"أصبح بإمكانه استخدام جميع أوامر صاحب البوت المتاحة في النظام."
        )

    async def _undelegate(self, ctx: commands.Context, member: discord.Member) -> None:
        if not await self._is_delegate(member.id):
            await ctx.send(f"⚠️ {member.mention} ما عندوش أصلاً صلاحيات أوامر صاحب البوت.", delete_after=8)
            return
        await self.db.execute("DELETE FROM owner_command_delegates WHERE user_id=?", (member.id,))
        await ctx.send(
            f"✅ **تم إلغاء صلاحيات أوامر صاحب البوت** عن {member.mention}.\n"
            f"ما بقاش يقدر يستعمل أوامر صاحب البوت المفوضة له."
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        parsed = self._parse(message.content.strip())
        if not parsed:
            return

        command_name, args = parsed

        # Grant/revoke delegation are always exclusive to the real bot owner.
        if command_name in ("بوت", "الغاء بوت"):
            if message.author.id != OWNER_ID:
                await message.channel.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
                return
            ctx = await self.bot.get_context(message)
            parts = args.split()
            if len(parts) != 1:
                usage = "-بوت @العضو" if command_name == "بوت" else "-الغاء بوت @العضو"
                await message.channel.send(f"❌ الاستعمال: `{usage}` أو ID", delete_after=8)
                return
            member = await self._resolve_member(ctx, parts[0])
            if member is None:
                await message.channel.send("❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
                return
            if command_name == "بوت":
                await self._delegate(ctx, member)
            else:
                await self._undelegate(ctx, member)
            return

        if not await self._is_authorized(message.author.id):
            await message.channel.send("❌ هذا الأمر مخصص لصاحب البوت أو لمنحه صلاحية أوامر البوت.", delete_after=8)
            return

        ctx = await self.bot.get_context(message)
        parts = args.split()

        if command_name == "اعطي":
            if len(parts) < 2:
                await message.channel.send("❌ الاستعمال: `!اعطي @العضو المبلغ` أو `!اعطي ID المبلغ`", delete_after=8)
                return
            member = await self._resolve_member(ctx, parts[0])
            if member is None:
                await message.channel.send("❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
                return
            await self._give(ctx, member, "".join(parts[1:]))
            return

        if command_name == "سحب":
            if len(parts) < 2:
                await message.channel.send("❌ الاستعمال: `-سحب @العضو المبلغ` أو `-سحب ID المبلغ`", delete_after=8)
                return
            member = await self._resolve_member(ctx, parts[0])
            if member is None:
                await message.channel.send("❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
                return
            await self._withdraw(ctx, member, "".join(parts[1:]))
            return

        if len(parts) != 1:
            usage = "-بلاك ليست @العضو" if command_name == "بلاك ليست" else "-الغاء بلاك ليست @العضو"
            await message.channel.send(f"❌ الاستعمال: `{usage}` أو ID", delete_after=8)
            return
        member = await self._resolve_member(ctx, parts[0])
        if member is None:
            await message.channel.send("❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
            return
        if command_name == "بلاك ليست":
            await self._blacklist(ctx, member)
        else:
            await self._unblacklist(ctx, member)


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerCurrency(bot, bot.db, bot.config))