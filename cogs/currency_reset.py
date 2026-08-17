"""Owner-only global currency reset command."""

from __future__ import annotations

import discord
from discord.ext import commands


OWNER_ID = 1472570059367911587
RESET_TIMEOUT = 60


class CurrencyResetView(discord.ui.View):
    def __init__(self, author_id: int, db):
        super().__init__(timeout=RESET_TIMEOUT)
        self.author_id = author_id
        self.db = db
        self.completed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ هذا التأكيد مخصص لصاحب البوت فقط.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="نعم، إعادة ضبط العملة", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.completed:
            return await interaction.response.send_message("❌ انتهت صلاحية هذا التأكيد.", ephemeral=True)

        self.completed = True
        await self.db.execute("UPDATE global_balances SET balance=0")
        await self.db.execute("UPDATE users SET balance=0")

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="✅ **تمت إعادة ضبط العملة بنجاح.**\nتم تصفير أرصدة جميع الأعضاء.",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="لا، إلغاء", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.completed:
            return await interaction.response.send_message("❌ انتهت صلاحية هذا التأكيد.", ephemeral=True)

        self.completed = True
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="❎ **تم إلغاء عملية إعادة ضبط العملة.**\nلم يتم تغيير أي رصيد.",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        if self.completed:
            return
        for child in self.children:
            child.disabled = True
        # The original message is intentionally not fetched/edited here.
        # This keeps timeout handling safe if the message was deleted.
        self.stop()


class CurrencyReset(commands.Cog):
    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config

    @commands.command(name="رست")
    async def reset_currency(self, ctx: commands.Context):
        """Owner-only command to reset every user's currency balance."""
        if ctx.author.id != OWNER_ID:
            return await ctx.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)

        view = CurrencyResetView(ctx.author.id, self.db)
        await ctx.send(
            "⚠️ **تأكيد إعادة ضبط العملة**\n\n"
            "هل أنت متأكد من أنك تريد **تصفير رصيد جميع الأعضاء**؟\n"
            "هذه العملية ستؤثر على جميع أرصدة العملة ولا يمكن التراجع عنها.",
            view=view,
        )


async def setup(bot: commands.Bot):
    db = getattr(bot, "db", None)
    if db is None:
        raise RuntimeError("Database manager is not available")
    config = getattr(bot, "config", {}) or {}
    await bot.add_cog(CurrencyReset(bot, db, config))
