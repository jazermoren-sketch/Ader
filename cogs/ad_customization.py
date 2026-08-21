from __future__ import annotations

import asyncio
import json
import re
import time

import discord
from discord import app_commands
from discord.ext import commands


EVENTS = {
    "after_ad": "بعد الإعلان",
    "after_giveaway": "بعد القيف أواي",
    "after_image": "بعد الصورة",
    "after_all": "بعد الاكتمال",
}


def strip_mentions(text: str) -> str:
    text = re.sub(r"<@!?\d+>", "", text or "")
    text = re.sub(r"<@&\d+>", "", text)
    return text.replace("@everyone", "").replace("@here", "").strip()


def parse_duration(value: str) -> int:
    raw = (value or "").strip().lower().replace(" ", "")
    if raw.isdigit():
        seconds = int(raw) * 60
    else:
        match = re.fullmatch(r"(\d+)([smhd])", raw)
        if not match:
            raise ValueError
        seconds = int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    if seconds < 10 or seconds > 30 * 86400:
        raise ValueError
    return seconds


class GiveawaySettingsModal(discord.ui.Modal, title="إعدادات القيف أواي"):
    enabled = discord.ui.TextInput(label="التفعيل", default="نعم", max_length=10)
    amount = discord.ui.TextInput(label="المبلغ الافتراضي", default="3000000", max_length=20)
    duration = discord.ui.TextInput(label="المدة", default="1h", max_length=20)
    required_server = discord.ui.TextInput(label="Server ID لشرط الدخول (فارغ = لا يوجد)", required=False, max_length=25)

    def __init__(self, cog, row=None):
        super().__init__()
        self.cog = cog
        if row:
            self.enabled.default = "نعم" if int(row["giveaway_enabled"] or 0) else "لا"
            self.amount.default = str(int(row["giveaway_amount"] or 3000000))
            self.duration.default = f"{int(row['giveaway_duration'] or 3600)}s"
            self.required_server.default = str(row["required_guild_id"] or "")

    async def on_submit(self, interaction: discord.Interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        raw = str(self.enabled.value).strip().lower()
        if raw in {"نعم", "yes", "y", "1", "on", "true"}:
            enabled = 1
        elif raw in {"لا", "no", "n", "0", "off", "false"}:
            enabled = 0
        else:
            return await interaction.response.send_message("❌ اكتب نعم أو لا.", ephemeral=True)
        try:
            value = int(str(self.amount.value).replace(",", "").replace(" ", ""))
            duration = parse_duration(str(self.duration.value))
            if value <= 0:
                raise ValueError
            server_id = int(str(self.required_server.value).strip()) if str(self.required_server.value).strip() else None
        except ValueError:
            return await interaction.response.send_message("❌ تحقق من المبلغ والمدة وServer ID.", ephemeral=True)
        if server_id is not None and self.cog.bot.get_guild(server_id) is None:
            return await interaction.response.send_message("❌ البوت خاصو يكون داخل السيرفر المعلن عنه باش يقدر يتحقق من العضوية.", ephemeral=True)
        await self.cog.db.execute(
            """INSERT INTO ad_settings_v2(guild_id,post_message,giveaway_enabled,giveaway_amount,giveaway_duration,giveaway_sponsor_id,image_path,updated_at,required_guild_id)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET
                giveaway_enabled=excluded.giveaway_enabled,
                giveaway_amount=excluded.giveaway_amount,
                giveaway_duration=excluded.giveaway_duration,
                giveaway_sponsor_id=excluded.giveaway_sponsor_id,
                updated_at=excluded.updated_at,
                required_guild_id=excluded.required_guild_id""",
            (interaction.guild.id, "", enabled, value, duration, interaction.user.id, None, time.time(), server_id),
        )
        await interaction.response.send_message("✅ تم حفظ إعدادات القيف أواي وشرط دخول السيرفر.", ephemeral=True)


class MessageModal(discord.ui.Modal):
    def __init__(self, cog, message_id=None, row=None):
        super().__init__(title="إضافة رسالة" if message_id is None else "تعديل رسالة")
        self.cog = cog
        self.message_id = message_id
        self.name_input = discord.ui.TextInput(label="اسم الرسالة", max_length=80)
        self.content_input = discord.ui.TextInput(label="نص الرسالة", style=discord.TextStyle.paragraph, max_length=4000)
        self.add_item(self.name_input)
        self.add_item(self.content_input)
        if row:
            self.name_input.default = str(row["name"])
            self.content_input.default = str(row["content"])

    async def on_submit(self, interaction: discord.Interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        name = str(self.name_input.value).strip()
        content = strip_mentions(str(self.content_input.value))
        if not name or not content:
            return await interaction.response.send_message("❌ الرسالة غير صالحة.", ephemeral=True)
        if self.message_id is None:
            await self.cog.db.execute(
                "INSERT INTO ad_custom_messages(guild_id,name,content,event,reply_to,reply_target,enabled,position,created_at,last_message_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (interaction.guild.id, name, content, "after_ad", None, "ad", 1, await self.cog.next_position(interaction.guild.id), time.time(), None),
            )
        else:
            await self.cog.db.execute("UPDATE ad_custom_messages SET name=?,content=? WHERE id=? AND guild_id=?", (name, content, self.message_id, interaction.guild.id))
        await interaction.response.send_message("✅ تم حفظ الرسالة.", ephemeral=True)


class EventSelect(discord.ui.Select):
    def __init__(self, cog):
        super().__init__(placeholder="اختر توقيت الرسالة", options=[discord.SelectOption(label=v, value=k) for k, v in EVENTS.items()], row=0)
        self.cog = cog

    async def callback(self, interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        self.cog.selected_event[(interaction.guild_id or 0, interaction.user.id)] = self.values[0]
        await interaction.response.send_message(f"✅ تم اختيار: **{EVENTS[self.values[0]]}**", ephemeral=True)


class MessageSelect(discord.ui.Select):
    def __init__(self, cog, rows):
        options = [discord.SelectOption(label=str(r["name"])[:100], value=str(r["id"]), description=EVENTS.get(str(r["event"]), "مخصص")) for r in rows[:25]] or [discord.SelectOption(label="لا توجد رسائل", value="0")]
        super().__init__(placeholder="اختر الرسالة التي تريد تعديلها", options=options, row=1)
        self.cog = cog

    async def callback(self, interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        mid = int(self.values[0]) if self.values[0].isdigit() else 0
        if not mid:
            return await interaction.response.send_message("❌ لا توجد رسالة للاختيار.", ephemeral=True)
        self.cog.selected_message[(interaction.guild_id or 0, interaction.user.id)] = mid
        await interaction.response.send_message("✅ تم اختيار الرسالة.", ephemeral=True)


class ReplyTargetSelect(discord.ui.Select):
    def __init__(self, cog, rows):
        options = [
            discord.SelectOption(label="بدون Reply", value="none"),
            discord.SelectOption(label="Reply للإعلان", value="ad"),
            discord.SelectOption(label="Reply للقيف أواي", value="giveaway"),
            discord.SelectOption(label="Reply للصورة / Attachment", value="image"),
        ]
        for row in rows[:20]:
            options.append(discord.SelectOption(label=f"Reply لـ: {str(row['name'])[:85]}", value=f"custom:{row['id']}"))
        super().__init__(placeholder="شنو بغيتي البوت يدير ليه Reply؟ بلا IDs", options=options[:25], row=2)
        self.cog = cog

    async def callback(self, interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        key = (interaction.guild_id or 0, interaction.user.id)
        mid = self.cog.selected_message.get(key)
        if not mid:
            return await interaction.response.send_message("❌ اختار الرسالة الحالية أولاً.", ephemeral=True)
        target = self.values[0]
        await self.cog.db.execute("UPDATE ad_custom_messages SET reply_target=? WHERE id=? AND guild_id=?", (target, mid, interaction.guild.id))
        await interaction.response.send_message("✅ تم حفظ الـReply بلا ما تحتاج تدخل أي ID.", ephemeral=True)


class SettingsView(discord.ui.View):
    def __init__(self, cog, rows):
        super().__init__(timeout=600)
        self.cog = cog
        self.add_item(EventSelect(cog))
        self.add_item(MessageSelect(cog, rows))
        self.add_item(ReplyTargetSelect(cog, rows))
        self.add_item(RoleSelect(cog))

    def key(self, interaction):
        return (interaction.guild_id or 0, interaction.user.id)

    async def guard(self, interaction):
        if not self.cog.is_admin(interaction):
            await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="إضافة", emoji="➕", style=discord.ButtonStyle.success, row=4)
    async def add(self, interaction, button):
        if await self.guard(interaction):
            await interaction.response.send_modal(MessageModal(self.cog))

    @discord.ui.button(label="تعديل", emoji="📝", style=discord.ButtonStyle.primary, row=4)
    async def edit(self, interaction, button):
        if not await self.guard(interaction):
            return
        mid = self.cog.selected_message.get(self.key(interaction))
        row = await self.cog.db.fetchone("SELECT * FROM ad_custom_messages WHERE id=? AND guild_id=?", (mid, interaction.guild.id)) if mid else None
        if not row:
            return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        await interaction.response.send_modal(MessageModal(self.cog, mid, row))

    @discord.ui.button(label="حذف", emoji="🗑️", style=discord.ButtonStyle.danger, row=4)
    async def delete(self, interaction, button):
        if not await self.guard(interaction):
            return
        mid = self.cog.selected_message.get(self.key(interaction))
        if not mid:
            return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        await self.cog.db.execute("DELETE FROM ad_custom_messages WHERE id=? AND guild_id=?", (mid, interaction.guild.id))
        self.cog.selected_message.pop(self.key(interaction), None)
        await self.cog.show_panel(interaction, edit=True)

    @discord.ui.button(label="↩️ Reply لرسالة ترسلها دابا", style=discord.ButtonStyle.secondary, row=4)
    async def capture_reply(self, interaction, button):
        if not await self.guard(interaction):
            return
        mid = self.cog.selected_message.get(self.key(interaction))
        if not mid:
            return await interaction.response.send_message("❌ اختر رسالة من القائمة أولاً.", ephemeral=True)
        await interaction.response.send_message("📌 صيفط دابا الرسالة أو الصورة/Attachment اللي بغيتي البوت يدير Reply ليها خلال 60 ثانية.", ephemeral=True)
        try:
            target = await self.cog.bot.wait_for("message", timeout=60, check=lambda m: m.author.id == interaction.user.id and m.channel.id == interaction.channel.id)
        except asyncio.TimeoutError:
            return await interaction.followup.send("⌛ سالات المهلة.", ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET reply_target=? WHERE id=? AND guild_id=?", (f"message:{target.id}", mid, interaction.guild.id))
        await interaction.followup.send("✅ تم تحديد الرسالة/Attachment كهدف للـReply بلا ID.", ephemeral=True)

    @discord.ui.button(label="🎁 قيف أواي", style=discord.ButtonStyle.success, row=4)
    async def giveaway(self, interaction, button):
        if await self.guard(interaction):
            row = await self.cog.db.fetchone("SELECT * FROM ad_settings_v2 WHERE guild_id=?", (interaction.guild.id,))
            await interaction.response.send_modal(GiveawaySettingsModal(self.cog, row))


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog):
        super().__init__(placeholder="اختر رتبة مسموح لها بـ$اعلان", min_values=1, max_values=1, row=3)
        self.cog = cog

    async def callback(self, interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        row = await self.cog.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (interaction.guild.id,))
        try:
            roles = {int(x) for x in json.loads(row["allowed_roles"] or "[]")} if row else set()
        except Exception:
            roles = set()
        role_id = self.values[0].id
        if role_id in roles:
            roles.remove(role_id)
            text = "تمت إزالة الرتبة من المسموح لهم"
        else:
            roles.add(role_id)
            text = "تمت إضافة الرتبة إلى المسموح لهم"
        await self.cog.db.execute("INSERT INTO ad_settings(guild_id,allowed_roles) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allowed_roles=excluded.allowed_roles", (interaction.guild.id, json.dumps(sorted(roles))))
        await interaction.response.send_message(f"✅ {text}.", ephemeral=True)


class AdCustomization(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.selected_event = {}
        self.selected_message = {}

    async def _ensure_column(self, table, column, definition):
        columns = {str(row[1]) for row in await self.db.fetchall(f"PRAGMA table_info({table})")}
        if column not in columns:
            await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def cog_load(self):
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_custom_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,content TEXT NOT NULL,event TEXT NOT NULL DEFAULT 'after_ad',reply_to INTEGER,enabled INTEGER NOT NULL DEFAULT 1,position INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL DEFAULT 0)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_ad_custom_guild_event ON ad_custom_messages(guild_id,event,position)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_settings(guild_id INTEGER PRIMARY KEY,allowed_roles TEXT NOT NULL DEFAULT '[]')")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_settings_v2(guild_id INTEGER PRIMARY KEY,post_message TEXT NOT NULL DEFAULT '',giveaway_enabled INTEGER NOT NULL DEFAULT 0,giveaway_amount INTEGER NOT NULL DEFAULT 3000000,giveaway_duration INTEGER NOT NULL DEFAULT 3600,giveaway_sponsor_id INTEGER,image_path TEXT,updated_at REAL NOT NULL DEFAULT 0,required_guild_id INTEGER)")
        await self._ensure_column("ad_custom_messages", "reply_target", "TEXT")
        await self._ensure_column("ad_custom_messages", "last_message_id", "INTEGER")
        await self._ensure_column("ad_settings_v2", "required_guild_id", "INTEGER")

    def is_admin(self, interaction):
        return bool(interaction.guild and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild))

    async def next_position(self, guild_id):
        row = await self.db.fetchone("SELECT COALESCE(MAX(position),0)+1 AS p FROM ad_custom_messages WHERE guild_id=?", (guild_id,))
        return int(row["p"])

    async def show_panel(self, interaction, edit=False):
        rows = await self.db.fetchall("SELECT * FROM ad_custom_messages WHERE guild_id=? ORDER BY position,id", (interaction.guild.id,))
        settings = await self.db.fetchone("SELECT giveaway_enabled,giveaway_amount,giveaway_duration,required_guild_id FROM ad_settings_v2 WHERE guild_id=?", (interaction.guild.id,))
        embed = discord.Embed(title="⚙️ إعدادات الإعلان", description="إدارة الرسائل، توقيتها، الـReply، الرتب، وإعدادات القيف أواي.", colour=discord.Colour.blurple())
        for event, label, emoji in (("after_ad", "بعد الإعلان", "📢"), ("after_giveaway", "بعد القيف أواي", "🎁"), ("after_image", "بعد الصورة", "🖼️"), ("after_all", "بعد الاكتمال", "🔗")):
            count = sum(str(row["event"]) == event and int(row["enabled"]) for row in rows)
            embed.add_field(name=f"{emoji} {label}", value=f"{count} رسالة", inline=True)
        giveaway = "معطل"
        if settings and int(settings["giveaway_enabled"] or 0):
            server = f"\nشرط الدخول: `{settings['required_guild_id']}`" if settings["required_guild_id"] else "\nشرط الدخول: لا يوجد"
            giveaway = f"{int(settings['giveaway_amount']):,} ANOCoin لمدة {int(settings['giveaway_duration']):,}s{server}"
        embed.add_field(name="🎁 القيف أواي", value=giveaway, inline=False)
        if edit:
            await interaction.response.edit_message(embed=embed, view=SettingsView(self, rows))
        else:
            await interaction.response.send_message(embed=embed, view=SettingsView(self, rows), ephemeral=True)

    @app_commands.command(name="ad-settings", description="تخصيص نظام الإعلانات")
    async def ad_settings(self, interaction):
        if not self.is_admin(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        try:
            await self.show_panel(interaction)
        except Exception as exc:
            self.bot.logger.error("ad-settings failed: %s", exc, exc_info=True)
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء فتح الإعدادات. راجع Logs.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء فتح الإعدادات. راجع Logs.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdCustomization(bot))
