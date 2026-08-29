"""
Permission checks and utilities for Logiq.

Permission checks tolerate partially resolved Discord members and stale/deleted
role IDs. Interaction-level permissions are preferred, and moderation hierarchy
falls back to explicit cached role positions when Member.top_role is unusable.
"""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands


async def _get_interaction_permissions(interaction: discord.Interaction) -> Optional[discord.Permissions]:
    """Resolve the effective permissions for an interaction reliably.

    ``interaction.permissions`` is the authoritative permission snapshot sent
    with an application command interaction. Falling back through the resolved
    member/cache/API keeps checks working when ``interaction.user`` is a stale
    or partially resolved Member object.
    """
    if interaction.guild is None:
        return None

    perms = getattr(interaction, "permissions", None)
    if perms is not None:
        return perms

    member = interaction.user
    try:
        if isinstance(member, discord.Member):
            return member.guild_permissions
    except (AttributeError, TypeError):
        pass

    cached_member = interaction.guild.get_member(interaction.user.id)
    if cached_member is not None:
        try:
            return cached_member.guild_permissions
        except (AttributeError, TypeError):
            pass

    try:
        fetched_member = await interaction.guild.fetch_member(interaction.user.id)
        return fetched_member.guild_permissions
    except (discord.HTTPException, AttributeError, TypeError):
        return None


async def can_manage_guild(interaction: discord.Interaction) -> bool:
    """Return whether the interaction user may manage the current guild.

    This is the single source of truth for commands that require either
    Administrator or Manage Server. It intentionally uses the interaction's
    effective Discord permission snapshot before relying on Member data, which
    prevents false denials caused by stale Member role caches.
    """
    guild = interaction.guild
    if guild is None:
        return False

    # Guild owners have full control regardless of role-cache state.
    if getattr(interaction.user, "id", None) == guild.owner_id:
        return True

    perms = await _get_interaction_permissions(interaction)
    return bool(perms and (perms.administrator or perms.manage_guild))


def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        perms = await _get_interaction_permissions(interaction)
        return bool(perms and perms.administrator)
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
    async def predicate(interaction: discord.Interaction) -> bool:
        perms = await _get_interaction_permissions(interaction)
        if perms is None:
            return False
        if perms.administrator:
            return True
        command = getattr(interaction, "command", None)
        required = _MODERATION_PERMISSIONS.get(getattr(command, "name", None))
        return bool(required and getattr(perms, required, False))

    def decorator(command):
        command_name = getattr(command, "name", None) or getattr(command, "__name__", "")
        required = _MODERATION_PERMISSIONS.get(command_name)
        if required:
            command = app_commands.default_permissions(**{required: True})(command)
        else:
            command = app_commands.default_permissions(administrator=True)(command)
        return app_commands.check(predicate)(command)
    return decorator


def has_role(role_id: int):
    async def predicate(interaction: discord.Interaction) -> bool:
        return any(role.id == role_id for role in getattr(interaction.user, "roles", []))
    return app_commands.check(predicate)


def bot_has_permissions(**perms):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not interaction.guild.me:
            return False
        try:
            bot_perms = interaction.guild.me.guild_permissions
        except (AttributeError, TypeError):
            return False
        return all(getattr(bot_perms, perm, False) for perm in perms)
    return app_commands.check(predicate)


def is_guild_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.user.id == interaction.guild.owner_id)
    return app_commands.check(predicate)


class PermissionChecker:
    @staticmethod
    def _highest_role_position(member: discord.Member) -> int:
        guild = getattr(member, "guild", None)
        if guild is None:
            return 0
        highest = int(getattr(getattr(guild, "default_role", None), "position", 0) or 0)
        for role_id in getattr(member, "_roles", ()):
            try:
                role_id = int(role_id)
            except (TypeError, ValueError):
                continue
            role = guild.get_role(role_id)
            if role is not None:
                highest = max(highest, int(getattr(role, "position", 0) or 0))
        return highest

    @staticmethod
    def check_hierarchy(executor: discord.Member, target: discord.Member) -> bool:
        if executor.guild.id != target.guild.id:
            return False
        if executor.guild.owner_id == executor.id:
            return True
        if target.guild.owner_id == target.id:
            return False
        try:
            executor_role = executor.top_role
            target_role = target.top_role
            if executor_role is not None and target_role is not None:
                return executor_role > target_role
        except (AttributeError, TypeError):
            pass
        return PermissionChecker._highest_role_position(executor) > PermissionChecker._highest_role_position(target)

    @staticmethod
    def can_moderate(moderator: discord.Member, target: discord.Member) -> tuple[bool, Optional[str]]:
        if moderator.id == target.id:
            return False, "You cannot moderate yourself"
        if target.guild.owner_id == target.id:
            return False, "You cannot moderate the server owner"
        if not PermissionChecker.check_hierarchy(moderator, target):
            return False, "You cannot moderate someone with a higher or equal role"
        return True, None

    @staticmethod
    def has_permission(member: discord.Member, permission: str) -> bool:
        try:
            return bool(getattr(member.guild_permissions, permission, False))
        except (AttributeError, TypeError):
            return False

    @staticmethod
    def get_missing_permissions(member: discord.Member, required_permissions: list[str]) -> list[str]:
        return [perm for perm in required_permissions if not PermissionChecker.has_permission(member, perm)]
