"""Ader owner-currency loader with global message dispatch deduplication."""

from .owner_currency_impl import OwnerCurrency


async def setup(bot):
    """Install the owner-currency cog and guard message events against duplicates."""
    if not getattr(bot, "_ader_message_dispatch_dedupe_installed", False):
        original_dispatch = bot.dispatch
        seen_message_ids: dict[int, None] = {}

        def deduplicating_dispatch(event_name, *args, **kwargs):
            if event_name == "message" and args:
                message = args[0]
                message_id = getattr(message, "id", None)
                if message_id is not None:
                    if message_id in seen_message_ids:
                        return
                    seen_message_ids[message_id] = None
                    if len(seen_message_ids) > 8192:
                        seen_message_ids.pop(next(iter(seen_message_ids)))
            return original_dispatch(event_name, *args, **kwargs)

        bot.dispatch = deduplicating_dispatch
        bot._ader_message_dispatch_dedupe_installed = True
        bot._ader_seen_message_ids = seen_message_ids

    await bot.add_cog(OwnerCurrency(bot, bot.db, bot.config))
