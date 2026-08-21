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
        # AdvertisingShop's purchase hook must be installed only after Shop exists.
        ad_shop = self.bot.get_cog("AdvertisingShop")
        shop = self.bot.get_cog("Shop")
        if ad_shop is not None and shop is not None:
            try:
                ad_shop._patch_shop()
            except Exception as exc:
                self.bot.logger.error("Final AdvertisingShop patch failed: %s", exc, exc_info=True)

        # One authoritative $اعلان command. Older versions patched the same command
        # from multiple cogs, making behavior depend on cog load order.
        self.bot.remove_command("اعلان")

    @commands.command(name="اعلان", help="إنشاء عملية إعلان لعضو محدد.")
    @commands.guild_only()
    async def اعلان(self, ctx: commands.Context, member: discord.Member | None = None):
        if ctx.guild is None:
            return

        allowed = bool(ctx.author.guild_permissions.administrator)
        if not allowed:
            row = await self.db.fetchone(
                "SELECT allowed_roles FROM ad_settings WHERE guild_id=?",
                (ctx.guild.id,),
            )
            try:
                role_ids = {int(x) for x in json.loads(row["allowed_roles"] or "[]")} if row else set()
            except Exception:
                role_ids = set()
            allowed = any(role.id in role_ids for role in getattr(ctx.author, "roles", ()))

        if not allowed:
            return await ctx.reply(
                "❌ ليست لديك صلاحية استعمال أمر `$اعلان`.",
                mention_author=False,
            )

        if member is None:
            return await ctx.reply(
                "❌ الاستعمال الصحيح: `$اعلان @user`",
                mention_author=False,
            )

        if member.bot:
            return await ctx.reply(
                "❌ لا يمكن إنشاء إعلان لبوت.",
                mention_author=False,
            )

        # Cancel only unfinished setup attempts for this exact target/invoker.
        # Existing ad rooms remain active, so the same member can receive unlimited rooms.
        await self.db.execute(
            "UPDATE ad_pending SET active=0 WHERE guild_id=? AND target_id=? AND invoker_id=? AND active=1",
            (ctx.guild.id, member.id, ctx.author.id),
        )

        from cogs.ad_command_controller_patch import MentionChoiceView

        ad_shop = self.bot.get_cog("AdvertisingShop")
        if ad_shop is None:
            return await ctx.reply(
                "❌ نظام الإعلانات مازال ما تحمّلش. عاود تشغيل البوت.",
                mention_author=False,
            )

        await ctx.reply(
            f"{member.mention}\n**اختر نوع المنشن حق الروم**",
            mention_author=False,
            view=MentionChoiceView(ad_shop, ctx.guild.id, member.id, ctx.author.id),
            allowed_mentions=discord.AllowedMentions(users=True),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RuntimeRepairs(bot))
