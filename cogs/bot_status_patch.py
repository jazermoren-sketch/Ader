"""Legacy compatibility patch for the -بوت command.

The real ``-بوت`` command is an owner-delegation command handled by
``owner_delegate_permissions`` / ``OwnerCurrency``. The old utility status
handler must not answer to the bare ``-بوت`` message, otherwise it races with
or masks the delegation flow.
"""
from __future__ import annotations

from functools import wraps

from .utility import Utility


_original_utility_on_message = Utility.on_message


@wraps(_original_utility_on_message)
async def _utility_on_message_without_bot_status(self, message):
    if message.guild is not None and message.content.strip() == "-بوت":
        return
    await _original_utility_on_message(self, message)


Utility.on_message = _utility_on_message_without_bot_status
