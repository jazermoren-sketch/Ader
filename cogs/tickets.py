"""Simple Ticket Tool-style ticket system for Ader."""

import asyncio
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import EmbedColor
from utils.permissions import is_admin


class OpenTicketButton(discord.ui.Button):
    def __init__(self, cog, category_id, support_role_id, ticket_description, label, emoji):
        super().__init__(label=label[:80] or "فتح تذكرة", style=discord.ButtonStyle.primary, emoji=emoji or "🎫", custom_id=f"ader_ticket_open:{category_id}")
        self.cog = cog
        self.category_id = category_id
        self.support_role_id = support_role_id
        self.ticket_description = ticket_description

    async def callback(self, interaction: discord.Interaction):
        await self.cog.create_ticket(interaction, self.category_id, self.support_role_id, self.ticket_description)


class TicketPanelView(discord.ui.View):
    def __init__(self, cog, category_id, support_role_id, ticket_description, label, emoji):
        super().__init__(timeout=None)
        self.add_item(OpenTicketButton(cog, category_id, support_role_id, ticket_description, label, emoji))


class TicketControls(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, emoji="🙋", custom_id="ader_ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.is_staff(interaction):
            return await interaction.response.send_message("❌ هاد الزر مخصص للـStaff.", ephemeral=True)
        ticket = await self.cog.get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message("❌ هادي ماشي تذكرة مسجلة.", ephemeral=True)
        if ticket["claimed_by"]:
            return await interaction.response.send_message(f"❌ التذكرة متكفّل بها <@{ticket['claimed_by']}>.", ephemeral=True)
        await self.cog.db.update_ticket(str(ticket["id"]), {"claimed_by": interaction.user.id})
        await interaction.response.send_message(f"🙋 {interaction.user.mention} تكفّل بالتذكرة.")

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="ader_ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_ticket(interaction)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="ader_ticket_delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.is_staff(interaction):
            return await interaction.response.send_message("❌ حذف التذكرة مخصص للـStaff.", ephemeral=True)
        await interaction.response.send_message("🗑️ غادي يتم حذف التذكرة بعد 3 ثواني.")
        await asyncio.sleep(3)
        await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")


class Tickets(commands.Cog):
    """One-command Ticket Tool-style system; no panel IDs or setup commands."""

    def __init__(self, bot, db, config):
        self.bot = bot
        self.db = db
        self.config = config

    async def is_staff(self, interaction):
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels:
            return True
        guild = await self.db.get_guild(interaction.guild.id)
        role_id = guild.get("support_role") if guild else None
        return bool(role_id and interaction.user.get_role(role_id))

    async def get_ticket(self, channel_id):
        return await self.db.fetchone("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (channel_id,))

    async def create_ticket(self, interaction, category_id, support_role_id, description):
        category = interaction.guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Category ديال التذاكر ما بقاتش موجودة.", ephemeral=True)
        existing = await self.db.fetchone("SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND status='open'", (interaction.guild.id, interaction.user.id))
        if existing:
            channel = interaction.guild.get_channel(existing["channel_id"])
            return await interaction.response.send_message(f"❌ عندك تذكرة مفتوحة بالفعل: {channel.mention if channel else 'غير متاحة'}", ephemeral=True)
        safe_name = "".join(ch for ch in interaction.user.name.lower() if ch.isalnum() or ch in "-_")[:18] or "user"
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        if support_role_id:
            role = interaction.guild.get_role(support_role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await category.create_text_channel(f"ticket-{safe_name}", overwrites=overwrites, reason="Ader ticket created")
        ticket_id = await self.db.create_ticket({"guild_id": interaction.guild.id, "user_id": interaction.user.id, "channel_id": channel.id, "status": "open", "data": {"description": description}})
        embed = discord.Embed(title="🎫 Ticket", description=f"مرحبا {interaction.user.mention}!\n\n{description}\n\n**Ticket ID:** `{ticket_id}`", color=EmbedColor.PRIMARY)
        await channel.send(content=interaction.user.mention, embed=embed, view=TicketControls(self))
        await interaction.response.send_message(f"✅ تفتحات التذكرة ديالك: {channel.mention}", ephemeral=True)

    async def close_ticket(self, interaction):
        ticket = await self.get_ticket(interaction.channel.id)
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

    @app_commands.command(name="ticket", description="Create a simple Ticket Tool-style panel")
    @app_commands.describe(category="Category where tickets will be created", support_role="Role that can access tickets", title="Panel title", description="Panel description", ticket_description="Description inside a ticket", button_label="Button text", button_emoji="Button emoji", image_url="Optional panel image URL")
    @is_admin()
    async def ticket(self, interaction, category: discord.CategoryChannel, support_role: Optional[discord.Role] = None, title: str = "🎫 الدعم الفني", description: str = "اضغط على الزر بالأسفل لفتح تذكرة خاصة مع فريق الدعم.", ticket_description: str = "شرح لينا المشكل ديالك بالتفصيل، وغادي يساعدك فريق الدعم في أقرب وقت.", button_label: str = "فتح تذكرة", button_emoji: str = "🎫", image_url: Optional[str] = None):
        await self.db.create_guild(interaction.guild.id)
        await self.db.update_guild(interaction.guild.id, {"support_role": support_role.id if support_role else None, "ticket_category": category.id})
        embed = discord.Embed(title=title[:256], description=description[:4096], color=EmbedColor.PRIMARY)
        if image_url:
            embed.set_image(url=image_url)
        embed.set_footer(text="Ader Tickets • Ticket Tool style")
        view = TicketPanelView(self, category.id, support_role.id if support_role else None, ticket_description, button_label, button_emoji)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ تم نشر Ticket Panel بنجاح.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tickets(bot, bot.db, bot.config))
