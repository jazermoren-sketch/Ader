from __future__ import annotations

import discord
from discord.ext import commands

from cogs.advertising_shop import AdModal, clean_name


class FreeAdSetupView(discord.ui.View):
    def __init__(self, cog, actor_id: int, owner_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.actor_id = actor_id
        self.owner_id = owner_id
        for label, style, mention in [("Everyone", discord.ButtonStyle.danger, "everyone"), ("Here", discord.ButtonStyle.success, "here")]:
            button = discord.ui.Button(label=label, style=style)
            button.callback = lambda interaction, m=mention: self.select(interaction, m)
            self.add_item(button)

    async def select(self, interaction: discord.Interaction, mention: str):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ هذا التحكم ليس لك.", ephemeral=True)
        row = await self.cog.db.fetchone(
            "SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1",
            (interaction.guild.id, self.owner_id),
        )
        if row:
            channel_id = int(row["channel_id"])
        else:
            channel = await self.cog.create_command_ad_room(interaction.guild, self.owner_id)
            if channel is None:
                return await interaction.response.send_message("❌ تعذر إنشاء روم الإعلان. تأكد من أن البوت يملك صلاحية **Manage Channels**.", ephemeral=True)
            channel_id = channel.id
        await self.cog.db.execute(
            "UPDATE ad_rooms SET mention_type=? WHERE guild_id=? AND owner_id=? AND active=1",
            (mention, interaction.guild.id, self.owner_id),
        )
        await interaction.response.send_modal(AdModal(self.cog.ad_cog, self.owner_id, channel_id, mention, self.actor_id))


class AdvertisingCommandOverride(commands.Cog):
    """Single canonical $اعلان handler with idempotency and free admin provisioning."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._processing: set[int] = set()
        self.ad_cog = None

    async def cog_load(self):
        self.ad_cog = self.bot.get_cog("AdvertisingShop")
        if self.ad_cog is None:
            return
        old = self.bot.get_command("اعلان")
        if old is not None:
            self.bot.remove_command(old.name)
        self.bot.add_command(self.advertise)

    def cog_unload(self):
        current = self.bot.get_command("اعلان")
        if current is not None and current.name == "اعلان":
            self.bot.remove_command("اعلان")

    async def create_command_ad_room(self, guild: discord.Guild, owner_id: int):
        member = guild.get_member(owner_id)
        me = guild.me
        if member is None or me is None or not me.guild_permissions.manage_channels:
            return None
        existing = await self.db.fetchone(
            "SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1",
            (guild.id, owner_id),
        )
        if existing:
            return guild.get_channel(int(existing["channel_id"]))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        }
        try:
            channel = await guild.create_text_channel(
                clean_name(f"ad-{member.display_name}"), category=None, overwrites=overwrites,
                reason="$اعلان: إنشاء روم إعلان للإدارة",
            )
            await self.db.execute(
                "INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type) VALUES(?,?,?,?)",
                (guild.id, channel.id, owner_id, "everyone"),
            )
            await channel.send(member.mention, allowed_mentions=discord.AllowedMentions(users=True))
            await self.ad_cog.render_panel(channel)
            return channel
        except (discord.Forbidden, discord.HTTPException):
            return None

    @commands.command(name="اعلان")
    async def advertise(self, ctx: commands.Context, member: discord.Member | None = None):
        message_id = ctx.message.id
        if message_id in self._processing:
            return
        self._processing.add(message_id)
        try:
            if ctx.guild is None:
                return
            if member is None:
                return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
            if not await self.ad_cog.authorized(ctx.author):
                return await ctx.reply("❌ هذا الأمر محمي. يلزم Administrator أو رتبة إعلان مسموح بها.", mention_author=False)

            # The administrative $اعلان command is independent of Shop purchases.
            row = await self.db.fetchone(
                "SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1",
                (ctx.guild.id, member.id),
            )
            if row is None:
                if await self.create_command_ad_room(ctx.guild, member.id) is None:
                    return await ctx.reply("❌ تعذر إنشاء روم الإعلان. تأكد من صلاحية **Manage Channels** للبوت.", mention_author=False)
            await ctx.reply(
                f"**اختر نوع المنشن حق الروم**\n{member.mention}",
                mention_author=False,
                view=FreeAdSetupView(self, ctx.author.id, member.id),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        finally:
            self._processing.discard(message_id)


async def setup(bot):
    await bot.add_cog(AdvertisingCommandOverride(bot))
