"""Reliable replacement for the /اختصارات slash command.

This hotfix intentionally reuses the existing Shortcuts cog for persistence and
runtime alias handling, while replacing only the fragile slash-command UI.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands


log = logging.getLogger("Ader.shortcuts")


class ShortcutsSlashHotfix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _shortcuts_cog(self):
        return self.bot.get_cog("Shortcuts")

    def _embed(self, title: str, description: str = "") -> discord.Embed:
        return discord.Embed(title=title, description=description, color=discord.Color.blurple())

    async def _send_error(self, interaction: discord.Interaction, text: str):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="اختصارات", description="إدارة اختصارات الإدارة")
    @app_commands.describe(اخفاء="إخفاء لوحة إعداد الاختصارات")
    @app_commands.default_permissions(manage_guild=True)
    async def shortcuts(self, interaction: discord.Interaction, اخفاء: bool = False):
        if interaction.guild is None:
            await self._send_error(interaction, "❌ هذا الأمر خاص بالسيرفرات.")
            return

        member = interaction.user
        permissions = getattr(member, "guild_permissions", None)
        if permissions is None or not (permissions.manage_guild or permissions.administrator):
            await self._send_error(interaction, "❌ تحتاج إلى صلاحية Manage Server أو Administrator.")
            return

        shortcuts_cog = self._shortcuts_cog()
        if shortcuts_cog is None:
            log.error("/اختصارات requested but Shortcuts cog is not loaded")
            await self._send_error(interaction, "❌ نظام الاختصارات غير جاهز حالياً. أعد تشغيل البوت.")
            return

        try:
            await interaction.response.send_message(
                embed=self._embed(
                    "⚙️ إدارة الاختصارات",
                    "اختر الاختصار الذي تريد تعديله من القائمة أسفله.",
                ),
                view=ShortcutMenuView(self, bool(اخفاء)),
                ephemeral=bool(اخفاء),
            )
        except Exception:
            log.exception("Failed to open /اختصارات for guild %s", interaction.guild.id)
            await self._send_error(interaction, "❌ تعذر فتح لوحة الاختصارات. تم تسجيل الخطأ في Log البوت.")

    async def show_editor(self, interaction: discord.Interaction, key: str, hidden: bool):
        shortcuts_cog = self._shortcuts_cog()
        if shortcuts_cog is None:
            await self._send_error(interaction, "❌ نظام الاختصارات غير جاهز حالياً.")
            return

        label = shortcuts_cog.SHORTCUTS[key] if hasattr(shortcuts_cog, "SHORTCUTS") else None
        # SHORTCUTS is module-level in the legacy cog; keep a stable fallback.
        labels = {
            "give_role": "إعطاء رتبة", "lock": "قفل الروم", "unlock": "فتح الروم",
            "timeout": "تايم اوت", "untimeout": "إلغاء تايم اوت", "kick": "طرد",
            "ban": "بان", "warn": "تحذير", "member_info": "معلومات العضو",
        }
        label = label or labels.get(key, key)
        try:
            current = shortcuts_cog.get_alias(interaction.guild.id, key)
            await interaction.response.edit_message(
                embed=self._embed(f"⚙️ إعدادات: {label}", f"الاختصار الحالي: `{current}`"),
                view=ShortcutEditorView(self, key, hidden, current),
            )
        except Exception:
            log.exception("Failed to open shortcut editor for %s", key)
            await self._send_error(interaction, "❌ تعذر فتح إعدادات الاختصار. حاول مرة أخرى.")

    async def save_alias(self, interaction: discord.Interaction, key: str, value: str):
        shortcuts_cog = self._shortcuts_cog()
        if shortcuts_cog is None or interaction.guild is None:
            await self._send_error(interaction, "❌ نظام الاختصارات غير جاهز حالياً.")
            return

        value = value.strip()
        if not value.startswith("!"):
            value = "!" + value
        if len(value) < 2 or any(char.isspace() for char in value):
            await self._send_error(interaction, "❌ الاختصار خاصو يبدأ بـ `!` وما يكونش فيه مسافات.")
            return

        try:
            shortcuts_cog.set_alias(interaction.guild.id, key, value)
            await interaction.response.send_message(f"✅ تم تغيير الاختصار إلى `{value}`", ephemeral=True)
        except Exception:
            log.exception("Failed to save shortcut alias for %s", key)
            await self._send_error(interaction, "❌ تعذر حفظ الاختصار. حاول مرة أخرى.")


class ShortcutMenuSelect(discord.ui.Select):
    OPTIONS = [
        discord.SelectOption(label="إعطاء رتبة", value="give_role"),
        discord.SelectOption(label="قفل الروم", value="lock"),
        discord.SelectOption(label="فتح الروم", value="unlock"),
        discord.SelectOption(label="تايم اوت", value="timeout"),
        discord.SelectOption(label="إلغاء تايم اوت", value="untimeout"),
        discord.SelectOption(label="طرد", value="kick"),
        discord.SelectOption(label="بان", value="ban"),
        discord.SelectOption(label="تحذير", value="warn"),
        discord.SelectOption(label="معلومات العضو", value="member_info"),
    ]

    def __init__(self, cog: ShortcutsSlashHotfix, hidden: bool):
        self.cog = cog
        self.hidden = hidden
        super().__init__(placeholder="اختر الاختصار...", options=self.OPTIONS)

    async def callback(self, interaction: discord.Interaction):
        await self.cog.show_editor(interaction, self.values[0], self.hidden)


class ShortcutMenuView(discord.ui.View):
    def __init__(self, cog: ShortcutsSlashHotfix, hidden: bool):
        super().__init__(timeout=300)
        self.add_item(ShortcutMenuSelect(cog, hidden))


class EditShortcutButton(discord.ui.Button):
    def __init__(self, cog: ShortcutsSlashHotfix, key: str, current: str):
        super().__init__(label="تعديل الاختصار", style=discord.ButtonStyle.primary)
        self.cog = cog
        self.key = key
        self.current = current

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ShortcutAliasModal(self.cog, self.key, self.current))


class BackToMenuButton(discord.ui.Button):
    def __init__(self, cog: ShortcutsSlashHotfix, hidden: bool):
        super().__init__(label="رجوع", style=discord.ButtonStyle.secondary)
        self.cog = cog
        self.hidden = hidden

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=self.cog._embed("⚙️ إدارة الاختصارات", "اختر الاختصار الذي تريد تعديله من القائمة أسفله."),
            view=ShortcutMenuView(self.cog, self.hidden),
        )


class ShortcutEditorView(discord.ui.View):
    def __init__(self, cog: ShortcutsSlashHotfix, key: str, hidden: bool, current: str):
        super().__init__(timeout=300)
        self.add_item(EditShortcutButton(cog, key, current))
        self.add_item(BackToMenuButton(cog, hidden))


class ShortcutAliasModal(discord.ui.Modal, title="تعديل الاختصار"):
    alias = discord.ui.TextInput(label="الاختصار", max_length=50, required=True)

    def __init__(self, cog: ShortcutsSlashHotfix, key: str, current: str):
        super().__init__()
        self.cog = cog
        self.key = key
        self.alias.default = current

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.save_alias(interaction, self.key, self.alias.value)


async def setup(bot: commands.Bot):
    await bot.add_cog(ShortcutsSlashHotfix(bot))
