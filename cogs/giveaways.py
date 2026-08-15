"""SQLite-backed giveaway system for Ader."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from database.db_manager import DatabaseManager
from utils.embeds import EmbedFactory, EmbedColor
from utils.permissions import is_admin
from utils.converters import TimeConverter

logger = logging.getLogger(__name__)


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int, cog: 'Giveaways'):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.cog = cog

    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.success, custom_id="ader:giveaway:enter")
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway = await self.cog.db.fetchone(
            "SELECT * FROM giveaways WHERE id=?", (self.giveaway_id,)
        )
        if not giveaway or giveaway['ended']:
            return await interaction.response.send_message(
                embed=EmbedFactory.error("Giveaway Ended", "This giveaway has already ended or does not exist."),
                ephemeral=True,
            )

        existing = await self.cog.db.fetchone(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND user_id=?",
            (self.giveaway_id, interaction.user.id),
        )
        if existing:
            return await interaction.response.send_message(
                embed=EmbedFactory.warning("Already Entered", "You have already entered this giveaway!"),
                ephemeral=True,
            )

        await self.cog.db.execute(
            "INSERT INTO giveaway_entries(giveaway_id,user_id,created_at) VALUES(?,?,?)",
            (self.giveaway_id, interaction.user.id, time.time()),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success("Entered!", f"You have been entered into the giveaway for **{giveaway['prize']}**!"),
            ephemeral=True,
        )


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot, db: DatabaseManager, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.module_config = config.get('modules', {}).get('giveaways', {})
        self.giveaway_task = self.bot.loop.create_task(self.check_giveaways())

    async def ensure_tables(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(giveaway_id, user_id)
            )
        """)

    def cog_unload(self):
        self.giveaway_task.cancel()

    async def check_giveaways(self):
        await self.bot.wait_until_ready()
        await self.ensure_tables()
        while not self.bot.is_closed():
            try:
                rows = await self.db.fetchall(
                    "SELECT * FROM giveaways WHERE ended=0 AND ends_at<=? LIMIT 100",
                    (time.time(),),
                )
                for giveaway in rows:
                    await self.end_giveaway(dict(giveaway))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Error in giveaway checker: %s", exc, exc_info=True)
            await asyncio.sleep(30)

    async def _participants(self, giveaway_id: int):
        rows = await self.db.fetchall(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id=?",
            (giveaway_id,),
        )
        return [int(row['user_id']) for row in rows]

    async def end_giveaway(self, giveaway: dict):
        try:
            guild = self.bot.get_guild(giveaway['guild_id'])
            channel = guild.get_channel(giveaway['channel_id']) if guild else None
            participants = await self._participants(giveaway['id'])
            winners_count = int(giveaway.get('winners', 1))

            if not channel:
                await self.db.execute("UPDATE giveaways SET ended=1 WHERE id=?", (giveaway['id'],))
                return

            if not participants:
                embed = EmbedFactory.warning(
                    "🎉 Giveaway Ended",
                    f"**Prize:** {giveaway['prize']}\n\nNo one entered the giveaway! 😢",
                )
                await channel.send(embed=embed)
                winners = []
            else:
                winners = random.sample(participants, min(winners_count, len(participants)))
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                embed = EmbedFactory.success(
                    "🎉 Giveaway Ended",
                    f"**Prize:** {giveaway['prize']}\n\n**Winners:** {mentions}\n\nCongratulations! 🎊",
                )
                await channel.send(mentions, embed=embed)

            await self.db.execute("UPDATE giveaways SET ended=1 WHERE id=?", (giveaway['id'],))
            logger.info("Ended giveaway %s", giveaway['id'])
        except Exception as exc:
            logger.error("Error ending giveaway: %s", exc, exc_info=True)

    @app_commands.command(name="giveaway", description="Start a giveaway (Admin)")
    @app_commands.describe(prize="What are you giving away?", duration="Duration such as 1h, 30m or 1d", winners="Number of winners")
    @is_admin()
    async def start_giveaway(self, interaction: discord.Interaction, prize: str, duration: str, winners: int = 1):
        if winners < 1 or winners > 20:
            return await interaction.response.send_message(embed=EmbedFactory.error("Invalid Winners", "Winners must be between 1 and 20."), ephemeral=True)
        seconds = TimeConverter.parse(duration)
        if not seconds or seconds < 60 or seconds > 2592000:
            return await interaction.response.send_message(embed=EmbedFactory.error("Invalid Duration", "Duration must be between 1 minute and 30 days."), ephemeral=True)

        await self.ensure_tables()
        ends_at = time.time() + seconds
        cur = await self.db.execute(
            "INSERT INTO giveaways(guild_id,channel_id,message_id,prize,ends_at,winners,ended) VALUES(?,?,?,?,?,?,0)",
            (interaction.guild.id, interaction.channel.id, 0, prize, ends_at, winners),
        )
        giveaway_id = cur.lastrowid
        end_timestamp = int(ends_at)
        embed = EmbedFactory.create(
            title="🎉 GIVEAWAY 🎉",
            description=(f"**Prize:** {prize}\n\n**Winners:** {winners}\n"
                         f"**Hosted by:** {interaction.user.mention}\n"
                         f"**Ends:** <t:{end_timestamp}:R> (<t:{end_timestamp}:F>)\n\n"
                         "Click the button below to enter!"),
            color=EmbedColor.SUCCESS,
        )
        msg = await interaction.channel.send(embed=embed, view=GiveawayView(giveaway_id, self))
        await self.db.execute("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, giveaway_id))
        await interaction.response.send_message("🎉 Giveaway started!", ephemeral=True)

    @app_commands.command(name="gend", description="End a giveaway early (Admin)")
    @app_commands.describe(message_id="Message ID of the giveaway")
    @is_admin()
    async def end_giveaway_early(self, interaction: discord.Interaction, message_id: str):
        try:
            msg_id = int(message_id)
        except ValueError:
            return await interaction.response.send_message(embed=EmbedFactory.error("Invalid ID", "Please provide a valid message ID."), ephemeral=True)
        row = await self.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id=? AND channel_id=? AND message_id=? AND ended=0",
            (interaction.guild.id, interaction.channel.id, msg_id),
        )
        if not row:
            return await interaction.response.send_message(embed=EmbedFactory.error("Not Found", "No active giveaway found for that message."), ephemeral=True)
        await self.end_giveaway(dict(row))
        await interaction.response.send_message(embed=EmbedFactory.success("Ended", "The giveaway has been ended."), ephemeral=True)

    @app_commands.command(name="greroll", description="Reroll giveaway winners (Admin)")
    @app_commands.describe(message_id="Message ID of the giveaway")
    @is_admin()
    async def reroll_giveaway(self, interaction: discord.Interaction, message_id: str):
        try:
            msg_id = int(message_id)
        except ValueError:
            return await interaction.response.send_message(embed=EmbedFactory.error("Invalid ID", "Please provide a valid message ID."), ephemeral=True)
        row = await self.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id=? AND channel_id=? AND message_id=? AND ended=1",
            (interaction.guild.id, interaction.channel.id, msg_id),
        )
        if not row:
            return await interaction.response.send_message(embed=EmbedFactory.error("Not Found", "No ended giveaway found for that message."), ephemeral=True)
        participants = await self._participants(row['id'])
        if not participants:
            return await interaction.response.send_message(embed=EmbedFactory.error("No Participants", "This giveaway had no participants."), ephemeral=True)
        winners = random.sample(participants, min(int(row['winners']), len(participants)))
        mentions = " ".join(f"<@{uid}>" for uid in winners)
        await interaction.response.send_message(mentions, embed=EmbedFactory.success("🎉 Giveaway Rerolled", f"**Prize:** {row['prize']}\n\n**New Winners:** {mentions}\n\nCongratulations! 🎊"))


async def setup(bot: commands.Bot):
    cog = Giveaways(bot, bot.db, bot.config)
    await cog.ensure_tables()
    await bot.add_cog(cog)
