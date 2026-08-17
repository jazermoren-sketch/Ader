"""Dashboard-backed permissions for slash commands and prefix shortcuts."""
from __future__ import annotations

import json
from typing import Any

import discord
from discord.ext import commands


class DashboardGuard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._prefix_check_registered = False

    async def cog_load(self):
        await self.bot.db.execute(
            """CREATE TABLE IF NOT EXISTS dashboard_command_settings (
                guild_id INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_roles TEXT NOT NULL DEFAULT '[]',
                denied_roles TEXT NOT NULL DEFAULT '[]',
                allowed_channels TEXT NOT NULL DEFAULT '[]',
                denied_channels TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id, command_name)
            )"""
        )
        await self.bot.db.execute(
            """CREATE TABLE IF NOT EXISTS dashboard_shortcut_settings (
                guild_id INTEGER NOT NULL,
                shortcut_name TEXT NOT NULL,
                alias TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_roles TEXT NOT NULL DEFAULT '[]',
                denied_roles TEXT NOT NULL DEFAULT '[]',
                allowed_channels TEXT NOT NULL DEFAULT '[]',
                denied_channels TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id, shortcut_name)
            )"""
        )
        if not self._prefix_check_registered:
            self.bot.add_check(self.prefix_check)
            self._prefix_check_registered = True

    @staticmethod
    def _ids(value: Any) -> set[int]:
        try:
            return {int(x) for x in json.loads(value or "[]")}
        except (TypeError, ValueError, json.JSONDecodeError):
            return set()

    async def _settings(self, guild_id: int, name: str):
        return await self.bot.db.fetchone(
            "SELECT * FROM dashboard_command_settings WHERE guild_id=? AND command_name=?",
            (guild_id, name),
        )

    async def allowed(self, guild: discord.Guild, member: discord.Member, name: str, channel_id: int | None) -> bool:
        row = await self._settings(guild.id, name)
        if not row:
            return True
        if member.guild_permissions.administrator:
            return True
        if not bool(row["enabled"]):
            return False
        roles = {r.id for r in member.roles}
        denied_roles = self._ids(row["denied_roles"])
        allowed_roles = self._ids(row["allowed_roles"])
        if denied_roles & roles:
            return False
        if allowed_roles and not (allowed_roles & roles):
            return False
        denied_channels = self._ids(row["denied_channels"])
        allowed_channels = self._ids(row["allowed_channels"])
        cid = int(channel_id or 0)
        if cid in denied_channels:
            return False
        if allowed_channels and cid not in allowed_channels:
            return False
        return True

    async def prefix_check(self, ctx: commands.Context) -> bool:
        if not ctx.guild or not ctx.command or not isinstance(ctx.author, discord.Member):
            return True
        name = getattr(ctx.command, "qualified_name", ctx.command.name)
        ok = await self.allowed(ctx.guild, ctx.author, name, ctx.channel.id)
        if not ok:
            await ctx.send("❌ هذا الأمر غير متاح لك في هذا الروم وفق إعدادات لوحة التحكم.", delete_after=6)
        return ok

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.command:
            return True
        name = getattr(interaction.command, "qualified_name", interaction.command.name)
        ok = await self.allowed(interaction.guild, interaction.user, name, interaction.channel_id)
        if not ok:
            if interaction.response.is_done():
                await interaction.followup.send("❌ هذا الأمر غير متاح لك في هذا الروم وفق إعدادات لوحة التحكم.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ هذا الأمر غير متاح لك في هذا الروم وفق إعدادات لوحة التحكم.", ephemeral=True)
        return ok


async def setup(bot):
    await bot.add_cog(DashboardGuard(bot))
    # CommandTree has one global interaction gate for application commands.
    guard = bot.get_cog("DashboardGuard")
    if guard:
        bot.tree.interaction_check = guard.interaction_check
