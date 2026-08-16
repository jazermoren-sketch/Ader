"""Small migration for idempotent one-shot commands."""
from discord.ext import commands


class ProcessedCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.db.execute("""
            CREATE TABLE IF NOT EXISTS processed_commands(
                command_key TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            )
        """)


async def setup(bot):
    await bot.add_cog(ProcessedCommands(bot))
