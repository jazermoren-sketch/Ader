"""Verified clubs/national teams and secure player offers for Ader."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedFactory, EmbedColor


EMOJI_RE = re.compile(r"^.{1,16}$", re.UNICODE)
OFFER_TTL = 48 * 60 * 60
MAX_TEAM_MEMBERS = 15


def _type_label(value: str) -> str:
    return "منتخب وطني" if value == "national" else "نادي"


class OfferView(discord.ui.View):
    def __init__(self, cog: "Teams", offer_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.offer_id = offer_id

    @discord.ui.button(label="قبول العرض", style=discord.ButtonStyle.success, emoji="✅", custom_id="team_offer_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_offer(interaction, self.offer_id, True)

    @discord.ui.button(label="رفض العرض", style=discord.ButtonStyle.danger, emoji="❌", custom_id="team_offer_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_offer(interaction, self.offer_id, False)


class TeamsListView(discord.ui.View):
    def __init__(self, cog: "Teams", guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="تحديث", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="teams_list_update")
    async def update(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != self.guild_id:
            return await interaction.response.send_message("❌ هذه اللوحة لا تخص هذا الخادم.", ephemeral=True)
        await interaction.response.defer()
        embed = await self.cog.build_teams_embed(self.guild_id)
        await interaction.message.edit(embed=embed, view=self)


class TeamSettingsView(discord.ui.View):
    def __init__(self, cog: "Teams", guild_id: int, user_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ لا يمكنك تعديل هذه الإعدادات.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="تحديد رتبة المدربين", style=discord.ButtonStyle.primary, emoji="🎯", row=0)
    async def set_coach_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RoleIdModal(self.cog, self.guild_id, "coach"))

    @discord.ui.button(label="إلغاء رتبة المدربين", style=discord.ButtonStyle.secondary, emoji="🧹", row=0)
    async def clear_coach_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.set_config(self.guild_id, None)
        await interaction.response.edit_message(embed=self.cog.settings_embed(self.guild_id), view=self)

    @discord.ui.button(label="تغيير الحد الأقصى للاعبين", style=discord.ButtonStyle.primary, emoji="👥", row=1)
    async def max_players(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MaxPlayersModal(self.cog, self.guild_id))

    @discord.ui.button(label="إغلاق", style=discord.ButtonStyle.danger, emoji="✖️", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(view=None)


class RoleIdModal(discord.ui.Modal, title="إعداد رتبة المدربين"):
    role_id = discord.ui.TextInput(label="معرّف الرتبة", placeholder="مثال: 123456789012345678", min_length=5, max_length=25)

    def __init__(self, cog: "Teams", guild_id: int, key: str):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.key = key

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rid = int(str(self.role_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ معرّف الرتبة غير صالح.", ephemeral=True)
        guild = interaction.guild
        role = guild.get_role(rid) if guild else None
        if role is None:
            return await interaction.response.send_message("❌ لم يتم العثور على هذه الرتبة.", ephemeral=True)
        await self.cog.set_config(self.guild_id, rid)
        await interaction.response.send_message("✅ تم حفظ رتبة المدربين.", ephemeral=True)


class MaxPlayersModal(discord.ui.Modal, title="الحد الأقصى للاعبين"):
    amount = discord.ui.TextInput(label="العدد", placeholder="15", min_length=1, max_length=2)

    def __init__(self, cog: "Teams", guild_id: int):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(str(self.amount.value).strip())
        except ValueError:
            return await interaction.response.send_message("❌ يجب إدخال رقم صحيح.", ephemeral=True)
        if not 1 <= value <= 50:
            return await interaction.response.send_message("❌ يجب أن يكون العدد بين 1 و50.", ephemeral=True)
        await self.cog.set_config(self.guild_id, value, key="max_players")
        await interaction.response.send_message(f"✅ تم تحديد الحد الأقصى للاعبين إلى **{value}**.", ephemeral=True)


class Teams(commands.Cog):
    """Unified verified-club and national-team management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.lock = asyncio.Lock()

    async def cog_load(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS verified_teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                emoji TEXT NOT NULL,
                team_type TEXT NOT NULL DEFAULT 'club',
                logo_url TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                UNIQUE(guild_id, role_id)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS team_members (
                team_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at REAL NOT NULL,
                PRIMARY KEY(team_id, user_id),
                UNIQUE(guild_id, user_id),
                FOREIGN KEY(team_id) REFERENCES verified_teams(id) ON DELETE CASCADE
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS team_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                offered_by INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                UNIQUE(team_id, player_id, status),
                FOREIGN KEY(team_id) REFERENCES verified_teams(id) ON DELETE CASCADE
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS team_settings (
                guild_id INTEGER PRIMARY KEY,
                coach_role_id INTEGER,
                max_players INTEGER NOT NULL DEFAULT 15,
                list_channel_id INTEGER,
                list_message_id INTEGER,
                updated_at REAL NOT NULL
            )
        """)
        rows = await self.db.fetchall("SELECT id,player_id FROM team_offers WHERE status='pending' AND expires_at<=?", (time.time(),))
        for row in rows:
            await self.db.execute("UPDATE team_offers SET status='expired' WHERE id=? AND status='pending'", (row[0],))
        pending = await self.db.fetchall("SELECT id FROM team_offers WHERE status='pending' AND expires_at>?", (time.time(),))
        for row in pending:
            self.bot.add_view(OfferView(self, int(row[0])))
        lists = await self.db.fetchall("SELECT guild_id,message_id FROM team_settings WHERE message_id IS NOT NULL")
        for row in lists:
            self.bot.add_view(TeamsListView(self, int(row[0])), message_id=int(row[1]))

    async def config(self, guild_id: int):
        row = await self.db.fetchone("SELECT * FROM team_settings WHERE guild_id=?", (guild_id,))
        if row:
            return dict(row)
        await self.db.execute("INSERT INTO team_settings(guild_id,updated_at) VALUES(?,?)", (guild_id, time.time()))
        return dict(await self.db.fetchone("SELECT * FROM team_settings WHERE guild_id=?", (guild_id,)))

    async def set_config(self, guild_id: int, value, key: str = "coach_role_id"):
        await self.config(guild_id)
        if key == "coach_role_id":
            await self.db.execute("UPDATE team_settings SET coach_role_id=?,updated_at=? WHERE guild_id=?", (value, time.time(), guild_id))
        else:
            await self.db.execute("UPDATE team_settings SET max_players=?,updated_at=? WHERE guild_id=?", (int(value), time.time(), guild_id))

    def settings_embed(self, guild_id: int):
        return EmbedFactory.create("⚙️ إعدادات الأندية والمنتخبات", "يمكن للإدارة تخصيص صلاحيات المدربين وحدود القوائم.", color=EmbedColor.ECONOMY)

    async def build_teams_embed(self, guild_id: int):
        teams = await self.db.fetchall("SELECT * FROM verified_teams WHERE guild_id=? AND active=1 ORDER BY team_type,id", (guild_id,))
        embed = EmbedFactory.create("🏅 الفرق الموثقة", "الأندية والمنتخبات الموثقة في هذا الخادم.", color=EmbedColor.ECONOMY)
        if not teams:
            embed.description = "لا توجد أندية أو منتخبات موثقة حاليًا."
            return embed
        lines = []
        for team in teams:
            count = await self.db.fetchone("SELECT COUNT(*) FROM team_members WHERE team_id=?", (team["id"],))
            role = f"<@&{team['role_id']}>"
            lines.append(f"{team['emoji']} **{team['name']}** — {role}\n└ 👥 **{int(count[0])}/15** لاعبًا · {_type_label(team['team_type'])}")
        embed.description = "\n\n".join(lines)
        return embed

    async def is_coach(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or interaction.user.guild_permissions.administrator:
            return True
        cfg = await self.config(interaction.guild.id)
        rid = cfg.get("coach_role_id")
        return bool(rid and any(r.id == int(rid) for r in interaction.user.roles))

    team_group = app_commands.Group(name="team", description="إدارة الأندية والمنتخبات الموثقة")

    @team_group.command(name="addteam", description="إضافة نادٍ أو منتخب موثق")
    @app_commands.describe(role="رتبة الفريق", emoji="رمز الفريق", team_type="نوع الفريق", logo_url="رابط شعار الفريق (اختياري)")
    @app_commands.choices(team_type=[app_commands.Choice(name="نادي", value="club"), app_commands.Choice(name="منتخب وطني", value="national")])
    @app_commands.checks.has_permissions(administrator=True)
    async def addteam(self, interaction: discord.Interaction, role: discord.Role, emoji: str, team_type: app_commands.Choice[str], logo_url: Optional[str] = None):
        if not EMOJI_RE.match(emoji.strip()):
            return await interaction.response.send_message("❌ رمز الفريق غير صالح.", ephemeral=True)
        if role.is_default() or role.managed:
            return await interaction.response.send_message("❌ لا يمكن استخدام رتبة @everyone أو رتبة مُدارة من تكامل خارجي.", ephemeral=True)
        if not interaction.guild.me or role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ يجب أن تكون رتبة الفريق أسفل أعلى رتبة للبوت حتى يتمكن من إدارتها.", ephemeral=True)
        name = role.name
        try:
            await self.db.execute("INSERT INTO verified_teams(guild_id,role_id,name,emoji,team_type,logo_url,created_at) VALUES(?,?,?,?,?,?,?)", (interaction.guild.id, role.id, name, emoji.strip(), team_type.value, logo_url, time.time()))
        except Exception:
            return await interaction.response.send_message("❌ هذه الرتبة مسجلة بالفعل كفريق موثق.", ephemeral=True)
        await interaction.response.send_message(f"✅ تم توثيق {_type_label(team_type.value)} **{name}** {emoji.strip()} بنجاح.", ephemeral=True)

    @team_group.command(name="removeteam", description="إزالة فريق موثق")
    @app_commands.describe(role="رتبة الفريق")
    @app_commands.checks.has_permissions(administrator=True)
    async def removeteam(self, interaction: discord.Interaction, role: discord.Role):
        row = await self.db.fetchone("SELECT id,name FROM verified_teams WHERE guild_id=? AND role_id=? AND active=1", (interaction.guild.id, role.id))
        if not row:
            return await interaction.response.send_message("❌ هذا الفريق غير موثق.", ephemeral=True)
        await self.db.execute("UPDATE verified_teams SET active=0 WHERE id=?", (row[0],))
        await self.db.execute("DELETE FROM team_offers WHERE team_id=? AND status='pending'", (row[0],))
        await interaction.response.send_message(f"✅ تمت إزالة توثيق **{row[1]}** وإلغاء عروضه المعلقة.", ephemeral=True)

    @team_group.command(name="list", description="عرض الأندية والمنتخبات الموثقة")
    async def team_list(self, interaction: discord.Interaction):
        embed = await self.build_teams_embed(interaction.guild.id)
        view = TeamsListView(self, interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        cfg = await self.config(interaction.guild.id)
        await self.db.execute("UPDATE team_settings SET list_channel_id=?,list_message_id=?,updated_at=? WHERE guild_id=?", (msg.channel.id, msg.id, time.time(), interaction.guild.id))

    @team_group.command(name="offer", description="إرسال عرض انضمام للاعب")
    @app_commands.describe(player="اللاعب الذي تريد دعوته", role="رتبة الفريق")
    async def offer(self, interaction: discord.Interaction, player: discord.Member, role: discord.Role):
        if not await self.is_coach(interaction):
            return await interaction.response.send_message("❌ هذا الأمر متاح فقط لمن يملك رتبة المدربين المحددة من الإدارة.", ephemeral=True)
        if player.bot:
            return await interaction.response.send_message("❌ لا يمكن دعوة حسابات البوتات.", ephemeral=True)
        team = await self.db.fetchone("SELECT * FROM verified_teams WHERE guild_id=? AND role_id=? AND active=1", (interaction.guild.id, role.id))
        if not team:
            return await interaction.response.send_message("❌ هذه الرتبة ليست لفريق موثق.", ephemeral=True)
        if player.id == interaction.user.id:
            return await interaction.response.send_message("❌ لا يمكنك إرسال عرض إلى نفسك.", ephemeral=True)
        member_team = await self.db.fetchone("SELECT t.name,t.team_type FROM team_members m JOIN verified_teams t ON t.id=m.team_id WHERE m.guild_id=? AND m.user_id=? AND t.active=1", (interaction.guild.id, player.id))
        if member_team:
            return await interaction.response.send_message(f"❌ هذا اللاعب مرتبط حاليًا بـ **{member_team[0]}** ولا يمكنه الانضمام إلى فريق آخر.", ephemeral=True)
        count = await self.db.fetchone("SELECT COUNT(*) FROM team_members WHERE team_id=?", (team["id"],))
        cfg = await self.config(interaction.guild.id)
        limit = int(cfg.get("max_players") or MAX_TEAM_MEMBERS)
        if int(count[0]) >= limit:
            return await interaction.response.send_message(f"❌ قائمة الفريق مكتملة ({limit} لاعبًا).", ephemeral=True)
        existing = await self.db.fetchone("SELECT id FROM team_offers WHERE team_id=? AND player_id=? AND status='pending' AND expires_at>?", (team["id"], player.id, time.time()))
        if existing:
            return await interaction.response.send_message("❌ يوجد بالفعل عرض نشط لهذا اللاعب من هذا الفريق.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        cur = await self.db.execute("INSERT INTO team_offers(team_id,guild_id,player_id,offered_by,created_at,expires_at) VALUES(?,?,?,?,?,?)", (team["id"], interaction.guild.id, player.id, interaction.user.id, time.time(), time.time() + OFFER_TTL))
        offer_id = int(cur.lastrowid)
        view = OfferView(self, offer_id)
        embed = EmbedFactory.create("📩 عرض انضمام إلى فريق", f"تلقيت عرضًا للانضمام إلى **{team['name']}** {team['emoji']}.", color=EmbedColor.ECONOMY)
        embed.add_field(name="نوع الفريق", value=_type_label(team["team_type"]), inline=True)
        embed.add_field(name="المرسل", value=interaction.user.mention, inline=True)
        embed.add_field(name="صلاحية العرض", value="48 ساعة", inline=True)
        if team["logo_url"]:
            embed.set_thumbnail(url=team["logo_url"])
        try:
            await player.send(embed=embed, view=view)
        except discord.Forbidden:
            await self.db.execute("UPDATE team_offers SET status='failed' WHERE id=? AND status='pending'", (offer_id,))
            return await interaction.followup.send("❌ تعذر إرسال رسالة خاصة للاعب؛ يجب أن تسمح إعدادات الخصوصية بالرسائل المباشرة.", ephemeral=True)
        self.bot.add_view(view)
        await interaction.followup.send(f"✅ تم إرسال العرض إلى {player.mention} في الخاص.", ephemeral=True)

    @team_group.command(name="settings", description="إعدادات نظام الأندية والمنتخبات")
    @app_commands.checks.has_permissions(administrator=True)
    async def team_settings(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.settings_embed(interaction.guild.id), view=TeamSettingsView(self, interaction.guild.id, interaction.user.id), ephemeral=True)

    async def handle_offer(self, interaction: discord.Interaction, offer_id: int, accept: bool):
        async with self.lock:
            offer = await self.db.fetchone("SELECT o.*,t.name,t.emoji,t.role_id,t.active FROM team_offers o JOIN verified_teams t ON t.id=o.team_id WHERE o.id=?", (offer_id,))
            if not offer or offer["status"] != "pending" or int(offer["player_id"]) != interaction.user.id:
                return await interaction.response.send_message("❌ هذا العرض غير صالح أو تم التعامل معه مسبقًا.", ephemeral=True)
            if float(offer["expires_at"]) <= time.time():
                await self.db.execute("UPDATE team_offers SET status='expired' WHERE id=? AND status='pending'", (offer_id,))
                return await interaction.response.send_message("❌ انتهت صلاحية هذا العرض.", ephemeral=True)
            if not accept:
                await self.db.execute("UPDATE team_offers SET status='rejected' WHERE id=? AND status='pending'", (offer_id,))
                return await interaction.response.edit_message(content="❌ تم رفض عرض الانضمام.", embed=None, view=None)
            if not offer["active"]:
                await self.db.execute("UPDATE team_offers SET status='cancelled' WHERE id=? AND status='pending'", (offer_id,))
                return await interaction.response.send_message("❌ لم يعد هذا الفريق موثقًا.", ephemeral=True)
            current = await self.db.fetchone("SELECT team_id FROM team_members WHERE guild_id=? AND user_id=?", (offer["guild_id"], interaction.user.id))
            if current:
                await self.db.execute("UPDATE team_offers SET status='conflict' WHERE id=? AND status='pending'", (offer_id,))
                return await interaction.response.send_message("❌ أنت مرتبط بالفعل بفريق آخر.", ephemeral=True)
            cfg = await self.config(int(offer["guild_id"]))
            limit = int(cfg.get("max_players") or MAX_TEAM_MEMBERS)
            count = await self.db.fetchone("SELECT COUNT(*) FROM team_members WHERE team_id=?", (offer["team_id"],))
            if int(count[0]) >= limit:
                await self.db.execute("UPDATE team_offers SET status='full' WHERE id=? AND status='pending'", (offer_id,))
                return await interaction.response.send_message("❌ اكتملت قائمة الفريق قبل قبول العرض.", ephemeral=True)
            # UNIQUE(guild_id,user_id) prevents double membership even if two requests race.
            cur = await self.db.execute("INSERT OR IGNORE INTO team_members(team_id,guild_id,user_id,joined_at) VALUES(?,?,?,?)", (offer["team_id"], offer["guild_id"], interaction.user.id, time.time()))
            if cur.rowcount != 1:
                await self.db.execute("UPDATE team_offers SET status='conflict' WHERE id=? AND status='pending'", (offer_id,))
                return await interaction.response.send_message("❌ تعذر قبول العرض لأن حسابك أصبح مرتبطًا بفريق آخر.", ephemeral=True)
            updated = await self.db.execute("UPDATE team_offers SET status='accepted' WHERE id=? AND status='pending'", (offer_id,))
            if updated.rowcount != 1:
                await self.db.execute("DELETE FROM team_members WHERE team_id=? AND guild_id=? AND user_id=?", (offer["team_id"], offer["guild_id"], interaction.user.id))
                return await interaction.response.send_message("❌ تعذر إتمام العرض. حاول مرة أخرى.", ephemeral=True)
        guild = self.bot.get_guild(int(offer["guild_id"]))
        if guild:
            member = guild.get_member(interaction.user.id)
            role = guild.get_role(int(offer["role_id"]))
            if member and role:
                try:
                    await member.add_roles(role, reason="قبول عرض فريق موثق")
                except discord.Forbidden:
                    # Database remains authoritative; notify the player that Discord role assignment needs permission repair.
                    return await interaction.response.edit_message(content=f"⚠️ تم تسجيل انضمامك إلى **{offer['name']}**، لكن تعذر إعطاؤك رتبة الفريق تلقائيًا. يجب على الإدارة رفع رتبة البوت فوق رتبة الفريق.", embed=None, view=None)
        await interaction.response.edit_message(content=f"✅ تم قبول العرض والانضمام إلى **{offer['name']}** {offer['emoji']} بنجاح.", embed=None, view=None)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
