from __future__ import annotations

import json
import time
import discord
from discord.ext import commands


class ReplyTargetModal(discord.ui.Modal, title="تحديد رسالة الـReply"):
    message_id = discord.ui.TextInput(
        label="ID ديال الرسالة",
        placeholder="مثال: 123456789012345678",
        max_length=30,
        required=True,
    )

    def __init__(self, cog, custom_message_id: int):
        super().__init__()
        self.cog = cog
        self.custom_message_id = custom_message_id

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        try:
            target_id = int(str(self.message_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ ID غير صالح.", ephemeral=True)
        try:
            target = await interaction.channel.fetch_message(target_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message("❌ ما قدرتش نلقى هاد الرسالة فالروم الحالي.", ephemeral=True)
        await self.cog.db.execute(
            "UPDATE ad_custom_messages SET reply_to=? WHERE id=? AND guild_id=?",
            (str(target.id), self.custom_message_id, interaction.guild.id),
        )
        await interaction.response.send_message(
            f"✅ هاد الرسالة غادي تكون Reply للرسالة `{target.id}`.", ephemeral=True
        )


class ReplyTargetButton(discord.ui.Button):
    def __init__(self, cog):
        super().__init__(label="Reply", emoji="↩️", style=discord.ButtonStyle.secondary, row=3)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        key = (interaction.guild.id, interaction.user.id)
        message_id = self.cog.selected_message.get(key)
        if not message_id:
            return await interaction.response.send_message("❌ اختار الرسالة اللي بغيتي تخصص Reply ديالها أولاً.", ephemeral=True)
        await interaction.response.send_modal(ReplyTargetModal(self.cog, int(message_id)))


class RuntimeRepairs(commands.Cog):
    """Final runtime reconciliation for dependent Ader cogs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        await self._patch_advertising_command()
        await self._patch_shortcuts_permission()
        await self._patch_giveaways()
        await self._patch_custom_message_reply()

    async def _patch_advertising_command(self):
        ad_shop = self.bot.get_cog("AdvertisingShop")
        shop = self.bot.get_cog("Shop")
        if ad_shop is not None and shop is not None:
            try:
                ad_shop._patch_shop()
            except Exception as exc:
                self.bot.logger.error("Final AdvertisingShop patch failed: %s", exc, exc_info=True)
        if ad_shop is None:
            return
        command = next((c for c in ad_shop.get_commands() if getattr(c, "name", "") == "اعلان"), None)
        if command is None:
            return

        async def callback(cog, ctx: commands.Context, member: discord.Member | None = None):
            if ctx.guild is None:
                return await ctx.reply("❌ هذا الأمر داخل السيرفر فقط.", mention_author=False)
            allowed = bool(ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild)
            if not allowed:
                row = await cog.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (ctx.guild.id,))
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

    async def _patch_shortcuts_permission(self):
        try:
            import cogs.shortcuts as shortcuts_module
        except Exception:
            return

        def permission(interaction):
            return bool(
                interaction.guild
                and (
                    interaction.user.guild_permissions.administrator
                    or interaction.user.guild_permissions.manage_guild
                )
            )

        shortcuts_module.has_server_manage_permission = permission
        cog = self.bot.get_cog("Shortcuts")
        if cog is None:
            return
        command = next((c for c in self.bot.tree.get_commands() if getattr(c, "name", "") == "اختصارات"), None)
        if command is None:
            return

        async def shortcuts_callback(cog_obj, interaction: discord.Interaction, اخفاء: bool = False):
            if not interaction.guild:
                return await interaction.response.send_message("❌ هذا الأمر خاص بالسيرفرات.", ephemeral=True)
            if not permission(interaction):
                return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
            await interaction.response.send_message(
                embed=cog_obj.selector_embed(),
                view=shortcuts_module.ShortcutView(cog_obj, اخفاء),
                ephemeral=اخفاء,
            )

        command.callback = shortcuts_callback

    async def _patch_giveaways(self):
        try:
            import cogs.advertising_shop as advertising_shop
        except Exception:
            return
        ad_shop = self.bot.get_cog("AdvertisingShop")
        if ad_shop is None:
            return

        # Make the legacy ad-room Giveaway button emoji-only. Existing messages are
        # repaired in-place and new views automatically use the same appearance.
        original_view_cls = advertising_shop.GiveawayView
        if not getattr(original_view_cls, "_ader_emoji_only", False):
            original_init = original_view_cls.__init__
            def patched_init(view_self, cog, giveaway_id):
                original_init(view_self, cog, giveaway_id)
                if view_self.children:
                    button = view_self.children[0]
                    button.label = None
                    button.emoji = "🎉"
            original_view_cls.__init__ = patched_init
            original_view_cls._ader_emoji_only = True

        # Remove the old single-active-giveaway restriction by replacing the
        # creation method with an equivalent implementation that has no global
        # active-giveaway guard.
        async def create_giveaway(cog, guild, owner, channel_id, amount, duration):
            row = await cog.db.fetchone(
                "SELECT * FROM ad_rooms WHERE guild_id=? AND channel_id=? AND owner_id=? AND active=1",
                (guild.id, channel_id, owner.id),
            )
            if not row:
                return False, "❌ هذا ليس رومك الإعلاني."
            if amount <= 0 or duration <= 0:
                return False, "❌ مبلغ ومدة القيف أواي غير صالحين."
            if await cog.db.get_balance(owner.id) < amount:
                return False, f"❌ يجب أن يكون لديك **{amount:,} ANOCoin** لبدء القيف أواي."
            if not await cog.db.remove_balance(owner.id, guild.id, amount):
                return False, "❌ تعذر خصم المبلغ."
            ends = time.time() + duration
            cur = await cog.db.execute(
                "INSERT INTO ad_giveaways(guild_id,channel_id,owner_id,amount,ends_at) VALUES(?,?,?,?,?)",
                (guild.id, channel_id, owner.id, amount, ends),
            )
            gid = int(cur.lastrowid)
            channel = guild.get_channel(channel_id)
            try:
                embed = discord.Embed(
                    title="🎁 قيف أواي ANOCoin",
                    description=f"الجائزة: **{amount:,} ANOCoin**\nينتهي: <t:{int(ends)}:R>\nاضغط على 🎉 للمشاركة.",
                    colour=discord.Colour.green(),
                )
                message = await channel.send(embed=embed, view=advertising_shop.GiveawayView(cog, gid))
                await cog.db.execute("UPDATE ad_giveaways SET message_id=? WHERE id=?", (message.id, gid))
            except discord.HTTPException:
                await cog.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=?", (gid,))
                await cog.db.add_balance(owner.id, guild.id, amount)
                return False, "❌ تعذر نشر القيف أواي؛ تمت إعادة المبلغ."
            self.bot.add_view(advertising_shop.GiveawayView(cog, gid))
            return True, f"✅ تم إنشاء القيف أواي **#{gid}** وخصم **{amount:,} ANOCoin** من رصيدك."

        ad_shop.create_giveaway = create_giveaway.__get__(ad_shop, ad_shop.__class__)

        # Repair existing active giveaway messages to the emoji-only view.
        try:
            rows = await self.db.fetchall("SELECT id,channel_id,message_id FROM ad_giveaways WHERE ended=0")
            for row in rows:
                message_id = int(row["message_id"] or 0)
                if not message_id:
                    continue
                channel = self.bot.get_channel(int(row["channel_id"]))
                if channel is None:
                    continue
                try:
                    message = await channel.fetch_message(message_id)
                    await message.edit(view=advertising_shop.GiveawayView(ad_shop, int(row["id"])))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
        except Exception as exc:
            self.bot.logger.error("Giveaway UI repair failed: %s", exc, exc_info=True)

    async def _patch_custom_message_reply(self):
        try:
            import cogs.ad_customization as customization
        except Exception:
            return

        cog = self.bot.get_cog("AdCustomization")
        if cog is None:
            return

        # Reply permissions: Manage Server and Administrator are equivalent here.
        original_is_admin = getattr(cog, "is_admin", None)
        def is_admin(interaction):
            return bool(
                interaction.guild
                and (
                    interaction.user.guild_permissions.administrator
                    or interaction.user.guild_permissions.manage_guild
                )
            )
        if original_is_admin:
            cog.is_admin = is_admin

        # Add a Reply-by-message-ID control to the existing customization panel.
        original_view_init = customization.SettingsView.__init__
        if not getattr(customization.SettingsView, "_ader_reply_button", False):
            def view_init(view_self, cog_obj, rows):
                original_view_init(view_self, cog_obj, rows)
                view_self.add_item(ReplyTargetButton(cog_obj))
            customization.SettingsView.__init__ = view_init
            customization.SettingsView._ader_reply_button = True

        # Convert stored custom-message reply_to values into actual Discord replies
        # immediately after an advertisement is posted. Any Discord message ID works,
        # including a Giveaway message or a message carrying an attachment/image.
        original_submit = customization.MessageModal.on_submit
        if not getattr(customization.MessageModal, "_ader_reply_permission", False):
            async def message_submit(modal_self, interaction):
                if not is_admin(interaction):
                    return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
                await original_submit(modal_self, interaction)
            customization.MessageModal.on_submit = message_submit
            customization.MessageModal._ader_reply_permission = True


async def setup(bot: commands.Bot):
    await bot.add_cog(RuntimeRepairs(bot))
