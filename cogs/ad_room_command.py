from __future__ import annotations

import discord
from discord.ext import commands

from cogs.advertising_shop import PrefixAdView, clean_name


class AdRoomCommandOverride(commands.Cog):
    """Always creates a new advertising room; never selects an old one."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Replace the legacy command after AdvertisingShop has been loaded.
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
        except (discord.Forbidden, discord.HTTPException):
            return await ctx.reply("❌ تعذر إنشاء روم إعلان جديد.", mention_author=False)

        await shop_cog.db.execute(
            "INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type) VALUES(?,?,?,?)",
            (ctx.guild.id, channel.id, member.id, "everyone"),
        )

        view = PrefixAdView(shop_cog, ctx.author.id, member.id, channel.id)
        control = await ctx.reply(
            f"{member.mention}\n🏠 تم إنشاء روم إعلان جديد: {channel.mention}\n**اختر نوع المنشن حق الروم**",
            mention_author=False,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        view.control_message_id = control.id


async def setup(bot: commands.Bot):
    await bot.add_cog(AdRoomCommandOverride(bot))
