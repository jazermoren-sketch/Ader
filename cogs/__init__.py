"""Cogs package for Logiq"""

# Normalize legacy app_commands.Range bounds before any cog annotations are evaluated.
from . import range_compat  # noqa: F401,E402

# Install owner delegation before any cog uses commands.is_owner()/Bot.is_owner().
from . import owner_delegate_permissions  # noqa: F401,E402

# Keep the legacy module import for compatibility; it no longer intercepts -بوت.
from . import bot_status_patch  # noqa: F401,E402
