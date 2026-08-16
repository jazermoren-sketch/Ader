from __future__ import annotations
import asyncio,re,discord
from discord import app_commands
from discord.ext import commands
from utils.embeds import EmbedColor
from utils.permissions import is_admin

MAX_OPTIONS=25

def clean(v): return re.sub(r"[^a-zA-Z0-9_-]+","-",str(v).strip().lower()).strip("-")[:90] or "ticket"

class TicketControls(discord.ui.View):
    def __init__(self,cog,cid):
        super().__init__(timeout=None);self.add_item(Claim(cog,cid));self.add_item(Close(cog,cid));self.add_item(Delete(cog,cid))
class Claim(discord.ui.Button):
    def __init__(self,cog,cid):super().__init__(label="Claim",emoji="🙋",style=discord.ButtonStyle.success,custom_id=f"ader:t:c:{cid}");self.cog=cog;self.cid=cid
    async def callback(self,i):
        if not await self.cog.staff(i):return await i.response.send_message("❌ Staff فقط.",ephemeral=True)
        t=await self.cog.ticket(self.cid)
        if not t:return await i.response.send_message("❌ التذكرة مسدودة.",ephemeral=True)
        if t["user_id"]==i.user.id:return await i.response.send_message("❌ صاحب التذكرة ما يقدرش يدير Claim لنفسو.",ephemeral=True)
        r=await self.cog.db.execute("UPDATE tickets SET claimed_by=? WHERE id=? AND status='open' AND claimed_by IS NULL",(i.user.id,t["id"]))
        if r.rowcount!=1:return await i.response.send_message("❌ شي Staff آخر تكفّل بها.",ephemeral=True)
        await i.response.send_message(f"🙋 {i.user.mention} تكفّل بالتذكرة.")
class Close(discord.ui.Button):
    def __init__(self,cog,cid):super().__init__(label="Close",emoji="🔒",style=discord.ButtonStyle.secondary,custom_id=f"ader:t:o:{cid}");self.cog=cog;self.cid=cid
    async def callback(self,i):await self.cog.close(i,self.cid)
class Delete(discord.ui.Button):
    def __init__(self,cog,cid):super().__init__(label="Delete",emoji="🗑️",style=discord.ButtonStyle.danger,custom_id=f"ader:t:d:{cid}");self.cog=cog;self.cid=cid
    async def callback(self,i):
        if not await self.cog.staff(i):return await i.response.send_message("❌ Staff فقط.",ephemeral=True)
        r=await self.cog.db.execute("UPDATE tickets SET status='deleted',closed_at=? WHERE channel_id=? AND status='open'",(discord.utils.utcnow().timestamp(),self.cid))
        if r.rowcount!=1:return await i.response.send_message("❌ التذكرة تسدات من قبل.",ephemeral=True)
        await i.response.send_message("🗑️ غادي يتحيد الروم بعد 2 ثواني.");await asyncio.sleep(2)
        try:await i.channel.delete(reason="Ader ticket delete")
        except discord.HTTPException:pass

class OpenButton(discord.ui.Button):
    def __init__(self,cog,pid,n,x):super().__init__(label=str(x.get("name") or "فتح تذكرة")[:80],emoji=x.get("emoji") or "🎫",style=discord.ButtonStyle.primary,custom_id=f"ader:t:open:{pid}:{n}");self.cog=cog;self.pid=pid;self.n=n
    async def callback(self,i):await self.cog.create(i,self.pid,self.n)
class OpenSelect(discord.ui.Select):
    def __init__(self,cog,p):
        opts=[discord.SelectOption(label=str(x.get("name") or "Ticket")[:100],description=str(x.get("description") or "فتح تذكرة")[:100],emoji=x.get("emoji") or "🎫",value=str(n)) for n,x in enumerate(p.get("options",[])[:25])]
        super().__init__(placeholder="اختار نوع التذكرة...",options=opts or [discord.SelectOption(label="فتح تذكرة",value="0",emoji="🎫")],custom_id=f"ader:t:s:{p['id']}");self.cog=cog;self.pid=p["id"]
    async def callback(self,i):await self.cog.create(i,self.pid,int(self.values[0]))
class PanelView(discord.ui.View):
    def __init__(self,cog,p):
        super().__init__(timeout=None);xs=p.get("options",[]) or [{"name":"فتح تذكرة","emoji":"🎫"}]
        if p.get("mode")=="select":self.add_item(OpenSelect(cog,{**p,"options":xs}))
        else:
            for n,x in enumerate(xs[:25]):self.add_item(OpenButton(cog,p["id"],n,x))

class CreateModal(discord.ui.Modal,title="Create a Panel"):
    titlex=discord.ui.TextInput(label="Panel title",default="🎫 الدعم الفني",max_length=256)
    desc=discord.ui.TextInput(label="Panel description",style=discord.TextStyle.paragraph,default="اختار القسم المناسب لفتح تذكرة.",max_length=4000)
    image=discord.ui.TextInput(label="Panel image URL",required=False,max_length=1000)
    ticketdesc=discord.ui.TextInput(label="Default ticket description",style=discord.TextStyle.paragraph,default="شرح لينا المشكل ديالك بالتفصيل.",max_length=2000)
    def __init__(self,cog):super().__init__();self.cog=cog
    async def on_submit(self,i):
        s={"guild_id":i.guild.id,"title":str(self.titlex),"description":str(self.desc),"image_url":str(self.image).strip() or None,"ticket_description":str(self.ticketdesc),"mode":"buttons","category_id":None,"channel_id":None,"support_role_id":None,"button_label":"فتح تذكرة","button_emoji":"🎫","options":[{"name":"فتح تذكرة","emoji":"🎫","description":str(self.ticketdesc),"ticket_name":"ticket-{user}","image_url":None}]}
        await i.response.send_message("خصّص الـPanel ثم نشره:",embed=self.cog.preview(s),view=Builder(self.cog,s),ephemeral=True)
class TypeModal(discord.ui.Modal,title="Add Ticket Type"):
    name=discord.ui.TextInput(label="Name",max_length=80);emoji=discord.ui.TextInput(label="Emoji",default="🎫",max_length=20);tname=discord.ui.TextInput(label="Ticket channel name",default="ticket-{user}",max_length=80);desc=discord.ui.TextInput(label="Ticket description",style=discord.TextStyle.paragraph,max_length=2000);image=discord.ui.TextInput(label="Ticket image URL",required=False,max_length=1000)
    def __init__(self,b):super().__init__();self.b=b
    async def on_submit(self,i):
        if len(self.b.s["options"])>=25:return await i.response.send_message("❌ الحد الأقصى 25.",ephemeral=True)
        self.b.s["options"].append({"name":str(self.name),"emoji":str(self.emoji) or "🎫","ticket_name":str(self.tname),"description":str(self.desc),"image_url":str(self.image).strip() or None});await i.response.edit_message(embed=self.b.cog.preview(self.b.s),view=self.b)

class Builder(discord.ui.View):
    def __init__(self,cog,s,pid=None):
        super().__init__(timeout=900);self.cog=cog;self.s=s;self.pid=pid;self.add_item(Cat(self));self.add_item(Channel(self));self.add_item(Role(self));self.add_item(Mode(self))
    @discord.ui.button(label="Add Ticket Type",emoji="➕",style=discord.ButtonStyle.secondary,row=4)
    async def add(self,i,b):await i.response.send_modal(TypeModal(self))
    @discord.ui.button(label="Remove Last Type",emoji="➖",style=discord.ButtonStyle.secondary,row=4)
    async def rem(self,i,b):
        if len(self.s["options"])<=1:return await i.response.send_message("❌ خاص يبقى واحد على الأقل.",ephemeral=True)
        self.s["options"].pop();await i.response.edit_message(embed=self.cog.preview(self.s),view=self)
    @discord.ui.button(label="Send Panel to Channel",emoji="📤",style=discord.ButtonStyle.success,row=4)
    async def send(self,i,b):
        if not self.s.get("category_id") or not self.s.get("channel_id"):return await i.response.send_message("❌ اختار Category وChannel.",ephemeral=True)
        ch=i.guild.get_channel(self.s["channel_id"]);cat=i.guild.get_channel(self.s["category_id"])
        if not isinstance(ch,discord.TextChannel) or not isinstance(cat,discord.CategoryChannel):return await i.response.send_message("❌ Channel أو Category غير صالح.",ephemeral=True)
        await i.response.defer(ephemeral=True);pid=self.pid;created=False
        try:
            if pid:
                if not await self.cog.db.get_ticket_panel(pid):return await i.followup.send("❌ Panel ما بقاتش موجودة.",ephemeral=True)
                await self.cog.db.update_ticket_panel(pid,{**self.s,"channel_id":ch.id});p=await self.cog.db.get_ticket_panel(pid)
            else:
                pid=await self.cog.db.create_ticket_panel({**self.s,"channel_id":ch.id});created=True;p=await self.cog.db.get_ticket_panel(pid)
            msg=await ch.send(embed=self.cog.panel_embed(p),view=PanelView(self.cog,p));await self.cog.db.update_ticket_panel(pid,{"channel_id":ch.id,"message_id":msg.id});self.cog.bot.add_view(PanelView(self.cog,p),message_id=msg.id)
            await i.followup.send(f"✅ تم حفظ ونشر Panel **#{pid}** في {ch.mention}.",ephemeral=True)
        except Exception as e:
            print(f"Ticket panel error: {e!r}")
            if created and pid:
                try:await self.cog.db.delete_ticket_panel(pid)
                except Exception:pass
            await i.followup.send("❌ فشل حفظ/نشر الـPanel. راجع صلاحيات البوت على Channel وCategory.",ephemeral=True)
class Cat(discord.ui.ChannelSelect):
    def __init__(self,b):super().__init__(channel_types=[discord.ChannelType.category],placeholder="Ticket Category",row=0);self.b=b
    async def callback(self,i):self.b.s["category_id"]=self.values[0].id;await i.response.edit_message(embed=self.b.cog.preview(self.b.s),view=self.b)
class Channel(discord.ui.ChannelSelect):
    def __init__(self,b):super().__init__(channel_types=[discord.ChannelType.text],placeholder="Panel Channel",row=1);self.b=b
    async def callback(self,i):self.b.s["channel_id"]=self.values[0].id;await i.response.edit_message(embed=self.b.cog.preview(self.b.s),view=self.b)
class Role(discord.ui.RoleSelect):
    def __init__(self,b):super().__init__(placeholder="Staff Role (اختياري)",row=2,min_values=0,max_values=1);self.b=b
    async def callback(self,i):self.b.s["support_role_id"]=self.values[0].id if self.values else None;await i.response.edit_message(embed=self.b.cog.preview(self.b.s),view=self.b)
class Mode(discord.ui.Select):
    def __init__(self,b):super().__init__(placeholder="Buttons / Select Menu",options=[discord.SelectOption(label="Buttons",value="buttons",emoji="🔘"),discord.SelectOption(label="Select Menu",value="select",emoji="📋")],row=3);self.b=b
    async def callback(self,i):self.b.s["mode"]=self.values[0];await i.response.edit_message(embed=self.b.cog.preview(self.b.s),view=self.b)

class Home(discord.ui.View):
    def __init__(self,cog):super().__init__(timeout=300);self.cog=cog
    @discord.ui.button(label="Create a Panel",emoji="➕",style=discord.ButtonStyle.primary)
    async def create(self,i,b):await i.response.send_modal(CreateModal(self.cog))
    @discord.ui.button(label="Manage Panels",emoji="⚙️",style=discord.ButtonStyle.secondary)
    async def manage(self,i,b):
        ps=await self.cog.db.list_ticket_panels(i.guild.id)
        if not ps:return await i.response.send_message("❌ ما كاين حتى Panel.",ephemeral=True)
        await i.response.edit_message(embed=discord.Embed(title="🎫 Manage Panels",description="اختار Panel من القائمة.",color=EmbedColor.PRIMARY),view=Manage(self.cog,ps))
class Manage(discord.ui.View):
    def __init__(self,cog,ps):super().__init__(timeout=600);self.cog=cog;self.pid=None;self.add_item(PanelSelect(self,ps))
    @discord.ui.button(label="Edit Panel",emoji="✏️",style=discord.ButtonStyle.primary,row=1)
    async def edit(self,i,b):
        if not self.pid:return await i.response.send_message("❌ اختار Panel.",ephemeral=True)
        p=await self.cog.db.get_ticket_panel(self.pid);await i.response.edit_message(embed=self.cog.preview(p),view=Builder(self.cog,p,p["id"]))
    @discord.ui.button(label="Send Panel",emoji="📤",style=discord.ButtonStyle.success,row=1)
    async def send(self,i,b):
        if not self.pid:return await i.response.send_message("❌ اختار Panel.",ephemeral=True)
        p=await self.cog.db.get_ticket_panel(self.pid);await i.response.edit_message(embed=self.cog.preview(p),view=Builder(self.cog,p,p["id"]))
    @discord.ui.button(label="Delete Panel",emoji="🗑️",style=discord.ButtonStyle.danger,row=1)
    async def delete(self,i,b):
        if not self.pid:return await i.response.send_message("❌ اختار Panel.",ephemeral=True)
        p=await self.cog.db.get_ticket_panel(self.pid)
        if p and p.get("channel_id") and p.get("message_id"):
            ch=i.guild.get_channel(p["channel_id"])
            if ch:
                try:await (await ch.fetch_message(p["message_id"])).delete()
                except discord.HTTPException:pass
        await self.cog.db.delete_ticket_panel(self.pid);await i.response.edit_message(content="✅ تحيد الـPanel.",embed=None,view=None)
class PanelSelect(discord.ui.Select):
    def __init__(self,parent,ps):super().__init__(placeholder="اختار Panel...",options=[discord.SelectOption(label=f"#{p['id']} • {p['title'][:80]}",value=str(p['id'])) for p in ps[:25]]);self.parent=parent
    async def callback(self,i):self.parent.pid=int(self.values[0]);p=await self.parent.cog.db.get_ticket_panel(self.parent.pid);await i.response.edit_message(embed=self.parent.cog.panel_details(p),view=self.parent)

class TicketManager(commands.Cog):
    def __init__(self,bot,db,config):self.bot=bot;self.db=db;self.config=config
    async def cog_load(self):
        for p in await self.db.get_all_ticket_panels():
            if p.get("message_id"):
                try:self.bot.add_view(PanelView(self,p),message_id=p["message_id"])
                except Exception as e:print(f"Ticket restore error: {e!r}")
    async def staff(self,i):
        return isinstance(i.user,discord.Member) and (i.user.guild_permissions.administrator or i.user.guild_permissions.manage_channels)
    async def ticket(self,cid):return await self.db.fetchone("SELECT * FROM tickets WHERE channel_id=? AND status='open'",(cid,))
    def panel_embed(self,p):
        e=discord.Embed(title=str(p.get("title") or "🎫 الدعم الفني")[:256],description=str(p.get("description") or "اختار القسم المناسب لفتح تذكرة.")[:4096],color=EmbedColor.PRIMARY)
        if p.get("image_url"):e.set_image(url=p["image_url"])
        return e
    def preview(self,s):
        e=self.panel_embed(s);e.add_field(name="Mode",value="Select Menu" if s.get("mode")=="select" else "Buttons",inline=True);e.add_field(name="Types",value=str(len(s.get("options",[]))),inline=True);e.add_field(name="Category",value=f"<#{s['category_id']}>" if s.get("category_id") else "❌",inline=True);e.add_field(name="Channel",value=f"<#{s['channel_id']}>" if s.get("channel_id") else "❌",inline=True);return e
    def panel_details(self,p):
        e=self.panel_embed(p);e.add_field(name="Ticket Types",value="\n".join(f"{x.get('emoji','🎫')} {x.get('name','Ticket')}" for x in p.get('options',[])[:25]) or "None",inline=False);return e
    async def create(self,i,pid,n):
        p=await self.db.get_ticket_panel(pid);opts=p.get("options",[]) if p else []
        if not p or p["guild_id"]!=i.guild.id or n>=len(opts):return await i.response.send_message("❌ Panel أو Ticket Type غير صالح.",ephemeral=True)
        cat=i.guild.get_channel(p.get("category_id"))
        if not isinstance(cat,discord.CategoryChannel):return await i.response.send_message("❌ Category ما بقاتش موجودة.",ephemeral=True)
        old=await self.db.fetchone("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open'",(i.guild.id,i.user.id))
        if old:return await i.response.send_message(f"❌ عندك Ticket مفتوحة بالفعل: <#{old[0]}>",ephemeral=True)
        x=opts[n];name=clean(str(x.get("ticket_name") or "ticket-{user}").replace("{user}",i.user.name).replace("{id}",str(i.user.id)))
        ow={i.guild.default_role:discord.PermissionOverwrite(view_channel=False),i.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
        if i.guild.me:ow[i.guild.me]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True,manage_messages=True)
        if p.get("support_role_id"):
            r=i.guild.get_role(int(p["support_role_id"]));
            if r:ow[r]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        await i.response.defer(ephemeral=True);ch=None
        try:
            ch=await cat.create_text_channel(name,overwrites=ow,reason="Ader Ticket");tid=await self.db.create_ticket({"guild_id":i.guild.id,"user_id":i.user.id,"channel_id":ch.id,"status":"open"})
            e=discord.Embed(title=f"🎫 {x.get('name','Ticket')}",description=f"مرحبا {i.user.mention}!\n\n{x.get('description') or p.get('ticket_description','')}",color=EmbedColor.PRIMARY)
            if x.get("image_url"):e.set_image(url=x["image_url"])
            await ch.send(content=i.user.mention,embed=e,view=TicketControls(self,ch.id));self.bot.add_view(TicketControls(self,ch.id));await i.followup.send(f"✅ تفتحات التذكرة: {ch.mention} (ID `{tid}`)",ephemeral=True)
        except Exception as e:
            print(f"Ticket create error: {e!r}")
            if ch:
                try:await ch.delete(reason="Ticket save failed")
                except discord.HTTPException:pass
            await i.followup.send("❌ فشل إنشاء التذكرة. تأكد من صلاحيات البوت.",ephemeral=True)
    async def close(self,i,cid):
        t=await self.ticket(cid)
        if not t:return await i.response.send_message("❌ التذكرة غير موجودة.",ephemeral=True)
        if i.user.id!=t["user_id"] and not await self.staff(i):return await i.response.send_message("❌ غير صاحب التذكرة أو Staff.",ephemeral=True)
        r=await self.db.execute("UPDATE tickets SET status='closed',closed_at=? WHERE id=? AND status='open'",(discord.utils.utcnow().timestamp(),t["id"]))
        if r.rowcount!=1:return await i.response.send_message("❌ تسدات من قبل.",ephemeral=True)
        await i.response.send_message("🔒 تسدات التذكرة. غادي يتحيد الروم بعد 5 ثواني.");await asyncio.sleep(5)
        try:await i.channel.delete(reason="Ader ticket close")
        except discord.HTTPException:pass
    @app_commands.command(name="ticket",description="Open Ader Ticket Tool manager")
    @is_admin()
    async def ticket_cmd(self,i):await i.response.send_message(embed=discord.Embed(title="🎫 Ader Ticket Tool",description="**Create a Panel** لإنشاء Panel\n**Manage Panels** لإدارة Panels",color=EmbedColor.PRIMARY),view=Home(self),ephemeral=True)
async def setup(bot):await bot.add_cog(TicketManager(bot,bot.db,bot.config))
