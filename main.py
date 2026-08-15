"""Ader Ultimate Discord bot entry point."""
import asyncio
import os
import sys
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
        self.logger.info(f"Loaded {loaded} cogs")

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
            # Global sync keeps the official command list correct.
            synced = await self.tree.sync()
            self.logger.info(f"Synced {len(synced)} global application commands")

            # Also sync each connected guild immediately. This prevents commands
            # such as /اختصارات from waiting for Discord's global command cache.
            for guild in self.guilds:
                try:
                    self.tree.copy_global_to(guild=guild)
                    guild_synced = await self.tree.sync(guild=guild)
                    self.logger.info(f"Synced {len(guild_synced)} commands in guild {guild.id}")
                except discord.HTTPException as exc:
                    self.logger.error(f"Guild command sync failed for {guild.id}: {exc}")
        except Exception as exc:
            self.logger.error(f"Command sync failed: {exc}", exc_info=True)

        self.logger.info(f"Ader ready as {self.user} in {len(self.guilds)} guilds")

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(original, app_commands.MissingPermissions):
            message = "❌ ما عندكش الصلاحيات المطلوبة لاستعمال هاد الأمر."
        elif isinstance(original, app_commands.CheckFailure):
            message = "❌ ما عندكش الصلاحية أو الشروط المطلوبة لاستعمال هاد الأمر."
        elif isinstance(original, discord.Forbidden):
            message = "❌ البوت ما عندوش الصلاحيات أو Access الكافي لتنفيذ هاد الأمر."
        elif isinstance(original, discord.HTTPException) and getattr(original, "code", None) == 350005:
            message = "❌ Discord رفض العملية بسبب Server Onboarding. خاص يبقى على الأقل روم واحد @everyone يقدر يقرا فيه ويرسل الرسائل."
        else:
            message = "❌ وقع خطأ أثناء تنفيذ الأمر. حاول مرة أخرى."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception as exc:
            self.logger.error(f"Could not acknowledge application command error: {exc}", exc_info=True)
        self.logger.error(f"Application command error: {original}", exc_info=True)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        try:
            if isinstance(error, commands.MissingPermissions):
                await ctx.send("❌ ليس لديك الصلاحية لاستخدام هذا الأمر.", delete_after=6)
                return
            if isinstance(error, commands.MissingRequiredArgument):
                await ctx.send(f"❌ المعطى المطلوب ناقص: `{error.param.name}`", delete_after=6)
                return
            if isinstance(error, commands.BotMissingPermissions):
                await ctx.send("❌ البوت ما عندوش الصلاحيات المطلوبة.", delete_after=6)
                return
            if isinstance(error, commands.CheckFailure):
                await ctx.send("❌ ما عندكش الشروط المطلوبة لاستعمال هاد الأمر.", delete_after=6)
                return
        except discord.HTTPException:
            pass
        self.logger.error(f"Command error: {error}", exc_info=True)

    async def on_error(self, event, *args, **kwargs):
        self.logger.error(f"Event error: {event}", exc_info=True)

    async def close(self):
        await self.db.disconnect()
        await super().close()


def load_config(path="config.yaml"):
    try:
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"Config error: {exc}")
        sys.exit(1)

    def replace(v):
        if isinstance(v, dict):
            return {k: replace(x) for k, x in v.items()}
        if isinstance(v, list):
            return [replace(x) for x in v]
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            return os.getenv(v[2:-1], v)
        return v

    return replace(config)


async def main():
    config = load_config()
    token = os.getenv("DISCORD_BOT_TOKEN") or config.get("bot", {}).get("token")
    if not token or token.startswith("${"):
        print("Error: DISCORD_BOT_TOKEN is not configured")
        sys.exit(1)
    bot = Ader(config)
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
