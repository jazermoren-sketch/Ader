from __future__ import annotations

import discord
from discord.ext import commands


class ShortcutFinalizer(commands.Cog):
    """Finalizes the $ prefix and makes $اعلان a single customization/entry command."""
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        prefixes = self.bot.command_prefix
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        else:
            prefixes = list(prefixes)
        if "$" not in prefixes:
            prefixes.append("$")
        self.bot.command_prefix = list(dict.fromkeys(prefixes))

        old = self.bot.get_command("اعلان")
        if old is not None:
            self.bot.remove_command(old.name)

    @commands.command(name="اعلان", aliases=("إعلان",))
    async def اعلان(self, ctx: commands.Context, member: discord.Member | None = None):
        ad = self.bot.get_cog("AdvertisingShop")
        if ad is None:
            return await ctx.reply("❌ نظام الإعلانات غير متاح حالياً.", mention_author=False)
        if not await ad.authorized(ctx.author):
            return await ctx.reply("❌ هذا الأمر محمي. تحتاج Administrator أو رتبة مسموحة.", mention_author=False)

        if member is None:
            embed = discord.Embed(
                title="⚙️ تخصيص نظام الإعلانات",
                description=(
                    "هذه هي نقطة التحكم الرئيسية بنظام `$اعلان`.\n\n"
                    "📌 **إضافة رتبة** — السماح لرتبة باستعمال النظام.\n"
                    "📌 **إزالة رتبة** — سحب الصلاحية من رتبة.\n"
                    "📌 **طريقة الاستخدام** — شرح النظام.\n\n"
                    "بعد ذلك استعمل `$اعلان @user` لاختيار نوع المنشن وإرسال الإعلان."
                ),
                colour=discord.Colour.blurple(),
            )
            view = AnnouncementSettingsView(ad, ctx.author.id)
            return await ctx.reply(embed=embed, view=view, mention_author=False)

        row = await ad.db.fetchone(
            "SELECT * FROM ad_rooms WHERE guild_id=? AND owner_id=? AND active=1",
            (ctx.guild.id, member.id),
        )
        if not row:
            return await ctx.reply("❌ هذا العضو لا يملك روم إعلان نشطاً.", mention_author=False)

        from cogs.advertising_shop import PrefixAdView
        await ctx.reply(
            f"**اختر نوع المنشن حق الروم**\n{member.mention}",
            view=PrefixAdView(ad, ctx.author.id, member.id, int(row["channel_id"])),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions(users=True),
        )


class AnnouncementSettingsView(discord.ui.View):
    def __init__(self, ad, owner_id: int):
        super().__init__(timeout=180)
        self.ad = ad
        self.owner_id = owner_id

    async def check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ هذه اللوحة ليست لك.", ephemeral=True)
            return False
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels or interaction.user.id == 1472570059367911587):
            await interaction.response.send_message("❌ تحتاج Administrator أو Manage Channels.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="إضافة رتبة", emoji="➕", style=discord.ButtonStyle.success)
    async def add_role(self, interaction, button):
        if await self.check(interaction):
            await interaction.response.send_modal(RoleModal(self.ad, True))

    @discord.ui.button(label="إزالة رتبة", emoji="➖", style=discord.ButtonStyle.danger)
    async def remove_role(self, interaction, button):
        if await self.check(interaction):
            await interaction.response.send_modal(RoleModal(self.ad, False))

    @discord.ui.button(label="عرض الرتب المسموحة", emoji="📋", style=discord.ButtonStyle.secondary)
    async def list_roles(self, interaction, button):
        if not await self.check(interaction):
            return
        import json
        row = await self.ad.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (interaction.guild.id,))
        ids = json.loads(row["allowed_roles"] or "[]") if row else []
        roles = [interaction.guild.get_role(int(x)) for x in ids]
        roles = [r.mention for r in roles if r]
        await interaction.response.send_message("📋 الرتب المسموحة:\n" + ("\n".join(roles) if roles else "لا توجد رتب مخصصة."), ephemeral=True)


class RoleModal(discord.ui.Modal, title="تخصيص صلاحية `$اعلان`"):
    role_id = discord.ui.TextInput(label="ID الرتبة", placeholder="أدخل ID الرتبة", min_length=1, max_length=25)

    def __init__(self, ad, adding: bool):
        super().__init__(custom_id=f"ader:final-role:{'add' if adding else 'remove'}")
        self.ad, self.adding = ad, adding

    async def on_submit(self, interaction):
        if not (interaction.user.guild_permissions.administrator or interaction.user.id == 1472570059367911587):
            return await interaction.response.send_message("❌ تحتاج Administrator.", ephemeral=True)
        try:
            rid = int(str(self.role_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ ID غير صحيح.", ephemeral=True)
        role = interaction.guild.get_role(rid)
        if role is None:
            return await interaction.response.send_message("❌ لم يتم العثور على الرتبة.", ephemeral=True)
        import json
        row = await self.ad.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (interaction.guild.id,))
        ids = set(json.loads(row["allowed_roles"] or "[]")) if row else set()
        if self.adding:
            ids.add(role.id)
        else:
            ids.discard(role.id)
        await self.ad.db.execute(
            "INSERT INTO ad_settings(guild_id,allowed_roles) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allowed_roles=excluded.allowed_roles",
            (interaction.guild.id, json.dumps(sorted(ids))),
        )
        await interaction.response.send_message(
            f"✅ {'تمت إضافة' if self.adding else 'تمت إزالة'} {role.mention} {'إلى' if self.adding else 'من'} صلاحيات `$اعلان`.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(ShortcutFinalizer(bot))
