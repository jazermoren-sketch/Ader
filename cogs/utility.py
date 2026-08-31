"""
Utility Cog for Logiq
General utility commands
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from typing import Optional
import logging
import asyncio

from utils.embeds import EmbedFactory, EmbedColor
from utils.converters import TimeConverter
from utils.permissions import is_admin
from database.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class PollView(discord.ui.View):
    """Interactive poll view"""

    def __init__(self, question: str, options: list, duration: int):
        super().__init__(timeout=duration)
        self.question = question
        self.options = options
        self.votes = {i: [] for i in range(len(options))}

    def get_results_embed(self) -> discord.Embed:
        """Generate results embed"""
        total_votes = sum(len(voters) for voters in self.votes.values())
        description = f"**{self.question}**\n\n"

        for i, option in enumerate(self.options):
            vote_count = len(self.votes[i])
            percentage = (vote_count / total_votes * 100) if total_votes > 0 else 0
            bar_length = int(percentage / 10)
            bar = "█" * bar_length + "░" * (10 - bar_length)
            description += f"{i + 1}. {option}\n{bar} {vote_count} votes ({percentage:.1f}%)\n\n"

        embed = EmbedFactory.create(
            title="📊 Poll Results",
            description=description,
            color=EmbedColor.INFO
        )
        embed.set_footer(text=f"Total votes: {total_votes}")
        return embed

    @discord.ui.button(label="1", style=discord.ButtonStyle.primary, custom_id="poll_1")
    async def option_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, 0)

    @discord.ui.button(label="2", style=discord.ButtonStyle.primary, custom_id="poll_2")
    async def option_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, 1)

    @discord.ui.button(label="3", style=discord.ButtonStyle.primary, custom_id="poll_3")
    async def option_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, 2)

    @discord.ui.button(label="4", style=discord.ButtonStyle.primary, custom_id="poll_4")
    async def option_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, 3)

    async def _vote(self, interaction: discord.Interaction, option_index: int):
        """Handle vote"""
        if option_index >= len(self.options):
            await interaction.response.send_message("Invalid option", ephemeral=True)
            return

        user_id = interaction.user.id

        # Remove previous vote
        for voters in self.votes.values():
            if user_id in voters:
                voters.remove(user_id)

        # Add new vote
        self.votes[option_index].append(user_id)

        # Update message
        await interaction.response.edit_message(embed=self.get_results_embed())


class Utility(commands.Cog):
    """Utility commands cog"""

    def __init__(self, bot: commands.Bot, db: DatabaseManager, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.reminders_task = None
        self.team_role_sync_task = None

    async def cog_load(self):
        self.reminders_task = asyncio.create_task(self.check_reminders())
        self.team_role_sync_task = asyncio.create_task(self.sync_team_roles_when_ready())

    def cog_unload(self):
        """Cleanup on cog unload"""
        if self.reminders_task:
            self.reminders_task.cancel()
        if self.team_role_sync_task:
            self.team_role_sync_task.cancel()

    async def sync_team_roles_when_ready(self):
        """Make verified team membership follow Discord team roles."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(1)
        try:
            await self.db.execute(
                "CREATE TABLE IF NOT EXISTS team_members(" 
                "team_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,joined_at REAL NOT NULL,"
                "PRIMARY KEY(team_id,user_id),UNIQUE(guild_id,user_id))"
            )
            teams = await self.db.fetchall(
                "SELECT id,guild_id,role_id FROM verified_teams WHERE active=1"
            )
            by_guild = {}
            for row in teams:
                by_guild.setdefault(int(row["guild_id"]), []).append(row)

            for guild_id, team_rows in by_guild.items():
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    continue
                for member in guild.members:
                    await self.sync_member_team_role(member, team_rows)
        except Exception:
            logger.error("Failed to synchronize team memberships from roles", exc_info=True)

    async def sync_member_team_role(self, member: discord.Member, team_rows=None):
        """Synchronize one member's team membership from their verified role."""
        if member.guild is None or member.bot:
            return
        if team_rows is None:
            team_rows = await self.db.fetchall(
                "SELECT id,guild_id,role_id FROM verified_teams WHERE guild_id=? AND active=1",
                (member.guild.id,),
            )

        role_ids = {role.id for role in member.roles}
        matches = [row for row in team_rows if int(row["role_id"]) in role_ids]

        # The existing team_members schema allows one team per guild/member.
        # If multiple verified team roles exist, use the highest Discord role.
        selected = None
        if matches:
            role_position = {role.id: role.position for role in member.roles}
            selected = max(matches, key=lambda row: role_position.get(int(row["role_id"]), -1))

        try:
            if selected is None:
                await self.db.execute(
                    "DELETE FROM team_members WHERE guild_id=? AND user_id=?",
                    (member.guild.id, member.id),
                )
                return

            team_id = int(selected["id"])
            await self.db.execute(
                "DELETE FROM team_members WHERE guild_id=? AND user_id=? AND team_id<>?",
                (member.guild.id, member.id, team_id),
            )
            await self.db.execute(
                "INSERT OR IGNORE INTO team_members(team_id,guild_id,user_id,joined_at) VALUES(?,?,?,?)",
                (team_id, member.guild.id, member.id, datetime.utcnow().timestamp()),
            )
        except Exception:
            logger.error(
                "Failed to synchronize team role for member %s in guild %s",
                member.id, member.guild.id, exc_info=True
            )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return
        await self.sync_member_team_role(after)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.sync_member_team_role(member)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        try:
            team = await self.db.fetchone(
                "SELECT id FROM verified_teams WHERE guild_id=? AND role_id=?",
                (role.guild.id, role.id),
            )
            if team:
                await self.db.execute(
                    "UPDATE verified_teams SET active=0 WHERE id=?",
                    (team["id"],),
                )
                await self.db.execute(
                    "DELETE FROM team_members WHERE team_id=?",
                    (team["id"],),
                )
        except Exception:
            logger.error("Failed to clean deleted team role", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle the requested `-بوت` shortcut."""
        if message.author.bot or message.guild is None:
            return
        if message.content.strip() != "-بوت":
            return

        bot_user = self.bot.user
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="🤖 Ader",
            description="البوت خدام بشكل طبيعي ✅",
            color=discord.Color.green(),
        )
        embed.add_field(name="📡 Ping", value=f"`{latency}ms`", inline=True)
        embed.add_field(name="🏠 السيرفرات", value=f"`{len(self.bot.guilds)}`", inline=True)
        if bot_user:
            embed.set_thumbnail(url=bot_user.display_avatar.url)
        await message.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def check_reminders(self):
        """Background task to check for due reminders"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                current_time = datetime.utcnow().timestamp()
                due_reminders = await self.db.get_due_reminders(current_time)

                for reminder in due_reminders:
                    try:
                        channel = self.bot.get_channel(reminder['channel_id'])
                        if channel:
                            user = await self.bot.fetch_user(reminder['user_id'])
                            embed = EmbedFactory.info(
                                "⏰ Reminder",
                                f"{user.mention} {reminder['message']}"
                            )
                            await channel.send(embed=embed)

                        await self.db.complete_reminder(str(reminder['_id']))
                    except Exception as e:
                        logger.error(f"Error sending reminder: {e}", exc_info=True)

                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"Error in reminder checker: {e}", exc_info=True)
                await asyncio.sleep(60)

    @app_commands.command(name="poll", description="Create a poll (Admin)")
    @app_commands.describe(
        question="Poll question",
        option1="Option 1",
        option2="Option 2",
        option3="Option 3 (optional)",
        option4="Option 4 (optional)",
        duration="Duration in minutes (default: 60)"
    )
    @is_admin()
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        option1: str,
        option2: str,
        option3: Optional[str] = None,
        option4: Optional[str] = None,
        duration: int = 60
    ):
        """Create a poll"""
        options = [option1, option2]
        if option3:
            options.append(option3)
        if option4:
            options.append(option4)

        if duration < 1 or duration > 10080:
            await interaction.response.send_message(
                embed=EmbedFactory.error("Invalid Duration", "Duration must be between 1 minute and 1 week"),
                ephemeral=True
            )
            return

        view = PollView(question, options, duration * 60)
        for i in range(4):
            if i >= len(options):
                view.children[i].disabled = True

        embed = view.get_results_embed()
        embed.set_footer(text=f"Poll ends in {duration} minutes | Total votes: 0")

        await interaction.response.send_message(embed=embed, view=view)
        logger.info(f"{interaction.user} created poll in {interaction.guild}")

    @app_commands.command(name="remind", description="Set a reminder (Admin)")
    @app_commands.describe(
        duration="When to remind (e.g., 1h, 30m, 1d)",
        message="Reminder message"
    )
    @is_admin()
    async def remind(self, interaction: discord.Interaction, duration: str, message: str):
        """Set a reminder"""
        seconds = TimeConverter.parse(duration)
        if not seconds:
            await interaction.response.send_message(
                embed=EmbedFactory.error("Invalid Duration", "Please provide a valid duration (e.g., 1h, 30m, 2d)"),
                ephemeral=True
            )
            return

        if seconds > 31536000:
            await interaction.response.send_message(
                embed=EmbedFactory.error("Duration Too Long", "Maximum reminder duration is 1 year"),
                ephemeral=True
            )
            return

        remind_at = datetime.utcnow().timestamp() + seconds
        reminder_data = {
            "user_id": interaction.user.id,
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "message": message,
            "remind_at": remind_at,
            "completed": False
        }

        await self.db.create_reminder(reminder_data)

        embed = EmbedFactory.success(
            "Reminder Set",
            f"I'll remind you in **{TimeConverter.format_seconds(seconds)}**\n\n"
            f"Message: {message}"
        )
        await interaction.response.send_message(embed=embed)
        logger.info(f"{interaction.user} set reminder in {interaction.guild}")

    @app_commands.command(name="serverstats", description="View server statistics (Admin)")
    @is_admin()
    async def serverstats(self, interaction: discord.Interaction):
        """View server stats"""
        guild = interaction.guild
        total_members = guild.member_count
        bots = sum(1 for member in guild.members if member.bot)
        humans = total_members - bots
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        roles = len(guild.roles)

        embed = EmbedFactory.create(
            title=f"📊 Server Statistics - {guild.name}",
            color=EmbedColor.INFO,
            thumbnail=guild.icon.url if guild.icon else None,
            fields=[
                {"name": "👥 Total Members", "value": str(total_members), "inline": True},
                {"name": "🙋 Humans", "value": str(humans), "inline": True},
                {"name": "🤖 Bots", "value": str(bots), "inline": True},
                {"name": "💬 Text Channels", "value": str(text_channels), "inline": True},
                {"name": "🔊 Voice Channels", "value": str(voice_channels), "inline": True},
                {"name": "🎭 Roles", "value": str(roles), "inline": True},
                {"name": "👑 Owner", "value": guild.owner.mention if guild.owner else "Unknown", "inline": True},
                {"name": "📅 Created", "value": guild.created_at.strftime("%Y-%m-%d"), "inline": True},
                {"name": "🚀 Boost Level", "value": f"Level {guild.premium_tier}", "inline": True}
            ]
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Get information about a user (Admin)")
    @app_commands.describe(user="User to get info about")
    @is_admin()
    async def userinfo(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """Get user information"""
        target = user or interaction.user
        roles = [role.mention for role in target.roles[1:]]
        roles_str = ", ".join(roles[:10]) if roles else "None"
        if len(roles) > 10:
            roles_str += f" (+{len(roles) - 10} more)"

        embed = EmbedFactory.create(
            title=f"User Information - {target.display_name}",
            color=target.color if target.color.value != 0 else EmbedColor.INFO,
            thumbnail=target.display_avatar.url,
            fields=[
                {"name": "Username", "value": str(target), "inline": True},
                {"name": "ID", "value": str(target.id), "inline": True},
                {"name": "Nickname", "value": target.nick or "None", "inline": True},
                {"name": "Account Created", "value": target.created_at.strftime("%Y-%m-%d"), "inline": True},
                {"name": "Joined Server", "value": target.joined_at.strftime("%Y-%m-%d") if target.joined_at else "Unknown", "inline": True},
                {"name": "Top Role", "value": target.top_role.mention, "inline": True},
                {"name": f"Roles ({len(roles)})", "value": roles_str, "inline": False}
            ]
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Get user's avatar (Admin)")
    @app_commands.describe(user="User to get avatar from")
    @is_admin()
    async def avatar(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """Get user avatar"""
        target = user or interaction.user
        embed = EmbedFactory.create(
            title=f"Avatar - {target.display_name}",
            color=EmbedColor.INFO,
            image=target.display_avatar.url
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    """Setup function for cog loading"""
    await bot.add_cog(Utility(bot, bot.db, bot.config))
