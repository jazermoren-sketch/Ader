from __future__ import annotations

import secrets
import time
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands

DAILY_REWARD = 10
DAILY_COOLDOWN = 24 * 60 * 60
VERIFY_TIMEOUT = 60
OWNER_ID = 1472570059367911587


class DailyVerifyView(discord.ui.View):
    def __init__(self, cog, user_id: int, code: str):
        super().__init__(timeout=VERIFY_TIMEOUT)
        self.cog = cog
        self.user_id = user_id
        self.code = code
        self.message = None

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="⌛ انتهت مهلة التحقق. استخدم `/daily` مرة أخرى.", view=None)
            except discord.HTTPException:
                pass
        self.cog.pending.pop(self.user_id, None)

    @discord.ui.button(label="إدخال رمز التحقق", emoji="🔢", style=discord.ButtonStyle.primary)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذا التحقق ليس لك.", ephemeral=True)
        await interaction.response.send_modal(DailyCodeModal(self.cog, self.user_id, self.code))


class DailyCodeModal(discord.ui.Modal, title="التحقق من Daily"):
    code = discord.ui.TextInput(label="رمز التحقق", placeholder="أدخل الرمز المكوّن من 6 أرقام", min_length=6, max_length=6)

    def __init__(self, cog, user_id: int, expected: str):
        super().__init__(custom_id=f"ader:daily-code:{user_id}")
        self.cog = cog
        self.user_id = user_id
        self.expected = expected

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذا التحقق ليس لك.", ephemeral=True)
        state = self.cog.pending.get(self.user_id)
        if not state or state["expires"] <= time.time():
            self.cog.pending.pop(self.user_id, None)
            return await interaction.response.send_message("⌛ انتهت مهلة التحقق. استخدم `/daily` مرة أخرى.", ephemeral=True)
        if str(self.code.value).strip() != self.expected:
            return await interaction.response.send_message("❌ رمز التحقق غير صحيح.", ephemeral=True)

        # Re-check the cooldown immediately before paying, preventing double claims/race conditions.
        row = await self.cog.db.fetchone(
            "SELECT last_claim FROM ader_daily_claims WHERE guild_id=? AND user_id=?",
            (interaction.guild.id, self.user_id),
        )
        now = int(time.time())
        if row and now - int(row["last_claim"]) < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - (now - int(row["last_claim"]))
            self.cog.pending.pop(self.user_id, None)
            return await interaction.response.send_message(
                f"⏳ لقد استلمت مكافأة Daily مسبقاً. حاول بعد <t:{now + remaining}:R>.", ephemeral=True
            )

        await self.cog.db.execute(
            "INSERT INTO ader_daily_claims(guild_id,user_id,last_claim) VALUES(?,?,?) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET last_claim=excluded.last_claim",
            (interaction.guild.id, self.user_id, now),
        )
        await self.cog.db.add_balance(self.user_id, interaction.guild.id, DAILY_REWARD)
        self.cog.pending.pop(self.user_id, None)
        await interaction.response.edit_message(
            content=f"✅ تم التحقق بنجاح! حصلت على **{DAILY_REWARD} ANOCoin**.\n⏰ يمكنك استلام Daily مرة أخرى <t:{now + DAILY_COOLDOWN}:R>.",
            view=None,
        )


class DailyPatch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.pending: dict[int, dict] = {}

    async def cog_load(self):
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS ader_daily_claims(" 
            "guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, last_claim INTEGER NOT NULL, "
            "PRIMARY KEY(guild_id,user_id))"
        )
        # Remove any previous /daily registration before this canonical implementation is added.
        self.bot.tree.remove_command("daily")

    @app_commands.command(name="daily", description="استلم مكافأة Daily مرة كل 24 ساعة")
    async def daily(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message("❌ هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)

        now = int(time.time())
        row = await self.db.fetchone(
            "SELECT last_claim FROM ader_daily_claims WHERE guild_id=? AND user_id=?",
            (interaction.guild.id, interaction.user.id),
        )
        if row:
            remaining = DAILY_COOLDOWN - (now - int(row["last_claim"]))
            if remaining > 0:
                return await interaction.response.send_message(
                    f"⏳ لا يمكنك استلام Daily الآن.\nستتمكن من الاستلام <t:{now + remaining}:R>.\n\n" 
                    f"💰 المكافأة اليومية: **{DAILY_REWARD} ANOCoin**",
                    ephemeral=True,
                )

        # One active verification per user prevents opening multiple simultaneous claim windows.
        pending = self.pending.get(interaction.user.id)
        if pending and pending["expires"] > now:
            return await interaction.response.send_message("⚠️ لديك عملية تحقق Daily مفتوحة بالفعل. أكملها أولاً.", ephemeral=True)

        code = f"{secrets.randbelow(1_000_000):06d}"
        self.pending[interaction.user.id] = {"code": code, "expires": now + VERIFY_TIMEOUT}
        view = DailyVerifyView(self, interaction.user.id, code)
        await interaction.response.send_message(
            "🔐 **التحقق من Daily**\n\n"
            "لإتمام العملية، اضغط الزر بالأسفل وأدخل رمز التحقق المكوّن من 6 أرقام.\n"
            "⏱️ لديك **60 ثانية** لإتمام التحقق.\n"
            f"💰 المكافأة: **{DAILY_REWARD} ANOCoin**",
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()


class AnnouncementAdminView(discord.ui.View):
    def __init__(self, cog, interaction: discord.Interaction):
        super().__init__(timeout=120)
        self.cog = cog
        self.owner_id = interaction.user.id

    async def check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ هذه لوحة تخص الإداري الذي فتحها فقط.", ephemeral=True)
            return False
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels or interaction.user.id == OWNER_ID):
            await interaction.response.send_message("❌ تحتاج Administrator أو Manage Channels.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="إضافة رتبة مسموحة", emoji="➕", style=discord.ButtonStyle.success)
    async def add_role(self, interaction, button):
        if await self.check(interaction):
            await interaction.response.send_modal(RoleConfigModal(self.cog, True))

    @discord.ui.button(label="إزالة رتبة", emoji="➖", style=discord.ButtonStyle.danger)
    async def remove_role(self, interaction, button):
        if await self.check(interaction):
            await interaction.response.send_modal(RoleConfigModal(self.cog, False))

    @discord.ui.button(label="طريقة الاستخدام", emoji="📖", style=discord.ButtonStyle.secondary)
    async def help(self, interaction, button):
        if await self.check(interaction):
            await interaction.response.send_message(
                "**تخصيص `$اعلان`**\n\n"
                "• إضافة/إزالة الرتب المسموح لها باستخدام الأمر.\n"
                "• `$اعلان @user` لفتح اختيار Everyone أو Here وإرسال الإعلان.\n"
                "• لوحة الروم نفسها تحتوي على الإعلان، القيف أواي، الرسالة والصورة.",
                ephemeral=True,
            )


class RoleConfigModal(discord.ui.Modal, title="تخصيص صلاحية الإعلان"):
    role_id = discord.ui.TextInput(label="ID الرتبة", placeholder="مثال: 123456789012345678", min_length=1, max_length=25)

    def __init__(self, cog, adding: bool):
        super().__init__(custom_id=f"ader:ad-role:{'add' if adding else 'remove'}")
        self.cog, self.adding = cog, adding

    async def on_submit(self, interaction):
        if not interaction.user.guild_permissions.administrator and interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("❌ تحتاج Administrator.", ephemeral=True)
        try:
            role_id = int(str(self.role_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ ID الرتبة غير صحيح.", ephemeral=True)
        role = interaction.guild.get_role(role_id)
        if role is None:
            return await interaction.response.send_message("❌ لم يتم العثور على هذه الرتبة.", ephemeral=True)
        row = await self.cog.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (interaction.guild.id,))
        import json
        roles = set(json.loads(row["allowed_roles"] or "[]")) if row else set()
        if self.adding: roles.add(role.id)
        else: roles.discard(role.id)
        await self.cog.db.execute(
            "INSERT INTO ad_settings(guild_id,allowed_roles) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allowed_roles=excluded.allowed_roles",
            (interaction.guild.id, json.dumps(sorted(roles))),
        )
        await interaction.response.send_message(
            f"✅ {'تمت إضافة' if self.adding else 'تمت إزالة'} {role.mention} {'إلى' if self.adding else 'من'} صلاحيات `$اعلان`.",
            ephemeral=True,
        )


class AnnouncementShortcutPatch(commands.Cog):
    """Canonical `$اعلان` entry point. It replaces the legacy prefix implementation."""
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Only the command named اعلان is replaced; the existing AdvertisingShop delivery logic remains intact.
        old = self.bot.get_command("اعلان")
        if old is not None:
            self.bot.remove_command(old.name)

    @commands.command(name="اعلان", aliases=("إعلان",))
    async def اعلان(self, ctx: commands.Context, member: discord.Member | None = None):
        cog = self.bot.get_cog("AdvertisingShop")
        if cog is None:
            return await ctx.reply("❌ نظام الإعلانات غير متاح حالياً.", mention_author=False)
        if not await cog.authorized(ctx.author):
            return await ctx.reply("❌ هذا الأمر محمي. تحتاج Administrator أو رتبة مسموحة.", mention_author=False)
        if member is None:
            return await ctx.reply(
                "⚙️ **تخصيص نظام الإعلان**\n\n"
                "اختر من اللوحة ما تريد تخصيصه.\n"
                "بعدها استخدم: `$اعلان @user` لإرسال إعلان.",
                view=AnnouncementAdminView(cog, ctx),
                mention_author=False,
            )
        row = await cog.db.fetchone(
            "SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1",
            (ctx.guild.id, member.id),
        )
        if not row:
            return await ctx.reply("❌ هذا العضو لا يملك روم إعلان نشطاً.", mention_author=False)
        # Reuse the existing secure view from AdvertisingShop.
        await ctx.reply(
            f"**اختر نوع المنشن حق الروم**\n{member.mention}",
            view=__import__("cogs.advertising_shop", fromlist=["PrefixAdView"]).PrefixAdView(cog, ctx.author.id, member.id, int(row["channel_id"])),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions(users=True),
        )


async def setup(bot):
    await bot.add_cog(DailyPatch(bot))
    await bot.add_cog(AnnouncementShortcutPatch(bot))
