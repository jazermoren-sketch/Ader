"""Official, immutable economy shortcuts for Ader."""

import discord
from discord.ext import commands


class OfficialShortcuts(commands.Cog):
    """Built-in shortcuts that are not editable through the shortcuts system."""

    FIXED_BALANCE_ALIASES = {"!balance", "!رصيدي"}

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_balance(self, message: discord.Message) -> None:
        balance = await self.bot.db.get_balance(message.author.id)
        symbol = self.bot.config.get("modules", {}).get("economy", {}).get("currency_symbol", "🪙")
        name = self.bot.config.get("modules", {}).get("economy", {}).get("currency_name", "ANOCoin")

        embed = discord.Embed(
            title=f"{symbol} رصيدك من {name}",
            description=f"{message.author.mention} عندك **{balance:,} {name}**.",
            color=discord.Color.gold(),
        )
        await message.channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.content.strip().lower() in self.FIXED_BALANCE_ALIASES:
            await self._send_balance(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(OfficialShortcuts(bot))
