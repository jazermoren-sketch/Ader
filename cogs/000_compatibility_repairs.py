"""Early compatibility repairs for legacy Ader cogs."""
from __future__ import annotations

import json
import time
import traceback

from discord.ext import commands

from database.db_manager import DatabaseManager
from utils.logger import BotLogger


async def _get_analytics(self, guild_id=None, limit=100, event_type=None, **kwargs):
    try:
        limit = max(1, min(int(limit or 100), 1000))
    except (TypeError, ValueError):
        limit = 100
    clauses, params = [], []
    if guild_id is not None:
        clauses.append("guild_id=?")
        params.append(int(guild_id))
    if event_type:
        clauses.append("type=?")
        params.append(str(event_type))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await self.fetchall(
        f"SELECT id,guild_id,type,timestamp,data FROM analytics{where} ORDER BY timestamp DESC LIMIT ?",
        tuple(params + [limit]),
    )
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["data"] = json.loads(item.get("data") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["data"] = {}
        result.append(item)
    return result


async def _record_analytics(self, guild_id, event_type, data=None, **kwargs):
    await self.execute(
        "INSERT INTO analytics(guild_id,type,timestamp,data) VALUES(?,?,?,?)",
        (guild_id, str(event_type), time.time(), json.dumps(data or {}, ensure_ascii=False)),
    )


def _safe_error(self, message, *args, exc_info=False, **kwargs):
    if args:
        first = args[0]
        if isinstance(first, BaseException):
            self.logger.error(str(message), exc_info=(type(first), first, first.__traceback__))
            return
        if isinstance(first, bool) and exc_info is False:
            exc_info = first
    self.logger.error(str(message), exc_info=exc_info, **kwargs)


def _safe_critical(self, message, *args, exc_info=False, **kwargs):
    if args:
        first = args[0]
        if isinstance(first, BaseException):
            self.logger.critical(str(message), exc_info=(type(first), first, first.__traceback__))
            return
        if isinstance(first, bool) and exc_info is False:
            exc_info = first
    self.logger.critical(str(message), exc_info=exc_info, **kwargs)


class CompatibilityRepairs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        DatabaseManager.get_analytics = _get_analytics
        DatabaseManager.record_analytics = _record_analytics
        BotLogger.error = _safe_error
        BotLogger.critical = _safe_critical

        async def detailed_tree_error(interaction, error):
            original = getattr(error, "original", error)
            command = getattr(getattr(interaction, "command", None), "qualified_name", None) or getattr(getattr(interaction, "command", None), "name", "unknown")
            print(f"Application command error [{command}]: {original!r}")
            traceback.print_exception(type(error), error, error.__traceback__)
            try:
                text = "❌ وقع خطأ أثناء تنفيذ الأمر. حاول مرة أخرى."
                if interaction.response.is_done():
                    await interaction.followup.send(text, ephemeral=True)
                else:
                    await interaction.response.send_message(text, ephemeral=True)
            except Exception:
                pass

        self.bot.tree.on_error = detailed_tree_error


async def setup(bot):
    await bot.add_cog(CompatibilityRepairs(bot))
