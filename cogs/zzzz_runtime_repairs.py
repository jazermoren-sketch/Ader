from __future__ import annotations

import json
import time
import discord
from discord import app_commands
from discord.ext import commands, tasks


class ReplyTargetModal(discord.ui.Modal, title="تحديد رسالة الـReply"):
    message_id = discord.ui.TextInput(label="ID ديال الرسالة", placeholder="123456789012345678", max_length=30, required=True)

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
        await self.cog.db.execute("UPDATE ad_custom_messages SET reply_to=? WHERE id=? AND guild_id=?", (str(target_id), self.custom_message_id, interaction.guild.id))
        await interaction.response.send_message(f"✅ تم تعيين Reply للرسالة `{target_id}`.", ephemeral=True)


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
    """Runtime repairs kept compatible with discord.py's read-only Command.callback property."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.giveaway_watcher.start()

    async def cog_load(self):
        await self._patch_advertising_command()
        await self._patch_shortcuts_permission()
        await self._patch_giveaways()
        await self._patch_custom_message_reply()
        await self._prepare_membership_requirement()

    def cog_unload(self):
        self.giveaway_watcher.cancel()

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
            await cog.db.execute("UPDATE ad_pending SET active=0 WHERE guild_id=? AND target_id=? AND invoker_id=? AND active=1", (ctx.guild.id, member.id, ctx.author.id))
            from cogs.ad_command_controller_patch import MentionChoiceView
            await ctx.reply(f"{member.mention}\n**اختر نوع المنشن حق الروم**", mention_author=False, view=MentionChoiceView(cog, ctx.guild.id, member.id, ctx.author.id), allowed_mentions=discord.AllowedMentions(users=True))

        # discord.py exposes callback as read-only; _callback is the supported runtime storage.
        command._callback = callback

    async def _patch_shortcuts_permission(self):
        try:
            import cogs.shortcuts as shortcuts_module
        except Exception:
            return
        def permission(interaction):
            return bool(interaction.guild and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild))
        shortcuts_module.has_server_manage_permission = permission
        command = next((c for c in self.bot.tree.get_commands() if getattr(c, "name", "") == "اختصارات"), None)
        cog = self.bot.get_cog("Shortcuts")
        if command is None or cog is None:
            return
        async def shortcuts_callback(cog_obj, interaction: discord.Interaction, اخفاء: bool = False):
            if not permission(interaction):
                return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
            await interaction.response.send_message(embed=cog_obj.selector_embed(), view=shortcuts_module.ShortcutView(cog_obj, اخفاء), ephemeral=اخفاء)
        command._callback = shortcuts_callback

    async def _patch_giveaways(self):
        try:
            import cogs.advertising_shop as advertising_shop
        except Exception:
            return
        ad_shop = self.bot.get_cog("AdvertisingShop")
        if ad_shop is None:
            return
        if not await self._has_column("ad_giveaways", "message_id"):
            await self.db.execute("ALTER TABLE ad_giveaways ADD COLUMN message_id INTEGER")
        view_cls = advertising_shop.GiveawayView
        if not getattr(view_cls, "_ader_emoji_only", False):
            original_init = view_cls.__init__
            def patched_init(view_self, cog, giveaway_id):
                original_init(view_self, cog, giveaway_id)
                if view_self.children:
                    view_self.children[0].label = None
                    view_self.children[0].emoji = "🎉"
            view_cls.__init__ = patched_init
            view_cls._ader_emoji_only = True

        async def create_giveaway(cog, guild, owner, channel_id, amount, duration):
            row = await cog.db.fetchone("SELECT * FROM ad_rooms WHERE guild_id=? AND channel_id=? AND owner_id=? AND active=1", (guild.id, channel_id, owner.id))
            if not row:
                return False, "❌ هذا ليس رومك الإعلاني."
            if amount <= 0 or duration <= 0:
                return False, "❌ مبلغ ومدة القيف أواي غير صالحين."
            if await cog.db.get_balance(owner.id) < amount:
                return False, f"❌ يجب أن يكون لديك **{amount:,} ANOCoin** لبدء القيف أواي."
            if not await cog.db.remove_balance(owner.id, guild.id, amount):
                return False, "❌ تعذر خصم المبلغ."
            ends = time.time() + duration
            cur = await cog.db.execute("INSERT INTO ad_giveaways(guild_id,channel_id,owner_id,amount,ends_at) VALUES(?,?,?,?,?)", (guild.id, channel_id, owner.id, amount, ends))
            gid = int(cur.lastrowid)
            channel = guild.get_channel(channel_id)
            try:
                embed = discord.Embed(title="🎁 قيف أواي ANOCoin", description=f"الجائزة: **{amount:,} ANOCoin**\nينتهي: <t:{int(ends)}:R>\nاضغط على 🎉 للمشاركة.", colour=discord.Colour.green())
                message = await channel.send(embed=embed, view=view_cls(cog, gid))
                await cog.db.execute("UPDATE ad_giveaways SET message_id=? WHERE id=?", (message.id, gid))
            except discord.HTTPException:
                await cog.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=?", (gid,))
                await cog.db.add_balance(owner.id, guild.id, amount)
                return False, "❌ تعذر نشر القيف أواي؛ تمت إعادة المبلغ."
            self.bot.add_view(view_cls(cog, gid))
            return True, f"✅ تم إنشاء القيف أواي **#{gid}** وخصم **{amount:,} ANOCoin** من رصيدك."
        ad_shop.create_giveaway = create_giveaway.__get__(ad_shop, ad_shop.__class__)

    async def _prepare_membership_requirement(self):
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_giveaway_requirements(guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, required_guild_id INTEGER)")

    @app_commands.command(name="ad-giveaway-requirement", description="إعداد شرط دخول سيرفر للفوز بالقيف أواي")
    @app_commands.describe(enabled="تفعيل أو تعطيل الشرط", server_id="ID السيرفر المعلن عنه عند التفعيل")
    async def ad_giveaway_requirement(self, interaction: discord.Interaction, enabled: bool, server_id: str | None = None):
        if not interaction.guild or not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        target_id = None
        if enabled:
            try:
                target_id = int(server_id or "")
            except ValueError:
                return await interaction.response.send_message("❌ فعّل Developer Mode وخذ ID السيرفر المعلن عنه.", ephemeral=True)
            if self.bot.get_guild(target_id) is None:
                return await interaction.response.send_message("❌ البوت يجب أن يكون داخل السيرفر المعلن عنه حتى يقدر يتحقق من الفائز.", ephemeral=True)
        await self.db.execute("INSERT INTO ad_giveaway_requirements(guild_id,enabled,required_guild_id) VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled,required_guild_id=excluded.required_guild_id", (interaction.guild.id, 1 if enabled else 0, target_id))
        text = "✅ تم تفعيل شرط دخول السيرفر للفوز بالقيف أواي." if enabled else "✅ تم تعطيل شرط دخول السيرفر للفوز بالقيف أواي."
        await interaction.response.send_message(text, ephemeral=True)

    @tasks.loop(seconds=8)
    async def giveaway_watcher(self):
        await self.bot.wait_until_ready()
        rows = await self.db.fetchall("SELECT * FROM ad_giveaways WHERE ended=0 AND ends_at<=?", (time.time(),))
        for row in rows:
            try:
                await self._finish_giveaway(row)
            except Exception as exc:
                self.bot.logger.error("Giveaway finish failed: %s", exc, exc_info=True)

    async def _finish_giveaway(self, row):
        gid = int(row["id"])
        channel = self.bot.get_channel(int(row["channel_id"]))
        if channel is None:
            await self.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=? AND ended=0", (gid,))
            return
        entries = await self.db.fetchall("SELECT user_id FROM ad_giveaway_entries WHERE giveaway_id=?", (gid,))
        if not entries:
            await self.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=? AND ended=0", (gid,))
            await channel.send("**لا يوجد اي فائز بسبب ان لا احد شارك بالقيف اواي**")
            return
        req = await self.db.fetchone("SELECT enabled,required_guild_id FROM ad_giveaway_requirements WHERE guild_id=?", (int(row["guild_id"]),))
        candidates = [int(x["user_id"]) for x in entries]
        if req and int(req["enabled"] or 0) and req["required_guild_id"]:
            required_guild = self.bot.get_guild(int(req["required_guild_id"]))
            eligible = []
            if required_guild is not None:
                for user_id in candidates:
                    member = required_guild.get_member(user_id)
                    if member is None:
                        try:
                            member = await required_guild.fetch_member(user_id)
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            member = None
                    if member is not None:
                        eligible.append(user_id)
            candidates = eligible
            if not candidates:
                await self.db.execute("UPDATE ad_giveaways SET ended=1 WHERE id=? AND ended=0", (gid,))
                await channel.send("**لا يوجد اي فائز بسبب ان لا احد دخل السيرفر المعلن عنه**")
                return
        winner_id = candidates[0] if len(candidates) == 1 else __import__("random").choice(candidates)
        await self.db.execute("UPDATE ad_giveaways SET ended=1,winner_id=? WHERE id=? AND ended=0", (winner_id, gid))
        await channel.send(f"🎉 مبروك <@{winner_id}>! ربحت **{int(row['amount']):,} ANOCoin**.", allowed_mentions=discord.AllowedMentions(users=True))
        try:
            await self.db.add_balance(winner_id, int(row["guild_id"]), int(row["amount"]))
        except Exception as exc:
            self.bot.logger.error("Could not pay giveaway winner %s: %s", winner_id, exc, exc_info=True)

    async def _has_column(self, table: str, column: str) -> bool:
        rows = await self.db.fetchall(f"PRAGMA table_info({table})")
        return any(str(row[1]) == column for row in rows)

    async def _patch_custom_message_reply(self):
        try:
            import cogs.ad_customization as customization
        except Exception:
            return
        cog = self.bot.get_cog("AdCustomization")
        if cog is None:
            return
        if not await self._has_column("ad_custom_messages", "reply_to"):
            await self.db.execute("ALTER TABLE ad_custom_messages ADD COLUMN reply_to TEXT")
        cog.is_admin = lambda interaction: bool(interaction.guild and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild))
        original_view_init = customization.SettingsView.__init__
        if not getattr(customization.SettingsView, "_ader_reply_button", False):
            def view_init(view_self, cog_obj, rows):
                original_view_init(view_self, cog_obj, rows)
                view_self.add_item(ReplyTargetButton(cog_obj))
            customization.SettingsView.__init__ = view_init
            customization.SettingsView._ader_reply_button = True


async def setup(bot: commands.Bot):
    await bot.add_cog(RuntimeRepairs(bot))
