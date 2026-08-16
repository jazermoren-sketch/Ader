"""Ader Ultimate Discord bot entry point."""
import asyncio
import os
import sys
import time
from pathlib import Path
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands
import yaml
from dotenv import load_dotenv

from database.db_manager import DatabaseManager
from utils.logger import BotLogger

load_dotenv()
discord.timedelta = timedelta

_original_context_send = commands.Context.send


async def _context_send(self, *args, **kwargs):
    content = getattr(getattr(self, "message", None), "content", "") or ""
    if content.strip().startswith("!") and getattr(self, "message", None) is not None:
        kwargs.setdefault("mention_author", False)
        kwargs.setdefault("reference", self.message)
        return await _original_context_send(self, *args, **kwargs)
    return await _original_context_send(self, *args, **kwargs)


commands.Context.send = _context_send


class Ader(commands.Bot):
    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        super().__init__(
            command_prefix=config.get("bot", {}).get("prefix", "/"),
            intents=intents,
            help_command=None,
        )
        self.config = config
        self.start_time = discord.utils.utcnow()
        self.logger = BotLogger(config.get("logging", {}))
        self.db = DatabaseManager(config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))

    async def setup_hook(self):
        await self.db.connect()
        await self.load_cogs()

    async def load_cogs(self):
        directory = Path(__file__).parent / "cogs"
        loaded = 0
        disabled = {
            "application_system.py",
            "application_system_v2.py",
            "application_system_v3.py",
            "leveling.py",
        }

        for path in sorted(directory.glob("*.py")):
            if path.stem.startswith("_") or path.stem == "__init__" or path.name in disabled:
                continue
            try:
                if path.stem == "application_system_v4":
                    self.tree.remove_command("تقديم")
                elif path.stem == "ultimate_system":
                    for command_name in ("warn", "warnings", "ban"):
                        self.tree.remove_command(command_name)
                await self.load_extension(f"cogs.{path.stem}")
                loaded += 1
            except Exception as exc:
                self.logger.error(f"Failed to load cog {path.stem}: {exc}", exc_info=True)

        self._remove_all_legacy_ticket_commands()
        self._replace_owner_give_command()
        self.logger.info(f"Loaded {loaded} cogs")

    def _remove_all_legacy_ticket_commands(self):
        """Keep exactly /ticket; remove every other ticket-named application command."""
        for command in list(self.tree.get_commands()):
            name = str(command.name).lower()
            if name != "ticket" and ("ticket" in name or name in {"tickets", "close-ticket", "delete-ticket"}):
                self.tree.remove_command(command.name)

    def _replace_owner_give_command(self):
        """Register exactly one idempotent owner-only !اعطي command."""
        while self.get_command("اعطي") is not None:
            self.remove_command("اعطي")

        @self.command(name="اعطي", help="Owner-only ANOCoin grant")
        async def owner_give(ctx: commands.Context, member: discord.Member | None = None, amount: int | None = None):
            owner_id = 1472570059367911587
            if ctx.author.id != owner_id:
                return
            if member is None or amount is None or amount <= 0 or member.bot or ctx.guild is None:
                return await ctx.send("❌ الاستعمال: `!اعطي @user المبلغ`", delete_after=6)

            # The message ID is the idempotency key. This prevents the same Discord
            # message from crediting the recipient twice, even if the handler is
            # accidentally invoked more than once against the same SQLite DB.
            key = f"owner-give:{ctx.message.id}"
            cur = await self.db.execute(
                "INSERT OR IGNORE INTO processed_commands(command_key, created_at) VALUES(?, ?)",
                (key, time.time()),
            )
            if cur.rowcount != 1:
                return await ctx.send("⚠️ هاد الأمر راه تعالج من قبل.", delete_after=6)

            ok = await self.db.add_balance(member.id, ctx.guild.id, amount)
            if not ok:
                return await ctx.send("❌ تعذر إضافة العملة. عاود المحاولة.", delete_after=6)
            new_balance = await self.db.get_balance(member.id)
            await ctx.send(
                f"🪙 تم إعطاء {member.mention} **{amount:,} ANOCoin**. "
                f"الرصيد الجديد: **{new_balance:,} ANOCoin**."
            )

    async def on_ready(self):
        types = {
            "playing": discord.ActivityType.playing,
            "watching": discord.ActivityType.watching,
            "listening": discord.ActivityType.listening,
            "streaming": discord.ActivityType.streaming,
        }
        typ = self.config.get("bot", {}).get("activity_type", "watching")
        text = self.config.get("bot", {}).get("activity", "مجتمعك")
        await self.change_presence(activity=discord.Activity(type=types.get(typ, discord.ActivityType.watching), name=text))

        try:
            synced = await self.tree.sync()
            self.logger.info(f"Synced {len(synced)} global application commands")
            for guild in self.guilds:
                try:
                    self.tree.clear_commands(guild=guild)
                    await self.tree.sync(guild=guild)
                    self.logger.info(f"Cleared legacy guild-scoped commands in {guild.id}")
                except Exception as exc:
                    self.logger.error(f"Guild command cleanup failed for {guild.id}: {exc}")
            self.logger.info(f"Ader ready as {self.user} in {len(self.guilds)} guilds")
        except Exception as exc:
            self.logger.error(f"Command sync failed: {exc}", exc_info=True)

    async def close(self):
        try:
            await self.db.disconnect()
        finally:
            await super().close()


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def main():
    config = load_config()
    token = os.getenv("DISCORD_TOKEN") or config.get("bot", {}).get("token")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not configured")
    bot = Ader(config)
    await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
