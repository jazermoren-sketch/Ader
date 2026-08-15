from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

SET2 = "<:set2:1521929996787257556>"
GGG = "<:ggg:1519567521857015928>"

DEFAULT_QUESTIONS = [
    {"label": "ما اسمك", "required": True, "paragraph": False},
    {"label": "كم عمرك", "required": True, "paragraph": False},
    {"label": "كم ساعة ناشط باليوم", "required": True, "paragraph": False},
    {"label": "كيف ستفيد السيرفر", "required": True, "paragraph": False},
    {"label": "اكتب خبراتك في الديسكورد", "required": True, "paragraph": True},
]

STYLE_MAP = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


def panel_default(number: int) -> dict:
    return {
        "title": f"تقديم {number}",
        "questions": [dict(q) for q in DEFAULT_QUESTIONS],
        "button_label": f"تقديم {number}",
        "button_emoji": None,
        "button_style": "primary",
        "image": None,
        "results": None,
        "accept_role": None,
        "reject_role": None,
    }


class AppV4(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.answers: dict[tuple[int, int, int], list[str]] = {}

    async def cog_load(self):
        await self.bot.db.execute(
            """CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                panel INTEGER NOT NULL,
                status TEXT NOT NULL,
                answers TEXT NOT NULL,
                created_at REAL NOT NULL,
                reviewer_id INTEGER,
                reason TEXT
            )"""
        )

    async def get_panel(self, guild_id: int, number: int) -> dict:
        row = await self.bot.db.fetchone(
            "SELECT value FROM settings WHERE guild_id=? AND key=?",
            (guild_id, f"app_panel_{number}"),
        )
        panel = panel_default(number)
        if row:
            try:
                saved = json.loads(row["value"])
                if isinstance(saved, dict):
                    panel.update(saved)
            except (ValueError, TypeError):
                pass
        if not isinstance(panel.get("questions"), list) or not panel["questions"]:
            panel["questions"] = [dict(q) for q in DEFAULT_QUESTIONS]
        return panel

    async def save_panel(self, guild_id: int, number: int, panel: dict):
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO settings(guild_id,key,value) VALUES(?,?,?)",
            (guild_id, f"app_panel_{number}", json.dumps(panel, ensure_ascii=False)),
        )

    async def send_error(self, interaction: discord.Interaction, text: str):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="تقديم", description="إعداد تقديمات الإدارة")
    @app_commands.describe(اخفاء="إخفاء لوحة إعداد التقديم")
    @app_commands.checks.has_permissions(administrator=True)
    async def تقديم(self, interaction: discord.Interaction, اخفاء: bool = False):
        await interaction.response.send_message(
            embed=discord.Embed(description=f"** اختر التقديم الذي تود التعديل عليه {GGG}**"),
            view=PanelPicker(self),
            ephemeral=اخفاء,
        )

    async def open_settings(self, interaction: discord.Interaction, number: int):
        await interaction.response.edit_message(
            embed=discord.Embed(description=f"** اعدادات تقديم {number} {SET2} من هنا **"),
            view=PanelSettings(self, number),
        )

    async def edit_panel(self, interaction: discord.Interaction, number: int, action: str):
        panel = await self.get_panel(interaction.guild.id, number)
        if action == "title":
            await interaction.response.send_modal(TextEditModal(self, number, "title", "تعديل عنوان Panel", "عنوان Panel", panel["title"]))
        elif action == "button_name":
            await interaction.response.send_modal(TextEditModal(self, number, "button_label", "اسم زر التقديم", "اسم الزر", panel["button_label"]))
        elif action == "emoji":
            await interaction.response.send_modal(TextEditModal(self, number, "button_emoji", "إيموجي التقديم", "Unicode أو <:name:id>", panel.get("button_emoji") or ""))
        elif action == "button":
            await interaction.response.send_message(
                embed=discord.Embed(description=f"** إعدادات زر تقديم {number} {SET2} **"),
                view=ButtonSettings(self, number), ephemeral=True,
            )
        elif action == "questions":
            await interaction.response.send_message(
                embed=discord.Embed(description=f"** تعديل أسئلة تقديم {number} {SET2} **"),
                view=QuestionSettings(self, number), ephemeral=True,
            )
        elif action == "results":
            await interaction.response.send_message(
                "**اختر الروم الذي ستصل إليه سجلات ونتائج التقديم**",
                view=ChannelPicker(self, number), ephemeral=True,
            )
        elif action == "roles":
            await interaction.response.send_message(
                embed=discord.Embed(description=f"** إعدادات رتب تقديم {number} {SET2} **"),
                view=RoleSettings(self, number), ephemeral=True,
            )
        elif action == "image":
            await interaction.response.send_message("أرسل صورة التقديم هنا خلال **5 دقائق**.", ephemeral=True)
            try:
                message = await self.bot.wait_for(
                    "message", timeout=300,
                    check=lambda m: m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and bool(m.attachments),
                )
                attachment = message.attachments[0]
                if not (attachment.content_type or "").startswith("image/"):
                    return await interaction.followup.send("❌ الملف المرسل ليس صورة.", ephemeral=True)
                panel["image"] = attachment.url
                await self.save_panel(interaction.guild.id, number, panel)
                await interaction.followup.send("**تم تحديد صورة لي التقديم**", ephemeral=True)
            except asyncio.TimeoutError:
                await interaction.followup.send("⌛ انتهت مهلة 5 دقائق.", ephemeral=True)
        elif action == "back":
            await interaction.response.edit_message(
                embed=discord.Embed(description=f"** اختر التقديم الذي تود التعديل عليه {GGG}**"),
                view=PanelPicker(self),
            )
        elif action == "send":
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                return await self.send_error(interaction, "❌ خاص إرسال التقديم من روم نصي.")
            embed = discord.Embed(title=panel["title"], description="اضغط الزر لبدء التقديم.")
            if panel.get("image"):
                embed.set_image(url=panel["image"])
            await channel.send(embed=embed, view=PublishedApplication(self, number, panel))
            await interaction.response.send_message("✅ تم إرسال التقديم في هذا الروم بنجاح.", ephemeral=True)

    async def save_question(self, interaction: discord.Interaction, number: int, index: int, label: str, required: bool, paragraph: bool):
        panel = await self.get_panel(interaction.guild.id, number)
        while len(panel["questions"]) <= index:
            panel["questions"].append({"label": "سؤال جديد", "required": True, "paragraph": False})
        panel["questions"][index] = {"label": label.strip() or f"سؤال {index + 1}", "required": required, "paragraph": paragraph}
        await self.save_panel(interaction.guild.id, number, panel)
        await interaction.response.send_message("✅ تم حفظ السؤال.", ephemeral=True)

    async def select_results(self, interaction: discord.Interaction, number: int, channel_id: int):
        panel = await self.get_panel(interaction.guild.id, number)
        panel["results"] = channel_id
        await self.save_panel(interaction.guild.id, number, panel)
        await interaction.response.send_message("✅ تم تحديد روم سجلات التقديمات بنجاح.", ephemeral=True)

    async def set_role(self, interaction: discord.Interaction, number: int, key: str, role_id: Optional[int]):
        panel = await self.get_panel(interaction.guild.id, number)
        panel[key] = role_id
        await self.save_panel(interaction.guild.id, number, panel)
        await interaction.response.send_message("✅ تم حفظ إعداد الرتبة.", ephemeral=True)

    async def start_application(self, interaction: discord.Interaction, number: int):
        panel = await self.get_panel(interaction.guild.id, number)
        questions = panel["questions"]
        self.answers[(interaction.guild.id, interaction.user.id, number)] = []
        await interaction.response.send_modal(ApplicationModal(self, number, 0, questions))

    async def collect(self, interaction: discord.Interaction, number: int, questions: list[dict]):
        key = (interaction.guild.id, interaction.user.id, number)
        values = self.answers.get(key, []) + [str(field.value) for field in interaction.fields]
        self.answers[key] = values
        if len(values) < len(questions):
            return await interaction.response.send_modal(ApplicationModal(self, number, len(values), questions))
        self.answers.pop(key, None)
        panel = await self.get_panel(interaction.guild.id, number)
        cursor = await self.bot.db.execute(
            "INSERT INTO applications(guild_id,user_id,panel,status,answers,created_at) VALUES(?,?,?,?,?,?)",
            (interaction.guild.id, interaction.user.id, number, "pending", json.dumps(values, ensure_ascii=False), time.time()),
        )
        application_id = cursor.lastrowid
        results = interaction.guild.get_channel(panel.get("results") or 0)
        if results and isinstance(results, discord.TextChannel):
            embed = discord.Embed(title=f"تقديم {number}", description=f"المتقدم: {interaction.user.mention}\nالحالة: **قيد المراجعة**")
            for question, answer in zip(questions, values):
                embed.add_field(name=question["label"][:256], value=answer[:1024] or "—", inline=False)
            await results.send(embed=embed, view=ReviewView(self, application_id))
        await interaction.response.send_message("✅ تم إرسال التقديم بنجاح.", ephemeral=True)

    async def review(self, interaction: discord.Interaction, application_id: int, accepted: bool, reason: Optional[str] = None):
        row = await self.bot.db.fetchone("SELECT * FROM applications WHERE id=?", (application_id,))
        if not row or row["status"] != "pending":
            return await self.send_error(interaction, "❌ تمت مراجعة هذا التقديم مسبقاً.")
        panel = await self.get_panel(interaction.guild.id, row["panel"])
        status = "accepted" if accepted else "rejected"
        await self.bot.db.execute(
            "UPDATE applications SET status=?, reviewer_id=?, reason=? WHERE id=?",
            (status, interaction.user.id, reason, application_id),
        )
        role_id = panel.get("accept_role") if accepted else panel.get("reject_role")
        member = interaction.guild.get_member(row["user_id"])
        if member and role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                try:
                    await member.add_roles(role, reason="مراجعة تقديم الإدارة")
                except discord.HTTPException:
                    pass
        if interaction.message:
            try:
                embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(title=f"تقديم {row['panel']}")
                embed.description = f"المتقدم: <@{row['user_id']}>\nالحالة: **{'مقبول' if accepted else 'مرفوض'}**\nالمراجع: {interaction.user.mention}" + (f"\nالسبب: {reason}" if reason else "")
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass
        await interaction.response.send_message("✅ تمت مراجعة التقديم بنجاح.", ephemeral=True)


class PanelPicker(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=600)
        for number in range(1, 4):
            self.add_item(PanelButton(cog, number))


class PanelButton(discord.ui.Button):
    def __init__(self, cog, number):
        super().__init__(label=f"تقديم {number}", style=discord.ButtonStyle.primary, emoji=SET2)
        self.cog, self.number = cog, number
    async def callback(self, interaction):
        await self.cog.open_settings(interaction, self.number)


class PanelSettings(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=600)
        actions = [("تعديل عنوان Panel", "title"), ("تعديل الأسئلة", "questions"), ("تعديل الزر", "button"), ("تحديد صورة لي التقديم", "image"), ("تحديد مكان نتائج التقديم", "results"), ("إعدادات رتب التقديم", "roles"), ("رجوع", "back"), ("إرسال", "send")]
        for label, action in actions:
            self.add_item(PanelAction(cog, number, label, action, discord.ButtonStyle.success if action == "send" else discord.ButtonStyle.secondary if action == "back" else discord.ButtonStyle.primary))


class PanelAction(discord.ui.Button):
    def __init__(self, cog, number, label, action, style):
        super().__init__(label=label, style=style)
        self.cog, self.number, self.action = cog, number, action
    async def callback(self, interaction):
        await self.cog.edit_panel(interaction, self.number, self.action)


class TextEditModal(discord.ui.Modal):
    def __init__(self, cog, number, key, title, label, value):
        super().__init__(title=title)
        self.cog, self.number, self.key = cog, number, key
        self.field = discord.ui.TextInput(label=label[:45], default=value or "", max_length=100, required=key != "button_emoji")
        self.add_item(self.field)
    async def on_submit(self, interaction):
        panel = await self.cog.get_panel(interaction.guild.id, self.number)
        panel[self.key] = self.field.value.strip() or None
        await self.cog.save_panel(interaction.guild.id, self.number, panel)
        await interaction.response.send_message("✅ تم حفظ التعديل.", ephemeral=True)


class ButtonSettings(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=300)
        self.add_item(PanelAction(cog, number, "اسم زر التقديم", "button_name", discord.ButtonStyle.primary))
        self.add_item(PanelAction(cog, number, "إيموجي التقديم", "emoji", discord.ButtonStyle.primary))
        self.add_item(ColorMenu(cog, number))


class ColorMenu(discord.ui.Button):
    def __init__(self, cog, number):
        super().__init__(label="ألوان الزر", style=discord.ButtonStyle.primary)
        self.cog, self.number = cog, number
    async def callback(self, interaction):
        await interaction.response.send_message("اختر لون الزر:", view=ColorView(self.cog, self.number), ephemeral=True)


class ColorView(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=180)
        for label, value, style in [("أزرق", "primary", discord.ButtonStyle.primary), ("أخضر", "success", discord.ButtonStyle.success), ("رمادي", "secondary", discord.ButtonStyle.secondary), ("أحمر", "danger", discord.ButtonStyle.danger)]:
            self.add_item(ColorButton(cog, number, label, value, style))


class ColorButton(discord.ui.Button):
    def __init__(self, cog, number, label, value, style):
        super().__init__(label=label, style=style)
        self.cog, self.number, self.value = cog, number, value
    async def callback(self, interaction):
        panel = await self.cog.get_panel(interaction.guild.id, self.number)
        panel["button_style"] = self.value
        await self.cog.save_panel(interaction.guild.id, self.number, panel)
        await interaction.response.send_message("✅ تم حفظ لون الزر.", ephemeral=True)


class QuestionSettings(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=300)
        self.cog, self.number = cog, number
        for index in range(10):
            self.add_item(QuestionButton(cog, number, index))


class QuestionButton(discord.ui.Button):
    def __init__(self, cog, number, index):
        panel_label = f"السؤال {index + 1}"
        super().__init__(label=panel_label, style=discord.ButtonStyle.primary, row=index // 5)
        self.cog, self.number, self.index = cog, number, index
    async def callback(self, interaction):
        panel = await self.cog.get_panel(interaction.guild.id, self.number)
        questions = panel["questions"]
        current = questions[self.index] if self.index < len(questions) else {"label": "", "required": True, "paragraph": False}
        await interaction.response.send_modal(QuestionModal(self.cog, self.number, self.index, current))


class QuestionModal(discord.ui.Modal):
    def __init__(self, cog, number, index, current):
        super().__init__(title=f"تعديل السؤال {index + 1}")
        self.cog, self.number, self.index = cog, number, index
        self.text = discord.ui.TextInput(label="نص السؤال", default=current.get("label", ""), max_length=256)
        self.required = discord.ui.TextInput(label="مطلوب؟ اكتب نعم أو لا", default="نعم" if current.get("required", True) else "لا", max_length=3)
        self.paragraph = discord.ui.TextInput(label="فقرة؟ اكتب نعم أو لا", default="نعم" if current.get("paragraph", False) else "لا", max_length=3)
        self.add_item(self.text); self.add_item(self.required); self.add_item(self.paragraph)
    async def on_submit(self, interaction):
        required = self.required.value.strip().lower() in {"نعم", "yes", "y", "1"}
        paragraph = self.paragraph.value.strip().lower() in {"نعم", "yes", "y", "1"}
        await self.cog.save_question(interaction, self.number, self.index, self.text.value, required, paragraph)


class ChannelPicker(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=180)
        self.add_item(ChannelSelect(cog, number))


class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog, number):
        super().__init__(placeholder="اختر روم السجلات...", channel_types=[discord.ChannelType.text])
        self.cog, self.number = cog, number
    async def callback(self, interaction):
        await self.cog.select_results(interaction, self.number, self.values[0].id)


class RoleSettings(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=300)
        self.cog, self.number = cog, number
        self.add_item(RoleSelect(cog, number, "accept_role", "رتبة القبول"))
        self.add_item(RoleSelect(cog, number, "reject_role", "رتبة الرفض"))
        self.add_item(RemoveRoleButton(cog, number, "accept_role", "إزالة رتبة القبول"))
        self.add_item(RemoveRoleButton(cog, number, "reject_role", "إزالة رتبة الرفض"))


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog, number, key, placeholder):
        super().__init__(placeholder=placeholder)
        self.cog, self.number, self.key = cog, number, key
    async def callback(self, interaction):
        await self.cog.set_role(interaction, self.number, self.key, self.values[0].id)


class RemoveRoleButton(discord.ui.Button):
    def __init__(self, cog, number, key, label):
        super().__init__(label=label, style=discord.ButtonStyle.danger)
        self.cog, self.number, self.key = cog, number, key
    async def callback(self, interaction):
        await self.cog.set_role(interaction, self.number, self.key, None)


class PublishedApplication(discord.ui.View):
    def __init__(self, cog, number, panel):
        super().__init__(timeout=None)
        emoji = panel.get("button_emoji")
        self.add_item(StartButton(cog, number, panel.get("button_label") or f"تقديم {number}", panel.get("button_style", "primary"), emoji))


class StartButton(discord.ui.Button):
    def __init__(self, cog, number, label, style, emoji):
        kwargs = {"label": label[:80], "style": STYLE_MAP.get(style, discord.ButtonStyle.primary)}
        if emoji:
            kwargs["emoji"] = emoji
        try:
            super().__init__(**kwargs)
        except (ValueError, TypeError):
            kwargs.pop("emoji", None)
            super().__init__(**kwargs)
        self.cog, self.number = cog, number
    async def callback(self, interaction):
        await self.cog.start_application(interaction, self.number)


class ApplicationModal(discord.ui.Modal):
    def __init__(self, cog, number, start, questions):
        super().__init__(title=f"تقديم {number} — الأسئلة {start + 1}-{min(start + 5, len(questions))}")
        self.cog, self.number, self.questions = cog, number, questions
        for question in questions[start:start + 5]:
            style = discord.TextStyle.paragraph if question.get("paragraph") else discord.TextStyle.short
            self.add_item(discord.ui.TextInput(label=question["label"][:45], required=bool(question.get("required", True)), style=style, max_length=4000))
    async def on_submit(self, interaction):
        await self.cog.collect(interaction, self.number, self.questions)


class ReviewView(discord.ui.View):
    def __init__(self, cog, application_id):
        super().__init__(timeout=None)
        self.add_item(ReviewButton(cog, application_id, True))
        self.add_item(ReviewButton(cog, application_id, False))


class ReviewButton(discord.ui.Button):
    def __init__(self, cog, application_id, accepted):
        super().__init__(label="قبول" if accepted else "رفض", style=discord.ButtonStyle.success if accepted else discord.ButtonStyle.danger)
        self.cog, self.application_id, self.accepted = cog, application_id, accepted
    async def callback(self, interaction):
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("❌ ما عندكش صلاحية مراجعة التقديمات.", ephemeral=True)
        if self.accepted:
            await self.cog.review(interaction, self.application_id, True)
        else:
            await interaction.response.send_modal(RejectModal(self.cog, self.application_id))


class RejectModal(discord.ui.Modal):
    def __init__(self, cog, application_id):
        super().__init__(title="سبب رفض التقديم")
        self.cog, self.application_id = cog, application_id
        self.reason = discord.ui.TextInput(label="سبب الرفض", style=discord.TextStyle.paragraph, required=False, max_length=1000)
        self.add_item(self.reason)
    async def on_submit(self, interaction):
        await self.cog.review(interaction, self.application_id, False, self.reason.value.strip() or None)


async def setup(bot: commands.Bot):
    await bot.add_cog(AppV4(bot))
