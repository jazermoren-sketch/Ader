from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


SHORTCUTS = {
    "give_role": "إعطاء رتبة",
    "lock": "قفل الروم",
    "unlock": "فتح الروم",
    "timeout": "تايم اوت",
    "untimeout": "الغاء تايم اوت",
    "kick": "طرد",
    "ban": "بان",
    "warn": "تحذير",
    "member_info": "معلومات العضو",
}
DEFAULT_ALIASES = {
    "give_role": "!رتبة",
    "lock": "!قفل",
    "unlock": "!فتح",
    "timeout": "!تايم اوت",
    "untimeout": "!الغاء تايم اوت",
    "kick": "!طرد",
    "ban": "!بان",
    "warn": "!تحذير",
    "member_info": "!معلومات العضو",
}
INFO_IMAGE = "https://cdn.discordapp.com/attachments/1517582979923185825/1537503900859633744/info-member.png"


class ShortcutSelect(discord.ui.Select):
    def __init__(self, cog: "Shortcuts", hidden: bool):
        self.cog = cog
        self.hidden = hidden
        options = [discord.SelectOption(label=label, value=key) for key, label in SHORTCUTS.items()]
        super().__init__(placeholder="اختر الاختصار...", options=options, custom_id="ader:shortcut_select")

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        await self.cog.show_editor(interaction, key, self.hidden)


class ShortcutView(discord.ui.View):
    def __init__(self, cog: "Shortcuts", hidden: bool):
        super().__init__(timeout=300)
        self.add_item(ShortcutSelect(cog, hidden))


class ShortcutEditor(discord.ui.View):
    def __init__(self, cog: "Shortcuts", key: str, hidden: bool):
        super().__init__(timeout=300)
        self.cog = cog
        self.key = key
        self.hidden = hidden
        self.add_item(EditAliasButton(cog, key, hidden))
        self.add_item(BackButton(cog, hidden))


class EditAliasButton(discord.ui.Button):
    def __init__(self, cog, key, hidden):
        super().__init__(label="تعديل الاختصار", style=discord.ButtonStyle.primary)
        self.cog, self.key, self.hidden = cog, key, hidden

    async def callback(self, interaction: discord.Interaction):
        current = self.cog.get_alias(interaction.guild.id, self.key)
        await interaction.response.send_modal(AliasModal(self.cog, self.key, current, self.hidden))


class BackButton(discord.ui.Button):
    def __init__(self, cog, hidden):
        super().__init__(label="رجوع", style=discord.ButtonStyle.secondary)
        self.cog, self.hidden = cog, hidden

    async def callback(self, interaction: discord.Interaction):
        embed = self.cog.selector_embed()
        view = ShortcutView(self.cog, self.hidden)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)


class AliasModal(discord.ui.Modal, title="تعديل الاختصار"):
    alias = discord.ui.TextInput(label="الاختصار", max_length=50, required=True)

    def __init__(self, cog, key, current, hidden):
        super().__init__()
        self.cog, self.key, self.hidden = cog, key, hidden
        self.alias.default = current

    async def on_submit(self, interaction: discord.Interaction):
        value = self.alias.value.strip()
        if not value.startswith("!"):
            value = "!" + value
        if len(value) < 2 or " " in value:
            return await interaction.response.send_message("❌ الاختصار خاصو يبدأ بـ `!` وما يكونش فيه مسافات.", ephemeral=True)
        self.cog.set_alias(interaction.guild.id, self.key, value)
        await interaction.response.send_message(f"✅ تم تغيير الاختصار إلى `{value}`", ephemeral=True)


class Shortcuts(commands.Cog):
    """Configurable prefix shortcuts for common moderation actions."""

    def __init__(self, bot):
        self.bot = bot
        self.path = Path("data/shortcuts.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self.load()

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_alias(self, guild_id: int, key: str):
        return self.data.get(str(guild_id), {}).get(key, DEFAULT_ALIASES[key])

    def set_alias(self, guild_id: int, key: str, value: str):
        self.data.setdefault(str(guild_id), {})[key] = value
        self.save()

    def selector_embed(self):
        return discord.Embed(title="اختر الاختصار الذي تود التعديل عليه", color=discord.Color.blurple())

    @app_commands.command(name="اختصارات", description="إدارة اختصارات الإدارة")
    @app_commands.describe(اخفاء="إخفاء لوحة إعداد الاختصارات")
    @app_commands.default_permissions(manage_guild=True)
    async def shortcuts(self, interaction: discord.Interaction, اخفاء: bool = False):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        await interaction.response.send_message(
            embed=self.selector_embed(),
            view=ShortcutView(self, اخفاء),
            ephemeral=اخفاء,
        )

    async def show_editor(self, interaction: discord.Interaction, key: str, hidden: bool):
        embed = discord.Embed(title=f"إعدادات اختصار {SHORTCUTS[key]}", color=discord.Color.blurple())
        embed.description = f"الاختصار الحالي: `{self.get_alias(interaction.guild.id, key)}`"
        await interaction.response.edit_message(embed=embed, view=ShortcutEditor(self, key, hidden))

    def aliases_for(self, guild_id: int, key: str):
        return [self.get_alias(guild_id, key)]

    async def execute(self, ctx: commands.Context, key: str, argument: Optional[discord.Member] = None, reason: str = ""):
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return
        if not (ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):
            return await ctx.send("❌ ما عندكش صلاحية استعمال هاد الاختصار.", delete_after=5)
        if key == "lock":
            if not ctx.channel.permissions_for(ctx.guild.me).manage_channels:
                return await ctx.send("❌ البوت ما عندوش Manage Channels.", delete_after=5)
            await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Shortcut lock by {ctx.author}")
            return await ctx.send("🔒 تم قفل الروم.")
        if key == "unlock":
            if not ctx.channel.permissions_for(ctx.guild.me).manage_channels:
                return await ctx.send("❌ البوت ما عندوش Manage Channels.", delete_after=5)
            await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Shortcut unlock by {ctx.author}")
            return await ctx.send("🔓 تم فتح الروم.")
        if not argument:
            return await ctx.send("❌ خاصك تحدد العضو، مثال: `!تايم اوت @عضو`", delete_after=6)
        if argument == ctx.author or argument == ctx.guild.owner or argument.top_role >= ctx.author.top_role:
            return await ctx.send("❌ ما تقدرش تستعمل هاد الإجراء على هاد العضو.", delete_after=6)
        if key == "timeout":
            await argument.timeout(discord.utils.utcnow() + discord.timedelta(minutes=10), reason=reason or f"Shortcut by {ctx.author}")
            return await ctx.send(f"⏱️ تم إعطاء Timeout لـ {argument.mention} لمدة 10 دقائق.")
        if key == "untimeout":
            await argument.timeout(None, reason=reason or f"Shortcut by {ctx.author}")
            return await ctx.send(f"✅ تم إلغاء Timeout لـ {argument.mention}.")
        if key == "kick":
            await argument.kick(reason=reason or f"Shortcut by {ctx.author}")
            return await ctx.send(f"👢 تم طرد {argument.mention}.")
        if key == "ban":
            await argument.ban(reason=reason or f"Shortcut by {ctx.author}", delete_message_days=0)
            return await ctx.send(f"🔨 تم حظر {argument.mention}.")
        if key == "member_info":
            embed = discord.Embed(title=f"معلومات العضو: {argument}", color=discord.Color.blurple())
            embed.set_thumbnail(url=argument.display_avatar.url)
            embed.set_image(url=INFO_IMAGE)
            embed.add_field(name="الاسم", value=argument.mention, inline=True)
            embed.add_field(name="ID", value=str(argument.id), inline=True)
            embed.add_field(name="الحساب", value=discord.utils.format_dt(argument.created_at, "F"), inline=False)
            embed.add_field(name="دخل السيرفر", value=discord.utils.format_dt(argument.joined_at, "F") if argument.joined_at else "غير معروف", inline=False)
            return await ctx.send(embed=embed)
        return await ctx.send("ℹ️ هاد الاختصار مضاف للإعدادات، والتنفيذ الخاص به خاصو يتربط مع نظام الإدارة الحالي.", delete_after=7)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content.startswith("!"):
            return
        content = message.content.split()
        raw = content[0]
        for key in SHORTCUTS:
            if raw == self.get_alias(message.guild.id, key):
                ctx = await self.bot.get_context(message)
                member = message.mentions[0] if message.mentions else None
                await self.execute(ctx, key, member)
                return


async def setup(bot):
    await bot.add_cog(Shortcuts(bot))
