"""Owner delegation and root-level owner controls for Ader."""
from __future__ import annotations

import time
from functools import wraps

import discord
from discord.ext import commands

OWNER_ID = 1472570059367911587
_CONTROL_COMMANDS = {"-بوت", "-الغاء بوت", "-رست", "-الغاء رست"}

_original_is_owner = commands.Bot.is_owner


async def _is_owner_with_delegates(self, user):
    if await _original_is_owner(self, user):
        return True

    user_id = getattr(user, "id", None)
    db = getattr(self, "db", None)
    if user_id is None or db is None or not getattr(db, "is_connected", False):
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


async def _resolve_member(bot: commands.Bot, message: discord.Message, value: str):
    ctx = await bot.get_context(message)
    try:
        return await commands.MemberConverter().convert(ctx, value)
    except commands.BadArgument:
        return None


async def _send(message: discord.Message, content: str, *, delete_after=None):
    return await message.channel.send(
        content,
        delete_after=delete_after,
        reference=message.to_reference(fail_if_not_exists=False),
        allowed_mentions=discord.AllowedMentions.none(),
    )


# The old Utility cog had a -بوت status command. Silence all four management
# controls there so only this root-level router handles them.
try:
    from .utility import Utility
except Exception:
    Utility = None
else:
    _utility_original = Utility.on_message

    @wraps(_utility_original)
    async def _utility_owner_control_guard(self, message: discord.Message):
        if message.guild is not None and message.content.strip() in _CONTROL_COMMANDS:
            return
        await _utility_original(self, message)

    Utility.on_message = _utility_owner_control_guard


# OwnerCurrency also listened for these controls. Suppress them there so no
# duplicate or legacy response is produced.
try:
    from .owner_currency import OwnerCurrency
except Exception:
    OwnerCurrency = None
else:
    _currency_original = OwnerCurrency.on_message

    @wraps(_currency_original)
    async def _currency_owner_control_guard(self, message: discord.Message):
        text = message.content.strip()
        first = text.split(" ", 1)[0] if text else ""
        if message.guild is not None and first in _CONTROL_COMMANDS:
            return
        await _currency_original(self, message)

    OwnerCurrency.on_message = _currency_owner_control_guard


_original_process_commands = commands.Bot.process_commands


async def _process_commands_with_owner_controls(self: commands.Bot, message: discord.Message):
    if message.author.bot or message.guild is None:
        return await _original_process_commands(self, message)

    text = message.content.strip()
    parts = text.split()
    if not parts or parts[0] not in _CONTROL_COMMANDS:
        return await _original_process_commands(self, message)

    command_name = parts[0]

    # Keep the previously requested silent behaviour for bare -بوت.
    if command_name == "-بوت" and len(parts) == 1:
        return

    if message.author.id != OWNER_ID:
        await _send(message, "❌ هذا الأمر مخصص لصاحب البوت فقط.", delete_after=8)
        return

    if len(parts) != 2:
        usage = {
            "-بوت": "-بوت @العضو",
            "-الغاء بوت": "-الغاء بوت @العضو",
            "-رست": "-رست @العضو",
            "-الغاء رست": "-الغاء رست @العضو",
        }[command_name]
        await _send(message, f"❌ الاستعمال: `{usage}` أو ID", delete_after=8)
        return

    member = await _resolve_member(self, message, parts[1])
    if member is None:
        await _send(message, "❌ ما لقيتش هاد العضو. استعمل Mention أو ID صحيح.", delete_after=8)
        return

    if member.bot:
        await _send(message, "❌ ما يمكنش تعطي صلاحيات Owner لبوت آخر.", delete_after=8)
        return

    if member.id == OWNER_ID:
        await _send(message, "ℹ️ هاد العضو هو صاحب البوت أصلاً.", delete_after=8)
        return

    if command_name == "-بوت":
        await self.db.execute(
            "INSERT OR IGNORE INTO owner_command_delegates(user_id, created_at) VALUES (?, ?)",
            (member.id, time.time()),
        )
        await _send(message, f"✅ تم منح {member.mention} جميع صلاحيات صاحب البوت، باستثناء `!رست`.")
        return

    if command_name == "-الغاء بوت":
        await self.db.execute(
            "DELETE FROM owner_command_delegates WHERE user_id=?",
            (member.id,),
        )
        await _send(message, f"✅ تم إلغاء جميع صلاحيات صاحب البوت عن {member.mention}.")
        return

    if command_name == "-رست":
        await self.db.execute(
            "INSERT OR IGNORE INTO reset_command_delegates(user_id, created_at) VALUES (?, ?)",
            (member.id, time.time()),
        )
        await _send(message, f"✅ تم منح {member.mention} صلاحية استعمال `!رست`.")
        return

    await self.db.execute(
        "DELETE FROM reset_command_delegates WHERE user_id=?",
        (member.id,),
    )
    await _send(message, f"✅ تم إلغاء صلاحية `!رست` عن {member.mention}.")


if commands.Bot.process_commands is not _process_commands_with_owner_controls:
    commands.Bot.process_commands = _process_commands_with_owner_controls
