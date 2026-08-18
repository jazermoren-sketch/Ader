from __future__ import annotations

import discord
from discord.ext import commands


class AdCommandControllerPatch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.db.execute(
            "CREATE TABLE IF NOT EXISTS ad_controllers(channel_id INTEGER PRIMARY KEY, controller_id INTEGER NOT NULL)"
        )
        module = __import__("cogs.advertising_shop", fromlist=["AdvertisingShop"])
        shop = self.bot.get_cog("AdvertisingShop")
        if not shop:
            return

        # Replace the existing prefix command callback without registering a second $اعلان command.
        command = shop.get_commands() if hasattr(shop, "get_commands") else []
        ad_command = next((c for c in command if getattr(c, "name", "") == "اعلان"), None)
        if ad_command:
            async def callback(cog, ctx, member: discord.Member | None = None):
                if not await cog.authorized(ctx.author):
                    return await ctx.reply("❌ هذا الأمر محمي. يلزم Administrator أو رتبة مسموح بها.", mention_author=False)
                if member is None:
                    return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
                if member.bot:
                    return await ctx.reply("❌ لا يمكن إنشاء روم إعلان لبوت.", mention_author=False)
                existing = await cog.db.fetchone(
                    "SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1",
                    (ctx.guild.id, member.id),
                )
                if existing:
                    channel_id = int(existing["channel_id"])
                else:
                    if not ctx.guild.me.guild_permissions.manage_channels:
                        return await ctx.reply("❌ البوت يحتاج إلى صلاحية Manage Channels لإنشاء روم الإعلان.", mention_author=False)
                    overwrites = {
                        ctx.guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                        member: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                        ctx.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, attach_files=True, embed_links=True, read_message_history=True),
                    }
                    try:
                        channel = await ctx.guild.create_text_channel(
                            cog.clean_name(f"ad-{member.display_name}"),
                            category=None,
                            overwrites=overwrites,
                            reason=f"Advertising room created by {ctx.author}",
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        return await ctx.reply("❌ تعذر إنشاء روم الإعلان.", mention_author=False)
                    channel_id = channel.id
                    await cog.db.execute(
                        "INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type) VALUES(?,?,?,?)",
                        (ctx.guild.id, channel.id, member.id, "everyone"),
                    )
                    await cog.db.execute(
                        "INSERT INTO ad_controllers(channel_id,controller_id) VALUES(?,?) ON CONFLICT(channel_id) DO UPDATE SET controller_id=excluded.controller_id",
                        (channel.id, ctx.author.id),
                    )
                    await channel.send(
                        f"📢 روم إعلان لـ {member.mention}\n\nالتحكم في هذا الإعلان محفوظ لصاحب أمر `$اعلان` فقط.",
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                    await cog.render_panel(channel)

                await cog.db.execute(
                    "INSERT INTO ad_controllers(channel_id,controller_id) VALUES(?,?) ON CONFLICT(channel_id) DO UPDATE SET controller_id=excluded.controller_id",
                    (channel_id, ctx.author.id),
                )
                target_channel = ctx.guild.get_channel(channel_id)
                await ctx.reply(
                    f"{member.mention}\n**اختر نوع المنشن حق الروم**",
                    mention_author=False,
                    view=module.PrefixAdView(cog, ctx.author.id, member.id, channel_id),
                    allowed_mentions=discord.AllowedMentions(users=True),
                )

            ad_command.callback = callback

        # The original modal is kept compatible, but its final message format is corrected.
        original_modal = module.AdModal
        original_submit = original_modal.on_submit

        async def modal_submit(modal, interaction: discord.Interaction):
            if interaction.user.id != modal.actor_id:
                return await interaction.response.send_message("❌ هذه العملية مخصصة لصاحب أمر الإعلان فقط.", ephemeral=True)
            row = await modal.cog.db.fetchone("SELECT * FROM ad_rooms WHERE channel_id=? AND active=1", (modal.channel_id,))
            controller = await modal.cog.db.fetchone("SELECT controller_id FROM ad_controllers WHERE channel_id=?", (modal.channel_id,))
            channel = interaction.guild.get_channel(modal.channel_id)
            if not row or not controller or int(controller["controller_id"]) != modal.actor_id or not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message("❌ هذه العملية لم تعد تحت صلاحيتك.", ephemeral=True)
            try:
                await channel.edit(name=module.clean_name(str(modal.name.value)), category=None, reason="Ader advertisement")
                mention = "@everyone" if modal.mention == "everyone" else "@here"
                # The mention is deliberately the final line of the advertisement.
                content = f"{str(modal.text.value).rstrip()}\n\n{mention}"
                await channel.send(content, allowed_mentions=discord.AllowedMentions(everyone=True))
                await interaction.response.send_message("✅ تم إرسال الإعلان بنجاح.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ البوت لا يملك الصلاحيات الكافية.", ephemeral=True)
            except discord.HTTPException:
                await interaction.response.send_message("❌ تعذر إرسال الإعلان حالياً.", ephemeral=True)

        original_modal.on_submit = modal_submit

        # Protect every button in the persistent ad panel using the controller stored for $اعلان.
        original_panel = module.AdPanel
        original_allowed = original_panel.check

        async def protected_check(panel, interaction):
            row = await panel.cog.db.fetchone("SELECT controller_id FROM ad_controllers WHERE channel_id=?", (panel.channel_id,))
            if row:
                allowed = int(row["controller_id"]) == interaction.user.id or interaction.user.guild_permissions.administrator
                if not allowed:
                    await interaction.response.send_message("❌ هذه اللوحة مخصصة لصاحب أمر `$اعلان` فقط.", ephemeral=True)
                    return False
                return True
            return await original_allowed(panel, interaction)

        original_panel.check = protected_check


async def setup(bot):
    await bot.add_cog(AdCommandControllerPatch(bot))
