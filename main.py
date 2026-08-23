"""Ader Ultimate Discord bot entry point."""
import asyncio
import os
from pathlib import Path
from datetime import timedelta

import discord
from discord.ext import commands
import yaml
from dotenv import load_dotenv

from database.db_manager import DatabaseManager
from utils.logger import BotLogger

load_dotenv()
discord.timedelta = timedelta

try:
    import fcntl
except ImportError:
    fcntl = None

class Ader(commands.Bot):
    TARGET_GUILD_ID = 1490355290116194388

    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        configured_prefix = config.get("bot", {}).get("prefix", "!")
        prefixes = [configured_prefix] if isinstance(configured_prefix, str) else [str(p) for p in configured_prefix] if isinstance(configured_prefix, (list, tuple)) else ["!"]
        if "!" not in prefixes:
            prefixes.append("!")
        prefixes = list(dict.fromkeys(prefixes))
        if "$" not in prefixes:
            prefixes.append("$")
        super().__init__(command_prefix=prefixes, intents=intents, help_command=None)
        self.config = config
        self.start_time = discord.utils.utcnow()
        self.logger = BotLogger(config.get("logging", {}))
        # Hosting providers commonly replace the checkout on deploy.  Keep the
        # database outside it whenever ADER_DATA_DIR is supplied.
        configured_db = Path(config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))
        data_dir = os.getenv("ADER_DATA_DIR", "").strip()
        db_path = Path(data_dir) / configured_db.name if data_dir else configured_db
        self.db = DatabaseManager(str(db_path))
        self._instance_lock_handle = None
        self._ready_sync_done = False
        self.tree.on_error = self._tree_error

    def _acquire_instance_lock(self):
        if fcntl is None:
            return
        lock_path = Path(self.config.get("database", {}).get("instance_lock", "data/ader.instance.lock"))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError("Another Ader process is already running with this installation.") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._instance_lock_handle = handle

    def _release_instance_lock(self):
        if self._instance_lock_handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._instance_lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._instance_lock_handle.close()
            self._instance_lock_handle = None

    async def setup_hook(self):
        self._acquire_instance_lock()
        await self.db.connect()
        await self.load_cogs()

    async def load_cogs(self):
        # This explicit release manifest is the single source of truth.  Do not
        # discover files alphabetically: that used to load hotfixes and duplicate
        # implementations beside their canonical cogs.
        extensions = (
            "cogs.admin", "cogs.analytics", "cogs.economy", "cogs.shop",
            "cogs.advertising_shop", "cogs.ad_customization", "cogs.shortcuts",
            "cogs.moderation", "cogs.roles", "cogs.ticket_manager", "cogs.utility",
            "cogs.verification", "cogs.games", "cogs.teams_v2",
            "cogs.temp_voice", "cogs.dashboard_config", "cogs.dashboard_server",
        )
        loaded, failed = [], []
        for extension in extensions:
            try:
                await self.load_extension(extension)
                loaded.append(extension)
            except Exception:
                failed.append(extension)
                self.logger.error(f"Failed to load cog {extension}", exc_info=True)
        self.logger.info("Loaded cogs (%d): %s", len(loaded), ", ".join(loaded))
        if failed:
            self.logger.error("Disabled after load failures (%d): %s", len(failed), ", ".join(failed))

    async def _dashboard_allowed(self, guild_id, command_name, user, channel_id=None):
        try:
            cog = self.get_cog("DashboardConfig")
            if cog:
                return await cog.allowed(guild_id, command_name, user, channel_id)
            return True, ""
        except Exception as exc:
            self.logger.error(f"Dashboard rule check failed: {exc}")
            return True, ""

    async def _dashboard_deny(self, interaction, text):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ {text}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ {text}", ephemeral=True)
        except Exception:
            pass

    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.guild is not None and interaction.type is discord.InteractionType.application_command:
            command = getattr(interaction, "command", None)
            if command is not None:
                name = getattr(command, "qualified_name", None) or getattr(command, "name", "")
                allowed, reason = await self._dashboard_allowed(interaction.guild.id, name, interaction.user, getattr(interaction.channel, "id", None))
                if not allowed:
                    await self._dashboard_deny(interaction, reason)
                    return
        await super().on_interaction(interaction)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        await self.process_commands(message)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        self.logger.error(f"Prefix command error command={getattr(ctx.command, 'qualified_name', 'unknown')} guild={getattr(ctx.guild, 'id', None)} user={ctx.author.id}", exc_info=(type(error), error, error.__traceback__))
        if not isinstance(error, commands.CommandNotFound):
            await ctx.send("❌ وقع خطأ أثناء تنفيذ الأمر. تم تسجيل التفاصيل.", delete_after=8)

    async def _tree_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        command = getattr(getattr(interaction, "command", None), "qualified_name", "unknown")
        self.logger.error(f"Application command error command={command} guild={interaction.guild_id} user={interaction.user.id}", exc_info=(type(error), error, error.__traceback__))
        text = "❌ وقع خطأ أثناء تنفيذ الأمر. تم تسجيل التفاصيل."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def on_ready(self):
        types = {"playing": discord.ActivityType.playing, "watching": discord.ActivityType.watching, "listening": discord.ActivityType.listening, "streaming": discord.ActivityType.streaming}
        typ = self.config.get("bot", {}).get("activity_type", "watching")
        text = self.config.get("bot", {}).get("activity", "مجتمعك")
        await self.change_presence(activity=discord.Activity(type=types.get(typ, discord.ActivityType.watching), name=text))
        if self._ready_sync_done:
            return
        try:
            global_synced = await self.tree.sync()
            self.logger.info(f"Synced {len(global_synced)} global application commands")
            guild = self.get_guild(self.TARGET_GUILD_ID)
            if guild is not None:
                target = discord.Object(id=self.TARGET_GUILD_ID)
                self.tree.copy_global_to(guild=target)
                guild_synced = await self.tree.sync(guild=target)
                self.logger.info(f"Synced {len(guild_synced)} application commands to target guild {self.TARGET_GUILD_ID}")
            else:
                self.logger.warning(f"Target guild {self.TARGET_GUILD_ID} is not visible to the bot")
            self._ready_sync_done = True
            self.logger.info(f"Ader ready as {self.user} in {len(self.guilds)} guilds")
        except Exception as exc:
            self.logger.error(f"Command sync failed: {exc}", exc_info=True)

    async def close(self):
        try:
            await self.db.disconnect()
        finally:
            self._release_instance_lock()
            await super().close()


def _get_discord_token(config):
    token = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
    if not token:
        configured = str(config.get("bot", {}).get("token", "") or "").strip()
        token = os.getenv(configured[2:-1].strip(), "") if configured.startswith("${") and configured.endswith("}") else configured
    token = (token or "").strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    if not token or token.startswith("${"):
        raise RuntimeError("Discord bot token is not configured. Set DISCORD_BOT_TOKEN in the hosting panel.")
    return token


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def main():
    config = load_config()
    token = _get_discord_token(config)
    await Ader(config).start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
