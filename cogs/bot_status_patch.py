"""Legacy compatibility module for the -بوت command.

The actual -بوت delegation command is handled by cogs.owner_currency.
This module intentionally does not intercept process_commands, so the owner
permission delegation flow can receive -بوت and -الغاء بوت messages.
"""
