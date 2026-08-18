from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path

import discord
from discord.ext import commands


class AdComposeModal(discord.ui.Modal, title="اكتب إعلانك"):
    ad_text = discord.ui.TextInput(
        label="الإعلان", placeholder="اكتب محتوى الإعلان هنا...",
        style=discord.TextStyle.paragraph, max_length=4000, required=True,
    )
    room_name = discord.ui.TextInput(
        label="اسم الروم", placeholder="اسم روم الإعلان", max_length=90, required=True,
    )

    def __init__(self, cog, guild_id: int, target_id: int, invoker_id: int, mention_type: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.target_id = target_id
        self.invoker_id = invoker_id
        self.mention_type = mention_type

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            return await interaction.response.send_message("❌ هذه العملية غير صالحة.", ephemeral=True)
        if interaction.user.id != self.target_id:
            return await interaction.response.send_message("❌ هذا الزر مخصص للعضو الذي تم منشنه في أمر `$اعلان` فقط.", ephemeral=True)

        pending = await self.cog.db.fetchone(
            "SELECT * FROM ad_pending WHERE guild_id=? AND target_id=? AND invoker_id=? AND active=1",
            (self.guild_id, self.target_id, self.invoker_id),
        )
        if not pending or not str(pending["mention_type"]):
            return await interaction.response.send_message("❌ انتهت صلاحية عملية الإعلان. أعد استخدام `$اعلان @user`.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild.me or not guild.me.guild_permissions.manage_channels:
            return await interaction.followup.send("❌ البوت يحتاج إلى صلاحية Manage Channels لإنشاء روم الإعلان.", ephemeral=True)

        name = self.cog.clean_name(str(self.room_name.value))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, manage_messages=True,
                attach_files=True, embed_links=True, read_message_history=True,
            ),
        }
        try:
            channel = await guild.create_text_channel(
                name, category=None, overwrites=overwrites,
                reason=f"Ader advertising room for {interaction.user} created by {self.invoker_id}",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.followup.send("❌ تعذر إنشاء روم الإعلان. تأكد من صلاحيات البوت.", ephemeral=True)

        try:
            await self.cog.db.execute(
                """INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type,template,active)
                   VALUES(?,?,?,?,?,1)
                   ON CONFLICT(channel_id) DO UPDATE SET
                     owner_id=excluded.owner_id, mention_type=excluded.mention_type,
                     template=excluded.template, active=1""",
                (guild.id, channel.id, self.target_id, self.mention_type, ""),
            )
            await self.cog.db.execute(
                "UPDATE ad_pending SET active=0, channel_id=? WHERE guild_id=? AND target_id=? AND invoker_id=?",
                (channel.id, self.guild_id, self.target_id, self.invoker_id),
            )
            await self.cog.db.execute(
                "INSERT INTO ad_controllers(channel_id,controller_id) VALUES(?,?) ON CONFLICT(channel_id) DO UPDATE SET controller_id=excluded.controller_id",
                (channel.id, self.invoker_id),
            )

            mention = "@everyone" if self.mention_type == "everyone" else "@here"
            # The requested mention is always the final line of the advertisement.
            await channel.send(
                f"{str(self.ad_text.value).rstrip()}\n\n{mention}",
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )

            settings = await self.cog.db.fetchone("SELECT * FROM ad_settings_v2 WHERE guild_id=?", (guild.id,))
            if settings and str(settings["post_message"] or "").strip():
                await channel.send(str(settings["post_message"]))

            if settings and int(settings["giveaway_enabled"] or 0):
                await self.cog.start_configured_giveaway(guild, channel, settings)

            # Image is intentionally sent last, after the advertisement, post-message and giveaway.
            if settings and settings["image_path"]:
                path = Path(str(settings["image_path"]))
                if path.exists():
                    try:
                        await channel.send(file=discord.File(str(path), filename=path.name))
                    except (discord.HTTPException, OSError):
                        pass

            await interaction.followup.send(f"✅ تم إنشاء روم الإعلان {channel.mention} ونشر الإعلان بنجاح.", ephemeral=True)
        except Exception:
            try:
                await channel.delete(reason="Ader advertising setup failed")
            except (discord.Forbidden, discord.HTTPException):
                pass
            raise


class MentionChoiceView(discord.ui.View):
    def __init__(self, cog, guild_id: int, target_id: int, invoker_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id
        self.target_id = target_id
        self.invoker_id = invoker_id

    async def _choose(self, interaction: discord.Interaction, mention_type: str):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message("❌ غير صاحب أمر `$اعلان` هو المسموح له باختيار Everyone أو Here.", ephemeral=True)
        await self.cog.db.execute(
            "INSERT INTO ad_pending(guild_id,target_id,invoker_id,mention_type,active,created_at) VALUES(?,?,?,?,1,?) "
            "ON CONFLICT(guild_id,target_id,invoker_id) DO UPDATE SET mention_type=excluded.mention_type,active=1,created_at=excluded.created_at",
            (self.guild_id, self.target_id, self.invoker_id, mention_type, time.time()),
        )
        view = TargetComposeView(self.cog, self.guild_id, self.target_id, self.invoker_id, mention_type)
        label = "@everyone" if mention_type == "everyone" else "@here"
        await interaction.response.edit_message(
            content=f"**تم اختيار {label}**\n\n<@{self.target_id}>، اضغط الزر بالأسفل لكتابة إعلانك.",
            view=view, allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(label="Everyone", emoji="🔴", style=discord.ButtonStyle.danger, custom_id="ader:ad:choose:everyone")
    async def everyone(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "everyone")

    @discord.ui.button(label="Here", emoji="🟢", style=discord.ButtonStyle.success, custom_id="ader:ad:choose:here")
    async def here(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "here")


class TargetComposeView(discord.ui.View):
    def __init__(self, cog, guild_id: int, target_id: int, invoker_id: int, mention_type: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id
        self.target_id = target_id
        self.invoker_id = invoker_id
        self.mention_type = mention_type

    @discord.ui.button(label="اكتب إعلانك", emoji="📝", style=discord.ButtonStyle.primary, custom_id="ader:ad:compose")
    async def compose(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            return await interaction.response.send_message("❌ هذا الزر مخصص للعضو الذي تم منشنه في أمر `$اعلان` فقط.", ephemeral=True)
        await interaction.response.send_modal(AdComposeModal(self.cog, self.guild_id, self.target_id, self.invoker_id, self.mention_type))


class AdCommandControllerPatch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS ad_controllers(channel_id INTEGER PRIMARY KEY, controller_id INTEGER NOT NULL)"
        )
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ad_pending(
                guild_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                invoker_id INTEGER NOT NULL,
                mention_type TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                channel_id INTEGER,
                created_at REAL NOT NULL,
                PRIMARY KEY(guild_id,target_id,invoker_id)
            )"""
        )
        self.shop = self.bot.get_cog("AdvertisingShop")
        if not self.shop:
            return
        self._install_command()
        self._install_helpers()

    def _install_command(self):
        command = next((c for c in self.shop.get_commands() if getattr(c, "name", "") == "اعلان"), None)
        if command is None:
            return

        async def callback(cog, ctx, member: discord.Member | None = None):
            if not await cog.authorized(ctx.author):
                return await ctx.reply("❌ هذا الأمر محمي. يلزم Administrator أو رتبة مسموح بها.", mention_author=False)
            if member is None:
                return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
            if member.bot:
                return await ctx.reply("❌ لا يمكن إنشاء إعلان لبوت.", mention_author=False)
            await cog.db.execute(
                "UPDATE ad_pending SET active=0 WHERE guild_id=? AND (target_id=? OR invoker_id=?)",
                (ctx.guild.id, member.id, ctx.author.id),
            )
            await ctx.reply(
                f"{member.mention}\n**اختر نوع المنشن حق الروم**",
                mention_author=False,
                view=MentionChoiceView(cog, ctx.guild.id, member.id, ctx.author.id),
                allowed_mentions=discord.AllowedMentions(users=True),
            )

        command.callback = callback

    def _install_helpers(self):
        if getattr(self.shop, "_configured_giveaway_installed", False):
            return

        async def start_configured_giveaway(guild, channel, settings):
            amount = int(settings["giveaway_amount"])
            duration = int(settings["giveaway_duration"])
            sponsor_id = int(settings["giveaway_sponsor_id"] or 0)
            if sponsor_id <= 0 or amount <= 0:
                return
            balance = await self.db.get_balance(sponsor_id)
            if balance < amount or not await self.db.remove_balance(sponsor_id, guild.id, amount):
                await channel.send("❌ تعذر إنشاء القيف أواي لأن رصيد الجهة الراعية غير كافٍ.")
                return
            ends_at = time.time() + duration
            cur = await self.db.execute(
                "INSERT INTO ad_giveaways(guild_id,channel_id,owner_id,amount,ends_at,ended) VALUES(?,?,?,?,?,0)",
                (guild.id, channel.id, sponsor_id, amount, ends_at),
            )
            giveaway_id = cur.lastrowid
            from cogs.advertising_shop import GiveawayView
            embed = discord.Embed(
                title="🎁 قيف أواي ANOCoin",
                description=f"الجائزة: **{amount:,} ANOCoin**\nينتهي: <t:{int(ends_at)}:R>\nاضغط **مشاركة** للدخول.",
                colour=discord.Colour.green(),
            )
            await channel.send(embed=embed, view=GiveawayView(self.shop, giveaway_id))
            self.bot.add_view(GiveawayView(self.shop, giveaway_id))

        self.shop.start_configured_giveaway = start_configured_giveaway
        self.shop._configured_giveaway_installed = True

    @staticmethod
    def clean_name(value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "")
        value = "".join(c for c in value if c.isalnum() or c in " _-")
        value = re.sub(r"\s+", "-", value)
        value = re.sub(r"-+", "-", value).strip("-_").lower()[:90]
        return value or "advertisement"


async def setup(bot):
    await bot.add_cog(AdCommandControllerPatch(bot))
