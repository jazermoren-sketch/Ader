from __future__ import annotations

import re

import discord
from discord.ext import commands

from cogs.advertising_shop import PrefixAdView, WriteAdView, ad_embed, clean_name


def _strip_ad_mentions(text: str) -> str:
    """Remove every user/role/@everyone/@here mention from advertiser text.

    The only broadcast mention that is ever sent is the one selected for the
    advertisement room, and it is appended at the end of the final message.
    """
    value = text or ""
    value = re.sub(r"<@!?\d+>", "", value)
    value = re.sub(r"<@&\d+>", "", value)
    value = re.sub(r"@(everyone|here)\b", "", value, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+\n", "\n", value).strip()


class FixedAdModal(discord.ui.Modal, title="اكتب إعلانك"):
    text = discord.ui.TextInput(label="اكتب إعلانك", style=discord.TextStyle.paragraph, max_length=4000)
    name = discord.ui.TextInput(label="اسم الروم", max_length=90)

    def __init__(self, cog, owner_id: int, channel_id: int, mention: str, actor_id: int, control_message_id: int | None):
        super().__init__(custom_id=f"ader:fixed-admodal:{channel_id}:{actor_id}")
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.mention = mention
        self.actor_id = actor_id
        self.control_message_id = control_message_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ هذه العملية ليست لك.", ephemeral=True)

        row = await self.cog.db.fetchone(
            "SELECT * FROM ad_rooms WHERE channel_id=? AND active=1",
            (self.channel_id,),
        )
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if channel is None and interaction.guild is not None:
            try:
                channel = await interaction.guild.fetch_channel(self.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        if not row or int(row["owner_id"]) != self.owner_id or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("❌ الروم غير موجود.", ephemeral=True)

        try:
            await channel.edit(name=clean_name(str(self.name.value)), reason="Ader advertisement")
            clean_text = _strip_ad_mentions(str(self.text.value))
            mention_text = "@everyone" if self.mention == "everyone" else "@here"
            content = f"{clean_text}\n\n{mention_text}".strip()
            message = await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False),
            )
            await self.cog.run_custom_event(channel, "after_ad", message)
            await self.cog.render_panel(channel)
            await interaction.response.send_message(
                embed=ad_embed(f"تم نشر إعلانك في {channel.mention} ✅"),
                ephemeral=True,
            )
            await self.cog.finish_control_message(interaction, self.control_message_id, channel)
        except discord.Forbidden:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ البوت لا يملك الصلاحيات الكافية.", ephemeral=True)
            else:
                await interaction.followup.send("❌ البوت لا يملك الصلاحيات الكافية.", ephemeral=True)
        except discord.HTTPException:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ تعذر إرسال الإعلان حالياً.", ephemeral=True)
            else:
                await interaction.followup.send("❌ تعذر إرسال الإعلان حالياً.", ephemeral=True)


class FixedWriteAdView(WriteAdView):
    def __init__(self, cog, owner_id: int, channel_id: int, mention: str, control_message_id: int | None):
        super().__init__(cog, owner_id, channel_id, mention, control_message_id)

    @discord.ui.button(label="اكتب إعلانك", emoji="📢", style=discord.ButtonStyle.primary)
    async def write(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ فقط صاحب الروم يمكنه كتابة الإعلان.", ephemeral=True)
        await interaction.response.send_modal(
            FixedAdModal(
                self.cog,
                self.owner_id,
                self.channel_id,
                self.mention,
                self.owner_id,
                self.control_message_id,
            )
        )


class FixedPrefixAdView(PrefixAdView):
    async def pick(self, interaction, mention):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ هذا التحكم ليس لك.", ephemeral=True)
        owner = interaction.guild.get_member(self.owner_id) if interaction.guild else None
        owner_mention = owner.mention if owner else ""
        await interaction.response.edit_message(
            content=f"{owner_mention}\nاضغط لكتابة إعلانك.",
            view=FixedWriteAdView(
                self.cog,
                self.owner_id,
                self.channel_id,
                mention,
                self.control_message_id,
            ),
        )


class AdRoomCommandOverride(commands.Cog):
    """Always creates a brand-new advertising room for every $اعلان request."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.remove_command("اعلان")

    @commands.command(name="اعلان")
    async def اعلان(self, ctx: commands.Context, member: discord.Member | None = None):
        if not ctx.guild:
            return

        shop_cog = self.bot.get_cog("AdvertisingShop")
        if shop_cog is None:
            return await ctx.reply("❌ نظام الإعلانات غير جاهز حالياً.", mention_author=False)

        if not await shop_cog.authorized(ctx.author):
            return await ctx.reply("❌ ليست لديك صلاحية استعمال أمر `$اعلان`.", mention_author=False)

        if member is None:
            return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
        if member.bot:
            return await ctx.reply("❌ لا يمكن إنشاء إعلان لبوت.", mention_author=False)

        me = ctx.guild.me
        if me is None or not me.guild_permissions.manage_channels:
            return await ctx.reply(
                "❌ البوت يحتاج صلاحية Manage Channels لإنشاء روم إعلان جديد.",
                mention_author=False,
            )

        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                read_message_history=True,
            ),
            me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
                read_message_history=True,
            ),
        }

        try:
            channel = await ctx.guild.create_text_channel(
                clean_name(f"ad-{member.display_name}"),
                category=None,
                overwrites=overwrites,
                reason=f"Ader advertisement room for {member}",
            )
            await shop_cog.db.execute(
                "INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type,active) VALUES(?,?,?,?,1)",
                (ctx.guild.id, channel.id, member.id, "everyone"),
            )
        except (discord.Forbidden, discord.HTTPException):
            return await ctx.reply("❌ تعذر إنشاء روم إعلان جديد.", mention_author=False)
        except Exception:
            try:
                await channel.delete(reason="Ader rollback after database error")
            except Exception:
                pass
            return await ctx.reply("❌ تعذر تجهيز روم الإعلان الجديد.", mention_author=False)

        view = FixedPrefixAdView(shop_cog, ctx.author.id, member.id, channel.id)
        control = await ctx.reply(
            f"{member.mention}\n🏠 تم إنشاء روم إعلان جديد: {channel.mention}\n**اختر نوع المنشن حق الروم**",
            mention_author=False,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        view.control_message_id = control.id


async def setup(bot: commands.Bot):
    await bot.add_cog(AdRoomCommandOverride(bot))
