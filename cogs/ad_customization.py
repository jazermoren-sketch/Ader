from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import discord
from discord.ext import commands


EVENTS = {
    "after_ad": "بعد الإعلان",
    "after_giveaway": "بعد القيف أواي",
    "after_image": "بعد الصورة",
    "after_all": "بعد اكتمال الإعلان",
}


def strip_mentions(text: str) -> str:
    """Remove every Discord mention token supplied by the advertiser."""
    text = re.sub(r"<@!?\d+>", "", text or "")
    text = re.sub(r"<@&\d+>", "", text)
    text = text.replace("@everyone", "").replace("@here", "")
    return text.strip()


class MessageModal(discord.ui.Modal):
    content = discord.ui.TextInput(
        label="نص الرسالة",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )
    name = discord.ui.TextInput(label="اسم الرسالة", max_length=80, required=True)

    def __init__(self, cog: "AdCustomization", message_id: int | None = None):
        super().__init__(title="إضافة رسالة" if message_id is None else "تعديل رسالة")
        self.cog = cog
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        name = str(self.name.value).strip()
        content = strip_mentions(str(self.content.value))
        if not content:
            return await interaction.response.send_message("❌ الرسالة فارغة بعد إزالة المنشنات.", ephemeral=True)
        if self.message_id is None:
            await self.cog.db.execute(
                "INSERT INTO ad_custom_messages(guild_id,name,content,event,reply_to,enabled,position) VALUES(?,?,?,?,?,?,?)",
                (interaction.guild.id, name, content, "after_ad", None, 1, await self.cog.next_position(interaction.guild.id)),
            )
            text = "✅ تمت إضافة الرسالة."
        else:
            await self.cog.db.execute(
                "UPDATE ad_custom_messages SET name=?,content=? WHERE id=? AND guild_id=?",
                (name, content, self.message_id, interaction.guild.id),
            )
            text = "✅ تم تعديل الرسالة."
        await interaction.response.send_message(text, ephemeral=True)
        await self.cog.show_panel(interaction, edit=True)


class EventSelect(discord.ui.Select):
    def __init__(self, cog: "AdCustomization"):
        options = [discord.SelectOption(label=v, value=k) for k, v in EVENTS.items()]
        super().__init__(placeholder="اختر متى تُرسل الرسائل", options=options, custom_id="ader:adsettings:event")
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        self.cog.selected_event[interaction.user.id] = self.values[0]
        await interaction.response.send_message(f"✅ تم اختيار: **{EVENTS[self.values[0]]}**", ephemeral=True)


class MessageSelect(discord.ui.Select):
    def __init__(self, cog: "AdCustomization", rows):
        options = [
            discord.SelectOption(label=str(r["name"])[:100], value=str(r["id"]), description=EVENTS.get(str(r["event"]), "مخصص"))
            for r in rows[:25]
        ]
        super().__init__(placeholder="اختر رسالة لتخصيصها", options=options or [discord.SelectOption(label="لا توجد رسائل", value="0")], custom_id="ader:adsettings:message")
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if not self.cog.is_admin(interaction):
            return await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
        try:
            mid = int(self.values[0])
        except ValueError:
            return await interaction.response.send_message("❌ لا توجد رسالة.", ephemeral=True)
        if mid <= 0:
            return await interaction.response.send_message("❌ لا توجد رسالة.", ephemeral=True)
        self.cog.selected_message[interaction.user.id] = mid
        await interaction.response.send_message("✅ تم اختيار الرسالة. استعمل الأزرار لتخصيصها.", ephemeral=True)


class SettingsView(discord.ui.View):
    def __init__(self, cog: "AdCustomization", rows):
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(EventSelect(cog))
        self.add_item(MessageSelect(cog, rows))

    async def guard(self, interaction):
        if not self.cog.is_admin(interaction):
            await interaction.response.send_message("❌ Administrator فقط.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="إضافة رسالة", emoji="➕", style=discord.ButtonStyle.success, row=2, custom_id="ader:adsettings:add")
    async def add(self, interaction, button):
        if await self.guard(interaction):
            await interaction.response.send_modal(MessageModal(self.cog))

    @discord.ui.button(label="تعديل", emoji="📝", style=discord.ButtonStyle.primary, row=2, custom_id="ader:adsettings:edit")
    async def edit(self, interaction, button):
        if not await self.guard(interaction): return
        mid = self.cog.selected_message.get(interaction.user.id)
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        row = await self.cog.db.fetchone("SELECT * FROM ad_custom_messages WHERE id=? AND guild_id=?", (mid, interaction.guild.id))
        if not row: return await interaction.response.send_message("❌ الرسالة غير موجودة.", ephemeral=True)
        modal = MessageModal(self.cog, mid)
        modal.content.default = str(row["content"])
        modal.name.default = str(row["name"])
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="حذف", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="ader:adsettings:delete")
    async def delete(self, interaction, button):
        if not await self.guard(interaction): return
        mid = self.cog.selected_message.get(interaction.user.id)
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        await self.cog.db.execute("DELETE FROM ad_custom_messages WHERE id=? AND guild_id=?", (mid, interaction.guild.id))
        await interaction.response.send_message("✅ تم حذف الرسالة.", ephemeral=True)
        await self.cog.show_panel(interaction, edit=True)

    @discord.ui.button(label="تفعيل/تعطيل", emoji="🔘", style=discord.ButtonStyle.secondary, row=2, custom_id="ader:adsettings:toggle")
    async def toggle(self, interaction, button):
        if not await self.guard(interaction): return
        mid = self.cog.selected_message.get(interaction.user.id)
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        row = await self.cog.db.fetchone("SELECT enabled FROM ad_custom_messages WHERE id=? AND guild_id=?", (mid, interaction.guild.id))
        if not row: return await interaction.response.send_message("❌ الرسالة غير موجودة.", ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET enabled=? WHERE id=?", (0 if int(row["enabled"]) else 1, mid))
        await interaction.response.send_message("✅ تم تغيير حالة الرسالة.", ephemeral=True)
        await self.cog.show_panel(interaction, edit=True)

    @discord.ui.button(label="تخصيص المختارة", emoji="⚙️", style=discord.ButtonStyle.secondary, row=3, custom_id="ader:adsettings:advanced")
    async def advanced(self, interaction, button):
        if not await self.guard(interaction): return
        mid = self.cog.selected_message.get(interaction.user.id)
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        await interaction.response.send_message("استعمل الاختيارات في هذه اللوحة لتحديد الحدث والـreply. سيتم حفظهما فوراً.", ephemeral=True)

    @discord.ui.button(label="حفظ الاختيار", emoji="💾", style=discord.ButtonStyle.success, row=3, custom_id="ader:adsettings:saveevent")
    async def save_event(self, interaction, button):
        if not await self.guard(interaction): return
        mid = self.cog.selected_message.get(interaction.user.id)
        event = self.cog.selected_event.get(interaction.user.id, "after_ad")
        if not mid: return await interaction.response.send_message("❌ اختر رسالة أولاً.", ephemeral=True)
        await self.cog.db.execute("UPDATE ad_custom_messages SET event=? WHERE id=? AND guild_id=?", (event, mid, interaction.guild.id))
        await interaction.response.send_message(f"✅ تم حفظ الحدث: **{EVENTS[event]}**", ephemeral=True)
        await self.cog.show_panel(interaction, edit=True)


class AdCustomization(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.selected_event: dict[int, str] = {}
        self.selected_message: dict[int, int] = {}

    async def cog_load(self):
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ad_custom_messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                event TEXT NOT NULL DEFAULT 'after_ad',
                reply_to INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                position INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0
            )"""
        )
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_ad_custom_guild_event ON ad_custom_messages(guild_id,event,position)")

    def is_admin(self, interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.user.guild_permissions.administrator)

    async def next_position(self, guild_id: int) -> int:
        row = await self.db.fetchone("SELECT COALESCE(MAX(position),0)+1 AS p FROM ad_custom_messages WHERE guild_id=?", (guild_id,))
        return int(row["p"])

    async def show_panel(self, interaction: discord.Interaction, edit=False):
        rows = await self.db.fetchall("SELECT * FROM ad_custom_messages WHERE guild_id=? ORDER BY position,id", (interaction.guild.id,))
        embed = discord.Embed(title="⚙️ تخصيص نظام الإعلانات", description="تحكم كامل في الرسائل التي يرسلها Ader أثناء الإعلان.", colour=discord.Colour.blurple())
        embed.add_field(name="📢 بعد الإعلان", value=str(sum(r["event"] == "after_ad" and int(r["enabled"]) for r in rows)), inline=True)
        embed.add_field(name="🎁 بعد القيف أواي", value=str(sum(r["event"] == "after_giveaway" and int(r["enabled"]) for r in rows)), inline=True)
        embed.add_field(name="🖼️ بعد الصورة", value=str(sum(r["event"] == "after_image" and int(r["enabled"]) for r in rows)), inline=True)
        embed.add_field(name="🔗 بعد الاكتمال", value=str(sum(r["event"] == "after_all" and int(r["enabled"]) for r in rows)), inline=True)
        if rows:
            lines = []
            for r in rows[:15]:
                state = "✅" if int(r["enabled"]) else "⛔"
                lines.append(f"{state} `{r['id']}` **{r['name']}** — {EVENTS.get(r['event'], r['event'])}")
            embed.add_field(name="الرسائل", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="الرسائل", value="لا توجد رسائل مخصصة بعد.", inline=False)
        view = SettingsView(self, rows)
        if edit and interaction.message:
            await interaction.message.edit(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        return

    async def dispatch(self, channel: discord.TextChannel, event: str, context: dict | None = None):
        context = context or {}
        rows = await self.db.fetchall(
            "SELECT * FROM ad_custom_messages WHERE guild_id=? AND event=? AND enabled=1 ORDER BY position,id",
            (channel.guild.id, event),
        )
        sent: dict[int, discord.Message] = {}
        for row in rows:
            content = strip_mentions(str(row["content"]))
            if not content:
                continue
            reference = None
            reply_to = row["reply_to"]
            if reply_to is not None:
                reference = sent.get(int(reply_to))
                if reference:
                    reference = reference.to_reference(fail_if_not_exists=False)
            try:
                msg = await channel.send(content, reference=reference, allowed_mentions=discord.AllowedMentions.none())
                sent[int(row["id"])] = msg
            except discord.HTTPException:
                continue
        return sent

    @discord.app_commands.command(name="ad-settings", description="تخصيص نظام الإعلانات")
    async def ad_settings(self, interaction: discord.Interaction):
        if not self.is_admin(interaction):
            return await interaction.response.send_message("❌ هذا الأمر مخصص للـAdministrator فقط.", ephemeral=True)
        await self.show_panel(interaction)


async def setup(bot):
    await bot.add_cog(AdCustomization(bot))
