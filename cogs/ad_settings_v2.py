from __future__ import annotations

import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


class AdSettingsV2(commands.Cog):
    """Administrator-only global settings for all advertising rooms."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        db_path = Path(bot.config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))
        self.image_dir = db_path.parent / "ad_images"

    async def cog_load(self):
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ad_settings_v2 (
                guild_id INTEGER PRIMARY KEY,
                post_message TEXT NOT NULL DEFAULT '',
                giveaway_enabled INTEGER NOT NULL DEFAULT 0,
                giveaway_amount INTEGER NOT NULL DEFAULT 3000000,
                giveaway_duration INTEGER NOT NULL DEFAULT 3600,
                giveaway_sponsor_id INTEGER,
                image_path TEXT,
                updated_at REAL NOT NULL DEFAULT 0
            )"""
        )

    @app_commands.command(name="ad-settings", description="تخصيص إعدادات رومات الإعلانات")
    @app_commands.describe(
        message="الرسالة التي تُرسل بعد الإعلان",
        giveaway="تفعيل أو تعطيل القيف أواي",
        giveaway_amount="مبلغ القيف أواي بـ ANOCoin",
        giveaway_duration="مدة القيف أواي بالدقائق",
        image="الصورة التي تستخدم مع الإعلانات (Attachment)",
        remove_image="حذف الصورة الحالية",
    )
    @app_commands.choices(giveaway=[
        app_commands.Choice(name="تفعيل", value="on"),
        app_commands.Choice(name="تعطيل", value="off"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def ad_settings(
        self,
        interaction: discord.Interaction,
        message: str | None = None,
        giveaway: app_commands.Choice[str] | None = None,
        giveaway_amount: int | None = None,
        giveaway_duration: int | None = None,
        image: discord.Attachment | None = None,
        remove_image: bool = False,
    ):
        if not interaction.guild or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ هذا الأمر مخصص للـAdministrator فقط.", ephemeral=True)

        row = await self.db.fetchone("SELECT * FROM ad_settings_v2 WHERE guild_id=?", (interaction.guild.id,))
        post_message = str(row["post_message"] or "") if row else ""
        enabled = bool(row["giveaway_enabled"]) if row else False
        amount = int(row["giveaway_amount"]) if row else 3_000_000
        duration = int(row["giveaway_duration"]) if row else 3600
        image_path = str(row["image_path"] or "") if row else ""

        if message is not None:
            if len(message) > 4000:
                return await interaction.response.send_message("❌ الرسالة تتجاوز 4000 حرف.", ephemeral=True)
            post_message = message
        if giveaway is not None:
            enabled = giveaway.value == "on"
        if giveaway_amount is not None:
            if not 1 <= giveaway_amount <= 2_000_000_000:
                return await interaction.response.send_message("❌ مبلغ القيف أواي غير صالح.", ephemeral=True)
            amount = giveaway_amount
        if giveaway_duration is not None:
            if not 1 <= giveaway_duration <= 10080:
                return await interaction.response.send_message("❌ مدة القيف أواي يجب أن تكون بين دقيقة و7 أيام.", ephemeral=True)
            duration = giveaway_duration * 60

        self.image_dir.mkdir(parents=True, exist_ok=True)
        if remove_image:
            if image_path:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except OSError:
                    pass
            image_path = ""
        if image is not None:
            if not (image.content_type or "").startswith("image/"):
                return await interaction.response.send_message("❌ يجب أن يكون الملف المرفوع صورة.", ephemeral=True)
            if image.size > 8 * 1024 * 1024:
                return await interaction.response.send_message("❌ حجم الصورة يجب ألا يتجاوز 8MB.", ephemeral=True)
            suffix = Path(image.filename).suffix.lower() or ".png"
            path = self.image_dir / f"guild_{interaction.guild.id}{suffix}"
            try:
                await image.save(path)
            except (OSError, discord.HTTPException):
                return await interaction.response.send_message("❌ تعذر حفظ الصورة.", ephemeral=True)
            image_path = str(path)

        await self.db.execute(
            """INSERT INTO ad_settings_v2(guild_id,post_message,giveaway_enabled,giveaway_amount,giveaway_duration,giveaway_sponsor_id,image_path,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET
              post_message=excluded.post_message,
              giveaway_enabled=excluded.giveaway_enabled,
              giveaway_amount=excluded.giveaway_amount,
              giveaway_duration=excluded.giveaway_duration,
              giveaway_sponsor_id=excluded.giveaway_sponsor_id,
              image_path=excluded.image_path,
              updated_at=excluded.updated_at""",
            (interaction.guild.id, post_message, int(enabled), amount, duration, interaction.user.id if enabled else (row["giveaway_sponsor_id"] if row else None), image_path or None, time.time()),
        )

        await interaction.response.send_message(
            "**⚙️ تم حفظ إعدادات رومات الإعلانات**\n\n"
            f"📝 الرسالة بعد الإعلان: {'محددة' if post_message else 'غير محددة'}\n"
            f"🖼️ الصورة: {'محددة' if image_path else 'غير محددة'}\n"
            f"🎁 القيف أواي: {'مفعل' if enabled else 'معطل'}\n"
            f"💰 المبلغ: **{amount:,} ANOCoin**\n"
            f"⏱️ المدة: **{duration // 60} دقيقة**\n\n"
            "هذه الإعدادات تطبق على جميع رومات الإعلانات الجديدة.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(AdSettingsV2(bot))
