from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

SHORTCUTS={"give_role":"إعطاء رتبة","lock":"قفل الروم","unlock":"فتح الروم","timeout":"تايم اوت","untimeout":"الغاء تايم اوت","kick":"طرد","ban":"بان","warn":"تحذير","member_info":"معلومات العضو"}
DEFAULT_ALIASES={"give_role":"!رتبة","lock":"!قفل","unlock":"!فتح","timeout":"!تايم اوت","untimeout":"!الغاء تايم اوت","kick":"!طرد","ban":"!بان","warn":"!تحذير","member_info":"!معلومات العضو"}

class ShortcutSelect(discord.ui.Select):
    def __init__(self,cog,hidden):
        self.cog,self.hidden=cog,hidden
        super().__init__(placeholder="اختر الاختصار...",options=[discord.SelectOption(label=v,value=k) for k,v in SHORTCUTS.items()])
    async def callback(self,interaction): await self.cog.show_editor(interaction,self.values[0],self.hidden)

class ShortcutView(discord.ui.View):
    def __init__(self,cog,hidden): super().__init__(timeout=300); self.add_item(ShortcutSelect(cog,hidden))

class ShortcutEditor(discord.ui.View):
    def __init__(self,cog,key,hidden):
        super().__init__(timeout=300); self.cog,self.key,self.hidden=cog,key,hidden; self.add_item(EditAliasButton(cog,key,hidden)); self.add_item(BackButton(cog,hidden))

class EditAliasButton(discord.ui.Button):
    def __init__(self,cog,key,hidden): super().__init__(label="تعديل الاختصار",style=discord.ButtonStyle.primary); self.cog,self.key,self.hidden=cog,key,hidden
    async def callback(self,interaction): await interaction.response.send_modal(AliasModal(self.cog,self.key,self.cog.get_alias(interaction.guild.id,self.key),self.hidden))

class BackButton(discord.ui.Button):
    def __init__(self,cog,hidden): super().__init__(label="رجوع",style=discord.ButtonStyle.secondary); self.cog,self.hidden=cog,hidden
    async def callback(self,interaction): await interaction.response.edit_message(embed=self.cog.selector_embed(),view=ShortcutView(self.cog,self.hidden))

class AliasModal(discord.ui.Modal,title="تعديل الاختصار"):
    alias=discord.ui.TextInput(label="الاختصار",max_length=50,required=True)
    def __init__(self,cog,key,current,hidden): super().__init__(); self.cog,self.key,self.hidden=cog,key,hidden; self.alias.default=current
    async def on_submit(self,interaction):
        value=self.alias.value.strip(); value=value if value.startswith("!") else "!"+value
        if len(value)<2 or " " in value:return await interaction.response.send_message("❌ الاختصار خاصو يبدأ بـ `!` وما يكونش فيه مسافات.",ephemeral=True)
        self.cog.set_alias(interaction.guild.id,self.key,value); await interaction.response.send_message(f"✅ تم تغيير الاختصار إلى `{value}`",ephemeral=True)

class Shortcuts(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.path=Path("data/shortcuts.json"); self.path.parent.mkdir(parents=True,exist_ok=True); self.data=self.load()
    def load(self):
        try:return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):return {}
    def save(self):self.path.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding="utf-8")
    def get_alias(self,guild_id,key):return self.data.get(str(guild_id),{}).get(key,DEFAULT_ALIASES[key])
    def set_alias(self,guild_id,key,value):self.data.setdefault(str(guild_id),{})[key]=value; self.save()
    def selector_embed(self):return discord.Embed(title="اختر الاختصار الذي تود التعديل عليه",color=discord.Color.blurple())
    @app_commands.command(name="اختصارات",description="إدارة اختصارات الإدارة")
    @app_commands.describe(اخفاء="إخفاء لوحة إعداد الاختصارات")
    @app_commands.default_permissions(manage_guild=True)
    async def shortcuts(self,interaction,اخفاء:bool=False):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.",ephemeral=True)
        await interaction.response.send_message(embed=self.selector_embed(),view=ShortcutView(self,اخفاء),ephemeral=اخفاء)
    async def show_editor(self,interaction,key,hidden):
        embed=discord.Embed(title=f"إعدادات اختصار {SHORTCUTS[key]}",color=discord.Color.blurple()); embed.description=f"الاختصار الحالي: `{self.get_alias(interaction.guild.id,key)}`"; await interaction.response.edit_message(embed=embed,view=ShortcutEditor(self,key,hidden))

    @staticmethod
    def _font(size,bold=False,arabic=False):
        candidates=[]
        if arabic:candidates=["/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"]
        candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        for path in candidates:
            try:return ImageFont.truetype(path,size)
            except OSError:pass
        return ImageFont.load_default()
    @staticmethod
    def _fit(draw,text,max_width,size,bold=False,arabic=False):
        while size>12:
            f=Shortcuts._font(size,bold,arabic)
            if draw.textbbox((0,0),text,font=f,direction="rtl" if arabic else None)[2] <= max_width:return f
            size-=1
        return Shortcuts._font(size,bold,arabic)
    @staticmethod
    def _text(draw,xy,text,font,fill=(255,255,255,255),anchor="la",arabic=False):draw.text(xy,text,font=font,fill=fill,anchor=anchor,direction="rtl" if arabic else None,language="ar" if arabic else None)
    @staticmethod
    def _toggle(draw,x,y,enabled):
        track=(42,184,96,255) if enabled else (54,52,72,255); knob=(245,245,250,255) if enabled else (177,177,195,255)
        draw.rounded_rectangle((x,y,x+88,y+44),radius=22,fill=track); cx=x+66 if enabled else x+22; draw.ellipse((cx-15,y+7,cx+15,y+37),fill=knob)
    @staticmethod
    def _age(created):
        now=datetime.now(timezone.utc); months=(now.year-created.year)*12+(now.month-created.month)
        if now.day<created.day:months-=1
        return f"{max(0,months)}month {(now-created).days%30}day"

    async def build_member_card(self,member:discord.Member):
        W,H=1536,1024; img=Image.new("RGBA",(W,H),(8,3,20,255)); d=ImageDraw.Draw(img)
        for i in range(8,0,-1):d.rounded_rectangle((16+i,16+i,W-16-i,H-16-i),radius=30,outline=(120,40,255,max(20,100-i*9)),width=3)
        d.rounded_rectangle((18,18,W-18,H-18),radius=28,outline=(176,72,255,255),width=3); d.line((52,40,W-52,40),fill=(159,61,255,255),width=7)
        for x in range(950,1450,120):d.line((x,80,x-260,360),fill=(64,25,115,100),width=3)
        white=(248,248,252,255); purple=(176,130,255,255); green=(38,214,94,255); red=(255,55,80,255)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(member.display_avatar.replace(size=256).url),timeout=aiohttp.ClientTimeout(total=10)) as r:avatar_bytes=await r.read()
            avatar=Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((330,330)); mask=Image.new("L",(330,330),0); ImageDraw.Draw(mask).ellipse((0,0,329,329),fill=255); img.paste(avatar,(82,58),mask)
        except Exception:pass
        for rad,width in [(178,5),(188,3),(196,2)]:d.ellipse((247-rad,223-rad,247+rad,223+rad),outline=(115,30,255,230),width=width)
        name=str(member); d.text((480,108),name,font=self._fit(d,name,780,72,True),fill=white); d.rounded_rectangle((480,196,650,252),radius=28,fill=(104,35,235,255)); self._text(d,(565,224),"Nova Aro",self._font(30,True),white,anchor="mm"); self._text(d,(480,286),f"ID: {member.id}",self._font(29),white)
        panels=[(58,360,525,498),(548,360,995,498),(1018,360,1475,498)]
        for box in panels:d.rounded_rectangle(box,radius=18,fill=(14,7,29,210),outline=(116,73,170,255),width=2)
        self._text(d,(292,392),"تاريخ الانضمام للديسكورد",self._font(28,True,True),purple,anchor="ma",arabic=True); self._text(d,(292,448),member.created_at.strftime("%d %b %Y"),self._font(32,True),white,anchor="ma")
        self._text(d,(771,392),"تاريخ الانضمام للسيرفر",self._font(28,True,True),purple,anchor="ma",arabic=True); self._text(d,(771,448),member.joined_at.strftime("%d %b %Y") if member.joined_at else "غير معروف",self._font(32,True),white,anchor="ma")
        self._text(d,(1246,392),"عمر الحساب",self._font(28,True,True),purple,anchor="ma",arabic=True); self._text(d,(1246,448),self._age(member.created_at),self._font(30,True),white,anchor="ma")
        left=(58,518,655,871); mid=(674,518,995,871); right=(1018,518,1475,871)
        for box in (left,mid,right):d.rounded_rectangle(box,radius=18,fill=(12,6,28,225),outline=(116,73,170,255),width=2)
        status=[("ADMINISTRATION",member.guild_permissions.administrator,False),("صاحب السيرفر",member.id==member.guild.owner_id,True),("بوت",member.bot,True),("صاحب الصلاحية الأعلى",member.top_role==member.guild.me.top_role if member.guild.me else False,True)]
        for (label,enabled,arabic),y in zip(status,[545,632,719,806]):
            self._text(d,(154,y+25),label,self._font(25,True,arabic),white,anchor="lm",arabic=arabic); self._text(d,(482,y+25),"نعم" if enabled else "لا",self._font(24,True,True),green if enabled else red,anchor="mm",arabic=True); self._toggle(d,533,y+3,enabled)
            if y<800:d.line((78,y+73,635,y+73),fill=(75,48,108,255),width=2)
        server_roles=max(0,len(member.guild.roles)-1); member_roles=[r for r in member.roles if r!=member.guild.default_role]
        self._text(d,(834,565),"الرتب في السيرفر",self._font(27,True,True),purple,anchor="ma",arabic=True); self._text(d,(834,620),str(server_roles),self._font(58,True),white,anchor="ma"); d.line((704,668,965,668),fill=(75,48,108,255),width=2); self._text(d,(834,700),"رتب العضو",self._font(27,True,True),purple,anchor="ma",arabic=True); self._text(d,(834,755),str(len(member_roles)),self._font(58,True),white,anchor="ma")
        self._text(d,(1246,552),"رتب العضو",self._font(27,True,True),purple,anchor="ma",arabic=True)
        for i,role in enumerate(member_roles[-6:][::-1]):
            yy=596+i*43; d.rounded_rectangle((1038,yy,1455,yy+37),radius=12,fill=(19,12,38,235),outline=(73,55,105,220),width=1); rgb=role.color.to_rgb() if role.color.value else (130,130,140); d.ellipse((1054,yy+9,1076,yy+31),fill=(*rgb,255)); label=role.name; self._text(d,(1090,yy+19),label,self._fit(d,label,345,19,False),white,anchor="lm")
        if len(member_roles)>6:self._text(d,(1246,854),f"+{len(member_roles)-6} more",self._font(16,True),purple,anchor="ma")
        d.line((220,955,585,955),fill=(150,60,255,255),width=3); d.line((950,955,1315,955),fill=(150,60,255,255),width=3); d.ellipse((720,878,816,974),fill=(12,5,30,255),outline=(145,57,255,255),width=3); self._text(d,(768,926),"♛",self._font(43,True),purple,anchor="mm"); self._text(d,(768,986),"✦ Nova Aro ✦",self._font(38,True),white,anchor="ms")
        out=io.BytesIO(); img.convert("RGB").save(out,format="JPEG",quality=92,optimize=True); out.seek(0); return out

    async def execute(self,ctx,key,argument:Optional[discord.Member]=None,reason=""):
        if not ctx.guild or not isinstance(ctx.author,discord.Member):return
        if not(ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):return await ctx.send("❌ ما عندكش صلاحية استعمال هاد الاختصار.",delete_after=5)
        if key in ("lock","unlock"):
            if not ctx.channel.permissions_for(ctx.guild.me).manage_channels:return await ctx.send("❌ البوت ما عندوش Manage Channels.",delete_after=5)
            try:
                await ctx.channel.set_permissions(ctx.guild.default_role,send_messages=False if key=="lock" else None,reason=f"Shortcut by {ctx.author}")
            except discord.HTTPException as exc:
                if getattr(exc,"code",None)==350005:
                    return await ctx.send("❌ Discord رفض قفل هاد الروم لأن إعدادات **Server Onboarding** كتطلب على الأقل روم واحد يقدر @everyone يقرا فيه ويرسل الرسائل. قفل هاد الروم غادي يخالف الشرط ديال Discord.",delete_after=10)
                if getattr(exc,"status",None)==403:return await ctx.send("❌ البوت ما عندوش صلاحية تعديل صلاحيات هاد الروم.",delete_after=7)
                return await ctx.send("❌ تعذر تعديل صلاحيات الروم حالياً.",delete_after=7)
            return await ctx.send("🔒 تم قفل الروم." if key=="lock" else "🔓 تم فتح الروم.")
        if not argument:return await ctx.send("❌ خاصك تحدد العضو، مثال: `!معلومات العضو @عضو`",delete_after=6)
        if key=="member_info":
            try:return await ctx.send(file=discord.File(await self.build_member_card(argument),filename="member-info.jpg"))
            except Exception:return await ctx.send("❌ تعذر إنشاء صورة معلومات العضو حالياً.",delete_after=7)
        if argument==ctx.author or argument==ctx.guild.owner or argument.top_role>=ctx.author.top_role:return await ctx.send("❌ ما تقدرش تستعمل هاد الإجراء على هاد العضو.",delete_after=6)
        try:
            if key=="give_role":return await ctx.send("ℹ️ الاستعمال: `!رتبة @عضو @رتبة` — خاص تحديد الرتبة المراد إعطاؤها.",delete_after=7)
            if key=="timeout":await argument.timeout(discord.utils.utcnow()+discord.timedelta(minutes=10),reason=reason or f"Shortcut by {ctx.author}"); return await ctx.send(f"⏱️ تم إعطاء Timeout لـ {argument.mention} لمدة 10 دقائق.")
            if key=="untimeout":await argument.timeout(None,reason=reason or f"Shortcut by {ctx.author}"); return await ctx.send(f"✅ تم إلغاء Timeout لـ {argument.mention}.")
            if key=="kick":await argument.kick(reason=reason or f"Shortcut by {ctx.author}"); return await ctx.send(f"👢 تم طرد {argument.mention}.")
            if key=="ban":await argument.ban(reason=reason or f"Shortcut by {ctx.author}",delete_message_days=0); return await ctx.send(f"🔨 تم حظر {argument.mention}.")
            if key=="warn":return await ctx.send(f"⚠️ تحذير {argument.mention}: {reason or 'تحذير إداري.'}")
        except discord.Forbidden:return await ctx.send("❌ البوت ما عندوش الصلاحيات الكافية أو العضو أعلى من البوت.",delete_after=7)
        except discord.HTTPException:return await ctx.send("❌ تعذر تنفيذ العملية بسبب خطأ من Discord.",delete_after=7)

    @commands.Cog.listener()
    async def on_message(self,message):
        if message.author.bot or not message.guild or not message.content.startswith("!"):return
        raw=message.content.split()[0]
        for key in SHORTCUTS:
            if raw==self.get_alias(message.guild.id,key):
                ctx=await self.bot.get_context(message); member=message.mentions[0] if message.mentions else None
                try:await self.execute(ctx,key,member)
                except discord.Forbidden:await message.channel.send("❌ البوت ما عندوش الصلاحيات الكافية لتنفيذ هاد الاختصار.",delete_after=7)
                except discord.HTTPException:await message.channel.send("❌ Discord رفض العملية. تأكد من صلاحيات البوت وإعدادات السيرفر.",delete_after=7)
                except Exception:await message.channel.send("❌ وقع خطأ غير متوقع أثناء تنفيذ الاختصار.",delete_after=7)
                return

async def setup(bot): await bot.add_cog(Shortcuts(bot))
