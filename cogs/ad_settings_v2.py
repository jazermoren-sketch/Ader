from __future__ import annotations

import asyncio
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


class AdSettingsV2(commands.Cog):
    """Final advertising policy: no panel in ad rooms; administrators own global ad settings."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        db_path = Path(bot.config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))
        self.image_dir = db_path.parent / "ad_images"
        self._patched = False
        self._patch_task: asyncio.Task | None = None

    async def cog_load(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS ad_settings_v2 (
                guild_id INTEGER PRIMARY KEY,
                post_message TEXT NOT NULL DEFAULT '',
                giveaway_enabled INTEGER NOT NULL DEFAULT 0,
                giveaway_amount INTEGER NOT NULL DEFAULT 3000000,
                giveaway_duration INTEGER NOT NULL DEFAULT 3600,
                giveaway_sponsor_id INTEGER,
                image_path TEXT,
                updated_at REAL NOT NULL DEFAULT 0
            )
        """)
        self._patch_task = asyncio.create_task(self._wait_and_patch())

    def cog_unload(self):
        if self._patch_task and not self._patch_task.done():
            self._patch_task.cancel()

    async def _wait_and_patch(self):
        await self.bot.wait_until_ready()
        for _ in range(30):
            shop = self.bot.get_cog("AdvertisingShop")
            if shop:
                await self._patch_ad_system(shop)
                return
            await asyncio.sleep(0.25)

    async def _patch_ad_system(self, shop):
        if self._patched:
            return
        self._patched = True

        async def no_panel(channel):
            return None
        shop.render_panel = no_panel

        rows = await self.db.fetchall("SELECT channel_id, panel_message_id FROM ad_rooms WHERE active=1 AND panel_message_id IS NOT NULL")
        for row in rows:
            channel = self.bot.get_channel(int(row["channel_id"]))
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(int(row["panel_message_id"]))
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            await self.db.execute("UPDATE ad_rooms SET panel_message_id=NULL WHERE channel_id=?", (int(row["channel_id"]),))

        module = __import__("cogs.advertising_shop", fromlist=["AdModal"])
        module.AdModal.on_submit = self._modal_submit

    async def _modal_submit(self, modal, interaction: discord.Interaction):
        if interaction.user.id != modal.actor_id:
            return await interaction.response.send_message("❌ هذه العملية مخصصة لصاحب أمر الإعلان فقط.", ephemeral=True)

        controller = await self.db.fetchone("SELECT controller_id FROM ad_controllers WHERE channel_id=?", (modal.channel_id,))
        row = await self.db.fetchone("SELECT * FROM ad_rooms WHERE channel_id=? AND active=1", (modal.channel_id,))
        channel = interaction.guild.get_channel(modal.channel_id)
        if not row or not controller or int(controller["controller_id"]) != interaction.user.id or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("❌ هذه العملية لم تعد تحت صلاحيتك.", ephemeral=True)

        try:
            module = __import__("cogs.advertising_shop", fromlist=["clean_name"])
            await channel.edit(name=module.clean_name(str(modal.name.value)), category=None, reason="Ader advertisement")

            settings = await self.db.fetchone("SELECT * FROM ad_settings_v2 WHERE guild_id=?", (interaction.guild.id,))
            image_path = settings["image_path"] if settings else None
            post_message = str(settings["post_message"] or "").strip() if settings else ""
            mention = "@everyone" if modal.mention == "everyone" else "@here"
            content = f"{str(modal.text.value).rstrip()}\n\n{mention}"

            file = None
            if image_path and Path(image_path).is_file():
                file = discord.File(str(image_path), filename=Path(image_path).name)
            await channel.send(content, file=file, allowed_mentions=discord.AllowedMentions(everyone=True))

            if post_message:
                await channel.send(post_message)

            if settings and int(settings["giveaway_enabled"]):
                sponsor_id = settings["giveaway_sponsor_id"]
                sponsor = interaction.guild.get_member(int(sponsor_id)) if sponsor_id else None
                giveaway_cog = self.bot.get_cog("AdvertisingShop")
                if sponsor and giveaway_cog and hasattr(giveaway_cog, "create_giveaway"):
                    await giveaway_cog.create_giveaway(
                        interaction.guild,
                        sponsor,
                        channel.id,
                        int(settings["giveaway_amount"]),
                        int(settings["giveaway_duration"]),
                    )

            await interaction.response.send_message("✅ تم نشر الإعلان بنجاح.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ البوت لا يملك الصلاحيات الكافية.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("❌ تعذر نشر الإعلان حالياً.", ephemeral=True)

    @app_commands.command(name="ad-settings", description="إعدادات روم الإعلانات لجميع الرومات في السيرفر")
    @app_commands.describe(
        message="الرسالة التي يرسلها البوت بعد نشر الإعلان",
        giveaway="تفعيل أو تعطيل القيف أواي التلقائي",
        giveaway_amount="مبلغ القيف أواي بـ ANOCoin",
        giveaway_duration="مدة القيف أواي بالدقائق",
        image="صورة Attachment تستخدم مع كل إعلان",
        remove_image="حذف الصورة الحالية",
    )
    @app_commands.choices(giveaway=[app_commands.Choice(name="تفعيل", value="on"), app_commands.Choice(name="تعطيل", value="off")])
    @app_commands.default_permissions(administrator=True)
    async def ad_settings(self, interaction: discord.Interaction, message: str | None = None, giveaway: app_commands.Choice[str] | None = None, giveaway_amount: int | None = None, giveaway_duration: int | None = None, image: discord.Attachment | None = None, remove_image: bool = False):
        if not interaction.guild or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ هذا الأمر مخصص للإدارة فقط.", ephemeral=True)

        current = await self.db.fetchone("SELECT * FROM ad_settings_v2 WHERE guild_id=?", (interaction.guild.id,))
        current_message = str(current["post_message"] or "") if current else ""
        enabled = bool(current["giveaway_enabled"]) if current else False
        amount = int(current["giveaway_amount"]) if current else 3_000_000
        duration = int(current["giveaway_duration"]) if current else 3600
        current_image = str(current["image_path"] or "") if current else ""

        if message is not None:
            if len(message) > 4000:
                return await interaction.response.send_message("❌ الرسالة طويلة جداً. الحد الأقصى 4000 حرف.", ephemeral=True)
            current_message = message
        if giveaway is not None:
            enabled = giveaway.value == "on"
        if giveaway_amount is not None:
            if giveaway_amount <= 0 or giveaway_amount > 2_000_000_000:
                return await interaction.response.send_message("❌ مبلغ القيف أواي غير صالح.", ephemeral=True)
            amount = giveaway_amount
        if giveaway_duration is not None:
            if giveaway_duration < 1 or giveaway_duration > 10080:
                return await interaction.response.send_message("❌ مدة القيف أواي يجب أن تكون بين دقيقة و7 أيام.", ephemeral=True)
            duration = giveaway_duration * 60

        self.image_dir.mkdir(parents=True, exist_ok=True)
        if remove_image:
            if current_image:
                try:
                    Path(current_image).unlink(missing_ok=True)
                except OSError:
                    pass
            current_image = ""
        if image is not None:
            if not (image.content_type or "").startswith("image/"):
                return await interaction.response.send_message("❌ الملف المرفوع يجب أن يكون صورة.", ephemeral=True)
            if image.size > 8 * 1024 * 1024:
                return await interaction.response.send_message("❌ حجم الصورة يجب ألا يتجاوز 8MB.", ephemeral=True)
            suffix = Path(image.filename).suffix.lower() or ".png"
            path = self.image_dir / f"guild_{interaction.guild.id}{suffix}"
            try:
                await image.save(path)
            except (OSError, discord.HTTPException):
                return await interaction.response.send_message("❌ تعذر حفظ الصورة.", ephemeral=True)
            current_image = str(path)

        sponsor_id = interaction.user.id if enabled else (current["giveaway_sponsor_id"] if current else None)
        await self.db.execute(
            """INSERT INTO ad_settings_v2(guild_id,post_message,giveaway_enabled,giveaway_amount,giveaway_duration,giveaway_sponsor_id,image_path,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(guild_id) DO UPDATE SET post_message=excluded.post_message,giveaway_enabled=excluded.giveaway_enabled,giveaway_amount=excluded.giveaway_amount,giveaway_duration=excluded.giveaway_duration,giveaway_sponsor_id=excluded.giveaway_sponsor_id,image_path=excluded.image_path,updated_at=excluded.updated_at""",
            (interaction.guild.id, current_message, int(enabled), amount, duration, sponsor_id, current_image or None, time.time()),
        )

        image_state = "محددة" if current_image else "غير محددة"
        giveaway_state = f"مفعل — {amount:,} ANOCoin / {duration // 60} دقيقة" if enabled else "معطل"
        await interaction.response.send_message(
            "**⚙️ إعدادات رومات الإعلانات**\n\n"
            f"📝 الرسالة بعد الإعلان: {'محددة' if current_message else 'غير محددة'}\n"
            f"🖼️ الصورة: {image_state}\n"
            f"🎁 القيف أواي: {giveaway_state}\n\n"
            "هذه الإعدادات تطبق على **جميع رومات الإعلانات** في هذا السيرفر.\n"
            "ولا توجد أي لوحة تحكم داخل رومات الإعلانات.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.TextChannel):
            return
        row = await self.db.fetchone("SELECT owner_id FROM ad_rooms WHERE channel_id=? AND active=1", (channel.id,))
        if not row:
            return
        owner = channel.guild.get_member(int(row["owner_id"]))
        if owner:
            try:
                overwrite = channel.overwrites_for(owner)
                overwrite.view_channel = True
                overwrite.send_messages = False
                overwrite.manage_channels = False
                overwrite.manage_messages = False
                overwrite.attach_files = False
                overwrite.embed_links = False
                await channel.set_permissions(owner, overwrite=overwrite, reason="Ader advertising room: owner has no room controls")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def setup(bot):
    await bot.add_cog(AdSettingsV2(bot))
