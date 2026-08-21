"""Final release-time UI compatibility repairs.

This loads after the older runtime repair layer and only normalizes the ad
settings component layout. It avoids changing business logic.
"""
from __future__ import annotations

import discord
from discord.ext import commands


class ReleaseUIRepairs(commands.Cog):
    async def cog_load(self):
        try:
            from cogs import ad_customization as customization
            from cogs import zzzz_runtime_repairs as runtime_repairs
        except Exception:
            return

        # The legacy runtime repair created ReplyTargetButton on row 3, which
        # already contains a full-width RoleSelect. Move it to row 4 and make
        # room by removing the redundant Toggle button.
        def reply_button_init(button, cog):
            discord.ui.Button.__init__(
                button,
                label="Reply",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                row=4,
            )
            button.cog = cog

        runtime_repairs.ReplyTargetButton.__init__ = reply_button_init

        current_init = customization.SettingsView.__init__
        original_init = None
        closure = getattr(current_init, "__closure__", None) or ()
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if callable(value) and getattr(value, "__name__", "") == "__init__":
                original_init = value
                break

        if original_init is None:
            return

        def safe_settings_init(view_self, cog, rows):
            original_init(view_self, cog, rows)

            # Remove the old five-row action that is not essential. This keeps
            # the final action row at exactly five components after Reply is
            # added: Add / Edit / Delete / Reply / Giveaway.
            toggle = next(
                (
                    child
                    for child in view_self.children
                    if isinstance(child, discord.ui.Button)
                    and str(getattr(child, "label", "")) == "تفعيل"
                ),
                None,
            )
            if toggle is not None:
                view_self.remove_item(toggle)

            view_self.add_item(runtime_repairs.ReplyTargetButton(cog))

        customization.SettingsView.__init__ = safe_settings_init


async def setup(bot: commands.Bot):
    await bot.add_cog(ReleaseUIRepairs())
