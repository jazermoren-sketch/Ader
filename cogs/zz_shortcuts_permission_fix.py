"""Hotfix for /اختصارات permission detection.

This cog is intentionally loaded after cogs.shortcuts (alphabetical order) and
replaces only the slash-command permission gate.  The existing shortcut storage
and UI classes remain unchanged.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from cogs.shortcuts import ShortcutView


class ShortcutsPermissionFix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Replace the previous registration made by cogs.shortcuts.
        existing = self.bot.tree.get_command("اختصارات")
        if existing is not None:
            self.bot.tree.remove_command("اختصارات")
        self.bot.tree.add_command(self.shortcuts)

    def _allowed(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            return False

        # The owner must always be allowed, independently of cache state.
        if interaction.user.id == guild.owner_id:
            return True

        # Application-command interactions expose resolved permissions here.
        perms = getattr(interaction, "permissions", None)
        if perms is not None and (
            getattr(perms, "administrator", False)
            or getattr(perms, "manage_guild", False)
        ):
            return True

        # Fallback to the cached Member permissions.
        member_perms = getattr(interaction.user, "guild_permissions", None)
        return bool(
            member_perms
            and (
                getattr(member_perms, "administrator", False)
                or getattr(member_perms, "manage_guild", False)
            )
        )

    @app_commands.command(name="اختصارات", description="إدارة اختصارات الإدارة")
    @app_commands.describe(اخفاء="إخفاء لوحة إعداد الاختصارات")
    async def shortcuts(self, interaction: discord.Interaction, اخفاء: bool = False):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ هذا الأمر خاص بالسيرفرات.", ephemeral=True
            )
            return

        if not self._allowed(interaction):
            await interaction.response.send_message(
                "❌ تحتاج إلى صلاحية Manage Server أو Administrator.", ephemeral=True
            )
            return

        original = self.bot.get_cog("Shortcuts")
        if original is None:
            await interaction.response.send_message(
                "❌ نظام الاختصارات غير جاهز حالياً. أعد تشغيل البوت.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=original.selector_embed(),
            view=ShortcutView(original, اخفاء),
            ephemeral=اخفاء,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ShortcutsPermissionFix(bot))
