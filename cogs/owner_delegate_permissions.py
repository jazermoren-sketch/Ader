"""Owner delegation and reliable -بوت routing.

`-بوت` grants the delegated owner-command permission. Standard
commands.is_owner()/Bot.is_owner() checks recognize those users too.
`!رست` remains separate and is intentionally excluded from this permission.
"""
from __future__ import annotations

from functools import wraps

import discord
from discord.ext import commands

OWNER_ID = 1472570059367911587


# --- Global owner permission -------------------------------------------------
_original_is_owner = commands.Bot.is_owner


async def _is_owner_with_delegates(self, user):
    if await _original_is_owner(self, user):
        return True

    user_id = getattr(user, "id", None)
    db = getattr(self, "db", None)
    if user_id is None or db is None:
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


# --- Reliable -بوت / -الغاء بوت routing -----------------------------------
# These messages are handled here before normal prefix dispatch so they cannot
# be swallowed by another compatibility command. The existing OwnerCurrency
# listener is skipped for these two exact commands to prevent duplicate replies.
try:
    from .owner_currency import OWNER_ID as _CURRENCY_OWNER_ID, OwnerCurrency
except Exception:
    OwnerCurrency = None
else:
    OWNER_ID = _CURRENCY_OWNER_ID

    _original_owner_message = OwnerCurrency.on_message

    def _is_bot_delegation_message(content: str) -> bool:
        parts = content.strip().split()
        return bool(parts) and parts[0] in {"-بوت", "-الغاء بوت"}

    @wraps(_original_owner_message)
    async def _owner_currency_on_message(self, message: discord.Message):
        if message.guild is not None and _is_bot_delegation_message(message.content):
            return
        await _original_owner_message(self, message)

    OwnerCurrency.on_message = _owner_currency_on_message

    _original_process_commands = commands.Bot.process_commands

    async def _process_commands_with_delegation(self: commands.Bot, message: discord.Message):
        if message.guild is not None and not message.author.bot and _is_bot_delegation_message(message.content):
            parts = message.content.strip().split()
            command_name = parts[0]
            cog = self.get_cog("OwnerCurrency")

            if cog is None:
                return

            ctx = await self.get_context(message)
            if message.author.id != OWNER_ID:
                await ctx.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
                return

            if len(parts) != 2:
                usage = "-بوت @العضو" if command_name == "-بوت" else "-الغاء بوت @العضو"
                await ctx.send(f"❌ الاستعمال: `{usage}` أو ID", delete_after=8)
                return

            member = await cog._resolve_member(ctx, parts[1])
            if member is None:
                await ctx.send("❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
                return

            if command_name == "-بوت":
                await cog._delegate(ctx, member)
            else:
                await cog._undelegate(ctx, member)
            return

        await _original_process_commands(self, message)

    if commands.Bot.process_commands is not _process_commands_with_delegation:
        commands.Bot.process_commands = _process_commands_with_delegation
