"""Ader Ultimate Discord bot entry point."""
import asyncio
import os
import sys
from pathlib import Path
from datetime import timedelta
import discord
from discord.ext import commands
import yaml
from dotenv import load_dotenv
from database.db_manager import DatabaseManager
from utils.logger import BotLogger

load_dotenv()

# Compatibility for legacy cogs that referenced discord.timedelta.
discord.timedelta = timedelta


class Ader(commands.Bot):
    def __init__(self, config: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        super().__init__(
            command_prefix=config.get('bot', {}).get('prefix', '/'),
            intents=intents,
            help_command=None,
        )
        self.config = config
        self.start_time = discord.utils.utcnow()
        self.logger = BotLogger(config.get('logging', {}))
        self.db = DatabaseManager(config.get('database', {}).get('sqlite_path', 'data/ader.sqlite3'))

    async def setup_hook(self):
        await self.db.connect()
        await self.load_cogs()

    async def load_cogs(self):
        directory = Path(__file__).parent / 'cogs'
        loaded = 0
        disabled = {
            'application_system.py',
            'application_system_v2.py',
            'application_system_v3.py',
        }

        for path in sorted(directory.glob('*.py')):
            if path.stem.startswith('_') or path.stem == '__init__' or path.name in disabled:
                continue
            try:
                # The newer cogs own these commands. Removing a stale command from
                # another legacy cog prevents CommandAlreadyRegistered during startup.
                if path.stem == 'application_system_v4':
                    self.tree.remove_command('تقديم')
                elif path.stem == 'ultimate_system':
                    self.tree.remove_command('warn')
                    self.tree.remove_command('warnings')

                await self.load_extension(f'cogs.{path.stem}')
                loaded += 1
            except Exception as exc:
                self.logger.error(f'Failed to load cog {path.stem}: {exc}', exc_info=True)
        self.logger.info(f'Loaded {loaded} cogs')

    async def on_ready(self):
        types = {
            'playing': discord.ActivityType.playing,
            'watching': discord.ActivityType.watching,
            'listening': discord.ActivityType.listening,
            'streaming': discord.ActivityType.streaming,
        }
        typ = self.config.get('bot', {}).get('activity_type', 'watching')
        text = self.config.get('bot', {}).get('activity', 'مجتمعك')
        await self.change_presence(
            activity=discord.Activity(
                type=types.get(typ, discord.ActivityType.watching),
                name=text,
            )
        )
        try:
            synced = await self.tree.sync()
            self.logger.info(f'Synced {len(synced)} application commands')
        except Exception as exc:
            self.logger.error(f'Command sync failed: {exc}', exc_info=True)
        self.logger.info(f'Ader ready as {self.user} in {len(self.guilds)} guilds')

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send('❌ ليس لديك الصلاحية لاستخدام هذا الأمر.', delete_after=6)
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f'❌ المعطى المطلوب ناقص: `{error.param.name}`', delete_after=6)
            return
        self.logger.error(f'Command error: {error}', exc_info=True)

    async def on_error(self, event, *args, **kwargs):
        self.logger.error(f'Event error: {event}', exc_info=True)

    async def close(self):
        await self.db.disconnect()
        await super().close()


def load_config(path='config.yaml'):
    try:
        with open(path, encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f'Config error: {exc}')
        sys.exit(1)

    def replace(v):
        if isinstance(v, dict):
            return {k: replace(x) for k, x in v.items()}
        if isinstance(v, list):
            return [replace(x) for x in v]
        if isinstance(v, str) and v.startswith('${') and v.endswith('}'):
            return os.getenv(v[2:-1], v)
        return v

    return replace(config)


async def main():
    config = load_config()
    token = os.getenv('DISCORD_BOT_TOKEN') or config.get('bot', {}).get('token')
    if not token or token.startswith('${'):
        print('Error: DISCORD_BOT_TOKEN is not configured')
        sys.exit(1)
    bot = Ader(config)
    async with bot:
        await bot.start(token)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
