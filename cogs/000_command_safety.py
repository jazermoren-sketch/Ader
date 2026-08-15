"""Global Discord interaction safety and duplicate-command protection.

Loaded first so command registration and UI failures cannot leave the bot with
partially loaded moderation systems or unanswered interactions.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


async def _view_error(view, interaction: discord.Interaction, error: Exception, item):
    print(f"UI error [{type(item).__name__}]: {error!r}")
    try:
        text = "❌ وقع خطأ أثناء تنفيذ العملية. حاول مرة أخرى."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except Exception:
        pass


async def _modal_error(modal, interaction: discord.Interaction, error: Exception):
    print(f"Modal error [{type(modal).__name__}]: {error!r}")
    try:
        text = "❌ وقع خطأ أثناء حفظ البيانات. حاول مرة أخرى."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except Exception:
        pass


class CommandSafety(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._original_add = bot.tree.add_command
        self._installed = False

    async def cog_load(self):
        if not self._installed:
            original = self._original_add

            def safe_add(command, *, guild=None, guilds=None, override=False):
                # Discord only permits one command with a given name/scope.
                # Replacing a stale/legacy registration is preferable to aborting
                # the whole cog and losing all its other commands.
                try:
                    return original(command, guild=guild, guilds=guilds, override=override)
                except app_commands.CommandAlreadyRegistered:
                    return original(command, guild=guild, guilds=guilds, override=True)

            self.bot.tree.add_command = safe_add
            discord.ui.View.on_error = _view_error
            discord.ui.Modal.on_error = _modal_error
            self._installed = True

            async def tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
                print(f"Application command error: {getattr(error, 'original', error)!r}")
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send("❌ وقع خطأ أثناء تنفيذ الأمر. حاول مرة أخرى.", ephemeral=True)
                    else:
                        await interaction.response.send_message("❌ وقع خطأ أثناء تنفيذ الأمر. حاول مرة أخرى.", ephemeral=True)
                except Exception:
                    pass

            self.bot.tree.on_error = tree_error


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandSafety(bot))
