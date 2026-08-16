"""Single-command Ticket Tool-style manager for Ader."""
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
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return value[:90] or fallback

class TicketControls(discord.ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=None)
        self.add_item(TicketClaim(cog, channel_id)); self.add_item(TicketClose(cog, channel_id)); self.add_item(TicketDelete(cog, channel_id))

class TicketClaim(discord.ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id=f"ader:t:claim:{channel_id}"); self.cog=cog; self.channel_id=channel_id
    async def callback(self, interaction):
        if not await self.cog.is_staff(interaction): return await interaction.response.send_message("❌ هاد الزر مخصص للـStaff.", ephemeral=True)
        ticket=await self.cog.get_ticket(self.channel_id)
        if not ticket: return await interaction.response.send_message("❌ التذكرة ما بقاتش مفتوحة.", ephemeral=True)
        if ticket["user_id"]==interaction.user.id: return await interaction.response.send_message("❌ صاحب التذكرة ما يقدرش يدير Claim لنفسو.", ephemeral=True)
        cur=await self.cog.db.execute("UPDATE tickets SET claimed_by=? WHERE id=? AND status='open' AND claimed_by IS NULL",(interaction.user.id,ticket["id"]))
        if cur.rowcount!=1: return await interaction.response.send_message("❌ شي Staff آخر تكفّل بالتذكرة قبل منك.", ephemeral=True)
        await interaction.response.send_message(f"🙋 {interaction.user.mention} تكفّل بالتذكرة.")

class TicketClose(discord.ui.Button):
    def __init__(self,cog,channel_id):
        super().__init__(label="Close",emoji="🔒",style=discord.ButtonStyle.secondary,custom_id=f"ader:t:close:{channel_id}"); self.cog=cog; self.channel_id=channel_id
    async def callback(self,interaction): await self.cog.close_ticket(interaction,self.channel_id)

class TicketDelete(discord.ui.Button):
    def __init__(self,cog,channel_id):
        super().__init__(label="Delete",emoji="🗑️",style=discord.ButtonStyle.danger,custom_id=f"ader:t:delete:{channel_id}"); self.cog=cog; self.channel_id=channel_id
    async def callback(self,interaction):
        if not await self.cog.is_staff(interaction): return await interaction.response.send_message("❌ حذف التذكرة مخصص للـStaff.",ephemeral=True)
        ticket=await self.cog.get_ticket(self.channel_id)
        if not ticket:return await interaction.response.send_message("❌ التذكرة غير موجودة.",ephemeral=True)
        cur=await self.cog.db.execute("UPDATE tickets SET status='deleted',closed_at=? WHERE id=? AND status='open'",(discord.utils.utcnow().timestamp(),ticket["id"]))
        if cur.rowcount!=1:return await interaction.response.send_message("❌ التذكرة تسدات من قبل.",ephemeral=True)
        await interaction.response.send_message("🗑️ غادي يتحيد الروم بعد 3 ثواني."); await asyncio.sleep(3)
        try: await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except discord.HTTPException: pass

class TicketOpenButton(discord.ui.Button):
    def __init__(self,cog,panel_id,index,label,emoji):
        super().__init__(label=(label or "فتح تذكرة")[:80],emoji=emoji or "🎫",style=discord.ButtonStyle.primary,custom_id=f"ader:t:open:{panel_id}:{index}"); self.cog=cog; self.panel_id=panel_id; self.index=index
    async def callback(self,interaction): await self.cog.create_ticket(interaction,self.panel_id,self.index)

class TicketOpenSelect(discord.ui.Select):
    def __init__(self,cog,panel):
        opts=[discord.SelectOption(label=str(x.get("name") or "فتح تذكرة")[:100],description=str(x.get("description") or "فتح تذكرة")[:100],emoji=x.get("emoji") or "🎫",value=str(i)) for i,x in enumerate(panel.get("options",[])[:MAX_OPTIONS])]
        super().__init__(placeholder="اختار نوع التذكرة...",options=opts or [discord.SelectOption(label="فتح تذكرة",value="0",emoji="🎫")],custom_id=f"ader:t:select:{panel['id']}"); self.cog=cog; self.panel_id=panel["id"]
    async def callback(self,interaction): await self.cog.create_ticket(interaction,self.panel_id,int(self.values[0]))

class TicketPanelView(discord.ui.View):
    def __init__(self,cog,panel):
        super().__init__(timeout=None); options=panel.get("options",[]) or [{"name":"فتح تذكرة","emoji":"🎫"}]
        if panel.get("mode")=="select": self.add_item(TicketOpenSelect(cog,{**panel,"options":options}))
        else:
            for i,x in enumerate(options[:MAX_OPTIONS]): self.add_item(TicketOpenButton(cog,panel["id"],i,x.get("name"),x.get("emoji","🎫")))

class HomeView(discord.ui.View):
    def __init__(self,cog): super().__init__(timeout=300); self.cog=cog
    @discord.ui.button(label="Create a Panel",emoji="➕",style=discord.ButtonStyle.primary)
    async def create(self,interaction,button): await interaction.response.send_modal(CreatePanelModal(self.cog))
    @discord.ui.button(label="Manage Panels",emoji="⚙️",style=discord.ButtonStyle.secondary)
    async def manage(self,interaction,button):
        panels=await self.cog.db.list_ticket_panels(interaction.guild.id)
        if not panels:return await interaction.response.send_message("❌ ما كاين حتى Panel. استعمل **Create a Panel** أولاً.",ephemeral=True)
        await interaction.response.edit_message(embed=self.cog.manage_embed(panels),view=ManageView(self.cog,panels))

class CreatePanelModal(discord.ui.Modal,title="Create a Panel"):
    title_input=discord.ui.TextInput(label="Panel title",default="🎫 الدعم الفني",max_length=256)
    description_input=discord.ui.TextInput(label="Panel description",style=discord.TextStyle.paragraph,default="اختار القسم المناسب لفتح تذكرة.",max_length=4000)
    image_input=discord.ui.TextInput(label="Panel image URL",required=False,max_length=1000)
    ticket_input=discord.ui.TextInput(label="Default ticket description",style=discord.TextStyle.paragraph,default="شرح لينا المشكل ديالك بالتفصيل.",max_length=2000)
    def __init__(self,cog):super().__init__();self.cog=cog
    async def on_submit(self,interaction):
        state={"guild_id":interaction.guild.id,"title":str(self.title_input),"description":str(self.description_input),"image_url":str(self.image_input).strip() or None,"ticket_description":str(self.ticket_input),"mode":"buttons","category_id":None,"channel_id":None,"support_role_id":None,"button_label":"فتح تذكرة","button_emoji":"🎫","options":[{"name":"فتح تذكرة","emoji":"🎫","description":str(self.ticket_input),"ticket_name":"ticket-{user}","image_url":None}]}
        await interaction.response.send_message("خصّص الـPanel ثم اختار **Send Panel to Channel**:",embed=self.cog.preview(state),view=BuilderView(self.cog,state),ephemeral=True)

class AddTypeModal(discord.ui.Modal,title="Add Ticket Type"):
    name_input=discord.ui.TextInput(label="Button / option name",max_length=80)
    emoji_input=discord.ui.TextInput(label="Emoji",default="🎫",max_length=20)
    channel_name_input=discord.ui.TextInput(label="Ticket channel name",default="ticket-{user}",max_length=80)
    description_input=discord.ui.TextInput(label="Ticket description",style=discord.TextStyle.paragraph,max_length=2000)
    image_input=discord.ui.TextInput(label="Ticket image URL",required=False,max_length=1000)
    def __init__(self,builder):super().__init__();self.builder=builder
    async def on_submit(self,interaction):
        if len(self.builder.state["options"])>=MAX_OPTIONS:return await interaction.response.send_message("❌ الحد الأقصى هو 25 Ticket Types.",ephemeral=True)
        self.builder.state["options"].append({"name":str(self.name_input),"emoji":str(self.emoji_input) or "🎫","ticket_name":str(self.channel_name_input) or "ticket-{user}","description":str(self.description_input),"image_url":str(self.image_input).strip() or None})
        await interaction.response.edit_message(embed=self.builder.cog.preview(self.builder.state),view=self.builder)

class BuilderView(discord.ui.View):
    def __init__(self,cog,state,panel_id=None):
        super().__init__(timeout=900);self.cog=cog;self.state=state;self.panel_id=panel_id
        self.add_item(CategorySelect(self));self.add_item(ChannelSelect(self));self.add_item(RoleSelect(self));self.add_item(ModeSelect(self))
    @discord.ui.button(label="Add Ticket Type",emoji="➕",style=discord.ButtonStyle.secondary,row=3)
    async def add_type(self,interaction,button):await interaction.response.send_modal(AddTypeModal(self))
    @discord.ui.button(label="Remove Last Type",emoji="➖",style=discord.ButtonStyle.secondary,row=4)
    async def remove_type(self,interaction,button):
        if len(self.state["options"])<=1:return await interaction.response.send_message("❌ خاص يبقى Ticket Type واحد على الأقل.",ephemeral=True)
        self.state["options"].pop();await interaction.response.edit_message(embed=self.cog.preview(self.state),view=self)
    @discord.ui.button(label="Send Panel to Channel",emoji="📤",style=discord.ButtonStyle.success,row=4)
    async def send_panel(self,interaction,button):
        if not self.state.get("category_id") or not self.state.get("channel_id"):return await interaction.response.send_message("❌ خاصك تختار Category وChannel قبل النشر.",ephemeral=True)
        channel=interaction.guild.get_channel(self.state["channel_id"]);category=interaction.guild.get_channel(self.state["category_id"])
        if not isinstance(channel,discord.TextChannel) or not isinstance(category,discord.CategoryChannel):return await interaction.response.send_message("❌ Category أو Channel غير صالح.",ephemeral=True)
        await interaction.response.defer(ephemeral=True); panel_id=self.panel_id; created=False
        try:
            if panel_id:
                if not await self.cog.db.get_ticket_panel(panel_id):return await interaction.followup.send("❌ Panel ما بقاتش موجودة.",ephemeral=True)
                await self.cog.db.update_ticket_panel(panel_id,{**self.state,"channel_id":channel.id});panel=await self.cog.db.get_ticket_panel(panel_id)
            else:
                panel_id=await self.cog.db.create_ticket_panel({**self.state,"channel_id":channel.id});created=True;panel=await self.cog.db.get_ticket_panel(panel_id)
            sent=await channel.send(embed=self.cog.panel_embed(panel),view=TicketPanelView(self.cog,panel))
            await self.cog.db.update_ticket_panel(panel_id,{"channel_id":channel.id,"message_id":sent.id});self.cog.bot.add_view(TicketPanelView(self.cog,panel),message_id=sent.id)
            await interaction.followup.send(f"✅ تم حفظ ونشر Panel **#{panel_id}** في {channel.mention}.",ephemeral=True)
        except Exception as exc:
            print(f"Ticket panel save/publish error: {exc!r}")
            if created and panel_id:
                try: await self.cog.db.delete_ticket_panel(panel_id)
                except Exception: pass
            await interaction.followup.send("❌ فشل حفظ/نشر الـPanel. تأكد من صلاحيات البوت على الـChannel والـCategory وحاول مرة أخرى.",ephemeral=True)

class CategorySelect(discord.ui.ChannelSelect):
    def __init__(self,builder):super().__init__(channel_types=[discord.ChannelType.category],placeholder="اختار Ticket Category",row=0);self.builder=builder
    async def callback(self,interaction):self.builder.state["category_id"]=self.values[0].id;await interaction.response.edit_message(embed=self.builder.cog.preview(self.builder.state),view=self.builder)
class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self,builder):super().__init__(channel_types=[discord.ChannelType.text],placeholder="اختار Channel لنشر الـPanel",row=1);self.builder=builder
    async def callback(self,interaction):self.builder.state["channel_id"]=self.values[0].id;await interaction.response.edit_message(embed=self.builder.cog.preview(self.builder.state),view=self.builder)
class RoleSelect(discord.ui.RoleSelect):
    def __init__(self,builder):super().__init__(placeholder="اختار Staff / Support Role (اختياري)",row=2,min_values=0,max_values=1);self.builder=builder
    async def callback(self,interaction):self.builder.state["support_role_id"]=self.values[0].id if self.values else None;await interaction.response.edit_message(embed=self.builder.cog.preview(self.builder.state),view=self.builder)
class ModeSelect(discord.ui.Select):
    def __init__(self,builder):super().__init__(placeholder="اختار Buttons أو Select Menu",options=[discord.SelectOption(label="Buttons",value="buttons",emoji="🔘"),discord.SelectOption(label="Select Menu",value="select",emoji="📋")],row=3);self.builder=builder
    async def callback(self,interaction):self.builder.state["mode"]=self.values[0];await interaction.response.edit_message(embed=self.builder.cog.preview(self.builder.state),view=self.builder)

class ManageView(discord.ui.View):
    def __init__(self,cog,panels):super().__init__(timeout=600);self.cog=cog;self.selected_id=None;self.add_item(PanelSelect(self,panels))
    @discord.ui.button(label="Edit Panel",emoji="✏️",style=discord.ButtonStyle.primary,row=1)
    async def edit(self,interaction,button):
        if not self.selected_id:return await interaction.response.send_message("❌ اختار Panel أولاً.",ephemeral=True)
        p=await self.cog.db.get_ticket_panel(self.selected_id)
        if not p:return await interaction.response.send_message("❌ Panel ما بقاتش موجودة.",ephemeral=True)
        await interaction.response.edit_message(content="خصّص الـPanel ثم Send Panel:",embed=self.cog.preview(p),view=BuilderView(self.cog,p,p["id"]))
    @discord.ui.button(label="Send Panel",emoji="📤",style=discord.ButtonStyle.success,row=1)
    async def send(self,interaction,button):
        if not self.selected_id:return await interaction.response.send_message("❌ اختار Panel أولاً.",ephemeral=True)
        p=await self.cog.db.get_ticket_panel(self.selected_id)
        if not p:return await interaction.response.send_message("❌ Panel ما بقاتش موجودة.",ephemeral=True)
        await interaction.response.edit_message(content="اختار Channel ثم انشر:",embed=self.cog.preview(p),view=BuilderView(self.cog,p,p["id"]))
    @discord.ui.button(label="Delete Panel",emoji="🗑️",style=discord.ButtonStyle.danger,row=1)
    async def delete(self,interaction,button):
        if not self.selected_id:return await interaction.response.send_message("❌ اختار Panel أولاً.",ephemeral=True)
        p=await self.cog.db.get_ticket_panel(self.selected_id)
        if p and p.get("channel_id") and p.get("message_id"):
            ch=interaction.guild.get_channel(p["channel_id"])
            if ch:
                try: await (await ch.fetch_message(p["message_id"])).delete()
                except discord.HTTPException: pass
        await self.cog.db.delete_ticket_panel(self.selected_id);await interaction.response.edit_message(content="✅ تحيد الـPanel.",embed=None,view=None)
class PanelSelect(discord.ui.Select):
    def __init__(self,parent,panels):super().__init__(placeholder="اختار Panel...",options=[discord.SelectOption(label=f"#{p['id']} • {p['title'][:80]}",value=str(p['id'])) for p in panels[:25]]);self.parent=parent
    async def callback(self,interaction):self.parent.selected_id=int(self.values[0]);p=await self.parent.cog.db.get_ticket_panel(self.parent.selected_id);await interaction.response.edit_message(embed=self.parent.cog.panel_details(p),view=self.parent)

class TicketManager(commands.Cog):
    def __init__(self,bot,db,config):self.bot=bot;self.db=db;self.config=config
    async def cog_load(self):
        for p in await self.db.get_all_ticket_panels():
            if p.get("message_id"):
                try:self.bot.add_view(TicketPanelView(self,p),message_id=p["message_id"])
                except Exception as exc:print(f"Ticket panel restore error: {exc!r}")
    async def is_staff(self,interaction):
        if not isinstance(interaction.user,discord.Member):return False
        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_channels:return True
        g=await self.db.get_guild(interaction.guild.id);rid=g.get("modules",{}).get("support_role") if g else None
        return bool(rid and interaction.user.get_role(int(rid)))
    async def get_ticket(self,channel_id):return await self.db.fetchone("SELECT * FROM tickets WHERE channel_id=? AND status='open'",(channel_id,))
    def panel_embed(self,p):
        e=discord.Embed(title=str(p.get("title") or "🎫 الدعم الفني")[:256],description=str(p.get("description") or "اختار القسم المناسب لفتح تذكرة.")[:4096],color=EmbedColor.PRIMARY)
        if p.get("image_url"):e.set_image(url=p["image_url"])
        return e
    def preview(self,s):
        e=self.panel_embed(s);e.add_field(name="Mode",value="Select Menu" if s.get("mode")=="select" else "Buttons",inline=True);e.add_field(name="Ticket Types",value=str(len(s.get("options",[]))),inline=True);e.add_field(name="Category",value=f"<#{s['category_id']}>" if s.get("category_id") else "❌",inline=True);e.add_field(name="Panel Channel",value=f"<#{s['channel_id']}>" if s.get("channel_id") else "❌",inline=True);e.add_field(name="Support Role",value=f"<@&{s['support_role_id']}>" if s.get("support_role_id") else "اختياري",inline=True);return e
    def panel_details(self,p):
        e=self.panel_embed(p);opts=p.get("options",[]);text="\n".join(f"{i+1}. {x.get('emoji','🎫')} {x.get('name','Ticket')} → `{x.get('ticket_name','ticket-{user}')}`" for i,x in enumerate(opts[:25])) or "ما كاين حتى Type";e.add_field(name="Ticket Types",value=text[:1024],inline=False);return e
    def manage_embed(self,panels):return discord.Embed(title="🎫 Manage Panels",description="اختار Panel ثم Edit / Send / Delete.",color=EmbedColor.PRIMARY)
    async def create_ticket(self,interaction,panel_id,index):
        p=await self.db.get_ticket_panel(panel_id)
        if not p or p["guild_id"]!=interaction.guild.id:return await interaction.response.send_message("❌ هاد الـPanel ما بقاش موجود.",ephemeral=True)
        opts=p.get("options",[])
        if index<0 or index>=len(opts):return await interaction.response.send_message("❌ نوع التذكرة غير صالح.",ephemeral=True)
        cat=interaction.guild.get_channel(p.get("category_id"))
        if not isinstance(cat,discord.CategoryChannel):return await interaction.response.send_message("❌ Ticket Category ما بقاتش موجودة.",ephemeral=True)
        existing=await self.db.fetchone("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open'",(interaction.guild.id,interaction.user.id))
        if existing:
            ch=interaction.guild.get_channel(existing[0]);return await interaction.response.send_message(f"❌ عندك Ticket مفتوحة بالفعل: {ch.mention if ch else 'غير متاحة'}",ephemeral=True)
        x=opts[index];name=clean_name(str(x.get("ticket_name") or "ticket-{user}").replace("{user}",interaction.user.name).replace("{id}",str(interaction.user.id)),f"ticket-{interaction.user.id}")
        ow={interaction.guild.default_role:discord.PermissionOverwrite(view_channel=False),interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True)}
        if interaction.guild.me:ow[interaction.guild.me]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True,manage_messages=True)
        if p.get("support_role_id"):
            role=interaction.guild.get_role(int(p["support_role_id"]));
            if role:ow[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        await interaction.response.defer(ephemeral=True);channel=None
        try:
            channel=await cat.create_text_channel(name,overwrites=ow,reason="Ader Ticket")
            tid=await self.db.create_ticket({"guild_id":interaction.guild.id,"user_id":interaction.user.id,"channel_id":channel.id,"status":"open"})
        except Exception as exc:
            print(f"Ticket create error: {exc!r}")
            if channel:
                try:await channel.delete(reason="Ticket DB save failed")
                except discord.HTTPException:pass
            return await interaction.followup.send("❌ ما قدرناش ننشئو التذكرة. تأكد من صلاحيات البوت وحاول مرة أخرى.",ephemeral=True)
        e=discord.Embed(title=f"🎫 {x.get('name','Ticket')}",description=f"مرحبا {interaction.user.mention}!\n\n{x.get('description') or p.get('ticket_description','')}",color=EmbedColor.PRIMARY)
        if x.get("image_url"):e.set_image(url=x["image_url"])
        await channel.send(content=interaction.user.mention,embed=e,view=TicketControls(self,channel.id));self.bot.add_view(TicketControls(self,channel.id));await interaction.followup.send(f"✅ تفتحات التذكرة ديالك: {channel.mention} (ID `{tid}`)",ephemeral=True)
    async def close_ticket(self,interaction,channel_id):
        t=await self.get_ticket(channel_id)
        if not t:return await interaction.response.send_message("❌ هادي ماشي تذكرة مفتوحة.",ephemeral=True)
        if interaction.user.id!=t["user_id"] and not await self.is_staff(interaction):return await interaction.response.send_message("❌ غير صاحب التذكرة أو الـStaff يقدر يسدها.",ephemeral=True)
        cur=await self.db.execute("UPDATE tickets SET status='closed',closed_at=? WHERE id=? AND status='open'",(discord.utils.utcnow().timestamp(),t["id"]))
        if cur.rowcount!=1:return await interaction.response.send_message("❌ التذكرة تسدات من قبل.",ephemeral=True)
        await interaction.response.send_message("🔒 تسدات التذكرة. غادي يتحيد الروم بعد 5 ثواني.");await asyncio.sleep(5)
        try:await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:pass
    @app_commands.command(name="ticket",description="Open the Ader Ticket Tool manager")
    @is_admin()
    async def ticket(self,interaction):
        await interaction.response.send_message(embed=discord.Embed(title="🎫 Ader Ticket Tool",description="**Create a Panel** لإنشاء Panel جديد\n**Manage Panels** لإدارة Panels الموجودة",color=EmbedColor.PRIMARY),view=HomeView(self),ephemeral=True)

async def setup(bot):await bot.add_cog(TicketManager(bot,bot.db,bot.config))
