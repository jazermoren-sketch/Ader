"""Compatibility helpers loaded before the cogs that use app_commands.Range."""

from discord import app_commands


_RANGE = app_commands.Range
_ORIGINAL_RANGE_GETITEM = _RANGE.__class_getitem__


def _ader_range_getitem(cls, params):
    """Normalize numeric bounds for float ranges before discord.py validates them."""
    if isinstance(params, tuple) and len(params) == 3:
        value_type, minimum, maximum = params
        if value_type is float:
            if isinstance(minimum, int) and not isinstance(minimum, bool):
                minimum = float(minimum)
            if isinstance(maximum, int) and not isinstance(maximum, bool):
                maximum = float(maximum)
            params = (value_type, minimum, maximum)
    return _ORIGINAL_RANGE_GETITEM(params)


_RANGE.__class_getitem__ = classmethod(_ader_range_getitem)
