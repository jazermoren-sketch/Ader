"""Compatibility bridge for the shortcuts command permission check.

Discord application-command interactions carry an effective permission snapshot
on ``Interaction.permissions``. The shortcuts cog historically checked only
``interaction.user.guild_permissions``; that can be stale for a resolved
Member and can incorrectly reject users who have Manage Server/Administrator.

Keep the fix isolated here so the existing shortcuts UI/behavior is unchanged.
The centralized permission implementation lives in ``utils.permissions``.
"""
from __future__ import annotations

import discord

from cogs.shortcuts import Shortcuts


def _can_manage(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if guild is None:
        return False

    # The owner has full guild control even if the member/role cache is stale.
    if getattr(interaction.user, "id", None) == guild.owner_id:
        return True

    # For application commands, Discord supplies the effective permissions on
    # the interaction itself. Prefer this over a potentially stale Member.
    permissions = getattr(interaction, "permissions", None)
    if permissions is not None:
        return bool(permissions.administrator or permissions.manage_guild)

    # Defensive fallback for interactions created by tests/older integrations.
    member_permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(
        member_permissions
        and (member_permissions.administrator or member_permissions.manage_guild)
    )


# Shortcuts calls this method synchronously from commands, select menus,
# buttons, and modals. Keep that contract while fixing the permission source.
Shortcuts.can_manage = staticmethod(_can_manage)


class ShortcutsPermissionFix(discord.Client):
    """No-op marker; the patch above is applied when this module is imported."""


async def setup(bot):
    # The module import is enough to apply the class-level compatibility fix.
    # Do not add another bot/client or duplicate the Shortcuts cog.
    return None
