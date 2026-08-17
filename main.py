"""Ader Ultimate Discord bot entry point."""
import asyncio
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

_original_context_send = commands.Context.send

async def _context_send(self, *args, **kwargs):
    content = getattr(getattr(self, "message", None), "content", "") or ""
    if content.strip().startswith("!") and getattr(self, "message", None) is not None:
        kwargs.setdefault("mention_author", False)
        kwargs.setdefault("reference", self.message)
    return await _original_context_send(self, *args, **kwargs)

commands.Context.send = _context_send

class Ader(commands.Bot):
    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        super().__init__(command_prefix=config.get("bot", {}).get("prefix", "/"), intents=intents, help_command=None)
        self.config = config
        self.start_time = discord.utils.utcnow()
        self.logger = BotLogger(config.get("logging", {}))
        self.db = DatabaseManager(config.get("database", {}).get("sqlite_path", "data/ader.sqlite3"))
        self._owner_give_lock = asyncio.Lock()
        self._owner_give_processed: set[int] = set()

    async def setup_hook(self):
        await self.db.connect()
        await self.load_cogs()

    async def load_cogs(self):
        directory = Path(__file__).parent / "cogs"
        loaded = 0
        disabled = {
            "application_system.py", "application_system_v2.py", "application_system_v3.py",
            "leveling.py", "tickets.py", "tournament_delete.py", "teams.py",
            # Legacy dashboard launcher. dashboard_server.py is now the single
            # canonical dashboard server; loading both would make two Uvicorn
            # instances bind to the same port and cause [Errno 98] EADDRINUSE.
            "web_dashboard.py",
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
        self._remove_legacy_ticket_commands()
        self._replace_owner_give_command()
        self.logger.info(f"Loaded {loaded} cogs")

    def _remove_legacy_ticket_commands(self):
        for command in list(self.tree.get_commands()):
            name = str(command.name).lower()
            if name != "ticket" and "ticket" in name:
                self.tree.remove_command(command.name)

    async def _dashboard_allowed(self, guild_id: int, command_name: str, user, channel_id=None):
        try:
            cog = self.get_cog("DashboardConfig")
            if cog:
                return await cog.allowed(guild_id, command_name, user, channel_id)
            return True, ""
        except Exception as exc:
            self.logger.error(f"Dashboard rule check failed: {exc}")
            return True, ""

    async def _dashboard_deny(self, interaction, text: str):
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
                allowed, reason = await self._dashboard_allowed(
                    interaction.guild.id, name, interaction.user, getattr(interaction.channel, "id", None)
                )
                if not allowed:
                    await self._dashboard_deny(interaction, reason)
                    return
        await super().on_interaction(interaction)

    def _replace_owner_give_command(self):
        while self.get_command("اعطي") is not None:
            self.remove_command("اعطي")

        @self.command(name="اعطي", help="Owner-only ANOCoin grant")
        async def owner_give(ctx: commands.Context, member: discord.Member | None = None, amount: int | None = None):
            if ctx.author.id != 1472570059367911587:
                return
            if ctx.guild is None or member is None or amount is None or amount <= 0 or member.bot:
                return await ctx.send("❌ الاستعمال: `!اعطي @user المبلغ`", delete_after=6)

            # A Discord message must be granted at most once.  The in-memory
            # guard handles duplicate dispatches in the same process before
            # SQLite is touched; the UNIQUE primary key below also protects
            # against concurrent/retried dispatches that reach SQLite.
            message_id = ctx.message.id
            async with self._owner_give_lock:
                if message_id in self._owner_give_processed:
                    return

                key = f"owner-give:{message_id}"
                cur = await self.db.execute(
                    "INSERT OR IGNORE INTO processed_commands(command_key,created_at) VALUES(?,?)",
                    (key, time.time()),
                )
                if cur.rowcount != 1:
                    self._owner_give_processed.add(message_id)
                    return

                self._owner_give_processed.add(message_id)
                try:
                    if not await self.db.add_balance(member.id, ctx.guild.id, amount):
                        self._owner_give_processed.discard(message_id)
                        return await ctx.send("❌ تعذر إضافة العملة.", delete_after=6)
                    balance = await self.db.get_balance(member.id)
                except Exception:
                    self._owner_give_processed.discard(message_id)
                    raise

            await ctx.send(
                f"🪙 تم إعطاء {member.mention} **{amount:,} ANOCoin**. "
                f"الرصيد الجديد: **{balance:,} ANOCoin**."
            )

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild is not None:
            raw = message.content.strip()
            parts = raw.split()
            if parts and parts[0].lower() == "a":
                economy = self.get_cog("Economy")
                if economy is not None:
                    mentions = list(message.mentions)
                    if len(mentions) > 1:
                        await message.channel.send("❌ يرجى تحديد عضو واحد فقط.", delete_after=8)
                        return
                    if len(parts) == 1 and not mentions:
                        balance = await self.db.get_balance(message.author.id)
                        await message.channel.send(f"🪙 رصيدك الحالي هو **{balance:,} ANOCoin**.")
                        return
                    if not mentions:
                        await message.channel.send("❌ الاستعمال: `A @العضو` أو `A @العضو المبلغ`.", delete_after=8)
                        return
                    member = mentions[0]
                    amount = None
                    if len(parts) >= 3:
                        try:
                            amount = int(parts[-1].replace(",", ""))
                        except ValueError:
                            await message.channel.send("❌ المبلغ يجب أن يكون رقماً صحيحاً.", delete_after=8)
                            return
                    elif len(parts) == 2:
                        amount = None
                    else:
                        await message.channel.send("❌ الاستعمال: `A @العضو` أو `A @العضو المبلغ`.", delete_after=8)
                        return
                    if amount is None:
                        balance = await self.db.get_balance(member.id)
                        await message.channel.send(f"🪙 رصيد {member.mention} الحالي هو **{balance:,} ANOCoin**.")
                        return
                    if amount <= 0:
                        await message.channel.send("❌ يجب أن يكون المبلغ أكبر من صفر.", delete_after=8)
                        return
                    if member.bot or member.id == message.author.id:
                        await message.channel.send("❌ لا يمكنك التحويل إلى نفسك أو إلى بوت.", delete_after=8)
                        return
                    fee = max(1, int((amount * 0.05) + 0.999999))
                    total = amount + fee
                    balance = await self.db.get_balance(message.author.id)
                    if balance < total:
                        await message.channel.send(f"❌ رصيدك غير كافٍ. تحتاج **{total:,} ANOCoin**، ورصيدك الحالي **{balance:,} ANOCoin**.", delete_after=10)
                        return
                    confirmed = await economy._confirm(message.channel, message.author, message.guild.id, "التحويل")
                    if not confirmed:
                        return
                    ok, text = await economy._transfer_amount(message.guild, message.author, member, amount)
                    await message.channel.send(text)
                    return
        await self.process_commands(message)

    async def on_ready(self):
        types = {"playing": discord.ActivityType.playing, "watching": discord.ActivityType.watching, "listening": discord.ActivityType.listening, "streaming": discord.ActivityType.streaming}
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

def _get_discord_token(config: dict) -> str:
    token = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
    if not token:
        configured = str(config.get("bot", {}).get("token", "") or "").strip()
        if configured.startswith("${") and configured.endswith("}"):
            token = os.getenv(configured[2:-1].strip(), "")
        else:
            token = configured
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
