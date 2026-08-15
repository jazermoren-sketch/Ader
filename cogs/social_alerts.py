"""SQLite-backed social media alert configuration for Ader."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from database.db_manager import DatabaseManager
from utils.embeds import EmbedFactory, EmbedColor
from utils.permissions import is_admin

logger = logging.getLogger(__name__)


class SocialAlerts(commands.Cog):
    def __init__(self, bot: commands.Bot, db: DatabaseManager, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.module_config = config.get('modules', {}).get('social_alerts', {})
        self.session: aiohttp.ClientSession | None = None
        self.check_alerts_task.start()

    async def ensure_tables(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS social_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                username TEXT NOT NULL,
                last_check REAL,
                last_content_id TEXT,
                UNIQUE(guild_id, platform, username)
            )
        """)

    def cog_unload(self):
        self.check_alerts_task.cancel()
        if self.session:
            asyncio.create_task(self.session.close())

    async def get_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    @tasks.loop(minutes=5)
    async def check_alerts_task(self):
        try:
            await self.ensure_tables()
            alerts = await self.db.fetchall("SELECT * FROM social_alerts ORDER BY id")
            for alert in alerts:
                try:
                    data = dict(alert)
                    platform = data['platform']
                    if platform == 'twitch':
                        await self.check_twitch(data)
                    elif platform == 'youtube':
                        await self.check_youtube(data)
                    elif platform == 'twitter':
                        await self.check_twitter(data)
                except Exception as exc:
                    logger.error("Error checking social alert %s: %s", alert['id'], exc, exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Error in social alerts task: %s", exc, exc_info=True)

    @check_alerts_task.before_loop
    async def before_check_alerts(self):
        await self.bot.wait_until_ready()

    async def check_twitch(self, alert: dict):
        # Provider-specific polling can be added later without coupling the DB to MongoDB.
        return None

    async def check_youtube(self, alert: dict):
        return None

    async def check_twitter(self, alert: dict):
        return None

    @app_commands.command(name="alert-add", description="Add social media alert (Admin)")
    @app_commands.describe(platform="Platform (twitch/youtube/twitter)", username="Username or channel ID", channel="Channel to send alerts to")
    @is_admin()
    async def add_alert(self, interaction: discord.Interaction, platform: str, username: str, channel: discord.TextChannel):
        platform = platform.lower()
        username = username.lower().strip()
        if platform not in ('twitch', 'youtube', 'twitter'):
            return await interaction.response.send_message(embed=EmbedFactory.error("Invalid Platform", "Platform must be twitch, youtube, or twitter."), ephemeral=True)
        await self.ensure_tables()
        existing = await self.db.fetchone(
            "SELECT id FROM social_alerts WHERE guild_id=? AND platform=? AND username=?",
            (interaction.guild.id, platform, username),
        )
        if existing:
            return await interaction.response.send_message(embed=EmbedFactory.warning("Already Exists", f"Alert for {username} on {platform} already exists."), ephemeral=True)
        await self.db.execute(
            "INSERT INTO social_alerts(guild_id,channel_id,platform,username,last_check,last_content_id) VALUES(?,?,?,?,NULL,NULL)",
            (interaction.guild.id, channel.id, platform, username),
        )
        emoji = {'twitch': '🟣', 'youtube': '🔴', 'twitter': '🐦'}[platform]
        await interaction.response.send_message(embed=EmbedFactory.success("Alert Added", f"{emoji} **{platform.title()}** alert added for **{username}** in {channel.mention}."))

    @app_commands.command(name="alert-remove", description="Remove social media alert (Admin)")
    @app_commands.describe(platform="Platform (twitch/youtube/twitter)", username="Username or channel ID")
    @is_admin()
    async def remove_alert(self, interaction: discord.Interaction, platform: str, username: str):
        platform = platform.lower()
        await self.ensure_tables()
        cur = await self.db.execute(
            "DELETE FROM social_alerts WHERE guild_id=? AND platform=? AND username=?",
            (interaction.guild.id, platform, username.lower().strip()),
        )
        if cur.rowcount == 0:
            return await interaction.response.send_message(embed=EmbedFactory.error("Not Found", f"No alert found for {username} on {platform}."), ephemeral=True)
        await interaction.response.send_message(embed=EmbedFactory.success("Alert Removed", f"Removed {platform} alert for **{username}**."))

    @app_commands.command(name="alert-list", description="List all social media alerts (Admin)")
    @is_admin()
    async def list_alerts(self, interaction: discord.Interaction):
        await self.ensure_tables()
        alerts = await self.db.fetchall("SELECT * FROM social_alerts WHERE guild_id=? ORDER BY platform,username", (interaction.guild.id,))
        if not alerts:
            return await interaction.response.send_message(embed=EmbedFactory.info("No Alerts", "No social media alerts configured."), ephemeral=True)
        grouped = {'twitch': [], 'youtube': [], 'twitter': []}
        for alert in alerts:
            channel = interaction.guild.get_channel(alert['channel_id'])
            grouped.setdefault(alert['platform'], []).append(f"• **{alert['username']}** → {channel.mention if channel else 'Unknown'}")
        labels = {'twitch': '🟣 **Twitch**', 'youtube': '🔴 **YouTube**', 'twitter': '🐦 **Twitter/X**'}
        description = ''
        for platform, items in grouped.items():
            if items:
                description += f"\n{labels[platform]}\n" + '\n'.join(items) + '\n'
        await interaction.response.send_message(embed=EmbedFactory.create(title="📢 Social Media Alerts", description=description or "No alerts configured", color=EmbedColor.INFO), ephemeral=True)

    @app_commands.command(name="alert-test", description="Test social media alert (Admin)")
    @app_commands.describe(platform="Platform (twitch/youtube/twitter)", username="Username to test")
    @is_admin()
    async def test_alert(self, interaction: discord.Interaction, platform: str, username: str):
        platform = platform.lower()
        row = await self.db.fetchone(
            "SELECT * FROM social_alerts WHERE guild_id=? AND platform=? AND username=?",
            (interaction.guild.id, platform, username.lower().strip()),
        )
        if not row:
            return await interaction.response.send_message(embed=EmbedFactory.error("Not Found", f"No alert found for {username} on {platform}."), ephemeral=True)
        channel = interaction.guild.get_channel(row['channel_id'])
        if not channel:
            return await interaction.response.send_message(embed=EmbedFactory.error("Channel Not Found", "Alert channel no longer exists."), ephemeral=True)
        links = {
            'twitch': f"https://twitch.tv/{username}",
            'youtube': f"https://youtube.com/@{username}",
            'twitter': f"https://twitter.com/{username}",
        }
        titles = {'twitch': '🟣 Twitch Stream Live!', 'youtube': '🔴 New YouTube Video!', 'twitter': '🐦 New Tweet!'}
        embed = EmbedFactory.create(title=titles.get(platform, '📢 Social Alert Test'), description=f"**{username}** test notification.\n\n[Open profile](<{links.get(platform, '')}>)", color=EmbedColor.INFO)
        embed.set_footer(text="This is a test notification")
        await channel.send(embed=embed)
        await interaction.response.send_message(embed=EmbedFactory.success("Test Sent", f"Test notification sent to {channel.mention}."), ephemeral=True)


async def setup(bot: commands.Bot):
    cog = SocialAlerts(bot, bot.db, bot.config)
    await cog.ensure_tables()
    await bot.add_cog(cog)
