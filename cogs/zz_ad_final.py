from __future__ import annotations

import asyncio
import discord
from discord.ext import commands


class FinalAdCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task = asyncio.create_task(self._patch())

    def cog_unload(self):
        if not self.task.done():
            self.task.cancel()

    async def _patch(self):
        await self.bot.wait_until_ready()
        for _ in range(40):
            shop = self.bot.get_cog("AdvertisingShop")
            if shop:
                command = next((c for c in shop.get_commands() if getattr(c, "name", "") == "اعلان"), None)
                if command:
                    module = __import__("cogs.advertising_shop", fromlist=["PrefixAdView", "clean_name"])
                    async def callback(cog, ctx, member: discord.Member | None = None):
                        if not await cog.authorized(ctx.author):
                            return await ctx.reply("❌ هذا الأمر محمي. يلزم Administrator أو رتبة مسموح بها.", mention_author=False)
                        if member is None:
                            return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
                        if member.bot:
                            return await ctx.reply("❌ لا يمكن إنشاء روم إعلان لبوت.", mention_author=False)
                        existing = await cog.db.fetchone("SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1", (ctx.guild.id, member.id))
                        if existing:
                            channel_id = int(existing["channel_id"])
                        else:
                            if not ctx.guild.me.guild_permissions.manage_channels:
                                return await ctx.reply("❌ البوت يحتاج إلى صلاحية Manage Channels لإنشاء روم الإعلان.", mention_author=False)
                            overwrites = {
                                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                                member: discord.PermissionOverwrite(view_channel=True, send_messages=False, manage_channels=False, manage_messages=False, attach_files=False, embed_links=False, read_message_history=True),
                                ctx.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, attach_files=True, embed_links=True, read_message_history=True),
                            }
                            try:
                                channel = await ctx.guild.create_text_channel(module.clean_name(f"ad-{member.display_name}"), category=None, overwrites=overwrites, reason=f"Advertising room created by {ctx.author}")
                            except (discord.Forbidden, discord.HTTPException):
                                return await ctx.reply("❌ تعذر إنشاء روم الإعلان.", mention_author=False)
                            channel_id = channel.id
                            await cog.db.execute("INSERT INTO ad_rooms(guild_id,channel_id,owner_id,mention_type) VALUES(?,?,?,?)", (ctx.guild.id, channel.id, member.id, "everyone"))
                            await channel.send(f"📢 روم إعلان لـ {member.mention}\n\nالتحكم في الإعلانات محفوظ للإداري الذي أنشأه عبر `$اعلان`.", allowed_mentions=discord.AllowedMentions(users=True))
                        await cog.db.execute("CREATE TABLE IF NOT EXISTS ad_controllers(channel_id INTEGER PRIMARY KEY, controller_id INTEGER NOT NULL)")
                        await cog.db.execute("INSERT INTO ad_controllers(channel_id,controller_id) VALUES(?,?) ON CONFLICT(channel_id) DO UPDATE SET controller_id=excluded.controller_id", (channel_id, ctx.author.id))
                        target = ctx.guild.get_channel(channel_id)
                        if target:
                            ow = target.overwrites_for(member)
                            ow.view_channel, ow.send_messages, ow.manage_channels, ow.manage_messages = True, False, False, False
                            ow.attach_files, ow.embed_links = False, False
                            try:
                                await target.set_permissions(member, overwrite=ow, reason="Advertising room owner has no controls")
                            except (discord.Forbidden, discord.HTTPException):
                                pass
                        await ctx.reply(f"{member.mention}\n**اختر نوع المنشن حق الروم**", mention_author=False, view=module.PrefixAdView(cog, ctx.author.id, member.id, channel_id), allowed_mentions=discord.AllowedMentions(users=True))
                    command.callback = callback
                return
            await asyncio.sleep(0.25)


async def setup(bot):
    await bot.add_cog(FinalAdCommand(bot))
