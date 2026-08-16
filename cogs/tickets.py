"""Full Ticket Tool-style system for Ader.

There is intentionally only one application command: /ticket.
All panel management is performed through buttons, selects and modals.
"""

import asyncio
import json
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedColor
from utils.permissions import is_admin


MAX_OPTIONS = 25


def clean_name(value: str, fallback: str = "ticket") -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return value[:80] or fallback


class TicketControls(discord.ui.View):
    def __init__(self, cog: "Tickets", channel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.add_item(TicketClaimButton(cog, channel_id))
        self.add_item(TicketCloseButton(cog, channel_id))
        self.add_item(TicketDeleteButton(cog, channel_id))


class TicketClaimButton(discord.ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(label="Claim", style=discord.ButtonStyle.success, emoji="🙋", custom_id=f"ader:ticket:claim:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        if not await self.cog.is_staff(interaction):
            return await interaction.response.send_message("❌ هاد الزر مخصص للـStaff.", ephemeral=True)
        ticket = await self.cog.get_ticket(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("❌ هادي ماشي تذكرة مفتوحة.", ephemeral=True)
        if ticket["claimed_by"]:
            return await interaction.response.send_message(f"❌ التذكرة متكفّل بها <@{ticket['claimed_by']}>", ephemeral=True)
        await self.cog.db.update_ticket(str(ticket["id"]), {"claimed_by": interaction.user.id})
        await interaction.response.send_message(f"🙋 {interaction.user.mention} تكفّل بالتذكرة.")


class TicketCloseButton(discord.ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(label="Close", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id=f"ader:ticket:close:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        await self.cog.close_ticket(interaction, self.channel_id)


class TicketDeleteButton(discord.ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id=f"ader:ticket:delete:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        if not await self.cog.is_staff(interaction):
            return await interaction.response.send_message("❌ حذف التذكرة مخصص للـStaff.", ephemeral=True)
        ticket = await self.cog.get_ticket(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("❌ هادي ماشي تذكرة مسجلة.", ephemeral=True)
        await self.cog.db.update_ticket(str(ticket["id"]), {"status": "deleted", "closed_at": discord.utils.utcnow().timestamp()})
        await interaction.response.send_message("🗑️ غادي يتحيد الروم بعد 3 ثواني.")
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except discord.HTTPException:
            pass


class TicketOpenButton(discord.ui.Button):
    def __init__(self, cog, panel_id: int, option_index: int, label: str, emoji: str):
        super().__init__(label=label[:80] or "فتح تذكرة", style=discord.ButtonStyle.primary, emoji=emoji or "🎫", custom_id=f"ader:ticket:open:{panel_id}:{option_index}")
        self.cog, self.panel_id, self.option_index = cog, panel_id, option_index

    async def callback(self, interaction: discord.Interaction):
        await self.cog.create_ticket_from_panel(interaction, self.panel_id, self.option_index)


class TicketOpenSelect(discord.ui.Select):
    def __init__(self, cog, panel: dict):
        options = []
        for index, item in enumerate(panel.get("options", [])[:MAX_OPTIONS]):
            options.append(discord.SelectOption(
                label=str(item.get("name") or "فتح تذكرة")[:100],
                description=str(item.get("description") or "فتح تذكرة")[:100],
                emoji=item.get("emoji") or "🎫",
                value=str(index),
            ))
        if not options:
            options = [discord.SelectOption(label="فتح تذكرة", value="0", emoji="🎫")]
        super().__init__(placeholder="اختار نوع التذكرة...", min_values=1, max_values=1, options=options, custom_id=f"ader:ticket:select:{panel['id']}")
        self.cog, self.panel_id = cog, panel["id"]

    async def callback(self, interaction: discord.Interaction):
        await self.cog.create_ticket_from_panel(interaction, self.panel_id, int(self.values[0]))


class TicketPanelView(discord.ui.View):
    def __init__(self, cog, panel: dict):
        super().__init__(timeout=None)
        self.cog = cog
        options = panel.get("options", []) or [{"name": panel.get("button_label", "فتح تذكرة"), "emoji": panel.get("button_emoji", "🎫")}]
        if panel.get("mode") == "select":
            self.add_item(TicketOpenSelect(cog, {**panel, "options": options}))
        else:
            for index, item in enumerate(options[:MAX_OPTIONS]):
                self.add_item(TicketOpenButton(cog, panel["id"], index, item.get("name", "فتح تذكرة"), item.get("emoji", "🎫")))


class TicketHomeView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label="Create a Panel", style=discord.ButtonStyle.primary, emoji="➕")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreatePanelModal(self.cog))

    @discord.ui.button(label="Manage Panels", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        panels = await self.cog.db.list_ticket_panels(interaction.guild.id)
        if not panels:
            return await interaction.response.send_message("❌ ما عندك حتى Panel مصايب. استعمل **Create a Panel** أولاً.", ephemeral=True)
        await interaction.response.edit_message(embed=self.cog.manage_embed(panels), view=PanelManagerView(self.cog, panels))


class CreatePanelModal(discord.ui.Modal, title="Create a Panel"):
    title_input = discord.ui.TextInput(label="Panel name / title", default="🎫 الدعم الفني", max_length=256)
    description_input = discord.ui.TextInput(label="Panel description", style=discord.TextStyle.paragraph, default="اختار القسم المناسب لفتح تذكرة.", max_length=4000)
    image_input = discord.ui.TextInput(label="Panel image URL (optional)", required=False, max_length=1000)
    ticket_desc_input = discord.ui.TextInput(label="Default ticket description", style=discord.TextStyle.paragraph, required=False, default="شرح لينا المشكل ديالك بالتفصيل، وغادي يساعدك فريق الدعم.", max_length=2000)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        state = {
            "guild_id": interaction.guild.id,
            "title": str(self.title_input),
            "description": str(self.description_input),
            "image_url": str(self.image_input).strip() or None,
            "ticket_description": str(self.ticket_desc_input),
            "mode": "buttons",
            "button_label": "فتح تذكرة",
            "button_emoji": "🎫",
            "category_id": None,
            "support_role_id": None,
            "options": [{"name": "فتح تذكرة", "emoji": "🎫", "description": str(self.ticket_desc_input), "ticket_name": "ticket-{user}", "image_url": None}],
        }
        await interaction.response.send_message("اختار إعدادات الـPanel من الواجهة التالية:", embed=self.cog.preview_embed(state), view=PanelBuilderView(self.cog, state), ephemeral=True)


class AddTicketTypeModal(discord.ui.Modal, title="Add Ticket Type"):
    name_input = discord.ui.TextInput(label="Button / option name", max_length=80)
    emoji_input = discord.ui.TextInput(label="Emoji", default="🎫", max_length=20)
    ticket_name_input = discord.ui.TextInput(label="Ticket channel name", default="ticket-{user}", max_length=80)
    description_input = discord.ui.TextInput(label="Ticket description", style=discord.TextStyle.paragraph, max_length=2000)
    image_input = discord.ui.TextInput(label="Ticket image URL (optional)", required=False, max_length=1000)

    def __init__(self, builder: "PanelBuilderView"):
        super().__init__()
        self.builder = builder

    async def on_submit(self, interaction: discord.Interaction):
        if len(self.builder.state["options"]) >= MAX_OPTIONS:
            return await interaction.response.send_message("❌ Discord كيسمح بحد أقصى 25 اختيار داخل Panel واحد.", ephemeral=True)
        self.builder.state["options"].append({
            "name": str(self.name_input), "emoji": str(self.emoji_input) or "🎫",
            "ticket_name": str(self.ticket_name_input) or "ticket-{user}",
            "description": str(self.description_input), "image_url": str(self.image_input).strip() or None,
        })
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class PanelBuilderView(discord.ui.View):
    def __init__(self, cog, state: dict, existing_panel_id: Optional[int] = None):
        super().__init__(timeout=600)
        self.cog, self.state, self.existing_panel_id = cog, state, existing_panel_id
        self.add_item(CategorySelect(self))
        self.add_item(ChannelSelect(self))
        self.add_item(RoleSelect(self))
        self.add_item(ModeSelect(self))

    @discord.ui.button(label="Add Ticket Type", style=discord.ButtonStyle.secondary, emoji="➕", row=3)
    async def add_type(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddTicketTypeModal(self))

    @discord.ui.button(label="Remove Last Type", style=discord.ButtonStyle.secondary, emoji="➖", row=3)
    async def remove_type(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.state["options"]) <= 1:
            return await interaction.response.send_message("❌ خاص Panel يبقى فيه اختيار واحد على الأقل.", ephemeral=True)
        self.state["options"].pop()
        await interaction.response.edit_message(embed=self.cog.preview_embed(self.state), view=self)

    @discord.ui.button(label="Send Panel to Channel", style=discord.ButtonStyle.success, emoji="📤", row=4)
    async def send_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.state.get("channel_id"):
            return await interaction.response.send_message("❌ اختار Channel اللي غادي يتنشر فيه الـPanel.", ephemeral=True)
        if not self.state.get("category_id"):
            return await interaction.response.send_message("❌ اختار Category ديال التذاكر.", ephemeral=True)
        channel = interaction.guild.get_channel(self.state["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("❌ الـChannel المختار غير صالح.", ephemeral=True)
        if self.existing_panel_id:
            panel = await self.cog.db.get_ticket_panel(self.existing_panel_id)
            if not panel:
                return await interaction.response.send_message("❌ Panel ما بقاتش موجودة.", ephemeral=True)
            message_id = panel.get("message_id")
            target = channel
            if message_id:
                try:
                    message = await target.fetch_message(message_id)
                    await message.edit(embed=self.cog.panel_embed(self.state), view=TicketPanelView(self.cog, {**self.state, "id": self.existing_panel_id}))
                    await self.cog.db.update_ticket_panel(self.existing_panel_id, {**self.state, "channel_id": channel.id, "message_id": message.id})
                    return await interaction.response.edit_message(content="✅ تم تحديث الـPanel.", embed=self.cog.preview_embed(self.state), view=None)
                except discord.HTTPException:
                    pass
            sent = await target.send(embed=self.cog.panel_embed(self.state), view=TicketPanelView(self.cog, {**self.state, "id": self.existing_panel_id}))
            await self.cog.db.update_ticket_panel(self.existing_panel_id, {**self.state, "channel_id": channel.id, "message_id": sent.id})
            return await interaction.response.edit_message(content="✅ تم نشر الـPanel.", embed=self.cog.preview_embed(self.state), view=None)

        panel_id = await self.cog.db.create_ticket_panel({**self.state, "channel_id": channel.id})
        panel = await self.cog.db.get_ticket_panel(panel_id)
        sent = await channel.send(embed=self.cog.panel_embed(panel), view=TicketPanelView(self.cog, panel))
        await self.cog.db.update_ticket_panel(panel_id, {"message_id": sent.id})
        self.cog.bot.add_view(TicketPanelView(self.cog, panel), message_id=sent.id)
        await interaction.response.edit_message(content=f"✅ تم إنشاء Panel #{panel_id} ونشره في {channel.mention}.", embed=self.cog.preview_embed(panel), view=None)


class CategorySelect(discord.ui.ChannelSelect):
    def __init__(self, builder):
        super().__init__(channel_types=[discord.ChannelType.category], placeholder="اختار Ticket Category", row=0)
        self.builder = builder

    async def callback(self, interaction: discord.Interaction):
        self.builder.state["category_id"] = self.values[0].id
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, builder):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="اختار Channel لنشر الـPanel", row=1)
        self.builder = builder

    async def callback(self, interaction: discord.Interaction):
        self.builder.state["channel_id"] = self.values[0].id
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, builder):
        super().__init__(placeholder="اختار Staff / Support Role", row=2, min_values=0, max_values=1)
        self.builder = builder

    async def callback(self, interaction: discord.Interaction):
        self.builder.state["support_role_id"] = self.values[0].id if self.values else None
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class ModeSelect(discord.ui.Select):
    def __init__(self, builder):
        super().__init__(placeholder="اختار شكل الـPanel: Buttons أو Select Menu", options=[
            discord.SelectOption(label="Buttons", value="buttons", emoji="🔘"),
            discord.SelectOption(label="Select Menu", value="select", emoji="📋"),
        ], row=4)
        self.builder = builder

    async def callback(self, interaction: discord.Interaction):
        self.builder.state["mode"] = self.values[0]
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class PanelManagerView(discord.ui.View):
    def __init__(self, cog, panels):
        super().__init__(timeout=600)
        self.cog = cog
        self.panels = panels
        options = [discord.SelectOption(label=f"#{p['id']} • {p['title'][:70]}", value=str(p['id']), emoji="🎫") for p in panels[:25]]
        self.add_item(PanelSelect(self, options))

    @discord.ui.button(label="Edit Panel", style=discord.ButtonStyle.primary, emoji="✏️", row=1)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_id = getattr(self, "selected_id", None)
        if not panel_id:
            return await interaction.response.send_message("❌ اختار Panel أولاً.", ephemeral=True)
        panel = await self.cog.db.get_ticket_panel(panel_id)
        if not panel:
            return await interaction.response.send_message("❌ Panel ما بقاتش موجودة.", ephemeral=True)
        await interaction.response.send_modal(EditPanelModal(self.cog, panel))

    @discord.ui.button(label="Send Panel", style=discord.ButtonStyle.success, emoji="📤", row=1)
    async def send_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_id = getattr(self, "selected_id", None)
        if not panel_id:
            return await interaction.response.send_message("❌ اختار Panel أولاً.", ephemeral=True)
        panel = await self.cog.db.get_ticket_panel(panel_id)
        if not panel:
            return await interaction.response.send_message("❌ Panel ما بقاتش موجودة.", ephemeral=True)
        state = dict(panel)
        await interaction.response.edit_message(content="خصّص الـPanel أو اختار Channel ثم نشره:", embed=self.cog.preview_embed(state), view=PanelBuilderView(self.cog, state, panel_id))

    @discord.ui.button(label="Delete Panel", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_id = getattr(self, "selected_id", None)
        if not panel_id:
            return await interaction.response.send_message("❌ اختار Panel أولاً.", ephemeral=True)
        panel = await self.cog.db.get_ticket_panel(panel_id)
        if panel and panel.get("channel_id") and panel.get("message_id"):
            channel = interaction.guild.get_channel(panel["channel_id"])
            if channel:
                try:
                    message = await channel.fetch_message(panel["message_id"])
                    await message.delete()
                except discord.HTTPException:
                    pass
        await self.cog.db.delete_ticket_panel(panel_id)
        await interaction.response.edit_message(content=f"✅ تحيد Panel #{panel_id}.", embed=None, view=None)


class PanelSelect(discord.ui.Select):
    def __init__(self, parent, options):
        super().__init__(placeholder="اختار Panel لإدارتها...", options=options)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        self.parent.selected_id = int(self.values[0])
        panel = await self.parent.cog.db.get_ticket_panel(self.parent.selected_id)
        await interaction.response.edit_message(embed=self.parent.cog.panel_details_embed(panel), view=self.parent)


class EditPanelModal(discord.ui.Modal, title="Edit Panel"):
    title_input = discord.ui.TextInput(label="Panel title", max_length=256)
    description_input = discord.ui.TextInput(label="Panel description", style=discord.TextStyle.paragraph, max_length=4000)
    image_input = discord.ui.TextInput(label="Panel image URL", required=False, max_length=1000)
    ticket_desc_input = discord.ui.TextInput(label="Default ticket description", style=discord.TextStyle.paragraph, max_length=2000)

    def __init__(self, cog, panel):
        super().__init__()
        self.cog, self.panel = cog, panel
        self.title_input.default = panel.get("title", "")
        self.description_input.default = panel.get("description", "")
        self.image_input.default = panel.get("image_url") or ""
        self.ticket_desc_input.default = panel.get("ticket_description", "")

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.db.update_ticket_panel(self.panel["id"], {"title": str(self.title_input), "description": str(self.description_input), "image_url": str(self.image_input).strip() or None, "ticket_description": str(self.ticket_desc_input)})
        panel = await self.cog.db.get_ticket_panel(self.panel["id"])
        await interaction.response.send_message("✅ تم تعديل معلومات الـPanel. استعمل **Manage Panels → Send Panel** لتحديث/إعادة نشر الواجهة.", ephemeral=True)


class Tickets(commands.Cog):
    """Ticket Tool-style manager exposed through exactly one slash command: /ticket."""

    def __init__(self, bot, db, config):
        self.bot, self.db, self.config = bot, db, config

    async def cog_load(self):
        panels = await self.db.get_all_ticket_panels()
        for panel in panels:
            try:
                self.bot.add_view(TicketPanelView(self, panel), message_id=panel.get("message_id") or None)
            except (ValueError, TypeError):
                pass

    def panel_embed(self, panel):
        embed = discord.Embed(title=panel.get("title", "🎫 الدعم الفني")[:256], description=panel.get("description", "اختار القسم المناسب لفتح تذكرة.")[:4096], color=EmbedColor.PRIMARY)
        if panel.get("image_url"):
            embed.set_image(url=panel["image_url"])
        embed.set_footer(text="Ader Tickets")
        return embed

    def preview_embed(self, state):
        panel = {**state, "title": state.get("title", "🎫 الدعم الفني"), "description": state.get("description", "")}
        embed = self.panel_embed(panel)
        options = state.get("options", [])
        embed.add_field(name="Mode", value="Select Menu" if state.get("mode") == "select" else "Buttons", inline=True)
        embed.add_field(name="Ticket Types", value=str(len(options)), inline=True)
        embed.add_field(name="Panel Channel", value=f"<#{state['channel_id']}>" if state.get("channel_id") else "Not selected", inline=True)
        embed.add_field(name="Category", value=f"<#{state['category_id']}>" if state.get("category_id") else "Not selected", inline=True)
        embed.add_field(name="Support Role", value=f"<@&{state['support_role_id']}>" if state.get("support_role_id") else "Not selected", inline=True)
        return embed

    def manage_embed(self, panels):
        embed = discord.Embed(title="🎫 Manage Panels", description="اختار Panel من القائمة ثم استعمل الأزرار لإدارته.", color=EmbedColor.PRIMARY)
        for p in panels[:10]:
            mode = "Select Menu" if p.get("mode") == "select" else "Buttons"
            embed.add_field(name=f"#{p['id']} • {p['title']}", value=f"{mode} • {len(p.get('options', []))} ticket types", inline=False)
        return embed

    def panel_details_embed(self, panel):
        embed = self.panel_embed(panel)
        embed.title = f"🎫 Panel #{panel['id']} • {panel['title'][:220]}"
        opts = panel.get("options", [])
        text = "\n".join(f"{i+1}. {o.get('emoji','🎫')} {o.get('name','Ticket')} → `{o.get('ticket_name','ticket-{user}')}`" for i,o in enumerate(opts[:25])) or "ما كاين حتى Ticket Type"
        embed.add_field(name="Ticket Types", value=text[:1024], inline=False)
        return embed

    async def is_staff(self, interaction):
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels:
            return True
        guild = await self.db.get_guild(interaction.guild.id)
        role_id = guild.get("modules", {}).get("support_role") if guild else None
        return bool(role_id and interaction.user.get_role(int(role_id)))

    async def get_ticket(self, channel_id):
        return await self.db.fetchone("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (channel_id,))

    async def create_ticket_from_panel(self, interaction, panel_id: int, option_index: int):
        panel = await self.db.get_ticket_panel(panel_id)
        if not panel or panel.get("guild_id") != interaction.guild.id:
            return await interaction.response.send_message("❌ هاد الـPanel ما بقاش موجود.", ephemeral=True)
        options = panel.get("options", [])
        option = options[option_index] if 0 <= option_index < len(options) else (options[0] if options else {})
        category = interaction.guild.get_channel(panel.get("category_id"))
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Ticket Category ما بقاتش موجودة.", ephemeral=True)
        existing = await self.db.fetchone("SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND status='open'", (interaction.guild.id, interaction.user.id))
        if existing:
            channel = interaction.guild.get_channel(existing["channel_id"])
            return await interaction.response.send_message(f"❌ عندك Ticket مفتوحة بالفعل: {channel.mention if channel else 'غير متاحة'}", ephemeral=True)

        ticket_name = option.get("ticket_name", "ticket-{user}")
        safe_user = clean_name(interaction.user.name, "user")[:25]
        ticket_name = ticket_name.replace("{user}", safe_user).replace("{id}", str(interaction.user.id))
        ticket_name = clean_name(ticket_name, f"ticket-{safe_user}")[:90]
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True),
        }
        role_id = panel.get("support_role_id")
        if role_id:
            role = interaction.guild.get_role(int(role_id))
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await category.create_text_channel(ticket_name, overwrites=overwrites, reason="Ader Ticket created")
        ticket_id = await self.db.create_ticket({"guild_id": interaction.guild.id, "user_id": interaction.user.id, "channel_id": channel.id, "status": "open", "data": {"panel_id": panel_id, "option_index": option_index, "description": option.get("description") or panel.get("ticket_description")}})
        embed = discord.Embed(title=f"🎫 {option.get('name', 'Ticket')}", description=f"مرحبا {interaction.user.mention}!\n\n{option.get('description') or panel.get('ticket_description','')}\n\n**Ticket ID:** `{ticket_id}`", color=EmbedColor.PRIMARY)
        if option.get("image_url"):
            embed.set_image(url=option["image_url"])
        embed.add_field(name="Opened by", value=interaction.user.mention)
        await channel.send(content=interaction.user.mention, embed=embed, view=TicketControls(self, channel.id))
        self.bot.add_view(TicketControls(self, channel.id))
        await interaction.response.send_message(f"✅ تفتحات التذكرة ديالك: {channel.mention}", ephemeral=True)

    async def close_ticket(self, interaction, channel_id):
        ticket = await self.get_ticket(channel_id)
        if not ticket:
            return await interaction.response.send_message("❌ هادي ماشي تذكرة مفتوحة.", ephemeral=True)
        owner = ticket["user_id"] == interaction.user.id
        if not owner and not await self.is_staff(interaction):
            return await interaction.response.send_message("❌ غير صاحب التذكرة أو الـStaff يقدر يسدها.", ephemeral=True)
        await self.db.update_ticket(str(ticket["id"]), {"status": "closed", "closed_at": discord.utils.utcnow().timestamp()})
        await interaction.response.send_message("🔒 تسدات التذكرة. غادي يتحيد الروم بعد 5 ثواني.")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            pass

    @app_commands.command(name="ticket", description="Open the Ader Ticket Tool manager")
    @is_admin()
    async def ticket(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🎫 Ader Ticket Tool", description="من هنا كتقدر تنشئ وتدير جميع Ticket Panels ديالك.\n\n**Create a Panel** لإنشاء Panel جديد\n**Manage Panels** لإدارة Panels الموجودة", color=EmbedColor.PRIMARY)
        embed.set_footer(text="Ader Tickets • Administrator only")
        await interaction.response.send_message(embed=embed, view=TicketHomeView(self), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tickets(bot, bot.db, bot.config))
