"""Make -بوت delegation count as Discord.py owner permission.

This keeps the existing delegation storage in OwnerCurrency, but teaches
commands.is_owner() / Bot.is_owner() about delegated owner users. The reset
permission is intentionally not included here and stays separate.
"""
from __future__ import annotations

import discord
from discord.ext import commands

OWNER_ID = 1472570059367911587

_original_is_owner = commands.Bot.is_owner


async def _delegated_is_owner(self: commands.Bot, user: discord.abc.User) -> bool:
    if await _original_is_owner(self, user):
        return True

    if getattr(user, "id", None) == OWNER_ID:
        return True

    cog = self.get_cog("OwnerCurrency")
    if cog is None:
        return False

    try:
        return await cog._is_delegate(int(user.id))
    except Exception:
        return False


if commands.Bot.is_owner is not _delegated_is_owner:
    commands.Bot.is_owner = _delegated_is_owner
