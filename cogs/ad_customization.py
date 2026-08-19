from __future__ import annotations

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
    "after_all": "بعد اكتمال الإعلان",
}


def strip_mentions(text: str) -> str:
    text = re.sub(r"<@!?\d+>", "", text or "")
    text = re.sub(r"<@&\d+>", "", text)
    return text.replace("@everyone", "").replace("@here", "").strip()


class MessageModal(discord.ui.Modal):
    def __init__(self, cog, message_id: int | None = None, row=None):
        super().__init__(title="إضافة رسالة" if message_id is None else "تعديل رسالة")
        self.cog = cog
        self.message_id = message_id
        self.name_input = discord.ui.TextInput(label="اسم الرسالة", max_length=80, required=True)
        self.content_input = discord.ui.TextInput(label="نص الرسالة", style=discord.TextStyle.paragraph, max_length=4000, required=True)
        if row is not None:
            self.name_input.default = str(row["name"])
            self.content_input.default = str(row["content"])
        self.add_item(self.name_input)
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        name = str(self.name_input.value).strip()
        content = strip_mentions(str(self.content_input.value))
        if not name or not content:
            return await interaction.response.send_message("❌ الرسالة غير صالحة.", ephemeral=True)
        if self.message_id is None:
            await self.cog.db.execute(
                "INSERT INTO ad_custom_messages(guild_id,name,content,event,reply_to,enabled,position,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (interaction.guild.id, name, content, "after_ad", None, 1, await self.cog.next_position(interaction.guild.id), time.time()),
            )
            await interaction.response.send_message("✅ تمت إضافة الرسالة.", ephemeral=True)
        else:
            await self.cog.db.execute(
                "UPDATE ad_custom_messages SET name=?,content=? WHERE id=? AND guild_id=?",
                (name, content, self.message_id, interaction.guild.id),
            )
            await interaction.response.send_message("✅ تم تعديل الرسالة.", ephemeral=True)


class EventSelect(discord.ui.Select):
    def __init__(self, cog):
        super().__init__(placeholder="اختر توقيت الرسالة", options=[discord.SelectOption(label=v, value=k) for k, v in EVENTS.items()], custom_id="ader:adsettings:event", row=0)
        self.cog = cog

    async def callback(self, interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        self.cog.selected_event[interaction.user.id] = self.values[0]
        await interaction.response.send_message(f"✅ تم اختيار: **{EVENTS[self.values[0]]}**", ephemeral=True)


class MessageSelect(discord.ui.Select):
    def __init__(self, cog, rows):
        options = [discord.SelectOption(label=str(r["name"])[:100], value=str(r["id"]), description=EVENTS.get(str(r["event"]), "مخصص")) for r in rows[:25]]
        if not options:
            options = [discord.SelectOption(label="لا توجد رسائل", value="0")]
        super().__init__(placeholder="اختر رسالة لتخصيصها", options=options, custom_id="ader:adsettings:message", row=1)
        self.cog = cog

    async def callback(self, interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        mid = int(self.values[0]) if self.values[0].isdigit() else 0
        if not mid:
            return await interaction.response.send_message("❌ لا توجد رسالة للاختيار.", ephemeral=True)
        self.cog.selected_message[interaction.user.id] = mid
        await interaction.response.send_message("✅ تم اختيار الرسالة.", ephemeral=True)


class ReplySelect(discord.ui.Select):
    def __init__(self, cog, rows):
        options = [discord.SelectOption(label="بدون Reply", value="none")]
        options += [discord.SelectOption(label=str(r["name"])[:100], value=str(r["id"])) for r in rows[:24]]
        super().__init__(placeholder="اختر رسالة تكون الحالية Reply لها", options=options, custom_id="ader:adsettings:reply", row=2)
        self.cog = cog

    async def callback(self, interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        mid = self.cog.selected_message.get(interaction.user.id)
        if not mid:
            return await interaction.response.send_message("❌ اختر الرسالة الحالية أولاً.", ephemeral=True)
        reply_to = None if self.values[0] == "none" else int(self.values[0])
        if reply_to == mid:
            return await interaction.response.send_message("❌ لا يمكن للرسالة أن تعمل Reply لنفسها.", ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET reply_to=? WHERE id=? AND guild_id=?", (reply_to, mid, interaction.guild.id))
        await interaction.response.send_message("✅ تم حفظ إعداد الـReply.", ephemeral=True)


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog):
        super().__init__(placeholder="اختر رتبة لتفعيل/إزالة صلاحية $اعلان", min_values=1, max_values=1, custom_id="ader:adsettings:role", row=3)
        self.cog = cog

    async def callback(self, interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        role_id = self.values[0].id
        row = await self.cog.db.fetchone("SELECT allowed_roles FROM ad_settings WHERE guild_id=?", (interaction.guild.id,))
        try:
            roles = set(json.loads(row["allowed_roles"] or "[]")) if row else set()
        except Exception:
            roles = set()
        if role_id in roles:
            roles.remove(role_id)
            action = "تمت إزالة الرتبة من الرتب المسموحة"
        else:
            roles.add(role_id)
            action = "تمت إضافة الرتبة إلى الرتب المسموحة"
        await self.cog.save_allowed_roles(interaction.guild.id, roles)
        await interaction.response.send_message(f"✅ {action}.", ephemeral=True)


class SettingsView(discord.ui.View):
    def __init__(self, cog, rows):
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(EventSelect(cog))
        self.add_item(MessageSelect(cog, rows))
        self.add_item(ReplySelect(cog, rows))
        self.add_item(RoleSelect(cog))

    async def guard(self, interaction):
        if not self.cog.is_admin(interaction):
            await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="إضافة", emoji="➕", style=discord.ButtonStyle.success, row=4, custom_id="ader:adsettings:add")
    async def add(self, interaction, button):
        if await self.guard(interaction):
            await interaction.response.send_modal(MessageModal(self.cog))

    @discord.ui.button(label="تعديل", emoji="📝", style=discord.ButtonStyle.primary, row=4, custom_id="ader:adsettings:edit")
    async def edit(self, interaction, button):
        if not await self.guard(interaction):
            return
        mid = self.cog.selected_message.get(interaction.user.id)
        if not mid:
            return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        row = await self.cog.db.fetchone("SELECT * FROM ad_custom_messages WHERE id=? AND guild_id=?", (mid, interaction.guild.id))
        if not row:
            return await interaction.response.send_message("❌ الرسالة غير موجودة.", ephemeral=True)
        await interaction.response.send_modal(MessageModal(self.cog, mid, row))

    @discord.ui.button(label="حذف", emoji="🗑️", style=discord.ButtonStyle.danger, row=4, custom_id="ader:adsettings:delete")
    async def delete(self, interaction, button):
        if not await self.guard(interaction):
            return
        mid = self.cog.selected_message.get(interaction.user.id)
        if not mid:
            return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        await self.cog.db.execute("DELETE FROM ad_custom_messages WHERE id=? AND guild_id=?", (mid, interaction.guild.id))
        self.cog.selected_message.pop(interaction.user.id, None)
        await self.cog.show_panel(interaction, edit=True)

    @discord.ui.button(label="تفعيل", emoji="🔘", style=discord.ButtonStyle.secondary, row=4, custom_id="ader:adsettings:toggle")
    async def toggle(self, interaction, button):
        if not await self.guard(interaction):
            return
        mid = self.cog.selected_message.get(interaction.user.id)
        if not mid:
            return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        row = await self.cog.db.fetchone("SELECT enabled FROM ad_custom_messages WHERE id=? AND guild_id=?", (mid, interaction.guild.id))
        if not row:
            return await interaction.response.send_message("❌ الرسالة غير موجودة.", ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET enabled=? WHERE id=? AND guild_id=?", (0 if int(row["enabled"]) else 1, mid, interaction.guild.id))
        await self.cog.show_panel(interaction, edit=True)

    @discord.ui.button(label="حفظ التوقيت", emoji="💾", style=discord.ButtonStyle.primary, row=4, custom_id="ader:adsettings:eventsave")
    async def save_event(self, interaction, button):
        if not await self.guard(interaction):
            return
        mid = self.cog.selected_message.get(interaction.user.id)
        event = self.cog.selected_event.get(interaction.user.id, "after_ad")
        if not mid:
            return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET event=? WHERE id=? AND guild_id=?", (event, mid, interaction.guild.id))
        await self.cog.show_panel(interaction, edit=True)


class AdCustomization(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.selected_event = {}
        self.selected_message = {}

    async def cog_load(self):
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_custom_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,content TEXT NOT NULL,event TEXT NOT NULL DEFAULT 'after_ad',reply_to INTEGER,enabled INTEGER NOT NULL DEFAULT 1,position INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL DEFAULT 0)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_ad_custom_guild_event ON ad_custom_messages(guild_id,event,position)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ad_settings(guild_id INTEGER PRIMARY KEY, allowed_roles TEXT NOT NULL DEFAULT '[]')")
        cols = await self.db.fetchall("PRAGMA table_info(ad_settings)")
        if "allowed_roles" not in {r[1] for r in cols}:
            await self.db.execute("ALTER TABLE ad_settings ADD COLUMN allowed_roles TEXT NOT NULL DEFAULT '[]'")

    def is_admin(self, interaction):
        return bool(interaction.guild and interaction.user.guild_permissions.administrator)

    async def next_position(self, guild_id):
        row = await self.db.fetchone("SELECT COALESCE(MAX(position),0)+1 AS p FROM ad_custom_messages WHERE guild_id=?", (guild_id,))
        return int(row["p"])

    async def save_allowed_roles(self, guild_id, roles):
        payload = json.dumps(sorted(int(x) for x in roles))
        await self.db.execute("INSERT INTO ad_settings(guild_id,allowed_roles) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allowed_roles=excluded.allowed_roles", (guild_id, payload))

    async def show_panel(self, interaction, edit=False):
        rows = await self.db.fetchall("SELECT * FROM ad_custom_messages WHERE guild_id=? ORDER BY position,id", (interaction.guild.id,))
        embed = discord.Embed(title="⚙️ تخصيص نظام الإعلانات", description="اختر الرسالة والتوقيت، واضبط الـReply والرتب المسموحة من نفس اللوحة.", colour=discord.Colour.blurple())
        for event, label, emoji in [("after_ad", "بعد الإعلان", "📢"), ("after_giveaway", "بعد القيف أواي", "🎁"), ("after_image", "بعد الصورة", "🖼️"), ("after_all", "بعد الاكتمال", "🔗")]:
            count = sum(str(r["event"]) == event and int(r["enabled"]) for r in rows)
            embed.add_field(name=f"{emoji} {label}", value=f"**{count}** رسالة", inline=True)
        embed.add_field(name="👥 الرتب المسموحة", value="اختيار رتبة يضيفها، واختيارها مرة ثانية يزيلها.", inline=False)
        if rows:
            lines = []
            for r in rows[:20]:
                state = "✅" if int(r["enabled"]) else "⛔"
                reply = f" → Reply #{r['reply_to']}" if r["reply_to"] else ""
                lines.append(f"{state} `{r['id']}` **{r['name']}** — {EVENTS.get(r['event'], r['event'])}{reply}")
            embed.add_field(name="📋 الرسائل", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="📋 الرسائل", value="لا توجد رسائل مخصصة بعد.", inline=False)
        view = SettingsView(self, rows)
        if edit and interaction.message:
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="ad-settings", description="تخصيص نظام الإعلانات")
    async def ad_settings(self, interaction: discord.Interaction):
        if not self.is_admin(interaction):
            return await interaction.response.send_message("❌ هذا الأمر متاح لـAdministrator فقط.", ephemeral=True)
        try:
            await self.show_panel(interaction)
        except Exception as exc:
            print(f"ad-settings fatal error: {exc!r}")
            if interaction.response.is_done():
                await interaction.followup.send("❌ وقع خطأ أثناء فتح لوحة الإعدادات. تم تسجيل الخطأ.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ وقع خطأ أثناء فتح لوحة الإعدادات. تم تسجيل الخطأ.", ephemeral=True)

    async def dispatch(self, channel, event, context=None):
        rows = await self.db.fetchall("SELECT * FROM ad_custom_messages WHERE guild_id=? AND event=? AND enabled=1 ORDER BY position,id", (channel.guild.id, event))
        sent = {}
        for row in rows:
            content = strip_mentions(str(row["content"]))
            if not content:
                continue
            reference = sent.get(int(row["reply_to"])) if row["reply_to"] else None
            msg = await channel.send(content, reference=reference, mention_author=False)
            sent[int(row["id"])] = msg
        return sent


async def setup(bot):
    await bot.add_cog(AdCustomization(bot))
