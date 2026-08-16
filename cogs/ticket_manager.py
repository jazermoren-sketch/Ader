"""Ader Ticket Tool.

This is the single ticket implementation. The only application command exposed by
this module is /ticket; panel creation/management happens through UI components.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedColor
from utils.permissions import is_admin

MAX_OPTIONS = 25


def clean_name(value: str, fallback: str = "ticket") -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value).strip().lower()).strip("-")
    return value[:90] or fallback


def valid_url(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    if not value.startswith(("https://", "http://")):
        return None
    return value[:1000]


class TicketControls(discord.ui.View):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(timeout=None)
        self.add_item(TicketClaimButton(cog, channel_id))
        self.add_item(TicketCloseButton(cog, channel_id))
        self.add_item(TicketDeleteButton(cog, channel_id))


class TicketClaimButton(discord.ui.Button):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="Claim", emoji="🙋", style=discord.ButtonStyle.success,
                         custom_id=f"ader:ticket:claim:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        if not await self.cog.is_staff(interaction):
            return await interaction.response.send_message("❌ هاد الزر مخصص للـStaff.", ephemeral=True)
        ticket = await self.cog.get_open_ticket(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("❌ هادي ماشي تذكرة مفتوحة.", ephemeral=True)
        if int(ticket["user_id"]) == interaction.user.id:
            return await interaction.response.send_message("❌ صاحب التذكرة ما يقدرش يدير Claim لنفسو.", ephemeral=True)
        cur = await self.cog.db.execute(
            "UPDATE tickets SET claimed_by=? WHERE id=? AND status='open' AND claimed_by IS NULL",
            (interaction.user.id, ticket["id"]),
        )
        if cur.rowcount != 1:
            return await interaction.response.send_message("❌ شي Staff آخر تكفّل بها.", ephemeral=True)
        await interaction.response.send_message(f"🙋 {interaction.user.mention} تكفّل بالتذكرة.")


class TicketCloseButton(discord.ui.Button):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="Close", emoji="🔒", style=discord.ButtonStyle.secondary,
                         custom_id=f"ader:ticket:close:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        await self.cog.close_ticket(interaction, self.channel_id)


class TicketDeleteButton(discord.ui.Button):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger,
                         custom_id=f"ader:ticket:delete:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        if not await self.cog.is_staff(interaction):
            return await interaction.response.send_message("❌ حذف التذكرة مخصص للـStaff.", ephemeral=True)
        cur = await self.cog.db.execute(
            "UPDATE tickets SET status='deleted', closed_at=? "
            "WHERE channel_id=? AND status IN ('open','closed')",
            (discord.utils.utcnow().timestamp(), self.channel_id),
        )
        if cur.rowcount != 1:
            return await interaction.response.send_message("❌ التذكرة تسدات/تحيدات من قبل.", ephemeral=True)
        await interaction.response.send_message("🗑️ غادي يتحيد الروم بعد ثانيتين.")
        await asyncio.sleep(2)
        try:
            await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except discord.HTTPException:
            pass


class TicketOpenButton(discord.ui.Button):
    def __init__(self, cog: "TicketManager", panel_id: int, option_index: int, label: str, emoji: str):
        super().__init__(label=str(label or "فتح تذكرة")[:80], emoji=emoji or "🎫",
                         style=discord.ButtonStyle.primary,
                         custom_id=f"ader:ticket:open:{panel_id}:{option_index}")
        self.cog, self.panel_id, self.option_index = cog, panel_id, option_index

    async def callback(self, interaction: discord.Interaction):
        await self.cog.create_ticket_from_panel(interaction, self.panel_id, self.option_index)


class TicketOpenSelect(discord.ui.Select):
    def __init__(self, cog: "TicketManager", panel: dict):
        options = []
        for index, item in enumerate(panel.get("options", [])[:MAX_OPTIONS]):
            options.append(discord.SelectOption(
                label=str(item.get("name") or "فتح تذكرة")[:100],
                description=str(item.get("description") or "فتح تذكرة")[:100] or None,
                emoji=item.get("emoji") or "🎫",
                value=str(index),
            ))
        if not options:
            options = [discord.SelectOption(label="فتح تذكرة", value="0", emoji="🎫")]
        super().__init__(placeholder="اختار نوع التذكرة...", min_values=1, max_values=1,
                         options=options, custom_id=f"ader:ticket:select:{panel['id']}")
        self.cog, self.panel_id = cog, panel["id"]

    async def callback(self, interaction: discord.Interaction):
        await self.cog.create_ticket_from_panel(interaction, self.panel_id, int(self.values[0]))


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "TicketManager", panel: dict):
        super().__init__(timeout=None)
        options = panel.get("options", []) or [{"name": "فتح تذكرة", "emoji": "🎫"}]
        if panel.get("mode") == "select":
            self.add_item(TicketOpenSelect(cog, {**panel, "options": options}))
        else:
            for index, item in enumerate(options[:MAX_OPTIONS]):
                self.add_item(TicketOpenButton(cog, panel["id"], index,
                                                item.get("name", "فتح تذكرة"), item.get("emoji", "🎫")))


class CreatePanelModal(discord.ui.Modal, title="Create a Panel"):
    title_input = discord.ui.TextInput(label="Panel name / title", default="🎫 الدعم الفني", max_length=256)
    description_input = discord.ui.TextInput(
        label="Panel description", style=discord.TextStyle.paragraph,
        default="اختار القسم المناسب لفتح تذكرة.", max_length=4000,
    )
    image_input = discord.ui.TextInput(label="Panel image URL (optional)", required=False, max_length=1000)
    ticket_desc_input = discord.ui.TextInput(
        label="Default ticket description", style=discord.TextStyle.paragraph,
        required=False, default="شرح لينا المشكل ديالك بالتفصيل.", max_length=2000,
    )

    def __init__(self, cog: "TicketManager"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        state = {
            "guild_id": interaction.guild.id,
            "title": str(self.title_input).strip(),
            "description": str(self.description_input).strip(),
            "image_url": valid_url(str(self.image_input)),
            "ticket_description": str(self.ticket_desc_input).strip(),
            "mode": "buttons",
            "button_label": "فتح تذكرة",
            "button_emoji": "🎫",
            "category_id": None,
            "channel_id": None,
            "support_role_id": None,
            "options": [{
                "name": "فتح تذكرة", "emoji": "🎫",
                "description": str(self.ticket_desc_input).strip(),
                "ticket_name": "ticket-{user}", "image_url": None,
            }],
        }
        await interaction.response.send_message(
            "خصّص الـPanel ثم نشره:",
            embed=self.cog.preview_embed(state),
            view=PanelBuilderView(self.cog, state),
            ephemeral=True,
        )


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
            "name": str(self.name_input).strip()[:80],
            "emoji": str(self.emoji_input).strip() or "🎫",
            "ticket_name": str(self.ticket_name_input).strip() or "ticket-{user}",
            "description": str(self.description_input).strip(),
            "image_url": valid_url(str(self.image_input)),
        })
        await interaction.response.edit_message(
            embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder
        )


class PanelBuilderView(discord.ui.View):
    def __init__(self, cog: "TicketManager", state: dict, existing_panel_id: Optional[int] = None):
        super().__init__(timeout=900)
        self.cog = cog
        self.state = state
        self.existing_panel_id = existing_panel_id
        self._saving = False
        self.add_item(CategorySelect(self))
        self.add_item(ChannelSelect(self))
        self.add_item(RoleSelect(self))
        self.add_item(ModeSelect(self))

    @discord.ui.button(label="Add Ticket Type", style=discord.ButtonStyle.secondary, emoji="➕", row=4)
    async def add_type(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddTicketTypeModal(self))

    @discord.ui.button(label="Remove Last Type", style=discord.ButtonStyle.secondary, emoji="➖", row=4)
    async def remove_type(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.state["options"]) <= 1:
            return await interaction.response.send_message("❌ خاص Panel يبقى فيه اختيار واحد على الأقل.", ephemeral=True)
        self.state["options"].pop()
        await interaction.response.edit_message(embed=self.cog.preview_embed(self.state), view=self)

    @discord.ui.button(label="Send Panel to Channel", style=discord.ButtonStyle.success, emoji="📤", row=4)
    async def send_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._saving:
            return await interaction.response.send_message("⏳ عملية الحفظ راه خدامة، تسنى لحظة.", ephemeral=True)
        if not self.state.get("category_id") or not self.state.get("channel_id"):
            return await interaction.response.send_message("❌ اختار Category وChannel قبل الحفظ.", ephemeral=True)
        channel = interaction.guild.get_channel(self.state["channel_id"])
        category = interaction.guild.get_channel(self.state["category_id"])
        if not isinstance(channel, discord.TextChannel) or not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ الـChannel أو الـCategory المختار غير صالح.", ephemeral=True)
        me = interaction.guild.me
        if me is None or not channel.permissions_for(me).send_messages or not category.permissions_for(me).manage_channels:
            return await interaction.response.send_message("❌ البوت خاصو صلاحيات إرسال الرسائل وManage Channels فالمكان المختار.", ephemeral=True)

        self._saving = True
        button.disabled = True
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(view=self)

        created_id = None
        sent_message = None
        try:
            if self.existing_panel_id is None:
                created_id = await self.cog.db.create_ticket_panel(self.state)
                panel = await self.cog.db.get_ticket_panel(created_id)
                if not panel:
                    raise RuntimeError("panel row was not readable after insert")
                sent_message = await channel.send(embed=self.cog.panel_embed(panel), view=TicketPanelView(self.cog, panel))
                if not await self.cog.db.update_ticket_panel(created_id, {
                    "channel_id": channel.id, "message_id": sent_message.id,
                }):
                    raise RuntimeError("panel message id could not be saved")
                self.cog.bot.add_view(TicketPanelView(self.cog, panel), message_id=sent_message.id)
                await interaction.followup.send(
                    f"✅ تم حفظ ونشر Panel **#{created_id}** في {channel.mention}.", ephemeral=True
                )
            else:
                panel = await self.cog.db.get_ticket_panel(self.existing_panel_id)
                if not panel or int(panel["guild_id"]) != interaction.guild.id:
                    raise RuntimeError("panel not found")
                await self.cog.db.update_ticket_panel(self.existing_panel_id, {
                    **self.state, "channel_id": channel.id,
                })
                panel = await self.cog.db.get_ticket_panel(self.existing_panel_id)
                old_message = None
                if panel and panel.get("message_id") and int(panel.get("channel_id") or 0) == channel.id:
                    try:
                        old_message = await channel.fetch_message(int(panel["message_id"]))
                    except discord.NotFound:
                        old_message = None
                    except discord.HTTPException:
                        old_message = None
                if old_message:
                    await old_message.edit(embed=self.cog.panel_embed(panel), view=TicketPanelView(self.cog, panel))
                    sent_message = old_message
                else:
                    sent_message = await channel.send(embed=self.cog.panel_embed(panel), view=TicketPanelView(self.cog, panel))
                    await self.cog.db.update_ticket_panel(self.existing_panel_id, {"message_id": sent_message.id})
                self.cog.bot.add_view(TicketPanelView(self.cog, panel), message_id=sent_message.id)
                await interaction.followup.send("✅ تم تحديث ونشر الـPanel بنجاح.", ephemeral=True)
        except Exception as exc:
            print(f"[TicketPanel] save error: {exc!r}")
            if sent_message and self.existing_panel_id is None:
                try:
                    await sent_message.delete()
                except discord.HTTPException:
                    pass
            if created_id is not None:
                try:
                    await self.cog.db.delete_ticket_panel(created_id)
                except Exception as cleanup_exc:
                    print(f"[TicketPanel] rollback error: {cleanup_exc!r}")
            await interaction.followup.send(
                "❌ فشل حفظ البيانات. حاول مرة أخرى. تأكد من صلاحيات البوت ثم عاود المحاولة.",
                ephemeral=True,
            )
        finally:
            self._saving = False
            button.disabled = False


class CategorySelect(discord.ui.ChannelSelect):
    def __init__(self, builder: PanelBuilderView):
        super().__init__(channel_types=[discord.ChannelType.category], placeholder="اختار Ticket Category", row=0)
        self.builder = builder

    async def callback(self, interaction: discord.Interaction):
        self.builder.state["category_id"] = self.values[0].id
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, builder: PanelBuilderView):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="اختار Channel لنشر الـPanel", row=1)
        self.builder = builder

    async def callback(self, interaction: discord.Interaction):
        self.builder.state["channel_id"] = self.values[0].id
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, builder: PanelBuilderView):
        super().__init__(placeholder="اختار Staff / Support Role (اختياري)", row=2, min_values=0, max_values=1)
        self.builder = builder

    async def callback(self, interaction: discord.Interaction):
        self.builder.state["support_role_id"] = self.values[0].id if self.values else None
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class ModeSelect(discord.ui.Select):
    def __init__(self, builder: PanelBuilderView):
        super().__init__(placeholder="Buttons / Select Menu", row=3, options=[
            discord.SelectOption(label="Buttons", value="buttons", emoji="🔘"),
            discord.SelectOption(label="Select Menu", value="select", emoji="📋"),
        ])
        self.builder = builder

    async def callback(self, interaction: discord.Interaction):
        self.builder.state["mode"] = self.values[0]
        await interaction.response.edit_message(embed=self.builder.cog.preview_embed(self.builder.state), view=self.builder)


class TicketHomeView(discord.ui.View):
    def __init__(self, cog: "TicketManager"):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label="Create a Panel", style=discord.ButtonStyle.primary, emoji="➕")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreatePanelModal(self.cog))

    @discord.ui.button(label="Manage Panels", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        panels = await self.cog.db.list_ticket_panels(interaction.guild.id)
        if not panels:
            return await interaction.response.send_message("❌ ما عندك حتى Panel مصايب.", ephemeral=True)
        await interaction.response.edit_message(
            embed=self.cog.manage_embed(panels), view=PanelManagerView(self.cog, panels)
        )


class PanelManagerView(discord.ui.View):
    def __init__(self, cog: "TicketManager", panels: list[dict]):
        super().__init__(timeout=600)
        self.cog = cog
        self.panel_id: Optional[int] = None
        self.add_item(PanelSelect(self, panels))

    @discord.ui.button(label="Edit Panel", style=discord.ButtonStyle.primary, emoji="✏️", row=1)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.panel_id is None:
            return await interaction.response.send_message("❌ اختار Panel أولاً.", ephemeral=True)
        panel = await self.cog.db.get_ticket_panel(self.panel_id)
        if not panel:
            return await interaction.response.send_message("❌ Panel ما بقاتش موجودة.", ephemeral=True)
        await interaction.response.edit_message(
            embed=self.cog.preview_embed(panel), view=PanelBuilderView(self.cog, panel, panel["id"])
        )

    @discord.ui.button(label="Delete Panel", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.panel_id is None:
            return await interaction.response.send_message("❌ اختار Panel أولاً.", ephemeral=True)
        panel = await self.cog.db.get_ticket_panel(self.panel_id)
        if not panel:
            return await interaction.response.send_message("❌ Panel ما بقاتش موجودة.", ephemeral=True)
        if panel.get("channel_id") and panel.get("message_id"):
            channel = interaction.guild.get_channel(int(panel["channel_id"]))
            if channel:
                try:
                    await (await channel.fetch_message(int(panel["message_id"]))).delete()
                except (discord.NotFound, discord.HTTPException):
                    pass
        await self.cog.db.delete_ticket_panel(self.panel_id)
        await interaction.response.edit_message(content="✅ تحيد الـPanel نهائياً.", embed=None, view=None)


class PanelSelect(discord.ui.Select):
    def __init__(self, parent: PanelManagerView, panels: list[dict]):
        options = [discord.SelectOption(
            label=f"#{p['id']} • {str(p.get('title') or 'Panel')[:75]}", value=str(p["id"])
        ) for p in panels[:MAX_OPTIONS]]
        super().__init__(placeholder="اختار Panel...", options=options)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.panel_id = int(self.values[0])
        panel = await self.parent_view.cog.db.get_ticket_panel(self.parent_view.panel_id)
        await interaction.response.edit_message(
            embed=self.parent_view.cog.panel_details(panel), view=self.parent_view
        )


class TicketManager(commands.Cog):
    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self._create_locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _lock_for(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._create_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._create_locks[key] = lock
        return lock

    async def cog_load(self):
        for panel in await self.db.get_all_ticket_panels():
            if panel.get("message_id"):
                try:
                    self.bot.add_view(TicketPanelView(self, panel), message_id=int(panel["message_id"]))
                except Exception as exc:
                    print(f"[TicketPanel] restore error: {exc!r}")
        for row in await self.db.fetchall("SELECT channel_id FROM tickets WHERE status='open' AND channel_id IS NOT NULL"):
            try:
                self.bot.add_view(TicketControls(self, int(row[0])), message_id=None)
            except Exception:
                pass

    async def is_staff(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        return isinstance(member, discord.Member) and (
            member.guild_permissions.administrator or member.guild_permissions.manage_channels
        )

    async def get_open_ticket(self, channel_id: int):
        return await self.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id=? AND status='open' LIMIT 1", (channel_id,)
        )

    async def create_ticket_from_panel(self, interaction: discord.Interaction, panel_id: int, option_index: int):
        if not interaction.guild:
            return await interaction.response.send_message("❌ التذاكر خدامة غير داخل السيرفر.", ephemeral=True)
        panel = await self.db.get_ticket_panel(panel_id)
        if not panel or int(panel["guild_id"]) != interaction.guild.id:
            return await interaction.response.send_message("❌ Panel غير صالح.", ephemeral=True)
        options = panel.get("options", [])
        if option_index < 0 or option_index >= len(options):
            return await interaction.response.send_message("❌ نوع التذكرة غير صالح.", ephemeral=True)
        category = interaction.guild.get_channel(int(panel.get("category_id") or 0))
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Category ديال التذاكر ما بقاتش موجودة.", ephemeral=True)
        me = interaction.guild.me
        if me is None or not category.permissions_for(me).manage_channels:
            return await interaction.response.send_message("❌ البوت خاصو Manage Channels فـCategory ديال التذاكر.", ephemeral=True)

        lock = self._lock_for(interaction.guild.id, interaction.user.id)
        async with lock:
            old = await self.db.fetchone(
                "SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open' LIMIT 1",
                (interaction.guild.id, interaction.user.id),
            )
            if old:
                return await interaction.response.send_message(
                    f"❌ عندك Ticket مفتوحة بالفعل: <#{old['channel_id']}>", ephemeral=True
                )
            await interaction.response.defer(ephemeral=True)
            channel = None
            try:
                item = options[option_index]
                raw_name = str(item.get("ticket_name") or "ticket-{user}")
                raw_name = raw_name.replace("{user}", interaction.user.name).replace("{id}", str(interaction.user.id))
                name = clean_name(raw_name)
                overwrites = {
                    interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    interaction.user: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    ),
                }
                if me:
                    overwrites[me] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True,
                        manage_channels=True, manage_messages=True,
                    )
                if panel.get("support_role_id"):
                    role = interaction.guild.get_role(int(panel["support_role_id"]))
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True, send_messages=True, read_message_history=True
                        )
                channel = await category.create_text_channel(name=name, overwrites=overwrites, reason="Ader Ticket")
                ticket_id = await self.db.create_ticket({
                    "guild_id": interaction.guild.id, "user_id": interaction.user.id,
                    "channel_id": channel.id, "status": "open",
                })
                embed = discord.Embed(
                    title=f"🎫 {str(item.get('name') or 'Ticket')[:256]}",
                    description=f"مرحبا {interaction.user.mention}!\n\n{item.get('description') or panel.get('ticket_description') or ''}",
                    color=EmbedColor.PRIMARY,
                )
                image = valid_url(item.get("image_url"))
                if image:
                    embed.set_image(url=image)
                await channel.send(content=interaction.user.mention, embed=embed,
                                   view=TicketControls(self, channel.id))
                self.bot.add_view(TicketControls(self, channel.id))
                await interaction.followup.send(
                    f"✅ تفتحات التذكرة: {channel.mention} (ID `{ticket_id}`)", ephemeral=True
                )
            except Exception as exc:
                print(f"[Ticket] create error: {exc!r}")
                if channel:
                    try:
                        await self.db.execute("UPDATE tickets SET status='deleted' WHERE channel_id=? AND status='open'", (channel.id,))
                    except Exception:
                        pass
                    try:
                        await channel.delete(reason="Ticket creation rollback")
                    except discord.HTTPException:
                        pass
                await interaction.followup.send("❌ فشل إنشاء التذكرة. حاول مرة أخرى.", ephemeral=True)

    async def close_ticket(self, interaction: discord.Interaction, channel_id: int):
        ticket = await self.get_open_ticket(channel_id)
        if not ticket:
            return await interaction.response.send_message("❌ التذكرة غير موجودة أو تسدات.", ephemeral=True)
        if interaction.user.id != int(ticket["user_id"]) and not await self.is_staff(interaction):
            return await interaction.response.send_message("❌ غير صاحب التذكرة أو Staff يقدر يسدها.", ephemeral=True)
        cur = await self.db.execute(
            "UPDATE tickets SET status='closed', closed_at=? WHERE id=? AND status='open'",
            (discord.utils.utcnow().timestamp(), ticket["id"]),
        )
        if cur.rowcount != 1:
            return await interaction.response.send_message("❌ التذكرة تسدات من قبل.", ephemeral=True)
        await interaction.response.send_message("🔒 تسدات التذكرة. غادي يتحيد الروم بعد 5 ثواني.")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason="Ader ticket close")
        except discord.HTTPException:
            pass

    def panel_embed(self, panel: dict) -> discord.Embed:
        embed = discord.Embed(
            title=str(panel.get("title") or "🎫 الدعم الفني")[:256],
            description=str(panel.get("description") or "اختار القسم المناسب لفتح تذكرة.")[:4096],
            color=EmbedColor.PRIMARY,
        )
        image = valid_url(panel.get("image_url"))
        if image:
            embed.set_image(url=image)
        return embed

    def preview_embed(self, panel: dict) -> discord.Embed:
        embed = self.panel_embed(panel)
        embed.add_field(name="Mode", value="Select Menu" if panel.get("mode") == "select" else "Buttons", inline=True)
        embed.add_field(name="Types", value=str(len(panel.get("options", []))), inline=True)
        embed.add_field(name="Category", value=f"<#{panel['category_id']}>" if panel.get("category_id") else "❌", inline=True)
        embed.add_field(name="Channel", value=f"<#{panel['channel_id']}>" if panel.get("channel_id") else "❌", inline=True)
        return embed

    def panel_details(self, panel: dict) -> discord.Embed:
        embed = self.panel_embed(panel)
        types = "\n".join(
            f"{x.get('emoji', '🎫')} {x.get('name', 'Ticket')} → `{x.get('ticket_name', 'ticket-{user}')}`"
            for x in panel.get("options", [])[:MAX_OPTIONS]
        ) or "ما كاين حتى نوع."
        embed.add_field(name="Ticket Types", value=types[:1024], inline=False)
        return embed

    def manage_embed(self, panels: list[dict]) -> discord.Embed:
        lines = [f"`#{p['id']}` **{str(p.get('title') or 'Panel')[:80]}**" for p in panels[:MAX_OPTIONS]]
        return discord.Embed(title="🎫 Manage Panels", description="\n".join(lines), color=EmbedColor.PRIMARY)

    @app_commands.command(name="ticket", description="Open the Ader Ticket Tool manager")
    @is_admin()
    async def ticket_cmd(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎫 Ader Ticket Tool",
            description="استعمل **Create a Panel** لإنشاء Panel أو **Manage Panels** لتعديل/حذف Panels.",
            color=EmbedColor.PRIMARY,
        )
        await interaction.response.send_message(embed=embed, view=TicketHomeView(self), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketManager(bot, bot.db, bot.config))
