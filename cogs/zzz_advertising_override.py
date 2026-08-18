from __future__ import annotations

from pathlib import Path

import discord
from discord.ext import commands

from cogs.advertising_shop import clean_name


class AdWriteModal(discord.ui.Modal, title="اكتب إعلانك"):
    text = discord.ui.TextInput(label="الإعلان", style=discord.TextStyle.paragraph, max_length=4000, required=True)
    room_name = discord.ui.TextInput(label="اسم الروم", max_length=90, required=True)

    def __init__(self, cog, controller_id: int, target_id: int, mention: str):
        super().__init__(custom_id=f"ader:ad-write:{controller_id}:{target_id}")
        self.cog = cog
        self.controller_id = controller_id
        self.target_id = target_id
        self.mention = mention

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.controller_id:
            return await interaction.response.send_message("❌ هذه العملية مخصصة لصاحب أمر `$اعلان` فقط.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)

        settings = await self.cog.db.fetchone("SELECT * FROM ad_settings_v2 WHERE guild_id=?", (interaction.guild.id,))
        if settings is None:
            settings = {"post_message": "", "giveaway_enabled": 0, "giveaway_amount": 3_000_000, "giveaway_duration": 3600, "giveaway_sponsor_id": None, "image_path": None}

        target = interaction.guild.get_member(self.target_id)
        if target is None:
            return await interaction.followup.send("❌ العضو المحدد لم يعد موجوداً في السيرفر.", ephemeral=True)
        me = interaction.guild.me
        if me is None or not me.guild_permissions.manage_channels:
            return await interaction.followup.send("❌ البوت يحتاج إلى صلاحية **Manage Channels**.", ephemeral=True)

        existing = await self.cog.db.fetchone("SELECT channel_id FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1", (interaction.guild.id, self.target_id))
        if existing:
            old = interaction.guild.get_channel(int(existing["channel_id"]))
            if old:
                return await interaction.followup.send("❌ هذا العضو لديه روم إعلان نشط بالفعل.", ephemeral=True)
            await self.cog.db.execute("UPDATE ad_rooms SET active=0 WHERE channel_id=?", (int(existing["channel_id"]),))

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
            target: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, attach_files=False, embed_links=False, manage_channels=False, manage_messages=False),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        }
        try:
            channel = await interaction.guild.create_text_channel(
                clean_name(str(self.room_name.value)),
                category=None,
                overwrites=overwrites,
                reason="$اعلان: إنشاء روم إعلان بعد كتابة الإعلان",
            )
            await self.cog.db.execute(
                "INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type) VALUES(?,?,?,?)",
                (interaction.guild.id, channel.id, self.target_id, self.mention),
            )
            content = f"{str(self.text.value).rstrip()}\n\n{'@everyone' if self.mention == 'everyone' else '@here'}"
            image_path = settings["image_path"] if isinstance(settings, dict) else settings["image_path"]
            file = None
            if image_path and Path(str(image_path)).is_file():
                file = discord.File(str(image_path), filename=Path(str(image_path)).name)
            await channel.send(content, file=file, allowed_mentions=discord.AllowedMentions(everyone=True))

            post_message = str(settings["post_message"] or "").strip()
            if post_message:
                await channel.send(post_message)

            if int(settings["giveaway_enabled"]):
                sponsor_id = settings["giveaway_sponsor_id"]
                sponsor = interaction.guild.get_member(int(sponsor_id)) if sponsor_id else None
                if sponsor:
                    await self.cog.ad_cog.create_giveaway(
                        interaction.guild,
                        sponsor,
                        channel.id,
                        int(settings["giveaway_amount"]),
                        int(settings["giveaway_duration"]),
                    )

            await interaction.followup.send(f"✅ تم إنشاء روم الإعلان ونشر الإعلان: {channel.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ البوت لا يملك الصلاحيات الكافية لإنشاء الروم أو النشر فيه.", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("❌ تعذر إنشاء روم الإعلان حالياً.", ephemeral=True)


class AdWriteView(discord.ui.View):
    def __init__(self, cog, controller_id: int, target_id: int, mention: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.controller_id = controller_id
        self.target_id = target_id
        self.mention = mention
        button = discord.ui.Button(label="اكتب إعلانك", emoji="📝", style=discord.ButtonStyle.primary, custom_id=f"ader:write:{controller_id}:{target_id}:{mention}")
        button.callback = self.write
        self.add_item(button)

    async def write(self, interaction: discord.Interaction):
        if interaction.user.id != self.controller_id:
            return await interaction.response.send_message("❌ هذا الزر مخصص لصاحب أمر `$اعلان` فقط.", ephemeral=True)
        await interaction.response.send_modal(AdWriteModal(self.cog, self.controller_id, self.target_id, self.mention))


class MentionChoiceView(discord.ui.View):
    def __init__(self, cog, controller_id: int, target_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.controller_id = controller_id
        self.target_id = target_id
        for label, style, mention in (("Everyone", discord.ButtonStyle.danger, "everyone"), ("Here", discord.ButtonStyle.success, "here")):
            button = discord.ui.Button(label=label, style=style, custom_id=f"ader:mention:{controller_id}:{target_id}:{mention}")
            button.callback = lambda interaction, m=mention: self.choose(interaction, m)
            self.add_item(button)

    async def choose(self, interaction: discord.Interaction, mention: str):
        if interaction.user.id != self.controller_id:
            return await interaction.response.send_message("❌ هذه الأزرار مخصصة لصاحب أمر `$اعلان` فقط.", ephemeral=True)
        target = interaction.guild.get_member(self.target_id)
        target_mention = target.mention if target else f"<@{self.target_id}>"
        content = f"**تم اختيار نوع المنشن:** {'@everyone' if mention == 'everyone' else '@here'}\n\nاضغط على الزر بالأسفل لكتابة الإعلان.\n{target_mention}"
        await interaction.response.edit_message(content=content, view=AdWriteView(self.cog, self.controller_id, self.target_id, mention), allowed_mentions=discord.AllowedMentions(users=True))


class AdvertisingCommandOverride(commands.Cog):
    """Canonical $اعلان flow: choose mention -> edit message -> write button -> create room only after submit."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.ad_cog = None
        self._processing: set[int] = set()

    async def cog_load(self):
        self.ad_cog = self.bot.get_cog("AdvertisingShop")
        if self.ad_cog is None:
            return
        old = self.bot.get_command("اعلان")
        if old is not None:
            self.bot.remove_command(old.name)

    @commands.command(name="اعلان")
    async def advertise(self, ctx: commands.Context, member: discord.Member | None = None):
        if ctx.guild is None or ctx.message.id in self._processing:
            return
        self._processing.add(ctx.message.id)
        try:
            if member is None:
                return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
            if not await self.ad_cog.authorized(ctx.author):
                return await ctx.reply("❌ هذا الأمر محمي. يلزم Administrator أو رتبة إعلان مسموح بها.", mention_author=False)
            await ctx.reply(
                f"{member.mention}\n**اختر نوع المنشن حق الروم**",
                mention_author=False,
                view=MentionChoiceView(self, ctx.author.id, member.id),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        finally:
            self._processing.discard(ctx.message.id)


async def setup(bot):
    await bot.add_cog(AdvertisingCommandOverride(bot))
