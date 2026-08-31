"""Reliable routing for the -بوت owner-delegation commands.

The delegation commands are handled before normal prefix dispatch, while the
legacy OwnerCurrency message listener is skipped for these two commands to
avoid competing handlers or duplicate responses. The reset permission stays
separate and is not affected.
"""
from __future__ import annotations

from functools import wraps

import discord
from discord.ext import commands

from .owner_currency import OWNER_ID, OwnerCurrency


_original_process_commands = commands.Bot.process_commands
_original_owner_message = OwnerCurrency.on_message


def _is_bot_delegation_message(content: str) -> bool:
    parts = content.strip().split()
    return bool(parts) and parts[0] in {"-بوت", "-الغاء بوت"}


@wraps(_original_owner_message)
async def _owner_currency_on_message(self: OwnerCurrency, message: discord.Message):
    if message.guild is not None and _is_bot_delegation_message(message.content):
        return
    await _original_owner_message(self, message)


OwnerCurrency.on_message = _owner_currency_on_message


async def _process_commands_with_bot_delegation(self: commands.Bot, message: discord.Message):
    if message.guild is not None and not message.author.bot and _is_bot_delegation_message(message.content):
        parts = message.content.strip().split()
        command_name = parts[0]
        target_text = " ".join(parts[1:]).strip()
        cog = self.get_cog("OwnerCurrency")

        if cog is None:
            return

        if message.author.id != OWNER_ID:
            ctx = await self.get_context(message)
            await ctx.send("❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
            return

        ctx = await self.get_context(message)
        if len(parts) != 2:
            usage = "-بوت @العضو" if command_name == "-بوت" else "-الغاء بوت @العضو"
            await ctx.send(f"❌ الاستعمال: `{usage}` أو ID", delete_after=8)
            return

        member = await cog._resolve_member(ctx, target_text)
        if member is None:
            await ctx.send("❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
            return

        if command_name == "-بوت":
            await cog._delegate(ctx, member)
        else:
            await cog._undelegate(ctx, member)
        return

    await _original_process_commands(self, message)


if commands.Bot.process_commands is not _process_commands_with_bot_delegation:
    commands.Bot.process_commands = _process_commands_with_bot_delegation
