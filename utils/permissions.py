"""
Permission checks and utilities for Logiq.

Moderation slash commands are intentionally mapped to their minimum Discord
permission.  Administrator inherits every Discord permission, so admins still
pass every moderation check without making Administrator the requirement for
all moderation actions.
"""

import discord
from discord import app_commands
from typing import Optional


def is_admin():
    """Check if user has Administrator permission."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.user.guild_permissions.administrator)
    return app_commands.check(predicate)


_MODERATION_PERMISSIONS = {
    "warn": "manage_messages",
    "warnings": "manage_messages",
    "timeout": "moderate_members",
    "kick": "kick_members",
    "ban": "ban_members",
    "unban": "ban_members",
    "clear": "manage_messages",
    "slowmode": "manage_channels",
    "lock": "manage_channels",
    "unlock": "manage_channels",
    "nickname": "manage_nicknames",
}


def is_moderator():
    """Require the minimum Discord permission for each moderation command.

    The decorator also sets Discord's native ``default_permissions`` metadata,
    so the slash-command UI reflects the same permission requirement instead
    of merely rejecting the command after a user invokes it.
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False

        perms = interaction.user.guild_permissions
        if perms.administrator:
            return True

        command = getattr(interaction, "command", None)
        command_name = getattr(command, "name", None)
        required = _MODERATION_PERMISSIONS.get(command_name)
        if required is None:
            return False
        return bool(getattr(perms, required, False))

    def decorator(command):
        command_name = getattr(command, "name", None) or getattr(command, "__name__", "")
        required = _MODERATION_PERMISSIONS.get(command_name)

        if required:
            command = app_commands.default_permissions(**{required: True})(command)
        else:
            # Never accidentally expose a future moderation command without a
            # known permission.  The runtime predicate remains fail-closed too.
            command = app_commands.default_permissions(administrator=True)(command)

        return app_commands.check(predicate)(command)

    return decorator


def has_role(role_id: int):
    """Check if user has specific role."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return any(role.id == role_id for role in getattr(interaction.user, "roles", []))
    return app_commands.check(predicate)


def bot_has_permissions(**perms):
    """Check if bot has required permissions."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not interaction.guild.me:
            return False
        bot_perms = interaction.guild.me.guild_permissions
        return all(getattr(bot_perms, perm, False) for perm in perms)
    return app_commands.check(predicate)


def is_guild_owner():
    """Check if user is guild owner."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.user.id == interaction.guild.owner_id)
    return app_commands.check(predicate)


class PermissionChecker:
    """Utility class for permission checking."""

    @staticmethod
    def check_hierarchy(
        executor: discord.Member,
        target: discord.Member
    ) -> bool:
        """Check if executor is higher in role hierarchy than target."""
        if executor.guild.owner_id == executor.id:
            return True
        if target.guild.owner_id == target.id:
            return False
        return executor.top_role > target.top_role

    @staticmethod
    def can_moderate(
        moderator: discord.Member,
        target: discord.Member
    ) -> tuple[bool, Optional[str]]:
        """Check whether a moderator can act on a target."""
        if moderator.id == target.id:
            return False, "You cannot moderate yourself"
        if target.guild.owner_id == target.id:
            return False, "You cannot moderate the server owner"
        if not PermissionChecker.check_hierarchy(moderator, target):
            return False, "You cannot moderate someone with a higher or equal role"
        return True, None

    @staticmethod
    def has_permission(
        member: discord.Member,
        permission: str
    ) -> bool:
        """Check if member has a specific Discord permission."""
        return getattr(member.guild_permissions, permission, False)

    @staticmethod
    def get_missing_permissions(
        member: discord.Member,
        required_permissions: list[str]
    ) -> list[str]:
        """Return required Discord permissions missing from a member."""
        return [
            perm for perm in required_permissions
            if not getattr(member.guild_permissions, perm, False)
        ]
