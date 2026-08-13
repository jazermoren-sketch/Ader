from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

SHORTCUTS={"give_role":"إعطاء رتبة","lock":"قفل الروم","unlock":"فتح الروم","timeout":"تايم اوت","untimeout":"الغاء تايم اوت","kick":"طرد","ban":"بان","warn":"تحذير","member_info":"معلومات العضو"}
DEFAULT_ALIASES={"give_role":"!رتبة","lock":"!قفل","unlock":"!فتح","timeout":"!تايم اوت","untimeout":"!الغاء تايم اوت","kick":"!طرد","ban":"!بان","warn":"!تحذير","member_info":"!معلومات العضو"}
INFO_IMAGE="https://cdn.discordapp.com/attachments/1517582979923185825/1537503900859633744/info-member.png"

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

    async def build_member_card(self,member:discord.Member):
        """Generate the member information image dynamically from the supplied member."""
        async with aiohttp.ClientSession() as session:
            async with session.get(INFO_IMAGE,timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200: raise RuntimeError("تعذر تحميل قالب معلومات العضو")
                data=await response.read()
        base=Image.open(io.BytesIO(data)).convert("RGBA")
        draw=ImageDraw.Draw(base)
        font_paths=["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
        bold_paths=["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        def font(size,bold=False):
            for p in bold_paths if bold else font_paths:
                try:return ImageFont.truetype(p,size)
                except OSError:pass
            return ImageFont.load_default()
        def fit(text,max_width,size=30,bold=False):
            f=font(size,bold)
            while draw.textbbox((0,0),text,font=f)[2]>max_width and size>14:
                size-=1; f=font(size,bold)
            return f
        # Overlay the dynamic values while preserving the supplied template artwork.
        w,h=base.size
        card_font=font(max(18,min(30,w//28)),True)
        small=font(max(14,min(22,w//38)),False)
        white=(255,255,255,255); dark=(25,25,30,235)
        # A readable information panel is drawn over the lower part of the template.
        panel_top=int(h*0.47); panel_bottom=h-30
        draw.rounded_rectangle((35,panel_top,w-35,panel_bottom),radius=24,fill=dark)
        avatar=member.display_avatar
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(avatar.url),timeout=aiohttp.ClientTimeout(total=10)) as response:
                    avatar_bytes=await response.read()
            av=Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((150,150))
            mask=Image.new("L",av.size,0); ImageDraw.Draw(mask).ellipse((0,0,149,149),fill=255)
            base.alpha_composite(av,(55,panel_top+25),mask)
        except Exception:
            pass
        x=230; y=panel_top+28; maxw=w-x-55
        draw.text((x,y),str(member),font=fit(str(member),maxw,32,True),fill=white); y+=48
        values=[
            f"Username: {member.name}",
            f"ID: {member.id}",
            f"Discord: {discord.utils.format_dt(member.created_at,'F')}",
            f"Server: {discord.utils.format_dt(member.joined_at,'F') if member.joined_at else 'غير معروف'}",
            f"Administrator: {'Yes' if member.guild_permissions.administrator else 'No'}",
            f"Server Owner: {'Yes' if member.id==member.guild.owner_id else 'No'}",
            f"Bot: {'Yes' if member.bot else 'No'}",
            f"Server Roles: {max(0,len(member.guild.roles)-1)} | Member Roles: {max(0,len(member.roles)-1)}",
            "Nova Aro",
        ]
        for value in values:
            draw.text((x,y),value,font=fit(value,maxw,20),fill=white); y+=30
            if y>panel_bottom-28: break
        out=io.BytesIO(); base.save(out,format="PNG",optimize=True); out.seek(0)
        return out

    async def execute(self,ctx,key,argument:Optional[discord.Member]=None,reason=""):
        if not ctx.guild or not isinstance(ctx.author,discord.Member):return
        if not(ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):return await ctx.send("❌ ما عندكش صلاحية استعمال هاد الاختصار.",delete_after=5)
        if key in ("lock","unlock"):
            if not ctx.channel.permissions_for(ctx.guild.me).manage_channels:return await ctx.send("❌ البوت ما عندوش Manage Channels.",delete_after=5)
            await ctx.channel.set_permissions(ctx.guild.default_role,send_messages=False if key=="lock" else None,reason=f"Shortcut by {ctx.author}"); return await ctx.send("🔒 تم قفل الروم." if key=="lock" else "🔓 تم فتح الروم.")
        if not argument:return await ctx.send("❌ خاصك تحدد العضو، مثال: `!معلومات العضو @عضو`",delete_after=6)
        if key=="member_info":
            try:
                image=await self.build_member_card(argument)
                return await ctx.send(file=discord.File(image,filename="member-info.png"))
            except Exception:
                return await ctx.send("❌ تعذر إنشاء صورة معلومات العضو حالياً.",delete_after=7)
        if argument==ctx.author or argument==ctx.guild.owner or argument.top_role>=ctx.author.top_role:return await ctx.send("❌ ما تقدرش تستعمل هاد الإجراء على هاد العضو.",delete_after=6)
        try:
            if key=="give_role":return await ctx.send("ℹ️ الاستعمال: `!رتبة @عضو @رتبة` — خاص تحديد الرتبة المراد إعطاؤها.",delete_after=7)
            if key=="timeout":await argument.timeout(discord.utils.utcnow()+discord.timedelta(minutes=10),reason=reason or f"Shortcut by {ctx.author}"); return await ctx.send(f"⏱️ تم إعطاء Timeout لـ {argument.mention} لمدة 10 دقائق.")
            if key=="untimeout":await argument.timeout(None,reason=reason or f"Shortcut by {ctx.author}"); return await ctx.send(f"✅ تم إلغاء Timeout لـ {argument.mention}.")
            if key=="kick":await argument.kick(reason=reason or f"Shortcut by {ctx.author}"); return await ctx.send(f"👢 تم طرد {argument.mention}.")
            if key=="ban":await argument.ban(reason=reason or f"Shortcut by {ctx.author}",delete_message_days=0); return await ctx.send(f"🔨 تم حظر {argument.mention}.")
            if key=="warn":return await ctx.send(f"⚠️ تحذير {argument.mention}: {reason or 'تحذير إداري.'}")
        except discord.Forbidden:return await ctx.send("❌ البوت ما عندوش الصلاحيات الكافية أو العضو أعلى من البوت.",delete_after=7)
        except discord.HTTPException as exc:return await ctx.send(f"❌ تعذر تنفيذ العملية: `{exc}`",delete_after=7)
    @commands.Cog.listener()
    async def on_message(self,message):
        if message.author.bot or not message.guild or not message.content.startswith("!"):return
        raw=message.content.split()[0]
        for key in SHORTCUTS:
            if raw==self.get_alias(message.guild.id,key):
                ctx=await self.bot.get_context(message); member=message.mentions[0] if message.mentions else None; await self.execute(ctx,key,member); return

async def setup(bot): await bot.add_cog(Shortcuts(bot))