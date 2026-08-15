"""Advanced customizable tickets and ANOCoin owner grants."""
from __future__ import annotations
import json
import re
import time
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

OWNER_ID = 1472570059367911587
CURRENCY_NAME = "ANOCoin"
CURRENCY_SYMBOL = "🪙"


def admin_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.user.guild_permissions.administrator)
    return app_commands.check(predicate)


def safe_name(value: str, fallback: str = "ticket") -> str:
    value = re.sub(r"[^a-zA-Z0-9\-_ ]+", "", value).strip().lower().replace(" ", "-")
    return value[:80] or fallback


class TicketOptionSelect(discord.ui.Select):
    def __init__(self, cog, panel):
        self.cog, self.panel = cog, panel
        options = [discord.SelectOption(label=str(x.get("label", "Ticket"))[:100], description=str(x.get("description", ""))[:100] or None, emoji=x.get("emoji") or None, value=str(x.get("id"))) for x in panel.get("options", [])[:25]]
        super().__init__(placeholder="اختار نوع التذكرة...", min_values=1, max_values=1, options=options, custom_id=f"ader:ticket:panel:{panel['id']}")

    async def callback(self, interaction):
        option = next((x for x in self.panel.get("options", []) if str(x.get("id")) == self.values[0]), None)
        if option:
            await self.cog.open_ticket(interaction, self.panel, option)


class TicketPanelView(discord.ui.View):
    def __init__(self, cog, panel):
        super().__init__(timeout=None)
        self.cog, self.panel = cog, panel
        options = panel.get("options", [])[:25]
        if panel.get("mode", "buttons") == "select":
            if options:
                self.add_item(TicketOptionSelect(cog, panel))
        else:
            for item in options[:5]:
                button = discord.ui.Button(label=str(item.get("label", "Ticket"))[:80], emoji=item.get("emoji") or None, style=int(item.get("style", 1)), custom_id=f"ader:ticket:open:{panel['id']}:{item['id']}")
                async def callback(interaction, item=item):
                    await self.cog.open_ticket(interaction, self.panel, item)
                button.callback = callback
                self.add_item(button)


class TicketControls(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id="ader:ticket:claim:v2")
    async def claim(self, interaction, button):
        row = await self.cog.get_ticket(interaction.channel.id)
        if not row:
            return await interaction.response.send_message("❌ هادي ماشي تذكرة.", ephemeral=True)
        if row["user_id"] == interaction.user.id:
            return await interaction.response.send_message("❌ صاحب التذكرة ما يقدرش يدير Claim لنفس التذكرة.", ephemeral=True)
        if not interaction.user.guild_permissions.manage_channels and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ خاصك صلاحية إدارة الرومات.", ephemeral=True)
        await self.cog.bot.db.update_ticket(str(row["id"]), {"claimed_by": interaction.user.id})
        await interaction.response.send_message(f"🙋 تم Claim التذكرة من طرف {interaction.user.mention}.")

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="ader:ticket:close:v2")
    async def close(self, interaction, button):
        row = await self.cog.get_ticket(interaction.channel.id)
        if not row:
            return await interaction.response.send_message("❌ هادي ماشي تذكرة.", ephemeral=True)
        if row["user_id"] != interaction.user.id and not interaction.user.guild_permissions.manage_channels and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ غير صاحب التذكرة أو Staff يقدر يسدها.", ephemeral=True)
        await self.cog.bot.db.update_ticket(str(row["id"]), {"status": "closed", "closed_at": time.time()})
        await interaction.channel.set_permissions(interaction.guild.default_role, view_channel=False)
        await interaction.response.send_message("🔒 تسدات التذكرة. Staff يقدر يحذفها من بعد.")

    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="ader:ticket:delete:v2")
    async def delete(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ خاصك صلاحية إدارة الرومات.", ephemeral=True)
        await interaction.response.send_message("🗑️ غادي تتحذف التذكرة...", ephemeral=True)
        await interaction.channel.delete(reason="Ader custom ticket delete")


class PanelSelect(discord.ui.Select):
    def __init__(self, cog, panels, action="view"):
        self.cog, self.action = cog, action
        options = [discord.SelectOption(label=p["name"][:100], description=p.get("title", "")[:100], value=p["id"]) for p in panels[:25]]
        super().__init__(placeholder="اختار Panel...", options=options, custom_id=f"ader:ticket:manage:{action}")

    async def callback(self, interaction):
        panel = await self.cog.get_panel(interaction.guild.id, self.values[0])
        if not panel:
            return await interaction.response.send_message("❌ Panel ما لقيتوش.", ephemeral=True)
        if self.action == "add":
            await interaction.response.send_modal(AddOptionModal(self.cog, panel["id"]))
        else:
            await interaction.response.send_message(self.cog.panel_summary(panel), ephemeral=True)


class PanelManageView(discord.ui.View):
    def __init__(self, cog, panels, action="view"):
        super().__init__(timeout=180)
        self.add_item(PanelSelect(cog, panels, action))


class CreatePanelModal(discord.ui.Modal, title="إنشاء Ticket Panel"):
    name = discord.ui.TextInput(label="اسم الـPanel", placeholder="Support", max_length=50)
    title_text = discord.ui.TextInput(label="العنوان", placeholder="🎫 الدعم والمساعدة", max_length=256)
    description = discord.ui.TextInput(label="وصف الـPanel", style=discord.TextStyle.paragraph, max_length=4000)
    image = discord.ui.TextInput(label="رابط صورة الـPanel (اختياري)", required=False, max_length=1000)
    mode = discord.ui.TextInput(label="النوع: buttons أو select", default="buttons", max_length=10)

    def __init__(self, cog):
        super().__init__(); self.cog = cog

    async def on_submit(self, interaction):
        mode = str(self.mode.value).lower().strip()
        if mode not in ("buttons", "select"):
            mode = "buttons"
        panel = {"id": f"p{int(time.time()*1000)}", "name": str(self.name.value).strip(), "title": str(self.title_text.value).strip(), "description": str(self.description.value), "image": str(self.image.value).strip(), "mode": mode, "options": []}
        panels = await self.cog.get_panels(interaction.guild.id)
        if len(panels) >= 25:
            return await interaction.response.send_message("❌ وصلتي للحد الأقصى ديال 25 Panel.", ephemeral=True)
        panels.append(panel)
        await self.cog.save_panels(interaction.guild.id, panels)
        await interaction.response.send_message(f"✅ تصاوب Panel **{panel['name']}**. دابا استعمل `/ticket-option-add` باش تزيد الأنواع.", ephemeral=True)


class AddOptionModal(discord.ui.Modal, title="إضافة نوع تذكرة"):
    label = discord.ui.TextInput(label="اسم الزر / الاختيار", max_length=80)
    emoji = discord.ui.TextInput(label="الإيموجي", required=False, max_length=20)
    ticket_name = discord.ui.TextInput(label="اسم التذكرة", default="ticket-{user}", max_length=80)
    ticket_description = discord.ui.TextInput(label="وصف التذكرة", style=discord.TextStyle.paragraph, max_length=4000)
    image = discord.ui.TextInput(label="صورة التذكرة (اختياري)", required=False, max_length=1000)

    def __init__(self, cog, panel_id):
        super().__init__(); self.cog, self.panel_id = cog, panel_id

    async def on_submit(self, interaction):
        panel = await self.cog.get_panel(interaction.guild.id, self.panel_id)
        if not panel:
            return await interaction.response.send_message("❌ Panel ما لقيتوش.", ephemeral=True)
        if len(panel.get("options", [])) >= 25:
            return await interaction.response.send_message("❌ ما يمكنش تزيد أكثر من 25 نوع فـPanel واحد.", ephemeral=True)
        style = 1
        options = panel.setdefault("options", [])
        options.append({"id": f"o{int(time.time()*1000)}", "label": str(self.label.value).strip(), "emoji": str(self.emoji.value).strip(), "ticket_name": str(self.ticket_name.value).strip(), "ticket_description": str(self.ticket_description.value), "ticket_image": str(self.image.value).strip(), "style": style})
        await self.cog.update_panel(interaction.guild.id, panel)
        await interaction.response.send_message(f"✅ تزاد **{self.label.value}** للـPanel.", ephemeral=True)


class AdvancedTickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        for name in ("ticket-panel", "balance", "daily"):
            self.bot.tree.remove_command(name)

    async def guild_cfg(self, guild_id):
        return await self.bot.db.create_guild(guild_id)

    async def get_panels(self, guild_id):
        cfg = await self.guild_cfg(guild_id)
        return (cfg.get("modules") or {}).get("ticket_panels", [])

    async def save_panels(self, guild_id, panels):
        await self.bot.db.update_guild(guild_id, {"ticket_panels": panels})

    async def get_panel(self, guild_id, panel_id):
        return next((p for p in await self.get_panels(guild_id) if p.get("id") == panel_id), None)

    async def update_panel(self, guild_id, panel):
        panels = await self.get_panels(guild_id)
        for i, p in enumerate(panels):
            if p.get("id") == panel.get("id"):
                panels[i] = panel
                break
        await self.save_panels(guild_id, panels)

    def panel_summary(self, panel):
        options = panel.get("options", [])
        names = "\n".join(f"{x.get('emoji','')} {x.get('label')} → `{x.get('ticket_name')}`" for x in options) or "ما كاين حتى نوع."
        return f"**{panel.get('name')}**\nالعنوان: {panel.get('title')}\nMode: `{panel.get('mode')}`\nالصورة: {'مضافة' if panel.get('image') else 'ما كايناش'}\n\n**الأنواع:**\n{names}"

    async def open_ticket(self, interaction, panel, option):
        guild = interaction.guild
        existing = await self.bot.db.fetchone('SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND status="open"', (guild.id, interaction.user.id))
        if existing:
            return await interaction.response.send_message(f"❌ عندك تذكرة مفتوحة: <#{existing['channel_id']}>.", ephemeral=True)
        category_name = panel.get("category") or "Tickets"
        category = discord.utils.get(guild.categories, name=category_name) or await guild.create_category(category_name, reason="Ader custom ticket")
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True), guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)}
        if panel.get("staff_role"):
            role = guild.get_role(int(panel["staff_role"]))
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        name = option.get("ticket_name", "ticket-{user}").replace("{user}", safe_name(interaction.user.name)).replace("{id}", str(interaction.user.id))
        channel = await guild.create_text_channel(safe_name(name), category=category, overwrites=overwrites, reason="Ader custom ticket")
        tid = await self.bot.db.create_ticket({"guild_id": guild.id, "channel_id": channel.id, "user_id": interaction.user.id, "status": "open"})
        embed = discord.Embed(title=option.get("ticket_name", "🎫 Ticket"), description=option.get("ticket_description") or "شرح التذكرة.")
        if option.get("ticket_image"):
            embed.set_image(url=option["ticket_image"])
        await channel.send(content=interaction.user.mention + f"\nTicket ID: `{tid}`", embed=embed, view=TicketControls(self))
        await interaction.response.send_message(f"✅ تصاوبات التذكرة: {channel.mention}", ephemeral=True)

    async def get_ticket(self, channel_id):
        return await self.bot.db.fetchone('SELECT * FROM tickets WHERE channel_id=? AND status IN ("open","closed")', (channel_id,))

    @app_commands.command(name="ticket-panel", description="فتح مدير Ticket Panels")
    @admin_check()
    async def ticket_panel(self, interaction):
        view = discord.ui.View(timeout=180)
        create = discord.ui.Button(label="Create Panel", emoji="➕", style=discord.ButtonStyle.success)
        manage = discord.ui.Button(label="Manage Panels", emoji="⚙️", style=discord.ButtonStyle.primary)
        add = discord.ui.Button(label="Add Ticket Type", emoji="🎫", style=discord.ButtonStyle.secondary)
        async def create_cb(i): await i.response.send_modal(CreatePanelModal(self))
        async def manage_cb(i):
            panels = await self.get_panels(i.guild.id)
            if not panels: return await i.response.send_message("❌ ما عندك حتى Panel.", ephemeral=True)
            await i.response.send_message("اختار Panel:", view=PanelManageView(self, panels), ephemeral=True)
        async def add_cb(i):
            panels = await self.get_panels(i.guild.id)
            if not panels: return await i.response.send_message("❌ صايب Panel أولاً.", ephemeral=True)
            await i.response.send_message("اختار Panel:", view=PanelManageView(self, panels, "add"), ephemeral=True)
        create.callback=create_cb; manage.callback=manage_cb; add.callback=add_cb
        for b in (create, manage, add): view.add_item(b)
        await interaction.response.send_message("🎫 **Ader Ticket Designer**\nتقدر تخصص اسم ووصف وصورة الـPanel، وتدير فيه حتى 25 نوع تذكرة، وكل نوع عندو اسم ووصف وصورة خاصين به، وتختار Buttons أو Select Menu.", view=view, ephemeral=True)

    @app_commands.command(name="ticket-option-add", description="إضافة نوع تذكرة إلى Panel")
    @admin_check()
    async def ticket_option_add(self, interaction, panel: str):
        p = next((x for x in await self.get_panels(interaction.guild.id) if x.get("name", "").lower() == panel.lower() or x.get("id") == panel), None)
        if not p: return await interaction.response.send_message("❌ Panel ما لقيتوش.", ephemeral=True)
        await interaction.response.send_modal(AddOptionModal(self, p["id"]))

    @app_commands.command(name="ticket-publish", description="نشر Ticket Panel")
    @admin_check()
    async def ticket_publish(self, interaction, panel: str, channel: discord.TextChannel):
        p = next((x for x in await self.get_panels(interaction.guild.id) if x.get("name", "").lower() == panel.lower() or x.get("id") == panel), None)
        if not p: return await interaction.response.send_message("❌ Panel ما لقيتوش.", ephemeral=True)
        if not p.get("options"): return await interaction.response.send_message("❌ زيد على الأقل نوع واحد للتذكرة.", ephemeral=True)
        embed = discord.Embed(title=p.get("title") or p.get("name"), description=p.get("description") or "")
        if p.get("image"):
            embed.set_image(url=p["image"])
        await channel.send(embed=embed, view=TicketPanelView(self, p))
        await interaction.response.send_message(f"✅ تنشر **{p['name']}** في {channel.mention}.", ephemeral=True)

    @app_commands.command(name="ticket-panel-settings", description="تحديد Category وStaff Role للـPanel")
    @admin_check()
    async def ticket_panel_settings(self, interaction, panel: str, category: discord.CategoryChannel, staff_role: Optional[discord.Role] = None):
        p = next((x for x in await self.get_panels(interaction.guild.id) if x.get("name", "").lower() == panel.lower() or x.get("id") == panel), None)
        if not p: return await interaction.response.send_message("❌ Panel ما لقيتوش.", ephemeral=True)
        p["category"] = category.name
        p["staff_role"] = staff_role.id if staff_role else None
        await self.update_panel(interaction.guild.id, p)
        await interaction.response.send_message(f"✅ Panel **{p['name']}** غادي يستعمل Category **{category.name}**" + (f" وStaff Role {staff_role.mention}." if staff_role else "."), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or not message.content.strip().startswith("!اعطي"):
            return
        if message.author.id != OWNER_ID:
            return
        parts = message.content.strip().split()
        if len(parts) < 3 or not message.mentions:
            return await message.channel.send("❌ الاستعمال: `!اعطي @user المبلغ`", delete_after=8)
        try:
            amount = int(parts[-1].replace(",", ""))
        except ValueError:
            return await message.channel.send("❌ المبلغ خاصو يكون رقم.", delete_after=8)
        if amount <= 0:
            return await message.channel.send("❌ المبلغ خاصو يكون أكبر من 0.", delete_after=8)
        target = message.mentions[0]
        await self.bot.db.add_balance(target.id, message.guild.id, amount)
        await message.channel.send(f"✅ عطيت {target.mention} **{CURRENCY_SYMBOL} {amount:,} {CURRENCY_NAME}**.")

    @app_commands.command(name="balance", description="Check your ANOCoin balance")
    async def balance(self, interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        data = await self.bot.db.create_user(target.id, interaction.guild.id)
        await interaction.response.send_message(f"🪙 {target.mention} عندو **{data.get('balance', 0):,} {CURRENCY_NAME}**.")


async def setup(bot):
    await bot.add_cog(AdvancedTickets(bot))
