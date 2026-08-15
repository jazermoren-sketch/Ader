"""Global ANOCoin economy commands."""

import logging
from datetime import datetime
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedFactory, EmbedColor

logger = logging.getLogger(__name__)
OWNER_ID = 1472570059367911587


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.module_config = config.get("modules", {}).get("economy", {})
        self.currency_symbol = self.module_config.get("currency_symbol", "🪙")
        self.currency_name = self.module_config.get("currency_name", "ANOCoin")

    @app_commands.command(name="credits", description="Show your ANOCoin balance or transfer ANOCoin")
    @app_commands.describe(user="User to receive ANOCoin", amount="Amount to transfer")
    async def credits(self, interaction: discord.Interaction, user: discord.Member | None = None, amount: int | None = None):
        if user is None and amount is None:
            balance = await self.db.get_balance(interaction.user.id)
            return await interaction.response.send_message(
                embed=EmbedFactory.create(
                    title=f"{self.currency_symbol} رصيدك من {self.currency_name}",
                    description=f"عندك حالياً **{balance:,} {self.currency_name}**.",
                    color=EmbedColor.ECONOMY,
                ), ephemeral=True
            )
        if user is None or amount is None:
            return await interaction.response.send_message(
                embed=EmbedFactory.error("استعمال غير صحيح", "حدد user و amount معاً، أو استعمل `/credits` بلا خيارات لمعرفة رصيدك."), ephemeral=True
            )
        if amount <= 0 or user.bot or user.id == interaction.user.id:
            return await interaction.response.send_message(embed=EmbedFactory.error("مبلغ غير صالح", "حدد مبلغاً موجباً وعضواً آخر غير البوتات."), ephemeral=True)
        balance = await self.db.get_balance(interaction.user.id)
        if balance < amount:
            return await interaction.response.send_message(embed=EmbedFactory.error("رصيد غير كافٍ", f"رصيدك هو **{balance:,} {self.currency_name}** فقط."), ephemeral=True)
        await self.db.remove_balance(interaction.user.id, interaction.guild.id, amount)
        await self.db.add_balance(user.id, interaction.guild.id, amount)
        await interaction.response.send_message(embed=EmbedFactory.success("تم التحويل", f"تم تحويل **{amount:,} {self.currency_name}** من {interaction.user.mention} إلى {user.mention}."))

    @app_commands.command(name="daily", description="Claim your daily ANOCoin reward")
    async def daily(self, interaction: discord.Interaction):
        user_data = await self.db.get_user(interaction.user.id, interaction.guild.id) or await self.db.create_user(interaction.user.id, interaction.guild.id)
        last_daily = user_data.get("last_daily", 0)
        now = datetime.utcnow().timestamp()
        cooldown = self.module_config.get("daily_cooldown", 86400)
        if now - last_daily < cooldown:
            left = cooldown - (now - last_daily)
            return await interaction.response.send_message(embed=EmbedFactory.warning("Cooldown", f"باقي **{int(left // 3600)}h {int((left % 3600) // 60)}m**."), ephemeral=True)
        amount = self.module_config.get("daily_reward", 100)
        await self.db.add_balance(interaction.user.id, interaction.guild.id, amount)
        await self.db.update_user(interaction.user.id, interaction.guild.id, {"last_daily": now})
        balance = await self.db.get_balance(interaction.user.id)
        await interaction.response.send_message(embed=EmbedFactory.success("🎁 Daily", f"ربحتي **{amount:,} {self.currency_name}**.\nرصيدك: **{balance:,} {self.currency_name}**"))

    @app_commands.command(name="give", description="Give ANOCoin from your own balance")
    @app_commands.describe(user="User to give to", amount="Amount to give")
    async def give(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        if amount <= 0 or user.bot or user.id == interaction.user.id:
            return await interaction.response.send_message(embed=EmbedFactory.error("مبلغ غير صالح", "حدد مبلغاً موجباً وعضواً آخر."), ephemeral=True)
        balance = await self.db.get_balance(interaction.user.id)
        if balance < amount:
            return await interaction.response.send_message(embed=EmbedFactory.error("رصيد غير كافٍ", f"عندك فقط **{balance:,} {self.currency_name}**."), ephemeral=True)
        await self.db.remove_balance(interaction.user.id, interaction.guild.id, amount)
        await self.db.add_balance(user.id, interaction.guild.id, amount)
        await interaction.response.send_message(embed=EmbedFactory.success("تم التحويل", f"تم إرسال **{amount:,} {self.currency_name}** إلى {user.mention}."))

    @app_commands.command(name="coinflip-bet", description="Bet ANOCoin on a coin flip")
    @app_commands.describe(amount="Amount to bet", choice="heads or tails")
    async def coinflip(self, interaction: discord.Interaction, amount: int, choice: str):
        if amount <= 0 or choice.lower() not in {"heads", "tails", "h", "t"}:
            return await interaction.response.send_message(embed=EmbedFactory.error("Invalid bet", "حدد مبلغاً موجباً واختياراً صحيحاً."), ephemeral=True)
        balance = await self.db.get_balance(interaction.user.id)
        if balance < amount:
            return await interaction.response.send_message(embed=EmbedFactory.error("رصيد غير كافٍ", "ما عندكش رصيد كافي."), ephemeral=True)
        choice = "heads" if choice.lower() in {"heads", "h"} else "tails"
        won = random.choice(["heads", "tails"]) == choice
        if won:
            await self.db.add_balance(interaction.user.id, interaction.guild.id, amount)
        else:
            await self.db.remove_balance(interaction.user.id, interaction.guild.id, amount)
        new_balance = await self.db.get_balance(interaction.user.id)
        text = f"🎉 ربحت **{amount:,} {self.currency_name}**" if won else f"❌ خسرت **{amount:,} {self.currency_name}**"
        embed = EmbedFactory.success("Coinflip", f"{text}\nرصيدك: **{new_balance:,} {self.currency_name}**") if won else EmbedFactory.error("Coinflip", f"{text}\nرصيدك: **{new_balance:,} {self.currency_name}**")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shop", description="View the server shop")
    async def shop(self, interaction: discord.Interaction):
        items = await self.db.get_shop_items(interaction.guild.id)
        if not items:
            return await interaction.response.send_message(embed=EmbedFactory.info("Empty Shop", "المتجر فارغ حالياً."), ephemeral=True)
        description = "\n\n".join(f"**{x['name']}** — {self.currency_symbol} {x['price']:,}\n{x['description']}" for x in items[:25])
        await interaction.response.send_message(embed=EmbedFactory.create(title="🏪 ANOCoin Shop", description=description, color=EmbedColor.ECONOMY))

    @commands.command(name="اعطي")
    async def owner_give_prefix(self, ctx: commands.Context, member: discord.Member | None = None, amount: int | None = None):
        if ctx.author.id != OWNER_ID:
            return
        if member is None or amount is None or amount <= 0 or member.bot:
            return await ctx.send("❌ الاستعمال: `!اعطي @user المبلغ`", delete_after=6)
        await self.db.add_balance(member.id, ctx.guild.id, amount)
        await ctx.send(f"🪙 تم إعطاء {member.mention} **{amount:,} {self.currency_name}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot, bot.db, bot.config))
