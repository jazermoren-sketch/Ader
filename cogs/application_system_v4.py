"""Compatibility layer for the administration application system.

The actual /تقديم command lives in application_system_v3. This module must not
register another copy of the command, otherwise discord.py raises
CommandAlreadyRegistered.
"""
from .application_system_v3 import App

AppV4 = App

async def setup(bot):
    # Intentionally empty: application_system_v3 owns /تقديم.
    return
