"""Secure single-elimination tournaments with a sequential Champion quest chain."""
from __future__ import annotations

import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedFactory, EmbedColor

OWNER_ID = 1472570059367911587
MAX_PLAYERS = 32
MIN_PLAYERS = 4
CHAMPION_REWARD = 10000

QUESTS = [
    (1, "⚔️ اربح مباراة واحدة", "اربح مباراة رسمية واحدة.", 1),
    (2, "⚔️ اربح 3 مباريات", "اربح ثلاث مباريات رسمية.", 3),
    (3, "🏟️ شارك في بطولة", "شارك في بطولة وابدأ المنافسة.", 1),
    (4, "🔥 وصل إلى نصف النهائي", "تأهل إلى مرحلة نصف النهائي.", 1),
    (5, "🏆 اربح البطولة", "فز بنهائي بطولة رسمية.", 1),
]


class TournamentCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config

    async def cog_load(self):
        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            max_players INTEGER NOT NULL,
            reward INTEGER NOT NULL DEFAULT 5000,
            status TEXT NOT NULL DEFAULT 'open',
            winner_id INTEGER,
            created_at REAL NOT NULL,
            started_at REAL,
            ended_at REAL
        )
        """)
        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS tournament_participants (
            tournament_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at REAL NOT NULL,
            eliminated INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(tournament_id, user_id)
        )
        """)
        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS tournament_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_no INTEGER NOT NULL,
            match_no INTEGER NOT NULL,
            player1_id INTEGER,
            player2_id INTEGER,
            winner_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            reported_by INTEGER,
            created_at REAL NOT NULL,
            UNIQUE(tournament_id, round_no, match_no)
        )
        """)
        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS tournament_quest_progress (
            user_id INTEGER PRIMARY KEY,
            stage INTEGER NOT NULL DEFAULT 1,
            wins INTEGER NOT NULL DEFAULT 0,
            tournaments_played INTEGER NOT NULL DEFAULT 0,
            semifinal_count INTEGER NOT NULL DEFAULT 0,
            champions INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )
        """)
        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS tournament_badges (
            user_id INTEGER NOT NULL,
            badge_key TEXT NOT NULL,
            earned_at REAL NOT NULL,
            PRIMARY KEY(user_id, badge_key)
        )
        """)
        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS tournament_titles (
            user_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """)
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_tournament_status ON tournaments(guild_id, status)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_tournament_matches ON tournament_matches(tournament_id, round_no, status)")

    def _quest_embed(self, progress: dict) -> discord.Embed:
        stage = int(progress.get("stage", 1))
        lines = []
        for number, title, description, target in QUESTS:
            if number < stage:
                mark = "✅"
                value = "مكتملة"
            elif number == stage:
                mark = "🔸"
                if number == 1:
                    value = f"{min(progress['wins'], target)}/{target}"
                elif number == 2:
                    value = f"{min(progress['wins'], target)}/{target}"
                elif number == 3:
                    value = f"{min(progress['tournaments_played'], target)}/{target}"
                elif number == 4:
                    value = f"{min(progress['semifinal_count'], target)}/{target}"
                else:
                    value = f"{min(progress['champions'], target)}/{target}"
            else:
                mark = "🔒"
                value = "مقفلة"
            lines.append(f"{mark} **{number}. {title}** — {value}\n> {description}")
        embed = EmbedFactory.create(
            title="⚔️ طريق البطل",
            description="\n\n".join(lines),
            color=EmbedColor.ECONOMY,
        )
        embed.set_footer(text="Quest Chain • كل مرحلة خاصها تكمل اللي قبلها")
        return embed

    async def _progress(self, user_id: int, *, win: bool = False, tournament: bool = False, semifinal: bool = False, champion: bool = False):
        row = await self.db.fetchone("SELECT * FROM tournament_quest_progress WHERE user_id=?", (user_id,))
        now = time.time()
        if not row:
            await self.db.execute("INSERT INTO tournament_quest_progress(user_id,stage,wins,tournaments_played,semifinal_count,champions,updated_at) VALUES(?,?,?,?,?,?,?)", (user_id, 1, 0, 0, 0, 0, now))
            row = await self.db.fetchone("SELECT * FROM tournament_quest_progress WHERE user_id=?", (user_id,))
        wins = int(row[2]) + (1 if win else 0)
        played = int(row[3]) + (1 if tournament else 0)
        semis = int(row[4]) + (1 if semifinal else 0)
        champs = int(row[5]) + (1 if champion else 0)
        stage = int(row[1])
        if stage == 1 and wins >= 1:
            stage = 2
        if stage == 2 and wins >= 3:
            stage = 3
        if stage == 3 and played >= 1:
            stage = 4
        if stage == 4 and semis >= 1:
            stage = 5
        if stage == 5 and champs >= 1:
            stage = 6
            await self.db.execute("INSERT OR IGNORE INTO tournament_badges(user_id,badge_key,earned_at) VALUES(?,?,?)", (user_id, "champion", now))
            await self.db.execute("INSERT INTO tournament_titles(user_id,title,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at", (user_id, "Champion", now))
            await self.db.add_balance(user_id, 0, CHAMPION_REWARD)
        await self.db.execute("UPDATE tournament_quest_progress SET stage=?,wins=?,tournaments_played=?,semifinal_count=?,champions=?,updated_at=? WHERE user_id=?", (stage, wins, played, semis, champs, now, user_id))
        return stage

    async def _is_manager(self, interaction: discord.Interaction, tournament: dict) -> bool:
        return interaction.user.id == tournament["creator_id"] or interaction.user.guild_permissions.administrator

    async def _get_tournament(self, guild_id: int, tournament_id: int):
        return await self.db.fetchone("SELECT * FROM tournaments WHERE id=? AND guild_id=?", (tournament_id, guild_id))

    @app_commands.command(name="tournament-quest", description="View your Champion Quest Chain progress")
    async def tournament_quest(self, interaction: discord.Interaction):
        row = await self.db.fetchone("SELECT * FROM tournament_quest_progress WHERE user_id=?", (interaction.user.id,))
        if not row:
            await self._progress(interaction.user.id)
            row = await self.db.fetchone("SELECT * FROM tournament_quest_progress WHERE user_id=?", (interaction.user.id,))
        await interaction.response.send_message(embed=self._quest_embed(dict(row)), ephemeral=True)

    @app_commands.command(name="tournament-create", description="Create a tournament (Administrator)")
    @app_commands.describe(name="Tournament name", max_players="4, 8, 16 or 32", reward="ANOCoin reward for the tournament champion")
    @app_commands.checks.has_permissions(administrator=True)
    async def tournament_create(self, interaction: discord.Interaction, name: str, max_players: int = 8, reward: int = 5000):
        if max_players not in {4, 8, 16, 32}:
            return await interaction.response.send_message("❌ عدد اللاعبين خاصو يكون 4 أو 8 أو 16 أو 32.", ephemeral=True)
        if reward < 0 or reward > 1_000_000:
            return await interaction.response.send_message("❌ المكافأة خاصها تكون بين 0 و1,000,000 ANOCoin.", ephemeral=True)
        active = await self.db.fetchone("SELECT id FROM tournaments WHERE guild_id=? AND status IN ('open','running') LIMIT 1", (interaction.guild.id,))
        if active:
            return await interaction.response.send_message(f"❌ كاينة بطولة نشطة بالفعل: `#{active[0]}`.", ephemeral=True)
        cur = await self.db.execute("INSERT INTO tournaments(guild_id,creator_id,name,max_players,reward,status,created_at) VALUES(?,?,?,?,?,'open',?)", (interaction.guild.id, interaction.user.id, name[:100], max_players, reward, time.time()))
        await interaction.response.send_message(embed=EmbedFactory.success("🏆 Tournament Created", f"**{name[:100]}**\nID: `{cur.lastrowid}`\nPlayers: **0/{max_players}**\nReward: **{reward:,} ANOCoin**\n\nاستعمل `/tournament-join {cur.lastrowid}` للانضمام."))

    @app_commands.command(name="tournament-join", description="Join an open tournament")
    @app_commands.describe(tournament_id="Tournament ID")
    async def tournament_join(self, interaction: discord.Interaction, tournament_id: int):
        t = await self._get_tournament(interaction.guild.id, tournament_id)
        if not t or t["status"] != "open":
            return await interaction.response.send_message("❌ البطولة غير موجودة أو ما بقاتش مفتوحة.", ephemeral=True)
        count = await self.db.fetchone("SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
        if count[0] >= t["max_players"]:
            return await interaction.response.send_message("❌ البطولة عامرة.", ephemeral=True)
        exists = await self.db.fetchone("SELECT 1 FROM tournament_participants WHERE tournament_id=? AND user_id=?", (tournament_id, interaction.user.id))
        if exists:
            return await interaction.response.send_message("❌ راك منضم لهاد البطولة من قبل.", ephemeral=True)
        active = await self.db.fetchone("SELECT t.id FROM tournaments t JOIN tournament_participants p ON p.tournament_id=t.id WHERE p.user_id=? AND t.status='running' LIMIT 1", (interaction.user.id,))
        if active:
            return await interaction.response.send_message("❌ ما تقدرش تشارك فبطولتين خدامتين فـنفس الوقت.", ephemeral=True)
        await self.db.execute("INSERT INTO tournament_participants(tournament_id,user_id,joined_at) VALUES(?,?,?)", (tournament_id, interaction.user.id, time.time()))
        await interaction.response.send_message(f"✅ تسجلتي فـ**{t['name']}**. كاينين دابا **{count[0]+1}/{t['max_players']}** لاعبين.")

    @app_commands.command(name="tournament-start", description="Start a tournament (creator/admin)")
    @app_commands.describe(tournament_id="Tournament ID")
    async def tournament_start(self, interaction: discord.Interaction, tournament_id: int):
        t = await self._get_tournament(interaction.guild.id, tournament_id)
        if not t or not await self._is_manager(interaction, t):
            return await interaction.response.send_message("❌ غير منشئ البطولة أو Administrator يقدر يبداها.", ephemeral=True)
        if t["status"] != "open":
            return await interaction.response.send_message("❌ البطولة ماشي فحالة قابلة للبداية.", ephemeral=True)
        players = await self.db.fetchall("SELECT user_id FROM tournament_participants WHERE tournament_id=? ORDER BY joined_at ASC", (tournament_id,))
        if len(players) != t["max_players"]:
            return await interaction.response.send_message(f"❌ خاص العدد يكون كامل: **{t['max_players']}** لاعب.", ephemeral=True)
        ids = [int(x[0]) for x in players]
        random.Random(tournament_id ^ interaction.guild.id).shuffle(ids)
        total_rounds = int(__import__('math').log2(len(ids)))
        for index in range(0, len(ids), 2):
            await self.db.execute("INSERT INTO tournament_matches(tournament_id,round_no,match_no,player1_id,player2_id,status,created_at) VALUES(?,?,?,?,?,'pending',?)", (tournament_id, 1, index // 2 + 1, ids[index], ids[index+1], time.time()))
        await self.db.execute("UPDATE tournaments SET status='running',started_at=? WHERE id=?", (time.time(), tournament_id))
        for uid in ids:
            await self._progress(uid, tournament=True)
        await interaction.response.send_message(embed=EmbedFactory.success("🏆 البطولة بدات", f"**{t['name']}**\nRound 1 جاهز.\nعدد الجولات: **{total_rounds}**\nاستعمل `/tournament-report {tournament_id} <match_id> <winner>` لتسجيل نتيجة كل Match."))

    @app_commands.command(name="tournament-report", description="Report an official match result (creator/admin)")
    @app_commands.describe(tournament_id="Tournament ID", match_id="Match ID", winner="Winner of this match")
    async def tournament_report(self, interaction: discord.Interaction, tournament_id: int, match_id: int, winner: discord.Member):
        t = await self._get_tournament(interaction.guild.id, tournament_id)
        if not t or t["status"] != "running" or not await self._is_manager(interaction, t):
            return await interaction.response.send_message("❌ البطولة غير صالحة أو ما عندكش صلاحية تسجيل النتائج.", ephemeral=True)
        match = await self.db.fetchone("SELECT * FROM tournament_matches WHERE id=? AND tournament_id=?", (match_id, tournament_id))
        if not match or match["status"] != "pending":
            return await interaction.response.send_message("❌ الـMatch غير موجود أو تسجلت نتيجتو من قبل.", ephemeral=True)
        if winner.id not in {match["player1_id"], match["player2_id"]}:
            return await interaction.response.send_message("❌ الفائز خاصو يكون واحد من لاعبي الـMatch.", ephemeral=True)
        loser = match["player2_id"] if winner.id == match["player1_id"] else match["player1_id"]
        await self.db.execute("UPDATE tournament_matches SET winner_id=?,status='completed',reported_by=? WHERE id=? AND status='pending'", (winner.id, interaction.user.id, match_id))
        await self.db.execute("UPDATE tournament_participants SET eliminated=1 WHERE tournament_id=? AND user_id=?", (tournament_id, loser))
        await self._progress(winner.id, win=True)

        current_round = int(match["round_no"])
        participants_left = await self.db.fetchone("SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=? AND eliminated=0", (tournament_id,))
        if participants_left[0] == 1:
            await self.db.execute("UPDATE tournaments SET status='completed',winner_id=?,ended_at=? WHERE id=? AND status='running'", (winner.id, time.time(), tournament_id))
            await self.db.add_balance(winner.id, interaction.guild.id, int(t["reward"]))
            await self._progress(winner.id, champion=True)
            return await interaction.response.send_message(embed=EmbedFactory.success("🏆 بطل البطولة", f"مبروك {winner.mention}! ربحت **{t['name']}**.\nالمكافأة: **{t['reward']:,} ANOCoin**\nQuest Champion reward: **{CHAMPION_REWARD:,} ANOCoin** إذا كانت المرحلة النهائية مكتملة."))

        next_round = current_round + 1
        completed_count = await self.db.fetchone("SELECT COUNT(*) FROM tournament_matches WHERE tournament_id=? AND round_no=? AND status='completed'", (tournament_id, current_round))
        total_matches = await self.db.fetchone("SELECT COUNT(*) FROM tournament_matches WHERE tournament_id=? AND round_no=?", (tournament_id, current_round))
        if completed_count[0] == total_matches[0]:
            winners = await self.db.fetchall("SELECT winner_id FROM tournament_matches WHERE tournament_id=? AND round_no=? ORDER BY match_no", (tournament_id, current_round))
            for idx in range(0, len(winners), 2):
                await self.db.execute("INSERT INTO tournament_matches(tournament_id,round_no,match_no,player1_id,player2_id,status,created_at) VALUES(?,?,?,?,?,'pending',?)", (tournament_id, next_round, idx // 2 + 1, winners[idx][0], winners[idx+1][0], time.time()))
                if next_round == int(__import__('math').log2(t["max_players"])) - 1:
                    await self._progress(winners[idx][0], semifinal=True)
                    await self._progress(winners[idx+1][0], semifinal=True)

        await interaction.response.send_message(f"✅ تسجلت النتيجة: {winner.mention} ربح Match `{match_id}`.")

    @app_commands.command(name="tournament-list", description="List tournaments in this server")
    async def tournament_list(self, interaction: discord.Interaction):
        rows = await self.db.fetchall("SELECT * FROM tournaments WHERE guild_id=? ORDER BY id DESC LIMIT 10", (interaction.guild.id,))
        if not rows:
            return await interaction.response.send_message("❌ ما كايناش بطولات.", ephemeral=True)
        lines = []
        for t in rows:
            count = await self.db.fetchone("SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=?", (t["id"],))
            lines.append(f"`#{t['id']}` **{t['name']}** — {t['status']} — {count[0]}/{t['max_players']} لاعبين — {t['reward']:,} ANOCoin")
        await interaction.response.send_message(embed=EmbedFactory.create(title="🏆 Tournaments", description="\n".join(lines), color=EmbedColor.INFO))


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentCog(bot, bot.db, bot.config))
