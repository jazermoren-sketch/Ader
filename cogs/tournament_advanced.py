"""Advanced configurable tournament engine for Ader.
Uses the existing SQLite tournament tables and keeps unrelated data untouched.
"""
from __future__ import annotations

import io
import json
import math
import time
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedFactory, EmbedColor

FORMATS = ("knockout", "groups", "league", "supercup")
SIZES = (4, 8, 16, 32)
MIN_GROUPS = 4
MAX_GROUPS = 8


def defaults():
    return {
        "format": "knockout",
        "knockout_size": 8,
        "groups": 4,
        "teams_per_group": 4,
        "qualifiers_per_group": 2,
        "best_thirds": 0,
        "third_place": False,
        "league_rounds": 1,
    }


def _safe_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def norm(settings):
    x = defaults()
    if isinstance(settings, dict):
        x.update(settings)

    if x["format"] not in FORMATS:
        x["format"] = "knockout"

    knockout_size = _safe_int(x["knockout_size"], 8)
    x["knockout_size"] = knockout_size if knockout_size in SIZES else 8

    groups = _safe_int(x["groups"], 4)
    x["groups"] = max(MIN_GROUPS, min(MAX_GROUPS, groups))

    teams_per_group = _safe_int(x["teams_per_group"], 4)
    x["teams_per_group"] = max(2, min(16, teams_per_group))

    qualifiers = _safe_int(x["qualifiers_per_group"], 2)
    x["qualifiers_per_group"] = max(1, min(x["teams_per_group"], qualifiers))

    best_thirds = _safe_int(x["best_thirds"], 0)
    best_thirds = max(0, min(x["groups"], best_thirds))

    # Best-third qualification is meaningful only when the tournament actually
    # has a group stage. Never keep a stale best-third value for knockout,
    # league, or super-cup formats.
    if x["format"] != "groups":
        best_thirds = 0
    x["best_thirds"] = best_thirds

    x["third_place"] = bool(x["third_place"])
    x["league_rounds"] = 2 if _safe_int(x["league_rounds"], 1) == 2 else 1
    return x


class SettingsView(discord.ui.View):
    def __init__(self, cog, guild_id, user_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self._sync_buttons()

    async def interaction_check(self, i):
        if i.user.id != self.user_id or not i.user.guild_permissions.administrator:
            await i.response.send_message("❌ غير الأدمن اللي فتح الإعدادات يقدر يبدلها.", ephemeral=True)
            return False
        return True

    def _sync_buttons(self):
        settings = self.cog.get(self.guild_id)
        for child in self.children:
            if getattr(child, "custom_id", None) == "best_thirds":
                child.label = (
                    f"أفضل الثوالث: {settings['best_thirds']}"
                    if settings["format"] == "groups"
                    else "أفضل الثوالث: غير متاح"
                )
                child.disabled = settings["format"] != "groups"
            elif getattr(child, "custom_id", None) == "third_place":
                child.label = f"المركز الثالث: {'تفعيل' if settings['third_place'] else 'تعطيل'}"
            elif getattr(child, "custom_id", None) == "league_legs":
                child.label = (
                    "ذهاب/إياب الدوري: ذهاب وإياب"
                    if settings["league_rounds"] == 2
                    else "ذهاب/إياب الدوري: ذهاب"
                )

    @discord.ui.select(
        placeholder="نظام البطولة",
        row=0,
        options=[
            discord.SelectOption(label="كأس إقصائية", value="knockout", emoji="🏆"),
            discord.SelectOption(label="مجموعات + إقصائيات", value="groups", emoji="📊"),
            discord.SelectOption(label="دوري", value="league", emoji="🏟️"),
            discord.SelectOption(label="كأس السوبر", value="supercup", emoji="⚡"),
        ],
    )
    async def fmt(self, i, s):
        settings = self.cog.get(self.guild_id)
        settings["format"] = s.values[0]
        # Changing away from a group stage immediately clears best thirds.
        if settings["format"] != "groups":
            settings["best_thirds"] = 0
        await self.cog.save(self.guild_id)
        self._sync_buttons()
        await i.response.edit_message(embed=self.cog.embed(self.guild_id), view=self)

    @discord.ui.select(
        placeholder="حجم الإقصائيات",
        row=1,
        options=[discord.SelectOption(label=f"{n} مشارك", value=str(n)) for n in SIZES],
    )
    async def size(self, i, s):
        self.cog.get(self.guild_id)["knockout_size"] = int(s.values[0])
        await self.cog.save(self.guild_id)
        self._sync_buttons()
        await i.response.edit_message(embed=self.cog.embed(self.guild_id), view=self)

    @discord.ui.button(label="أفضل الثوالث: غير متاح", style=discord.ButtonStyle.secondary, row=2, custom_id="best_thirds")
    async def thirds(self, i, b):
        settings = self.cog.get(self.guild_id)
        if settings["format"] != "groups":
            return await i.response.send_message(
                "❌ أفضل الثوالث متاح غير فـالبطولات اللي فيها **دور المجموعات**.",
                ephemeral=True,
            )
        current = settings.get("best_thirds", 0)
        # Cycle 0 -> 1 -> ... -> number of groups -> 0.
        # This allows the admin to reduce the value as well as increase it.
        settings["best_thirds"] = 0 if current >= settings["groups"] else current + 1
        await self.cog.save(self.guild_id)
        self._sync_buttons()
        await i.response.edit_message(embed=self.cog.embed(self.guild_id), view=self)

    @discord.ui.button(label="المركز الثالث: تعطيل", style=discord.ButtonStyle.secondary, row=2, custom_id="third_place")
    async def third(self, i, b):
        settings = self.cog.get(self.guild_id)
        settings["third_place"] = not settings.get("third_place", False)
        await self.cog.save(self.guild_id)
        self._sync_buttons()
        await i.response.edit_message(embed=self.cog.embed(self.guild_id), view=self)

    @discord.ui.button(label="ذهاب/إياب الدوري: ذهاب", style=discord.ButtonStyle.secondary, row=3, custom_id="league_legs")
    async def legs(self, i, b):
        settings = self.cog.get(self.guild_id)
        settings["league_rounds"] = 1 if settings.get("league_rounds", 1) == 2 else 2
        await self.cog.save(self.guild_id)
        self._sync_buttons()
        await i.response.edit_message(embed=self.cog.embed(self.guild_id), view=self)

    @discord.ui.button(label="إغلاق", style=discord.ButtonStyle.danger, row=3)
    async def close(self, i, b):
        self.stop()
        await i.response.edit_message(view=None)


class DeleteView(discord.ui.View):
    def __init__(self, cog, guild_id, tid, user_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id
        self.tid = tid
        self.user_id = user_id

    async def interaction_check(self, i):
        if i.user.id != self.user_id or not i.user.guild_permissions.administrator:
            await i.response.send_message("❌ غير الأدمن اللي طلب الحذف يقدر يستعمل الأزرار.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="حذف", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete(self, i, b):
        ok = await self.cog.delete_tournament(self.guild_id, self.tid)
        self.stop()
        await i.response.edit_message(
            content="🗑️ تم حذف البطولة وبياناتها المرتبطة بها." if ok else "❌ فشل الحذف.",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, i, b):
        self.stop()
        await i.response.edit_message(content="✅ تم إلغاء الحذف.", embed=None, view=None)


class AdvancedTournament(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.settings = {}

    async def cog_load(self):
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS tournament_settings(guild_id INTEGER PRIMARY KEY,settings_json TEXT NOT NULL,updated_at REAL NOT NULL)"
        )
        await self.db.execute("ALTER TABLE tournaments ADD COLUMN format TEXT NOT NULL DEFAULT 'knockout'") if not await self._has_col("tournaments", "format") else None
        await self.db.execute("ALTER TABLE tournaments ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'") if not await self._has_col("tournaments", "settings_json") else None
        await self.db.execute("ALTER TABLE tournament_participants ADD COLUMN group_no INTEGER") if not await self._has_col("tournament_participants", "group_no") else None
        await self.db.execute("ALTER TABLE tournament_participants ADD COLUMN points INTEGER NOT NULL DEFAULT 0") if not await self._has_col("tournament_participants", "points") else None
        for c, d in (("wins", "INTEGER NOT NULL DEFAULT 0"), ("draws", "INTEGER NOT NULL DEFAULT 0"), ("losses", "INTEGER NOT NULL DEFAULT 0"), ("played", "INTEGER NOT NULL DEFAULT 0"), ("goal_diff", "INTEGER NOT NULL DEFAULT 0")):
            if not await self._has_col("tournament_participants", c):
                await self.db.execute(f"ALTER TABLE tournament_participants ADD COLUMN {c} {d}")
        for c, d in (("score1", "INTEGER"), ("score2", "INTEGER"), ("stage", "TEXT NOT NULL DEFAULT 'knockout'")):
            if not await self._has_col("tournament_matches", c):
                await self.db.execute(f"ALTER TABLE tournament_matches ADD COLUMN {c} {d}")
        rows = await self.db.fetchall("SELECT guild_id,settings_json FROM tournament_settings")
        for r in rows:
            try:
                self.settings[int(r[0])] = norm(json.loads(r[1]))
            except Exception:
                pass

    async def _has_col(self, t, c):
        return any(r[1] == c for r in await self.db.fetchall(f"PRAGMA table_info({t})"))

    def get(self, g):
        self.settings[g] = norm(self.settings.get(g, defaults()))
        return self.settings[g]

    async def save(self, g):
        s = norm(self.settings.get(g, defaults()))
        self.settings[g] = s
        await self.db.execute(
            "INSERT INTO tournament_settings VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at",
            (g, json.dumps(s, ensure_ascii=False), time.time()),
        )

    def embed(self, g):
        s = self.get(g)
        best_thirds = str(s["best_thirds"]) if s["format"] == "groups" and s["best_thirds"] else "تعطيل"
        if s["format"] != "groups":
            best_thirds = "غير متاح — خاص دور المجموعات"
        return EmbedFactory.create(
            "⚙️ إعدادات البطولات",
            f"**النظام:** `{s['format']}`\n"
            f"**حجم الإقصائيات:** `{s['knockout_size']}`\n"
            f"**المجموعات:** `{s['groups']} × {s['teams_per_group']}`\n"
            f"**المتأهلون:** `{s['qualifiers_per_group']}` لكل مجموعة\n"
            f"**أفضل الثوالث:** `{best_thirds}`\n"
            f"**المركز الثالث:** `{'تفعيل' if s['third_place'] else 'تعطيل'}`\n"
            f"**الدوري:** `{'ذهاب وإياب' if s['league_rounds'] == 2 else 'ذهاب'}`",
            color=EmbedColor.ECONOMY,
        )

    async def tournament(self, g, tid):
        return await self.db.fetchone("SELECT * FROM tournaments WHERE id=? AND guild_id=?", (tid, g))

    async def manager(self, i, t):
        return bool(t and (i.user.id == t["creator_id"] or i.user.guild_permissions.administrator))

    async def match(self, tid, r, n, p1, p2, stage):
        await self.db.execute(
            "INSERT OR IGNORE INTO tournament_matches(tournament_id,round_no,match_no,player1_id,player2_id,status,stage,created_at) VALUES(?,?,?,?,?,'pending',?,?,?)",
            (tid, r, n, p1, p2, stage, time.time()),
        )

    async def create_round(self, tid, ids, r=1, stage="knockout"):
        for n in range(0, len(ids), 2):
            await self.match(tid, r, n // 2 + 1, ids[n], ids[n + 1], stage)

    async def advance_knockout(self, t, r):
        ms = await self.db.fetchall(
            "SELECT * FROM tournament_matches WHERE tournament_id=? AND stage='knockout' AND round_no=? ORDER BY match_no",
            (t["id"], r),
        )
        if not ms or any(m["status"] != "completed" for m in ms):
            return
        w = [m["winner_id"] for m in ms]
        if len(w) == 1:
            return await self.finish(t, w[0])
        await self.create_round(t["id"], w, r + 1, "knockout")

    async def finish(self, t, winner):
        cur = await self.db.execute(
            "UPDATE tournaments SET status='completed',winner_id=?,ended_at=? WHERE id=? AND status='running'",
            (winner, time.time(), t["id"]),
        )
        if cur.rowcount == 1:
            await self.db.add_balance(winner, t["guild_id"], int(t["reward"]))

    async def delete_tournament(self, g, tid):
        t = await self.tournament(g, tid)
        if not t:
            return False
        try:
            await self.db.connection.execute("BEGIN")
            await self.db.connection.execute("DELETE FROM tournament_matches WHERE tournament_id=?", (tid,))
            await self.db.connection.execute("DELETE FROM tournament_participants WHERE tournament_id=?", (tid,))
            cur = await self.db.connection.execute("DELETE FROM tournaments WHERE id=? AND guild_id=?", (tid, g))
            if cur.rowcount != 1:
                await self.db.connection.rollback()
                return False
            await self.db.connection.commit()
            return True
        except Exception:
            try:
                await self.db.connection.rollback()
            except Exception:
                pass
            return False

    @app_commands.command(name="tournament-settings", description="إعدادات كاملة للبطولات")
    @app_commands.checks.has_permissions(administrator=True)
    async def settings_cmd(self, i):
        await i.response.send_message(embed=self.embed(i.guild.id), view=SettingsView(self, i.guild.id, i.user.id), ephemeral=True)

    @app_commands.command(name="tournament-delete", description="حذف بطولة نهائيا")
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_cmd(self, i, tournament_id: int):
        t = await self.tournament(i.guild.id, tournament_id)
        if not t:
            return await i.response.send_message("❌ البطولة غير موجودة.", ephemeral=True)
        n = await self.db.fetchone("SELECT COUNT(*) c FROM tournament_participants WHERE tournament_id=?", (tournament_id,))
        m = await self.db.fetchone("SELECT COUNT(*) c FROM tournament_matches WHERE tournament_id=?", (tournament_id,))
        e = EmbedFactory.create(
            "⚠️ هل أنت متأكد من حذف هذه البطولة؟",
            f"🏆 **{t['name']}**\n🆔 `{tournament_id}`\n👥 `{n['c']}` لاعبين\n⚔️ `{m['c']}` مباريات\n\n**لا يمكن استرجاعها بعد الحذف.**",
            color=EmbedColor.ERROR,
        )
        await i.response.send_message(embed=e, view=DeleteView(self, i.guild.id, tournament_id, i.user.id), ephemeral=True)

    @app_commands.command(name="tournament-view", description="عرض البطولة والترتيب والمراحل")
    async def view_cmd(self, i, tournament_id: int):
        t = await self.tournament(i.guild.id, tournament_id)
        if not t:
            return await i.response.send_message("❌ البطولة غير موجودة.", ephemeral=True)
        rows = await self.db.fetchall(
            "SELECT * FROM tournament_participants WHERE tournament_id=? ORDER BY points DESC,wins DESC,goal_diff DESC,joined_at",
            (tournament_id,),
        )
        lines = [f"`{n:02}` <@{r['user_id']}> • **{r['points']} pts** • W {r['wins']} • P {r['played']}" for n, r in enumerate(rows, 1)]
        e = EmbedFactory.create(
            f"🏆 {t['name']}",
            f"النظام: `{t['format']}`\nالحالة: `{t['status']}`\n\n" + "\n".join(lines[:50]),
            color=EmbedColor.ECONOMY,
        )
        await i.response.send_message(embed=e)

    @app_commands.command(name="tournament-stage-report", description="تسجيل نتيجة أي مباراة من مراحل البطولة")
    @app_commands.describe(tournament_id="Tournament ID", match_id="Match ID", winner="الفائز")
    async def report(self, i, tournament_id: int, match_id: int, winner: discord.Member):
        t = await self.tournament(i.guild.id, tournament_id)
        if not await self.manager(i, t) or t["status"] != "running":
            return await i.response.send_message("❌ البطولة أو الصلاحية غير صالحة.", ephemeral=True)
        m = await self.db.fetchone("SELECT * FROM tournament_matches WHERE id=? AND tournament_id=?", (match_id, tournament_id))
        if not m or m["status"] != "pending":
            return await i.response.send_message("❌ الـMatch غير موجود أو تسجل من قبل.", ephemeral=True)
        if winner.id not in {m["player1_id"], m["player2_id"]}:
            return await i.response.send_message("❌ الفائز خاصو يكون من لاعبي الـMatch.", ephemeral=True)
        loser = m["player2_id"] if winner.id == m["player1_id"] else m["player1_id"]
        cur = await self.db.execute(
            "UPDATE tournament_matches SET winner_id=?,status='completed',reported_by=? WHERE id=? AND status='pending'",
            (winner.id, i.user.id, match_id),
        )
        if cur.rowcount != 1:
            return await i.response.send_message("⚠️ النتيجة تسجلات فـنفس الوقت.", ephemeral=True)
        if str(m["stage"]).startswith("group") or m["stage"] == "league":
            await self.db.execute(
                "UPDATE tournament_participants SET played=played+1,wins=wins+1,points=points+3 WHERE tournament_id=? AND user_id=?",
                (tournament_id, winner.id),
            )
            await self.db.execute(
                "UPDATE tournament_participants SET played=played+1,losses=losses+1 WHERE tournament_id=? AND user_id=?",
                (tournament_id, loser),
            )
        else:
            await self.db.execute(
                "UPDATE tournament_participants SET eliminated=1 WHERE tournament_id=? AND user_id=? AND eliminated=0",
                (tournament_id, loser),
            )
        pending = await self.db.fetchone("SELECT 1 FROM tournament_matches WHERE tournament_id=? AND status='pending' LIMIT 1", (tournament_id,))
        if not pending and m["stage"] == "knockout":
            await self.advance_knockout(t, int(m["round_no"]))
        if not pending and m["stage"] == "supercup":
            await self.finish(t, winner.id)
        if not pending and m["stage"] == "league":
            top = await self.db.fetchone(
                "SELECT user_id FROM tournament_participants WHERE tournament_id=? ORDER BY points DESC,wins DESC,goal_diff DESC,played ASC,joined_at ASC LIMIT 1",
                (tournament_id,),
            )
            await self.finish(t, int(top["user_id"]))
        await i.response.send_message(f"✅ تسجلات النتيجة: {winner.mention} ربح.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdvancedTournament(bot))
