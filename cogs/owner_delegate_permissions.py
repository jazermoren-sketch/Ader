"""Extend discord.py owner checks to include delegated bot-owner users.

`-بوت` grants the delegated owner-command permission. This compatibility
patch makes the standard commands.is_owner()/Bot.is_owner() checks recognize
those users too. The reset command is intentionally NOT handled here; its
separate permission is enforced by cogs.owner_currency.
"""
from __future__ import annotations

from discord.ext import commands


_original_is_owner = commands.Bot.is_owner


async def _is_owner_with_delegates(self, user):
    if await _original_is_owner(self, user):
        return True

    db = getattr(self, "db", None)
    if db is None:
        return False

    user_id = getattr(user, "id", None)
    if user_id is None:
        return False

    try:
        row = await db.fetchone(
            "SELECT 1 FROM owner_command_delegates WHERE user_id=? LIMIT 1",
            (int(user_id),),
        )
        return row is not None
    except Exception:
        return False


if commands.Bot.is_owner is not _is_owner_with_delegates:
    commands.Bot.is_owner = _is_owner_with_delegates
