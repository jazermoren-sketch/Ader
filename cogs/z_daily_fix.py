"""Professional daily reward replacement.

Replaces the legacy /daily command with a race-safe 24-hour reward flow.
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedFactory, EmbedColor

DAILY_COOLDOWN = 24 * 60 * 60
DAILY_REWARD = 25_000
CONFIRM_TIMEOUT = 60
CODE_LENGTH = 6


class DailyFix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _lock_for(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def _now() -> int:
        return int(datetime.now(timezone.utc).timestamp())

    async def _captcha(self, interaction: discord.Interaction) -> bool:
        code = "".join(random.choices("0123456789", k=CODE_LENGTH))
        embed = discord.Embed(
            title="🔐 التحقق من المكافأة اليومية",
            description=(
                f"اكتب **الأرقام الستة** الظاهرة أدناه في نفس القناة خلال **{CONFIRM_TIMEOUT} ثانية**.\n\n"
                f"### `{code}`\n\n"
                "⚠️ لا ترسل أي معلومات أخرى؛ هذا الرمز مخصص لهذه العملية فقط."
            ),
            colour=EmbedColor.ECONOMY,
        )
        await interaction.channel.send(
            content=interaction.user.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        def check(message: discord.Message) -> bool:
            return (
                message.author.id == interaction.user.id
                and message.channel.id == interaction.channel.id
                and not message.author.bot
            )

        try:
            message = await self.bot.wait_for("message", timeout=CONFIRM_TIMEOUT, check=check)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⌛ انتهت مهلة التحقق البالغة **60 ثانية**. لم يتم صرف المكافأة.",
                ephemeral=True,
            )
            return False

        if message.content.strip() != code:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            await interaction.followup.send(
                "❌ رمز التحقق غير صحيح. لم يتم صرف المكافأة اليومية.",
                ephemeral=True,
            )
            return False

        try:
            await message.delete()
        except discord.HTTPException:
            pass
        return True

    @app_commands.command(name="daily", description="احصل على مكافأتك اليومية من ANOCoin مرة كل 24 ساعة")
    async def daily(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message("❌ هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)

        key = (interaction.guild.id, interaction.user.id)
        async with self._lock_for(*key):
            user = await self.db.get_user(interaction.user.id, interaction.guild.id)
            if user is None:
                user = await self.db.create_user(interaction.user.id, interaction.guild.id)

            last_daily = int(user.get("last_daily") or 0)
            now = self._now()
            remaining = DAILY_COOLDOWN - (now - last_daily)
            if remaining > 0:
                hours, remainder = divmod(remaining, 3600)
                minutes, seconds = divmod(remainder, 60)
                return await interaction.response.send_message(
                    embed=EmbedFactory.warning(
                        "⏳ المكافأة اليومية غير متاحة",
                        f"لقد حصلت على مكافأتك بالفعل.\n\n"
                        f"يمكنك استلام المكافأة التالية بعد **{hours:02d} ساعة و{minutes:02d} دقيقة و{seconds:02d} ثانية**.\n"
                        "يتم احتساب المدة من وقت الاستلام الفعلي، وليس من منتصف الليل.",
                    ),
                    ephemeral=True,
                )

            await interaction.response.defer(ephemeral=True)
            if not await self._captcha(interaction):
                return

            # Re-read after the 60-second verification. This closes the race where
            # two /daily interactions were started before either one was completed.
            user = await self.db.get_user(interaction.user.id, interaction.guild.id)
            if user is None:
                user = await self.db.create_user(interaction.user.id, interaction.guild.id)
            now = self._now()
            if now - int(user.get("last_daily") or 0) < DAILY_COOLDOWN:
                return await interaction.followup.send(
                    "❌ تم استلام المكافأة اليومية بالفعل. لا يمكنك استلامها مرة ثانية قبل مرور 24 ساعة.",
                    ephemeral=True,
                )

            await self.db.add_balance(interaction.user.id, interaction.guild.id, DAILY_REWARD)
            await self.db.update_user(
                interaction.user.id,
                interaction.guild.id,
                {"last_daily": now},
            )
            balance = await self.db.get_balance(interaction.user.id)
            await interaction.followup.send(
                embed=EmbedFactory.create(
                    title="💎 المكافأة اليومية",
                    description=(
                        f"تمت إضافة **{DAILY_REWARD:,} ANOCoin** إلى رصيدك.\n\n"
                        f"🪙 رصيدك الحالي: **{balance:,} ANOCoin**\n"
                        "⏰ المكافأة التالية: بعد **24 ساعة كاملة**."
                    ),
                    color=EmbedColor.ECONOMY,
                ),
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    # Economy already registers its legacy /daily command. Remove that tree
    # registration before this replacement cog is added.
    bot.tree.remove_command("daily")
    await bot.add_cog(DailyFix(bot))
