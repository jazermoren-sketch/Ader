"""Ader Ultimate Platform systems.

Adds a unified, database-first layer for security, staff management,
achievements, automations, analytics, invite tracking and an extensible
currency marketplace. Existing cogs remain untouched.
"""
from __future__ import annotations

import json
import random
import re
import time
from collections import defaultdict, deque
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands


SCHEMA = """
CREATE TABLE IF NOT EXISTS ader_module_settings (
    guild_id INTEGER NOT NULL,
    module TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (guild_id, module)
);
CREATE TABLE IF NOT EXISTS ader_security_settings (
    guild_id INTEGER PRIMARY KEY,
    raid_enabled INTEGER NOT NULL DEFAULT 1,
    join_window INTEGER NOT NULL DEFAULT 20,
    join_threshold INTEGER NOT NULL DEFAULT 8,
    lockdown_enabled INTEGER NOT NULL DEFAULT 1,
    log_channel_id INTEGER,
    auto_restore_minutes INTEGER NOT NULL DEFAULT 15
);
CREATE TABLE IF NOT EXISTS ader_security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    actor_id INTEGER,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    data TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ader_security_events ON ader_security_events(guild_id, created_at DESC);
CREATE TABLE IF NOT EXISTS ader_staff_profiles (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0,
    tickets_handled INTEGER NOT NULL DEFAULT 0,
    ratings_sum INTEGER NOT NULL DEFAULT 0,
    ratings_count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS ader_staff_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    staff_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target_id INTEGER,
    data TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ader_staff_points ON ader_staff_profiles(guild_id, points DESC);
CREATE TABLE IF NOT EXISTS ader_achievements (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '🏆'
);
CREATE TABLE IF NOT EXISTS ader_user_achievements (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    achievement_key TEXT NOT NULL,
    earned_at REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id, achievement_key)
);
CREATE TABLE IF NOT EXISTS ader_invite_snapshots (
    guild_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    inviter_id INTEGER,
    uses INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (guild_id, code)
);
CREATE TABLE IF NOT EXISTS ader_invites (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    inviter_id INTEGER,
    code TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_ader_invites_inviter ON ader_invites(guild_id, inviter_id);
CREATE TABLE IF NOT EXISTS ader_automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    trigger TEXT NOT NULL,
    trigger_value TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    action_value TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ader_automations_trigger ON ader_automations(guild_id, trigger, enabled);
CREATE TABLE IF NOT EXISTS ader_automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    user_id INTEGER,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ader_currency_assets (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'external',
    enabled INTEGER NOT NULL DEFAULT 1,
    adapter TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ader_exchange_rates (
    base_code TEXT NOT NULL,
    quote_code TEXT NOT NULL,
    rate REAL NOT NULL,
    fee_percent REAL NOT NULL DEFAULT 2.0,
    updated_by INTEGER,
    updated_at REAL NOT NULL,
    PRIMARY KEY (base_code, quote_code)
);
CREATE TABLE IF NOT EXISTS ader_exchange_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id INTEGER NOT NULL,
    guild_id INTEGER,
    sell_code TEXT NOT NULL,
    sell_amount INTEGER NOT NULL,
    buy_code TEXT NOT NULL,
    buy_amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ader_exchange_open ON ader_exchange_orders(status, sell_code, buy_code);
CREATE TABLE IF NOT EXISTS ader_exchange_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    buyer_id INTEGER NOT NULL,
    seller_id INTEGER NOT NULL,
    sell_code TEXT NOT NULL,
    sell_amount INTEGER NOT NULL,
    buy_code TEXT NOT NULL,
    buy_amount INTEGER NOT NULL,
    settlement TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS ader_currency_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT NOT NULL,
    reference_id TEXT,
    created_at REAL NOT NULL
);
"""

ACHIEVEMENTS = (
    ("first_message", "First Message", "Send your first tracked message.", "💬"),
    ("active_100", "Regular", "Reach 100 tracked messages.", "🔥"),
    ("active_1000", "Veteran", "Reach 1,000 tracked messages.", "👑"),
    ("first_invite", "Recruiter", "Invite your first member.", "🤝"),
    ("staff_star", "Staff Star", "Reach 100 separate staff points.", "⭐"),
    ("ticket_master", "Ticket Master", "Handle 25 tickets.", "🎫"),
)

ASSETS = (
    ("ANORIS", "ANORIS", "🪙", "native", 1, "native", "{}"),
    ("CREDITS", "Credits PROBOT", "💳", "external", 1, None, "{\"provider\":\"PROBOT\"}"),
    ("ADAMC", "AdamC", "🟣", "external", 1, None, "{\"provider\":\"ADAMC\"}"),
    ("FLXCOINS", "FLXCoins", "🟦", "external", 1, None, "{\"provider\":\"FLIXERX\"}"),
    ("VETO", "Veto", "🟠", "external", 1, None, "{\"provider\":\"VETO\"}"),
)


def _now() -> float:
    return time.time()


def _parse_json(value: str | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else (fallback or {})
    except (TypeError, json.JSONDecodeError):
        return fallback or {}


def _fmt_amount(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def _progressive_level(points: int) -> int:
    if points < 100:
        return 1
    if points < 250:
        return 2
    if points < 500:
        return 3
    if points < 1000:
        return 4
    if points < 2500:
        return 5
    return 6 + ((points - 2500) // 1000)


class UltimatePlatform(commands.Cog):
    """Cross-cutting Ader platform systems."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._joins: dict[int, deque[float]] = defaultdict(deque)
        self._invite_cache: dict[int, dict[str, int]] = {}
        self._invite_last_refresh: dict[int, float] = {}
        self._staff_message_cache: dict[tuple[int, int], int] = defaultdict(int)
        self._lockdown_tasks: dict[int, discord.utils.MISSING | Any] = {}

    async def cog_load(self):
        await self.bot.db.connection.executescript(SCHEMA)
        for key, name, description, emoji in ACHIEVEMENTS:
            await self.bot.db.execute(
                "INSERT OR IGNORE INTO ader_achievements(key,name,description,emoji) VALUES(?,?,?,?)",
                (key, name, description, emoji),
            )
        for row in ASSETS:
            await self.bot.db.execute(
                "INSERT OR IGNORE INTO ader_currency_assets(code,name,symbol,kind,enabled,adapter,metadata) VALUES(?,?,?,?,?,?,?)",
                row,
            )

    async def _module_enabled(self, guild_id: int, module: str) -> bool:
        row = await self.bot.db.fetchone(
            "SELECT enabled FROM ader_module_settings WHERE guild_id=? AND module=?",
            (guild_id, module),
        )
        return bool(row[0]) if row else True

    async def _set_module(self, guild_id: int, module: str, enabled: bool):
        await self.bot.db.execute(
            "INSERT INTO ader_module_settings(guild_id,module,enabled,config) VALUES(?,?,?,?) "
            "ON CONFLICT(guild_id,module) DO UPDATE SET enabled=excluded.enabled",
            (guild_id, module, 1 if enabled else 0, "{}"),
        )

    async def _get_security(self, guild_id: int):
        row = await self.bot.db.fetchone("SELECT * FROM ader_security_settings WHERE guild_id=?", (guild_id,))
        if row:
            return row
        await self.bot.db.execute("INSERT OR IGNORE INTO ader_security_settings(guild_id) VALUES(?)", (guild_id,))
        return await self.bot.db.fetchone("SELECT * FROM ader_security_settings WHERE guild_id=?", (guild_id,))

    async def _log_security(self, guild_id: int, event_type: str, severity: str, actor_id: int | None = None, data: dict[str, Any] | None = None):
        await self.bot.db.execute(
            "INSERT INTO ader_security_events(guild_id,actor_id,event_type,severity,data,created_at) VALUES(?,?,?,?,?,?)",
            (guild_id, actor_id, event_type, severity, json.dumps(data or {}, ensure_ascii=False), _now()),
        )
        row = await self._get_security(guild_id)
        log_id = row["log_channel_id"] if row else None
        if not log_id:
            return
        channel = self.bot.get_channel(int(log_id))
        if channel is None:
            return
        colours = {"critical": discord.Colour.red(), "warning": discord.Colour.orange(), "info": discord.Colour.blurple()}
        embed = discord.Embed(title=f"🛡️ Ader Security — {event_type}", colour=colours.get(severity, discord.Colour.blurple()), timestamp=discord.utils.utcnow())
        embed.add_field(name="Severity", value=severity.upper(), inline=True)
        if actor_id:
            embed.add_field(name="Actor", value=f"<@{actor_id}>", inline=True)
        if data:
            compact = "\n".join(f"**{k}:** {v}" for k, v in data.items())[:3900]
            embed.description = compact
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def _award(self, guild_id: int, user_id: int, key: str):
        inserted = await self.bot.db.execute(
            "INSERT OR IGNORE INTO ader_user_achievements(guild_id,user_id,achievement_key,earned_at) VALUES(?,?,?,?)",
            (guild_id, user_id, key, _now()),
        )
        if inserted.rowcount != 1:
            return False
        row = await self.bot.db.fetchone("SELECT name,description,emoji FROM ader_achievements WHERE key=?", (key,))
        if row:
            user = self.bot.get_user(user_id)
            guild = self.bot.get_guild(guild_id)
            if user and guild:
                try:
                    await user.send(f"🏆 **{row['emoji']} {row['name']}**\n{row['description']}\n\nالسيرفر: **{guild.name}**")
                except discord.HTTPException:
                    pass
        return True

    async def _track_staff_message(self, message: discord.Message):
        if message.guild is None:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            return
        if not (member.guild_permissions.manage_messages or member.guild_permissions.kick_members or member.guild_permissions.ban_members or member.guild_permissions.manage_guild):
            return
        key = (message.guild.id, member.id)
        self._staff_message_cache[key] += 1
        count = self._staff_message_cache[key]
        await self.bot.db.execute(
            "INSERT INTO ader_staff_profiles(guild_id,user_id,points,messages,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET messages=messages+1,updated_at=excluded.updated_at",
            (message.guild.id, member.id, 0, 1, _now()),
        )
        if count % 30 == 0:
            await self.bot.db.execute("UPDATE ader_staff_profiles SET points=points+1,updated_at=? WHERE guild_id=? AND user_id=?", (_now(), message.guild.id, member.id))
            row = await self.bot.db.fetchone("SELECT points FROM ader_staff_profiles WHERE guild_id=? AND user_id=?", (message.guild.id, member.id))
            if row and int(row["points"]) >= 100:
                await self._award(message.guild.id, member.id, "staff_star")

    async def _run_automation(self, guild: discord.Guild, user: discord.Member | discord.User, trigger: str, trigger_value: str = ""):
        if not await self._module_enabled(guild.id, "automation"):
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM ader_automations WHERE guild_id=? AND trigger=? AND enabled=1",
            (guild.id, trigger),
        )
        for row in rows:
            expected = str(row["trigger_value"] or "").strip()
            if expected and trigger == "message_contains" and expected.lower() not in trigger_value.lower():
                continue
            action = row["action"]
            value = str(row["action_value"] or "")
            try:
                if action == "role":
                    role = guild.get_role(int(value))
                    if role and isinstance(user, discord.Member) and role not in user.roles:
                        await user.add_roles(role, reason=f"Ader automation: {row['name']}")
                elif action == "dm":
                    await user.send(value.replace("{user}", user.mention).replace("{server}", guild.name))
                elif action == "channel_message":
                    channel = guild.get_channel(int(value.split("|", 1)[0]))
                    text = value.split("|", 1)[1] if "|" in value else "تم تشغيل Automation بواسطة Ader."
                    if channel:
                        await channel.send(text.replace("{user}", user.mention).replace("{server}", guild.name), allowed_mentions=discord.AllowedMentions(users=[user]))
                elif action == "timeout" and isinstance(user, discord.Member):
                    minutes = max(1, min(int(value), 40320))
                    await user.timeout(discord.utils.utcnow() + discord.timedelta(minutes=minutes), reason=f"Ader automation: {row['name']}")
                await self.bot.db.execute("INSERT INTO ader_automation_runs(automation_id,guild_id,user_id,created_at) VALUES(?,?,?,?)", (row["id"], guild.id, user.id, _now()))
            except (discord.HTTPException, ValueError):
                await self.bot.db.execute("INSERT INTO ader_automation_runs(automation_id,guild_id,user_id,created_at) VALUES(?,?,?,?)", (row["id"], guild.id, user.id, _now()))

    async def _lockdown(self, guild: discord.Guild, reason: str):
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels:
            return False
        changed = 0
        everyone = guild.default_role
        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(everyone)
                if overwrite.send_messages is not False:
                    overwrite.send_messages = False
                    await channel.set_permissions(everyone, overwrite=overwrite, reason=f"Ader Security: {reason}")
                    changed += 1
            except discord.HTTPException:
                continue
        await self._log_security(guild.id, "AUTO_LOCKDOWN", "critical", guild.me.id, {"reason": reason, "channels_locked": changed})
        return True

    async def _refresh_invites(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
        except discord.HTTPException:
            return
        cache = {}
        for invite in invites:
            cache[invite.code] = int(invite.uses or 0)
            await self.bot.db.execute(
                "INSERT INTO ader_invite_snapshots(guild_id,code,inviter_id,uses,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(guild_id,code) DO UPDATE SET inviter_id=excluded.inviter_id,uses=excluded.uses,updated_at=excluded.updated_at",
                (guild.id, invite.code, invite.inviter.id if invite.inviter else None, int(invite.uses or 0), _now()),
            )
        self._invite_cache[guild.id] = cache
        self._invite_last_refresh[guild.id] = _now()

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._refresh_invites(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if await self._module_enabled(guild.id, "security"):
            settings = await self._get_security(guild.id)
            window = max(5, int(settings["join_window"]))
            threshold = max(2, int(settings["join_threshold"]))
            q = self._joins[guild.id]
            now = _now()
            q.append(now)
            while q and q[0] < now - window:
                q.popleft()
            if len(q) >= threshold and int(settings["raid_enabled"]):
                await self._lockdown(guild, f"join spike: {len(q)} joins / {window}s") if int(settings["lockdown_enabled"]) else None
                q.clear()
        inviter_id = None
        code = None
        try:
            old = self._invite_cache.get(guild.id, {})
            invites = await guild.invites()
            for invite in invites:
                before = old.get(invite.code, 0)
                after = int(invite.uses or 0)
                if after > before:
                    inviter_id = invite.inviter.id if invite.inviter else None
                    code = invite.code
                    break
            self._invite_cache[guild.id] = {i.code: int(i.uses or 0) for i in invites}
        except discord.HTTPException:
            pass
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO ader_invites(guild_id,user_id,inviter_id,code,created_at) VALUES(?,?,?,?,?)",
            (guild.id, member.id, inviter_id, code, _now()),
        )
        if inviter_id:
            count_row = await self.bot.db.fetchone("SELECT COUNT(*) AS c FROM ader_invites WHERE guild_id=? AND inviter_id=?", (guild.id, inviter_id))
            if count_row and int(count_row["c"]) >= 1:
                await self._award(guild.id, inviter_id, "first_invite")
        await self._run_automation(guild, member, "member_join")
        await self.bot.db.record_analytics(guild.id, "member_join", {"user_id": member.id, "inviter_id": inviter_id})

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        await self._track_staff_message(message)
        await self._run_automation(message.guild, message.author, "message_contains", message.content)
        await self.bot.db.record_analytics(message.guild.id, "message", {"user_id": message.author.id, "channel_id": message.channel.id})
        if message.content.strip():
            await self._award(message.guild.id, message.author.id, "first_message")
            row = await self.bot.db.fetchone("SELECT COUNT(*) AS c FROM analytics WHERE guild_id=? AND type='message' AND json_extract(data,'$.user_id')=?", (message.guild.id, str(message.author.id)))
            count = int(row["c"]) if row else 0
            if count >= 100:
                await self._award(message.guild.id, message.author.id, "active_100")
            if count >= 1000:
                await self._award(message.guild.id, message.author.id, "active_1000")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.guild:
            await self.bot.db.record_analytics(member.guild.id, "member_leave", {"user_id": member.id})

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if await self._module_enabled(channel.guild.id, "security"):
            await self._log_security(channel.guild.id, "CHANNEL_DELETE", "warning", None, {"channel": channel.name, "channel_id": channel.id})

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if await self._module_enabled(role.guild.id, "security"):
            await self._log_security(role.guild.id, "ROLE_DELETE", "warning", None, {"role": role.name, "role_id": role.id})

    @app_commands.command(name="security-status", description="عرض حالة حماية Ader للسيرفر")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def security_status(self, interaction: discord.Interaction):
        row = await self._get_security(interaction.guild_id)
        enabled = await self._module_enabled(interaction.guild_id, "security")
        embed = discord.Embed(title="🛡️ Ader Security Shield", colour=discord.Colour.green() if enabled else discord.Colour.red())
        embed.add_field(name="Module", value="ON ✅" if enabled else "OFF ❌", inline=True)
        embed.add_field(name="Anti-Raid", value="ON ✅" if row["raid_enabled"] else "OFF ❌", inline=True)
        embed.add_field(name="Threshold", value=f"{row['join_threshold']} joins / {row['join_window']}s", inline=True)
        embed.add_field(name="Auto Lockdown", value="ON ✅" if row["lockdown_enabled"] else "OFF ❌", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{row['log_channel_id']}>" if row["log_channel_id"] else "Not set", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="security-config", description="ضبط Anti-Raid وLockdown ديال Ader")
    @app_commands.describe(enabled="تشغيل حماية Ader", threshold="عدد الدخولات خلال النافذة", window="النافذة بالثواني", lockdown="قفل القنوات تلقائياً عند Raid", log_channel="قناة السجلات")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def security_config(self, interaction: discord.Interaction, enabled: bool = True, threshold: app_commands.Range[int, 2, 100] = 8, window: app_commands.Range[int, 5, 120] = 20, lockdown: bool = True, log_channel: discord.TextChannel | None = None):
        await self._set_module(interaction.guild_id, "security", enabled)
        await self.bot.db.execute(
            "INSERT INTO ader_security_settings(guild_id,raid_enabled,join_threshold,join_window,lockdown_enabled,log_channel_id) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET raid_enabled=excluded.raid_enabled,join_threshold=excluded.join_threshold,join_window=excluded.join_window,lockdown_enabled=excluded.lockdown_enabled,log_channel_id=excluded.log_channel_id",
            (interaction.guild_id, 1 if enabled else 0, threshold, window, 1 if lockdown else 0, log_channel.id if log_channel else None),
        )
        await interaction.response.send_message("✅ تظبط **Ader Security Shield** بنجاح.", ephemeral=True)

    @app_commands.command(name="security-unlock", description="فك Lockdown ديال السيرفر")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def security_unlock(self, interaction: discord.Interaction):
        guild = interaction.guild
        changed = 0
        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(guild.default_role)
                if overwrite.send_messages is False:
                    overwrite.send_messages = None
                    await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="Ader Security manual unlock")
                    changed += 1
            except discord.HTTPException:
                continue
        await self._log_security(guild.id, "MANUAL_UNLOCK", "info", interaction.user.id, {"channels": changed})
        await interaction.response.send_message(f"🔓 تحيد Lockdown من **{changed}** قناة.", ephemeral=True)

    @app_commands.command(name="staff", description="عرض Profile ديال Staff")
    @app_commands.describe(member="عضو Staff")
    async def staff(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        row = await self.bot.db.fetchone("SELECT * FROM ader_staff_profiles WHERE guild_id=? AND user_id=?", (interaction.guild_id, member.id))
        if not row:
            await interaction.response.send_message(f"📋 مازال ما تسجلات حتى إحصائيات لـ{member.mention}.", ephemeral=True)
            return
        rating = (row["ratings_sum"] / row["ratings_count"]) if row["ratings_count"] else 0
        embed = discord.Embed(title=f"⭐ Staff Profile — {member.display_name}", colour=discord.Colour.gold())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Points", value=f"**{row['points']:,}**", inline=True)
        embed.add_field(name="Level", value=f"**{_progressive_level(int(row['points']))}**", inline=True)
        embed.add_field(name="Messages", value=f"{row['messages']:,}", inline=True)
        embed.add_field(name="Tickets", value=f"{row['tickets_handled']:,}", inline=True)
        embed.add_field(name="Rating", value=f"{rating:.2f}/5" if rating else "No ratings", inline=True)
        embed.add_field(name="Status", value="Active" if member.voice or member.status != discord.Status.offline else "Offline", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="staff-leaderboard", description="أفضل Staff في السيرفر")
    async def staff_leaderboard(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall("SELECT * FROM ader_staff_profiles WHERE guild_id=? ORDER BY points DESC, messages DESC LIMIT 10", (interaction.guild_id,))
        lines = []
        for i, row in enumerate(rows, 1):
            lines.append(f"**{i}.** <@{row['user_id']}> — **{row['points']:,} pts** • {row['tickets_handled']} tickets")
        embed = discord.Embed(title="🏆 Ader Staff Leaderboard", description="\n".join(lines) if lines else "مازال ما كاين حتى Staff إحصائيات.", colour=discord.Colour.gold())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="achievements", description="عرض Achievements ديالك")
    @app_commands.describe(member="العضو")
    async def achievements(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        rows = await self.bot.db.fetchall("SELECT a.*, u.earned_at FROM ader_user_achievements u JOIN ader_achievements a ON a.key=u.achievement_key WHERE u.guild_id=? AND u.user_id=? ORDER BY u.earned_at", (interaction.guild_id, member.id))
        all_count = await self.bot.db.fetchone("SELECT COUNT(*) AS c FROM ader_achievements")
        description = "\n".join(f"{r['emoji']} **{r['name']}** — {r['description']}" for r in rows) or "مازال ما ربحت حتى Achievement."
        embed = discord.Embed(title=f"🏆 Achievements — {member.display_name}", description=description[:4000], colour=discord.Colour.purple())
        embed.set_footer(text=f"{len(rows)}/{int(all_count['c'])} unlocked")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="invite-leaderboard", description="أفضل أعضاء جابو الناس للسيرفر")
    async def invite_leaderboard(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall("SELECT inviter_id, COUNT(*) AS c FROM ader_invites WHERE guild_id=? AND inviter_id IS NOT NULL GROUP BY inviter_id ORDER BY c DESC LIMIT 10", (interaction.guild_id,))
        lines = [f"**{i}.** <@{r['inviter_id']}> — **{r['c']}** invites" for i, r in enumerate(rows, 1)]
        await interaction.response.send_message(embed=discord.Embed(title="🤝 Invite Leaderboard", description="\n".join(lines) if lines else "مازال ما تسجلات حتى invite.", colour=discord.Colour.blurple()))

    @app_commands.command(name="automation-create", description="إنشاء Automation IF → THEN")
    @app_commands.describe(name="اسم الـAutomation", trigger="Trigger", trigger_value="قيمة Trigger إذا احتاج", action="Action", action_value="قيمة Action")
    @app_commands.choices(trigger=[app_commands.Choice(name="Member Join", value="member_join"), app_commands.Choice(name="Message Contains", value="message_contains")], action=[app_commands.Choice(name="Give Role", value="role"), app_commands.Choice(name="DM User", value="dm"), app_commands.Choice(name="Channel Message", value="channel_message"), app_commands.Choice(name="Timeout", value="timeout")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automation_create(self, interaction: discord.Interaction, name: str, trigger: str, trigger_value: str, action: str, action_value: str):
        if trigger == "message_contains" and not trigger_value.strip():
            await interaction.response.send_message("❌ Message Contains خاصو كلمة/عبارة.", ephemeral=True)
            return
        await self.bot.db.execute("INSERT INTO ader_automations(guild_id,name,trigger,trigger_value,action,action_value,created_at) VALUES(?,?,?,?,?,?,?)", (interaction.guild_id, name[:80], trigger, trigger_value[:500], action, action_value[:1000], _now()))
        await self._set_module(interaction.guild_id, "automation", True)
        await interaction.response.send_message(f"✅ تخلقات Automation **{name}**.\n**IF:** `{trigger}`\n**THEN:** `{action}`", ephemeral=True)

    @app_commands.command(name="automation-list", description="عرض Automations")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automation_list(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall("SELECT * FROM ader_automations WHERE guild_id=? ORDER BY id DESC LIMIT 25", (interaction.guild_id,))
        lines = [f"`#{r['id']}` {'✅' if r['enabled'] else '⛔'} **{r['name']}** — `{r['trigger']} → {r['action']}`" for r in rows]
        await interaction.response.send_message("\n".join(lines) if lines else "ما كايناش Automations.", ephemeral=True)

    @app_commands.command(name="automation-delete", description="حذف Automation")
    @app_commands.describe(automation_id="ID ديال Automation")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automation_delete(self, interaction: discord.Interaction, automation_id: int):
        cur = await self.bot.db.execute("DELETE FROM ader_automations WHERE id=? AND guild_id=?", (automation_id, interaction.guild_id))
        if cur.rowcount == 0:
            await interaction.response.send_message("❌ Automation ما لقيتهاش.", ephemeral=True)
            return
        await interaction.response.send_message("🗑️ تحيدات Automation.", ephemeral=True)

    @app_commands.command(name="currency-info", description="عرض عملات Ader والـMarketplace")
    async def currency_info(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall("SELECT * FROM ader_currency_assets WHERE enabled=1 ORDER BY kind DESC, code")
        lines = []
        for row in rows:
            mode = "Native ✅" if row["kind"] == "native" else "External adapter/manual settlement"
            lines.append(f"{row['symbol']} **{row['code']}** — {row['name']} • {mode}")
        embed = discord.Embed(title="💱 Ader Currency Network", description="\n".join(lines), colour=discord.Colour.gold())
        embed.add_field(name="Native currency", value="**ANORIS** هي العملة الأصلية ديال Ader.")
        embed.add_field(name="External currencies", value="CREDITS / ADAMC / FLXCOINS / VETO مدعومين كأصول قابلة للعرض والتداول، لكن التسوية الفعلية مع بوت خارجي تحتاج Adapter/API أو تحقق يدوي من العملية.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="exchange-rate", description="تعيين سعر صرف بين جوج عملات")
    @app_commands.describe(base="العملة اللي غادي تبيع", quote="العملة اللي غادي تاخذ", rate="كم من quote لكل 1 base", fee_percent="نسبة الرسوم")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def exchange_rate(self, interaction: discord.Interaction, base: str, quote: str, rate: app_commands.Range[float, 0.000001, 1000000000], fee_percent: app_commands.Range[float, 0, 25] = 2.0):
        base = base.upper(); quote = quote.upper()
        valid = await self.bot.db.fetchall("SELECT code FROM ader_currency_assets WHERE code IN (?,?) AND enabled=1", (base, quote))
        if len(valid) != 2 or base == quote:
            await interaction.response.send_message("❌ Currency code غير صالح أو نفس العملة.", ephemeral=True); return
        await self.bot.db.execute("INSERT INTO ader_exchange_rates(base_code,quote_code,rate,fee_percent,updated_by,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(base_code,quote_code) DO UPDATE SET rate=excluded.rate,fee_percent=excluded.fee_percent,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (base, quote, float(rate), float(fee_percent), interaction.user.id, _now()))
        await interaction.response.send_message(f"✅ السعر دابا: **1 {base} = {_fmt_amount(rate)} {quote}** • fee **{fee_percent:g}%**", ephemeral=True)

    @app_commands.command(name="exchange-quote", description="حساب Quote سريع")
    @app_commands.describe(base="العملة", quote="العملة المطلوبة", amount="المبلغ")
    async def exchange_quote(self, interaction: discord.Interaction, base: str, quote: str, amount: app_commands.Range[int, 1, 10**12]):
        base = base.upper(); quote = quote.upper()
        row = await self.bot.db.fetchone("SELECT rate,fee_percent FROM ader_exchange_rates WHERE base_code=? AND quote_code=?", (base, quote))
        if not row:
            await interaction.response.send_message("❌ ما كاينش سعر صرف مضبوط لهاد الزوج.", ephemeral=True); return
        gross = amount * float(row["rate"])
        fee = gross * float(row["fee_percent"]) / 100
        net = gross - fee
        await interaction.response.send_message(f"💱 **{amount:,} {base}** → **{_fmt_amount(net)} {quote}**\nGross: {_fmt_amount(gross)} {quote}\nFee: {_fmt_amount(fee)} {quote}")

    @app_commands.command(name="exchange-list", description="عرض عروض التداول المتاحة")
    async def exchange_list(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall("SELECT * FROM ader_exchange_orders WHERE status='open' AND (expires_at IS NULL OR expires_at>?) ORDER BY id DESC LIMIT 20", (_now(),))
        if not rows:
            await interaction.response.send_message("📭 ما كاين حتى Offer مفتوح دابا.")
            return
        lines = [f"**#{r['id']}** <@{r['seller_id']}> — `{r['sell_amount']:,} {r['sell_code']}` ⇄ `{r['buy_amount']:,} {r['buy_code']}`" + (f" — {r['note']}" if r['note'] else "") for r in rows]
        await interaction.response.send_message(embed=discord.Embed(title="🏪 Ader Exchange Market", description="\n".join(lines), colour=discord.Colour.teal()))

    @app_commands.command(name="exchange-offer", description="نشر Offer لتبديل عملة مقابل عملة")
    @app_commands.describe(sell_code="العملة اللي عندك", sell_amount="المبلغ", buy_code="العملة اللي بغيتي", buy_amount="المبلغ المطلوب", note="ملاحظة")
    async def exchange_offer(self, interaction: discord.Interaction, sell_code: str, sell_amount: app_commands.Range[int, 1, 10**12], buy_code: str, buy_amount: app_commands.Range[int, 1, 10**12], note: str = ""):
        sell_code = sell_code.upper(); buy_code = buy_code.upper()
        valid = await self.bot.db.fetchall("SELECT code FROM ader_currency_assets WHERE code IN (?,?) AND enabled=1", (sell_code, buy_code))
        if len(valid) != 2 or sell_code == buy_code:
            await interaction.response.send_message("❌ العملات غير صالحة.", ephemeral=True); return
        # Native ANORIS offers can be atomically reserved. External balances cannot
        # be escrowed unless their provider adapter is configured, so they remain P2P/manual.
        if sell_code == "ANORIS":
            balance = await self.bot.db.get_balance(interaction.user.id)
            if balance < sell_amount:
                await interaction.response.send_message(f"❌ ما عندكش {sell_amount:,} ANORIS.", ephemeral=True); return
            await self.bot.db.update_global_balance(interaction.user.id, -sell_amount)
            await self.bot.db.execute("INSERT INTO ader_currency_ledger(user_id,code,amount,reason,created_at) VALUES(?,?,?,?,?)", (interaction.user.id, "ANORIS", -sell_amount, "exchange_escrow", _now()))
        await self.bot.db.execute("INSERT INTO ader_exchange_orders(seller_id,guild_id,sell_code,sell_amount,buy_code,buy_amount,status,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (interaction.user.id, interaction.guild_id, sell_code, sell_amount, buy_code, buy_amount, "open", note[:300], _now()))
        await interaction.response.send_message(f"✅ Offer تدار: **{sell_amount:,} {sell_code} ⇄ {buy_amount:,} {buy_code}**\nID ديالو غادي يبان فـ`/exchange-list`.", ephemeral=True)

    @app_commands.command(name="exchange-buy", description="شراء Offer من Marketplace")
    @app_commands.describe(order_id="ID ديال Offer")
    async def exchange_buy(self, interaction: discord.Interaction, order_id: int):
        row = await self.bot.db.fetchone("SELECT * FROM ader_exchange_orders WHERE id=? AND status='open'", (order_id,))
        if not row:
            await interaction.response.send_message("❌ Offer غير موجود أو تسالى.", ephemeral=True); return
        if int(row["seller_id"]) == interaction.user.id:
            await interaction.response.send_message("❌ ما تقدرش تشري Offer ديالك.", ephemeral=True); return
        if row["expires_at"] and float(row["expires_at"]) <= _now():
            await self.bot.db.execute("UPDATE ader_exchange_orders SET status='expired' WHERE id=?", (order_id,))
            await interaction.response.send_message("❌ Offer سالا.", ephemeral=True); return
        if row["buy_code"] == "ANORIS":
            balance = await self.bot.db.get_balance(interaction.user.id)
            if balance < int(row["buy_amount"]):
                await interaction.response.send_message(f"❌ ما عندكش `{row['buy_amount']:,} ANORIS`.", ephemeral=True); return
            await self.bot.db.update_global_balance(interaction.user.id, -int(row["buy_amount"]))
            await self.bot.db.update_global_balance(int(row["seller_id"]), int(row["buy_amount"]))
            await self.bot.db.execute("INSERT INTO ader_currency_ledger(user_id,code,amount,reason,reference_id,created_at) VALUES(?,?,?,?,?,?)", (interaction.user.id, "ANORIS", -int(row["buy_amount"]), "exchange_purchase", str(order_id), _now()))
            await self.bot.db.execute("INSERT INTO ader_currency_ledger(user_id,code,amount,reason,reference_id,created_at) VALUES(?,?,?,?,?,?)", (int(row["seller_id"]), "ANORIS", int(row["buy_amount"]), "exchange_sale", str(order_id), _now()))
        else:
            await self.bot.db.execute("INSERT INTO ader_exchange_transactions(order_id,buyer_id,seller_id,sell_code,sell_amount,buy_code,buy_amount,settlement,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (order_id, interaction.user.id, int(row["seller_id"]), row["sell_code"], int(row["sell_amount"]), row["buy_code"], int(row["buy_amount"]), "manual/external-adapter", "pending", _now()))
            await interaction.response.send_message(f"🧾 تسجل طلب الشراء **#{order_id}**. هاد الصفقة فيها عملة خارجية (**{row['sell_code']} / {row['buy_code']}**)، لذلك Ader ما غاديش يدّعي أنه حوّلها تلقائياً بلا Provider Adapter. خاص التسوية تكون عبر Adapter موثوق أو Manual Settlement.", ephemeral=True)
            return
        await self.bot.db.execute("UPDATE ader_exchange_orders SET status='filled' WHERE id=?", (order_id,))
        if row["sell_code"] == "ANORIS":
            await self.bot.db.update_global_balance(interaction.user.id, int(row["sell_amount"]))
            await self.bot.db.execute("INSERT INTO ader_currency_ledger(user_id,code,amount,reason,reference_id,created_at) VALUES(?,?,?,?,?,?)", (interaction.user.id, "ANORIS", int(row["sell_amount"]), "exchange_purchase", str(order_id), _now()))
        await interaction.response.send_message(f"✅ تمت الصفقة **#{order_id}** بنجاح.")

    @app_commands.command(name="exchange-cancel", description="إلغاء Offer ديالك")
    @app_commands.describe(order_id="ID ديال Offer")
    async def exchange_cancel(self, interaction: discord.Interaction, order_id: int):
        row = await self.bot.db.fetchone("SELECT * FROM ader_exchange_orders WHERE id=? AND seller_id=? AND status='open'", (order_id, interaction.user.id))
        if not row:
            await interaction.response.send_message("❌ Offer غير موجود.", ephemeral=True); return
        await self.bot.db.execute("UPDATE ader_exchange_orders SET status='cancelled' WHERE id=?", (order_id,))
        if row["sell_code"] == "ANORIS":
            await self.bot.db.update_global_balance(interaction.user.id, int(row["sell_amount"]))
            await self.bot.db.execute("INSERT INTO ader_currency_ledger(user_id,code,amount,reason,reference_id,created_at) VALUES(?,?,?,?,?,?)", (interaction.user.id, "ANORIS", int(row["sell_amount"]), "exchange_refund", str(order_id), _now()))
        await interaction.response.send_message("✅ تلغى Offer وترجع الرصيد الأصلي إذا كان ANORIS.", ephemeral=True)

    @app_commands.command(name="server-health", description="Ader Server Health overview")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def server_health(self, interaction: discord.Interaction):
        guild = interaction.guild
        messages = await self.bot.db.fetchone("SELECT COUNT(*) AS c FROM analytics WHERE guild_id=? AND type='message' AND timestamp>=?", (guild.id, _now() - 86400))
        joins = await self.bot.db.fetchone("SELECT COUNT(*) AS c FROM analytics WHERE guild_id=? AND type='member_join' AND timestamp>=?", (guild.id, _now() - 86400))
        leaves = await self.bot.db.fetchone("SELECT COUNT(*) AS c FROM analytics WHERE guild_id=? AND type='member_leave' AND timestamp>=?", (guild.id, _now() - 86400))
        tickets = await self.bot.db.fetchone("SELECT COUNT(*) AS c FROM tickets WHERE guild_id=? AND created_at>=?", (guild.id, _now() - 86400))
        embed = discord.Embed(title="📊 Ader Server Health", colour=discord.Colour.blurple())
        embed.add_field(name="Members", value=f"{guild.member_count:,}", inline=True)
        embed.add_field(name="Messages / 24h", value=f"{int(messages['c']):,}", inline=True)
        embed.add_field(name="Joins / 24h", value=f"{int(joins['c']):,}", inline=True)
        embed.add_field(name="Leaves / 24h", value=f"{int(leaves['c']):,}", inline=True)
        embed.add_field(name="Tickets / 24h", value=f"{int(tickets['c']):,}", inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Boosts", value=str(guild.premium_subscription_count or 0), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="module", description="تشغيل/إيقاف module ديال Ader")
    @app_commands.describe(module="اسم module", enabled="ON/OFF")
    @app_commands.choices(module=[app_commands.Choice(name=x, value=x) for x in ("security", "automation")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def module(self, interaction: discord.Interaction, module: str, enabled: bool):
        await self._set_module(interaction.guild_id, module, enabled)
        await interaction.response.send_message(f"✅ **{module}** = {'ON' if enabled else 'OFF'}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(UltimatePlatform(bot))
