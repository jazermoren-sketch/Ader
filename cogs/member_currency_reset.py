"""Prefix command for resetting one member's ANORIS balance."""

from __future__ import annotations

import discord
from discord.ext import commands

OWNER_ID = 1472570059367911587


class MemberResetConfirmView(discord.ui.View):
    def __init__(self, cog: "MemberCurrencyReset", author_id: int, member: discord.Member, balance: int, guild_id: int):
        super().__init__(timeout=30)
        self.cog = cog
        self.author_id = author_id
        self.member = member
        self.balance = balance
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ غير الشخص اللي استعمل الأمر يقدر يأكد العملية.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="تأكيد التصفير", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = await self.cog.db.get_balance(self.member.id)
        if current <= 0:
            await interaction.response.edit_message(
                content=f"ℹ️ رصيد {self.member.mention} راه أصلاً **0 ANORIS**.",
                view=None,
            )
            self.stop()
            return

        ok = await self.cog.db.remove_balance(self.member.id, self.guild_id, current)
        if not ok:
            await interaction.response.edit_message(
                content="❌ وقع خطأ وما تبدلش الرصيد.",
                view=None,
            )
            self.stop()
            return

        await interaction.response.edit_message(
            content=(
                f"✅ **تم تصفير عملة العضو بنجاح**\n"
                f"👤 العضو: {self.member.mention}\n"
                f"💰 المبلغ الذي تم تصفيره: **{current:,} ANORIS**\n"
                f"🪙 الرصيد الجديد: **0 ANORIS**"
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ تم إلغاء عملية تصفير عملة العضو.", view=None)
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class MemberCurrencyReset(commands.Cog):
    """Owner-only prefix command: -ظبط @member / ID."""

    def __init__(self, bot: commands.Bot, db):
        self.bot = bot
        self.db = db

    @staticmethod
    def _resolve_prefix_command(content: str) -> str | None:
        text = content.strip()
        for prefix in ("-", "!", "$", "/"):
            if text.startswith(prefix):
                body = text[len(prefix):].strip()
                if body == "ظبط" or body.startswith("ظبط "):
                    return body[len("ظبط"):].strip()
        return None

    async def _resolve_member(self, ctx: commands.Context, value: str) -> discord.Member | None:
        try:
            return await commands.MemberConverter().convert(ctx, value)
        except commands.BadArgument:
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        args = self._resolve_prefix_command(message.content)
        if args is None:
            return

        if message.author.id != OWNER_ID:
            await message.channel.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
            return

        ctx = await self.bot.get_context(message)
        parts = args.split()
        if len(parts) != 1:
            await message.channel.send("❌ الاستعمال: `-ظبط @العضو` أو `-ظبط ID`", delete_after=8)
            return

        member = await self._resolve_member(ctx, parts[0])
        if member is None:
            await message.channel.send("❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
            return
        if member.bot:
            await message.channel.send("❌ ما يمكنش تصفير عملة بوت.", delete_after=8)
            return
        if member.id == OWNER_ID:
            await message.channel.send("❌ ما يمكنش تصفير عملة صاحب البوت.", delete_after=8)
            return

        balance = await self.db.get_balance(member.id)
        if balance <= 0:
            await message.channel.send(f"ℹ️ رصيد {member.mention} راه أصلاً **0 ANORIS**.", delete_after=8)
            return

        view = MemberResetConfirmView(self, message.author.id, member, balance, message.guild.id)
        await message.channel.send(
            f"⚠️ **تأكيد تصفير عملة العضو**\n\n"
            f"👤 العضو: {member.mention}\n"
            f"💰 الرصيد الحالي: **{balance:,} ANORIS**\n\n"
            f"واش متأكد أنك باغي تصفر رصيد هاد العضو؟\n"
            f"⚠️ العملية غادي تخلي الرصيد **0 ANORIS**.",
            view=view,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberCurrencyReset(bot, bot.db))
    # Keep the main extension list backward-compatible while making the new
    # platform layer load automatically from an already-loaded core cog.
    if bot.get_cog("UltimatePlatform") is None:
        await bot.load_extension("cogs.ultimate_platform")
