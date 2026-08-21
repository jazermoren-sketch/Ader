from __future__ import annotations

import json

import discord
from discord.ext import commands


class RuntimeRepairs(commands.Cog):
    """Final runtime reconciliation for dependent Ader cogs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        ad_shop = self.bot.get_cog("AdvertisingShop")
        shop = self.bot.get_cog("Shop")
        if ad_shop is not None and shop is not None:
            try:
                ad_shop._patch_shop()
            except Exception as exc:
                self.bot.logger.error("Final AdvertisingShop patch failed: %s", exc, exc_info=True)

        # Do not register a second $اعلان command. Patch the existing
        # AdvertisingShop command in-place so Discord.py has exactly one callback.
        if ad_shop is not None:
            command = next((c for c in ad_shop.get_commands() if getattr(c, "name", "") == "اعلان"), None)
            if command is not None:
                async def callback(cog, ctx: commands.Context, member: discord.Member | None = None):
                    if ctx.guild is None:
                        return await ctx.reply("❌ هذا الأمر داخل السيرفر فقط.", mention_author=False)

                    allowed = bool(ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild)
                    if not allowed:
                        row = await cog.db.fetchone(
                            "SELECT allowed_roles FROM ad_settings WHERE guild_id=?",
                            (ctx.guild.id,),
                        )
                        try:
                            role_ids = {int(x) for x in json.loads(row["allowed_roles"] or "[]")} if row else set()
                        except Exception:
                            role_ids = set()
                        allowed = any(role.id in role_ids for role in getattr(ctx.author, "roles", ()))
                    if not allowed:
                        return await ctx.reply("❌ ليست لديك صلاحية استعمال أمر `$اعلان`.", mention_author=False)
                    if member is None:
                        return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
                    if member.bot:
                        return await ctx.reply("❌ لا يمكن إنشاء إعلان لبوت.", mention_author=False)

                    await cog.db.execute(
                        "UPDATE ad_pending SET active=0 WHERE guild_id=? AND target_id=? AND invoker_id=? AND active=1",
                        (ctx.guild.id, member.id, ctx.author.id),
                    )
                    from cogs.ad_command_controller_patch import MentionChoiceView
                    await ctx.reply(
                        f"{member.mention}\n**اختر نوع المنشن حق الروم**",
                        mention_author=False,
                        view=MentionChoiceView(cog, ctx.guild.id, member.id, ctx.author.id),
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )

                command.callback = callback

        # Remove any separately registered bot-level command left by legacy patches.
        bot_command = self.bot.get_command("اعلان")
        if bot_command is not None and getattr(bot_command, "cog_name", "") != "AdvertisingShop":
            try:
                self.bot.remove_command("اعلان")
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RuntimeRepairs(bot))
