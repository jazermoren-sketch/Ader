"""Official, immutable economy shortcuts for Ader."""

import discord
from discord.ext import commands


BOT_OWNER_ID = 1472570059367911587


class OfficialShortcuts(commands.Cog):
    """Built-in shortcuts that are not editable through the shortcuts system."""

    # The balance shortcut is intentionally fixed and cannot be changed from the
    # server shortcut manager. `A` is the only official balance trigger.
    FIXED_BALANCE_ALIASES = {"a"}

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _reply(self, message: discord.Message, content: str = None, **kwargs):
        return await message.reply(content=content, mention_author=False, **kwargs)

    async def _send_balance(self, message: discord.Message) -> None:
        balance = await self.bot.db.get_balance(message.author.id)
        symbol = self.bot.config.get("modules", {}).get("economy", {}).get("currency_symbol", "🪙")
        name = self.bot.config.get("modules", {}).get("economy", {}).get("currency_name", "ANOCoin")

        embed = discord.Embed(
            title=f"{symbol} رصيدك من {name}",
            description=f"عندك **{balance:,} {name}**.",
            color=discord.Color.gold(),
        )
        await self._reply(message, embed=embed)

    async def _owner_give(self, message: discord.Message) -> None:
        if message.author.id != BOT_OWNER_ID:
            await self._reply(message, "❌ هاد الاختصار مخصص لصاحب البوت فقط.", delete_after=6)
            return

        parts = message.content.split()
        if len(parts) < 3 or not message.mentions:
            await self._reply(message, "❌ الاستعمال: `!اعطي @user المبلغ`", delete_after=7)
            return

        try:
            amount = int(parts[-1].replace(",", ""))
        except ValueError:
            await self._reply(message, "❌ المبلغ خاصو يكون رقم صحيح.", delete_after=6)
            return

        if amount <= 0:
            await self._reply(message, "❌ المبلغ خاصو يكون أكبر من 0.", delete_after=6)
            return

        target = message.mentions[0]
        if target.bot:
            await self._reply(message, "❌ ما يمكنش تعطي العملة لبوت.", delete_after=6)
            return

        await self.bot.db.add_balance(target.id, message.guild.id, amount)
        name = self.bot.config.get("modules", {}).get("economy", {}).get("currency_name", "ANOCoin")
        symbol = self.bot.config.get("modules", {}).get("economy", {}).get("currency_symbol", "🪙")
        new_balance = await self.bot.db.get_balance(target.id)

        embed = discord.Embed(
            title=f"{symbol} تم إعطاء العملة",
            description=(
                f"تمت إضافة **{amount:,} {name}** إلى رصيد {target.mention}.\n"
                f"الرصيد الجديد: **{new_balance:,} {name}**"
            ),
            color=discord.Color.green(),
        )
        await self._reply(message, embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        content = message.content.strip().lower()
        if content in self.FIXED_BALANCE_ALIASES:
            await self._send_balance(message)
            return

        if message.content.strip().startswith("!اعطي"):
            await self._owner_give(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(OfficialShortcuts(bot))
