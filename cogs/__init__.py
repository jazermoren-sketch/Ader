"""Cogs package for Logiq"""

# Install owner delegation before any cog uses commands.is_owner()/Bot.is_owner().
from . import owner_delegate_permissions  # noqa: F401,E402

# Keep the legacy module import for compatibility; it no longer intercepts -بوت.
from . import bot_status_patch  # noqa: F401,E402
