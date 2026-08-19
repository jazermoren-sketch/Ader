from __future__ import annotations

import json
import discord
from discord.ext import commands


class AdSettingsHotfix(commands.Cog):
    """Keeps ad-settings selection state scoped per guild/user and fixes reply UX."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        try:
            from cogs import ad_customization
        except Exception as exc:
            print(f"AdSettingsHotfix: import failed: {exc!r}")
            return

        def key(interaction: discord.Interaction):
            return (interaction.guild_id or 0, interaction.user.id)

        original_message_callback = ad_customization.MessageSelect.callback
        original_reply_callback = ad_customization.ReplySelect.callback

        async def message_callback(select, interaction: discord.Interaction):
            if not select.cog.is_admin(interaction):
                return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
            raw = select.values[0] if select.values else "0"
            mid = int(raw) if raw.isdigit() else 0
            if not mid:
                return await interaction.response.send_message("❌ لا توجد رسالة للاختيار.", ephemeral=True)
            row = await select.cog.db.fetchone(
                "SELECT id FROM ad_custom_messages WHERE id=? AND guild_id=?",
                (mid, interaction.guild.id),
            )
            if not row:
                return await interaction.response.send_message("❌ الرسالة غير موجودة.", ephemeral=True)
            select.cog.selected_message[key(interaction)] = mid
            await interaction.response.send_message(
                f"✅ تم اختيار الرسالة `{mid}`. يمكنك الآن تحديد توقيتها أو الـReply.",
                ephemeral=True,
            )

        async def reply_callback(select, interaction: discord.Interaction):
            if not select.cog.is_admin(interaction):
                return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)

            state_key = key(interaction)
            mid = select.cog.selected_message.get(state_key)

            # Backward compatibility: migrate an old user-only selection once.
            if not mid:
                legacy = select.cog.selected_message.get(interaction.user.id)
                if legacy:
                    row = await select.cog.db.fetchone(
                        "SELECT id FROM ad_custom_messages WHERE id=? AND guild_id=?",
                        (legacy, interaction.guild.id),
                    )
                    if row:
                        mid = int(legacy)
                        select.cog.selected_message[state_key] = mid

            rows = await select.cog.db.fetchall(
                "SELECT id FROM ad_custom_messages WHERE guild_id=? ORDER BY position,id",
                (interaction.guild.id,),
            )

            # If there is exactly one message, use it automatically instead of
            # showing the misleading 'choose a message first' error.
            if not mid and len(rows) == 1:
                mid = int(rows[0]["id"])
                select.cog.selected_message[state_key] = mid

            if not mid:
                return await interaction.response.send_message(
                    "❌ حدد أولاً الرسالة التي تريد جعلها Reply: من قائمة «اختر رسالة لتخصيصها»، ثم اختر الرسالة المرجعية من قائمة الـReply.",
                    ephemeral=True,
                )

            reply_to = None if select.values[0] == "none" else int(select.values[0])
            if reply_to == mid:
                return await interaction.response.send_message("❌ لا يمكن للرسالة أن تعمل Reply لنفسها.", ephemeral=True)
            if reply_to is not None:
                exists = await select.cog.db.fetchone(
                    "SELECT id FROM ad_custom_messages WHERE id=? AND guild_id=?",
                    (reply_to, interaction.guild.id),
                )
                if not exists:
                    return await interaction.response.send_message("❌ الرسالة المرجعية غير موجودة.", ephemeral=True)

            await select.cog.db.execute(
                "UPDATE ad_custom_messages SET reply_to=? WHERE id=? AND guild_id=?",
                (reply_to, mid, interaction.guild.id),
            )
            await interaction.response.send_message("✅ تم حفظ إعداد الـReply بنجاح.", ephemeral=True)

        ad_customization.MessageSelect.callback = message_callback
        ad_customization.ReplySelect.callback = reply_callback

        # Patch the existing command callback so configured roles can use $اعلان,
        # while Administrators always retain access. Existing active ad rooms are
        # deliberately not checked, allowing unlimited rooms per member.
        controller = self.bot.get_cog("AdCommandControllerPatch")
        shop = self.bot.get_cog("AdvertisingShop")
        if controller and shop:
            command = next((c for c in shop.get_commands() if getattr(c, "name", "") == "اعلان"), None)
            if command is not None:
                async def callback(cog, ctx, member: discord.Member | None = None):
                    if ctx.guild is None:
                        return await ctx.reply("❌ هذا الأمر داخل السيرفر فقط.", mention_author=False)
                    allowed = bool(ctx.author.guild_permissions.administrator)
                    if not allowed:
                        row = await cog.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (ctx.guild.id,))
                        try:
                            role_ids = {int(x) for x in json.loads(row["allowed_roles"] or "[]")} if row else set()
                        except Exception:
                            role_ids = set()
                        allowed = any(role.id in role_ids for role in getattr(ctx.author, "roles", []))
                    if not allowed:
                        return await ctx.reply("❌ ليست لديك صلاحية استعمال أمر `$اعلان`.", mention_author=False)
                    if member is None:
                        return await ctx.reply("❌ الاستعمال الصحيح: `$اعلان @user`", mention_author=False)
                    if member.bot:
                        return await ctx.reply("❌ لا يمكن إنشاء إعلان لبوت.", mention_author=False)

                    # Only replace an unfinished setup for the same target/invoker.
                    # Do not inspect or deactivate existing active ad rooms.
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

        print("AdSettingsHotfix: reply state and multi-room advertisement flow fixed")


async def setup(bot):
    await bot.add_cog(AdSettingsHotfix(bot))
