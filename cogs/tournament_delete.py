"""Secure permanent tournament deletion with explicit confirmation."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedFactory, EmbedColor


class TournamentDeleteView(discord.ui.View):
    def __init__(self, cog: "TournamentDeleteCog", tournament_id: int, guild_id: int, requester_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.tournament_id = tournament_id
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ غير الأدمن اللي استعمل أمر الحذف يقدر يستعمل هاد الأزرار.",
                ephemeral=True,
            )
            return False
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ خاصك Administrator.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="حذف", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return await interaction.response.send_message("⚠️ هاد العملية تسالات من قبل.", ephemeral=True)

        # Re-check immediately before deletion so a stale confirmation cannot
        # act on a missing or changed tournament.
        tournament = await self.cog.get_tournament(self.guild_id, self.tournament_id)
        if not tournament:
            self.finished = True
            self.stop()
            return await interaction.response.edit_message(
                content="❌ البطولة ما بقاتش موجودة.", embed=None, view=None
            )

        deleted = await self.cog.delete_tournament(self.guild_id, self.tournament_id)
        self.finished = True
        self.stop()

        if not deleted:
            return await interaction.response.edit_message(
                content="❌ وقع مشكل أثناء الحذف. العملية ما تكملاتش.",
                embed=None,
                view=None,
            )

        await interaction.response.edit_message(
            content=(
                f"🗑️ تم حذف البطولة **{tournament['name']}** (`#{self.tournament_id}`) نهائياً.\n\n"
                "❗ بيانات البطولة واللاعبين والمباريات المرتبطة بها تحذفات وما يمكنش استرجاعها."
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return await interaction.response.send_message("⚠️ هاد العملية تسالات من قبل.", ephemeral=True)
        self.finished = True
        self.stop()
        await interaction.response.edit_message(
            content="✅ تم إلغاء عملية حذف البطولة. ما تبدل فيها والو.",
            embed=None,
            view=None,
        )

    async def on_timeout(self):
        if not self.finished:
            self.finished = True
            self.stop()


class TournamentDeleteCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config

    async def get_tournament(self, guild_id: int, tournament_id: int):
        return await self.db.fetchone(
            "SELECT * FROM tournaments WHERE id=? AND guild_id=?",
            (tournament_id, guild_id),
        )

    async def delete_tournament(self, guild_id: int, tournament_id: int) -> bool:
        """Atomically delete tournament-owned data only.

        Quest progress, badges, titles, balances, and all unrelated guild data
        are intentionally untouched.
        """
        conn = self.db.connection
        if conn is None:
            return False

        tournament = await self.get_tournament(guild_id, tournament_id)
        if not tournament:
            return False

        try:
            await conn.execute("BEGIN")
            await conn.execute(
                "DELETE FROM tournament_matches WHERE tournament_id=?",
                (tournament_id,),
            )
            await conn.execute(
                "DELETE FROM tournament_participants WHERE tournament_id=?",
                (tournament_id,),
            )
            cursor = await conn.execute(
                "DELETE FROM tournaments WHERE id=? AND guild_id=?",
                (tournament_id, guild_id),
            )
            if cursor.rowcount != 1:
                await conn.rollback()
                return False
            await conn.commit()
            return True
        except Exception:
            try:
                await conn.rollback()
            except Exception:
                pass
            return False

    @app_commands.command(
        name="tournament-delete",
        description="Delete a tournament permanently (Administrator)",
    )
    @app_commands.describe(tournament_id="Tournament ID")
    @app_commands.checks.has_permissions(administrator=True)
    async def tournament_delete(self, interaction: discord.Interaction, tournament_id: int):
        tournament = await self.get_tournament(interaction.guild.id, tournament_id)
        if not tournament:
            return await interaction.response.send_message(
                "❌ ما لقيتش بطولة بهاد الـID فهاد السيرفر.",
                ephemeral=True,
            )

        count = await self.db.fetchone(
            "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,),
        )
        matches = await self.db.fetchone(
            "SELECT COUNT(*) FROM tournament_matches WHERE tournament_id=?",
            (tournament_id,),
        )

        embed = EmbedFactory.create(
            title="⚠️ هل أنت متأكد من حذف هذه البطولة؟",
            description=(
                f"🏆 **{tournament['name']}**\n"
                f"🆔 Tournament ID: `{tournament_id}`\n"
                f"📊 الحالة: **{tournament['status']}**\n"
                f"👥 اللاعبون: **{count[0]}**\n"
                f"⚔️ المباريات: **{matches[0]}**\n\n"
                "❌ **لا يمكن استرجاعها بعد الحذف.**\n"
                "سيتم حذف بيانات هذه البطولة فقط."
            ),
            color=EmbedColor.ERROR,
        )
        view = TournamentDeleteView(
            self,
            tournament_id,
            interaction.guild.id,
            interaction.user.id,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentDeleteCog(bot, bot.db, bot.config))
