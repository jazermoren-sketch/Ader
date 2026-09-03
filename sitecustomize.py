"""Runtime compatibility shim for Ader's pinned discord.py version.

This is intentionally tiny: discord.py 2.4 rejects a float Range when its
minimum and maximum literals have different concrete numeric types. Ader has
one legacy annotation using ``Range[float, 0, 25]``. Normalize only numeric
bounds for float ranges before discord.py validates them.
"""

try:
    from discord import app_commands

    _Range = app_commands.Range
    _original_getitem = _Range.__class_getitem__

    def _ader_range_getitem(cls, params):
        if isinstance(params, tuple) and len(params) == 3:
            value_type, minimum, maximum = params
            if value_type is float:
                if isinstance(minimum, int) and not isinstance(minimum, bool):
                    minimum = float(minimum)
                if isinstance(maximum, int) and not isinstance(maximum, bool):
                    maximum = float(maximum)
                params = (value_type, minimum, maximum)
        return _original_getitem(params)

    _Range.__class_getitem__ = classmethod(_ader_range_getitem)
except Exception:
    # Never prevent the bot from starting if discord.py changes its internals.
    pass
