"""Global ANOCoin economy commands."""

from __future__ import annotations

import asyncio
import io
import logging
import math
import random
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from utils.embeds import EmbedFactory, EmbedColor

logger = logging.getLogger(__name__)
OWNER_ID = 1472570059367911587
TRANSFER_TAX = 0.05
CONFIRM_TIMEOUT = 60


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.module_config = config.get("modules", {}).get("economy", {})
        self.currency_symbol = self.module_config.get("currency_symbol", "🪙")
        self.currency_name = self.module_config.get("currency_name", "ANOCoin")
        self._pending_confirmations: set[tuple[int, int]] = set()

    @staticmethod
    def _font(size: int, bold: bool = False):
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf" if bold else "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
        ]
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    @classmethod
    def _confirmation_image(cls, code: str) -> io.BytesIO:
        width, height = 900, 360
        image = Image.new("RGB", (width, height), (20, 20, 28))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=28, fill=(30, 30, 42), outline=(90, 90, 115), width=3)
        draw.text((width // 2, 72), "CONFIRM TRANSACTION", font=cls._font(38, True), fill=(245, 245, 250), anchor="mm")
        rng = random.Random(code)
        for _ in range(70):
            x1 = rng.randint(60, width - 60)
            y1 = rng.randint(125, height - 65)
            x2 = x1 + rng.randint(-45, 45)
            y2 = y1 + rng.randint(-20, 20)
            draw.line((x1, y1, x2, y2), fill=(65, 65, 82), width=2)
        for _ in range(12):
            x = rng.randint(60, width - 60)
            y = rng.randint(135, height - 70)
            draw.ellipse((x, y, x + 5, y + 5), fill=(120, 120, 140))
        draw.text((width // 2, 215), "  ".join(code), font=cls._font(76, True), fill=(255, 255, 255), anchor="mm")
        draw.text((width // 2, 310), "اكتب الأرقام الموجودة في الصورة لإتمام العملية • 60 ثانية", font=cls._font(23), fill=(190, 190, 205), anchor="mm")
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        out.seek(0)
        return out

    async def _confirm(self, channel: discord.abc.Messageable, user: discord.abc.User, guild_id: int, operation: str) -> bool:
        key = (guild_id, user.id)
        if key in self._pending_confirmations:
            return False
        self._pending_confirmations.add(key)
        code = "".join(random.choices("0123456789", k=6))
        try:
            file = discord.File(self._confirmation_image(code), filename="confirmation.png")
            await channel.send(
                content=f"🔐 **تأكيد العملية** — {user.mention}\nأرسل الكود الموجود في الصورة هنا خلال **{CONFIRM_TIMEOUT} ثانية**.",
                file=file,
            )

            def check(message: discord.Message) -> bool:
                return message.author.id == user.id and message.channel.id == getattr(channel, "id", None) and not message.author.bot

            try:
                message = await self.bot.wait_for("message", timeout=CONFIRM_TIMEOUT, check=check)
            except asyncio.TimeoutError:
                await channel.send(f"⌛ {user.mention} انتهى وقت تأكيد {operation}.", delete_after=8)
                return False
            if message.content.strip() != code:
                await channel.send(f"❌ {user.mention} رمز التأكيد غير صحيح. العملية **لم تتم**.", delete_after=8)
                return False
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            return True
        finally:
            self._pending_confirmations.discard(key)

    async def _send_transfer_dm(self, recipient: discord.Member, amount: int, sender: discord.Member):
        try:
            await recipient.send(
                f"**تم تحويل مبلغ '{amount:,} {self.currency_name}' لرصيدك من قبل {sender.mention} من الرصيد.**"
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.info("Could not send transfer DM to %s", recipient.id)

    async def _transfer_amount(self, guild: discord.Guild, sender: discord.Member, recipient: discord.Member, amount: int) -> tuple[bool, str]:
        if amount <= 0 or recipient.bot or recipient.id == sender.id:
            return False, "❌ حدد مبلغاً موجباً وعضواً آخر غير البوتات."

        # Keep the existing economy tax for transfers.
        fee = max(1, math.ceil(amount * TRANSFER_TAX))
        total_cost = amount + fee
        balance = await self.db.get_balance(sender.id)
        if balance < total_cost:
            return False, f"❌ رصيدك غير كافٍ. تحتاج **{total_cost:,} {self.currency_name}** (المبلغ + ضريبة 5%) ورصيدك الحالي **{balance:,} {self.currency_name}**."

        if not await self.db.remove_balance(sender.id, guild.id, total_cost):
            return False, "❌ تعذر خصم المبلغ من رصيدك. لم يتم التحويل."
        await self.db.add_balance(recipient.id, guild.id, amount)
        new_balance = await self.db.get_balance(sender.id)
        await self._send_transfer_dm(recipient, amount, sender)
        return True, f"✅ تم تحويل **{amount:,} {self.currency_name}** إلى {recipient.mention}.\nالضريبة: **{fee:,} {self.currency_name} (5%)**\nرصيدك الجديد: **{new_balance:,} {self.currency_name}**."

    async def _transfer_interaction(self, interaction: discord.Interaction, recipient: discord.Member, amount: int):
        if amount <= 0 or recipient.bot or recipient.id == interaction.user.id:
            return await interaction.response.send_message(embed=EmbedFactory.error("مبلغ غير صالح", "حدد مبلغاً موجباً وعضواً آخر غير البوتات."), ephemeral=True)
        balance = await self.db.get_balance(interaction.user.id)
        fee = max(1, math.ceil(amount * TRANSFER_TAX))
        if balance < amount + fee:
            return await interaction.response.send_message(embed=EmbedFactory.error("رصيد غير كافٍ", f"تحتاج **{amount + fee:,} {self.currency_name}** ورصيدك **{balance:,} {self.currency_name}**."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if not await self._confirm(interaction.channel, interaction.user, interaction.guild.id, "التحويل"):
            return await interaction.followup.send("❌ لم يتم تنفيذ التحويل.", ephemeral=True)
        ok, text = await self._transfer_amount(interaction.guild, interaction.user, recipient, amount)
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="credits", description="Show your balance or another member's balance, or transfer ANOCoin")
    @app_commands.describe(user="Member whose balance you want to view or receive ANOCoin", amount="Amount to transfer")
    async def credits(self, interaction: discord.Interaction, user: discord.Member | None = None, amount: int | None = None):
        if user is None and amount is None:
            balance = await self.db.get_balance(interaction.user.id)
            return await interaction.response.send_message(embed=EmbedFactory.create(title=f"{self.currency_symbol} رصيدك من {self.currency_name}", description=f"رصيدك الحالي: **{balance:,} {self.currency_name}**.", color=EmbedColor.ECONOMY), ephemeral=True)
        if user is not None and amount is None:
            balance = await self.db.get_balance(user.id)
            return await interaction.response.send_message(embed=EmbedFactory.create(title=f"{self.currency_symbol} رصيد {user.display_name}", description=f"رصيد {user.mention}: **{balance:,} {self.currency_name}**.", color=EmbedColor.ECONOMY), ephemeral=True)
        if user is None or amount is None:
            return await interaction.response.send_message(embed=EmbedFactory.error("استعمال غير صحيح", "حدد العضو والمبلغ معاً للتحويل."), ephemeral=True)
        await self._transfer_interaction(interaction, user, amount)

    @commands.command(name="A", aliases=["a"])
    async def balance_or_transfer_prefix(self, ctx: commands.Context, member: discord.Member | None = None, amount: int | None = None):
        """A @user -> balance. A @user amount -> transfer with image confirmation."""
        if ctx.guild is None:
            return
        if member is None:
            balance = await self.db.get_balance(ctx.author.id)
            return await ctx.send(f"🪙 رصيدك الحالي هو **{balance:,} {self.currency_name}**.")
        if amount is None:
            balance = await self.db.get_balance(member.id)
            return await ctx.send(f"🪙 رصيد {member.mention} الحالي هو **{balance:,} {self.currency_name}**.")
        if amount <= 0 or member.bot or member.id == ctx.author.id:
            return await ctx.send("❌ يجب تحديد مبلغ موجب وعضو آخر غير البوتات.", delete_after=8)

        balance = await self.db.get_balance(ctx.author.id)
        fee = max(1, math.ceil(amount * TRANSFER_TAX))
        total = amount + fee
        if balance < total:
            return await ctx.send(f"❌ رصيدك غير كافٍ. تحتاج **{total:,} {self.currency_name}** ورصيدك الحالي **{balance:,} {self.currency_name}**.", delete_after=10)

        confirmed = await self._confirm(ctx.channel, ctx.author, ctx.guild.id, "التحويل")
        if not confirmed:
            return
        ok, text = await self._transfer_amount(ctx.guild, ctx.author, member, amount)
        await ctx.send(text)

    @app_commands.command(name="daily", description="Claim your daily ANOCoin reward")
    async def daily(self, interaction: discord.Interaction):
        user_data = await self.db.get_user(interaction.user.id, interaction.guild.id) or await self.db.create_user(interaction.user.id, interaction.guild.id)
        last_daily = user_data.get("last_daily", 0)
        now = datetime.utcnow().timestamp()
        cooldown = self.module_config.get("daily_cooldown", 86400)
        if now - last_daily < cooldown:
            left = cooldown - (now - last_daily)
            return await interaction.response.send_message(embed=EmbedFactory.warning("Cooldown", f"باقي **{int(left // 3600)}h {int((left % 3600) // 60)}m**."), ephemeral=True)
        amount = int(self.module_config.get("daily_reward", 100))
        await interaction.response.defer(ephemeral=True)
        if not await self._confirm(interaction.channel, interaction.user, interaction.guild.id, "المكافأة اليومية"):
            return await interaction.followup.send("❌ لم يتم جمع المكافأة اليومية.", ephemeral=True)
        user_data = await self.db.get_user(interaction.user.id, interaction.guild.id) or await self.db.create_user(interaction.user.id, interaction.guild.id)
        now = datetime.utcnow().timestamp()
        if now - user_data.get("last_daily", 0) < cooldown:
            return await interaction.followup.send(embed=EmbedFactory.warning("Cooldown", "تم جمع المكافأة اليومية بالفعل."), ephemeral=True)
        await self.db.add_balance(interaction.user.id, interaction.guild.id, amount)
        await self.db.update_user(interaction.user.id, interaction.guild.id, {"last_daily": now})
        balance = await self.db.get_balance(interaction.user.id)
        await interaction.followup.send(embed=EmbedFactory.success("🎁 Daily", f"ربحت **{amount:,} {self.currency_name}**.\nرصيدك: **{balance:,} {self.currency_name}**"), ephemeral=True)

    @app_commands.command(name="give", description="Give ANOCoin from your own balance")
    @app_commands.describe(user="User to give to", amount="Amount to give")
    async def give(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        await self._transfer_interaction(interaction, user, amount)

    @app_commands.command(name="coinflip-bet", description="Bet ANOCoin on a coin flip")
    @app_commands.describe(amount="Amount to bet", choice="heads or tails")
    async def coinflip(self, interaction: discord.Interaction, amount: int, choice: str):
        if amount <= 0 or choice.lower() not in {"heads", "tails", "h", "t"}:
            return await interaction.response.send_message(embed=EmbedFactory.error("Invalid bet", "حدد مبلغاً موجباً واختياراً صحيحاً."), ephemeral=True)
        balance = await self.db.get_balance(interaction.user.id)
        if balance < amount:
            return await interaction.response.send_message(embed=EmbedFactory.error("رصيد غير كافٍ", "لا يوجد لديك رصيد كافٍ."), ephemeral=True)
        choice = "heads" if choice.lower() in {"heads", "h"} else "tails"
        won = random.choice(["heads", "tails"]) == choice
        await self.db.remove_balance(interaction.user.id, interaction.guild.id, amount)
        if won:
            await self.db.add_balance(interaction.user.id, interaction.guild.id, amount * 2)
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
        new_balance = await self.db.get_balance(member.id)
        await ctx.send(f"🪙 تم إعطاء {member.mention} **{amount:,} {self.currency_name}**. الرصيد الجديد: **{new_balance:,} {self.currency_name}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot, bot.db, bot.config))
