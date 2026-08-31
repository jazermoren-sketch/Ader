"""Ader Ultimate Discord bot entry point."""
import asyncio
import math
import os
import time
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
        prefixes = [p for p in prefixes if p.strip().lower() != "a"]
        if "!" not in prefixes:
            prefixes.append("!")
        prefixes = list(dict.fromkeys(prefixes))
        if "$" not in prefixes:
            prefixes.append("$")
        super().__init__(command_prefix=prefixes, intents=intents, help_command=None)
        self.config = config
        self.start_time = discord.utils.utcnow()
        self.logger = BotLogger(config.get("logging", {}))
        configured_db = Path(config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))
        data_dir = os.getenv("ADER_DATA_DIR", "").strip()
        db_path = Path(data_dir) / configured_db.name if data_dir else configured_db
        self.db = DatabaseManager(str(db_path))
        self._instance_lock_handle = None
        self._ready_sync_done = False
        self._processed_message_count = 0
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
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS processed_messages(
                message_id INTEGER PRIMARY KEY,
                created_at REAL NOT NULL
            )"""
        )
        await self.db.execute(
            "DELETE FROM processed_messages WHERE created_at < ?",
            (time.time() - 7 * 24 * 60 * 60,),
        )
        await self.load_cogs()

    async def _claim_message_once(self, message_id: int) -> bool:
        """Atomically claim a Discord message before any prefix handler runs.

        Discord can deliver the same user message to more than one running
        bot process when an old/restarted instance overlaps with the current
        instance.  The SQLite primary key makes the claim global for all Ader
        processes sharing the configured database, so only one process can
        produce a response or execute a prefix command for that message.
        """
        try:
            cursor = await self.db.execute(
                "INSERT OR IGNORE INTO processed_messages(message_id, created_at) VALUES(?, ?)",
                (int(message_id), time.time()),
            )
            claimed = cursor.rowcount == 1
            try:
                await cursor.close()
            except Exception:
                pass

            if claimed:
                self._processed_message_count += 1
                if self._processed_message_count % 1000 == 0:
                    await self.db.execute(
                        "DELETE FROM processed_messages WHERE created_at < ?",
                        (time.time() - 7 * 24 * 60 * 60,),
                    )
            return claimed
        except Exception as exc:
            # A dedupe failure must not silently disable the whole bot.  Log it
            # and process the message normally; command-level safeguards remain.
            self.logger.error(f"Message dedupe check failed for {message_id}: {exc}")
            return True

    async def load_cogs(self):
        extensions = (
            "cogs.admin", "cogs.analytics", "cogs.economy", "cogs.shop",
            "cogs.advertising_shop", "cogs.ad_room_command", "cogs.ad_customization", "cogs.shortcuts",
            "cogs.moderation", "cogs.roles", "cogs.ticket_manager", "cogs.utility",
            "cogs.verification", "cogs.games", "cogs.teams_v2",
            "cogs.temp_voice", "cogs.dashboard_config", "cogs.dashboard_server",
            "cogs.owner_currency", "cogs.member_currency_reset",
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
                owner_currency = self.get_cog("OwnerCurrency")
                command_cog = getattr(command, "binding", None)
                if owner_currency is not None and getattr(command_cog, "__class__", None) is not None and command_cog.__class__.__name__ == "Economy":
                    if await owner_currency._is_blacklisted(interaction.user.id):
                        await self._dashboard_deny(interaction, "أنت في بلاك ليست العملة، ولا يمكنك استعمال نظام العملة.")
                        return
                allowed, reason = await self._dashboard_allowed(interaction.guild.id, name, interaction.user, getattr(interaction.channel, "id", None))
                if not allowed:
                    await self._dashboard_deny(interaction, reason)
                    return
        await super().on_interaction(interaction)

    async def _handle_a_message(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        raw = message.content.strip()
        parts = raw.split()
        if not parts or parts[0].lower() != "a":
            return False
        if len(parts) > 3:
            await message.channel.send("❌ الاستعمال: `A` أو `A @العضو` أو `A @العضو المبلغ`", delete_after=8)
            return True
        economy = self.get_cog("Economy")
        if economy is None:
            await message.channel.send("❌ نظام الاقتصاد غير متوفر حالياً.", delete_after=8)
            return True
        owner_currency = self.get_cog("OwnerCurrency")
        if owner_currency is not None and await owner_currency._is_blacklisted(message.author.id):
            await message.channel.send("❌ أنت في بلاك ليست العملة، ولا يمكنك استعمال نظام العملة.", delete_after=8)
            return True
        mentions = list(message.mentions)
        if len(mentions) > 1:
            await message.channel.send("❌ يرجى تحديد عضو واحد فقط.", delete_after=8)
            return True
        if len(mentions) == 0:
            if len(parts) == 1:
                balance = await self.db.get_balance(message.author.id)
                await message.channel.send(embed=discord.Embed(title="🪙 رصيدك", description=f"رصيدك الحالي: **{balance:,} ANORIS**", colour=discord.Colour.gold()))
            else:
                await message.channel.send("❌ الاستعمال: `A` أو `A @العضو` أو `A @العضو المبلغ`", delete_after=8)
            return True

        member = mentions[0]
        amount = None
        if len(parts) == 3:
            try:
                amount = int(parts[-1].replace(",", ""))
            except ValueError:
                await message.channel.send("❌ المبلغ يجب أن يكون رقماً صحيحاً.", delete_after=8)
                return True
        if amount is None:
            balance = await self.db.get_balance(member.id)
            await message.channel.send(embed=discord.Embed(title=f"🪙 رصيد {member.display_name}", description=f"رصيد {member.mention}: **{balance:,} ANORIS**", colour=discord.Colour.gold()))
            return True
        if amount <= 0 or member.bot or member.id == message.author.id:
            await message.channel.send("❌ يجب تحديد مبلغ موجب وعضو آخر غير البوتات.", delete_after=8)
            return True
        balance = await self.db.get_balance(message.author.id)
        fee = max(1, math.ceil(amount * 0.05))
        total = amount + fee
        if balance < total:
            await message.channel.send(f"❌ رصيدك غير كافٍ. تحتاج **{total:,} ANORIS** ورصيدك الحالي **{balance:,} ANORIS**.", delete_after=10)
            return True
        confirmed = await economy._confirm(message.channel, message.author, message.guild.id, "التحويل")
        if not confirmed:
            return True
        ok, text = await economy._transfer_amount(message.guild, message.author, member, amount)
        await message.channel.send(text if ok else text, delete_after=None if ok else 10)
        return True

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not await self._claim_message_once(message.id):
            return
        if await self._handle_a_message(message):
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
        except Exception:
            self.logger.error("Command synchronization failed", exc_info=True)

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
    bot = Ader(config)
    token = _get_discord_token(config)
    try:
        await bot.start(token)
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
